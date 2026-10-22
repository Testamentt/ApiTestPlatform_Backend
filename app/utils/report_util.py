# 简单 HTML 报告（替代 Allure）。why：MVP 自包含 HTML 即可演示；必须处理无有效结果（pytest 失败）；
# 所有插值字段 html.escape——用例名/失败信息来自用户与 pytest 输出，不转义即存储型 XSS。
# 报告路径唯一来源在 report_path()：写入（执行引擎）与读取（带鉴权端点）共用，避免两处拼接漂移。
from __future__ import annotations

import logging
from html import escape
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)


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


def report_dir(task_id: int) -> Path:
    """任务报告目录（report.html 的父目录）。why：读写两侧共用，避免目录口径漂移。"""
    return report_path(task_id).parent


def remove_report(task_id: int) -> bool:
    """删除任务的旧报告文件（重试重置时调用）。

    why：`result_summary` / `report_link` 清空后，磁盘上的 report.html 仍是上一次执行的产物——
    `GET /tasks/{id}/report` 走的是文件系统而不是 DB（见 TaskService.get_report_html），
    不删文件就会出现「DB 已是 pending、报告端点仍返回旧内容」的脏读（与重试不清旧结果同源）。
    best-effort：删不掉只警告，不阻塞重试（report_link 已为 None，前端不会主动拉）。
    """
    path = report_path(task_id)
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError as e:
        logger.warning("删除任务 %s 旧报告失败（忽略）: %s", task_id, e)
        return False


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
