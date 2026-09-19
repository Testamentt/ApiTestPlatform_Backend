# 依赖注入。why：get_db 每个请求独立会话，try-finally 保证关闭（泄漏会锁库）；
# verify_token 统一「准入控制」——Bearer 鉴权（静态 token 或 JWT）+ 可选限流（§10.3 Phase 4；
# JWT 与限流见 P3-8b/P3-8c，默认关闭以保持历史行为）。
from __future__ import annotations

import hmac
import logging
from collections.abc import Iterator

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.exceptions import AppError
from app.utils.rate_limit import hash_identity, hit_count

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _verify_jwt(token: str) -> bool:
    """校验 HS256 JWT（P3-8b）。why：静态 api_token 适合机器与演示，人工使用需要「可过期凭证」；
    这里只做验签 + 过期/生效时间 + 可选 iss/aud，不负责颁发（登录端点留给后续）。

    - `security.jwt_secret` 为空 = 关闭 JWT 通道（默认，保持历史行为）
    - `algorithms` 显式白名单 → 防 `alg=none` 类降级攻击
    - `require exp` → 不接受永不过期令牌
    """
    settings = get_settings().security
    if not settings.jwt_secret:
        return False
    try:
        jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer or None,
            audience=settings.jwt_audience or None,
            options={"require": ["exp"]},
        )
        return True
    except jwt.PyJWTError as e:
        # why：验签失败属"凭证无效"的正常分支，不是异常事故——info 级即可（不刷堆栈）
        logger.info("JWT 校验未通过: %s", e)
        return False


def _enforce_rate_limit(credentials: HTTPAuthorizationCredentials | None, request: Request) -> None:
    """固定窗口限流（P3-8c）。why：见 `app/utils/rate_limit.py` 模块注释；此处只做主体识别与判定。

    主体优先取令牌哈希（按 IP 会被 NAT 合并成同一个桶），无令牌才退化为客户端 IP。
    """
    settings = get_settings().security
    limit = settings.rate_limit_per_minute
    if limit <= 0:
        return  # 默认关闭：不引入行为变化，也不碰 Redis
    if credentials is not None and credentials.credentials:
        identity = "tok:" + hash_identity(credentials.credentials)
    else:
        host = request.client.host if request.client else "unknown"
        identity = "ip:" + hash_identity(host)
    count = hit_count(identity, window_seconds=settings.rate_limit_window_seconds)
    if count is not None and count > limit:
        raise AppError(
            "RATE_LIMITED",
            status_code=429,
            detail=f"超过 {limit} 次/{settings.rate_limit_window_seconds}s 限额，请稍后重试",
        )


def verify_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    """统一鉴权 + 限流依赖（挂到业务路由的 dependencies；health 免鉴权也不限流）。

    why：auto_error=False 让无凭证请求走到这里由我们手动判 401（而非框架自动抛错），
    从而区分「无凭证 401 / 凭证无效 403」且响应格式统一为 {code, message, detail}；
    令牌接受两种形态——配置里的静态 api_token（dev 默认仅供演示），或启用 `security.jwt_secret` 后的 HS256 JWT。
    """
    expected = get_settings().security.api_token
    if credentials is None:
        raise AppError("AUTH_REQUIRED", "缺少 Bearer Token", status_code=401)
    # hmac.compare_digest：常数时间比较防时序探测（L2）；encode 成 bytes——str 比较要求双方 ASCII，
    # 非 ASCII token 会抛 TypeError→500，bytes 比较则正确返回 False→403（R3 批次）
    token = credentials.credentials
    if not hmac.compare_digest(token.encode(), expected.encode()) and not _verify_jwt(token):
        raise AppError("AUTH_INVALID", "Token 无效", status_code=403)
    _enforce_rate_limit(credentials, request)
