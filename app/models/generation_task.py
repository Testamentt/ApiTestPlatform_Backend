# AI 生成任务。why：独立于执行 tasks 表（语义分离，RULES §8.3）；run_id 幂等（Lookup-Create 防重复调 LLM）；
# document 落库——大对象不传队列（任务入参只传 task_id，RULES §8.4）。
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin
from app.models.enums import GenerationStatus


class GenerationTask(TimestampMixin, Base):
    __tablename__ = "generation_tasks"

    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=GenerationStatus.PENDING,
        server_default=text("'pending'"),
    )
    celery_task_id: Mapped[str | None] = mapped_column(String(64))
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    operation_ids: Mapped[list | None] = mapped_column(JSON)  # 定向子集；NULL=全量/untested
    operation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    prompt_version: Mapped[str | None] = mapped_column(String(16))
    error_stage: Mapped[str | None] = mapped_column(String(32))  # parse/internal/timeout/dispatch
    error_msg: Mapped[str | None] = mapped_column(Text)
    result_summary: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (Index("idx_generation_tasks_status", "status"),)
