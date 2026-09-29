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


def test_swagger_hash_version_contract():
    """默认契约：hash_version=2——L3 指纹算法 md5→sha256 时按字段语义递增。

    why：旧快照（1/md5）与新解析（2/sha256）diff 时 hash_version_equal=False →
    公共接口保守全标 changed，防跨算法指纹混比（文档漂移审计 2026-09-03）。
    """
    settings = get_settings()
    assert settings.swagger.hash_version == 2


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


# ---------- P3-1：Redis 密码 URL 转义 ----------


def test_redis_password_is_url_escaped(monkeypatch):
    """why：密码含 `@ : / #` 等保留字符时会拼坏 broker URL（`p@ss` 里的 @ 会被当成 userinfo 分隔符）；
    转义后 URL 必须能被标准解析器还原出原始密码。"""
    from urllib.parse import unquote, urlsplit

    settings = get_settings()
    monkeypatch.setattr(settings.redis, "password", "p@ss:w/rd#1")
    url = settings.broker_url
    parsed = urlsplit(url)
    assert parsed.hostname == settings.redis.host  # 主机没被密码里的 @ 抢走
    assert parsed.port == settings.redis.port
    assert unquote(parsed.password or "") == "p@ss:w/rd#1"
    # 结果后端同理（共用 _redis_auth）
    assert unquote(urlsplit(settings.result_backend).password or "") == "p@ss:w/rd#1"


def test_redis_password_empty_keeps_url_clean(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings.redis, "password", "")
    assert "@" not in settings.broker_url.split("//", 1)[1]


def test_explicit_broker_url_wins(monkeypatch):
    """显式配置 broker_url 时不做任何拼接（历史行为保持）。"""
    settings = get_settings()
    monkeypatch.setattr(settings.celery, "broker_url", "redis://custom:6379/9")
    assert settings.broker_url == "redis://custom:6379/9"


# ---------- P3-2 / P3-8b / P3-8c：新增配置项默认值（默认关闭，不改变历史行为） ----------


def test_celery_queue_default_and_env_override(monkeypatch):
    settings = get_settings()
    assert settings.celery.queue == "testplatform"  # 默认即带命名空间，避免跨环境串扰
    monkeypatch.setenv("TESTPLATFORM_CELERY_QUEUE", "testplatform-ci")
    get_settings.cache_clear()
    try:
        assert get_settings().celery.queue == "testplatform-ci"
    finally:
        get_settings.cache_clear()


def test_security_optional_features_default_off():
    security = get_settings().security
    assert security.jwt_secret == ""  # JWT 通道默认关闭
    assert security.jwt_algorithm == "HS256"
    assert security.rate_limit_per_minute == 0  # 限流默认关闭
    assert security.rate_limit_window_seconds == 60
