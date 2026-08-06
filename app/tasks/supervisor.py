# 超时劫持。why：权威兜底——从 DB 读 mark_running 写入的 tasks.pid 杀整棵树；
# 僵尸任务迁移 failed（error_stage=timeout），禁止自动重试（重试仅人工）。
from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.celery_app import celery_app
from app.core.database import SessionLocal
from app.models.enums import TaskStatus
from app.models.task import Task
from app.utils.subprocess_util import kill_process_tree

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@celery_app.task(name="scan_stale_tasks")
def scan_stale_tasks() -> int:
    now = _utcnow()
    killed = 0
    with SessionLocal() as session:
        running = session.query(Task).filter(Task.status == TaskStatus.RUNNING.value).all()
        for task in running:
            if task.started_at is None:
                continue
            elapsed = (now - task.started_at).total_seconds()
            if elapsed <= (task.timeout_seconds or 300):
                continue
            # 读 DB 写入的 pid 杀整棵树（权威清理；pid 缺失则跳过）
            if task.pid:
                kill_process_tree(task.pid)
            task.status = TaskStatus.FAILED.value
            task.error_stage = "timeout"
            task.error_msg = f"running 超时 {elapsed:.0f}s，强杀于 {now}"
            task.finished_at = now
            killed += 1
        if killed:
            session.commit()
    if killed:
        logger.info("scan_stale_tasks: 迁移 %s 个超时任务为 failed", killed)
    return killed
