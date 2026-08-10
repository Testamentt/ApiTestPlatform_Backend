# 生成任务数据访问。why：run_id 唯一（Lookup-Create 幂等）；update_status 简单状态机守卫。
from __future__ import annotations

from sqlalchemy import select

from app.core.exceptions import AppError
from app.models.enums import GenerationStatus
from app.models.generation_task import GenerationTask
from app.repositories.base import BaseRepository


class GenerationTaskRepository(BaseRepository[GenerationTask]):
    model = GenerationTask

    def find_by_run_id(self, run_id: str) -> GenerationTask | None:
        return self.session.scalar(select(GenerationTask).where(GenerationTask.run_id == run_id))

    def update_status(
        self, task: GenerationTask, *, to: GenerationStatus, from_: GenerationStatus
    ) -> GenerationTask:
        """简单状态机守卫。why：只允许顺序迁移（PENDING→RUNNING→SUCCESS/FAILED），禁止任意跳转。"""
        if task.status != from_.value:
            raise AppError(
                "INVALID_TRANSITION", status_code=409, detail=f"{task.status} → {to.value} 不允许"
            )
        task.status = to.value
        self.session.commit()
        return task
