# 健康检查。why：不做鉴权（部署探针用）；db/redis 状态供运维判断。
from __future__ import annotations

import logging

import redis
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.core.config import get_settings

logger = logging.getLogger(__name__)

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
        # why：健康检查探测失败属预期场景，warning 级记录（不刷堆栈），状态置 down 供探针降级
        logger.warning("health: db 探测失败", exc_info=True)
        db_status = "down"
    redis_status = "up"
    try:
        settings = get_settings()
        r = redis.Redis(
            host=settings.redis.host,
            port=settings.redis.port,
            password=settings.redis.password or None,
            db=settings.redis.db,
            socket_timeout=settings.redis.socket_timeout,
        )
        r.ping()
    except Exception:
        logger.warning("health: redis 探测失败", exc_info=True)
        redis_status = "down"
    status = "ok" if db_status == "up" and redis_status == "up" else "degraded"
    return HealthRead(status=status, db=db_status, redis=redis_status)
