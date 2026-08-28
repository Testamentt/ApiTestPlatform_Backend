# 派发器透传测试：request_id 随任务参数跨进程传递（§6.2，review 批次 C）。
from __future__ import annotations

from app.middleware.request_id import reset_request_id, set_request_id


def test_dispatch_execution_forwards_request_id(monkeypatch):
    import app.tasks.execute_cases as ec
    from app.services.dispatcher import dispatch_execution

    captured: dict = {}

    def fake_delay(task_id, **kwargs):
        captured["task_id"] = task_id
        captured["request_id"] = kwargs.get("request_id")
        return type("_R", (), {"id": "celery-1"})()

    monkeypatch.setattr(ec.execute_cases_task, "delay", fake_delay)
    try:
        set_request_id("rid-dispatch-1")
        dispatch_execution(42)
    finally:
        reset_request_id()
    assert captured == {"task_id": 42, "request_id": "rid-dispatch-1"}  # 请求上下文的 id 透传 Worker


def test_dispatch_generation_forwards_request_id(monkeypatch):
    import app.tasks.generate_cases as gc
    from app.services.dispatcher import dispatch_generation

    captured: dict = {}

    def fake_delay(task_id, **kwargs):
        captured["task_id"] = task_id
        captured["request_id"] = kwargs.get("request_id")
        return type("_R", (), {"id": "celery-2"})()

    monkeypatch.setattr(gc.generate_cases_task, "delay", fake_delay)
    try:
        set_request_id("rid-dispatch-2")
        dispatch_generation(7)
    finally:
        reset_request_id()
    assert captured == {"task_id": 7, "request_id": "rid-dispatch-2"}


def test_task_signature_accepts_request_id(session_factory, patch_sessionlocal, fake_execution):
    # 任务第二参数 request_id 保持兼容（旧调用不带也 OK），并从上下文恢复
    from app.middleware.request_id import get_request_id, reset_request_id
    from app.tasks.execute_cases import execute_cases_task

    try:
        execute_cases_task(0, request_id="rid-task-3")  # task 0 不存在 → 直接跳过，但已 set
        assert get_request_id() == "rid-task-3"
    finally:
        reset_request_id()
