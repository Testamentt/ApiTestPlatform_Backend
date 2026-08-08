# 影响分析数据访问。why：mark_regression 持久化每次一键回归结果（F3：可追溯）。
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError

from app.models.impact_analysis import ImpactAnalysis
from app.repositories.base import BaseRepository


class ImpactAnalysisRepository(BaseRepository[ImpactAnalysis]):
    model = ImpactAnalysis

    def mark_regression(self, analysis_id: int, task_id: int, executed_count: int) -> None:
        """F3：回归结果持久化——更新 last_regression_*，面试展示「每次回归可追溯」。"""
        analysis = self.get_or_raise(analysis_id)
        analysis.last_regression_at = datetime.now(UTC).replace(tzinfo=None)
        analysis.last_regression_task_id = str(task_id)
        analysis.last_regression_executed_count = executed_count
        try:
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise
