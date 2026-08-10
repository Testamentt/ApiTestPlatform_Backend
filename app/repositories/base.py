# 通用数据访问。why：收敛查询（分页/异常），避免业务代码散落 session.query；MVP 物理删除。
from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.exceptions import AppError

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

    def _base_query(self) -> Select:
        return select(self.model)

    def get(self, obj_id: int) -> ModelT | None:
        return self.session.get(self.model, obj_id)

    def get_or_raise(self, obj_id: int) -> ModelT:
        obj = self.get(obj_id)
        if obj is None:
            raise AppError(
                "NOT_FOUND", status_code=404, detail=f"{self.model.__name__} {obj_id} 不存在"
            )
        return obj

    def page(
        self,
        page: int,
        page_size: int,
        *filters,
        order_by=None,
    ) -> tuple[list[ModelT], int]:
        stmt = self._base_query()
        for f in filters:
            stmt = stmt.where(f)
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        order = order_by if order_by is not None else self.model.id
        stmt = stmt.order_by(order).offset((page - 1) * page_size).limit(page_size)
        return list(self.session.scalars(stmt)), total

    def add(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        try:
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()  # 唯一回滚点，防止脏数据残留（RULES §15 示例 2）
            raise
        self.session.refresh(obj)
        return obj

    def delete(self, obj: ModelT) -> None:
        """物理删除（MVP，无软删除）。"""
        self.session.delete(obj)
        try:
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise
