# execute_cases Celery 任务。why：Redis broker 为 at-least-once 投递，
# 写库幂等靠 run_id UNIQUE + 状态守卫（PENDING 才执行）；只对瞬时异常重试，业务错误直接 failed。
from __future__ import annotations

from app.celery_app import celery_app
from app.core.database import SessionLocal
from app.services.execution_service import ExecutionService


@celery_app.task(
    name="execute_cases",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def execute_cases_task(task_id: int) -> None:
    ExecutionService(SessionLocal).execute_cases(task_id)
