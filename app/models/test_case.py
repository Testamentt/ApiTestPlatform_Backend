# 用例模型。why：operation_id 必填——Phase 2 影响分析的血缘映射依据（创建即绑定）。
from __future__ import annotations

from sqlalchemy import JSON, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin
from app.models.enums import CaseSource, CaseStatus


class TestCase(TimestampMixin, Base):
    __tablename__ = "test_cases"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    params: Mapped[dict | None] = mapped_column(JSON)
    body: Mapped[dict | None] = mapped_column(JSON)
    expected_status: Mapped[int] = mapped_column(
        Integer, nullable=False, default=200, server_default=text("200")
    )
    assertions: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CaseStatus.DRAFT, server_default=text("'draft'")
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CaseSource.MANUAL, server_default=text("'manual'")
    )

    __table_args__ = (Index("idx_test_cases_status", "status"),)
