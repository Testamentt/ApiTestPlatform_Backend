# generate_cases Celery 任务。why：LLM 调用不阻塞 Web（RULES §9.5）；
# time_limit 取 llm.task_timeout_seconds（200 接口串行 ≈400s 的硬超时兜底）；
# 不设 autoretry——LLM 瞬时重试已在 llm_client 内部收敛，Celery 层重跑会重复生成 draft（副作用）。
from __future__ import annotations

from app.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.generation_service import run_generation


@celery_app.task(name="generate_cases", time_limit=get_settings().llm.task_timeout_seconds)
def generate_cases_task(task_id: int) -> None:
    run_generation(SessionLocal, task_id)
