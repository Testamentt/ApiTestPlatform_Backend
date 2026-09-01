# jshERP 转换产物解析冒烟（unit）。why：docs/examples/jsherp-openapi3.json 由 convert_swagger2.py
# 生成，此处内嵌真实转换片段验证 parse 闭环（结构契约回归：2.0→3.0 映射不被 parser 拒绝）。
from __future__ import annotations

import json

from app.utils.openapi_parser import parse_openapi

# 片段取自 docs/examples/jsherp-openapi3.json 真实转换输出（字段结构一致，缩减 paths）
JSHERP_SNIPPET = {
    "openapi": "3.0.3",
    "info": {
        "description": "管伊佳ERP接口描述",
        "version": "3.0",
        "title": "管伊佳ERP Restful Api",
    },
    "servers": [{"url": "http://127.0.0.1:9999/jshERP-boot"}],
    "paths": {
        "/account/checkIsNameExist": {
            "get": {
                "tags": ["账户管理"],
                "summary": "检查名称是否存在",
                "operationId": "checkIsNameExistUsingGET",
                "parameters": [
                    {
                        "name": "id",
                        "in": "query",
                        "required": True,
                        "description": "id",
                        "schema": {"type": "integer", "format": "int64"},
                    },
                    {
                        "name": "name",
                        "in": "query",
                        "required": False,
                        "description": "name",
                        "schema": {"type": "string"},
                    },
                ],
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {"application/json": {"schema": {"type": "string"}}},
                    },
                    "401": {"description": "Unauthorized"},
                },
            }
        },
        "/account/add": {
            "post": {
                "tags": ["账户管理"],
                "summary": "新增",
                "operationId": "addResourceUsingPOST",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "additionalProperties": {"type": "object"},
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "OK",
                        "content": {"application/json": {"schema": {"type": "string"}}},
                    },
                    "201": {"description": "Created"},
                },
            }
        },
    },
}


def test_jsherp_snippet_parses():
    parsed = parse_openapi(JSHERP_SNIPPET)
    assert sorted(parsed.operation_ids) == ["addResourceUsingPOST", "checkIsNameExistUsingGET"]
    assert parsed.warnings == []


def test_jsherp_body_mapping():
    op = next(
        o
        for o in parse_openapi(JSHERP_SNIPPET).operations
        if o.operation_id == "addResourceUsingPOST"
    )
    assert op.method == "POST"
    assert op.request_body is not None
    assert op.request_body["required"] is True
    assert op.request_body["schema"]["type"] == "object"


def test_jsherp_query_params_mapping():
    op = next(
        o
        for o in parse_openapi(JSHERP_SNIPPET).operations
        if o.operation_id == "checkIsNameExistUsingGET"
    )
    assert [(p["name"], p["in"], p["required"]) for p in op.parameters] == [
        ("id", "query", True),
        ("name", "query", False),
    ]
    assert op.parameters[0]["schema"]["type"] == "integer"  # 2.0 type 平移进 schema


def test_jsherp_hash_deterministic():
    h1 = parse_openapi(JSHERP_SNIPPET).operation_hashes
    h2 = parse_openapi(JSHERP_SNIPPET).operation_hashes
    assert h1 == h2
    # 断言片段与入库产物结构一致（防转换脚本回归后片段失真）
    with open("docs/examples/jsherp-openapi3.json", encoding="utf-8") as f:
        product = json.load(f)
    real_op = product["paths"]["/account/add"]["post"]
    assert real_op["operationId"] == "addResourceUsingPOST"
    assert "requestBody" in real_op and "content" in real_op["requestBody"]
