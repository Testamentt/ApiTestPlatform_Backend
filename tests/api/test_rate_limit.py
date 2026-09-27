# 限流测试（P3-8c）：固定窗口命中 429 / 关闭时不碰 Redis / Redis 不可用放行（降级策略）。
# why：限流是"保护"不是"正确性前提"——降级行为必须被测试锁住，否则一次 Redis 抖动会变成全站 429。
from __future__ import annotations

import pytest
import redis
from app.core.config import get_settings
from fastapi.testclient import TestClient


class _FakeRedis:
    """最小 Redis 假实现：只支持 INCR/EXPIRE/close（限流用到的全部命令）。"""

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.counts: dict[str, int] = {}
        self.expires: list[tuple[str, int]] = []
        self.closed = False

    def incr(self, key: str) -> int:
        if self.fail:
            raise redis.ConnectionError("boom")
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key: str, seconds: int) -> bool:
        if self.fail:
            raise redis.ConnectionError("boom")
        self.expires.append((key, seconds))
        return True

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def auth_client(monkeypatch, session_factory):
    """独立客户端：真实 verify_token（因此限流也真实生效），DB 走内存库。"""
    from app.api.v1.deps import get_db
    from app.main import app

    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(get_settings().security, "api_token", "test-token")
    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _patch_redis(monkeypatch, fake: _FakeRedis) -> None:
    monkeypatch.setattr("app.utils.rate_limit._client", lambda: fake)


def test_rate_limit_disabled_does_not_touch_redis(monkeypatch, auth_client):
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    monkeypatch.setattr(get_settings().security, "rate_limit_per_minute", 0)
    r = auth_client.get("/api/v1/cases", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    assert fake.counts == {}  # 关闭时一次都不该访问 Redis


def test_rate_limit_blocks_over_quota(monkeypatch, auth_client):
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    monkeypatch.setattr(get_settings().security, "rate_limit_per_minute", 2)
    headers = {"Authorization": "Bearer test-token"}
    assert auth_client.get("/api/v1/cases", headers=headers).status_code == 200
    assert auth_client.get("/api/v1/cases", headers=headers).status_code == 200
    blocked = auth_client.get("/api/v1/cases", headers=headers)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "RATE_LIMITED"
    assert fake.expires  # 首个请求设过窗口过期时间


def test_rate_limit_key_hides_token(monkeypatch, auth_client):
    # why：限流 key 落 Redis——不能出现令牌明文（§8 密钥不入库/不入外部存储）
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    monkeypatch.setattr(get_settings().security, "rate_limit_per_minute", 5)
    auth_client.get("/api/v1/cases", headers={"Authorization": "Bearer test-token"})
    keys = list(fake.counts)
    assert keys and all("test-token" not in key for key in keys)


def test_rate_limit_fails_open_when_redis_down(monkeypatch, auth_client):
    _patch_redis(monkeypatch, _FakeRedis(fail=True))
    monkeypatch.setattr(get_settings().security, "rate_limit_per_minute", 1)
    headers = {"Authorization": "Bearer test-token"}
    # 连打两次都不该被拒（降级为放行），且不能 500
    assert auth_client.get("/api/v1/cases", headers=headers).status_code == 200
    assert auth_client.get("/api/v1/cases", headers=headers).status_code == 200


def test_rate_limit_skips_unauthenticated_requests(monkeypatch, auth_client):
    # 鉴权在限流之前：无凭证直接 401，不该消耗配额（否则匿名请求能把配额打满）
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    monkeypatch.setattr(get_settings().security, "rate_limit_per_minute", 1)
    assert auth_client.get("/api/v1/cases").status_code == 401
    assert fake.counts == {}


def test_window_key_rotates_with_window():
    from app.utils.rate_limit import window_key

    a = window_key("tok:abc", now=100.0, window_seconds=60)
    b = window_key("tok:abc", now=160.0, window_seconds=60)
    assert a != b  # 跨窗口换 key → 固定窗口语义
    assert a.endswith(":1") and b.endswith(":2")
