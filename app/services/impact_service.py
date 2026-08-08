# 影响分析编排。why：纯规则（解析→diff→检索→落库）短事务分界；回归宽容降级 + 真实口径 summary +
# last_regression_* 持久化 + 结构化日志审计。breaking_changed 唯一触发圈定（D1/F2）；removed 进迁移清单（D2）。
from __future__ import annotations

import json
import logging

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models.impact_analysis import ImpactAnalysis
from app.repositories.api_definition_repository import ApiDefinitionRepository
from app.repositories.case_repository import CaseRepository
from app.repositories.impact_analysis_repository import ImpactAnalysisRepository
from app.schemas.impact import (
    AffectedCase,
    AnalyzeResult,
    RegressionResult,
)
from app.schemas.task import TaskCreate
from app.services.task_service import TaskService
from app.utils.impact_diff import build_suggested_remap, diff_operations
from app.utils.openapi_parser import parse_openapi

logger = logging.getLogger(__name__)


class ImpactService:
    def __init__(self, session) -> None:
        self.session = session
        self.impact_repo = ImpactAnalysisRepository(session)
        self.def_repo = ApiDefinitionRepository(session)
        self.case_repo = CaseRepository(session)
        self.task_service = TaskService(session)

    def analyze(
        self,
        document: dict,
        new_version: str | None = None,
        old_version: str | None = None,
    ) -> AnalyzeResult:
        """新版 Swagger vs 旧版本 diff → 圈定受影响用例 → 落库分析结果。首次分析 added=untested=全部。"""
        settings = get_settings()
        if len(json.dumps(document)) > settings.swagger.max_upload_bytes:
            raise AppError(
                "SWAGGER_TOO_LARGE",
                status_code=422,
                detail=f"文档超过 {settings.swagger.max_upload_bytes} 字节上限",
            )
        parsed = parse_openapi(document)  # 无 Session 解析（短事务分界）

        old_def = self.def_repo.get_by_version(old_version) if old_version else self.def_repo.get_latest()
        old_label = old_def.version if old_def else None
        diff = diff_operations(
            old_def.operation_ids if old_def else [],
            old_def.operation_hashes if old_def else {},
            old_def.operation_contracts if old_def else {},
            parsed.operation_ids,
            parsed.operation_hashes,
            parsed.operation_contracts,
            hash_version_equal=(old_def is None or old_def.hash_version == settings.swagger.hash_version),
        )

        new_label = self.def_repo.resolve_version(new_version)
        # 碰撞守卫：新版本号与对比基线相同会覆盖历史快照（diff 源永久丢失），拒绝而非静默覆盖
        if old_def is not None and new_label == old_label:
            raise AppError(
                "VERSION_COLLISION",
                status_code=409,
                detail=f"新版本号 {new_label} 与对比基线相同，会覆盖历史快照，请更换版本号",
            )
        # 新版落库（reparse 覆盖）——版本历史自洽，连续 v1→v2→v3 可对比
        self.def_repo.upsert(
            version=new_label,
            hash_version=settings.swagger.hash_version,
            operation_ids=parsed.operation_ids,
            operation_hashes=parsed.operation_hashes,
            operation_contracts=parsed.operation_contracts,
        )

        # 反向检索：affected=breaking 变更的 active 用例；orphaned=removed 绑定的用例（迁移清单）
        affected_cases = self.case_repo.find_by_operation_ids(diff.breaking_changed, active_only=True)
        orphaned_cases = self.case_repo.find_by_operation_ids(diff.removed, active_only=True)
        bound = self.case_repo.bound_operation_ids(parsed.operation_ids)
        untested_ops = sorted(set(parsed.operation_ids) - bound)  # D3：未绑用例接口清单
        affected_ids = sorted({c.id for c in affected_cases})
        remap = build_suggested_remap(diff.removed, diff.added)

        analysis = ImpactAnalysis(
            old_version=old_label,
            new_version=new_label,
            added_ops=diff.added,
            removed_ops=diff.removed,
            changed_ops=diff.changed,
            breaking_changed_ops=diff.breaking_changed,
            affected_case_ids=affected_ids,  # @validates 同步 affected_count
            orphaned_case_ids=sorted({c.id for c in orphaned_cases}),
            suggested_remap=remap,
            untested_ops=untested_ops,
            affected_summary={"total": len(affected_ids)},
        )
        self.impact_repo.add(analysis)

        return AnalyzeResult(
            analysis_id=analysis.id,
            old_version=old_label,
            new_version=new_label,
            added_ops=analysis.added_ops,
            removed_ops=analysis.removed_ops,
            changed_ops=analysis.changed_ops,
            breaking_changed_ops=analysis.breaking_changed_ops,
            affected_cases=[
                AffectedCase(case_id=c.id, name=c.name, method=c.method, path=c.path) for c in affected_cases
            ],
            affected_summary=analysis.affected_summary,
            orphaned_case_ids=analysis.orphaned_case_ids,
            suggested_remap=analysis.suggested_remap,
            untested_ops=analysis.untested_ops,
            warnings=parsed.warnings,
        )

    def regression(self, analysis_id: int) -> RegressionResult:
        """一键回归。宽容降级：快照里失效用例被过滤，只执行仍 active 的；summary 用执行时真实口径（D6）。"""
        analysis = self.impact_repo.get_or_raise(analysis_id)
        affected = analysis.affected_case_ids
        if not affected:
            raise AppError("NO_AFFECTED_CASES", status_code=422, detail="无受影响用例")

        active_now = self.case_repo.find_active_by_ids(affected)
        active_ids = sorted({c.id for c in active_now})
        active_set = set(active_ids)
        dropped = [cid for cid in affected if cid not in active_set]
        existing_ids = {c.id for c in self.case_repo.find_by_ids(affected)}
        dropped_reasons = {
            cid: ("case deleted" if cid not in existing_ids else "case draft") for cid in dropped
        }
        if not active_ids:
            raise AppError(
                "NO_ACTIVE_CASES", status_code=422, detail=f"受影响用例全部失效: {dropped}"
            )

        task = self.task_service.create_execution_task(TaskCreate(case_ids=active_ids))  # Lookup-Create 幂等
        summary = {
            "total": len(active_ids) + len(dropped),  # 执行时真实口径，不用旧快照（D6）
            "executed": len(active_ids),
            "dropped": len(dropped),
        }
        self.impact_repo.mark_regression(analysis.id, task.id, len(active_ids))  # F3 持久化
        # 审计日志：保留 analysis/task/executed/dropped/reasons，无独立 event 表（可追溯）
        logger.info(
            "impact.regression analysis=%s task=%s executed=%s dropped=%s reasons=%s",
            analysis.id, task.id, active_ids, dropped, dropped_reasons,
        )
        return RegressionResult(
            analysis_id=analysis.id,
            task_id=task.id,
            task_status=task.status,
            executed_case_ids=active_ids,
            executed_count=len(active_ids),
            dropped_case_ids=dropped,
            dropped_count=len(dropped),
            dropped_reasons=dropped_reasons,
            affected_summary=summary,
        )
