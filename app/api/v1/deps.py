# 数据库会话依赖。why：每个请求独立会话，try-finally 保证关闭（泄漏会锁库）；
# 测试通过 app.dependency_overrides[get_db] 注入内存库。
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.core.database import SessionLocal


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
