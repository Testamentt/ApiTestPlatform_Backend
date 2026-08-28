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
from app.services.dispatcher import dispatch_execution, dispatch_or_fail


class TaskService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.task_repo = TaskRepository(session)
        self.case_repo = CaseRepository(session)

    @staticmethod
    def compute_run_id(case_ids: list[int], timeout_seconds: int) -> str:
        """why：排序 + 序列化保证同输入同指纹；sha256 作为幂等键。"""
        key = json.dumps({"case_ids": sorted(case_ids), "timeout": timeout_seconds}, sort_keys=True)
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
        # Lookup-Create 幂等：SUCCESS 直接复用；FAILED 重置重试；PENDING/RUNNING 返回现状（review H4）
        existing = self.task_repo.find_by_run_id(run_id)
        if existing:
            if existing.status == TaskStatus.SUCCESS.value:
                return existing  # §8.3：已成功不重复执行
            if existing.status == TaskStatus.FAILED.value:
                # why：失败任务允许同输入重试——重置 PENDING 重新入队；任务函数有 PENDING 状态守卫，重复派发安全
                existing.status = TaskStatus.PENDING.value
                existing.error_stage = None
                existing.error_msg = None
                existing.pid = None
                existing.celery_task_id = None
                existing.finished_at = None
                try:
                    self.session.commit()
                except Exception:
                    self.session.rollback()
                    raise
                dispatch_or_fail(existing, TaskStatus, dispatch_execution, session=self.session)
                return existing
            return existing
        task = Task(
            run_id=run_id,
            case_ids=payload.case_ids,
            status=TaskStatus.PENDING,
            timeout_seconds=timeout,
        )
        self.task_repo.add(task)
        # why：持久化 celery_task_id（RULES §8.3）——任务卡死时可通过 Celery 定位/revoke
        dispatch_or_fail(task, TaskStatus, dispatch_execution, session=self.session)
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
        return TaskResults(task=task, results=[TaskResultItem(**item) for item in results])
