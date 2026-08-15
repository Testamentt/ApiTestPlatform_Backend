# 任务接口测试：202 立即返回 / Lookup-Create 幂等 / draft 不可执行 / 结果查询 / 重试与入队兜底。
from __future__ import annotations

from pathlib import Path

import pytest
from app.core.exceptions import AppError
from app.utils.subprocess_util import CmdResult
from tests.fakes import JUNIT_OK, make_fake_run_cmd


@pytest.fixture()
def active_case_factory(client):
    def _make(name="get"):
        cid = client.post(
            "/api/v1/cases",
            json={"name": name, "operation_id": "op_" + name, "method": "GET", "path": "/get"},
        ).json()["data"]["id"]
        client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "demo"})
        return cid

    return _make


def test_create_task_202_and_execute(
    client, patch_sessionlocal, fake_execution, active_case_factory
):
    cid = active_case_factory()
    r = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    assert r.status_code == 202
    tid = r.json()["data"]["id"]
    # eager 已同步执行完毕
    t = client.get(f"/api/v1/tasks/{tid}").json()["data"]
    assert t["status"] == "success"
    assert t["result_summary"]["passed"] == 1
    assert t["report_link"].startswith("/static/")


def test_lookup_create_returns_same_task(
    client, patch_sessionlocal, fake_execution, active_case_factory
):
    cid = active_case_factory()
    r1 = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    r2 = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    assert r1.json()["data"]["id"] == r2.json()["data"]["id"]


def test_different_input_new_task(client, patch_sessionlocal, fake_execution, active_case_factory):
    c1, c2 = active_case_factory("a"), active_case_factory("b")
    t1 = client.post("/api/v1/tasks", json={"case_ids": [c1]}).json()["data"]["id"]
    t2 = client.post("/api/v1/tasks", json={"case_ids": [c1, c2]}).json()["data"]["id"]
    assert t1 != t2


def test_non_active_case_rejected(client, patch_sessionlocal):
    cid = client.post(
        "/api/v1/cases",
        json={"name": "draft", "operation_id": "op_draft", "method": "GET", "path": "/get"},
    ).json()["data"]["id"]  # 未 confirm，保持 draft
    r = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    assert r.status_code == 422


def test_get_task_results(client, patch_sessionlocal, fake_execution, active_case_factory):
    cid = active_case_factory()
    tid = client.post("/api/v1/tasks", json={"case_ids": [cid]}).json()["data"]["id"]
    data = client.get(f"/api/v1/tasks/{tid}/results").json()["data"]
    assert data["task"]["status"] == "success"
    assert data["results"][0]["case_id"] == cid


def test_get_missing_task_404(client):
    assert client.get("/api/v1/tasks/99999").status_code == 404


def test_failed_task_retry_same_input(client, patch_sessionlocal, active_case_factory, monkeypatch):
    # why：失败任务同输入重试——重置 PENDING 重新入队（review H4），同一任务行复用
    cid = active_case_factory()
    monkeypatch.setattr(
        "app.services.execution_service.run_cmd",
        make_fake_run_cmd(exception=AppError("SUBPROCESS_TIMEOUT", status_code=502)),
    )
    r1 = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    assert r1.status_code == 202
    tid = r1.json()["data"]["id"]
    assert client.get(f"/api/v1/tasks/{tid}").json()["data"]["status"] == "failed"

    monkeypatch.setattr(
        "app.services.execution_service.run_cmd", make_fake_run_cmd(junit_xml=JUNIT_OK)
    )
    r2 = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    assert r2.status_code == 202
    assert r2.json()["data"]["id"] == tid
    t2 = client.get(f"/api/v1/tasks/{tid}").json()["data"]
    assert t2["status"] == "success"
    assert t2["error_stage"] is None  # 重试已清空失败信息


def test_dispatch_failure_marks_failed(
    client, patch_sessionlocal, active_case_factory, monkeypatch
):
    # why：Celery/Redis 不可用时入队失败——落 FAILED(dispatch) 而非 PENDING 孤儿（review M2）
    cid = active_case_factory()

    def _boom(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.services.task_service.dispatch_execution", _boom)
    r = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    assert r.status_code == 503
    assert r.json()["code"] == "DISPATCH_FAILED"
    page = client.get("/api/v1/tasks").json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["status"] == "failed"
    assert page["items"][0]["error_stage"] == "dispatch"


def test_task_timeout_controls_subprocess(
    client, patch_sessionlocal, active_case_factory, monkeypatch
):
    # why：timeout_seconds 必须真正控制 subprocess 超时（review M3/B4），而非仅参与幂等键
    captured = {}

    def _fake_run(args, timeout, *, check=True, cwd=None, on_start=None):
        captured["timeout"] = timeout
        if on_start is not None:
            on_start(9999)
        (Path(cwd) / "report.xml").write_text(JUNIT_OK, encoding="utf-8")
        return CmdResult(0, "", "", 100, 9999)

    monkeypatch.setattr("app.services.execution_service.run_cmd", _fake_run)
    cid = active_case_factory()
    tid = client.post(
        "/api/v1/tasks", json={"case_ids": [cid], "timeout_seconds": 120}
    ).json()["data"]["id"]
    assert captured["timeout"] == 120
    assert client.get(f"/api/v1/tasks/{tid}").json()["data"]["timeout_seconds"] == 120
