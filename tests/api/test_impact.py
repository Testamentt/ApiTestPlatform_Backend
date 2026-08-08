# 解析与影响分析接口测试：parse 落库 / analyze 对比 / regression 一键回归（eager + FakeSubprocess 隔离）。
from __future__ import annotations

from types import SimpleNamespace


def _doc(path="/users", method="get", operation_id="listUsers", body_schema=None, responses=None):
    op = {"operationId": operation_id}
    if body_schema is not None:
        op["requestBody"] = {"content": {"application/json": {"schema": body_schema}}}
    op["responses"] = responses or {"200": {"description": "ok"}}
    return {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1.0"},
        "paths": {path: {method: op}},
    }


def _bound_active_case(client, operation_id="listUsers"):
    """创建并确认一个绑定 operation_id 的 active 用例。"""
    cid = client.post(
        "/api/v1/cases",
        json={"name": "c", "operation_id": operation_id, "method": "GET", "path": "/users"},
    ).json()["data"]["id"]
    client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "demo"})
    return cid


# ---------- parse ----------

def test_parse_201_creates_version(client):
    r = client.post("/api/v1/parse", json={"document": _doc()})
    assert r.status_code == 201
    data = r.json()["data"]
    assert data["version"] == "v1"
    assert data["operation_count"] == 1
    assert data["operation_ids"] == ["listUsers"]
    assert data["warnings"] == []


def test_parse_auto_version_increments(client):
    client.post("/api/v1/parse", json={"document": _doc()})
    d2 = _doc(operation_id="createUser", path="/users")
    d2["paths"]["/users"]["post"] = d2["paths"]["/users"].pop("get")
    r = client.post("/api/v1/parse", json={"document": d2})
    assert r.status_code == 201
    assert r.json()["data"]["version"] == "v2"


def test_parse_invalid_document_422(client):
    r = client.post("/api/v1/parse", json={"document": {"openapi": "2.0", "paths": {}}})
    assert r.status_code == 422


def test_parse_too_large_422(client, monkeypatch):
    from app.services import swagger_service

    fake = SimpleNamespace(
        swagger=SimpleNamespace(max_upload_bytes=10, hash_version=1, max_operation_ids_warn=200)
    )
    monkeypatch.setattr(swagger_service, "get_settings", lambda: fake)
    r = client.post("/api/v1/parse", json={"document": _doc()})
    assert r.status_code == 422


# ---------- analyze ----------

def test_analyze_first_time_all_added(client):
    r = client.post("/api/v1/impact/analyze", json={"document": _doc()})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["old_version"] is None
    assert data["added_ops"] == ["listUsers"]
    assert data["untested_ops"] == ["listUsers"]  # 首次 = 全部
    assert data["affected_cases"] == []


def test_analyze_identical_no_changes(client):
    doc = _doc()
    client.post("/api/v1/parse", json={"document": doc})
    r = client.post("/api/v1/impact/analyze", json={"document": doc})
    data = r.json()["data"]
    assert data["changed_ops"] == []
    assert data["breaking_changed_ops"] == []


def test_analyze_field_change_detected_and_affects_case(client):
    # 字段级 type 变化必须命中 hash + breaking，并圈定绑定用例（原 _normalize 缺陷回归防线）
    v1 = _doc(body_schema={"type": "object", "properties": {"age": {"type": "integer"}}})
    v2 = _doc(body_schema={"type": "object", "properties": {"age": {"type": "string"}}})
    client.post("/api/v1/parse", json={"document": v1})
    _bound_active_case(client)
    r = client.post("/api/v1/impact/analyze", json={"document": v2})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["breaking_changed_ops"] == ["listUsers"]
    assert len(data["affected_cases"]) == 1
    assert data["affected_cases"][0]["case_id"] > 0
    assert data["affected_summary"]["total"] == 1


def test_analyze_version_collision_409(client):
    doc = _doc()
    client.post("/api/v1/parse", json={"document": doc})
    # 显式 new_version 与对比基线（最近版本 v1）相同 → 拒绝覆盖历史快照
    r = client.post("/api/v1/impact/analyze", json={"document": doc, "new_version": "v1"})
    assert r.status_code == 409


# ---------- regression ----------

def test_regression_executes_affected(client, patch_sessionlocal, fake_execution):
    v1 = _doc(body_schema={"type": "object", "properties": {"age": {"type": "integer"}}})
    v2 = _doc(body_schema={"type": "object", "properties": {"age": {"type": "string"}}})
    client.post("/api/v1/parse", json={"document": v1})
    _bound_active_case(client)
    analysis_id = client.post("/api/v1/impact/analyze", json={"document": v2}).json()["data"]["analysis_id"]

    r = client.post(f"/api/v1/impact/{analysis_id}/regression")
    assert r.status_code == 202
    data = r.json()["data"]
    assert data["executed_count"] == 1
    assert data["dropped_count"] == 0
    assert data["affected_summary"] == {"total": 1, "executed": 1, "dropped": 0}
    # eager 执行完成：任务为 success 且 passed=1
    task = client.get(f"/api/v1/tasks/{data['task_id']}").json()["data"]
    assert task["status"] == "success"
    assert task["result_summary"]["passed"] == 1


def test_regression_no_affected_422(client):
    doc = _doc()
    client.post("/api/v1/parse", json={"document": doc})
    analysis_id = client.post("/api/v1/impact/analyze", json={"document": doc}).json()["data"]["analysis_id"]
    r = client.post(f"/api/v1/impact/{analysis_id}/regression")
    assert r.status_code == 422


def test_regression_all_cases_dropped_422(client):
    v1 = _doc(body_schema={"type": "object", "properties": {"age": {"type": "integer"}}})
    v2 = _doc(body_schema={"type": "object", "properties": {"age": {"type": "string"}}})
    client.post("/api/v1/parse", json={"document": v1})
    cid = _bound_active_case(client)
    analysis_id = client.post("/api/v1/impact/analyze", json={"document": v2}).json()["data"]["analysis_id"]
    client.delete(f"/api/v1/cases/{cid}")  # 受影响用例被删 → 宽容降级后全部失效
    r = client.post(f"/api/v1/impact/{analysis_id}/regression")
    assert r.status_code == 422


def test_regression_partial_drop(client, patch_sessionlocal, fake_execution):
    # 部分受影响用例失效：宽容降级只执行仍 active 的，dropped 附 reason
    v1 = _doc(body_schema={"type": "object", "properties": {"age": {"type": "integer"}}})
    v2 = _doc(body_schema={"type": "object", "properties": {"age": {"type": "string"}}})
    client.post("/api/v1/parse", json={"document": v1})
    c1 = _bound_active_case(client, "listUsers")
    _bound_active_case(client, "listUsers")  # 第二个 active 用例（回归中保留）
    analysis_id = client.post("/api/v1/impact/analyze", json={"document": v2}).json()["data"]["analysis_id"]
    client.delete(f"/api/v1/cases/{c1}")  # 回归前删除其中一个 → 宽容降级

    r = client.post(f"/api/v1/impact/{analysis_id}/regression")
    assert r.status_code == 202
    data = r.json()["data"]
    assert data["executed_count"] == 1
    assert data["dropped_case_ids"] == [c1]
    assert data["dropped_reasons"][str(c1)] == "case deleted"
    assert data["affected_summary"] == {"total": 2, "executed": 1, "dropped": 1}
