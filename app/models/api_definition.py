# Swagger 版本快照。why：只存 operation_ids + 分段 hashes + contracts（不存原文），支撑 O(1) diff；
# reparse 覆盖由 repository 的 upsert 实现（同 version 先删旧插新，CI 幂等不膨胀）。
from __future__ import annotations

from sqlalchemy import JSON, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class ApiDefinition(TimestampMixin, Base):
    __tablename__ = "api_definitions"

    version: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    hash_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    operation_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    operation_hashes: Mapped[dict] = mapped_column(JSON, nullable=False)
    operation_contracts: Mapped[dict] = mapped_column(JSON, nullable=False)
