# 派发器。why：周 1 同步直调 ExecutionService 验证执行逻辑；周 2 换 Celery 入队（最终形态）。
# 当前为 Celery 异步版；延迟导入防循环引用（celery 任务模块会反向 import 本包）。
from __future__ import annotations


def dispatch_execution(task_id: int) -> None:
    from app.tasks.execute_cases import execute_cases_task  # noqa: PLC0415

    execute_cases_task.delay(task_id)
