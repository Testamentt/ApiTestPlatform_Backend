# 影响分析结果。why：affected_case_ids 快照是回归依据；affected_count 由 @validates 写时同步——
# validates 只在整体赋值时触发（列表原地 append 不触发），故业务写入口收敛为整体替换。
# last_regression_* 持久化每次一键回归结果（F3：可追溯）。
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.core.database import Base
from app.models.base import TimestampMixin


class ImpactAnalysis(TimestampMixin, Base):
    __tablename__ = "impact_analyses"

    old_version: Mapped[str | None] = mapped_column(String(64))
    new_version: Mapped[str] = mapped_column(String(64), nullable=False)
    added_ops: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    removed_ops: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    changed_ops: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    breaking_changed_ops: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    affected_case_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    affected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    orphaned_case_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    suggested_remap: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    untested_ops: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    affected_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    last_regression_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_regression_task_id: Mapped[str | None] = mapped_column(String(64))
    last_regression_executed_count: Mapped[int | None] = mapped_column(Integer)
    ai_fix_hint: Mapped[dict | None] = mapped_column(JSON)  # Phase 3：breaking 变更的一句话 LLM 修复建议（best-effort，可空）

    @validates("affected_case_ids")
    def _sync_affected_count(self, key, value):
        # why：冗余计数写时同步；仅整体赋值触发（原地 append 不触发 validates，业务禁止原地改）
        self.affected_count = len(value)
        return value
