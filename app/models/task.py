# 执行任务模型。why：run_id（sha256 指纹）唯一，支撑 Lookup-Create 幂等；pid 记录 pytest 进程供超时劫持。
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin
from app.models.enums import TaskStatus


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"

    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    case_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    # why：任务级执行超时（review M3）——此前只进幂等键不生效，语义误导；真正控制 subprocess 超时
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=300, server_default=text("300")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TaskStatus.PENDING, server_default=text("'pending'")
    )
    pid: Mapped[int | None] = mapped_column(Integer)
    celery_task_id: Mapped[str | None] = mapped_column(String(64))
    error_stage: Mapped[str | None] = mapped_column(String(32))
    error_msg: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    result_summary: Mapped[dict | None] = mapped_column(JSON)
    report_link: Mapped[str | None] = mapped_column(String(1024))

    __table_args__ = (Index("idx_tasks_status", "status"),)
