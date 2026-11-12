# 本项目编码格式：UTF-8（无 BOM）。
# 执行器契约。why：执行引擎与「在哪里跑」解耦——本地 subprocess 与 Jenkins 只是两种实现，
# 编排层（状态机 / 事务边界 / 报告落盘）只依赖本协议，从而避免把 pytest 命令写死进服务层。
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.models.task import Task
from app.models.test_case import TestCase
from app.utils.junit_parser import JunitSummary

# on_start 由编排层注入：本地执行传子进程 pid，Jenkins 执行传 None（无本地进程可杀）
StartCallback = Callable[[int | None], None]


@dataclass(frozen=True)
class ExecutionResult:
    """一次执行的标准化结果。why：只暴露汇总与逐用例条目，调用方不必知道报告来自本地文件还是 Jenkins 制品。"""

    summary: JunitSummary
    entries: list[dict]
    build_url: str | None = None


class Executor(Protocol):
    name: str

    def run(
        self,
        *,
        task: Task,
        cases: list[TestCase],
        timeout_seconds: int,
        on_start: StartCallback,
    ) -> ExecutionResult: ...
