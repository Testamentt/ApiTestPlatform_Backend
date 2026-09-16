# 派发器。why：任务入队抽象为单点（当前是 Celery 异步版），Web 层不直接 import 任务模块；
# dispatch_or_fail 收敛「入队失败兜底」策略（review M2），执行/生成两类任务共用同一实现；
# revoke_task 收敛「撤销已入队消息」（取消/排队超时回收，review N4）。
# 延迟导入防循环引用（celery 任务模块会反向 import 本包）。
from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)


def task_time_limits(timeout_seconds: int | None) -> dict[str, int]:
    """执行任务级 Celery 时间限制（review N13）。

    why：全局 soft_time_limit=300 与接口允许的 timeout_seconds（≤3600）口径冲突——用户在 Linux/prefork
    池下设 600s 会被 300s 提前掐掉。这里按任务真实超时派生限制，并留清理余量；
    §8.2 要求 time_limit < visibility_timeout（否则运行中任务被重复投递），故超上限时向 visibility 收敛。
    """
    settings = get_settings()
    base = timeout_seconds or settings.execution.pytest_timeout
    soft, hard = base + 30, base + 60
    visibility = settings.celery.visibility_timeout
    if hard >= visibility:
        hard = visibility - 60
        soft = min(soft, hard - 30)
    return {"soft_time_limit": soft, "time_limit": hard}


def dispatch_execution(task_id: int, *, timeout_seconds: int | None = None) -> str | None:
    from app.middleware.request_id import get_request_id
    from app.tasks.execute_cases import execute_cases_task  # noqa: PLC0415

    # why：request_id 随任务参数透传（§6.2）——跨进程 ContextVar 不共享，
    # Worker 侧任务函数开头 set_request_id 恢复，任务日志/LLM 调用沿用同一链路 id
    return execute_cases_task.apply_async(
        args=[task_id],
        kwargs={"request_id": get_request_id()},
        **task_time_limits(timeout_seconds),
    ).id


def dispatch_generation(task_id: int) -> str | None:
    from app.middleware.request_id import get_request_id
    from app.tasks.generate_cases import generate_cases_task  # noqa: PLC0415

    return generate_cases_task.delay(task_id, request_id=get_request_id()).id


def revoke_task(celery_task_id: str | None) -> bool:
    """撤销已入队的 Celery 消息（best-effort）。

    why：取消/排队超时回收后消息仍在 broker，Worker 领取时虽被状态守卫拦下（空跑一次），
    撤销可避免这次无意义领取；撤销失败不影响业务终态，但必须留日志（禁止静默吞）。
    """
    if not celery_task_id:
        return False
    try:
        from app.celery_app import celery_app  # noqa: PLC0415

        celery_app.control.revoke(celery_task_id)
        return True
    except Exception as e:  # noqa: BLE001  # broker 不可用不应反向拖垮取消/回收
        logger.warning("revoke 失败 celery_task_id=%s: %s", celery_task_id, e)
        return False


def dispatch_or_fail[TaskT](
    task: TaskT,
    status_enum: type,
    dispatch_fn: Callable[..., str | None],
    *,
    session: Session,
    label: str = "",
    dispatch_kwargs: dict | None = None,
) -> None:
    """why：入队失败（Celery/Redis 不可用）不能留 PENDING 孤儿（review M2）——
    置 FAILED(dispatch) 后抛业务异常（503 结构化响应），同输入再提交走重试路径。
    label：错误信息前缀（如「生成」），区分执行/生成两条链路；
    dispatch_kwargs：透传给 dispatch_fn 的任务级参数（如执行任务的 timeout_seconds）。"""
    try:
        result = dispatch_fn(task.id, **(dispatch_kwargs or {}))
    except Exception as e:
        task.status = status_enum.FAILED.value
        task.error_stage = "dispatch"
        task.error_msg = f"{label}任务入队失败（Celery/Redis 不可用）: {e}"
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
        raise AppError("DISPATCH_FAILED", status_code=503, detail=task.error_msg) from e
    if result:
        task.celery_task_id = result
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
