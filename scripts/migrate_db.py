# 本项目编码格式：UTF-8（无 BOM）。
"""SQLite 增量补列：模型新增列后修复已存在的旧库，免去删库重来。

why 需要它：`Base.metadata.create_all` 只建不存在的表，**不 ALTER 已存在表**。模型加列后
旧库缺列，ORM 一读就 `OperationalError: no such column`（`GET /cases` 直接 500）。
历史上 `trust_score`、`quality_flags` 两次都撞到这个问题，处置方式一次是手写 ALTER、
一次是删库。容器场景更难受：库在 named volume 里，删库要 `docker compose down -v`，
把执行记录与用例一起丢掉。

设计约束：
- **加列与删列都要处理**：模型删掉的列如果残留成 `NOT NULL` 且无默认值，会让新插入直接
  `NOT NULL constraint failed`（`ai_confidence` 就是这样从"读不到"升级成"写不进"）。
  只补列不删列，迁移完仍然 500。
- **只删"阻断写入"的残留列**：可空或有默认值的多余列不影响 ORM，留着比删更安全。
- **幂等**：以「当前实际列」与「模型定义列」求差集，重复执行不产生副作用。
- **不改已有数据**：ADD COLUMN 一律带默认值或允许 NULL，旧行读出来是默认值而非报错。
- **不替代建表**：空库仍走 `create_all`；本脚本只处理"表已存在但列不对"。

用法（在 backend/ 目录下，应用停掉再跑）：
    python scripts/migrate_db.py                      # 迁移 DATABASE_URL 指向的库
    python scripts/migrate_db.py --db data/platform.db # 指定文件
    python scripts/migrate_db.py --dry-run            # 只打印将执行的 DDL
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 与数据库无关的模型必须已注册，否则 Base.metadata 里没有表定义
import app.models  # noqa: F401,E402  # noqa: F401
from app.core.database import Base  # noqa: E402
from sqlalchemy import Boolean, Float, Integer, String  # noqa: E402
from sqlalchemy.schema import CreateColumn  # noqa: E402


def _sqlite_type(column) -> str:
    """把模型列类型翻成 SQLite 声明类型。

    why 不用 `str(CreateColumn)` 的完整 DDL：它带 FOREIGN KEY / 约束子句，而 SQLite 的
    `ALTER TABLE ADD COLUMN` 限制极严（不能加 PRIMARY KEY、不能加 UNIQUE），拼接会直接报错。
    这里只保留类型与可空性——补列的目标是"让 ORM 能读"，不是复刻完整约束。
    """
    try:
        compiled = str(CreateColumn(column).compile(dialect=None))
    except Exception:  # noqa: BLE001
        compiled = ""
    for token in ("VARCHAR", "TEXT", "INTEGER", "FLOAT", "BOOLEAN", "JSON", "DATETIME", "DATE"):
        if token in compiled.upper():
            if token == "VARCHAR":
                size = getattr(column.type, "length", None) or 255
                return f"VARCHAR({size})"
            if token == "JSON":
                return "JSON"
            return token
    # 兜底：按 SQLAlchemy 类型族判断，避免上面字符串匹配漏掉方言变体
    if isinstance(column.type, String):
        return f"VARCHAR({column.type.length or 255})"
    if isinstance(column.type, Integer):
        return "INTEGER"
    if isinstance(column.type, Float):
        return "FLOAT"
    if isinstance(column.type, Boolean):
        return "BOOLEAN"
    return "TEXT"


def _default_clause(column) -> str:
    """ADD COLUMN 的默认值子句。

    why 必须有：SQLite 的 `ADD COLUMN ... NOT NULL` 要求给出非 NULL 默认值，否则 DDL 报错；
    对可空列也补默认值，让旧行读出确定值而不是 NULL（例如 trust_score 旧行应为 100）。
    """
    if column.server_default is not None:
        text = str(column.server_default.arg)
        return f" DEFAULT {text}"
    default = getattr(column, "default", None)
    if default is not None and getattr(default, "is_scalar", False):
        value = default.arg
        if isinstance(value, str):
            escaped = value.replace("'", "''")
            return f" DEFAULT '{escaped}'"
        if isinstance(value, bool):
            return f" DEFAULT {1 if value else 0}"
        return f" DEFAULT {value}"
    # 无可用的 Python 侧默认值：交给 NOT NULL 分支决定是否报错
    return ""


def plan_migrations(connection: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """对比模型与实际库，产出 (待执行 DDL, 跳过说明)。

    why 只覆盖"表已存在"：表不存在说明是全新库，`create_all` 会建全量 schema，
    这里不该插手（否则会漏建索引与外键）。

    why 残留列只在"会阻断写入"时删除：SQLite 的 `ALTER TABLE ADD COLUMN` 无法给已有列补默认值，
    所以 `NOT NULL` 且无默认值的残留列只能删掉——否则新插入必然 `NOT NULL constraint failed`。
    可空或有默认值的残留列不影响 ORM（SQLAlchemy 只操作映射过的列），留着不冒删数据的风险。
    """
    statements: list[str] = []
    notes: list[str] = []
    existing_tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            continue
        info = list(connection.execute(f'PRAGMA table_info("{table_name}")'))
        actual = {row[1]: row for row in info}
        mapped = {column.name: column for column in table.columns}

        for column in table.columns:
            if column.name in actual:
                continue
            ddl = (
                f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" '
                f"{_sqlite_type(column)}{_default_clause(column)}"
            )
            if not column.nullable and " DEFAULT " not in ddl:
                raise SystemExit(
                    f"无法自动补列 {table_name}.{column.name}："
                    "该列 NOT NULL 且无默认值，SQLite 要求 ADD COLUMN 必须给出默认值。"
                    "请在模型上补 default/server_default 后重试。"
                )
            statements.append(ddl)

        for name, row in actual.items():
            if name in mapped:
                continue
            not_null = bool(row[3])
            has_default = row[4] is not None
            if not_null and not has_default:
                statements.append(f'ALTER TABLE "{table_name}" DROP COLUMN "{name}"')
                notes.append(
                    f"{table_name}.{name}：模型已删除，但库中仍为 NOT NULL 且无默认值"
                    "（会阻断新插入）→ 删除该列"
                )
            else:
                notes.append(
                    f"{table_name}.{name}：模型已删除，但库中可空或有默认值 → 保留（不影响读写）"
                )
    return statements, notes


def migrate(db_path: Path, *, dry_run: bool = False) -> tuple[list[str], list[str]]:
    """对单个 SQLite 文件执行 schema 对齐，返回 (实际执行的 DDL, 说明)。"""
    if not db_path.exists():
        raise SystemExit(f"库不存在：{db_path}（新库由应用启动时的 create_all 建全量 schema）")
    connection = sqlite3.connect(db_path)
    try:
        statements, notes = plan_migrations(connection)
        if not statements:
            return [], notes
        if dry_run:
            return statements, notes
        for ddl in statements:
            connection.execute(ddl)
        connection.commit()
        return statements, notes
    finally:
        connection.close()


def resolve_db_path(explicit: str | None) -> Path:
    """确定目标库文件。

    why 先看显式参数、再看 TESTPLATFORM_DATABASE_URL：容器里 DATABASE_URL 是
    `sqlite:////app/data/platform.db`（四个斜杠），换成路径要剥掉协议前缀。
    """
    if explicit:
        return Path(explicit)
    url = os.environ.get("TESTPLATFORM_DATABASE_URL", "")
    if not url:
        # 与 config 默认值保持一致，但不需要加载完整 settings（避免 .env 缺失时启动失败）
        from app.core.config import get_settings

        url = get_settings().database.url
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///") :])
    if url.startswith("sqlite://"):
        return Path(url[len("sqlite://") :])
    raise SystemExit(f"仅支持 SQLite 文件库，当前 DATABASE_URL={url}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="为已存在的 SQLite 库对齐模型 schema（补列/删阻断列）"
    )
    parser.add_argument("--db", help="库文件路径；缺省读 TESTPLATFORM_DATABASE_URL / 配置")
    parser.add_argument("--dry-run", action="store_true", help="只打印 DDL，不执行")
    args = parser.parse_args(argv)

    db_path = resolve_db_path(args.db)
    print(f"目标库：{db_path}")
    statements, notes = migrate(db_path, dry_run=args.dry_run)

    if notes:
        print("\n检查说明：")
        for note in notes:
            print(f"  - {note}")

    if not statements:
        print("\nschema 已可用，无需迁移")
        return 0

    print(f"\n{'将执行' if args.dry_run else '已执行'} {len(statements)} 条 DDL：")
    for ddl in statements:
        print(f"  {ddl}")
    if args.dry_run:
        print("\n（dry-run：未写入数据库）")
    else:
        print("\n完成。重启应用即可，无需删库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
