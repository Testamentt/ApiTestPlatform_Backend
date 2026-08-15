# HTML 报告生成测试：有结果 / 无结果兜底（best-effort 报告不因缺结果而失败）。
from __future__ import annotations

from types import SimpleNamespace

from app.utils.report_util import write_report_html


def test_write_report_html_renders_results(tmp_path):
    task = SimpleNamespace(id=1, status="success")
    summary = {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "skipped": 0,
        "duration_ms": 100,
        "results": [
            {"case_id": 1, "name": "a", "status": "pass", "failure_msg": ""},
            {"case_id": 2, "name": "b", "status": "fail", "failure_msg": "assert 200 != 500"},
        ],
    }
    path = write_report_html(task, summary, tmp_path / "reports")
    assert path.exists()
    html = path.read_text(encoding="utf-8")
    assert "任务 #1 执行报告" in html
    assert "通过: 1" in html and "失败: 1" in html
    assert "assert 200 != 500" in html


def test_write_report_html_no_results(tmp_path):
    # pytest 失败导致无有效结果时仍生成可查看报告（执行失败，无有效结果）
    task = SimpleNamespace(id=2, status="failed")
    summary = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "results": []}
    path = write_report_html(task, summary, tmp_path / "reports")
    assert path.exists()
    html = path.read_text(encoding="utf-8")
    assert "执行失败，无有效结果" in html


def test_write_report_html_escapes_user_content(tmp_path):
    # why：用例名/失败信息是用户与 pytest 可控内容，不转义即存储型 XSS（review H2）
    task = SimpleNamespace(id=1, status="success")
    summary = {
        "total": 1,
        "passed": 1,
        "failed": 0,
        "skipped": 0,
        "results": [
            {
                "case_id": 1,
                "name": "<script>alert(1)</script>",
                "status": "pass",
                "failure_msg": 'assert <b>200</b> != 500 "&"',
            },
        ],
    }
    path = write_report_html(task, summary, tmp_path / "reports")
    html = path.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;b&gt;200&lt;/b&gt;" in html
    assert "&amp;" in html
