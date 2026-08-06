# 业务异常基类。why：把「业务失败原因」与 HTTP 状态解耦，
# 统一走异常体系返回 {code, message, detail}，路由不裸 return 错误字典。
from __future__ import annotations


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        status_code: int = 400,
        detail: str | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.message = message or code
        self.detail = detail
        super().__init__(self.message)
