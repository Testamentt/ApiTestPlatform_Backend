# 数据库基座。why：所有 SQLite 连接必须经唯一 get_engine() 工厂，
# 统一挂 WAL / busy_timeout / foreign_keys（SQLite 默认不启用外键）；MVP 用 create_all 建表（Phase 4 切 Alembic）。
from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def apply_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """why：WAL 允许读写并发、busy_timeout 缓解锁竞争、foreign_keys 默认关闭必须显式开。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@lru_cache(maxsize=1)
def get_engine():
    """唯一引擎工厂。why：FastAPI 线程池跨线程用 SQLite 必须 check_same_thread=False，否则报
    'SQLite objects created in a thread can only be used in that same thread'。"""
    settings = get_settings()
    is_sqlite = settings.database.url.startswith("sqlite")
    engine = create_engine(
        settings.database.url,
        echo=settings.database.echo,
        connect_args={"check_same_thread": False} if is_sqlite else {},
    )
    if is_sqlite:
        event.listen(engine, "connect", apply_sqlite_pragmas)
    return engine


SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)


def init_db() -> None:
    """启动时建表（仅执行一次，阻塞无妨）。why：MVP 用 create_all，Phase 4 切 Alembic（RULES.md §5.1）。"""
    from app import models  # noqa: F401  # 确保模型注册进 Base.metadata

    Base.metadata.create_all(bind=get_engine())
