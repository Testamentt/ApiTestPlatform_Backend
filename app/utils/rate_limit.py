# 限流基础设施（Redis 固定窗口）。why：鉴权解决"你是谁"，限流解决"你能多快"——脚本误跑/暴力试错需要准入闸。
# 固定窗口（而非令牌桶/滑动窗口）是刻意选择：实现 20 行、行为可解释、面试讲得清，且复用既有 Redis 不引入新组件。
# 本模块只做"纯逻辑 + 一次 Redis INCR/EXPIRE"，不依赖 FastAPI（依赖注入在 app/api/v1/deps.py 组装）。
from __future__ import annotations

import hashlib
import logging
import time

import redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def hash_identity(raw: str) -> str:
    """主体标识只存哈希。why：限流 key 会落到 Redis——不能把 Bearer 令牌明文写进去（§8 密钥不入库/不入外部存储）。"""
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def window_key(identity: str, *, now: float, window_seconds: int) -> str:
    """固定窗口 key：同一窗口内固定，跨窗口自动切换（旧 key 靠 EXPIRE 自然淘汰）。

    why：窗口对齐用 `int(now / window)`——不用滑动窗口是为了让"重置时刻"可预测（排障时能一眼算出何时恢复）。
    """
    bucket = int(now) // max(1, window_seconds)
    return f"testplatform:ratelimit:{identity}:{bucket}"


def _client() -> redis.Redis:
    """按配置建一次性 Redis 连接。独立函数便于测试注入 fake（不连真实 Redis）。"""
    settings = get_settings().redis
    return redis.Redis(
        host=settings.host,
        port=settings.port,
        password=settings.password or None,
        db=settings.db,
        socket_timeout=settings.socket_timeout,
    )


def hit_count(identity: str, *, window_seconds: int, now: float | None = None) -> int | None:
    """记一次访问并返回窗口内计数；Redis 不可用时返回 None（调用方据此放行）。

    why：限流是**保护**而不是正确性前提——Redis 抖动时若直接拒绝，会把"可用性故障"升级成"全站 429"；
    因此失败降级为放行 + 告警，把风险交给既有的鉴权与超时机制兜底。
    """
    moment = time.time() if now is None else now
    key = window_key(identity, now=moment, window_seconds=window_seconds)
    client = _client()
    try:
        count = client.incr(key)
        if count == 1:
            client.expire(key, window_seconds)  # 只在窗口首个请求设过期，避免每次刷新 TTL
        return int(count)
    except Exception as e:  # noqa: BLE001  # redis.RedisError 之外的连接层异常也要降级，故兜底
        logger.warning("限流计数失败，放行本次请求（P3-8c 降级策略）: %s", e)
        return None
    finally:
        client.close()
