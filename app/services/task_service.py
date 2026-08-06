# 任务创建入口。why：Lookup-Create 幂等（同 case_ids+timeout 只跑一次）；draft 禁止执行。
from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.models.enums import TaskStatus
from app.models.task import Task
from app.repositories.case_repository import CaseRepository
from app.repositories.task_repository import TaskRepository
from app.schemas.task import TaskCreate, TaskResultItem, TaskResults
from app.services.dispatcher import dispatch_execution


class TaskService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.task_repo = TaskRepository(session)
        self.case_repo = CaseRepository(session)

    @staticmethod
    def compute_run_id(case_ids: list[int], timeout_seconds: int) -> str:
        """why：排序 + 序列化保证同输入同指纹；sha256 作为幂等键。"""
        key = json.dumps(
            {"case_ids": sorted(case_ids), "timeout": timeout_seconds}, sort_keys=True
        )
        return hashlib.sha256(key.encode()).hexdigest()

    def create_execution_task(self, payload: TaskCreate) -> Task:
        active = self.case_repo.find_active_by_ids(payload.case_ids)
        active_ids = {c.id for c in active}
        missing = set(payload.case_ids) - active_ids
        if missing:
            raise AppError(
                "CASES_NOT_ACTIVE",
                status_code=422,
                detail=f"用例不存在或非 active: {sorted(missing)}",
            )
        timeout = payload.timeout_seconds or 300
        run_id = self.compute_run_id(payload.case_ids, timeout)
        # Lookup-Create：指纹已存在直接返回，不重复执行
        existing = self.task_repo.find_by_run_id(run_id)
        if existing:
            return existing
        task = Task(run_id=run_id, case_ids=payload.case_ids, status=TaskStatus.PENDING)
        self.task_repo.add(task)
        dispatch_execution(task.id)
        return task

    def get_task(self, task_id: int) -> Task:
        return self.task_repo.get_or_raise(task_id)

    def list_tasks(
        self, *, page: int, page_size: int, status: str | None = None
    ) -> tuple[list[Task], int]:
        filters = []
        if status:
            filters.append(Task.status == status)
        return self.task_repo.page(page, page_size, *filters, order_by=Task.created_at.desc())

    def get_task_results(self, task_id: int) -> TaskResults:
        task = self.get_task(task_id)
        results = (task.result_summary or {}).get("results", [])
        return TaskResults(
            task=task, results=[TaskResultItem(**item) for item in results]
        )
