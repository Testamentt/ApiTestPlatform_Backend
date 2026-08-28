# 派发器。why：任务入队抽象为单点（当前是 Celery 异步版），Web 层不直接 import 任务模块；
# dispatch_or_fail 收敛「入队失败兜底」策略（review M2），执行/生成两类任务共用同一实现。
# 延迟导入防循环引用（celery 任务模块会反向 import 本包）。
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.orm import Session

from app.core.exceptions import AppError

_TaskT = TypeVar("_TaskT")


def dispatch_execution(task_id: int) -> str | None:
    from app.middleware.request_id import get_request_id
    from app.tasks.execute_cases import execute_cases_task  # noqa: PLC0415

    # why：request_id 随任务参数透传（§6.2）——跨进程 ContextVar 不共享，
    # Worker 侧任务函数开头 set_request_id 恢复，任务日志/LLM 调用沿用同一链路 id
    return execute_cases_task.delay(task_id, request_id=get_request_id()).id


def dispatch_generation(task_id: int) -> str | None:
    from app.middleware.request_id import get_request_id
    from app.tasks.generate_cases import generate_cases_task  # noqa: PLC0415

    return generate_cases_task.delay(task_id, request_id=get_request_id()).id


def dispatch_or_fail(
    task: _TaskT,
    status_enum: type,
    dispatch_fn: Callable[[int], str | None],
    *,
    session: Session,
    label: str = "",
) -> None:
    """why：入队失败（Celery/Redis 不可用）不能留 PENDING 孤儿（review M2）——
    置 FAILED(dispatch) 后抛业务异常（503 结构化响应），同输入再提交走重试路径。
    label：错误信息前缀（如「生成」），区分执行/生成两条链路。"""
    try:
        result = dispatch_fn(task.id)
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
