# generate API 测试：202 / Lookup-Create 幂等 / 超大小 422 / 定向 / NO_UNTESTED / 任务查询。
from __future__ import annotations

import pytest
from app.models.generation_task import GenerationTask
from app.models.impact_analysis import ImpactAnalysis


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
    ]
)


@pytest.fixture()
def no_dispatch(monkeypatch):
    # why：避免 eager 模式下 create 触发真实 LLM 调用（单元测试隔离，tasks 测试再覆盖执行）
    monkeypatch.setattr("app.services.generation_service.dispatch_generation", lambda tid: None)


def _seed_impact(session_factory, *, untested_ops):
    with session_factory() as s:
        s.add(
            ImpactAnalysis(
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
        )
        s.commit()


def test_create_generate_202(client, no_dispatch):
    r = client.post("/api/v1/generate", json={"document": DOC})
    assert r.status_code == 202
    assert r.json()["data"]["status"] == "pending"
    assert r.json()["data"]["operation_count"] == 0


def test_create_generate_idempotent(client, no_dispatch):
    r1 = client.post("/api/v1/generate", json={"document": DOC})
    r2 = client.post("/api/v1/generate", json={"document": DOC})
    assert r1.json()["data"]["id"] == r2.json()["data"]["id"]  # Lookup-Create 幂等


def test_create_generate_too_large_422(client, no_dispatch):
    big = {"document": {"openapi": "3.0.3", "paths": {"x": {"data": "y" * 3_000_000}}}}
    r = client.post("/api/v1/generate", json=big)
    assert r.status_code == 422
    assert r.json()["code"] == "SWAGGER_TOO_LARGE"


def test_create_generate_no_untested_422(client, session_factory, no_dispatch):
    _seed_impact(session_factory, untested_ops=[])
    r = client.post("/api/v1/generate", json={"document": DOC})
    assert r.status_code == 422
    assert r.json()["code"] == "NO_UNTESTED_OPS"


def test_create_generate_uses_untested(client, session_factory, no_dispatch):
    _seed_impact(session_factory, untested_ops=["listUsers"])
    r = client.post("/api/v1/generate", json={"document": DOC})
    assert r.status_code == 202
    task_id = r.json()["data"]["id"]
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        assert task.operation_ids == ["listUsers"]


def test_create_generate_explicit_operation_ids(client, session_factory, no_dispatch):
    r = client.post("/api/v1/generate", json={"document": DOC, "operation_ids": ["createUser"]})
    assert r.status_code == 202
    task_id = r.json()["data"]["id"]
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        assert task.operation_ids == ["createUser"]  # 显式定向优先


def test_explicit_operation_ids_all_missing_422(client, no_dispatch):
    # why：A1 输入层修复——定向 id 全部未命中必须 fail fast，而不是跑成 SUCCESS/0 的假成功；
    # message 里要带合法示例，用户才知道 operation_id 长什么样（此前自由文本输入无从校验）
    r = client.post(
        "/api/v1/generate", json={"document": DOC, "operation_ids": ["post_/user/login"]}
    )
    assert r.status_code == 422
    assert r.json()["code"] == "INVALID_OPERATION_IDS"
    assert "listUsers" in r.json()["message"]  # 文档中的合法示例回传给用户


def test_explicit_operation_ids_partial_hit_runs(client, session_factory, no_dispatch):
    # 部分命中照常执行（宽容降级保留），未命中项由 Worker 记 skipped_detail
    r = client.post(
        "/api/v1/generate",
        json={"document": DOC, "operation_ids": ["listUsers", "ghost_op"]},
    )
    assert r.status_code == 202
    with session_factory() as s:
        task = s.get(GenerationTask, r.json()["data"]["id"])
        assert task.operation_ids == ["listUsers", "ghost_op"]


def test_generate_concurrent_same_input_returns_existing(client, monkeypatch):
    """C1 同类回归：生成任务并发同输入不得 500（run_id 唯一约束）。"""
    from app.repositories.generation_task_repository import GenerationTaskRepository

    monkeypatch.setattr("app.services.generation_service.dispatch_generation", lambda tid: None)
    real_lookup = GenerationTaskRepository.find_by_run_id
    calls = {"n": 0}

    def stale_lookup(self, run_id):
        calls["n"] += 1
        if calls["n"] == 2:
            return None
        return real_lookup(self, run_id)

    monkeypatch.setattr(GenerationTaskRepository, "find_by_run_id", stale_lookup)
    r1 = client.post("/api/v1/generate", json={"document": DOC})
    r2 = client.post("/api/v1/generate", json={"document": DOC})
    assert (r1.status_code, r2.status_code) == (202, 202)
    assert r1.json()["data"]["id"] == r2.json()["data"]["id"]


def test_get_generation(client, no_dispatch):
    tid = client.post("/api/v1/generate", json={"document": DOC}).json()["data"]["id"]
    r = client.get(f"/api/v1/generate/{tid}")
    assert r.status_code == 200
    assert r.json()["data"]["id"] == tid
    assert r.json()["data"]["status"] == "pending"


def test_get_generation_missing_404(client):
    assert client.get("/api/v1/generate/99999").status_code == 404


def test_create_generate_dispatch_failure_marks_failed(client, session_factory, monkeypatch):
    # why：入队失败（Celery/Redis 不可用）落 FAILED(dispatch) 而非 PENDING 孤儿（review M2）
    def _boom(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.services.generation_service.dispatch_generation", _boom)
    r = client.post("/api/v1/generate", json={"document": DOC})
    assert r.status_code == 503
    assert r.json()["code"] == "DISPATCH_FAILED"
    with session_factory() as s:
        task = s.query(GenerationTask).one()
        assert task.status == "failed"
        assert task.error_stage == "dispatch"


def test_failed_generate_retry_same_input(client, session_factory, monkeypatch):
    # why：失败生成任务同输入重试——重置 PENDING 重新入队（review H4），同一任务行复用
    def _boom(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.services.generation_service.dispatch_generation", _boom)
    r1 = client.post("/api/v1/generate", json={"document": DOC})
    assert r1.status_code == 503

    monkeypatch.setattr(
        "app.services.generation_service.dispatch_generation",
        lambda tid: None,
    )
    r2 = client.post("/api/v1/generate", json={"document": DOC})
    assert r2.status_code == 202
    with session_factory() as s:
        task = s.query(GenerationTask).one()  # 同 run_id → 同一行
        assert task.status == "pending"
        assert task.error_stage is None
        assert task.error_msg is None
    assert r2.json()["data"]["id"] == task.id
