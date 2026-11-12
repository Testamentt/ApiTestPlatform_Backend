# 本项目编码格式：UTF-8（无 BOM）。
# 迁移脚本单测：模型加列/删列后，旧库必须能对齐到"可读可写"，而不是靠删库重来。
#
# why：`Base.metadata.create_all` 只建表不 ALTER，模型一改旧库就漂移。历史上 `trust_score`
# 缺失导致 `GET /cases` 500，`quality_flags` 缺失会让同一处再挂一次；而删掉 `ai_confidence`
# 留下的 `NOT NULL` 残留列更隐蔽——读没事，写入时 `NOT NULL constraint failed`。
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "migrate_db.py"
UTF8 = "utf-8"


def _load_module():
    """按路径加载脚本。why：scripts/ 不是包，且要保证测试跑的是仓库里那份实现。"""
    spec = importlib.util.spec_from_file_location("migrate_db", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["migrate_db"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


migrate_db = _load_module()


def _make_stale_db(path: Path) -> None:
    """复刻一个"旧库"：缺 quality_flags，且残留 NOT NULL 无默认值的 ai_confidence。

    同时建一张与模型无关的表，验证脚本不会越界改动。
    """
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE test_cases (
            name VARCHAR(255) NOT NULL,
            method VARCHAR(16) NOT NULL,
            path VARCHAR(1024) NOT NULL,
            operation_id VARCHAR(255) NOT NULL,
            params JSON,
            body JSON,
            expected_status INTEGER DEFAULT 200 NOT NULL,
            assertions JSON,
            status VARCHAR(16) DEFAULT 'draft' NOT NULL,
            source VARCHAR(16) DEFAULT 'manual' NOT NULL,
            trust_score INTEGER DEFAULT 100 NOT NULL,
            reviewer VARCHAR(64),
            id INTEGER NOT NULL PRIMARY KEY,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
        );
        CREATE TABLE generation_logs (
            generation_task_id INTEGER,
            operation_id VARCHAR(255) NOT NULL,
            model VARCHAR(64) NOT NULL,
            prompt_version VARCHAR(16) NOT NULL,
            status VARCHAR(16) NOT NULL,
            ai_confidence FLOAT NOT NULL,
            usage JSON,
            latency_ms INTEGER,
            cost_estimate FLOAT,
            raw_response TEXT,
            error_msg TEXT,
            id INTEGER NOT NULL PRIMARY KEY,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
        );
        CREATE TABLE unrelated (a TEXT);
        INSERT INTO test_cases (name, method, path, operation_id, id)
            VALUES ('legacy', 'GET', '/x', 'op', 1);
        INSERT INTO generation_logs (operation_id, model, prompt_version, status, ai_confidence, id)
            VALUES ('op', 'm', 'v1', 'success', 1.0, 1);
        """
    )
    con.commit()
    con.close()


def test_migrate_adds_missing_column(tmp_path):
    db = tmp_path / "old.db"
    _make_stale_db(db)
    statements, _notes = migrate_db.migrate(db)
    assert any("ADD COLUMN" in s and "quality_flags" in s for s in statements)

    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(test_cases)")}
    con.close()
    assert "quality_flags" in cols


def test_migrate_drops_insert_blocking_legacy_column(tmp_path):
    """核心回归：删模型字段后，残留的 NOT NULL 不用默认值会阻断写入。"""
    db = tmp_path / "old.db"
    _make_stale_db(db)
    migrate_db.migrate(db)

    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(generation_logs)")}
    con.close()
    assert "ai_confidence" not in cols

    # 迁移后必须能按新模型形状插入（不带 ai_confidence）
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO generation_logs (operation_id, model, prompt_version, status)"
        " VALUES ('op2', 'm', 'v1', 'success')"
    )
    con.commit()
    con.close()


def test_migrate_keeps_legacy_rows(tmp_path):
    """补列不得丢数据：旧行仍在，新列取默认值（可空 → NULL）。"""
    db = tmp_path / "old.db"
    _make_stale_db(db)
    migrate_db.migrate(db)

    con = sqlite3.connect(db)
    row = con.execute("SELECT name, quality_flags FROM test_cases WHERE id=1").fetchone()
    logs = con.execute("SELECT count(*) FROM generation_logs").fetchone()[0]
    con.close()
    assert row == ("legacy", None)
    assert logs == 1  # 删列不删行


def test_migrate_is_idempotent(tmp_path):
    db = tmp_path / "old.db"
    _make_stale_db(db)
    first, _ = migrate_db.migrate(db)
    second, _ = migrate_db.migrate(db)
    assert first  # 首次有 DDL
    assert second == []  # 再跑无动作


def test_migrate_leaves_unrelated_tables_alone(tmp_path):
    db = tmp_path / "old.db"
    _make_stale_db(db)
    migrate_db.migrate(db)
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(unrelated)")}
    con.close()
    assert cols == {"a"}


def test_migrate_skips_unknown_tables_not_in_models(tmp_path):
    """库里存在模型没有的表时，不报错也不动它。"""
    db = tmp_path / "old.db"
    _make_stale_db(db)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE extra_table (x TEXT)")
    con.commit()
    con.close()

    statements, _ = migrate_db.migrate(db)
    assert all("extra_table" not in s for s in statements)


def test_dry_run_changes_nothing(tmp_path):
    db = tmp_path / "old.db"
    _make_stale_db(db)
    statements, _ = migrate_db.migrate(db, dry_run=True)
    assert statements

    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(test_cases)")}
    con.close()
    assert "quality_flags" not in cols  # dry-run 不落盘


def test_missing_db_fails_with_clear_message(tmp_path):
    with pytest.raises(SystemExit) as exc:
        migrate_db.migrate(tmp_path / "nope.db")
    assert "不存在" in str(exc.value)


def test_plan_reports_retained_legacy_column(tmp_path):
    """可空/有默认值的残留列只提示不删——删数据要有明确理由。"""
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE test_cases (
            name VARCHAR(255) NOT NULL,
            method VARCHAR(16) NOT NULL,
            path VARCHAR(1024) NOT NULL,
            operation_id VARCHAR(255) NOT NULL,
            params JSON, body JSON,
            expected_status INTEGER DEFAULT 200 NOT NULL,
            assertions JSON,
            status VARCHAR(16) DEFAULT 'draft' NOT NULL,
            source VARCHAR(16) DEFAULT 'manual' NOT NULL,
            trust_score INTEGER DEFAULT 100 NOT NULL,
            reviewer VARCHAR(64),
            id INTEGER NOT NULL PRIMARY KEY,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            legacy_optional TEXT
        );
        """
    )
    con.commit()
    con.close()

    _statements, notes = migrate_db.migrate(db, dry_run=True)
    assert any("legacy_optional" in n and "保留" in n for n in notes)

    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(test_cases)")}
    con.close()
    assert "legacy_optional" in cols  # dry-run 且本就不该删
