# 本项目编码格式：UTF-8（无 BOM）。
# Jenkins 客户端单测：触发→排队→轮询→取制品全链路用 MockTransport 模拟，绝不触网。
# why：集成代码最容易在错误路径上"看起来正常"（超时当成失败、token 拼进 URL），
# 这里把错误码与鉴权头也钉死。
from __future__ import annotations

import httpx
import pytest
from app.core.config import Settings
from app.core.exceptions import AppError
from app.utils.jenkins_client import JenkinsClient

JUNIT_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="2" failures="1" errors="0" skipped="0" time="0.2">
    <testcase name="test_1" time="0.1"/>
    <testcase name="test_2" time="0.1"><failure message="boom"/></testcase>
  </testsuite>
</testsuites>"""


def _settings() -> Settings:
    settings = Settings()
    settings.jenkins.base_url = "http://jenkins.test"
    settings.jenkins.username = "admin"
    settings.jenkins.job_name = "cases"
    settings.jenkins.artifact_path = "reports/junit.xml"
    settings.jenkins.poll_interval_seconds = 0
    return settings


def _client(handler, *, clock=None) -> JenkinsClient:
    return JenkinsClient(
        _settings(),
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
        clock=clock or (lambda: 0.0),
    )


def test_build_and_wait_happy_path(monkeypatch):
    monkeypatch.setenv("JENKINS_API_TOKEN", "tok")
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/crumbIssuer/api/json":
            return httpx.Response(200, json={"crumb": "c-1", "crumbRequestField": "Jenkins-Crumb"})
        if request.url.path == "/job/cases/buildWithParameters":
            # token 只允许出现在 Authorization 头（URL/query 里出现即泄漏）
            assert request.headers["authorization"].startswith("Basic ")
            assert request.headers["jenkins-crumb"] == "c-1"
            return httpx.Response(201, headers={"Location": "http://jenkins.test/queue/item/9/"})
        if request.url.path == "/queue/item/9/api/json":
            return httpx.Response(200, json={"executable": {"number": 7}})
        if request.url.path == "/job/cases/7/api/json":
            return httpx.Response(
                200,
                json={
                    "building": False,
                    "result": "SUCCESS",
                    "url": "http://jenkins.test/job/cases/7/",
                    "number": 7,
                    "duration": 1234,
                },
            )
        raise AssertionError(f"未预期的请求: {request.method} {request.url}")

    build = _client(handler).build_and_wait(
        task_id=1, case_ids=[2, 3], base_url="http://target", timeout_seconds=60
    )
    assert (build.number, build.result, build.duration_ms) == (7, "SUCCESS", 1234)
    assert build.url.endswith("/job/cases/7/")
    assert ("POST", "/job/cases/buildWithParameters") in seen


def test_download_artifact_returns_bytes(monkeypatch):
    monkeypatch.setenv("JENKINS_API_TOKEN", "tok")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/job/cases/7/artifact/reports/junit.xml"
        return httpx.Response(200, content=JUNIT_XML.encode("utf-8"))

    assert b"testsuites" in _client(handler).download_artifact(7, "reports/junit.xml")


def test_not_configured_without_token(monkeypatch):
    monkeypatch.delenv("JENKINS_API_TOKEN", raising=False)
    client = _client(lambda request: httpx.Response(500))
    assert client.is_configured() is False
    with pytest.raises(AppError) as exc:
        client.build_and_wait(task_id=1, case_ids=[1], base_url="http://t", timeout_seconds=5)
    assert exc.value.code == "JENKINS_NOT_CONFIGURED"


def test_queue_cancelled_raises(monkeypatch):
    monkeypatch.setenv("JENKINS_API_TOKEN", "tok")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crumbIssuer/api/json":
            return httpx.Response(404)  # CSRF 关闭：无 crumb 也要能跑
        if request.url.path == "/job/cases/buildWithParameters":
            return httpx.Response(201, headers={"Location": "http://jenkins.test/queue/item/9/"})
        if request.url.path == "/queue/item/9/api/json":
            return httpx.Response(200, json={"cancelled": True})
        raise AssertionError(f"未预期的请求: {request.url}")

    with pytest.raises(AppError) as exc:
        _client(handler).build_and_wait(
            task_id=1, case_ids=[1], base_url="http://t", timeout_seconds=60
        )
    assert exc.value.code == "JENKINS_BUILD_CANCELLED"


def test_build_timeout_hits_deadline(monkeypatch):
    monkeypatch.setenv("JENKINS_API_TOKEN", "tok")
    elapsed = {"t": 0.0}

    def clock() -> float:
        elapsed["t"] += 10.0
        return elapsed["t"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crumbIssuer/api/json":
            return httpx.Response(200, json={"crumb": "c", "crumbRequestField": "Jenkins-Crumb"})
        if request.url.path == "/job/cases/buildWithParameters":
            return httpx.Response(201, headers={"Location": "http://jenkins.test/queue/item/9/"})
        if request.url.path == "/queue/item/9/api/json":
            return httpx.Response(200, json={"executable": {"number": 7}})
        return httpx.Response(200, json={"building": True, "number": 7})

    with pytest.raises(AppError) as exc:
        _client(handler, clock=clock).build_and_wait(
            task_id=1, case_ids=[1], base_url="http://t", timeout_seconds=5
        )
    assert exc.value.code == "JENKINS_TIMEOUT"


def test_http_error_and_unreachable_are_distinguished(monkeypatch):
    monkeypatch.setenv("JENKINS_API_TOKEN", "tok")

    def http_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(AppError) as exc:
        _client(http_error).download_artifact(7, "reports/junit.xml")
    assert exc.value.code == "JENKINS_HTTP_ERROR"

    def connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(AppError) as exc2:
        _client(connect_error).download_artifact(7, "reports/junit.xml")
    assert exc2.value.code == "JENKINS_UNAVAILABLE"
