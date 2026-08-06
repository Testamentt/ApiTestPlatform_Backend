# 用例数据访问。
from __future__ import annotations

from sqlalchemy import or_, select

from app.models.enums import CaseStatus
from app.models.test_case import TestCase
from app.repositories.base import BaseRepository


class CaseRepository(BaseRepository[TestCase]):
    model = TestCase

    def find_active_by_ids(self, case_ids: list[int]) -> list[TestCase]:
        """why：执行引擎只能选 active（draft 禁止执行，防幻觉护栏）。"""
        return list(
            self.session.scalars(
                select(TestCase).where(
                    TestCase.id.in_(case_ids), TestCase.status == CaseStatus.ACTIVE
                )
            )
        )

    def list_by_filters(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
        method: str | None = None,
        operation_id: str | None = None,
        keyword: str | None = None,
    ) -> tuple[list[TestCase], int]:
        filters = []
        if status:
            filters.append(TestCase.status == status)
        if method:
            filters.append(TestCase.method == method)
        if operation_id:
            filters.append(TestCase.operation_id == operation_id)
        if keyword:
            filters.append(
                or_(
                    TestCase.name.like(f"%{keyword}%"),
                    TestCase.path.like(f"%{keyword}%"),
                )
            )
        return self.page(page, page_size, *filters)
