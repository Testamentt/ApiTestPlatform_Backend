# 本项目编码格式：UTF-8（无 BOM）。
# 用例模型。why：operation_id 必填——影响分析的血缘映射依据（创建即绑定）。
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
    # 血缘可信度 0-100。计算口径：
    # - 手工创建 = 100
    # - AI 生成 = 逐条计算（app/services/case_quality.py）：100 起评，按未声明期望码 /
    #   断言字段不可判 / 无字段层断言 / 接口解析告警 / 重复 逐项扣分
    # 不再是由 document 级 warnings 决定的固定标签——同一文档内不同用例分数可以不同，
    # 扣分原因见 quality_flags。低分用例需重点 Review（confirm 是进 active 的唯一入口，天然兜底）。
    trust_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default=text("100")
    )
    # 扣分原因（JSON 数组，每项 {reason, penalty}）。why：分数必须能解释到「因为什么扣了几分」，
    # 否则它又变成一个不可追问的数字。手工用例为 NULL。
    quality_flags: Mapped[list | None] = mapped_column(JSON)
    # why：批准人审计（confirm 需传入 reviewer）——draft→active 的责任追溯字段，
    # 仅 confirm 接口写入；模型加列后 dev 库需 reset（create_all 不 ALTER，见 docs/database.md）
    reviewer: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (Index("idx_test_cases_status", "status"),)
