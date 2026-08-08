# 超时劫持测试（eager）：超时→failed/timeout + 杀树；未超时保持 running；pid 缺失跳过杀树。
from __future__ import annotations

from datetime import UTC, datetime

from app.models.enums import TaskStatus
from app.models.task import Task
from app.tasks.supervisor import scan_stale_tasks


def _seed_running(session_factory, *, started_at, pid=1234):
    factory = session_factory
    with factory() as s:
        task = Task(
            run_id="run_super",
            case_ids=[1],
            status=TaskStatus.RUNNING.value,
            started_at=started_at,
            pid=pid,
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        task_id = task.id
    return task_id


def test_scan_marks_stale_failed_and_kills(session_factory, monkeypatch):
    # why：真实时钟 now 与固定过去时间比较，必然超时，可重复
    task_id = _seed_running(session_factory, started_at=datetime(2020, 1, 1))
    killed = []

    monkeypatch.setattr("app.tasks.supervisor.SessionLocal", session_factory)
    monkeypatch.setattr(
        "app.tasks.supervisor.kill_process_tree", lambda pid: killed.append(pid)
    )

    count = scan_stale_tasks.delay().get()
    assert count == 1
    assert killed == [1234]  # 读 DB 写入的 pid 杀整棵树
    with session_factory() as s:
        task = s.get(Task, task_id)
        assert task.status == "failed"
        assert task.error_stage == "timeout"
        assert task.finished_at is not None


def test_scan_keeps_recent_running(session_factory, monkeypatch):
    # 刚启动的任务未超时：不迁移、不杀
    task_id = _seed_running(session_factory, started_at=datetime.now(UTC).replace(tzinfo=None))
    monkeypatch.setattr("app.tasks.supervisor.SessionLocal", session_factory)
    monkeypatch.setattr("app.tasks.supervisor.kill_process_tree", lambda pid: None)

    count = scan_stale_tasks.delay().get()
    assert count == 0
    with session_factory() as s:
        task = s.get(Task, task_id)
        assert task.status == "running"
        assert task.error_stage is None


def test_scan_without_pid_still_fails(session_factory, monkeypatch):
    # pid 缺失：跳过杀树但任务仍进 failed（终态保证）
    task_id = _seed_running(session_factory, started_at=datetime(2020, 1, 1), pid=None)
    monkeypatch.setattr("app.tasks.supervisor.SessionLocal", session_factory)
    monkeypatch.setattr("app.tasks.supervisor.kill_process_tree", lambda pid: None)

    count = scan_stale_tasks.delay().get()
    assert count == 1
    with session_factory() as s:
        task = s.get(Task, task_id)
        assert task.status == "failed"
        assert task.error_stage == "timeout"


def test_scan_empty_returns_zero(session_factory, monkeypatch):
    monkeypatch.setattr("app.tasks.supervisor.SessionLocal", session_factory)
    assert scan_stale_tasks.delay().get() == 0
