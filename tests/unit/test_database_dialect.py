# 数据库方言适配测试（P3-8a）：SQLite / PostgreSQL 的引擎参数分派。
# why：WAL pragma 与 check_same_thread 只对 SQLite 有效，对 PostgreSQL 执行会直接报错——
# 方言判定必须是纯函数、可测、唯一。
from __future__ import annotations

from app.core.database import engine_kwargs, is_sqlite_url


def test_is_sqlite_url():
    assert is_sqlite_url("sqlite:///./data/platform.db")
    assert is_sqlite_url("sqlite+pysqlite:///:memory:")
    assert not is_sqlite_url("postgresql+psycopg://user:pass@host:5432/db")


def test_engine_kwargs_sqlite_uses_check_same_thread():
    kwargs = engine_kwargs("sqlite:///./data/platform.db")
    assert kwargs == {"connect_args": {"check_same_thread": False}}
    # SQLite 不能带 pool_pre_ping（SQLAlchemy 对 SQLite 的 pool 参数有限制，且无必要）
    assert "pool_pre_ping" not in kwargs


def test_engine_kwargs_postgres_uses_pre_ping():
    kwargs = engine_kwargs("postgresql+psycopg://user:pass@host:5432/db")
    assert kwargs == {"pool_pre_ping": True}
    assert "connect_args" not in kwargs
