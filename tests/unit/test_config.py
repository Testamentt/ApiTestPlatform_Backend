"""Phase 4 配置建模测试。

why：验证新增 security / frontend / app.docs_enabled 的默认值契约与
TESTPLATFORM_* env 覆盖链路（§3.1 配置契约三处同步）。
"""

from app.core.config import get_settings


def test_phase4_defaults():
    """默认契约：/docs 开、dev token、CORS 空白名单（MVP 零前端）。"""
    settings = get_settings()
    assert settings.app.docs_enabled is True
    assert settings.security.api_token == "testplatform-dev-token"
    assert settings.frontend.cors_origins == []


def test_env_override_security_token(monkeypatch):
    """生产必须经 env 覆盖 token（§3.1：dev 默认仅供演示）。"""
    monkeypatch.setenv("TESTPLATFORM_SECURITY_API_TOKEN", "ci-token")
    get_settings.cache_clear()
    try:
        assert get_settings().security.api_token == "ci-token"
    finally:
        get_settings.cache_clear()


def test_env_override_docs_disabled(monkeypatch):
    """生产关 /docs（§10.5）。"""
    monkeypatch.setenv("TESTPLATFORM_APP_DOCS_ENABLED", "false")
    get_settings.cache_clear()
    try:
        assert get_settings().app.docs_enabled is False
    finally:
        get_settings.cache_clear()


def test_env_override_cors_origins(monkeypatch):
    """CORS 白名单 env 注入（列表解析走 _parse_value）。"""
    monkeypatch.setenv("TESTPLATFORM_FRONTEND_CORS_ORIGINS", '["http://localhost:5173"]')
    get_settings.cache_clear()
    try:
        assert get_settings().frontend.cors_origins == ["http://localhost:5173"]
    finally:
        get_settings.cache_clear()
