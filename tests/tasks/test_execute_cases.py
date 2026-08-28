# Celery 任务测试（eager）：成功写结果 / 超时→failed / 解析失败降级。
from __future__ import annotations

from app.core.exceptions import AppError
from app.models.enums import TaskStatus
from app.models.task import Task
from app.models.test_case import TestCase
from app.tasks.execute_cases import execute_cases_task


def _seed_task(session_factory, *, case_status="active"):
    factory = session_factory
    with factory() as s:
        case = TestCase(
            name="c",
            operation_id="op",
            method="GET",
            path="/get",
            expected_status=200,
            status=case_status,
        )
        s.add(case)
        s.commit()
        s.refresh(case)
        task = Task(run_id=f"run_{case.id}", case_ids=[case.id], status=TaskStatus.PENDING.value)
        s.add(task)
        s.commit()
        s.refresh(task)
        case_id, task_id = case.id, task.id
    return case_id, task_id


def test_execute_success(session_factory, patch_sessionlocal, fake_execution):
    case_id, task_id = _seed_task(session_factory)
    execute_cases_task.delay(task_id)
    with session_factory() as s:
        task = s.get(Task, task_id)
        assert task.status == "success"
        assert task.result_summary["passed"] == 1
        assert task.report_link is not None
        assert task.pid == 9999  # 来自 fake run_cmd 的 on_start


def test_execute_timeout_marks_failed(session_factory, patch_sessionlocal, monkeypatch):
    from ..fakes import make_fake_run_cmd

    case_id, task_id = _seed_task(session_factory)
    monkeypatch.setattr(
        "app.services.execution_service.run_cmd",
        make_fake_run_cmd(exception=AppError("SUBPROCESS_TIMEOUT", status_code=502)),
    )
    execute_cases_task.delay(task_id)
    with session_factory() as s:
        task = s.get(Task, task_id)
        assert task.status == "failed"
        assert task.error_stage == "timeout"


def test_execute_command_not_allowed_stage(session_factory, patch_sessionlocal, monkeypatch):
    # review L7：白名单拒绝 ≠ 超时——错误分类必须落 command，否则排障误入 timeout 诊断路径
    from ..fakes import make_fake_run_cmd

    case_id, task_id = _seed_task(session_factory)
    monkeypatch.setattr(
        "app.services.execution_service.run_cmd",
        make_fake_run_cmd(exception=AppError("COMMAND_NOT_ALLOWED", status_code=502)),
    )
    execute_cases_task.delay(task_id)
    with session_factory() as s:
        task = s.get(Task, task_id)
        assert task.status == "failed"
        assert task.error_stage == "command"


def test_execute_parse_failure_falls_back(session_factory, patch_sessionlocal, monkeypatch):
    from ..fakes import make_fake_run_cmd

    case_id, task_id = _seed_task(session_factory)
    monkeypatch.setattr(
        "app.services.execution_service.run_cmd",
        make_fake_run_cmd(stdout="pytest exploded", junit_xml=None),  # 不写 report.xml
    )
    execute_cases_task.delay(task_id)
    with session_factory() as s:
        task = s.get(Task, task_id)
        assert task.status == "failed"
        assert task.error_stage == "parse"
        assert "pytest exploded" in task.error_msg


def test_execute_collection_error_fails_on_returncode(
    session_factory, patch_sessionlocal, monkeypatch
):
    # pytest 收集失败（syntax error）退出码 5：即使 junit 缺失也须置 failed，不得误判 SUCCESS
    from ..fakes import make_fake_run_cmd

    case_id, task_id = _seed_task(session_factory)
    monkeypatch.setattr(
        "app.services.execution_service.run_cmd",
        make_fake_run_cmd(returncode=5, stdout="ERROR: file not found"),
    )
    execute_cases_task.delay(task_id)
    with session_factory() as s:
        task = s.get(Task, task_id)
        assert task.status == "failed"
        assert task.error_stage == "subprocess"
        assert "退出码 5" in task.error_msg


def test_execute_returncode_1_is_success_when_junit_valid(
    session_factory, patch_sessionlocal, monkeypatch
):
    # pytest 退出码 1 = 有用例失败：junit 已含结果，任务仍算执行完成（SUCCESS + failed 计数）
    from ..fakes import JUNIT_FAIL, make_fake_run_cmd

    case_id, task_id = _seed_task(session_factory)
    monkeypatch.setattr(
        "app.services.execution_service.run_cmd",
        make_fake_run_cmd(returncode=1, junit_xml=JUNIT_FAIL),
    )
    execute_cases_task.delay(task_id)
    with session_factory() as s:
        task = s.get(Task, task_id)
        assert task.status == "success"
        assert task.result_summary["failed"] == 1


def test_draft_case_excluded(session_factory, patch_sessionlocal, monkeypatch):
    from ..fakes import make_fake_run_cmd

    case_id, task_id = _seed_task(session_factory, case_status="draft")
    monkeypatch.setattr(
        "app.services.execution_service.run_cmd",
        make_fake_run_cmd(),  # 即使被调用也不写 report.xml
    )
    execute_cases_task.delay(task_id)
    with session_factory() as s:
        task = s.get(Task, task_id)
        # 无 active 用例 → 不跑 pytest，直接 failed(parse)
        assert task.status == "failed"
        assert task.error_stage == "parse"
