# Swagger 2.0 → OpenAPI 3.0 一次性转换（被测系统文档接入前置）。
# why：平台解析器 openapi_parser 只认 openapi 主版本 3；管伊佳ERP（jshERP-boot）文档是 Swagger 2.0。
# 优先从被测系统线上 /v2/api-docs 拉取（源头最新），本地文件兜底（自动剥 markdown 围栏）。
# 产物入库 docs/examples/ 供 POST /parse 与 AI 生成演示复用；映射保持最小集（对齐 parser 读取的字段）。
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
_PARAM_SCHEMA_KEYS = ("type", "format", "items", "enum", "minimum", "maximum", "default", "pattern")


def _load_swagger(url: str | None, input_path: str | None) -> dict:
    if url:
        resp = httpx.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()
    text = Path(input_path).read_text(encoding="utf-8").strip()
    lines = text.splitlines()
    # why：API_doc.md 外层是 ```{ ... ``` 围栏，直接 json.loads 会失败
    if lines and lines[0].startswith("```"):
        lines[0] = lines[0][3:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return json.loads("\n".join(lines).strip())


def _inline_params(params: list, common_params: dict, notes: list[str]) -> list[dict]:
    out: list[dict] = []
    for p in params:
        if isinstance(p, dict) and "$ref" in p:
            name = str(p["$ref"]).rsplit("/", 1)[-1]
            target = common_params.get(name)
            if isinstance(target, dict):
                out.append(target)
            else:
                notes.append(f"参数 $ref 未解析（已跳过）: {p['$ref']}")
        elif isinstance(p, dict):
            out.append(p)
    return out


def _param_schema(p: dict) -> dict:
    schema = {k: p[k] for k in _PARAM_SCHEMA_KEYS if k in p}
    return schema or {"type": "string"}


def _convert_operation(
    op: dict,
    path_level_params: list[dict],
    common_params: dict,
    global_consumes: list[str],
    notes: list[str],
) -> dict:
    params_raw = (
        _inline_params(op.get("parameters") or [], common_params, notes) + path_level_params
    )
    params3: list[dict] = []
    body_schema = None
    body_required = False
    form_props: dict = {}
    # why：2.0 的 consumes/produces → 3.0 content media type；parser 只读 application/json
    consumes = op.get("consumes") or global_consumes or ["application/json"]
    body_ct = "application/json" if "application/json" in consumes else consumes[0]
    for p in params_raw:
        pin = p.get("in")
        if pin == "body":
            body_schema = p.get("schema") or {"type": "object"}
            body_required = bool(p.get("required"))
        elif pin == "formData":
            form_props[p.get("name", "")] = _param_schema(p)
        else:
            params3.append(
                {
                    "name": p.get("name", ""),
                    "in": pin,
                    "required": bool(p.get("required")),
                    "description": p.get("description", ""),
                    "schema": _param_schema(p),
                }
            )

    new_op = {k: op[k] for k in ("tags", "summary", "operationId", "security") if op.get(k)}
    if params3:
        new_op["parameters"] = params3
    if body_schema is not None:
        new_op["requestBody"] = {
            "required": body_required,
            "content": {body_ct: {"schema": body_schema}},
        }
    elif form_props:
        new_op["requestBody"] = {
            "required": True,
            "content": {
                "application/x-www-form-urlencoded": {
                    "schema": {"type": "object", "properties": form_props}
                }
            },
        }

    produces = op.get("produces") or []
    # why：produces */* 时取 application/json（parser/用例均按 JSON 口径）
    resp_ct = "application/json" if (not produces or "*/*" in produces) else produces[0]
    responses: dict = {}
    for code, r in (op.get("responses") or {}).items():
        entry = {"description": (r or {}).get("description", "")}
        if isinstance(r, dict) and isinstance(r.get("schema"), dict):
            entry["content"] = {resp_ct: {"schema": r["schema"]}}
        responses[str(code)] = entry
    new_op["responses"] = responses
    return new_op


def _rewrite_refs(node) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str) and v.startswith("#/definitions/"):
                node[k] = v.replace("#/definitions/", "#/components/schemas/")
            else:
                _rewrite_refs(v)
    elif isinstance(node, list):
        for v in node:
            _rewrite_refs(v)


def convert(doc: dict, *, host_override: str | None) -> tuple[dict, list[str]]:
    notes: list[str] = []
    host = host_override or doc.get("host") or "localhost"
    base_path = doc.get("basePath", "")
    out: dict = {
        "openapi": "3.0.3",
        "info": doc.get("info", {}),
        "tags": doc.get("tags", []),
        "servers": [{"url": f"http://{host}{base_path}"}],
    }

    components: dict = {}
    if doc.get("definitions"):
        components["schemas"] = doc["definitions"]
    schemes: dict = {}
    for name, s in (doc.get("securityDefinitions") or {}).items():
        if s.get("type") == "apiKey":
            schemes[name] = {
                "type": "apiKey",
                "in": s.get("in", "header"),
                "name": s.get("name", name),
            }
        else:
            notes.append(f"securityDefinitions.{name} type={s.get('type')} 未映射，已跳过")
    if schemes:
        components["securitySchemes"] = schemes
    if components:
        out["components"] = components
    if doc.get("security"):
        out["security"] = doc["security"]

    common_params = doc.get("parameters") or {}
    global_consumes = doc.get("consumes") or []
    paths_out: dict = {}
    for path, item in (doc.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        path_level_params = _inline_params(item.get("parameters") or [], common_params, notes)
        path_out = {}
        for method, op in item.items():
            if method.lower() not in METHODS or not isinstance(op, dict):
                continue
            path_out[method] = _convert_operation(
                op, path_level_params, common_params, global_consumes, notes
            )
        if path_out:
            paths_out[path] = path_out
    out["paths"] = paths_out
    _rewrite_refs(out)
    return out, notes


def main() -> int:
    ap = argparse.ArgumentParser(description="Swagger 2.0 → OpenAPI 3.0 转换")
    ap.add_argument("--url", default=None, help="被测系统 /v2/api-docs 地址")
    ap.add_argument("--input", default=None, help="本地 Swagger2 文件（markdown 围栏自动剥离）")
    ap.add_argument("--host", default=None, help="覆盖 host（如 127.0.0.1:9999，servers.url 用）")
    ap.add_argument("--out", required=True, help="输出 OpenAPI 3 JSON 路径")
    args = ap.parse_args()
    if not args.url and not args.input:
        print("必须提供 --url 或 --input", file=sys.stderr)
        return 2
    doc = _load_swagger(args.url, args.input)
    converted, notes = convert(doc, host_override=args.host)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(converted, ensure_ascii=False, indent=1), encoding="utf-8")
    n_ops = sum(1 for v in converted["paths"].values() for m in v if m in METHODS)
    print(f"转换完成: paths={len(converted['paths'])} operations={n_ops} -> {out_path}")
    for note in notes[:10]:
        print("note:", note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
