# Celery 实例。why：显式读 get_settings() 拼 broker/backend URL（Celery 不会自动加载 FastAPI Settings）；
# worker_ready 触发 scan_stale_tasks（启动扫描一次，无 Beat）。
from __future__ import annotations

from celery import Celery
from celery.signals import worker_ready

from app.core.config import get_settings

celery_app = Celery("testplatform")


def configure_celery(app: Celery) -> None:
    settings = get_settings()
    app.conf.update(
        broker_url=settings.broker_url,
        result_backend=settings.result_backend,
        # why：acks_late 必须配 time_limit，否则卡死任务永不结束（RULES.md §16.7）
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_reject_on_worker_lost=True,
        task_soft_time_limit=settings.celery.soft_time_limit,
        task_time_limit=settings.celery.time_limit,
        result_expires=3600,
        broker_connection_retry_on_startup=True,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        include=["app.tasks.execute_cases", "app.tasks.supervisor"],
    )


configure_celery(celery_app)


@worker_ready.connect
def _on_worker_ready(**kwargs) -> None:
    """why：Worker 启动即扫描一次超时任务（MVP 无 Beat），权威兜底僵尸 running 任务。"""
    from app.tasks.supervisor import scan_stale_tasks  # noqa: PLC0415

    scan_stale_tasks.delay()
