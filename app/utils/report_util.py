# 简单 HTML 报告（替代 Allure）。why：MVP 自包含 HTML 即可演示；必须处理无有效结果（pytest 失败）。
from __future__ import annotations

from pathlib import Path


def write_report_html(task, summary: dict, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    total = summary.get("total", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    rows = []
    for item in summary.get("results", []):
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                item.get("case_id", ""),
                item.get("name", ""),
                item.get("status", ""),
                item.get("failure_msg", ""),
            )
        )
    if not rows:
        rows.append("<tr><td colspan='4'>执行失败，无有效结果</td></tr>")
    html = (
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
        f"<title>任务 #{task.id} 报告</title></head><body>"
        f"<h1>任务 #{task.id} 执行报告</h1>"
        f"<p>状态: {task.status} | 总数: {total} | 通过: {passed} | 失败: {failed}</p>"
        "<table border='1' cellspacing='0' cellpadding='4'>"
        "<tr><th>case_id</th><th>name</th><th>status</th><th>failure</th></tr>"
        + "".join(rows)
        + "</table></body></html>"
    )
    path = report_dir / "report.html"
    path.write_text(html, encoding="utf-8")
    return path
