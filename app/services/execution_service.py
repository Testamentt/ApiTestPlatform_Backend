# 执行编排。why：短事务分界（读→commit→算→写），禁止持有 Session 期间跑 subprocess；
# 结果落 tasks.result_summary（无独立结果表）；超时/解析失败降级保证任务进入明确终态。
from __future__ import annotations

import logging
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models.enums import CaseStatus, TaskStatus
from app.models.task import Task
from app.models.test_case import TestCase
from app.utils.case_generator import render_test_file
from app.utils.junit_parser import parse_junit_xml
from app.utils.report_util import write_report_html
from app.utils.subprocess_util import run_cmd


def _utcnow() -> datetime:
    # why：时间统一存 UTC naive（对齐 RULES.md §5.2）。
    return datetime.now(UTC).replace(tzinfo=None)


class ExecutionService:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory
        self.logger = logging.getLogger(__name__)

    def execute_cases(self, task_id: int) -> None:
        # ① 读任务与 active 用例（短事务，读完即释放连接）
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.status != TaskStatus.PENDING:
                self.logger.info("task %s 跳过（不存在或非 pending）", task_id)
                return
            cases = list(
                session.scalars(
                    select(TestCase).where(
                        TestCase.id.in_(task.case_ids),
                        TestCase.status == CaseStatus.ACTIVE,
                    )
                )
            )
        # ② 建按 task_id 隔离的 workspace + 生成测试文件（防多任务互相覆盖 report.xml）
        workspace = self._build_workspace(task_id, cases)
        test_files = sorted(workspace.glob("test_*.py"))
        if not test_files:
            self._fail(task_id, "parse", "没有可执行的 active 用例")
            return

        # ③ 运行 pytest；on_start 立即回传 pid 写 running（权威超时劫持依据）
        def _mark_running(pid: int) -> None:
            with self.session_factory() as s:
                t = s.get(Task, task_id)
                if t is None:
                    return
                t.pid = pid
                t.status = TaskStatus.RUNNING.value
                t.started_at = _utcnow()
                s.commit()

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
        try:
            result = run_cmd(
                cmd,
                timeout=get_settings().execution.pytest_timeout,
                check=False,
                cwd=workspace,
                on_start=_mark_running,
            )
        except AppError as e:
            # run_cmd 内已 best-effort 杀树；权威兜底是 scan_stale_tasks
            self._fail(task_id, "timeout", f"subprocess 超时: {e.detail}")
            return

        # ④ 解析 junit；损坏时降级为 stdout 文本（保证任务有明确终态）
        try:
            summary, entries = parse_junit_xml(workspace / "report.xml")
        except AppError as e:
            self._fail(task_id, "parse", result.stdout[-2000:] or str(e.detail))
            return

        # ⑤ 组 result_summary + 写 HTML 报告 + success
        result_summary = {
            "total": summary.total,
            "passed": summary.passed,
            "failed": summary.failed,
            "skipped": summary.skipped,
            "duration_ms": summary.duration_ms,
            "results": entries,
        }
        self._finish_success(task_id, result_summary)

    def _build_workspace(self, task_id: int, cases: list[TestCase]) -> Path:
        # why：绝对路径——pytest 的 cwd 就是 workspace，相对路径会被再次拼接导致翻倍；
        # 先清空再重建，避免复用 task_id 时残留旧 test 文件 / report.xml（真实正确性 + 测试隔离）
        workspace = (Path(get_settings().execution.workspace_dir).resolve() / "tasks" / str(task_id))
        shutil.rmtree(workspace, ignore_errors=True)
        workspace.mkdir(parents=True, exist_ok=True)
        for case in cases:
            (workspace / f"test_{case.id}.py").write_text(
                render_test_file(case), encoding="utf-8"
            )
        return workspace

    def _fail(self, task_id: int, stage: str, msg: str) -> None:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None:
                return
            task.status = TaskStatus.FAILED.value
            task.error_stage = stage
            task.error_msg = msg
            task.finished_at = _utcnow()
            session.commit()

    def _finish_success(self, task_id: int, result_summary: dict) -> None:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None:
                return
            report_link = None
            try:
                # 报告生成 best-effort：即使失败也不影响任务状态
                report_dir = (
                    Path(get_settings().execution.workspace_dir).resolve()
                    / "reports"
                    / str(task_id)
                )
                write_report_html(task, result_summary, report_dir)
                report_link = f"/static/reports/{task_id}/report.html"
            except Exception:
                self.logger.exception("生成 HTML 报告失败（best-effort）")
            task.result_summary = result_summary
            task.report_link = report_link
            task.status = TaskStatus.SUCCESS.value
            task.finished_at = _utcnow()
            session.commit()
