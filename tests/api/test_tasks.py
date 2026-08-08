# 任务接口测试：202 立即返回 / Lookup-Create 幂等 / draft 不可执行 / 结果查询。
from __future__ import annotations

import pytest


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


def test_create_task_202_and_execute(client, patch_sessionlocal, fake_execution, active_case_factory):
    cid = active_case_factory()
    r = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    assert r.status_code == 202
    tid = r.json()["data"]["id"]
    # eager 已同步执行完毕
    t = client.get(f"/api/v1/tasks/{tid}").json()["data"]
    assert t["status"] == "success"
    assert t["result_summary"]["passed"] == 1
    assert t["report_link"].startswith("/static/reports/")


def test_lookup_create_returns_same_task(client, patch_sessionlocal, fake_execution, active_case_factory):
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
