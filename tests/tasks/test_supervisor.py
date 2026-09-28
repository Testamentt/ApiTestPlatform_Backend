# 超时劫持测试（eager）：超时→failed/timeout + 杀树；未超时保持 running；pid 缺失跳过杀树；
# 生成任务同样扫描（review H3）。
from __future__ import annotations

from datetime import UTC, datetime

from app.models.enums import GenerationStatus, TaskStatus
from app.models.generation_task import GenerationTask
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


def _seed_running_generation(session_factory, *, started_at):
    factory = session_factory
    with factory() as s:
        task = GenerationTask(
            run_id="run_gen_super",
            document={"openapi": "3.0.3", "paths": {}},
            status=GenerationStatus.RUNNING.value,
            started_at=started_at,
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
    monkeypatch.setattr("app.tasks.supervisor.kill_process_tree", lambda pid: killed.append(pid))

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


def test_scan_marks_stale_generation_task_failed(session_factory, monkeypatch):
    # why：Worker 被强杀后生成任务卡 RUNNING 会永久污染 run_id，必须一并迁移（review H3）
    gen_id = _seed_running_generation(session_factory, started_at=datetime(2020, 1, 1))
    monkeypatch.setattr("app.tasks.supervisor.SessionLocal", session_factory)

    count = scan_stale_tasks.delay().get()
    assert count == 1
    with session_factory() as s:
        task = s.get(GenerationTask, gen_id)
        assert task.status == "failed"
        assert task.error_stage == "timeout"
        assert task.finished_at is not None


def test_scan_keeps_recent_generation_running(session_factory, monkeypatch):
    gen_id = _seed_running_generation(
        session_factory, started_at=datetime.now(UTC).replace(tzinfo=None)
    )
    monkeypatch.setattr("app.tasks.supervisor.SessionLocal", session_factory)

    count = scan_stale_tasks.delay().get()
    assert count == 0
    with session_factory() as s:
        task = s.get(GenerationTask, gen_id)
        assert task.status == "running"


# ---------- PENDING 孤儿回收（review N4）----------


def _seed_pending(session_factory) -> int:
    with session_factory() as s:
        task = Task(run_id="run_pending_orphan", case_ids=[1], status=TaskStatus.PENDING.value)
        s.add(task)
        s.commit()
        s.refresh(task)
        return task.id


def _age_pending(session_factory, task_id: int, *, seconds_ago: int, celery_id: str = "cid-x"):
    """把 pending 行的 updated_at 拨回过去——回收按 updated_at 计时（重试会刷新它）。"""
    from datetime import timedelta

    with session_factory() as s:
        t = s.get(Task, task_id)
        t.updated_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=seconds_ago)
        t.celery_task_id = celery_id
        s.commit()


def test_scan_reclaims_stale_pending_orphan(session_factory, monkeypatch):
    # why：消息入队却无人领取会永久 pending 且 run_id 被永久复用——必须能回收（review N4）
    task_id = _seed_pending(session_factory)
    _age_pending(session_factory, task_id, seconds_ago=10_000)
    revoked: list[str | None] = []
    monkeypatch.setattr("app.tasks.supervisor.SessionLocal", session_factory)
    monkeypatch.setattr("app.tasks.supervisor.revoke_task", lambda cid: revoked.append(cid) or True)

    assert scan_stale_tasks.delay().get() == 1
    with session_factory() as s:
        t = s.get(Task, task_id)
        assert t.status == "failed"
        assert t.error_stage == "pending_timeout"
        assert t.finished_at is not None
    assert revoked == ["cid-x"]  # broker 里的消息一并撤销，避免 Worker 领取后空跑


def test_scan_keeps_recent_pending(session_factory, monkeypatch):
    task_id = _seed_pending(session_factory)
    monkeypatch.setattr("app.tasks.supervisor.SessionLocal", session_factory)
    monkeypatch.setattr("app.tasks.supervisor.revoke_task", lambda cid: True)

    assert scan_stale_tasks.delay().get() == 0
    with session_factory() as s:
        assert s.get(Task, task_id).status == "pending"


def test_retry_refreshes_updated_at_so_reaper_spares_it(session_factory, monkeypatch):
    # why：重试会把老行重置为 pending——若回收按 created_at 计时，重试后会被立刻误杀（review N4）
    from app.services.task_service import TaskService

    task_id = _seed_pending(session_factory)
    _age_pending(session_factory, task_id, seconds_ago=10_000)
    monkeypatch.setattr("app.services.task_service.dispatch_execution", lambda *a, **kw: None)
    with session_factory() as s:
        task = s.get(Task, task_id)
        task.status = TaskStatus.FAILED.value
        s.commit()
        TaskService(s).retry_task(task_id)

    monkeypatch.setattr("app.tasks.supervisor.SessionLocal", session_factory)
    monkeypatch.setattr("app.tasks.supervisor.revoke_task", lambda cid: True)
    assert scan_stale_tasks.delay().get() == 0
    with session_factory() as s:
        assert s.get(Task, task_id).status == "pending"
