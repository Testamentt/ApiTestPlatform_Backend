# 逐 operation LLM 调用日志。why：审计可追溯（usage/cost/confidence/raw_response，RULES §9.6）；
# 校验失败同样落库（validation_failed + 具体错误 + 原文）——不建坏用例但可追溯，防「静默丢失败」（§11.2）。
from __future__ import annotations

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class GenerationLog(TimestampMixin, Base):
    __tablename__ = "generation_logs"

    # why：fix-hints 的 LLM 调用无关联生成任务，允许 NULL（文档承诺 model='fix_hint' 来源区分）
    generation_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("generation_tasks.id"), nullable=True, index=True
    )
    operation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)  # 生成=model，建议=fix_hint
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # success/validation_failed/error
    ai_confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0
    )  # 校验通过=1.0、失败=0.0
    usage: Mapped[dict | None] = mapped_column(
        JSON
    )  # {prompt_tokens, completion_tokens, total_tokens}
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_estimate: Mapped[float | None] = mapped_column(Float)
    raw_response: Mapped[str | None] = mapped_column(Text)  # 校验失败时保存 LLM 原始响应
    error_msg: Mapped[str | None] = mapped_column(Text)  # 具体 Pydantic 校验错误
