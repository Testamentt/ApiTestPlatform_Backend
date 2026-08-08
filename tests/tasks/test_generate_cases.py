# generate_cases Celery 任务测试（eager + FakeLlmClient）：成功落库 / 校验失败 / 解析失败。
from __future__ import annotations

from app.models.enums import GenerationStatus
from app.models.generation_log import GenerationLog
from app.models.generation_task import GenerationTask
from app.models.test_case import TestCase
from app.tasks.generate_cases import generate_cases_task

from tests.fakes import FakeLlmClient


def _swagger_doc(operations):
    paths = {}
    for op in operations:
        paths.setdefault(op["path"], {})[op["method"].lower()] = {
            "operationId": op.get("operation_id"),
            "responses": {"200": {"description": "ok"}},
        }
    return {"openapi": "3.0.3", "info": {"title": "t", "version": "1"}, "paths": paths}


DOC = _swagger_doc([
    {"path": "/users", "method": "GET", "operation_id": "listUsers"},
])


def _seed_task(session_factory, *, document=None):
    with session_factory() as s:
        t = GenerationTask(run_id="run_task", document=document or DOC, status="pending")
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def _patch_worker(monkeypatch, session_factory, llm):
    # why：eager 下 worker 用真实 SessionLocal/LlmClient，需指向内存库 + Fake（不真调 API/Redis）
    monkeypatch.setattr("app.tasks.generate_cases.SessionLocal", session_factory)
    monkeypatch.setattr("app.services.generation_service.LlmClient", lambda: llm)


def test_generate_task_success(session_factory, monkeypatch):
    task_id = _seed_task(session_factory)
    _patch_worker(monkeypatch, session_factory, FakeLlmClient())
    generate_cases_task.delay(task_id)
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        assert task.status == GenerationStatus.SUCCESS.value
        assert task.result_summary["draft_created"] == 1
        cases = s.query(TestCase).filter(TestCase.source == "ai").all()
        assert len(cases) == 1
        assert cases[0].operation_id == "listUsers"  # 服务端注入
        assert s.query(GenerationLog).count() == 1  # 审计日志


def test_generate_task_validation_failed(session_factory, monkeypatch):
    task_id = _seed_task(session_factory)
    _patch_worker(monkeypatch, session_factory, FakeLlmClient(data={"bad": 1}))
    generate_cases_task.delay(task_id)
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        assert task.result_summary["rejected"] == 1
        assert "校验失败" in task.result_summary["rejected_detail"][0]["reason"]
        assert s.query(TestCase).count() == 0  # 不建坏用例
        assert s.query(GenerationLog).one().status == "validation_failed"


def test_generate_task_parse_failure(session_factory, monkeypatch):
    task_id = _seed_task(session_factory, document={"openapi": "3.0.3"})  # 缺 paths
    _patch_worker(monkeypatch, session_factory, FakeLlmClient())
    generate_cases_task.delay(task_id)
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        assert task.status == GenerationStatus.FAILED.value
        assert task.error_stage == "parse"
        assert s.query(TestCase).count() == 0  # 入口拦截，不产生 draft


def test_generate_task_soft_timeout_fails(session_factory, monkeypatch):
    # RULES §8.2：SoftTimeLimitExceeded 捕获 → force_fail_timeout 落 FAILED(timeout)，不卡 RUNNING
    from celery.exceptions import SoftTimeLimitExceeded

    task_id = _seed_task(session_factory)
    monkeypatch.setattr("app.tasks.generate_cases.SessionLocal", session_factory)

    def _boom(*a, **k):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr("app.tasks.generate_cases.run_generation", _boom)
    generate_cases_task.delay(task_id)
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        assert task.status == GenerationStatus.FAILED.value
        assert task.error_stage == "timeout"
        assert "软超时" in task.error_msg
