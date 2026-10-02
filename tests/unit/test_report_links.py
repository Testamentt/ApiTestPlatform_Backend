# report_link 迁移脚本测试（P3-3）：旧 /static 链接改写为带鉴权端点，幂等且不留半截数据。
from __future__ import annotations

from app.models.enums import TaskStatus
from app.models.task import Task
from scripts.fix_report_links import migrate, rewrite_report_link


def test_rewrite_legacy_link():
    assert rewrite_report_link("/static/7/report.html") == "/api/v1/tasks/7/report"


def test_rewrite_legacy_absolute_and_trailing():
    # 兼容历史可能出现的绝对 URL 形态（同一条正则覆盖）
    assert (
        rewrite_report_link("http://127.0.0.1:8000/static/42/report.html")
        == "/api/v1/tasks/42/report"
    )


def test_rewrite_is_idempotent_for_new_form():
    assert rewrite_report_link("/api/v1/tasks/3/report") == "/api/v1/tasks/3/report"


def test_rewrite_keeps_empty_and_unknown_values():
    assert rewrite_report_link(None) is None
    assert rewrite_report_link("") == ""
    assert rewrite_report_link("/reports/whatever.html") == "/reports/whatever.html"


def test_migrate_updates_only_legacy_rows(session_factory, monkeypatch):
    with session_factory() as s:
        legacy = Task(run_id="r-legacy", case_ids=[1], status=TaskStatus.SUCCESS.value)
        legacy.report_link = "/static/1/report.html"
        fresh = Task(run_id="r-fresh", case_ids=[1], status=TaskStatus.SUCCESS.value)
        fresh.report_link = "/api/v1/tasks/2/report"
        pending = Task(run_id="r-none", case_ids=[1], status=TaskStatus.PENDING.value)
        s.add_all([legacy, fresh, pending])
        s.commit()
        legacy_id, fresh_id, pending_id = legacy.id, fresh.id, pending.id

    monkeypatch.setattr("scripts.fix_report_links.SessionLocal", session_factory)
    scanned, changed = migrate()
    assert (scanned, changed) == (3, 1)  # 只改旧形态那一行

    with session_factory() as s:
        assert s.get(Task, legacy_id).report_link == f"/api/v1/tasks/{legacy_id}/report"
        assert s.get(Task, fresh_id).report_link == "/api/v1/tasks/2/report"
        assert s.get(Task, pending_id).report_link is None


def test_migrate_dry_run_does_not_persist(session_factory, monkeypatch):
    with session_factory() as s:
        task = Task(run_id="r-dry", case_ids=[1], status=TaskStatus.SUCCESS.value)
        task.report_link = "/static/9/report.html"
        s.add(task)
        s.commit()
        task_id = task.id

    monkeypatch.setattr("scripts.fix_report_links.SessionLocal", session_factory)
    scanned, changed = migrate(dry_run=True)
    assert (scanned, changed) == (1, 1)  # 报告"将改写 1 行"，但不落库
    with session_factory() as s:
        assert s.get(Task, task_id).report_link == "/static/9/report.html"
