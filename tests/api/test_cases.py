# 用例接口测试：CRUD / operation_id 必填 / confirm 防幻觉 / 物理删除。
from __future__ import annotations


def _create_case(client, **over):
    payload = {"name": "get", "operation_id": "httpbin_get", "method": "GET", "path": "/get"}
    payload.update(over)
    return client.post("/api/v1/cases", json=payload)


def test_create_case_201_default_draft(client):
    r = _create_case(client)
    assert r.status_code == 201
    assert r.json()["data"]["status"] == "draft"
    assert r.json()["data"]["operation_id"] == "httpbin_get"


def test_operation_id_required(client):
    assert _create_case(client, operation_id="").status_code == 422


def test_method_whitelist(client):
    assert _create_case(client, method="TRACE").status_code == 422


def test_path_must_start_with_slash(client):
    assert _create_case(client, path="get").status_code == 422


def test_confirm_draft_to_active(client):
    cid = _create_case(client).json()["data"]["id"]
    r = client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "demo"})
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "active"


def test_confirm_idempotent(client):
    cid = _create_case(client).json()["data"]["id"]
    client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "demo"})
    r2 = client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "demo"})
    assert r2.json()["data"]["status"] == "active"


def test_physical_delete(client):
    cid = _create_case(client).json()["data"]["id"]
    assert client.delete(f"/api/v1/cases/{cid}").status_code == 204
    assert client.get(f"/api/v1/cases/{cid}").status_code == 404


def test_get_missing_404(client):
    assert client.get("/api/v1/cases/99999").status_code == 404
