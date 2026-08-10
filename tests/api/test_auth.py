"""Phase 4 Bearer Token 鉴权测试。

why：现有测试经 conftest 的 client fixture override verify_token 绕过鉴权；
本文件用独立 auth_client（真实 verify_token）验证 401/403/200 语义、health 免鉴权，
以及 OpenAPI securitySchemes 注册（Swagger UI Authorize 按钮存在，防回归）。
"""

from collections.abc import Iterator

import pytest
from app.api.v1.deps import get_db
from app.core.config import get_settings
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture()
def auth_client(session_factory, monkeypatch):
    """独立客户端：DB 走内存库，鉴权走真实 verify_token，token 固定 test-token。"""

    def _override_get_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(get_settings().security, "api_token", "test-token")
    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_no_token_401(auth_client):
    resp = auth_client.get("/api/v1/cases")
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_REQUIRED"


def test_wrong_token_403(auth_client):
    resp = auth_client.get("/api/v1/cases", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "AUTH_INVALID"


def test_valid_token_200(auth_client):
    resp = auth_client.get("/api/v1/cases", headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 200


def test_health_no_auth(auth_client):
    """why：health 是部署探针，免鉴权（§10.3 探针端点）。"""
    resp = auth_client.get("/api/v1/health")
    assert resp.status_code == 200


def test_openapi_has_bearer_scheme(auth_client):
    """why：HTTPBearer 自动注册进 components.securitySchemes → Swagger UI 显示 Authorize 按钮。"""
    openapi = auth_client.get("/openapi.json").json()
    assert "HTTPBearer" in openapi["components"]["securitySchemes"]
