# Celery 实例。why：显式读 get_settings() 拼 broker/backend URL（Celery 不会自动加载 FastAPI Settings）；
# worker_ready 触发日志接管 + scan_stale_tasks（启动扫描一次，无 Beat）。
from __future__ import annotations

import logging
import sys

from celery import Celery
from celery.signals import after_setup_logger, after_setup_task_logger, worker_ready

from app.core.config import get_settings

logger = logging.getLogger(__name__)

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
        # why：队列名即环境命名空间（P3-2）——不同环境/库共用同一 Redis 时靠它物理隔离，
        # 避免"另一个环境的消息被本环境 Worker 消费后 skip"这类跨环境串扰
        task_default_queue=settings.celery.queue,
        # why：visibility_timeout 必须 > time_limit，否则运行中任务被重复投递（RULES.md §8.2）
        broker_transport_options={"visibility_timeout": settings.celery.visibility_timeout},
        result_expires=settings.celery.result_expires,
        broker_connection_retry_on_startup=True,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        include=["app.tasks.execute_cases", "app.tasks.supervisor", "app.tasks.generate_cases"],
    )


configure_celery(celery_app)


def _ensure_utf8_streams() -> None:
    """把 Worker 进程 stdio 切到 UTF-8（P3-5）。

    why：Windows 下控制台/重定向默认用系统代码页（cp936）编码——日志里的中文会显示成乱码，
    编码失败时 logging 还会走 handleError 把那一行丢掉。应用侧字符串本身是正确的（API/DB 均 UTF-8），
    这里只解决"输出通道"的编码。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # 被替换过的流（如 CI 捕获对象）没有 reconfigure：跳过而非报错
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError) as e:
            logger.warning("Worker stdio 切换 UTF-8 失败（忽略）: %s", e)


def configure_worker_logging() -> None:
    """Worker 侧日志接管（review N1）+ 输出编码（P3-5）。

    why：统一日志格式与 request_id 此前只在 Web 进程安装（create_app → setup_logging），Worker 的
    任务/LLM/Celery 日志既没有 `[request_id]` 也没有 `logger:lineno`，README 承诺的"Web → Celery → LLM
    全链路可追踪"在 Worker 侧断环。这里不改 Celery 的 handler/level 结构，只把已有 handler 的格式与
    Filter 换成本项目的统一口径。
    """
    from app.core.logging import (  # noqa: PLC0415  # 延迟导入：避开 celery_app 与 core 的导入环
        apply_request_id_formatting,
    )

    _ensure_utf8_streams()

    apply_request_id_formatting(logging.getLogger())
    apply_request_id_formatting(logging.getLogger("celery"))


# why：必须挂在 Celery **自己完成 dictConfig 之后**的信号上——挂 worker_ready 实测会被它随后的
# logging 配置覆盖/沿用其 format 串（Celery 默认 format 不含 request_id，接管后仍打不出链路 id）
@after_setup_logger.connect
def _on_after_setup_logger(logger=None, **kwargs) -> None:
    configure_worker_logging()


@after_setup_task_logger.connect
def _on_after_setup_task_logger(logger=None, **kwargs) -> None:
    configure_worker_logging()


@worker_ready.connect
def _on_worker_ready(**kwargs) -> None:
    """why：Worker 启动即扫描一次超时任务（MVP 无 Beat），权威兜底 running 超时 + pending 排队孤儿。"""
    from app.tasks.supervisor import scan_stale_tasks  # noqa: PLC0415

    scan_stale_tasks.delay()
