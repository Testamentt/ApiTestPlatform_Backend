# 执行编排。why：短事务分界（读→commit→算→写），禁止持有 Session 期间跑 subprocess；
# 结果落 tasks.result_summary（无独立结果表）；超时/解析失败降级保证任务进入明确终态。
from __future__ import annotations

import logging
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from celery.exceptions import (
    SoftTimeLimitExceeded,  # 服务层须识别软超时以放行（R3-1），异常类型耦合可接受
)
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models.enums import CaseStatus, TaskStatus
from app.models.task import Task
from app.models.test_case import TestCase
from app.utils.case_generator import render_conftest, render_test_file
from app.utils.junit_parser import parse_junit_xml
from app.utils.report_util import report_path, write_report_html
from app.utils.subprocess_util import kill_process_tree, run_cmd


def _utcnow() -> datetime:
    # why：时间统一存 UTC naive（对齐 RULES.md §5.2）。
    return datetime.now(UTC).replace(tzinfo=None)


class ExecutionService:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory
        self.logger = logging.getLogger(__name__)

    def execute_cases(self, task_id: int) -> None:
        """执行编排入口。why：兜底任何未预期异常，保证任务进入明确终态（不卡 PENDING/RUNNING）。"""
        try:
            self._execute_cases(task_id)
        except SoftTimeLimitExceeded:
            # 放行给任务层专属 handler（force_fail_timeout 落 timeout 并杀树）——
            # 被 except Exception 截胡会让任务层 handler 成死代码且子进程孤儿化（review R3-1）
            raise
        except Exception:
            self.logger.exception("execute_cases 任务 %s 未预期异常", task_id)
            self._fail(task_id, "internal", "执行引擎未预期异常，详见服务日志")

    def _execute_cases(self, task_id: int) -> None:
        # ① 读任务与 active 用例（短事务，读完即释放连接）
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.status != TaskStatus.PENDING:
                self.logger.info("task %s 跳过（不存在或非 pending）", task_id)
                return
            task_timeout = task.timeout_seconds or get_settings().execution.pytest_timeout
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

        # ③ 运行 pytest；on_start 条件更新为 running（权威超时劫持依据 + 取消竞态守卫）
        def _mark_running(pid: int) -> None:
            with self.session_factory() as s:
                # why：条件更新（review C2）——无条件覆盖会把"刚被取消的任务"重新拉回 RUNNING；
                # 影响行数 0 说明已非 pending（被取消/已被回收），必须放弃本次执行并杀掉已启动的子进程
                result = s.execute(
                    update(Task)
                    .where(Task.id == task_id, Task.status == TaskStatus.PENDING.value)
                    .values(status=TaskStatus.RUNNING.value, pid=pid, started_at=_utcnow())
                    .execution_options(synchronize_session=False)
                )
                try:
                    s.commit()
                except Exception:
                    s.rollback()
                    raise
                if result.rowcount == 0:
                    raise AppError(
                        "TASK_CANCELLED",
                        status_code=409,
                        detail=f"任务 {task_id} 已非 pending（可能被取消），放弃执行",
                    )

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
                timeout=task_timeout,
                check=False,
                cwd=workspace,
                on_start=_mark_running,
            )
        except AppError as e:
            # 按错误码分类落 error_stage（review L7）：白名单拒绝 ≠ 超时，
            # 混为一谈会让排障误入 timeout 诊断路径；run_cmd 内已 best-effort 杀树，
            # 权威兜底是 scan_stale_tasks
            if e.code in ("COMMAND_NOT_ALLOWED", "INVALID_ARGS"):
                self._fail(task_id, "command", f"命令未通过白名单校验: {e.detail}")
            elif e.code == "SUBPROCESS_TIMEOUT":
                self._fail(task_id, "timeout", f"subprocess 超时: {e.detail}")
            elif e.code == "TASK_CANCELLED":
                # why：取消已写好终态（failed/cancelled）——执行层不得再写状态，否则覆盖掉取消语义（review C2）
                self.logger.info("task %s 启动瞬间已被取消，放弃执行（终态由取消方写入）", task_id)
            else:
                self._fail(task_id, "subprocess", f"subprocess 失败: {e.detail}")
            return

        # ④ returncode 终态检查。why：pytest 退出码 1=有用例失败（junit 已含结果，任务仍算执行完成）；
        # 其余非零（2 中断/3 内部/4 usage/5 收集失败）表示 pytest 自身异常，产出的 junit 可能缺测试
        # （如收集错误 → total=0），若判 SUCCESS 会误导为"全部通过"——置 FAILED(subprocess)。
        if result.returncode not in (0, 1):
            tail = (result.stdout or "")[-2000:] or (result.stderr or "")[-2000:]
            self._fail(task_id, "subprocess", f"pytest 异常退出码 {result.returncode}: {tail}")
            return

        # ⑤ 解析 junit；损坏时降级为 stdout 文本（保证任务有明确终态）
        try:
            summary, entries = parse_junit_xml(workspace / "report.xml")
        except AppError as e:
            self._fail(task_id, "parse", (result.stdout or "")[-2000:] or str(e.detail))
            return

        # ⑥ 组 result_summary + 写 HTML 报告 + success
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
        workspace = Path(get_settings().execution.workspace_dir).resolve() / "tasks" / str(task_id)
        shutil.rmtree(workspace, ignore_errors=True)
        workspace.mkdir(parents=True, exist_ok=True)
        if get_settings().execution.auth_enabled:
            # why：登录 fixture 随任务 workspace 生成——session 级登录取 token，用例文件只引用 fixture
            (workspace / "conftest.py").write_text(render_conftest(), encoding="utf-8")
        for case in cases:
            (workspace / f"test_{case.id}.py").write_text(render_test_file(case), encoding="utf-8")
        return workspace

    def _fail(self, task_id: int, stage: str, msg: str) -> None:
        """写失败终态。why：条件更新（仅 pending/running 生效）——人工取消会先把任务置成
        `failed(cancelled)`，无条件覆盖会把它改写成 subprocess/timeout 这类「看起来像执行失败」的阶段，
        取消语义丢失（P2-3，与 C2 的取消竞态同源）。"""
        with self.session_factory() as session:
            result = session.execute(
                update(Task)
                .where(
                    Task.id == task_id,
                    Task.status.in_((TaskStatus.PENDING.value, TaskStatus.RUNNING.value)),
                )
                .values(
                    status=TaskStatus.FAILED.value,
                    error_stage=stage,
                    error_msg=msg,
                    finished_at=_utcnow(),
                )
                .execution_options(synchronize_session=False)
            )
            try:
                session.commit()
            except Exception:
                session.rollback()
                raise
            if result.rowcount == 0:
                self.logger.info(
                    "task %s 已是终态（可能被取消），跳过失败回写 stage=%s", task_id, stage
                )

    def force_fail_timeout(self, task_id: int) -> None:
        """软超时兜底：置终态并 best-effort 杀 pytest 进程树。
        why：Celery 软超时中断任务函数后 DB 状态可能停留在 running/pending，
        必须迁移 failed，否则要等下次 Worker 启动的扫描兜底（RULES §8.2）。
        同样条件更新——已被取消的任务不被软超时覆盖（P2-3）。"""
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None:
                return
            if task.pid:
                kill_process_tree(task.pid)
            result = session.execute(
                update(Task)
                .where(
                    Task.id == task_id,
                    Task.status.in_((TaskStatus.PENDING.value, TaskStatus.RUNNING.value)),
                )
                .values(
                    status=TaskStatus.FAILED.value,
                    error_stage="timeout",
                    error_msg=f"Celery 软超时（{get_settings().celery.soft_time_limit}s）",
                    finished_at=_utcnow(),
                )
                .execution_options(synchronize_session=False)
            )
            try:
                session.commit()
            except Exception:
                session.rollback()
                raise
            if result.rowcount == 0:
                self.logger.info("task %s 已是终态（可能被取消），跳过软超时回写", task_id)

    def _finish_success(self, task_id: int, result_summary: dict) -> None:
        """写成功终态 + 报告链接。why：先做条件更新（仅 running→success）再在同一事务里落报告——
        已被取消的任务不会被「成功」覆盖（P2-3），报告仍保持 best-effort（失败不影响状态）。"""
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None:
                return
            result = session.execute(
                update(Task)
                .where(Task.id == task_id, Task.status == TaskStatus.RUNNING.value)
                .values(
                    status=TaskStatus.SUCCESS.value,
                    result_summary=result_summary,
                    finished_at=_utcnow(),
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 0:
                session.rollback()
                self.logger.info("task %s 已非 running（可能被取消），跳过成功回写", task_id)
                return
            task.status = TaskStatus.SUCCESS.value  # 报告页显示的终态（同事务内提交）
            try:
                # why：report_link 指向带鉴权的 API 端点而非 /static——报告含用例名与失败信息，
                # 无鉴权挂载会随 task_id 自增被枚举（review N3）
                write_report_html(task, result_summary, report_path(task_id).parent)
                task.report_link = f"/api/v1/tasks/{task_id}/report"
            except Exception:
                self.logger.exception("生成 HTML 报告失败（best-effort）")
            try:
                session.commit()
            except Exception:
                session.rollback()
                raise
