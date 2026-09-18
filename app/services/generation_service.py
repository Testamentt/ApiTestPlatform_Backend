# AI 生成编排。why：create（Web 短操作）用传入 session + run_id 幂等 + 定向决策；
# run_generation（Worker 长流程）用 session_factory 短事务分界——LLM 调用不占连接（RULES §2.1）。
# 防幻觉三层护栏：Pydantic 校验 + draft 恒为 + operation_id 服务端注入（§11.2）。
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from time import monotonic

from celery.exceptions import (
    SoftTimeLimitExceeded,  # 服务层须识别软超时以放行（R3-1），异常类型耦合可接受
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models.enums import CaseSource, CaseStatus, GenerationStatus
from app.models.generation_log import GenerationLog
from app.models.generation_task import GenerationTask
from app.models.impact_analysis import ImpactAnalysis
from app.models.test_case import TestCase
from app.repositories.generation_task_repository import GenerationTaskRepository
from app.schemas.generate import GeneratedCaseList
from app.services.dispatcher import dispatch_generation, dispatch_or_fail
from app.utils.llm_client import LlmClient
from app.utils.openapi_parser import ParsedOperation, parse_openapi
from app.utils.prompt_util import PROMPT_VERSION, load_system_prompt, render_user_prompt


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


logger = logging.getLogger(__name__)


def compute_run_id(document: dict, operation_ids: list[str] | None) -> str:
    """幂等键：document + 定向子集。why：同输入同指纹，Lookup-Create 防重复调 LLM 花钱。"""
    key = json.dumps({"document": document, "operation_ids": operation_ids or []}, sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()


# 进 Prompt 的结构约束白名单；description/example/title/default/x-* 等文档字段剥离（§10.1）
_PROMPT_STRUCT_KEYS = {
    "type",
    "properties",
    "items",
    "required",
    "enum",
    "format",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
    "nullable",
}

_SCRIPT_TAG_RE = re.compile(r"<[^>]*script[^>]*>", re.IGNORECASE)
# §10.2 完整清洗（review R3-4）：危险标签族（script/iframe/object/embed/base/link/style）+ 控制字符
_DANGEROUS_TAG_RE = re.compile(
    r"<\s*/?\s*(?:script|iframe|object|embed|base|link|style)\b[^>]*>", re.IGNORECASE
)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# §10.1-4 敏感扫描（review R3-3）：注入句式/密钥形态/内网地址——命中即拦截该 operation
_INJECTION_HINTS_RE = re.compile(
    r"ignore (?:all )?(?:previous|prior) instructions|忽略(?:以上|上述|之前)的?(?:全部)?指令"
    r"|sk-[A-Za-z0-9]{16,}|BEGIN (?:RSA )?PRIVATE KEY",
    re.IGNORECASE,
)
_PRIVATE_NET_RE = re.compile(
    r"(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|127\.\d{1,3}\.\d{1,3}\.\d{1,3})"
)
# §10.1-1 逐字段截断上限（review R3-3）：结构键的字符串值（pattern/enum 项等）进 prompt 前限长
_PROMPT_STR_LIMIT = 256

# why：输出侧敏感片段（P2-5/D2）——schema 只管形状、原 `_sanitize_llm_field` 只删标签与控制字符；
# 若 LLM 把内网地址/密钥形态/注入句式回显出来，会随用例落库并在前端展示。命中即替换为占位符（不静默：记数）。
_REDACTED_PLACEHOLDER = "[REDACTED]"


def _redact_sensitive(text: str) -> tuple[str, int]:
    """替换敏感片段，返回 (清洗后文本, 命中次数)。why：命中数要能被调用方累计并写日志，避免静默改写。"""
    hits = 0
    out = text
    for pattern in (_INJECTION_HINTS_RE, _PRIVATE_NET_RE):
        out, count = pattern.subn(_REDACTED_PLACEHOLDER, out)
        hits += count
    return out, hits


def _sanitize_llm_field(value, *, counter: list[int] | None = None):
    """LLM 输出文本清洗（§10.2 + P2-5）。why：LLM 输出不可信——先剔除危险标签与控制字符
    （防 XSS/存储注入），再脱敏敏感片段（密钥形态/内网地址/注入句式）；counter 传入时累计命中次数，
    供调用方写审计日志（不静默）。"""
    if isinstance(value, str):
        v = _DANGEROUS_TAG_RE.sub("", _SCRIPT_TAG_RE.sub("", value))
        v, hits = _redact_sensitive(_CONTROL_CHARS_RE.sub("", v))
        if counter is not None:
            counter[0] += hits
        return v
    if isinstance(value, list):
        return [_sanitize_llm_field(v, counter=counter) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize_llm_field(v, counter=counter) for k, v in value.items()}
    return value


def _strip_doc_fields(value):
    """递归剥离 schema 文档字段，只留结构约束进 Prompt。
    why：§10.1 预处理——用户 Swagger 的 description/example 可含指令/HTML/敏感文本，不得进入 LLM 输入；
    保留的结构键字符串值（pattern/enum/default）仍可能夹带注入句式/超长内容——控制字符剥离 + 逐字段截断
    （review R3-3，§10.1-1/2）。"""
    if isinstance(value, dict):
        return {k: _strip_doc_fields(v) for k, v in value.items() if k in _PROMPT_STRUCT_KEYS}
    if isinstance(value, list):
        return [_strip_doc_fields(v) for v in value]
    if isinstance(value, str):
        v = _CONTROL_CHARS_RE.sub("", value)
        return v[:_PROMPT_STR_LIMIT]
    return value


def _scan_prompt_risk(text: str) -> str | None:
    """敏感信息/注入句式扫描（§10.1-4，review R3-3）。

    why：白名单+截断仍可能放过「忽略上述指令」类句式或意外携带的密钥/内网地址——
    入 prompt 前扫描，命中即拦截该 operation 并告警（不调 LLM）。Returns 命中描述或 None。
    """
    if _INJECTION_HINTS_RE.search(text):
        return "注入句式/密钥形态命中"
    if _PRIVATE_NET_RE.search(text):
        return "内网地址命中"
    return None


def _operation_to_json(op: ParsedOperation) -> str:
    """operation 可读描述（Prompt 注入）。why：只取结构化字段（结构键白名单，剥离 description/example
    等文档字段），防注入（RULES §10.1）。"""
    params = [
        {
            "name": p["name"],
            "in": p["in"],
            "required": p["required"],
            "schema": _strip_doc_fields(p.get("schema", {})),
        }
        for p in op.parameters
    ]
    body = (
        {
            "required": op.request_body["required"],
            "schema": _strip_doc_fields(op.request_body.get("schema", {})),
        }
        if op.request_body
        else None
    )
    payload = {
        "operation_id": _CONTROL_CHARS_RE.sub("", op.operation_id),
        "method": op.method,
        "path": _CONTROL_CHARS_RE.sub("", op.path),
        "parameters": params,
        "request_body": body,
        "responses": {code: _strip_doc_fields(r.get("schema")) for code, r in op.responses.items()},
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _write_log(
    session_factory,
    *,
    task_id: int,
    op_id: str,
    model: str,
    status: str,
    confidence: float = 1.0,
    usage: dict | None = None,
    latency_ms: int | None = None,
    cost: float | None = None,
    raw: str | None = None,
    error: str | None = None,
) -> None:
    """逐 operation 审计日志（RULES §9.6）：成功/校验失败/错误均落库，不静默丢失败。"""
    with session_factory() as s:
        s.add(
            GenerationLog(
                generation_task_id=task_id,
                operation_id=op_id,
                model=model,
                prompt_version=PROMPT_VERSION,
                status=status,
                ai_confidence=confidence,
                usage=usage,
                latency_ms=latency_ms,
                cost_estimate=cost,
                raw_response=raw,
                error_msg=error,
            )
        )
        s.commit()


def _fail(
    session_factory, task_id: int, stage: str, msg: str, *, summary: dict | None = None
) -> None:
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        if task is None:
            return
        try:
            task.status = GenerationStatus.FAILED.value
            task.error_stage = stage
            task.error_msg = msg
            # why：超时/失败也要留下进度快照——用户需要知道"跑到哪一步了"，而不是只知道失败了
            if summary is not None:
                task.result_summary = summary
            task.finished_at = _utcnow()
            s.commit()
        except Exception:
            s.rollback()
            raise


def force_fail_timeout(session_factory, task_id: int) -> None:
    """软超时兜底：置终态并落 failed(timeout)。
    why：Celery 软超时中断任务函数后 DB 状态可能停在 running，须迁移 failed（RULES §8.2），
    否则 run_id 幂等会永久复用该卡死任务、无法重新生成。"""
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        if task is None:
            return
        try:
            task.status = GenerationStatus.FAILED.value
            task.error_stage = "timeout"
            task.error_msg = f"Celery 软超时（{get_settings().llm.task_soft_timeout_seconds}s）"
            task.finished_at = _utcnow()
            s.commit()
        except Exception:
            s.rollback()
            raise


def run_generation(session_factory, task_id: int, *, llm: LlmClient | None = None) -> None:
    """生成编排入口。why：兜底任何未预期异常，保证任务进入明确终态（不卡 RUNNING，RULES §8.3）。"""
    try:
        _run_generation(session_factory, task_id, llm=llm)
    except SoftTimeLimitExceeded:
        # 放行给任务层专属 handler（force_fail_timeout 落 timeout，否则 run_id 永久污染）——R3-1
        raise
    except Exception:
        logger.exception("run_generation 任务 %s 未预期异常", task_id)
        _fail(session_factory, task_id, "internal", "生成引擎未预期异常，详见服务日志")


def _run_generation(session_factory, task_id: int, *, llm: LlmClient | None = None) -> None:
    """Worker 内生成编排（短事务分界）。宽容：单接口失败记 log 继续；致命错误（解析失败）才整任务 FAILED。"""
    llm = llm or LlmClient()
    cost_per_1k = llm.settings.cost_per_1k_tokens

    # 短事务①：PENDING→RUNNING
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        if task is None or task.status != GenerationStatus.PENDING.value:
            return
        task.status = GenerationStatus.RUNNING.value
        task.started_at = _utcnow()
        s.commit()

    # 解析（无 Session，CPU 密集不占连接）
    try:
        parsed = parse_openapi(task.document)
    except AppError as e:
        _fail(session_factory, task_id, "parse", str(e.detail))
        return

    # 定向过滤：未在 document 找到的记 skipped（不静默忽略，加分项）
    ops_map = {op.operation_id: op for op in parsed.operations}
    target_ids = parsed.operation_ids
    skipped_by_filter = 0
    skipped_detail: list[str] = []
    if task.operation_ids:
        wanted = set(task.operation_ids)
        target_ids = [oid for oid in parsed.operation_ids if oid in wanted]
        skipped = sorted(wanted - set(parsed.operation_ids))
        skipped_by_filter = len(skipped)
        skipped_detail = [f"operation_id '{x}' not found in document" for x in skipped]

    warnings_any = bool(parsed.warnings)
    system = load_system_prompt()
    json_schema = json.dumps(GeneratedCaseList.model_json_schema(), ensure_ascii=False)

    generated = draft_created = rejected = 0
    rejected_detail: list[dict] = []
    cost_total = 0.0
    redacted_total = 0  # P2-5：输出脱敏命中总数（进 result_summary，可审计）
    # why：任务级时间预算必须由任务自己守（review N13）——Celery 的 soft/hard time_limit 在
    # `--pool=solo`（Windows 本地与 docker-compose 都用它）下被静默关闭（官方文档：切换到非 prefork 池
    # 会静默关闭 soft_timeout 等特性；实测 soft=3s 对 10s 任务毫无效果），此前这条链路没有任何总时长上限，
    # 338 个 operation 串行循环可以跑几小时。检查点放在每个 operation 之前——超时最多多花"一个 operation
    # 的 LLM 预算"（llm.connect_timeout/read_timeout × 重试次数）。
    deadline = monotonic() + get_settings().llm.task_timeout_seconds

    def _snapshot(**extra) -> dict:
        return {
            "generated": generated,
            "draft_created": draft_created,
            "rejected": rejected,
            "rejected_detail": rejected_detail,
            "skipped_by_filter": skipped_by_filter,
            "skipped_detail": skipped_detail,
            "prompt_version": PROMPT_VERSION,
            "cost_total": round(cost_total, 6),
            "redacted": redacted_total,  # P2-5：LLM 输出被脱敏的片段数（0 = 未命中）
            **extra,
        }

    for op_id in target_ids:
        if monotonic() >= deadline:
            _fail(
                session_factory,
                task_id,
                "timeout",
                f"生成超时（>{get_settings().llm.task_timeout_seconds}s）："
                f"已处理 {generated + rejected}/{len(target_ids)} 个接口，同输入可重试",
                summary=_snapshot(timed_out=True, timed_out_at=op_id),
            )
            return
        op = ops_map[op_id]
        op_json = _operation_to_json(op)
        user = render_user_prompt(op_json, json_schema)
        # §10.1-4 敏感扫描（R3-3）+ P2-7：**只扫注入的数据面**（operation 结构化描述）——此前扫整段渲染后
        # prompt，模板/JSON Schema 文本里出现关键词会误拦全部 operation（扫描面过宽）。
        risk = _scan_prompt_risk(op_json)
        if risk:
            logger.warning("operation %s prompt 命中敏感扫描，已拦截: %s", op_id, risk)
            _write_log(
                session_factory,
                task_id=task_id,
                op_id=op_id,
                model=llm.settings.model,
                status="error",
                confidence=0.0,
                error=f"敏感信息扫描拦截: {risk}",
            )
            rejected += 1
            rejected_detail.append({"operation_id": op_id, "reason": f"敏感扫描拦截: {risk}"})
            continue
        start = monotonic()
        try:
            # P2-4：把任务级 deadline 透传给 llm_client——单次调用（含重试）被剩余预算钳位，
            # 不再出现"检查点之间又花掉一个完整 LLM 预算"的粗粒度超时
            parsed_cases, usage = llm.chat_json(
                system, user, schema=GeneratedCaseList, deadline=deadline
            )
        except AppError as e:
            latency_ms = int((monotonic() - start) * 1000)
            if e.code == "LLM_VALIDATION_FAILED":
                # 校验失败同样落库（不建坏用例）+ raw_response + 具体错误（间隙 2）
                _write_log(
                    session_factory,
                    task_id=task_id,
                    op_id=op_id,
                    model=llm.settings.model,
                    status="validation_failed",
                    confidence=0.0,
                    latency_ms=latency_ms,
                    raw=getattr(e, "raw_response", None),
                    error=str(e.detail),
                )
                reason = "校验失败: " + str(e.detail)[:100]
            else:
                _write_log(
                    session_factory,
                    task_id=task_id,
                    op_id=op_id,
                    model=llm.settings.model,
                    status="error",
                    confidence=0.0,
                    latency_ms=latency_ms,
                    error=str(e.detail),
                )
                reason = "LLM 失败: " + str(e.detail)[:100]
            rejected += 1
            rejected_detail.append({"operation_id": op_id, "reason": reason})
            continue  # 宽容：单接口失败不拖垮整批
        latency_ms = int((monotonic() - start) * 1000)

        # 落库 draft：operation_id 服务端注入 + trust_score（document 级 warnings 决定 80/60）
        trust_score = 60 if warnings_any else 80
        usage_dict = {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        }
        cost = round(usage.total_tokens * cost_per_1k / 1000, 6)
        # why：counter 是可变容器（Python 闭包不能重绑定外层变量）——累计本条用例被脱敏的片段数
        redactions = [0]
        with session_factory() as s:
            for c in parsed_cases.cases:
                s.add(
                    TestCase(
                        name=_sanitize_llm_field(c.name, counter=redactions),
                        method=c.method,
                        path=_sanitize_llm_field(c.path, counter=redactions),
                        operation_id=op_id,  # 【防幻觉】服务端注入，不信任 LLM 输出
                        params=_sanitize_llm_field(c.params or None, counter=redactions),
                        body=_sanitize_llm_field(c.body, counter=redactions),
                        expected_status=c.expected_status,
                        # why：AssertionItem → dict 落 JSON 列；value 仍过 LLM 清洗（防存储注入），
                        # path/op 已被 schema 白名单约束无需清洗
                        assertions=[
                            _sanitize_llm_field(a.model_dump(), counter=redactions)
                            for a in c.assertions
                        ],
                        status=CaseStatus.DRAFT,  # draft 恒为，人工 confirm 才 active
                        source=CaseSource.AI,
                        trust_score=trust_score,
                    )
                )
            s.add(
                GenerationLog(
                    generation_task_id=task_id,
                    operation_id=op_id,
                    model=llm.settings.model,
                    prompt_version=PROMPT_VERSION,
                    status="success",
                    ai_confidence=1.0,
                    usage=usage_dict,
                    latency_ms=latency_ms,
                    cost_estimate=cost,
                )
            )
            s.commit()
        if redactions[0]:
            # why：脱敏是"改写用户数据"，必须留痕（P2-5）——否则排查"为什么用例里出现 [REDACTED]"无迹可循
            logger.warning(
                "operation %s LLM 输出命中敏感片段，已脱敏 %d 处（P2-5）", op_id, redactions[0]
            )
            redacted_total += redactions[0]
        generated += 1
        draft_created += len(parsed_cases.cases)
        cost_total += cost

    # 收尾：result_summary + SUCCESS
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        if task is None:
            return
        task.operation_count = generated
        task.prompt_version = (
            PROMPT_VERSION  # 任务层版本溯源（review R3-2 批次：列定义了此前从未写入）
        )
        task.result_summary = _snapshot()
        task.status = GenerationStatus.SUCCESS.value
        task.finished_at = _utcnow()
        s.commit()


class GenerationService:
    def __init__(self, session) -> None:
        self.session = session
        self.repo = GenerationTaskRepository(session)

    def create_generation_task(
        self,
        document: dict,
        operation_ids: list[str] | None = None,
        force_full: bool = False,
    ) -> GenerationTask:
        """创建生成任务（Web）。定向优先级：operation_ids > force_full > 默认（untested/全量/422）。
        幂等：run_id Lookup-Create 存在即返回（不重复调 LLM）。"""
        settings = get_settings()
        if len(json.dumps(document)) > settings.swagger.max_upload_bytes:
            raise AppError(
                "SWAGGER_TOO_LARGE",
                status_code=422,
                detail=f"文档超过 {settings.swagger.max_upload_bytes} 字节上限",
            )

        if operation_ids is not None:
            # why：显式定向必须与文档对齐（review A1 输入层）——此前自由文本无任何校验，全部未命中会跑成
            # "SUCCESS + operation_count=0"的假成功；现改为 fail fast（同 NO_UNTESTED_OPS 的 422 口径），
            # 并把「文档中合法 id 长什么样」直接回给用户（消息里带示例，用户不必去猜 operation_id 规则）
            current_ids = set(parse_openapi(document).operation_ids)
            missing = [oid for oid in operation_ids if oid not in current_ids]
            if missing and len(missing) == len(operation_ids):
                samples = "、".join(sorted(current_ids)[:3]) or "（文档无 operationId）"
                raise AppError(
                    "INVALID_OPERATION_IDS",
                    status_code=422,
                    message=(
                        f"定向 operation_id 全部不在文档中（{len(missing)} 个），"
                        f"文档中合法示例：{samples}"
                    ),
                    detail=(
                        f"未命中: {missing[:20]}；文档共 {len(current_ids)} 个 operation_id，"
                        "格式为 operationId 或自动生成的 {METHOD}_{path}"
                    ),
                )
            if missing:
                # 部分命中照常执行，未命中项由 Worker 记入 result_summary.skipped_detail（前端展示）
                logger.warning("定向 operation_id 部分未命中（将记入 skipped_detail）: %s", missing)

        if operation_ids is None and not force_full:
            # 系统智能决策（间隙 1）：按 created_at 降序取最新一次分析；无历史 → 全量（开箱即用）。
            # why：校验最新分析的 untested_ops 与当前提交的 document 对应——过期快照可能静默全量跳过
            # （generated=0 仍 SUCCESS）；parse 顺带提前校验文档合法性（fail fast）。
            current_ids = set(parse_openapi(document).operation_ids)
            latest = self.session.scalar(
                select(ImpactAnalysis).order_by(ImpactAnalysis.created_at.desc()).limit(1)
            )
            if latest is None:
                pass  # 首次运行友好 → 全量（operation_ids 保持 None）
            elif latest.untested_ops:
                valid = sorted(oid for oid in latest.untested_ops if oid in current_ids)
                if valid:
                    operation_ids = valid
                # 全部不在当前文档 → 回退全量（防过期快照静默跳过）
            else:
                raise AppError(
                    "NO_UNTESTED_OPS", status_code=422, detail="所有接口已有 active 用例，无需生成"
                )

        run_id = compute_run_id(document, operation_ids)
        existing = self.repo.find_by_run_id(run_id)
        if existing:
            if existing.status == GenerationStatus.SUCCESS.value:
                return existing  # §8.3：已成功不重复生成
            if existing.status == GenerationStatus.FAILED.value:
                # why：失败任务允许同输入重试——重置 PENDING 重新入队（review H4）；
                # run_generation 有 PENDING 状态守卫（重复派发安全），否则 run_id 被 FAILED 永久占用
                existing.status = GenerationStatus.PENDING.value
                existing.error_stage = None
                existing.error_msg = None
                existing.celery_task_id = None
                existing.finished_at = None
                try:
                    self.session.commit()
                except Exception:
                    self.session.rollback()
                    raise
                dispatch_or_fail(
                    existing,
                    GenerationStatus,
                    dispatch_generation,
                    session=self.session,
                    label="生成",
                )
                return existing
            return existing  # PENDING/RUNNING：执行中或排队中，返回现状

        task = GenerationTask(
            run_id=run_id,
            document=document,
            operation_ids=operation_ids,
            status=GenerationStatus.PENDING,
        )
        try:
            self.repo.add(task)
        except IntegrityError as e:
            # why：同输入并发提交撞 UNIQUE(run_id)——查回赢家返回，避免 500（review C1，与 TaskService 同口径），
            # 这条路径尤其重要：不给用户 500 才不会让他再点一次从而多花一份 LLM 钱
            winner = self.repo.resolve_after_unique_violation(
                lambda: self.repo.find_by_run_id(run_id)
            )
            if winner is None:
                raise AppError(
                    "GENERATION_CONFLICT",
                    status_code=409,
                    detail="并发生成任务创建冲突且未查到既有任务，请重试",
                ) from e
            logger.info("生成任务并发创建命中既有任务 id=%s run_id=%s", winner.id, run_id)
            return winner
        dispatch_or_fail(
            task,
            GenerationStatus,
            dispatch_generation,
            session=self.session,
            label="生成",
        )
        return task

    def get_generation_task(self, task_id: int) -> GenerationTask:
        return self.repo.get_or_raise(task_id)
