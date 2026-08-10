# 健康检查接口测试：db/redis 探测 up/down 组合，mock 真实 Redis 连接（不依赖本机 Redis）。
from __future__ import annotations

from app.api.v1.deps import get_db
from app.main import app
from fastapi.testclient import TestClient


class _FakeRedis:
    """ping 成功/失败的假 Redis。"""

    def __init__(self, *, ok=True, **kw):
        self._ok = ok

    def ping(self):
        if not self._ok:
            raise ConnectionError("redis down")
        return True


def test_health_ok(client, monkeypatch):
    monkeypatch.setattr("app.api.v1.health.redis.Redis", _FakeRedis)
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["db"] == "up"
    assert data["redis"] == "up"


def test_health_redis_down_degraded(client, monkeypatch):
    monkeypatch.setattr("app.api.v1.health.redis.Redis", lambda **kw: _FakeRedis(ok=False))
    r = client.get("/api/v1/health")
    data = r.json()
    assert data["redis"] == "down"
    assert data["status"] == "degraded"
    assert data["db"] == "up"


def test_health_db_down_degraded(monkeypatch):
    class _BoomSession:
        def execute(self, *a, **k):
            raise Exception("db down")

        def close(self):
            pass

    def _override_get_db():
        yield _BoomSession()

    monkeypatch.setattr("app.api.v1.health.redis.Redis", _FakeRedis)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        r = TestClient(app).get("/api/v1/health")
    finally:
        app.dependency_overrides.clear()
    data = r.json()
    assert data["db"] == "down"
    assert data["status"] == "degraded"
