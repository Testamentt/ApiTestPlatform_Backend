# generation_service 单测：create（幂等/定向/untested/首次/全覆盖）+ run_generation（成功/校验失败/skipped/warnings/解析失败）。
from __future__ import annotations

import pytest
from app.core.exceptions import AppError
from app.models.enums import GenerationStatus
from app.models.generation_log import GenerationLog
from app.models.generation_task import GenerationTask
from app.models.impact_analysis import ImpactAnalysis
from app.models.test_case import TestCase
from app.services.generation_service import GenerationService, run_generation
from sqlalchemy import select

from tests.fakes import FakeLlmClient


def _swagger_doc(operations):
    paths = {}
    for op in operations:
        paths.setdefault(op["path"], {})[op["method"].lower()] = {
            "operationId": op.get("operation_id"),
            "responses": {"200": {"description": "ok"}},
        }
    return {"openapi": "3.0.3", "info": {"title": "t", "version": "1"}, "paths": paths}


DOC = _swagger_doc(
    [
        {"path": "/users", "method": "GET", "operation_id": "listUsers"},
        {"path": "/users", "method": "POST", "operation_id": "createUser"},
        {"path": "/users/{id}", "method": "DELETE", "operation_id": "deleteUser"},
    ]
)


def _seed_task(session_factory, *, document=None, operation_ids=None, status="pending"):
    with session_factory() as s:
        t = GenerationTask(
            run_id="run_gen",
            document=document or DOC,
            operation_ids=operation_ids,
            status=status,
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def _seed_impact(session_factory, *, untested_ops):
    with session_factory() as s:
        a = ImpactAnalysis(
            new_version="v1",
            added_ops=[],
            removed_ops=[],
            changed_ops=[],
            breaking_changed_ops=[],
            affected_case_ids=[],
            orphaned_case_ids=[],
            suggested_remap={},
            untested_ops=untested_ops,
            affected_summary={},
        )
        s.add(a)
        s.commit()


# ---------- create_generation_task ----------


def test_create_idempotent(session_factory, monkeypatch):
    with session_factory() as s:
        svc = GenerationService(s)
        monkeypatch.setattr("app.services.generation_service.dispatch_generation", lambda _tid: None)
        t1 = svc.create_generation_task(DOC)
        t2 = svc.create_generation_task(DOC)
        assert t1.id == t2.id  # Lookup-Create：同输入返回同一任务


def test_create_default_uses_untested(session_factory, monkeypatch):
    _seed_impact(session_factory, untested_ops=["listUsers"])
    with session_factory() as s:
        svc = GenerationService(s)
        monkeypatch.setattr("app.services.generation_service.dispatch_generation", lambda _tid: None)
        task = svc.create_generation_task(DOC)
        assert task.operation_ids == ["listUsers"]  # 读最新分析 untested（间隙 1）


def test_create_first_run_full(session_factory, monkeypatch):
    # 无历史分析 → 全量（operation_ids=None，开箱即用）
    with session_factory() as s:
        svc = GenerationService(s)
        monkeypatch.setattr("app.services.generation_service.dispatch_generation", lambda _tid: None)
        task = svc.create_generation_task(DOC)
        assert task.operation_ids is None


def test_create_fully_covered_422(session_factory, monkeypatch):
    _seed_impact(session_factory, untested_ops=[])
    with session_factory() as s:
        svc = GenerationService(s)
        with pytest.raises(AppError) as ei:
            svc.create_generation_task(DOC)
        assert ei.value.code == "NO_UNTESTED_OPS"


def test_create_force_full_ignores_untested(session_factory, monkeypatch):
    _seed_impact(session_factory, untested_ops=["listUsers"])
    with session_factory() as s:
        svc = GenerationService(s)
        monkeypatch.setattr("app.services.generation_service.dispatch_generation", lambda _tid: None)
        task = svc.create_generation_task(DOC, force_full=True)
        assert task.operation_ids is None  # 全量重建


def test_create_explicit_operation_ids(session_factory, monkeypatch):
    _seed_impact(session_factory, untested_ops=["listUsers"])
    with session_factory() as s:
        svc = GenerationService(s)
        monkeypatch.setattr("app.services.generation_service.dispatch_generation", lambda _tid: None)
        task = svc.create_generation_task(DOC, operation_ids=["createUser"])
        assert task.operation_ids == ["createUser"]  # 显式定向优先


# ---------- run_generation ----------


def test_run_generation_success(session_factory):
    task_id = _seed_task(session_factory)
    run_generation(session_factory, task_id, llm=FakeLlmClient())
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        assert task.status == GenerationStatus.SUCCESS.value
        assert task.result_summary["draft_created"] == 3  # 3 个 operation 各 1 条
        cases = list(s.scalars(select(TestCase).where(TestCase.source == "ai")))
        assert len(cases) == 3
        assert {c.operation_id for c in cases} == {
            "listUsers",
            "createUser",
            "deleteUser",
        }  # 服务端注入
        assert all(c.trust_score == 80 for c in cases)  # 无 warnings → 80
        assert all(c.status == "draft" for c in cases)  # draft 恒为
        assert s.query(GenerationLog).count() == 3  # 逐 operation 审计日志


def test_run_generation_validation_failed(session_factory):
    # FakeLlmClient 返回坏 dict（缺 cases 键）→ 校验失败落库 + rejected_detail，不建坏用例
    task_id = _seed_task(session_factory, operation_ids=["listUsers"])
    run_generation(session_factory, task_id, llm=FakeLlmClient(data={"bad": 1}))
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        assert task.result_summary["rejected"] == 1
        assert task.result_summary["draft_created"] == 0
        assert task.result_summary["rejected_detail"][0]["operation_id"] == "listUsers"
        assert "校验失败" in task.result_summary["rejected_detail"][0]["reason"]
        assert s.query(TestCase).count() == 0  # 不建坏用例
        log = s.query(GenerationLog).one()
        assert log.status == "validation_failed"
        assert log.ai_confidence == 0.0
        assert log.raw_response is not None  # 原始响应落库


def test_run_generation_skipped_filter(session_factory):
    # 定向含不存在 ID → skipped 记录（不静默忽略）
    task_id = _seed_task(session_factory, operation_ids=["listUsers", "ghostOp"])
    run_generation(session_factory, task_id, llm=FakeLlmClient())
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        assert task.result_summary["skipped_by_filter"] == 1
        assert task.result_summary["skipped_detail"] == [
            "operation_id 'ghostOp' not found in document"
        ]


def test_run_generation_warnings_trust_score(session_factory):
    # 解析有 warnings（重复 operation_id）→ trust_score=60
    dup_doc = _swagger_doc(
        [
            {"path": "/a", "method": "GET", "operation_id": "dup"},
            {"path": "/b", "method": "POST", "operation_id": "dup"},
        ]
    )
    task_id = _seed_task(session_factory, document=dup_doc)
    run_generation(session_factory, task_id, llm=FakeLlmClient())
    with session_factory() as s:
        cases = list(s.scalars(select(TestCase)))
        assert cases and all(c.trust_score == 60 for c in cases)


def test_run_generation_sanitizes_llm_output(session_factory):
    # §10.2：LLM 输出中的 <script> 等危险标签/控制字符入库前清洗
    task_id = _seed_task(session_factory, operation_ids=["listUsers"])
    evil = {
        "cases": [
            {
                "name": "<script>alert(1)</script>正向",
                "method": "GET",
                "path": "/users",
                "params": {},
                "body": None,
                "expected_status": 200,
            }
        ]
    }
    run_generation(session_factory, task_id, llm=FakeLlmClient(data=evil))
    with session_factory() as s:
        case = s.query(TestCase).one()
        assert "<script>" not in case.name  # 危险标签剔除（§10.2）；残留文本在前端自动转义下安全
        assert "正向" in case.name


def test_run_generation_parse_failure(session_factory):
    task_id = _seed_task(session_factory, document={"openapi": "3.0.3"})  # 缺 paths
    run_generation(session_factory, task_id, llm=FakeLlmClient())
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        assert task.status == GenerationStatus.FAILED.value
        assert task.error_stage == "parse"
        assert s.query(TestCase).count() == 0  # 入口拦截，不产生 draft


def test_run_generation_unexpected_error_fails_internal(session_factory):
    # 非 AppError 意外异常（如 llm_client 内部 AttributeError）→ 外层兜底落 FAILED(internal)，不卡 RUNNING
    task_id = _seed_task(session_factory)
    run_generation(session_factory, task_id, llm=FakeLlmClient(raise_error=RuntimeError("boom")))
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        assert task.status == GenerationStatus.FAILED.value
        assert task.error_stage == "internal"
        assert s.query(TestCase).count() == 0  # 未完成不残留部分 draft


def test_run_generation_partial_success(session_factory):
    # 宽容批处理：1 个失败 + 其余成功 → 任务仍 SUCCESS、成功 op 建用例、失败 op 记 log 继续
    calls = {"n": 0}

    class _MixedLlm(FakeLlmClient):
        def chat_json(self, system, user, *, schema):
            calls["n"] += 1
            if calls["n"] == 1:
                raise AppError("LLM_FAILED", status_code=502, detail="mock fail")
            return super().chat_json(system, user, schema=schema)

    task_id = _seed_task(session_factory)  # 3 个 operation
    run_generation(session_factory, task_id, llm=_MixedLlm())
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        assert task.status == GenerationStatus.SUCCESS.value  # 部分失败不整任务 FAILED
        assert task.result_summary["rejected"] == 1
        assert task.result_summary["generated"] == 2
        assert task.result_summary["draft_created"] == 2
        assert s.query(TestCase).count() == 2  # 成功 op 用例保留
        statuses = {log.status for log in s.query(GenerationLog).all()}
        assert {"success", "error"} <= statuses  # 失败与成功都审计


def test_create_concurrent_run_id_collision(session_factory, monkeypatch):
    # 并发竞态：check-then-insert 窗口内第二个请求 commit 撞 UNIQUE(run_id)（§11.1 唯一约束冲突必测场景）
    from sqlalchemy.exc import IntegrityError

    with session_factory() as s:
        svc = GenerationService(s)
        monkeypatch.setattr("app.services.generation_service.dispatch_generation", lambda _tid: None)
        svc.create_generation_task(DOC)  # 插入 run_id=X
        # 模拟并发第二个请求：find 未命中（TOCTOU），commit 撞唯一约束
        monkeypatch.setattr(svc.repo, "find_by_run_id", lambda rid: None)
        with pytest.raises(IntegrityError):
            svc.create_generation_task(DOC)
