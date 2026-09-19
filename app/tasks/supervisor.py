# 超时劫持。why：权威兜底——从 DB 读 mark_running 写入的 tasks.pid 杀整棵树；
# 僵尸任务迁移 failed（error_stage=timeout），禁止自动重试（重试仅人工）。
# 执行任务与生成任务都扫：Worker 强杀后 generation_tasks 卡 RUNNING 会污染 run_id（review H3）；
# PENDING 孤儿同样回收（review N4）——消息入队却无人领取会永久占用 run_id。
from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.enums import GenerationStatus, TaskStatus
from app.models.generation_task import GenerationTask
from app.models.task import Task
from app.services.dispatcher import revoke_task
from app.utils.subprocess_util import kill_process_tree

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@celery_app.task(name="scan_stale_tasks")
def scan_stale_tasks() -> int:
    now = _utcnow()
    settings = get_settings()
    exec_threshold = settings.execution.pytest_timeout
    gen_threshold = settings.llm.task_timeout_seconds
    pending_threshold = settings.celery.pending_timeout
    stale: list[Task] = []
    stale_gen: list[GenerationTask] = []
    pending: list[Task] = []
    with SessionLocal() as session:
        running = session.query(Task).filter(Task.status == TaskStatus.RUNNING.value).all()
        for task in running:
            if task.started_at is None:
                continue
            # why：任务级超时优先（review M3），缺省回退全局 pytest_timeout
            threshold = getattr(task, "timeout_seconds", None) or exec_threshold
            if (now - task.started_at).total_seconds() <= threshold:
                continue
            stale.append(task)
        for task in session.query(Task).filter(Task.status == TaskStatus.PENDING.value).all():
            # why：PENDING 孤儿回收（review N4）——消息入队后无人领取（broker 丢消息/Worker 长期缺席/
            # 队列被清）会永久停在 pending，且 Lookup-Create 会把同输入永久复用成这个死任务。
            # 计时用 updated_at 而非 created_at：重试会把老行重置为 pending，用创建时间会立刻误杀。
            if task.updated_at is None:
                continue
            if (now - task.updated_at).total_seconds() <= pending_threshold:
                continue
            pending.append(task)
        gen_running = (
            session.query(GenerationTask)
            .filter(GenerationTask.status == GenerationStatus.RUNNING.value)
            .all()
        )
        for task in gen_running:
            if task.started_at is None:
                continue
            if (now - task.started_at).total_seconds() <= gen_threshold:
                continue
            stale_gen.append(task)
        if stale or stale_gen or pending:
            session.expunge_all()  # 脱离 Session，供事务关闭后引用其已加载字段
    # 短事务分界：先关事务再杀进程树——禁止持 DB Session 期间调 subprocess（RULES §2.1）
    for task in stale:
        if task.pid:
            kill_process_tree(task.pid)  # 读 DB 写入的 pid 杀整棵树（权威清理；pid 缺失跳过）
    if stale or stale_gen or pending:
        with SessionLocal() as session:
            for task in stale:
                t = session.get(Task, task.id)
                if t is None:
                    continue
                t.status = TaskStatus.FAILED.value
                t.error_stage = "timeout"
                t.error_msg = f"running 超时，强杀于 {now}"
                t.finished_at = now
            for task in pending:
                t = session.get(Task, task.id)
                if t is None:
                    continue
                t.status = TaskStatus.FAILED.value
                t.error_stage = "pending_timeout"
                t.error_msg = (
                    f"排队超过 {pending_threshold}s 未被 Worker 领取，回收于 {now}（可人工重试）"
                )
                t.finished_at = now
                revoke_task(t.celery_task_id)  # 消息仍在 broker：撤销避免 Worker 领取后空跑
            for task in stale_gen:
                t = session.get(GenerationTask, task.id)
                if t is None:
                    continue
                t.status = GenerationStatus.FAILED.value
                t.error_stage = "timeout"
                t.error_msg = f"running 超时，强杀于 {now}"
                t.finished_at = now
            try:
                session.commit()
            except Exception:
                session.rollback()
                raise
        logger.info(
            "scan_stale_tasks: 迁移 %s 个执行任务(running) + %s 个排队孤儿(pending) + %s 个生成任务为 failed",
            len(stale),
            len(pending),
            len(stale_gen),
        )
    return len(stale) + len(pending) + len(stale_gen)
