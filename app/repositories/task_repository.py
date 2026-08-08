# 任务数据访问。why：run_id 唯一（Lookup-Create）；update_status 做简单状态机守卫。
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.exceptions import AppError
from app.models.enums import TaskStatus
from app.models.task import Task
from app.repositories.base import BaseRepository


class TaskRepository(BaseRepository[Task]):
    model = Task

    def find_by_run_id(self, run_id: str) -> Task | None:
        return self.session.scalar(select(Task).where(Task.run_id == run_id))

    def find_running(self) -> list[Task]:
        return list(self.session.scalars(select(Task).where(Task.status == TaskStatus.RUNNING)))

    def update_status(self, task: Task, *, to: TaskStatus, from_: TaskStatus, **fields) -> Task:
        """简单状态机守卫。why：只允许顺序迁移（如 RUNNING→SUCCESS/FAILED），禁止任意跳转。"""
        if task.status != from_.value:
            raise AppError(
                "INVALID_TRANSITION", status_code=409, detail=f"{task.status} → {to.value} 不允许"
            )
        for key, value in fields.items():
            setattr(task, key, value)
        task.status = to.value
        try:
            self.session.commit()
        except SQLAlchemyError:
            self.session.rollback()
            raise
        return task
