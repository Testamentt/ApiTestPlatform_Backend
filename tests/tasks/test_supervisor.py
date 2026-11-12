# 本项目编码格式：UTF-8（无 BOM）。
# 超时劫持测试（eager）：超时→failed/timeout + 杀树；未超时保持 running；pid 缺失跳过杀树；
# 生成任务同样扫描；PENDING 孤儿按「队列位置 + 前序任务超时」放宽（solo 串行不误杀）。
from __future__ import annotations

from datetime import UTC, datetime

from app.models.enums import GenerationStatus, TaskStatus
from app.models.generation_task import GenerationTask
from app.models.task import Task
from app.tasks.supervisor import pending_allowance_seconds, scan_stale_tasks


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
    # why：Worker 被强杀后生成任务卡 RUNNING 会永久污染 run_id，必须一并迁移
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


# ---------- PENDING 孤儿回收 ----------


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
    # why：消息入队却无人领取会永久 pending 且 run_id 被永久复用——必须能回收
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
    # why：重试会把老行重置为 pending——若回收按 created_at 计时，重试后会被立刻误杀
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


# ---------- 队列感知：solo 串行下排队不是孤儿 ----------


def test_pending_allowance_grows_with_tasks_ahead():
    """why：`pending_timeout` 判的是「没人领取」，不是「排队太久」——前面任务要占满自己的执行时间。

    回归口径：solo 单 Worker 串行，第三个 300s 任务正常也要等 ~900s，全局 600s 会把它误杀。
    """
    assert pending_allowance_seconds(base_timeout=600, ahead_timeouts=[]) == 600
    assert pending_allowance_seconds(base_timeout=600, ahead_timeouts=[300]) == 900
    # 队列第三个位置（前面两个各 300s）→ 600 + 600 = 1200s > 全局 600s
    assert pending_allowance_seconds(base_timeout=600, ahead_timeouts=[300, 300]) == 1200


def test_pending_allowance_ignores_task_timeout_when_zero():
    """零/None 超时（历史脏行）不能把宽限算小，也不能算成负数。"""
    assert pending_allowance_seconds(base_timeout=600, ahead_timeouts=[0, None]) == 600
    assert pending_allowance_seconds(base_timeout=0, ahead_timeouts=[]) == 0


def _seed_pending_with_timeout(session_factory, *, run_id: str, timeout: int) -> int:
    with session_factory() as s:
        task = Task(
            run_id=run_id,
            case_ids=[1],
            status=TaskStatus.PENDING.value,
            timeout_seconds=timeout,
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        return task.id


def _seed_running_with_timeout(session_factory, *, run_id: str, timeout: int) -> int:
    with session_factory() as s:
        task = Task(
            run_id=run_id,
            case_ids=[1],
            status=TaskStatus.RUNNING.value,
            timeout_seconds=timeout,
            started_at=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        return task.id


def test_reaper_spares_queued_task_that_global_timeout_would_kill(session_factory, monkeypatch):
    """核心回归：前面排着两个 300s 任务时，第三个任务等待 700s（> 全局 600s）不该被判孤儿。

    why：这正是被测的线上现象——solo 串行下第三个 300s 任务被误杀成 failed(pending_timeout)，
    而 failed 只能人工重试，把正常排队变成了故障。
    """
    _seed_running_with_timeout(session_factory, run_id="run_q1", timeout=300)
    _seed_running_with_timeout(session_factory, run_id="run_q2", timeout=300)
    third = _seed_pending_with_timeout(session_factory, run_id="run_q3", timeout=300)
    _age_pending(session_factory, third, seconds_ago=700)  # > 600 但 < 600+300+300

    monkeypatch.setattr("app.tasks.supervisor.SessionLocal", session_factory)
    monkeypatch.setattr("app.tasks.supervisor.revoke_task", lambda cid: True)

    assert scan_stale_tasks.delay().get() == 0
    with session_factory() as s:
        assert s.get(Task, third).status == "pending"


def test_reaper_still_reclaims_truly_orphaned_queue_task(session_factory, monkeypatch):
    """放宽后仍要能回收真孤儿：等待超过「基础宽限 + 前序超时预算」照样 failed。

    why：队列感知不能被写成「永不回收」——那等于把 pending 孤儿问题原样留回去。
    """
    _seed_running_with_timeout(session_factory, run_id="run_o1", timeout=300)
    _seed_running_with_timeout(session_factory, run_id="run_o2", timeout=300)
    third = _seed_pending_with_timeout(session_factory, run_id="run_o3", timeout=300)
    _age_pending(session_factory, third, seconds_ago=1300)  # > 600+300+300

    revoked: list[str | None] = []
    monkeypatch.setattr("app.tasks.supervisor.SessionLocal", session_factory)
    monkeypatch.setattr("app.tasks.supervisor.revoke_task", lambda cid: revoked.append(cid) or True)

    assert scan_stale_tasks.delay().get() == 1
    with session_factory() as s:
        t = s.get(Task, third)
        assert t.status == "failed"
        assert t.error_stage == "pending_timeout"
        assert "1200" in t.error_msg  # 消息里写清实际宽限，排障时不用猜口径
    assert revoked == ["cid-x"]


def test_reaper_limits_allowance_to_queue_position(session_factory, monkeypatch):
    """FIFO：只有排在后面的任务被放宽，队首任务仍按基础宽限判（否则孤儿永远等不到回收）。"""
    head = _seed_pending_with_timeout(session_factory, run_id="run_head", timeout=300)
    _age_pending(session_factory, head, seconds_ago=700)  # 队首：600 就够判孤儿
    tail = _seed_pending_with_timeout(session_factory, run_id="run_tail", timeout=300)
    _age_pending(session_factory, tail, seconds_ago=700)

    monkeypatch.setattr("app.tasks.supervisor.SessionLocal", session_factory)
    monkeypatch.setattr("app.tasks.supervisor.revoke_task", lambda cid: True)

    # 队首超 600 → 回收；队尾宽限 = 600 + 队首300 = 900 > 700 → 保留
    assert scan_stale_tasks.delay().get() == 1
    with session_factory() as s:
        assert s.get(Task, head).status == "failed"
        assert s.get(Task, tail).status == "pending"


# ---------- Beat 周期回收（不再只在启动扫一次）----------


def test_beat_schedule_registers_periodic_scan():
    """why：僵尸回收此前只在 Worker 启动时扫一次——Worker 长期不重启时任务卡到下次人工重启。

    Beat 里必须有 scan_stale_tasks 的周期项，且间隔来自配置（禁止硬编码节拍）。
    """
    from app.celery_app import beat_schedule
    from app.core.config import get_settings

    settings = get_settings()
    schedule = beat_schedule(settings)
    entry = schedule["scan-stale-tasks"]
    assert entry["task"] == "scan_stale_tasks"
    assert entry["schedule"] == float(settings.celery.stale_scan_interval)
    # why：expires 防堆叠——上一次扫描没跑完时，过期消息应被丢弃而不是排队
    assert entry["options"]["expires"] == float(settings.celery.stale_scan_interval)


def test_celery_conf_wires_beat_schedule():
    """配置必须真的进 Celery conf——只写函数不接线等于没开 Beat。"""
    from app.celery_app import celery_app

    assert "scan-stale-tasks" in celery_app.conf.beat_schedule


def test_zero_interval_disables_periodic_scan(monkeypatch):
    """`stale_scan_interval=0` 关闭周期扫描（兼容无 Beat 的部署形态），退化为启动扫描。"""
    from app.celery_app import beat_schedule
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings.celery, "stale_scan_interval", 0)
    assert beat_schedule(settings) == {}
