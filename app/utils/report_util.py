# 简单 HTML 报告（替代 Allure）。why：MVP 自包含 HTML 即可演示；必须处理无有效结果（pytest 失败）；
# 所有插值字段 html.escape——用例名/失败信息来自用户与 pytest 输出，不转义即存储型 XSS（review H2）。
# 报告路径唯一来源在 report_path()：写入（执行引擎）与读取（带鉴权端点）共用，避免两处拼接漂移。
from __future__ import annotations

from html import escape
from pathlib import Path

from app.core.config import get_settings


def report_path(task_id: int) -> Path:
    """任务报告文件路径（唯一来源）。why：按 task_id 隔离目录，报告不外泄；读写共用同一计算避免漂移。"""
    return (
        Path(get_settings().execution.workspace_dir).resolve()
        / "reports"
        / str(task_id)
        / "report.html"
    )


def _esc(value) -> str:
    return escape(str(value), quote=True)


def write_report_html(task, summary: dict, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    total = summary.get("total", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    rows = []
    for item in summary.get("results", []):
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                _esc(item.get("case_id", "")),
                _esc(item.get("name", "")),
                _esc(item.get("status", "")),
                _esc(item.get("failure_msg", "")),
            )
        )
    if not rows:
        rows.append("<tr><td colspan='4'>执行失败，无有效结果</td></tr>")
    html = (
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
        f"<title>任务 #{_esc(task.id)} 报告</title></head><body>"
        f"<h1>任务 #{_esc(task.id)} 执行报告</h1>"
        f"<p>状态: {_esc(task.status)} | 总数: {total} | 通过: {passed} | 失败: {failed}</p>"
        "<table border='1' cellspacing='0' cellpadding='4'>"
        "<tr><th>case_id</th><th>name</th><th>status</th><th>failure</th></tr>"
        + "".join(rows)
        + "</table></body></html>"
    )
    path = report_dir / "report.html"
    path.write_text(html, encoding="utf-8")
    return path
