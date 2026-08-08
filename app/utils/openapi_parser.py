# OpenAPI 3.x 解析器。why：纯规则提取（无 Session、无状态），供影响分析 O(1) diff 与 Phase 3 生成复用。
# 宽容解析：只提取不校验——畸形 schema 原样保留不抛异常，降级/跳过问题逐条记入 warnings（不静默，D4/D5）。
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from app.core.exceptions import AppError

# 影响用例请求/断言的「结构键」白名单；description/example/default/x-* 等文档键不参与 hash（防误报）
_STRUCT_KEYS = {
    "type", "properties", "items", "required", "enum", "format",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "pattern", "minItems", "maxItems",
    "uniqueItems", "nullable",
}
# 宽容解析的占位键与跨文件引用原文，参与 hash 以暴露差异
_EXTRA_KEYS = {"$ref", "x-circular", "x-broken-ref"}

_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
_PATH_PARAM_RE = re.compile(r"\{[^}]+\}")


@dataclass(frozen=True)
class ParsedOperation:
    operation_id: str
    method: str
    path: str
    parameters: list[dict]
    request_body: dict | None
    responses: dict
    contract: dict


@dataclass
class ParsedApi:
    operations: list[ParsedOperation]
    operation_ids: list[str]
    operation_hashes: dict
    operation_contracts: dict
    warnings: list[str] = field(default_factory=list)


def parse_openapi(document: dict) -> ParsedApi:
    """解析 OpenAPI 3.x 文档，返回 operations + 分段 hashes + contracts + warnings。非法文档抛 AppError。"""
    if not isinstance(document, dict) or not isinstance(document.get("paths"), dict):
        raise AppError("INVALID_SWAGGER", status_code=422, detail="缺少 paths（OpenAPI 3.x 结构）")
    openapi = str(document.get("openapi", "")).split(".")[0]
    if openapi != "3":
        raise AppError("INVALID_SWAGGER", status_code=422, detail=f"仅支持 OpenAPI 3.x，当前 {document.get('openapi')}")

    components = document.get("components") or {}
    schemas = components.get("schemas") or {}
    param_components = components.get("parameters") or {}
    warnings: list[str] = []
    operations: list[ParsedOperation] = []

    for path, item in document["paths"].items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() not in _METHODS or not isinstance(op, dict):
                continue
            operations.append(_extract_operation(path, method.upper(), op, schemas, param_components, warnings))

    if not operations:
        raise AppError("INVALID_SWAGGER", status_code=422, detail="paths 下无有效 operation")

    warnings.extend(_detect_duplicate_ids(operations))
    # 排序去重保证 operation_ids 确定性（diff 的集合输入）
    operation_ids = sorted({op.operation_id for op in operations})
    return ParsedApi(
        operations=operations,
        operation_ids=operation_ids,
        operation_hashes={op.operation_id: _hash_op(op) for op in operations},
        operation_contracts={op.operation_id: op.contract for op in operations},
        warnings=warnings,
    )


def _extract_operation(path: str, method: str, op: dict, schemas: dict, param_components: dict, warnings: list[str]) -> ParsedOperation:
    """单个 operation 提取：operationId（缺失自动生成+归一化）+ 参数/请求体/响应（$ref 展开）+ contract。"""
    operation_id = op.get("operationId") or _autogen_operation_id(method, path)
    parameters = [_resolve_parameter(p, schemas, param_components, warnings) for p in (op.get("parameters") or [])]
    request_body = None
    rb = op.get("requestBody")
    if isinstance(rb, dict):
        content = (rb.get("content") or {}).get("application/json") or {}
        rb_schema = content.get("schema")
        request_body = {
            "required": bool(rb.get("required")),
            "schema": _resolve_schema(rb_schema, schemas, set(), warnings) if isinstance(rb_schema, dict) else {},
        }
    responses: dict = {}
    for code, r in (op.get("responses") or {}).items():
        resp_schema = None
        if isinstance(r, dict):
            rc = (r.get("content") or {}).get("application/json") or {}
            if isinstance(rc.get("schema"), dict):
                resp_schema = _resolve_schema(rc["schema"], schemas, set(), warnings)
        responses[str(code)] = {"schema": resp_schema}
    return ParsedOperation(
        operation_id=operation_id,
        method=method,
        path=path,
        parameters=parameters,
        request_body=request_body,
        responses=responses,
        contract=_extract_contract(parameters, request_body, responses),
    )


def _resolve_parameter(p, schemas: dict, param_components: dict, warnings: list[str]) -> dict:
    """参数解析：支持 components/parameters 引用（一级循环，防死循环靠 visited 集合）。"""
    visited: set[str] = set()
    while isinstance(p, dict) and "$ref" in p and str(p["$ref"]).startswith("#/components/parameters/"):
        name = p["$ref"].rsplit("/", 1)[-1]
        if name in visited:
            break
        target = (param_components or {}).get(name)
        if not isinstance(target, dict):
            warnings.append(f"参数 $ref 目标不存在: {p['$ref']}")
            break
        visited.add(name)
        p = target
    return {
        "name": p.get("name", "") if isinstance(p, dict) else "",
        "in": p.get("in", "") if isinstance(p, dict) else "",
        "required": bool(p.get("required")) if isinstance(p, dict) else False,
        "schema": _resolve_schema(p.get("schema", {}), schemas, set(), warnings) if isinstance(p, dict) else {},
    }


def _resolve_schema(schema, schemas: dict, visited: set[str], warnings: list[str]) -> dict:
    """$ref 内联 + allOf/oneOf/items/properties 递归展开，保留原键（白名单过滤留给 _normalize）。"""
    if not isinstance(schema, dict):
        return schema if schema is not None else {}
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/components/schemas/"):
            # 跨文件/外部引用：保留原文参与 hash + warning，用户可见 hash 可能不完整（D5）
            warnings.append(f"跨文件/外部 $ref 未解析，hash 可能不完整: {ref}")
            return {"$ref": ref}
        name = ref.rsplit("/", 1)[-1]
        if name in visited:
            return {"type": "object", "x-circular": True}  # 循环引用占位，防死循环
        target = (schemas or {}).get(name)
        if target is None:
            warnings.append(f"$ref 目标不存在，hash 不可靠: {ref}")  # D4：不静默
            return {"type": "object", "x-broken-ref": True}
        return _resolve_schema(target, schemas, visited | {name}, warnings)

    out: dict = {}
    for k, v in schema.items():
        if k in ("items", "additionalProperties"):
            out[k] = _resolve_schema(v, schemas, visited, warnings)
        elif k == "properties":
            out[k] = {kk: _resolve_schema(vv, schemas, visited, warnings) for kk, vv in (v or {}).items()}
        elif k == "allOf" and isinstance(v, list):
            # 合并各分支 properties + required（对齐 ai-generation §2）
            merged = {"type": "object", "properties": {}, "required": []}
            for part in v:
                resolved = _resolve_schema(part, schemas, visited, warnings)
                merged["properties"].update(resolved.get("properties") or {})
                merged["required"].extend(resolved.get("required") or [])
            merged["required"] = sorted(set(merged["required"]))
            out.update(merged)
        elif k in ("oneOf", "anyOf") and isinstance(v, list):
            if v:
                warnings.append("oneOf/anyOf 取首个分支，hash 可能不完整")
                out.update(_resolve_schema(v[0], schemas, visited, warnings))
        else:
            out[k] = v
    return out


def _normalize(value):
    """递归白名单键过滤（hash 输入）。why：description/example 等文档键不参与——改注释不误报。
    properties 的值是「属性名 → schema」映射，属性名本身是结构键必须保留；
    否则嵌套对象字段级变更（type 改/enum 删/字段增删）对 hash 不可见，diff 永不命中（原缺陷）。"""
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            if k == "properties" and isinstance(v, dict):
                # 保留属性名键，递归过滤每个属性 schema 内部的非结构键
                out[k] = {kk: _normalize(vv) for kk, vv in v.items()}
            elif k in _STRUCT_KEYS or k in _EXTRA_KEYS:
                out[k] = _normalize(v)
        return out
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def _build_request_core(op: ParsedOperation) -> dict:
    """请求侧 hash 输入：参数元信息（name/in/required）显式保留，schema 过白名单。"""
    params = [
        {"name": p["name"], "in": p["in"], "required": p["required"], "schema": _normalize(p.get("schema", {}))}
        for p in op.parameters
    ]
    body = (
        {"required": op.request_body["required"], "schema": _normalize(op.request_body.get("schema", {}))}
        if op.request_body
        else None
    )
    return {"parameters": params, "request_body": body}


def _build_response_core(op: ParsedOperation) -> dict:
    """响应侧 hash 输入：状态码指纹 + 各码 schema。why：200→202 语义变化（schema 相同）也必须命中（F1）。"""
    return {
        "status_codes": sorted(op.responses.keys()),
        "schemas": {code: _normalize(r["schema"]) for code, r in op.responses.items()},
    }


def _hash_op(op: ParsedOperation) -> dict:
    """分段 hash：{request, response}。O(1) diff 的关键——只存哈希不存原文。"""
    req = json.dumps(_build_request_core(op), sort_keys=True, ensure_ascii=False)
    resp = json.dumps(_build_response_core(op), sort_keys=True, ensure_ascii=False)
    return {
        "request": hashlib.md5(req.encode()).hexdigest(),
        "response": hashlib.md5(resp.encode()).hexdigest(),
    }


def _autogen_operation_id(method: str, path: str) -> str:
    """无 operationId 时自动生成。why：路径参数归一化 /users/{id}→/users/:param，
    {id} 与 {userId} 不误判为不同接口（D1 场景）。"""
    norm = _PATH_PARAM_RE.sub(":param", path)
    return f"{method}_{norm}"


def _extract_contract(parameters: list[dict], request_body: dict | None, responses: dict | None = None) -> dict:
    """请求侧契约：required 集合 + 字段级 type/enum 签名——breaking 联合判定的输入（F2）。
    额外记录响应状态码集合：200→202 等状态码变化会让下游按旧码断言静默失败，须纳入 breaking（F1）。"""
    required: list[str] = []
    signature: dict = {}
    for p in parameters:
        if p.get("required"):
            required.append(p["name"])
        _collect_signature(f"param:{p['in']}:{p['name']}", p.get("schema", {}), signature)
    if request_body:
        # why：请求体顶层必填字段也是「必需项」——breaking 的 required 收紧判定必须覆盖（F2）
        required.extend((request_body.get("schema") or {}).get("required") or [])
        _collect_signature("body", request_body.get("schema", {}), signature)
    contract: dict = {"required": sorted(set(required)), "signature": signature}
    if responses is not None:
        contract["response_status_codes"] = sorted(str(c) for c in responses)
    return contract


def _collect_signature(path: str, schema, out: dict) -> None:
    """递归收集字段 type/enum 签名（object properties / array items 展开）。"""
    schema = schema or {}
    if schema.get("type") == "object" and isinstance(schema.get("properties"), dict):
        for k, v in schema["properties"].items():
            _collect_signature(f"{path}.{k}", v, out)
    elif "items" in schema:
        _collect_signature(f"{path}[]", schema["items"], out)
    else:
        out[path] = {"type": schema.get("type"), "enum": list(schema.get("enum") or [])}


def _detect_duplicate_ids(operations: list[ParsedOperation]) -> list[str]:
    """检测 operation_id 重复。why：copy-paste 共用 id 会误圈定血缘，提前告警（F4）。"""
    seen: dict[str, list[str]] = {}
    for op in operations:
        seen.setdefault(op.operation_id, []).append(f"{op.method} {op.path}")
    return [
        f"operation_id 重复: {oid}（{'、'.join(paths)}），血缘可能误圈定"
        for oid, paths in seen.items()
        if len(paths) > 1
    ]
