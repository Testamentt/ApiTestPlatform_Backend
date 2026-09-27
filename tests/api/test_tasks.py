# 任务接口测试：202 立即返回 / Lookup-Create 幂等 / draft 不可执行 / 结果查询 / 重试与入队兜底。
from __future__ import annotations

from pathlib import Path

import pytest
from app.core.exceptions import AppError
from app.utils.subprocess_util import CmdResult

from tests.fakes import JUNIT_OK, make_fake_run_cmd


@pytest.fixture()
def active_case_factory(client):
    def _make(name="get"):
        cid = client.post(
            "/api/v1/cases",
            json={"name": name, "operation_id": "op_" + name, "method": "GET", "path": "/get"},
        ).json()["data"]["id"]
        client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "demo"})
        return cid

    return _make


def test_create_task_202_and_execute(
    client, patch_sessionlocal, fake_execution, active_case_factory
):
    cid = active_case_factory()
    r = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    assert r.status_code == 202
    tid = r.json()["data"]["id"]
    # eager 已同步执行完毕
    t = client.get(f"/api/v1/tasks/{tid}").json()["data"]
    assert t["status"] == "success"
    assert t["result_summary"]["passed"] == 1
    assert t["report_link"] == f"/api/v1/tasks/{tid}/report"  # 带鉴权端点（review N3）


def test_lookup_create_returns_same_task(
    client, patch_sessionlocal, fake_execution, active_case_factory
):
    cid = active_case_factory()
    r1 = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    r2 = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    assert r1.json()["data"]["id"] == r2.json()["data"]["id"]


def test_different_input_new_task(client, patch_sessionlocal, fake_execution, active_case_factory):
    c1, c2 = active_case_factory("a"), active_case_factory("b")
    t1 = client.post("/api/v1/tasks", json={"case_ids": [c1]}).json()["data"]["id"]
    t2 = client.post("/api/v1/tasks", json={"case_ids": [c1, c2]}).json()["data"]["id"]
    assert t1 != t2


def test_non_active_case_rejected(client, patch_sessionlocal):
    cid = client.post(
        "/api/v1/cases",
        json={"name": "draft", "operation_id": "op_draft", "method": "GET", "path": "/get"},
    ).json()["data"]["id"]  # 未 confirm，保持 draft
    r = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    assert r.status_code == 422


def test_get_task_results(client, patch_sessionlocal, fake_execution, active_case_factory):
    cid = active_case_factory()
    tid = client.post("/api/v1/tasks", json={"case_ids": [cid]}).json()["data"]["id"]
    data = client.get(f"/api/v1/tasks/{tid}/results").json()["data"]
    assert data["task"]["status"] == "success"
    assert data["results"][0]["case_id"] == cid


def test_get_missing_task_404(client):
    assert client.get("/api/v1/tasks/99999").status_code == 404


def test_report_endpoint_returns_html(
    client, patch_sessionlocal, fake_execution, active_case_factory
):
    # why：报告改走带鉴权端点（review N3）——原 /static 静态挂载可被无凭证枚举
    cid = active_case_factory()
    tid = client.post("/api/v1/tasks", json={"case_ids": [cid]}).json()["data"]["id"]
    r = client.get(f"/api/v1/tasks/{tid}/report")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert f"任务 #{tid} 执行报告" in r.text


def test_report_endpoint_404_when_not_generated(
    client, patch_sessionlocal, active_case_factory, fake_execution, monkeypatch
):
    # 报告生成是 best-effort：写盘失败时 report_link 必须为 None，端点回 404 而非 500
    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("app.services.execution_service.write_report_html", _boom)
    cid = active_case_factory()
    tid = client.post("/api/v1/tasks", json={"case_ids": [cid]}).json()["data"]["id"]
    assert client.get(f"/api/v1/tasks/{tid}").json()["data"]["report_link"] is None
    r = client.get(f"/api/v1/tasks/{tid}/report")
    assert r.status_code == 404
    assert r.json()["code"] == "REPORT_NOT_FOUND"
    assert client.get("/api/v1/tasks/99999/report").json()["code"] == "NOT_FOUND"


def test_static_mount_removed(client):
    # 回归：无鉴权的 /static 报告面必须彻底关闭（不能再被枚举）
    assert client.get("/static/1/report.html").status_code == 404


def test_failed_task_retry_same_input(client, patch_sessionlocal, active_case_factory, monkeypatch):
    # why：失败任务同输入重试——重置 PENDING 重新入队（review H4），同一任务行复用
    cid = active_case_factory()
    monkeypatch.setattr(
        "app.services.execution_service.run_cmd",
        make_fake_run_cmd(exception=AppError("SUBPROCESS_TIMEOUT", status_code=502)),
    )
    r1 = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    assert r1.status_code == 202
    tid = r1.json()["data"]["id"]
    assert client.get(f"/api/v1/tasks/{tid}").json()["data"]["status"] == "failed"

    monkeypatch.setattr(
        "app.services.execution_service.run_cmd", make_fake_run_cmd(junit_xml=JUNIT_OK)
    )
    r2 = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    assert r2.status_code == 202
    assert r2.json()["data"]["id"] == tid
    t2 = client.get(f"/api/v1/tasks/{tid}").json()["data"]
    assert t2["status"] == "success"
    assert t2["error_stage"] is None  # 重试已清空失败信息


def test_dispatch_failure_marks_failed(
    client, patch_sessionlocal, active_case_factory, monkeypatch
):
    # why：Celery/Redis 不可用时入队失败——落 FAILED(dispatch) 而非 PENDING 孤儿（review M2）
    cid = active_case_factory()

    def _boom(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.services.task_service.dispatch_execution", _boom)
    r = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    assert r.status_code == 503
    assert r.json()["code"] == "DISPATCH_FAILED"
    page = client.get("/api/v1/tasks").json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["status"] == "failed"
    assert page["items"][0]["error_stage"] == "dispatch"


def test_task_timeout_controls_subprocess(
    client, patch_sessionlocal, active_case_factory, monkeypatch
):
    # why：timeout_seconds 必须真正控制 subprocess 超时（review M3/B4），而非仅参与幂等键
    captured = {}

    def _fake_run(args, timeout, *, check=True, cwd=None, on_start=None):
        captured["timeout"] = timeout
        if on_start is not None:
            on_start(9999)
        (Path(cwd) / "report.xml").write_text(JUNIT_OK, encoding="utf-8")
        return CmdResult(0, "", "", 100, 9999)

    monkeypatch.setattr("app.services.execution_service.run_cmd", _fake_run)
    cid = active_case_factory()
    tid = client.post("/api/v1/tasks", json={"case_ids": [cid], "timeout_seconds": 120}).json()[
        "data"
    ]["id"]
    assert captured["timeout"] == 120
    assert client.get(f"/api/v1/tasks/{tid}").json()["data"]["timeout_seconds"] == 120


def test_page_size_too_large_422(client):
    # why：分页上限（review M5）——page_size 越界拒绝，禁无上限全量返回
    r = client.get("/api/v1/tasks?page_size=1000")
    assert r.status_code == 422


def test_concurrent_same_input_returns_existing(
    client, patch_sessionlocal, fake_execution, active_case_factory, monkeypatch
):
    """C1 回归：并发同输入不得 500。

    why：把「两个请求都先查、都未命中」的 TOCTOU 时序固定下来——第二次 lookup 强制返回 None，
    于是第二个请求必然撞 UNIQUE(run_id)；正确行为是查回赢家返回同一任务，而非 500。
    """
    from app.repositories.task_repository import TaskRepository

    cid = active_case_factory()
    real_lookup = TaskRepository.find_by_run_id
    calls = {"n": 0}

    def stale_lookup(self, run_id):
        calls["n"] += 1
        if calls["n"] == 2:  # 第 2 个请求的首次 lookup：模拟读到并发前快照
            return None
        return real_lookup(self, run_id)

    monkeypatch.setattr(TaskRepository, "find_by_run_id", stale_lookup)
    r1 = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    r2 = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    assert (r1.status_code, r2.status_code) == (202, 202)
    assert r1.json()["data"]["id"] == r2.json()["data"]["id"]
    page = client.get("/api/v1/tasks").json()["data"]
    assert page["total"] == 1  # 唯一约束 + 查回赢家 = 1 条记录、0 个 500


# ---------- 取消 / 重试（review C2/N4）：PENDING 孤儿的出口 ----------


@pytest.fixture()
def pending_task(client, active_case_factory, monkeypatch):
    """造一个真 PENDING 任务：派发成功但不执行（不 mock run_cmd，任务根本没被领取）。"""
    monkeypatch.setattr("app.services.task_service.dispatch_execution", lambda *a, **kw: "cid-1")
    monkeypatch.setattr("app.services.task_service.revoke_task", lambda cid: True)

    def _make() -> int:
        cid = active_case_factory()
        return client.post("/api/v1/tasks", json={"case_ids": [cid]}).json()["data"]["id"]

    return _make


def test_cancel_pending_task(client, pending_task):
    tid = pending_task()
    r = client.post(f"/api/v1/tasks/{tid}/cancel")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "failed"
    assert data["error_stage"] == "cancelled"
    assert data["finished_at"] is not None


def test_cancel_non_pending_409(client, patch_sessionlocal, fake_execution, active_case_factory):
    # 已完成（success）的任务不可取消：终态是事实，只能重跑
    cid = active_case_factory()
    tid = client.post("/api/v1/tasks", json={"case_ids": [cid]}).json()["data"]["id"]
    r = client.post(f"/api/v1/tasks/{tid}/cancel")
    assert r.status_code == 409
    assert r.json()["code"] == "TASK_NOT_CANCELLABLE"


def test_cancel_missing_404(client):
    assert client.post("/api/v1/tasks/99999/cancel").status_code == 404


def test_retry_cancelled_task_runs(
    client, pending_task, patch_sessionlocal, fake_execution, monkeypatch
):
    # why：取消后必须能重跑（N4 的另一半）——否则 run_id 被 FAILED 行永久占死
    tid = pending_task()
    assert client.post(f"/api/v1/tasks/{tid}/cancel").json()["data"]["status"] == "failed"
    # 恢复真实派发（pending_task 期间为造 pending 态替换成了 no-op），让重试走 eager 真跑
    from app.services import task_service as ts
    from app.services.dispatcher import dispatch_execution as real_dispatch

    monkeypatch.setattr(ts, "dispatch_execution", real_dispatch)
    r = client.post(f"/api/v1/tasks/{tid}/retry")
    assert r.status_code == 202
    assert r.json()["data"]["id"] == tid
    # why：202 响应体是"刚重置并重新入队的快照"（pending 才是异步语义下的正确回执）；
    # eager 模式下执行已同步完成，故终态必须重新 GET 才看得到
    fresh = client.get(f"/api/v1/tasks/{tid}").json()["data"]
    assert fresh["status"] == "success"
    assert fresh["error_stage"] is None and fresh["report_link"] is not None


def test_retry_pending_409(client, pending_task):
    tid = pending_task()
    r = client.post(f"/api/v1/tasks/{tid}/retry")
    assert r.status_code == 409
    assert r.json()["code"] == "TASK_NOT_RETRYABLE"


# ---------- P2-3：RUNNING 任务可取消（先杀进程树，再落取消终态）----------


def test_cancel_running_task_kills_tree(client, session_factory, pending_task, monkeypatch):
    from app.models.enums import TaskStatus
    from app.models.task import Task

    tid = pending_task()
    with session_factory() as s:  # 模拟 Worker 已领取：running + 有 pid
        t = s.get(Task, tid)
        t.status = TaskStatus.RUNNING.value
        t.pid = 4321
        s.commit()

    killed: list[int] = []
    monkeypatch.setattr(
        "app.services.task_service.kill_process_tree", lambda pid: killed.append(pid)
    )

    r = client.post(f"/api/v1/tasks/{tid}/cancel")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "failed"
    assert data["error_stage"] == "cancelled"
    assert killed == [4321]  # 杀树先于状态写入


def test_cancel_running_without_pid_still_cancels(client, session_factory, pending_task):
    """pid 缺失（极端：mark_running 尚未落库）仍可取消——杀树跳过，但终态必须给。"""
    from app.models.enums import TaskStatus
    from app.models.task import Task

    tid = pending_task()
    with session_factory() as s:
        t = s.get(Task, tid)
        t.status = TaskStatus.RUNNING.value
        t.pid = None
        s.commit()

    r = client.post(f"/api/v1/tasks/{tid}/cancel")
    assert r.status_code == 200
    assert r.json()["data"]["error_stage"] == "cancelled"


def test_cancel_does_not_revoke_running_message(client, session_factory, pending_task, monkeypatch):
    """RUNNING 的消息早被 Worker 消费——revoke 无意义，只对还在排队的 pending 撤回。"""
    from app.models.enums import TaskStatus
    from app.models.task import Task

    tid = pending_task()
    with session_factory() as s:
        t = s.get(Task, tid)
        t.status = TaskStatus.RUNNING.value
        t.celery_task_id = "cid-running"
        s.commit()

    revoked: list = []
    monkeypatch.setattr("app.services.task_service.revoke_task", lambda cid: revoked.append(cid))
    monkeypatch.setattr("app.services.task_service.kill_process_tree", lambda pid: True)
    assert client.post(f"/api/v1/tasks/{tid}/cancel").status_code == 200
    assert revoked == []  # 未撤回
