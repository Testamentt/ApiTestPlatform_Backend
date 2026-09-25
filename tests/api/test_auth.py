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


def test_report_requires_auth(auth_client):
    """why：报告含用例名与失败信息，必须走鉴权（review N3）——无凭证一律 401，不得有无鉴权旁路。"""
    resp = auth_client.get("/api/v1/tasks/1/report")
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_REQUIRED"


def test_openapi_has_bearer_scheme(auth_client):
    """why：HTTPBearer 自动注册进 components.securitySchemes → Swagger UI 显示 Authorize 按钮。"""
    openapi = auth_client.get("/openapi.json").json()
    assert "HTTPBearer" in openapi["components"]["securitySchemes"]


# ---------- JWT 通道（P3-8b）：与静态 token 并存，可配置开关 ----------


def _jwt(secret: str, *, expires_in: int = 60, algorithm: str = "HS256", **claims) -> str:
    import time

    import jwt

    payload = {"sub": "alice", "exp": int(time.time()) + expires_in, **claims}
    return jwt.encode(payload, secret, algorithm=algorithm)


def test_jwt_disabled_by_default(auth_client, monkeypatch):
    """未配置 jwt_secret 时 JWT 不被接受（保持历史行为，只认静态 token）。"""
    from app.core.config import get_settings

    token = _jwt("any-secret-0123456789abcdef")
    resp = auth_client.get("/api/v1/cases", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert get_settings().security.jwt_secret == ""


def test_valid_jwt_accepted(auth_client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings().security, "jwt_secret", "unit-test-secret-0123456789abcdef")
    resp = auth_client.get(
        "/api/v1/cases",
        headers={"Authorization": f"Bearer {_jwt('unit-test-secret-0123456789abcdef')}"},
    )
    assert resp.status_code == 200


def test_expired_jwt_rejected(auth_client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings().security, "jwt_secret", "unit-test-secret-0123456789abcdef")
    resp = auth_client.get(
        "/api/v1/cases",
        headers={
            "Authorization": f"Bearer {_jwt('unit-test-secret-0123456789abcdef', expires_in=-10)}"
        },
    )
    assert resp.status_code == 403


def test_jwt_wrong_signature_rejected(auth_client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings().security, "jwt_secret", "unit-test-secret-0123456789abcdef")
    resp = auth_client.get(
        "/api/v1/cases",
        headers={"Authorization": f"Bearer {_jwt('another-secret-0123456789abcdef')}"},
    )
    assert resp.status_code == 403


def test_jwt_alg_none_rejected(auth_client, monkeypatch):
    """why：`alg=none` 降级攻击——Token 不带签名也必须被拒（algorithms 白名单挡住）。"""
    import jwt
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings().security, "jwt_secret", "unit-test-secret-0123456789abcdef")
    forged = jwt.encode({"sub": "mallory", "exp": 9999999999}, key="", algorithm="none")
    resp = auth_client.get("/api/v1/cases", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 403


def test_jwt_without_exp_rejected(auth_client, monkeypatch):
    """why：`require exp`——不接受永不过期令牌。"""
    import jwt
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings().security, "jwt_secret", "unit-test-secret-0123456789abcdef")
    token = jwt.encode({"sub": "alice"}, "unit-test-secret-0123456789abcdef", algorithm="HS256")
    resp = auth_client.get("/api/v1/cases", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_jwt_issuer_and_audience_enforced(auth_client, monkeypatch):
    from app.core.config import get_settings

    security = get_settings().security
    monkeypatch.setattr(security, "jwt_secret", "unit-test-secret-0123456789abcdef")
    monkeypatch.setattr(security, "jwt_issuer", "testplatform")
    monkeypatch.setattr(security, "jwt_audience", "testplatform-api")
    good = _jwt("unit-test-secret-0123456789abcdef", iss="testplatform", aud="testplatform-api")
    bad_iss = _jwt("unit-test-secret-0123456789abcdef", iss="other", aud="testplatform-api")
    headers = lambda token: {"Authorization": f"Bearer {token}"}  # noqa: E731
    assert auth_client.get("/api/v1/cases", headers=headers(good)).status_code == 200
    assert auth_client.get("/api/v1/cases", headers=headers(bad_iss)).status_code == 403
