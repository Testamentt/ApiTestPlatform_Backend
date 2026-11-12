# 本项目编码格式：UTF-8（无 BOM）。
# 执行器工厂。why：把「在哪里跑」收敛成一个配置项（execution.executor=local|jenkins），
# 编排层只依赖 Executor 协议；新增执行器（如 K8s Job）只需加实现 + 在这里注册一行。
from __future__ import annotations

import logging
from collections.abc import Callable

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.services.executors.base import ExecutionResult, Executor, StartCallback
from app.services.executors.jenkins import JenkinsExecutor
from app.services.executors.local import LocalExecutor
from app.utils.subprocess_util import CmdResult, run_cmd

logger = logging.getLogger(__name__)

__all__ = [
    "ExecutionResult",
    "Executor",
    "JenkinsExecutor",
    "LocalExecutor",
    "StartCallback",
    "get_executor",
]


def get_executor(
    *,
    settings: Settings | None = None,
    run_cmd_fn: Callable[..., CmdResult] = run_cmd,
) -> Executor:
    """按配置返回执行器。why run_cmd_fn 可注入：测试替换子进程层，不动真实命令白名单。"""
    cfg = settings or get_settings()
    name = cfg.execution.executor
    if name == "local":
        return LocalExecutor(cfg, run_cmd_fn)
    if name == "jenkins":
        return JenkinsExecutor(cfg)
    raise AppError(
        "EXECUTOR_NOT_SUPPORTED",
        status_code=500,
        detail=f"不支持的执行器 '{name}'，可选 local|jenkins",
    )
