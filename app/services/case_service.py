# 用例业务编排。why：业务规则（draft→active 防幻觉护栏、删除）收敛在 service，路由零逻辑。
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
        case = TestCase(**payload.model_dump())
        return self.repo.add(case)

    def get_case(self, case_id: int) -> TestCase:
        return self.repo.get_or_raise(case_id)

    def update_case(self, case_id: int, payload: CaseUpdate) -> TestCase:
        case = self.repo.get_or_raise(case_id)
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(case, key, value)
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
