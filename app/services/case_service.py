# 用例业务编排。why：业务规则（draft→active 防幻觉护栏、两层更新闸、删除）收敛在 service，路由零逻辑。
from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.models.enums import CaseStatus
from app.models.test_case import TestCase
from app.repositories.case_repository import CaseRepository
from app.schemas.case import CaseCreate, CaseUpdate


class CaseService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = CaseRepository(session)

    def create_case(self, payload: CaseCreate) -> TestCase:
        # why：mode="json" 把嵌套 AssertionItem 拍平成 dict——JSON 列无法序列化 pydantic 模型
        case = TestCase(**payload.model_dump(mode="json"))
        return self.repo.add(case)

    def get_case(self, case_id: int) -> TestCase:
        return self.repo.get_or_raise(case_id)

    def update_case(self, case_id: int, payload: CaseUpdate) -> TestCase:
        """两层闸更新（review C3 + P3-4 细化）。

        why：update 是 exclude_unset 部分更新——此前既不重校验也不降级，active 用例被改成坏数据后
        仍留在执行池（错误断言/非法 path 直到跑挂才暴露）。现补：
        ① 形状闸——**只对 active 用例**校验合并后的完整记录：执行池必须始终是合法数据。
           draft 不做形状闸（P3-4）：否则「历史坏行 + 只想改个名字」这类修复式更新会被自己的闸门卡死；
           draft 的安全性由 confirm 的激活闸 `_revalidate_for_active` 兜底（它是进执行池的唯一入口）。
        ② 语义闸——active 用例的语义字段一变即降级 draft，重新走人工审核（active 是执行资格，不是永久标签）。
        """
        case = self.repo.get_or_raise(case_id)
        # why：mode="json" 同 create_case——assertions 嵌套模型须落 dict 才能进 JSON 列，也便于与库中值比较
        changes = payload.model_dump(exclude_unset=True, mode="json")
        merged = {
            field: changes.get(field, getattr(case, field)) for field in CaseCreate.model_fields
        }
        if case.status == CaseStatus.ACTIVE.value:
            self._validate_merged(merged)
        semantic_changed = [
            field for field, value in changes.items() if value != getattr(case, field)
        ]
        for key, value in changes.items():
            setattr(case, key, value)
        if semantic_changed and case.status == CaseStatus.ACTIVE.value:
            # why：审核结论绑定的是"那一版内容"——内容变了，批准人签名随内容失效（reviewer 清空留痕于 updated_at/日志）
            case.status = CaseStatus.DRAFT.value
            case.reviewer = None
        try:
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise
        self.session.refresh(case)
        return case

    def confirm_case(self, case_id: int, reviewer: str) -> TestCase:
        """防幻觉护栏：仅 draft→active；已 active 幂等返回；禁止任何自动化路径直接置 active。"""
        case = self.repo.get_or_raise(case_id)
        if case.status == CaseStatus.ACTIVE:
            return case
        if case.status != CaseStatus.DRAFT:
            raise AppError(
                "INVALID_CONFIRM", status_code=409, detail=f"仅 draft 可确认，当前 {case.status}"
            )
        self._revalidate_for_active(case)
        case.status = CaseStatus.ACTIVE
        case.reviewer = reviewer  # 批准人落库（RULES §11.2：责任可审计，review R3-2）
        try:
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise
        self.session.refresh(case)
        return case

    def _validate_merged(self, merged: dict) -> None:
        """形状闸：合并后的记录必须能通过入参 schema（update 的唯一校验入口）。

        why：与 _revalidate_for_active 同一把尺子，但触发点更早——错在写入前，而不是等激活/执行时才发现。
        代价：若历史行被 out-of-band 写坏，任何不含该字段的更新都会被拒（422 detail 会点名坏字段，
        补上正确值即可修复）——这是有意的取舍：宁可拒绝一次更新，也不让坏数据继续流通。
        """
        try:
            CaseCreate(**merged)
        except ValidationError as e:
            fields = sorted({str(err["loc"][0]) for err in e.errors() if err.get("loc")})
            raise AppError(
                "CASE_INVALID",
                status_code=422,
                detail=f"更新后用例校验未通过，请修正字段 {fields}: {e}",
            ) from e

    def _revalidate_for_active(self, case: TestCase) -> None:
        """转 active 前按入参 schema 重新校验（RULES §11.2 MUST）。

        why：draft 期间数据可能被改坏或来自未校验的历史写入——激活是进入执行池的最后一道闸，
        校验失败必须拦在状态迁移前（否则坏用例获得执行资格）。
        """
        try:
            CaseCreate(
                name=case.name,
                method=case.method,  # type: ignore[arg-type]
                path=case.path,
                operation_id=case.operation_id,
                params=case.params,
                body=case.body,
                expected_status=case.expected_status,
                assertions=case.assertions,
            )
        except ValidationError as e:
            raise AppError(
                "CASE_INVALID_FOR_ACTIVE", status_code=422, detail=f"激活前校验未通过: {e}"
            ) from e

    def delete_case(self, case_id: int) -> None:
        self.repo.delete(self.repo.get_or_raise(case_id))

    def list_cases(self, *, page: int, page_size: int, **filters) -> tuple[list[TestCase], int]:
        return self.repo.list_by_filters(page=page, page_size=page_size, **filters)
