# 派发器。why：任务入队抽象为单点（当前是 Celery 异步版），Web 层不直接 import 任务模块；
# 延迟导入防循环引用（celery 任务模块会反向 import 本包）。
from __future__ import annotations


def dispatch_execution(task_id: int) -> None:
    from app.tasks.execute_cases import execute_cases_task  # noqa: PLC0415

    execute_cases_task.delay(task_id)
