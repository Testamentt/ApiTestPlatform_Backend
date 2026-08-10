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

from sqlalchemy import select

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models.enums import CaseSource, CaseStatus, GenerationStatus
from app.models.generation_log import GenerationLog
from app.models.generation_task import GenerationTask
from app.models.impact_analysis import ImpactAnalysis
from app.models.test_case import TestCase
from app.repositories.generation_task_repository import GenerationTaskRepository
from app.schemas.generate import GeneratedCaseList
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


def _sanitize_llm_field(value):
    """LLM 输出文本清洗（§10.2）。why：LLM 输出不可信，可回显 prompt 中的注入文本，
    入库前剔除 <script> 等危险标签与控制字符（防 XSS/存储注入）。"""
    if isinstance(value, str):
        return _SCRIPT_TAG_RE.sub("", value).replace("\x00", "")
    if isinstance(value, list):
        return [_sanitize_llm_field(v) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize_llm_field(v) for k, v in value.items()}
    return value


def _strip_doc_fields(value):
    """递归剥离 schema 文档字段，只留结构约束进 Prompt。
    why：§10.1 预处理——用户 Swagger 的 description/example 可含指令/HTML/敏感文本，不得进入 LLM 输入。"""
    if isinstance(value, dict):
        return {k: _strip_doc_fields(v) for k, v in value.items() if k in _PROMPT_STRUCT_KEYS}
    if isinstance(value, list):
        return [_strip_doc_fields(v) for v in value]
    return value


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
        "operation_id": op.operation_id,
        "method": op.method,
        "path": op.path,
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


def _fail(session_factory, task_id: int, stage: str, msg: str) -> None:
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        if task is None:
            return
        try:
            task.status = GenerationStatus.FAILED.value
            task.error_stage = stage
            task.error_msg = msg
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
    for op_id in target_ids:
        op = ops_map[op_id]
        user = render_user_prompt(_operation_to_json(op), json_schema)
        start = monotonic()
        try:
            parsed_cases, usage = llm.chat_json(system, user, schema=GeneratedCaseList)
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
        with session_factory() as s:
            for c in parsed_cases.cases:
                s.add(
                    TestCase(
                        name=_sanitize_llm_field(c.name),
                        method=c.method,
                        path=_sanitize_llm_field(c.path),
                        operation_id=op_id,  # 【防幻觉】服务端注入，不信任 LLM 输出
                        params=_sanitize_llm_field(c.params or None),
                        body=_sanitize_llm_field(c.body),
                        expected_status=c.expected_status,
                        assertions=_sanitize_llm_field(c.assertions or None),
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
        generated += 1
        draft_created += len(parsed_cases.cases)
        cost_total += cost

    # 收尾：result_summary + SUCCESS
    with session_factory() as s:
        task = s.get(GenerationTask, task_id)
        if task is None:
            return
        task.operation_count = generated
        task.result_summary = {
            "generated": generated,
            "draft_created": draft_created,
            "rejected": rejected,
            "rejected_detail": rejected_detail,
            "skipped_by_filter": skipped_by_filter,
            "skipped_detail": skipped_detail,
            "prompt_version": PROMPT_VERSION,
            "cost_total": round(cost_total, 6),
        }
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
            return existing  # Lookup-Create：存在即返回

        task = GenerationTask(
            run_id=run_id,
            document=document,
            operation_ids=operation_ids,
            status=GenerationStatus.PENDING,
        )
        self.repo.add(task)
        self._dispatch(task.id)
        return task

    def get_generation_task(self, task_id: int) -> GenerationTask:
        return self.repo.get_or_raise(task_id)

    def _dispatch(self, task_id: int) -> None:
        # why：延迟导入防循环引用（celery 任务模块会反向 import 本包）；测试 monkeypatch 隔离
        from app.tasks.generate_cases import generate_cases_task  # noqa: PLC0415

        result = generate_cases_task.delay(task_id)
        # why：持久化 celery_task_id（RULES §8.3）——任务卡死时可通过 Celery 定位/revoke
        task = self.session.get(GenerationTask, task_id)
        if task is not None:
            task.celery_task_id = result.id
            try:
                self.session.commit()
            except Exception:
                self.session.rollback()
                raise
