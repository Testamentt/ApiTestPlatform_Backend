# 旧库 report_link 迁移（P3-3）。why：报告访问从「无鉴权 /static 静态面」改为「带鉴权 API 端点」后，
# 历史任务行里存的仍是 /static/{id}/report.html，点开就是 404；本脚本把旧链接就地改写成新端点。
# 幂等：已是新形态（或为空）的行不动，可反复执行。
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal  # noqa: E402
from app.models.task import Task  # noqa: E402
from sqlalchemy import select  # noqa: E402

# 旧形态：/static/{task_id}/report.html（也兼容历史可能出现的绝对 URL 前缀）
_LEGACY_RE = re.compile(r"^.*?/static/(?P<task_id>\d+)/report\.html$")


def rewrite_report_link(value: str | None) -> str | None:
    """把旧 report_link 改写成带鉴权端点；无法识别或已是新形态时原样返回（幂等）。

    why：抽成纯函数——迁移脚本的正确性靠单测锁定，而不是靠"跑一遍看看"。
    """
    if not value:
        return value
    match = _LEGACY_RE.match(value)
    if match is None:
        return value
    return f"/api/v1/tasks/{match.group('task_id')}/report"


def migrate(*, dry_run: bool = False) -> tuple[int, int]:
    """扫描 tasks 表并改写旧链接，返回 (扫描数, 改写数)。"""
    scanned = changed = 0
    with SessionLocal() as session:
        for task in session.scalars(select(Task)).all():
            scanned += 1
            new_link = rewrite_report_link(task.report_link)
            if new_link != task.report_link:
                changed += 1
                print(f"task #{task.id}: {task.report_link} → {new_link}")
                task.report_link = new_link
        if dry_run:
            session.rollback()
        else:
            session.commit()
    return scanned, changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把历史任务的 report_link 迁移到带鉴权端点（P3-3）"
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印将改写的行，不落库")
    args = parser.parse_args()
    scanned, changed = migrate(dry_run=args.dry_run)
    mode = "（dry-run，未落库）" if args.dry_run else ""
    print(f"扫描 {scanned} 行，改写 {changed} 行{mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
