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
    statements, _notes, _backup = migrate_db.migrate(db)
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
    first, _, _ = migrate_db.migrate(db)
    second, _, _ = migrate_db.migrate(db)
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

    statements, _, _ = migrate_db.migrate(db)
    assert all("extra_table" not in s for s in statements)


def test_migrate_backs_up_database_before_ddl(tmp_path):
    """why：DROP COLUMN 不可逆，脚本是通用的——下次删的可能是有数据的列。

    备份必须在任何 DDL 之前完成，且内容要是「迁移前」的状态（含将被删的列）。
    """
    db = tmp_path / "old.db"
    _make_stale_db(db)
    statements, _notes, backup = migrate_db.migrate(db)

    assert statements
    assert backup is not None and backup.exists()

    # 备份里必须仍有 ai_confidence（证明拷的是迁移前状态，不是迁移后的库）
    con = sqlite3.connect(backup)
    cols = {r[1] for r in con.execute("PRAGMA table_info(generation_logs)")}
    con.close()
    assert "ai_confidence" in cols

    # 原库已迁移：列被删掉
    con = sqlite3.connect(db)
    live_cols = {r[1] for r in con.execute("PRAGMA table_info(generation_logs)")}
    con.close()
    assert "ai_confidence" not in live_cols


def test_backup_name_keeps_original_extension(tmp_path):
    """备份名不能改写原扩展名。

    why 不用 Path.with_suffix：`x.sqlite3` → `with_suffix('.db.bak')` 会变成 `x.db.bak`，
    看起来像另一个库；这里断言原名是前缀。
    """
    db = tmp_path / "platform.sqlite3"
    _make_stale_db(db)
    _statements, _notes, backup = migrate_db.migrate(db)

    assert backup is not None
    assert backup.name.startswith("platform.sqlite3.")
    assert backup.name.endswith(".bak")


def test_backup_does_not_overwrite_previous_run(tmp_path):
    """同一库连续迁移两次，备份不能互相覆盖：第一次那份才是真正的迁移前状态。"""
    db = tmp_path / "old.db"
    _make_stale_db(db)
    _first_statements, _notes, first_backup = migrate_db.migrate(db)
    # 第二次已无 DDL，不会再备份
    second_statements, _notes2, second_backup = migrate_db.migrate(db)

    assert first_backup is not None and first_backup.exists()
    assert second_statements == []
    assert second_backup is None


def test_dry_run_creates_no_backup(tmp_path):
    """dry-run 不写库，也不该产备份文件（大库拷贝代价不该白付）。"""
    db = tmp_path / "old.db"
    _make_stale_db(db)
    before = set(tmp_path.iterdir())

    statements, _, backup = migrate_db.migrate(db, dry_run=True)

    assert statements
    assert backup is None
    assert set(tmp_path.iterdir()) == before


def test_backup_copies_wal_sidecar_when_present(tmp_path):
    """WAL 模式下 -wal 可能含未 checkpoint 的数据，副本必须连它一起拷。"""
    db = tmp_path / "old.db"
    _make_stale_db(db)
    wal = tmp_path / "old.db-wal"
    wal.write_bytes(b"fake-wal")

    _statements, _notes, backup = migrate_db.migrate(db)

    assert backup is not None
    assert backup.with_name(backup.name + "-wal").exists()


def test_dry_run_changes_nothing(tmp_path):
    db = tmp_path / "old.db"
    _make_stale_db(db)
    statements, _, _ = migrate_db.migrate(db, dry_run=True)
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

    _statements, notes, _ = migrate_db.migrate(db, dry_run=True)
    assert any("legacy_optional" in n and "保留" in n for n in notes)

    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(test_cases)")}
    con.close()
    assert "legacy_optional" in cols  # dry-run 且本就不该删
