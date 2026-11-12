# 本项目编码格式：UTF-8（无 BOM）。
# confirm 路径语义校验：激活是进执行池的唯一入口，形状合法不等于「属于这个接口」。
from __future__ import annotations

import json

from app.models.api_definition import ApiDefinition
from app.models.test_case import TestCase
from app.utils.openapi_parser import parse_openapi

DOC = {
    "openapi": "3.0.3",
    "info": {"title": "t", "version": "1"},
    "paths": {
        "/users/{id}": {
            "get": {
                "operationId": "getUser",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                ],
                "responses": {"200": {"description": "ok"}, "404": {"description": "nf"}},
            },
            "delete": {
                "operationId": "deleteUser",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                ],
                "responses": {"204": {"description": "done"}},
            },
        }
    },
}


def _seed_snapshot(session_factory, document=None, version="v1"):
    """把文档解析结果落成 api_definitions 快照（confirm 侧唯一数据源）。"""
    parsed = parse_openapi(document or DOC)
    with session_factory() as s:
        s.add(
            ApiDefinition(
                version=version,
                hash_version=2,
                operation_ids=parsed.operation_ids,
                operation_hashes=parsed.operation_hashes,
                operation_contracts=parsed.operation_contracts,
            )
        )
        s.commit()


def _seed_case(session_factory, **over):
    payload = {
        "name": "用例",
        "operation_id": "getUser",
        "method": "GET",
        "path": "/users/1",
        "expected_status": 200,
    }
    payload.update(over)
    with session_factory() as s:
        case = TestCase(**payload)
        s.add(case)
        s.commit()
        return case.id


def _confirm(client, case_id, reviewer="demo"):
    return client.post(f"/api/v1/cases/{case_id}/confirm", json={"reviewer": reviewer})


def test_confirm_rejects_wrong_method(client, session_factory):
    """核心场景：DELETE 打到 GET 接口。形状校验全程放行，只有语义校验能拦。"""
    _seed_snapshot(session_factory)
    cid = _seed_case(session_factory, method="DELETE", path="/users/1", operation_id="getUser")
    r = _confirm(client, cid)
    assert r.status_code == 422
    assert r.json()["code"] == "CASE_CONTRACT_MISMATCH"
    assert "method" in r.json()["detail"]


def test_confirm_rejects_wrong_path(client, session_factory):
    _seed_snapshot(session_factory)
    cid = _seed_case(session_factory, path="/orders/1", operation_id="getUser")
    r = _confirm(client, cid)
    assert r.status_code == 422
    assert r.json()["code"] == "CASE_CONTRACT_MISMATCH"


def test_confirm_accepts_matching_case(client, session_factory):
    _seed_snapshot(session_factory)
    cid = _seed_case(session_factory)  # GET /users/1 符合 getUser 契约
    r = _confirm(client, cid)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "active"


def test_confirm_accepts_path_param_instantiation(client, session_factory):
    """文档 /users/{id} 允许 /users/42 —— 参数实例化不是不匹配。"""
    _seed_snapshot(session_factory)
    cid = _seed_case(session_factory, path="/users/42")
    assert _confirm(client, cid).status_code == 200


def test_confirm_rejects_unknown_params(client, session_factory):
    _seed_snapshot(session_factory)
    cid = _seed_case(session_factory, params={"page": 1})
    r = _confirm(client, cid)
    assert r.status_code == 422
    assert "params" in r.json()["detail"]


def test_confirm_does_not_block_on_warn_level(client, session_factory):
    """warn 级（期望码未在文档声明）只影响评分，不应阻断激活——
    否则手工识别出的探索性用例永远进不了执行池。"""
    _seed_snapshot(session_factory)
    cid = _seed_case(session_factory, expected_status=500)
    assert _confirm(client, cid).status_code == 200


def test_confirm_without_snapshot_degrades_to_shape_only(client):
    """库里没有快照时无从判定，必须放行（否则全新环境手工用例全部无法激活）。"""
    cid = _seed_case_simple(client)
    assert _confirm(client, cid).status_code == 200


def test_confirm_with_stale_snapshot_missing_method_degrades(client, session_factory):
    """旧版本落库的快照没有 method/path —— 降级为不阻断，不能让历史数据永久锁死。"""
    with session_factory() as s:
        s.add(
            ApiDefinition(
                version="v-old",
                hash_version=1,
                operation_ids=["getUser"],
                operation_hashes={"getUser": {"request": "x", "response": "y"}},
                operation_contracts={
                    "getUser": {
                        "required": [],
                        "signature": {},
                        "response_status_codes": ["200"],
                    }
                },
            )
        )
        s.commit()
    cid = _seed_case(session_factory, method="DELETE")  # 若按新规则应被拒
    assert _confirm(client, cid).status_code == 200  # 但旧快照判定不了 → 放行


def test_confirm_unknown_operation_id_degrades(client, session_factory):
    """operation_id 不在最新快照里（文档已移除该接口）→ 无从判定，不阻断。"""
    _seed_snapshot(session_factory)
    cid = _seed_case(session_factory, operation_id="ghostOp", path="/ghost")
    assert _confirm(client, cid).status_code == 200


def test_contract_snapshot_includes_method_and_path():
    """快照必须带 method/path —— 否则 confirm 侧核心校验无从下手。"""
    parsed = parse_openapi(DOC)
    contract = parsed.operation_contracts["getUser"]
    assert contract["method"] == "GET"
    assert contract["path"] == "/users/{id}"
    assert "200" in contract["response_schemas"]
    # 加键不得影响 hash（_hash_op 不读 contract）
    assert set(parsed.operation_hashes["getUser"]) == {"request", "response"}


def test_snapshot_contract_roundtrip():
    """解析产物 → 快照 dict → 适配器还原，校验结果必须与原契约一致。"""
    from app.schemas.generate import GeneratedCase
    from app.services.generation_validators import (
        contract_from_operation,
        contract_from_snapshot,
        validate_case,
    )

    op = next(o for o in parse_openapi(DOC).operations if o.operation_id == "getUser")
    direct = contract_from_operation(op)
    snapshot = contract_from_snapshot(json.loads(json.dumps(op.contract)))
    assert snapshot is not None

    case = GeneratedCase.model_validate(
        {
            "name": "x",
            "method": "GET",
            "path": "/users/1",
            "expected_status": 200,
            "params": {"id": 1},
        }
    )
    assert [v.rule for v in validate_case(case, direct)] == [
        v.rule for v in validate_case(case, snapshot)
    ]


def _seed_case_simple(client):
    r = client.post(
        "/api/v1/cases",
        json={"name": "c", "operation_id": "op", "method": "GET", "path": "/x"},
    )
    return r.json()["data"]["id"]
