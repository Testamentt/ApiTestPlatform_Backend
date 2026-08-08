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


DOC = _swagger_doc([
    {"path": "/users", "method": "GET", "operation_id": "listUsers"},
    {"path": "/users", "method": "POST", "operation_id": "createUser"},
])


@pytest.fixture()
def no_dispatch(monkeypatch):
    # why：避免 eager 模式下 create 触发真实 LLM 调用（单元测试隔离，tasks 测试再覆盖执行）
    monkeypatch.setattr(
        "app.services.generation_service.GenerationService._dispatch", lambda self, tid: None
    )


def _seed_impact(session_factory, *, untested_ops):
    with session_factory() as s:
        s.add(ImpactAnalysis(
            new_version="v1", added_ops=[], removed_ops=[], changed_ops=[],
            breaking_changed_ops=[], affected_case_ids=[], orphaned_case_ids=[],
            suggested_remap={}, untested_ops=untested_ops, affected_summary={},
        ))
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


def test_get_generation(client, no_dispatch):
    tid = client.post("/api/v1/generate", json={"document": DOC}).json()["data"]["id"]
    r = client.get(f"/api/v1/generate/{tid}")
    assert r.status_code == 200
    assert r.json()["data"]["id"] == tid
    assert r.json()["data"]["status"] == "pending"


def test_get_generation_missing_404(client):
    assert client.get("/api/v1/generate/99999").status_code == 404
