# 依赖注入。why：get_db 每个请求独立会话，try-finally 保证关闭（泄漏会锁库）；
# verify_token 统一 Bearer Token 鉴权（§10.3 Phase 4：无凭证 401 / 凭证错误 403，health 免鉴权作探针）。
from __future__ import annotations

import hmac
from collections.abc import Iterator

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.exceptions import AppError

bearer_scheme = HTTPBearer(auto_error=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    """统一鉴权依赖（挂到 5 个业务路由的 dependencies；health 免鉴权）。

    why：auto_error=False 让无凭证请求走到这里由我们手动判 401（而非框架自动抛错），
    从而区分「无凭证 401 / 凭证无效 403」且响应格式统一为 {code, message, detail}；
    token 值走配置（§3.1：dev 默认仅供演示，生产必须 env 覆盖）。
    """
    expected = get_settings().security.api_token
    if credentials is None:
        raise AppError("AUTH_REQUIRED", "缺少 Bearer Token", status_code=401)
    # hmac.compare_digest：常数时间比较防时序探测（L2）；encode 成 bytes——str 比较要求双方 ASCII，
    # 非 ASCII token 会抛 TypeError→500，bytes 比较则正确返回 False→403（R3 批次）
    if not hmac.compare_digest(credentials.credentials.encode(), expected.encode()):
        raise AppError("AUTH_INVALID", "Token 无效", status_code=403)
