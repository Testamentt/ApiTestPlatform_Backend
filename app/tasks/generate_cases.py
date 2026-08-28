# generate_cases Celery 任务。why：LLM 调用不阻塞 Web（RULES §9.5）；
# soft_time_limit/task_timeout 取 llm 配置（200 接口串行 ≈400s 兜底），软超时捕获落 FAILED（RULES §8.2）；
# 不设 autoretry——LLM 瞬时重试已在 llm_client 内部收敛，Celery 层重跑会重复生成 draft（副作用）。
from __future__ import annotations

import logging

from celery.exceptions import SoftTimeLimitExceeded

from app.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.middleware.request_id import set_request_id
from app.services.generation_service import force_fail_timeout, run_generation

logger = logging.getLogger(__name__)


@celery_app.task(
    name="generate_cases",
    soft_time_limit=get_settings().llm.task_soft_timeout_seconds,
    time_limit=get_settings().llm.task_timeout_seconds,
)
def generate_cases_task(task_id: int, request_id: str | None = None) -> None:
    # why：Web 侧 dispatch 透传 request_id（§6.2）——LLM 调用日志携带同一链路 id
    set_request_id(request_id)
    try:
        run_generation(SessionLocal, task_id)
    except SoftTimeLimitExceeded:
        # why：RULES §8.2 MUST——软超时必须清理落 FAILED，否则 DB 卡 RUNNING 且 run_id 被永久污染
        logger.exception("task %s Celery 软超时，清理并落 failed", task_id)
        force_fail_timeout(SessionLocal, task_id)
