# 本项目编码格式：UTF-8（无 BOM）。
# 本地执行器：把 active 用例渲染成 pytest 文件，子进程执行并解析 JUnit。
# why：原逻辑内联在 ExecutionService，抽出后执行器只负责「跑 + 解析」，
# 状态机与事务边界仍留在编排层；行为与拆封前一致（返回码语义、白名单、超时都沿用）。
from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from app.core.config import Settings
from app.core.exceptions import AppError
from app.models.task import Task
from app.models.test_case import TestCase
from app.services.executors.base import ExecutionResult, StartCallback
from app.utils.case_generator import render_conftest, render_test_file
from app.utils.junit_parser import parse_junit_xml
from app.utils.subprocess_util import CmdResult

# run_cmd 由编排层注入：既保持唯一封装入口（subprocess_util），也让测试能替换它而不打真实子进程
RunCmd = Callable[..., CmdResult]


class LocalExecutor:
    name = "local"

    def __init__(self, settings: Settings, run_cmd: RunCmd) -> None:
        self._settings = settings
        self._run_cmd = run_cmd

    def run(
        self,
        *,
        task: Task,
        cases: list[TestCase],
        timeout_seconds: int,
        on_start: StartCallback,
    ) -> ExecutionResult:
        workspace = self._build_workspace(task.id, cases)
        test_files = sorted(workspace.glob("test_*.py"))
        if not test_files:
            raise AppError("NO_ACTIVE_CASES", status_code=422, detail="没有可执行的 active 用例")

        cmd = [
            sys.executable,
            "-m",
            "pytest",
            *[str(f) for f in test_files],
            f"--junitxml={workspace / 'report.xml'}",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
        ]
        result = self._run_cmd(
            cmd,
            timeout=timeout_seconds,
            check=False,
            cwd=workspace,
            on_start=on_start,
        )

        # why：pytest 退出码 1=有用例失败（junit 已含结果，任务仍算执行完成）；
        # 其余非零（2 中断/3 内部/4 usage/5 收集失败）表示 pytest 自身异常，产出的 junit 可能缺测试
        # （如收集错误 → total=0），若判 SUCCESS 会误导为"全部通过"——按异常退出处理。
        if result.returncode not in (0, 1):
            tail = (result.stdout or "")[-2000:] or (result.stderr or "")[-2000:]
            raise AppError(
                "PYTEST_EXIT_CODE",
                status_code=502,
                detail=f"pytest 异常退出码 {result.returncode}: {tail}",
            )

        try:
            summary, entries = parse_junit_xml(workspace / "report.xml")
        except AppError as e:
            # 损坏的 junit 不能拖垮终态：把 stdout 尾部交给编排层落 error_msg（可排障）
            raise AppError(
                "JUNIT_PARSE_FAILED",
                status_code=502,
                detail=(result.stdout or "")[-2000:] or str(e.detail),
            ) from e
        return ExecutionResult(summary=summary, entries=entries)

    def _build_workspace(self, task_id: int, cases: list[TestCase]) -> Path:
        # why：绝对路径——pytest 的 cwd 就是 workspace，相对路径会被再次拼接导致翻倍；
        # 先清空再重建，避免复用 task_id 时残留旧 test 文件 / report.xml（真实正确性 + 测试隔离）
        workspace = Path(self._settings.execution.workspace_dir).resolve() / "tasks" / str(task_id)
        shutil.rmtree(workspace, ignore_errors=True)
        workspace.mkdir(parents=True, exist_ok=True)
        if self._settings.execution.auth_enabled:
            # why：登录 fixture 随任务 workspace 生成——session 级登录取 token，用例文件只引用 fixture
            (workspace / "conftest.py").write_text(render_conftest(), encoding="utf-8")
        for case in cases:
            (workspace / f"test_{case.id}.py").write_text(render_test_file(case), encoding="utf-8")
        return workspace
