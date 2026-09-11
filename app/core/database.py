# 数据库基座。why：所有 SQLite 连接必须经唯一 get_engine() 工厂，
# 统一挂 WAL / busy_timeout / foreign_keys（SQLite 默认不启用外键）；MVP 用 create_all 建表（Phase 4 切 Alembic）。
# 方言差异（SQLite vs PostgreSQL）收敛在 is_sqlite_url / engine_kwargs 两个纯函数里（P3-8a）。
from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def is_sqlite_url(url: str) -> bool:
    """是否 SQLite 方言。why：WAL/外键 pragma 与 check_same_thread 只对 SQLite 有效，
    对 PostgreSQL 执行会直接报错——方言判定必须显式且唯一（P3-8a）。"""
    return url.startswith("sqlite")


def engine_kwargs(url: str) -> dict:
    """create_engine 的方言相关参数（纯函数：可单测，不留「只有真连库才能验」的分支）。

    - SQLite：`check_same_thread=False`——FastAPI 线程池跨线程复用连接，否则报
      'SQLite objects created in a thread can only be used in that same thread'
    - 其它方言（PostgreSQL 等）：`pool_pre_ping=True`——连接被 DB/中间件掐断后先探活再取用，
      避免把「陈旧连接」当业务错误抛给调用方
    """
    if is_sqlite_url(url):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


def apply_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """why：WAL 允许读写并发、busy_timeout 缓解锁竞争、foreign_keys 默认关闭必须显式开。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@lru_cache(maxsize=1)
def get_engine():
    """唯一引擎工厂。连接参数与 pragma 接线由上面的纯函数决定（SQLite / PostgreSQL 走同一条路径）。"""
    settings = get_settings()
    url = settings.database.url
    engine = create_engine(url, echo=settings.database.echo, **engine_kwargs(url))
    if is_sqlite_url(url):
        event.listen(engine, "connect", apply_sqlite_pragmas)
    return engine


SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)


def init_db() -> None:
    """启动时建表（仅执行一次，阻塞无妨）。why：MVP 用 create_all，Phase 4 切 Alembic（RULES.md §5.1）。"""
    from app import models  # noqa: F401  # 确保模型注册进 Base.metadata

    Base.metadata.create_all(bind=get_engine())
