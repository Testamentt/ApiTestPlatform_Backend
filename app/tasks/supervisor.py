# 本项目编码格式：UTF-8（无 BOM）。
# 超时劫持。why：权威兜底——从 DB 读 mark_running 写入的 tasks.pid 杀整棵树；
# 僵尸任务迁移 failed（error_stage=timeout），禁止自动重试（重试仅人工）。
# 执行任务与生成任务都扫：Worker 强杀后 generation_tasks 卡 RUNNING 会污染 run_id；
# PENDING 孤儿同样回收——消息入队却无人领取会永久占用 run_id。
# 触发：Beat 周期调度（celery.beat_schedule）+ Worker 启动时各扫一次——只在启动扫意味着
# 「Worker 一直不重启」时僵尸任务永远不被回收，任务卡到下一次人工重启才恢复。
from __future__ import annotations

import logging
from collections.abc import Iterable
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


def queue_order_key(task: Task) -> tuple[datetime, int]:
    """队列顺序键。why：SQLite 的 created_at 由 CURRENT_TIMESTAMP 生成，同秒创建的并行任务会撞值，
    单看 created_at 排不出稳定顺序；(created_at, id) 才是与自增主键一致的 FIFO 口径。"""
    return (task.created_at or datetime.min, task.id)


def pending_allowance_seconds(
    *,
    base_timeout: int,
    ahead_timeouts: Iterable[int],
) -> float:
    """某个 PENDING 任务允许排队的秒数（队列感知）。

    why：`pending_timeout` 是「没人领取」的判定，而不是「排队太久」的判定——solo 单 Worker 串行执行时
    排在第三位的 300s 任务正常也要等 600s 以上；用全局固定值会把它当成孤儿误杀成 failed(pending_timeout)，
    而这种 failed 需要人工重试，等于把正常的排队变成故障。

    口径：前面每个任务最坏占满它自己的 `timeout_seconds`，叠加在基础宽限之上——
    `base_timeout + Σ(前序任务超时)`。前面没任务时退化为全局值（孤儿判定语义不变）。
    """
    return float(base_timeout + sum(max(0, int(t or 0)) for t in ahead_timeouts))


@celery_app.task(name="scan_stale_tasks")
def scan_stale_tasks() -> int:
    now = _utcnow()
    settings = get_settings()
    exec_threshold = settings.execution.pytest_timeout
    gen_threshold = settings.llm.task_timeout_seconds
    pending_threshold = settings.celery.pending_timeout
    stale: list[Task] = []
    stale_gen: list[GenerationTask] = []
    pending: list[tuple[Task, float]] = []
    with SessionLocal() as session:
        running = session.query(Task).filter(Task.status == TaskStatus.RUNNING.value).all()
        for task in running:
            if task.started_at is None:
                continue
            # why：任务级超时优先，缺省回退全局 pytest_timeout
            threshold = getattr(task, "timeout_seconds", None) or exec_threshold
            if (now - task.started_at).total_seconds() <= threshold:
                continue
            stale.append(task)
        # why：PENDING 孤儿回收——消息入队后无人领取（broker 丢消息/Worker 长期缺席/
        # 队列被清）会永久停在 pending，且 Lookup-Create 会把同输入永久复用成这个死任务。
        # 计时用 updated_at 而非 created_at：重试会把老行重置为 pending，用创建时间会立刻误杀。
        # 阈值按队列位置放宽（见 pending_allowance_seconds）：solo 串行下排队本身就是正常等待。
        pending_rows = session.query(Task).filter(Task.status == TaskStatus.PENDING.value).all()
        # 前面所有任务「最坏占满自身超时」的累计预算，按 FIFO 顺序逐个体累加
        ahead_budget = [getattr(t, "timeout_seconds", None) or exec_threshold for t in running]
        for task in sorted(pending_rows, key=queue_order_key):
            if task.updated_at is None:
                continue
            allowance = pending_allowance_seconds(
                base_timeout=pending_threshold, ahead_timeouts=ahead_budget
            )
            ahead_budget.append(getattr(task, "timeout_seconds", None) or exec_threshold)
            if (now - task.updated_at).total_seconds() <= allowance:
                continue
            pending.append((task, allowance))
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
    # 短事务分界：先关事务再杀进程树——禁止持 DB Session 期间调 subprocess
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
            for task, allowance in pending:
                t = session.get(Task, task.id)
                if t is None:
                    continue
                t.status = TaskStatus.FAILED.value
                t.error_stage = "pending_timeout"
                t.error_msg = (
                    f"排队超过 {allowance:.0f}s（含前序任务超时预算）未被 Worker 领取，"
                    f"回收于 {now}（可人工重试）"
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
