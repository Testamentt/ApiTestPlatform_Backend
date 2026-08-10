# 版本快照数据访问。why：get_latest（id desc 取最近）+ upsert 实现 reparse 覆盖（同 version 先删旧插新）。
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.models.api_definition import ApiDefinition
from app.repositories.base import BaseRepository


class ApiDefinitionRepository(BaseRepository[ApiDefinition]):
    model = ApiDefinition

    def get_latest(self) -> ApiDefinition | None:
        """最近版本。why：id desc（created_at 有同秒风险）。"""
        return self.session.scalar(select(ApiDefinition).order_by(ApiDefinition.id.desc()).limit(1))

    def get_by_version(self, version: str) -> ApiDefinition | None:
        return self.session.scalar(select(ApiDefinition).where(ApiDefinition.version == version))

    def resolve_version(self, requested: str | None) -> str:
        """version 解析：用户指定或 auto `v{n}`。why：auto 递增时跳过已存在版本，
        避免 latest 为非 vN 标签时回退算出 v1 撞历史 v1 快照（UNIQUE 冲突 / 覆盖 diff 基线）。"""
        if requested:
            return requested
        n = 1
        latest = self.get_latest()
        if latest and latest.version.startswith("v") and latest.version[1:].isdigit():
            n = int(latest.version[1:]) + 1
        while self.get_by_version(f"v{n}") is not None:
            n += 1
        return f"v{n}"

    def upsert(
        self,
        *,
        version: str,
        hash_version: int,
        operation_ids: list,
        operation_hashes: dict,
        operation_contracts: dict,
    ) -> ApiDefinition:
        """reparse 覆盖：同 version 先删旧插新（CI 重复触发不膨胀版本表）。"""
        existing = self.get_by_version(version)
        if existing is not None:
            self.session.delete(existing)
            self.session.flush()  # 释放 UNIQUE(version)，避免插入冲突
        obj = ApiDefinition(
            version=version,
            hash_version=hash_version,
            operation_ids=operation_ids,
            operation_hashes=operation_hashes,
            operation_contracts=operation_contracts,
        )
        self.session.add(obj)
        try:
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()  # 先删后插，任何一步失败整事务回滚（RULES §6.1）
            raise
        self.session.refresh(obj)
        return obj
