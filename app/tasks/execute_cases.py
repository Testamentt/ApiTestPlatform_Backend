# execute_cases Celery 任务。why：Redis broker 为 at-least-once 投递，
# 写库幂等靠 run_id UNIQUE + 状态守卫（PENDING 才执行）；只对瞬时异常重试，业务错误直接 failed。
from __future__ import annotations

import logging

from celery.exceptions import SoftTimeLimitExceeded

from app.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.execution_service import ExecutionService

logger = logging.getLogger(__name__)


@celery_app.task(
    name="execute_cases",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=get_settings().celery.max_retries,  # 来自 config（RULES §8.1，禁止硬编码）
)
def execute_cases_task(task_id: int) -> None:
    svc = ExecutionService(SessionLocal)
    try:
        svc.execute_cases(task_id)
    except SoftTimeLimitExceeded:
        # why：RULES §8.2 MUST——软超时必须清理并落 FAILED，避免 DB 状态停留在 running/pending；
        # 子进程由 force_fail_timeout best-effort 杀树，权威兜底是 scan_stale_tasks。
        logger.exception("task %s Celery 软超时，清理并落 failed", task_id)
        svc.force_fail_timeout(task_id)
