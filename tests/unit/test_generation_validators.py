# 本项目编码格式：UTF-8（无 BOM）。
# 语义校验单测：用 operation 契约反向校验 LLM 输出。
# 覆盖重点是「哪些判 error、哪些必须放行」——真实文档大面积不可判，误杀比漏判更伤生成率。
from __future__ import annotations

import pytest
from app.schemas.generate import GeneratedCase
from app.services.generation_validators import (
    contract_from_operation,
    has_error,
    path_matches,
    schema_leaves,
    validate_case,
)
from app.utils.openapi_parser import parse_openapi


def _op(
    *,
    method="GET",
    path="/users",
    operation_id="op",
    parameters=None,
    request_body=None,
    responses=None,
):
    """构造单个 operation 的 ParsedOperation（走真实解析器，不手搓 dataclass）。"""
    op: dict = {
        "operationId": operation_id,
        "responses": responses or {"200": {"description": "ok"}},
    }
    if parameters is not None:
        op["parameters"] = parameters
    if request_body is not None:
        op["requestBody"] = request_body
    doc = {"openapi": "3.0.3", "info": {"title": "t", "version": "1"}, "paths": {path: {}}}
    doc["paths"][path][method.lower()] = op
    return parse_openapi(doc).operations[0]


def _case(**over):
    base = {
        "name": "用例",
        "method": "GET",
        "path": "/users",
        "expected_status": 200,
        "assertions": [{"path": "status_code", "op": "eq", "value": 200}],
    }
    base.update(over)
    return GeneratedCase.model_validate(base)


# ---------- 必测四项：method / path / 编造字段 / 断言字段 ----------


def test_method_mismatch_is_error():
    """用户举的反例：文档是 GET，LLM 返回 DELETE —— 确认后执行会真发 DELETE。"""
    contract = contract_from_operation(_op(method="GET"))
    case = _case(method="DELETE")
    violations = validate_case(case, contract)
    assert [v.rule for v in violations] == ["method_mismatch"]
    assert violations[0].level == "error"
    assert has_error(violations)


def test_path_mismatch_is_error():
    contract = contract_from_operation(_op(path="/users/{id}"))
    case = _case(path="/orders/1")
    violations = validate_case(case, contract)
    assert "path_mismatch" in [v.rule for v in violations]
    assert has_error(violations)


def test_path_param_instantiation_allowed():
    """文档 /users/{id} 允许生成 /users/1——参数实例化不是不匹配。"""
    contract = contract_from_operation(_op(path="/users/{id}"))
    assert not has_error(validate_case(_case(path="/users/1"), contract))
    assert not has_error(validate_case(_case(path="/users/abc-def"), contract))


def test_path_extra_segment_rejected():
    contract = contract_from_operation(_op(path="/users/{id}"))
    assert has_error(validate_case(_case(path="/users/1/extra"), contract))


def test_path_trailing_slash_normalized():
    """尾斜杠在多数框架里等价，不该判违规。"""
    contract = contract_from_operation(_op(path="/users"))
    assert not has_error(validate_case(_case(path="/users/"), contract))


def test_unknown_params_is_error():
    contract = contract_from_operation(
        _op(parameters=[{"name": "limit", "in": "query", "schema": {"type": "integer"}}])
    )
    case = _case(params={"page": 1})
    violations = validate_case(case, contract)
    assert "params_unknown" in [v.rule for v in violations]
    assert has_error(violations)


def test_declared_params_allowed():
    contract = contract_from_operation(
        _op(parameters=[{"name": "limit", "in": "query", "schema": {"type": "integer"}}])
    )
    assert not has_error(validate_case(_case(params={"limit": 10}), contract))


def test_body_unknown_key_is_error():
    """properties 可枚举时才判 error——这是能判的 6/96 情形。"""
    contract = contract_from_operation(
        _op(
            method="POST",
            path="/users",
            request_body={
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                            "required": ["name"],
                        }
                    }
                },
            },
        )
    )
    case = _case(method="POST", body={"admin": True, "name": "x"})
    violations = validate_case(case, contract)
    assert "body_unknown_key" in [v.rule for v in violations]
    assert has_error(violations)


def test_body_missing_required_is_error():
    contract = contract_from_operation(
        _op(
            method="POST",
            request_body={
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
        )
    )
    violations = validate_case(_case(method="POST", body={"other": 1}), contract)
    rules = [v.rule for v in violations]
    assert "body_missing_required" in rules


def test_body_free_map_is_warn_not_error():
    """真实 ERP 文档 90/96 个请求体是 additionalProperties 自由 map ——
    判 error 会把它们全部拒掉，这里必须只 warn 放行。"""
    contract = contract_from_operation(
        _op(
            method="POST",
            request_body={
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "additionalProperties": {"type": "object"}}
                    }
                }
            },
        )
    )
    violations = validate_case(_case(method="POST", body={"anything": 1}), contract)
    assert not has_error(violations)
    assert [v.rule for v in violations] == ["body_unjudgeable"]
    assert violations[0].level == "warn"


def test_assertion_unknown_field_is_warn_not_error():
    contract = contract_from_operation(
        _op(
            responses={
                "200": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"code": {"type": "integer"}},
                            }
                        }
                    }
                }
            }
        )
    )
    case = _case(
        assertions=[
            {"path": "status_code", "op": "eq", "value": 200},
            {"path": "code", "op": "eq", "value": 0},
            {"path": "data.fabricated", "op": "eq", "value": 1},
        ]
    )
    violations = validate_case(case, contract)
    assert not has_error(violations)  # 断言问题只降分
    unknown = [v for v in violations if v.rule == "assertion_path_unknown"]
    assert len(unknown) == 1
    assert "data.fabricated" in unknown[0].detail


def test_assertion_under_opaque_node_allowed():
    """真实 ERP 文档 106 个 op 的 200.data 是裸 object ——
    data.records 这类断言无法判定，必须放行而不是判成编造。"""
    contract = contract_from_operation(
        _op(
            responses={
                "200": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "code": {"type": "integer"},
                                    "data": {"type": "object"},
                                },
                            }
                        }
                    }
                }
            }
        )
    )
    case = _case(
        assertions=[
            {"path": "status_code", "op": "eq", "value": 200},
            {"path": "data.records", "op": "exists"},
            {"path": "data.records.0.id", "op": "exists"},
        ]
    )
    violations = validate_case(case, contract)
    assert [v.rule for v in violations] == []


def test_assertion_array_index_normalized():
    """断言写 items.0.name，schema 叶子是 items[].name —— 不归一会全部误判。"""
    contract = contract_from_operation(
        _op(
            responses={
                "200": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "items": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {"name": {"type": "string"}},
                                        },
                                    }
                                },
                            }
                        }
                    }
                }
            }
        )
    )
    case = _case(
        assertions=[
            {"path": "status_code", "op": "eq", "value": 200},
            {"path": "items.0.name", "op": "eq", "value": "a"},
            {"path": "items.3.name", "op": "exists"},
        ]
    )
    assert [v.rule for v in validate_case(case, contract)] == []


# ---------- expected_status：文档没声明 4xx 时不扣分 ----------


def test_undeclared_status_warned_when_doc_declares_4xx():
    contract = contract_from_operation(
        _op(responses={"200": {"description": "ok"}, "404": {"description": "nf"}})
    )
    violations = validate_case(_case(expected_status=500), contract)
    assert [v.rule for v in violations] == ["expected_status_undeclared"]
    assert violations[0].level == "warn"


def test_4xx_case_not_penalized_when_doc_has_no_4xx():
    """文档只声明 2xx 时，4xx 用例是 prompt 要求产出的探索用例——
    把「文档写得糙」算在用例头上会让评分反向激励。"""
    contract = contract_from_operation(_op(responses={"200": {"description": "ok"}}))
    violations = validate_case(_case(expected_status=400), contract)
    assert violations == []


def test_declared_status_ok():
    contract = contract_from_operation(_op(responses={"200": {"description": "ok"}}))
    assert validate_case(_case(expected_status=200), contract) == []


# ---------- 契约视图 / 工具函数 ----------


def test_contract_from_operation_extracts_body_shape():
    contract = contract_from_operation(
        _op(
            method="POST",
            request_body={
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
                            "required": ["a"],
                        }
                    }
                }
            },
        )
    )
    assert contract.body_properties == {"a", "b"}
    assert contract.body_required == {"a"}
    assert contract.op_method == "POST"


def test_contract_body_properties_empty_for_free_map():
    contract = contract_from_operation(
        _op(
            method="POST",
            request_body={
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "additionalProperties": {"type": "object"}}
                    }
                }
            },
        )
    )
    assert contract.body_properties == set()  # 不可枚举 → 校验方据此降级为 warn


def test_schema_leaves_separates_known_and_opaque():
    leaves, opaque = schema_leaves(
        {
            "type": "object",
            "properties": {"code": {"type": "integer"}, "data": {"type": "object"}},
        }
    )
    assert leaves == {"code"}
    assert opaque == {"data"}


def test_schema_leaves_handles_nested_and_arrays():
    leaves, opaque = schema_leaves(
        {
            "type": "object",
            "properties": {
                "list": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                    },
                }
            },
        }
    )
    assert leaves == {"list[].id"}
    assert opaque == set()


def test_schema_leaves_root_array_emits_no_empty_prefix():
    """根就是数组时不能产出 `[]`——它不是合法断言路径（AssertionItem 要求字母开头），
    会误导调用方拼出非法断言。"""
    leaves, opaque = schema_leaves(
        {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer"}}}}
    )
    assert leaves == set()
    assert opaque == set()


def test_schema_leaves_non_dict_node_is_opaque():
    """畸形 schema（布尔/字符串等非 dict 值）按不可判处理——宽容解析的产物不应让校验崩溃。"""
    leaves, opaque = schema_leaves(
        {"type": "object", "properties": {"weird": True, "ok": {"type": "string"}}}
    )
    assert leaves == {"ok"}
    assert opaque == {"weird"}


def test_path_matches_direct():
    assert path_matches("/users", "/users")
    assert path_matches("/users/1", "/users/{id}")
    assert not path_matches("/users", "/users/{id}")
    assert not path_matches("/users/1/extra", "/users/{id}")


@pytest.mark.parametrize(
    ("case_path", "op_path"),
    [
        ("/plugin/start/123", "/plugin/start/{id}"),
        ("/plugin/uninstall/9", "/plugin/uninstall/{id}"),
        ("/account/list", "/account/list"),
    ],
)
def test_path_matches_real_erp_shapes(case_path, op_path):
    """真实 ERP 文档里仅有的 4 个带路径参数的 op，规格必须支持。"""
    assert path_matches(case_path, op_path)
