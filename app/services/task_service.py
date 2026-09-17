# 任务创建入口。why：Lookup-Create 幂等（同 case_ids+timeout 只跑一次）+ 唯一约束竞态收敛；draft 禁止执行。
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models.enums import TaskStatus
from app.models.task import Task
from app.repositories.case_repository import CaseRepository
from app.repositories.task_repository import TaskRepository
from app.schemas.task import TaskCreate, TaskResultItem, TaskResults
from app.services.dispatcher import dispatch_execution, dispatch_or_fail, revoke_task
from app.utils.report_util import report_path
from app.utils.subprocess_util import kill_process_tree

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    # why：时间统一存 UTC naive（对齐 RULES.md §5.2）。
    return datetime.now(UTC).replace(tzinfo=None)


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
        timeout = (
            payload.timeout_seconds or get_settings().execution.pytest_timeout
        )  # 缺省走 config（R3-6）
        run_id = self.compute_run_id(payload.case_ids, timeout)
        # Lookup-Create 幂等：SUCCESS 直接复用；FAILED 重置重试；PENDING/RUNNING 返回现状（review H4）
        existing = self.task_repo.find_by_run_id(run_id)
        if existing:
            if existing.status == TaskStatus.SUCCESS.value:
                return existing  # §8.3：已成功不重复执行
            if existing.status == TaskStatus.FAILED.value:
                # why：失败任务允许同输入重试——复用 retry_task 的重置逻辑；任务函数有 PENDING 状态守卫，重复派发安全
                return self._reset_and_dispatch(existing)
            return existing
        task = Task(
            run_id=run_id,
            case_ids=payload.case_ids,
            status=TaskStatus.PENDING,
            timeout_seconds=timeout,
        )
        try:
            self.task_repo.add(task)
        except IntegrityError as e:
            # why：并发双击/重试撞 UNIQUE(run_id)——唯一约束已拦住重复建，此处把"赢家"查回来返回，
            # 使并发语义与串行一致（review C1：此前未捕获 → 用户收到 500）
            winner = self.task_repo.resolve_after_unique_violation(
                lambda: self.task_repo.find_by_run_id(run_id)
            )
            if winner is None:
                raise AppError(
                    "TASK_CONFLICT",
                    status_code=409,
                    detail="并发创建任务冲突且未查到既有任务，请重试",
                ) from e
            logger.info("任务并发创建命中既有任务 id=%s run_id=%s", winner.id, run_id)
            return winner
        # why：持久化 celery_task_id（RULES §8.3）——任务卡死时可通过 Celery 定位/revoke；
        # 执行任务把 timeout_seconds 透传给派发器（任务级 soft/time limit 与接口口径对齐，review N13）
        dispatch_or_fail(
            task,
            TaskStatus,
            dispatch_execution,
            session=self.session,
            dispatch_kwargs={"timeout_seconds": task.timeout_seconds},
        )
        return task

    def retry_task(self, task_id: int) -> Task:
        """人工重试失败任务（RULES §8.5：重试仅人工触发）。

        why：取消/排队超时回收后必须有重跑出口（review N4）——否则 run_id 被 FAILED 行永久占死，
        用户只能改 timeout 绕开（幂等键含 timeout，纯属意外行为）。
        """
        task = self.task_repo.get_or_raise(task_id)
        if task.status != TaskStatus.FAILED.value:
            raise AppError(
                "TASK_NOT_RETRYABLE",
                status_code=409,
                detail=f"仅 failed 任务可重试，当前 {task.status}",
            )
        return self._reset_and_dispatch(task)

    def cancel_task(self, task_id: int) -> Task:
        """取消任务（PENDING 与 RUNNING 均可，P2-3）。

        why：PENDING 与 RUNNING 的差别只在"要不要动手"——RUNNING 必须先杀 pytest 进程树，
        否则状态改了、进程还在跑（继续占着被测系统）。杀树是 best-effort：杀不掉也不阻塞终态，
        权威兜底是 `scan_stale_tasks`。终态一律 `failed(error_stage=cancelled)` 承载，不扩状态枚举。
        条件更新保证与 Worker 的并发交错不会"谎报取消成功"。
        """
        task = self.task_repo.get_or_raise(task_id)
        cancellable = (TaskStatus.PENDING.value, TaskStatus.RUNNING.value)
        if task.status not in cancellable:
            raise AppError(
                "TASK_NOT_CANCELLABLE",
                status_code=409,
                detail=f"仅 pending/running 可取消，当前 {task.status}",
            )
        was_running = task.status == TaskStatus.RUNNING.value
        if was_running and task.pid:
            kill_process_tree(task.pid)  # best-effort；失败由扫描兜底
        # 条件更新：以"读到的那个状态"为前置条件——期间被 Worker 推进或已跑完（rowcount=0）即取消失败
        result = self.session.execute(
            update(Task)
            .where(Task.id == task_id, Task.status == task.status)
            .values(
                status=TaskStatus.FAILED.value,
                error_stage="cancelled",
                error_msg=f"用户取消于 {_utcnow()}",
                finished_at=_utcnow(),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            self.session.rollback()
            raise AppError(
                "TASK_NOT_CANCELLABLE",
                status_code=409,
                detail="任务状态已变化（可能已结束或被重新领取），请刷新后重试",
            )
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        if not was_running:
            # why：RUNNING 的消息早被 Worker 消费，revoke 无意义；只有还在排队的才需要撤回消息
            revoke_task(task.celery_task_id)
        self.session.refresh(task)
        logger.info(
            "任务 %s 已取消（原状态 %s，杀树=%s，原 celery_task_id=%s）",
            task_id,
            "running" if was_running else "pending",
            bool(was_running and task.pid),
            task.celery_task_id,
        )
        return task

    def _reset_and_dispatch(self, task: Task) -> Task:
        """FAILED→PENDING 重置并重新入队（创建路径与人工重试共用）。

        why：updated_at 必须随重置刷新——PENDING 孤儿回收按 updated_at 计时，
        否则一个"创建很久后失败再重试"的任务会被立刻误判为排队超时（review N4）。
        """
        task.status = TaskStatus.PENDING.value
        task.error_stage = None
        task.error_msg = None
        task.pid = None
        task.celery_task_id = None
        task.finished_at = None
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        dispatch_or_fail(
            task,
            TaskStatus,
            dispatch_execution,
            session=self.session,
            dispatch_kwargs={"timeout_seconds": task.timeout_seconds},
        )
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

    def get_report_html(self, task_id: int) -> str:
        """读取任务的 HTML 报告（带鉴权端点专用，review N3）。

        why：报告含用例名与失败信息，此前经无鉴权 /static 静态挂载暴露且 task_id 自增可枚举；
        改由本方法经业务鉴权链路读出。任务不存在与报告未生成区分成 404 与 404（不同 code，便于前端区分文案）。
        """
        self.get_task(task_id)  # 不存在 → 404 NOT_FOUND（复用统一语义）
        path = report_path(task_id)
        if not path.is_file():
            raise AppError(
                "REPORT_NOT_FOUND",
                status_code=404,
                detail=f"任务 {task_id} 尚未生成报告（任务未成功或报告生成失败）",
            )
        return path.read_text(encoding="utf-8")
