# 健康检查。why：不做鉴权（部署探针用）；db/redis 状态供运维判断。
from __future__ import annotations

import redis
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.core.config import get_settings

router = APIRouter(tags=["health"])


class HealthRead(BaseModel):
    status: str
    db: str
    redis: str


@router.get("/health", response_model=HealthRead)
def health(db: Session = Depends(get_db)) -> HealthRead:
    db_status = "up"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "down"
    redis_status = "up"
    try:
        settings = get_settings()
        r = redis.Redis(
            host=settings.redis.host,
            port=settings.redis.port,
            password=settings.redis.password or None,
            db=settings.redis.db,
            socket_timeout=2,
        )
        r.ping()
    except Exception:
        redis_status = "down"
    status = "ok" if db_status == "up" and redis_status == "up" else "degraded"
    return HealthRead(status=status, db=db_status, redis=redis_status)
