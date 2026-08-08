# 用例模型。why：operation_id 必填——Phase 2 影响分析的血缘映射依据（创建即绑定）。
from __future__ import annotations

from sqlalchemy import JSON, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin
from app.models.enums import CaseSource, CaseStatus


class TestCase(TimestampMixin, Base):
    # why：__test__=False——pytest 会把 Test 前缀类当测试类收集（测试内 import 本模型时报
    # PytestCollectionWarning），显式排除避免误收集。
    __test__ = False

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
    # 【面试锚点】血缘可信度 0-100。计算口径：
    # - 手工创建 = 100（Phase 1/2）
    # - AI 生成（校验通过，parse warnings 为空）= 80（Phase 3）
    # - AI 生成（校验通过，parse warnings 非空）= 60（Phase 3）
    # 赋值位置：generation_service.generate 中逐 operation 落库时写入（可导航指针）。
    # 低分用例需重点 Review（confirm 是进 active 的唯一入口，天然兜底）；动态降权 Phase 4。
    trust_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default=text("100")
    )

    __table_args__ = (Index("idx_test_cases_status", "status"),)
