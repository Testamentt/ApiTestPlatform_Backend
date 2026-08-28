# OpenAPI 解析器单测：提取/$ref/allOf/oneOf/array/自动生成归一化/分段 hash/contracts/warnings/宽容解析/非法文档。
from __future__ import annotations

import pytest
from app.core.exceptions import AppError
from app.utils.openapi_parser import parse_openapi


def _swagger(operations, schemas=None, openapi="3.0.3", info=None):
    paths = {}
    for op in operations:
        paths.setdefault(op["path"], {})[op["method"].lower()] = {
            "operationId": op.get("operation_id"),
            "parameters": op.get("parameters", []),
            "requestBody": op.get("request_body"),
            "responses": op.get("responses", {"200": {"description": "ok"}}),
            "description": op.get("description", "d"),
        }
    doc = {"openapi": openapi, "info": info or {"title": "t", "version": "1.0"}, "paths": paths}
    if schemas is not None:
        doc["components"] = {"schemas": schemas}
    return doc


def _users_op(**over):
    return {
        "path": "/users",
        "method": "GET",
        "operation_id": "listUsers",
        "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
        **over,
    }


# ---------- 基础提取 ----------


def test_parse_extracts_operations():
    doc = _swagger(
        [
            {"path": "/users", "method": "GET", "operation_id": "listUsers"},
            {"path": "/users", "method": "POST", "operation_id": "createUser"},
            {"path": "/users/{id}", "method": "DELETE", "operation_id": "deleteUser"},
        ]
    )
    api = parse_openapi(doc)
    assert api.operation_ids == ["createUser", "deleteUser", "listUsers"]  # 排序确定性
    assert set(api.operation_hashes) == set(api.operation_ids)
    assert set(api.operation_contracts) == set(api.operation_ids)
    assert api.warnings == []


# ---------- $ref / 组合 ----------


def test_ref_resolution_inlines_component():
    doc = _swagger(
        [
            {
                "path": "/users",
                "method": "POST",
                "operation_id": "createUser",
                "request_body": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/User"}}
                    },
                },
            }
        ],
        schemas={
            "User": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            }
        },
    )
    api = parse_openapi(doc)
    contract = api.operation_contracts["createUser"]
    assert contract["required"] == ["name"]  # $ref 内联后 required 可提取
    assert contract["signature"]["body.name"] == {"type": "string", "enum": []}


def test_allof_merges_properties_and_required():
    doc = _swagger(
        [
            {
                "path": "/users",
                "method": "POST",
                "operation_id": "createUser",
                "request_body": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "allOf": [
                                    {
                                        "type": "object",
                                        "properties": {"a": {"type": "string"}},
                                        "required": ["a"],
                                    },
                                    {"$ref": "#/components/schemas/Base"},
                                ]
                            }
                        }
                    }
                },
            }
        ],
        schemas={
            "Base": {"type": "object", "properties": {"b": {"type": "integer"}}, "required": ["b"]}
        },
    )
    contract = parse_openapi(doc).operation_contracts["createUser"]
    assert set(contract["required"]) == {"a", "b"}  # allOf 合并 required
    assert set(contract["signature"]) == {"body.a", "body.b"}


def test_oneof_takes_first_and_warns():
    doc = _swagger(
        [
            {
                "path": "/users",
                "method": "POST",
                "operation_id": "createUser",
                "request_body": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "oneOf": [
                                    {"type": "string"},
                                    {"type": "integer"},
                                ]
                            }
                        }
                    }
                },
            }
        ]
    )
    api = parse_openapi(doc)
    assert any("oneOf/anyOf" in w for w in api.warnings)
    assert api.operation_contracts["createUser"]["signature"]["body"]["type"] == "string"  # 取首个


def test_array_items_recursed():
    doc = _swagger(
        [
            {
                "path": "/users",
                "method": "POST",
                "operation_id": "createUser",
                "request_body": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "array",
                                "items": {"type": "string", "enum": ["a", "b"]},
                            }
                        }
                    }
                },
            }
        ]
    )
    contract = parse_openapi(doc).operation_contracts["createUser"]
    assert contract["signature"]["body[]"] == {"type": "string", "enum": ["a", "b"]}


# ---------- 自动生成 + 归一化 ----------


def test_autogen_operation_id_normalizes_path_params():
    d1 = _swagger([{"path": "/users/{id}", "method": "GET"}])
    d2 = _swagger([{"path": "/users/{userId}", "method": "GET"}])
    id1 = parse_openapi(d1).operation_ids[0]
    id2 = parse_openapi(d2).operation_ids[0]
    assert id1 == "GET_/users/:param"  # 归一化
    assert id1 == id2  # {id} 与 {userId} 不误判（必挂场景）


# ---------- 分段 hash ----------


def test_request_change_only_request_hash():
    h1 = parse_openapi(_swagger([_users_op()])).operation_hashes["listUsers"]
    h2 = parse_openapi(
        _swagger(
            [
                _users_op(
                    parameters=[
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer"},
                            "required": True,
                        }
                    ]
                )
            ]
        )
    ).operation_hashes["listUsers"]
    assert h1["request"] != h2["request"]  # required 变化 → 请求 hash 变
    assert h1["response"] == h2["response"]  # 响应未动


def test_status_code_change_changes_response_hash():
    # F1 必挂场景：200→202（schema 相同）也必须命中
    base = {"path": "/tasks/{id}", "method": "GET", "operation_id": "getTask"}
    d1 = _swagger(
        [
            {
                **base,
                "responses": {
                    "200": {"content": {"application/json": {"schema": {"type": "object"}}}}
                },
            }
        ]
    )
    d2 = _swagger(
        [
            {
                **base,
                "responses": {
                    "202": {"content": {"application/json": {"schema": {"type": "object"}}}}
                },
            }
        ]
    )
    h1 = parse_openapi(d1).operation_hashes["getTask"]
    h2 = parse_openapi(d2).operation_hashes["getTask"]
    assert h1["response"] != h2["response"]
    assert h1["request"] == h2["request"]


def test_hash_ignores_description():
    # 必挂场景：只改 description → 全 hash 不变（白名单键不误报）
    h1 = parse_openapi(_swagger([_users_op(description="获取用户列表")])).operation_hashes[
        "listUsers"
    ]
    h2 = parse_openapi(_swagger([_users_op(description="获取所有用户")])).operation_hashes[
        "listUsers"
    ]
    assert h1 == h2


def test_hash_changes_on_constraint_tightening():
    # 约束变更必改：新增 required 字段
    base = _users_op()
    h1 = parse_openapi(_swagger([base])).operation_hashes["listUsers"]
    h2 = parse_openapi(
        _swagger(
            [
                {
                    **base,
                    "request_body": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        }
                    },
                }
            ]
        )
    ).operation_hashes["listUsers"]
    assert h1["request"] != h2["request"]


# ---------- 字段级变更（properties 内）必须命中 hash（防 _normalize 剥离属性名） ----------


def _body_op(schema):
    return {
        "path": "/users",
        "method": "POST",
        "operation_id": "createUser",
        "request_body": {"content": {"application/json": {"schema": schema}}},
    }


def test_hash_detects_property_type_change():
    # 关键回归：嵌套属性 type 变化（string→integer）必须改变 hash（旧实现 properties 被归一化为 {}）
    d1 = _swagger([_body_op({"type": "object", "properties": {"age": {"type": "integer"}}})])
    d2 = _swagger([_body_op({"type": "object", "properties": {"age": {"type": "string"}}})])
    h1 = parse_openapi(d1).operation_hashes["createUser"]
    h2 = parse_openapi(d2).operation_hashes["createUser"]
    assert h1["request"] != h2["request"]


def test_hash_detects_property_removed():
    d1 = _swagger(
        [
            _body_op(
                {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
                }
            )
        ]
    )
    d2 = _swagger([_body_op({"type": "object", "properties": {"name": {"type": "string"}}})])
    h1 = parse_openapi(d1).operation_hashes["createUser"]
    h2 = parse_openapi(d2).operation_hashes["createUser"]
    assert h1["request"] != h2["request"]


def test_hash_detects_enum_removed_in_property():
    d1 = _swagger(
        [
            _body_op(
                {
                    "type": "object",
                    "properties": {"status": {"type": "string", "enum": ["active", "pending"]}},
                }
            )
        ]
    )
    d2 = _swagger(
        [
            _body_op(
                {"type": "object", "properties": {"status": {"type": "string", "enum": ["active"]}}}
            )
        ]
    )
    h1 = parse_openapi(d1).operation_hashes["createUser"]
    h2 = parse_openapi(d2).operation_hashes["createUser"]
    assert h1["request"] != h2["request"]


def test_hash_keeps_ignoring_description_inside_property():
    # 属性内部 description 仍不参与 hash（不因修复而误报）
    d1 = _swagger(
        [
            _body_op(
                {"type": "object", "properties": {"name": {"type": "string", "description": "旧"}}}
            )
        ]
    )
    d2 = _swagger(
        [
            _body_op(
                {"type": "object", "properties": {"name": {"type": "string", "description": "新"}}}
            )
        ]
    )
    h1 = parse_openapi(d1).operation_hashes["createUser"]
    h2 = parse_openapi(d2).operation_hashes["createUser"]
    assert h1 == h2


def test_hash_is_sha256_hexdigest():
    # review L3：指纹算法 md5→sha256——64 位 hex
    api = parse_openapi(_swagger([_users_op()]))
    for h in api.operation_hashes["listUsers"].values():
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


def test_deep_nesting_truncated_with_warning():
    # review L6：超深嵌套不抛 RecursionError——截断展开并记 warning（降级不静默）
    deep = {"type": "object"}
    for _ in range(60):
        deep = {"type": "object", "properties": {"a": deep}}
    api = parse_openapi(_swagger([_body_op(deep)]))
    assert any("深度超限" in w for w in api.warnings)
    assert api.operation_ids == ["createUser"]  # 宽容解析仍产出 operation


# ---------- contracts ----------


def test_contract_required_and_signature():
    doc = _swagger(
        [
            {
                "path": "/users",
                "method": "POST",
                "operation_id": "createUser",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                ],
                "request_body": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "status": {"type": "string", "enum": ["active", "pending"]}
                                },
                                "required": ["status"],
                            }
                        }
                    }
                },
            }
        ]
    )
    c = parse_openapi(doc).operation_contracts["createUser"]
    assert c["required"] == ["id", "status"]
    assert c["signature"]["param:path:id"] == {"type": "integer", "enum": []}
    assert c["signature"]["body.status"] == {"type": "string", "enum": ["active", "pending"]}


# ---------- warnings（宽容解析不静默） ----------


def test_warning_broken_ref():
    doc = _swagger(
        [
            _users_op(
                parameters=[
                    {"name": "x", "in": "query", "schema": {"$ref": "#/components/schemas/Nope"}}
                ]
            )
        ],
        schemas={},
    )
    api = parse_openapi(doc)
    assert any("目标不存在" in w for w in api.warnings)


def test_warning_external_ref_preserved():
    doc = _swagger(
        [
            {
                **_users_op(),
                "request_body": {
                    "content": {"application/json": {"schema": {"$ref": "./common.yaml#/User"}}}
                },
            }
        ]
    )
    api = parse_openapi(doc)
    assert any("跨文件" in w for w in api.warnings)


def test_warning_duplicate_operation_id():
    doc = _swagger(
        [
            {"path": "/a", "method": "GET", "operation_id": "dup"},
            {"path": "/b", "method": "POST", "operation_id": "dup"},
        ]
    )
    api = parse_openapi(doc)
    assert any("operation_id 重复" in w for w in api.warnings)


def test_malformed_schema_is_tolerated():
    # 宽容解析：畸形结构不抛，正常返回
    doc = _swagger(
        [
            {
                **_users_op(),
                "request_body": {"content": {"application/json": {"schema": {"type": "bogus"}}}},
            }
        ]
    )
    api = parse_openapi(doc)
    assert api.operation_ids == ["listUsers"]


# ---------- 非法文档 ----------


def test_missing_paths_rejected():
    with pytest.raises(AppError):
        parse_openapi({"openapi": "3.0.3", "info": {}})


def test_non_openapi3_rejected():
    with pytest.raises(AppError):
        parse_openapi({"swagger": "2.0", "paths": {}})


def test_no_valid_operations_rejected():
    with pytest.raises(AppError):
        parse_openapi(_swagger([{"path": "/users", "method": "TRACE"}], openapi="3.0.3"))
