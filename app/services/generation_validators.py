# 本项目编码格式：UTF-8（无 BOM）。
# 语义校验：用 operation 契约反向校验 LLM 输出。
# why：Pydantic 只校验形状（字段类型/白名单/长度），不校验「这条用例是否属于这个接口」——
# LLM 返回 DELETE /users/1 却能落在 GET /users/{id} 的 operation 下，确认后执行就是真发 DELETE。
# 分级原则：契约能判的判 error（拒掉，不进执行池），判不了的降 warn（降分但保留）——
# 真实文档大面积不可判（裸 object 响应、自由 map 请求体），一刀切全拒会把生成率打到很低。
# 纯函数、无 Session、无 I/O：可单测，也可被 confirm 路径复用。
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.utils.openapi_parser import ParsedOperation

_PATH_PARAM_RE = re.compile(r"\{[^}]+\}")

ViolationLevel = Literal["error", "warn"]


@dataclass(frozen=True)
class Violation:
    """单条违规。why：level 决定处置（拒/降分），rule 供前端分组与测试断言，
    detail 给人看——三者缺一都会让「为什么这条被拒/被扣分」变成黑盒。"""

    rule: str
    level: ViolationLevel
    detail: str


class CaseContract(Protocol):
    """校验所需的契约视图。why：生成链路有 ParsedOperation，confirm 链路只有
    api_definitions 里扁平化的 contract——用 Protocol 让两条链路共用同一个校验器，
    而不必把其中一方改造成对方的形状。"""

    @property
    def op_method(self) -> str: ...

    @property
    def op_path(self) -> str: ...

    @property
    def param_names(self) -> set[str]: ...

    @property
    def body_properties(self) -> set[str]:
        """请求体顶层可枚举字段。自由 map/空 schema 时为空集合。"""
        ...

    @property
    def body_required(self) -> set[str]: ...

    @property
    def response_codes(self) -> set[str]: ...

    def response_schema(self, status_code: int) -> dict | None:
        """该状态码的响应 schema。未声明或无 schema 时返回 None。"""
        ...


@dataclass(frozen=True)
class OperationContract:
    """从 ParsedOperation 派生的契约视图（生成链路用）。"""

    op_method: str
    op_path: str
    param_names: set[str]
    body_properties: set[str]
    body_required: set[str]
    response_codes: set[str]
    _responses: dict

    def response_schema(self, status_code: int) -> dict | None:
        entry = self._responses.get(str(status_code)) or {}
        schema = entry.get("schema")
        return schema if isinstance(schema, dict) else None


def contract_from_operation(op: ParsedOperation) -> OperationContract:
    """把解析产物折成校验视图。why：body 的「可枚举字段」必须区分「有 properties」与
    「自由 map」——后者无法判断字段是否编造，不做判定比误判更安全。"""
    schema = (op.request_body or {}).get("schema") or {}
    properties = schema.get("properties")
    return OperationContract(
        op_method=op.method,
        op_path=op.path,
        param_names={p["name"] for p in op.parameters},
        body_properties=set(properties) if isinstance(properties, dict) else set(),
        body_required=set(schema.get("required") or []),
        response_codes=set(op.responses),
        _responses=op.responses,
    )


@dataclass(frozen=True)
class SnapshotContract:
    """从 api_definitions.operation_contracts 还原的契约视图（confirm 链路用）。

    why 需要它：confirm 拿不到 document，只有落库时的快照。快照是扁平化的
    （method/path/response_status_codes/response_schemas），与被解析器产物的形状不同，
    这里做一次还原，让两条链路共用同一个 validate_case。
    """

    op_method: str
    op_path: str
    param_names: set[str]
    body_properties: set[str]
    body_required: set[str]
    response_codes: set[str]
    _response_schemas: dict

    def response_schema(self, status_code: int) -> dict | None:
        schema = self._response_schemas.get(str(status_code))
        return schema if isinstance(schema, dict) else None


def contract_from_snapshot(contract: dict) -> SnapshotContract | None:
    """把快照 dict 还原成校验视图。返回 None 表示快照缺少 method/path（旧版本落库的数据）。

    why 返回 None 而不是抛异常：旧快照没有 method/path 是历史事实，此时 confirm 应降级为
    「不阻断」而不是让整个接口 500——用旧文档入库的用例本来就不该被新规则判死。
    """
    if not isinstance(contract, dict):
        return None
    method = contract.get("method")
    path = contract.get("path")
    if not method or not path:
        return None

    # signature 里请求侧字段的形态：param:{in}:{name} 与 body.a.b
    param_names: set[str] = set()
    body_properties: set[str] = set()
    for key in contract.get("signature") or {}:
        if key.startswith("param:"):
            parts = key.split(":", 2)
            if len(parts) == 3:
                param_names.add(parts[2])
        elif key == "body":
            continue  # 自由 map：只有 'body' 一项，无字段名可枚举
        elif key.startswith("body."):
            # 只取顶层字段：body.a.b 的顶层是 a
            body_properties.add(key[len("body.") :].split(".")[0])

    return SnapshotContract(
        op_method=str(method).upper(),
        op_path=str(path),
        param_names=param_names,
        body_properties=body_properties,
        body_required=set(contract.get("required") or []),
        response_codes={str(c) for c in (contract.get("response_status_codes") or [])},
        _response_schemas=contract.get("response_schemas") or {},
    )


def path_matches(case_path: str, op_path: str) -> bool:
    """用例路径是否落在契约路径上。why：文档写 `/users/{id}`、用例写 `/users/1` 是合法的
    参数实例化；但多一段（`/users/1/extra`）或不匹配（`/users` vs `/users/{id}`）不是。
    尾斜杠归一化：两种写法在多数框架里等价，不该判违规。"""
    parts: list[str] = []
    for token in re.split(r"(\{[^}]+\})", op_path):
        if _PATH_PARAM_RE.fullmatch(token):
            parts.append(r"[^/]+")
        else:
            parts.append(re.escape(token))
    pattern = "^" + "".join(parts).rstrip("/") + "/?$"
    return re.fullmatch(pattern, case_path) is not None


def schema_leaves(schema: Any, prefix: str = "") -> tuple[set[str], set[str]]:
    """把响应 schema 拆成 (可判叶子路径, 不可判前缀)。

    why 不复用 `_collect_signature`：它把裸 object 记成叶子（`resp.data`），于是
    `data.records` 这种合法深路径会被判成编造。真实 ERP 文档 106 个 op 的 `200.data`
    是裸 object，一旦按 `_collect_signature` 的产物判定就是大规模误杀。
    这里显式区分「已知叶子」与「只知道是个对象、内部不可判」两种情况。
    """
    leaves: set[str] = set()
    opaque: set[str] = set()
    _walk(schema, prefix, leaves, opaque)
    return leaves, opaque


def _walk(schema: Any, prefix: str, leaves: set[str], opaque: set[str]) -> None:
    if not isinstance(schema, dict):
        if prefix:
            opaque.add(prefix)
        return
    properties = schema.get("properties")
    if isinstance(properties, dict) and properties:
        for key, sub in properties.items():
            _walk(sub, f"{prefix}.{key}" if prefix else key, leaves, opaque)
        return
    if "items" in schema:
        # why 不给空前缀拼 []：根就是数组时产出的 `[]` 不是合法断言路径
        # （AssertionItem 要求字母开头），会误导调用方拼出非法断言
        if prefix:
            _walk(schema["items"], f"{prefix}[]", leaves, opaque)
        return
    # 无 properties 的 object / 自由 map：内部字段不可判，记前缀后放行
    if schema.get("type") == "object" or "additionalProperties" in schema:
        if prefix:
            opaque.add(prefix)
        return
    if prefix:
        leaves.add(prefix)


def _assertion_target(path: str) -> str:
    """断言点路径 → 与 schema 叶子可比的形式。

    why：断言用 `items.0.name`（数字段是数组下标），schema 叶子用 `items[].name`。
    不归一化会让所有数组元素断言都判成「字段不存在」。
    """
    return re.sub(r"\.\d+(?=\.|$)", "[]", path)


def _under_opaque(path: str, opaque: set[str]) -> bool:
    for prefix in opaque:
        if path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "[]"):
            return True
    return False


def validate_case(case, contract: CaseContract) -> list[Violation]:
    """按 operation 契约校验单条 LLM 用例。

    error：契约能明确判定「这条用例不属于这个接口」——放进去会在真实系统上打错接口。
    warn：契约不足以判定（文档自身缺失信息），只降分并记录原因，不阻断。
    """
    violations: list[Violation] = []

    # 规则 1：method 必须一致（error）
    if str(case.method).upper() != contract.op_method:
        violations.append(
            Violation(
                rule="method_mismatch",
                level="error",
                detail=f"用例 method={case.method} 与接口 {contract.op_method} 不一致",
            )
        )

    # 规则 2：path 必须落在契约路径上（error）
    if not path_matches(case.path, contract.op_path):
        violations.append(
            Violation(
                rule="path_mismatch",
                level="error",
                detail=f"用例 path={case.path} 不匹配接口 {contract.op_path}",
            )
        )

    # 规则 3：params key 必须已声明（error）
    params = case.params or {}
    if isinstance(params, dict) and params:
        unknown = set(params) - contract.param_names
        if unknown:
            violations.append(
                Violation(
                    rule="params_unknown",
                    level="error",
                    detail=f"params 含未声明参数 {sorted(unknown)}，接口声明 {sorted(contract.param_names)}",
                )
            )

    # 规则 4：body 顶层字段必须在 schema 里（error，仅当 properties 可枚举时）
    body = case.body
    if isinstance(body, dict) and body:
        if contract.body_properties:
            unknown = set(body) - contract.body_properties
            if unknown:
                violations.append(
                    Violation(
                        rule="body_unknown_key",
                        level="error",
                        detail=f"body 含 schema 未定义字段 {sorted(unknown)}",
                    )
                )
            missing = contract.body_required - set(body)
            if missing:
                violations.append(
                    Violation(
                        rule="body_missing_required",
                        level="error",
                        detail=f"body 缺必填字段 {sorted(missing)}",
                    )
                )
        else:
            # 自由 map / 空 schema：字段名无从枚举，判 error 就是误杀
            violations.append(
                Violation(
                    rule="body_unjudgeable",
                    level="warn",
                    detail="接口请求体未声明具体字段（自由 map 或空 schema），无法校验字段是否编造",
                )
            )

    # 规则 5：expected_status 应在文档声明的响应码里（warn）
    declared = contract.response_codes
    status_str = str(case.expected_status)
    if declared and status_str not in declared:
        # why：文档完全没声明任何 4xx 时，4xx 用例是 prompt 明确要求产出的探索用例，
        # 把「文档写得糙」算在用例头上会反向激励——此时不扣分
        has_4xx = any(code.startswith("4") for code in declared)
        expects_4xx = status_str.startswith("4")
        if has_4xx or not expects_4xx:
            violations.append(
                Violation(
                    rule="expected_status_undeclared",
                    level="warn",
                    detail=f"expected_status={case.expected_status} 不在文档声明码 {sorted(declared)} 中",
                )
            )

    # 规则 6：断言字段应在响应 schema 里（warn，opaque 节点下放行）
    schema = contract.response_schema(case.expected_status)
    if isinstance(schema, dict):
        leaves, opaque = schema_leaves(schema)
        for assertion in case.assertions or []:
            raw_path = getattr(assertion, "path", None) or assertion.get("path")
            if not raw_path or raw_path == "status_code":
                continue
            target = _assertion_target(str(raw_path))
            if target in leaves or _under_opaque(target, opaque):
                continue
            # why：一条断言一条 Violation——扣分要按条数累计，把多字段塞进一条
            # 会让计分方不得不解析字符串才能数出条数
            violations.append(
                Violation(
                    rule="assertion_path_unknown",
                    level="warn",
                    detail=f"断言字段 {raw_path} 不在响应 schema 中，已知叶子 {sorted(leaves)[:6]}",
                )
            )

    return violations


def has_error(violations: list[Violation]) -> bool:
    return any(v.level == "error" for v in violations)
