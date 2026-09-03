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


def test_create_case_exposes_trust_score(client):
    # trust_score 可观测（手工=100），审核按血缘可信度优先 Review（P1-4 修复）
    r = _create_case(client)
    assert r.json()["data"]["trust_score"] == 100


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


def test_confirm_records_reviewer(client, session_factory):
    # why：批准人审计（RULES §11.2「需传入 reviewer」）——reviewer 必须落库可追溯（review R3-2）
    from app.models.test_case import TestCase

    cid = _create_case(client).json()["data"]["id"]
    client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "alice"})
    with session_factory() as s:
        assert s.get(TestCase, cid).reviewer == "alice"


def test_confirm_empty_reviewer_422(client):
    # 契约：reviewer min_length=1——批准人不可为空（防幻觉护栏的审计前提）
    cid = _create_case(client).json()["data"]["id"]
    r = client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "  "})
    assert r.status_code == 422


def test_confirm_revalidates_schema(client, session_factory):
    # why：RULES §11.2「转 active 时重新执行 schema 校验」——draft 期间数据被改坏必须拦在
    # 状态迁移前（review R3-2）；绕过 API 校验器直接落库的坏数据在此被激活闸拦下
    from app.models.test_case import TestCase

    cid = _create_case(client).json()["data"]["id"]
    with session_factory() as s:
        case = s.get(TestCase, cid)
        case.path = "no-slash"  # 模拟绕过 API 校验的坏数据（path 必须以 / 开头）
        s.commit()
    r = client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "demo"})
    assert r.status_code == 422
    assert r.json()["code"] == "CASE_INVALID_FOR_ACTIVE"
    with session_factory() as s:
        assert s.get(TestCase, cid).status == "draft"  # 校验失败不迁移状态


def test_update_case_fields(client):
    # why：PUT 端点此前零测试（review R3-2）——正常更新路径 + 字段落库
    cid = _create_case(client).json()["data"]["id"]
    r = client.put(
        f"/api/v1/cases/{cid}",
        json={"name": "renamed", "method": "POST", "path": "/users", "expected_status": 201},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert (data["name"], data["method"], data["path"], data["expected_status"]) == (
        "renamed",
        "POST",
        "/users",
        201,
    )


def test_update_case_rejects_bad_path(client):
    # why：CaseUpdate 校验器（path 前缀）须被测试锁定（review R3-2）
    cid = _create_case(client).json()["data"]["id"]
    r = client.put(f"/api/v1/cases/{cid}", json={"path": "users"})
    assert r.status_code == 422


def test_update_missing_404(client):
    r = client.put("/api/v1/cases/99999", json={"name": "x"})
    assert r.status_code == 404


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
