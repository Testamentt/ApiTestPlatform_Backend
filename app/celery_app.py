# 本项目编码格式：UTF-8（无 BOM）。
# Celery 实例。why：显式读 get_settings() 拼 broker/backend URL（Celery 不会自动加载 FastAPI Settings）；
# Beat 周期调度 scan_stale_tasks（僵尸回收）+ worker_ready 启动时补扫一次。
from __future__ import annotations

import logging
import sys

from celery import Celery
from celery.signals import after_setup_logger, after_setup_task_logger, worker_ready

from app.core.config import get_settings

logger = logging.getLogger(__name__)

celery_app = Celery("testplatform")


def beat_schedule(settings) -> dict:
    """Beat 周期任务表。why：僵尸回收此前只在 Worker 启动时扫一次——Worker 长期不重启时，
    被强杀的任务会一直停在 running、排队孤儿会永久占着 run_id，故障要等下一次重启才自愈。
    周期必须 < 最短的任务级 pending 判定，否则「扫得太晚」等于没扫；这里取
    `celery.stale_scan_interval`（默认 60s），>0 才启用（=0 关闭 Beat，保留纯启动扫描的部署形态）。
    """
    interval = settings.celery.stale_scan_interval
    if interval <= 0:
        return {}
    return {
        "scan-stale-tasks": {
            "task": "scan_stale_tasks",
            "schedule": float(interval),
            # why：不排队——本次没跑完就跳过下一次 tick，避免慢扫描堆积成串行队列
            "options": {"expires": float(interval)},
        }
    }


def configure_celery(app: Celery) -> None:
    settings = get_settings()
    app.conf.update(
        broker_url=settings.broker_url,
        result_backend=settings.result_backend,
        # why：acks_late 必须配 time_limit，否则卡死任务永不结束
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_reject_on_worker_lost=True,
        task_soft_time_limit=settings.celery.soft_time_limit,
        task_time_limit=settings.celery.time_limit,
        # why：队列名即环境命名空间——不同环境/库共用同一 Redis 时靠它物理隔离，
        # 避免"另一个环境的消息被本环境 Worker 消费后 skip"这类跨环境串扰
        task_default_queue=settings.celery.queue,
        # why：visibility_timeout 必须 > time_limit，否则运行中任务被重复投递
        broker_transport_options={"visibility_timeout": settings.celery.visibility_timeout},
        result_expires=settings.celery.result_expires,
        broker_connection_retry_on_startup=True,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        include=["app.tasks.execute_cases", "app.tasks.supervisor", "app.tasks.generate_cases"],
        # why：Beat 与 Worker 共用同一个 celery_app 实例——节拍只在这里定义一次，
        # 禁止在运维脚本/compose 里另写一份（两处口径分叉后「为什么扫描没跑」无法从代码回答）
        beat_schedule=beat_schedule(settings),
    )


configure_celery(celery_app)


def _ensure_utf8_streams() -> None:
    """把 Worker 进程 stdio 切到 UTF-8。

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
    """Worker 侧日志接管 + 输出编码。

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
    """why：Worker 启动即扫描一次（Beat 还没跑第一个 tick 时的即时兜底），
    周期回收由 beat_schedule 承担——两者入口同一个 scan_stale_tasks，不做两套逻辑。"""
    from app.tasks.supervisor import scan_stale_tasks  # noqa: PLC0415

    scan_stale_tasks.delay()
