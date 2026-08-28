# 全链路 request_id（§6.2 明文要求）。why：一次"创建→执行→报告"的运维追踪必须能跨
# Web 请求 / Celery 任务 / LLM 日志串联；ContextVar 承载（async 原生、线程安全），
# 跨进程（Web→Worker）靠任务参数显式透传（Worker 内重新 set 恢复）。
from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

# 当前请求上下文中的 request_id（无请求上下文时为 None，如 Worker 启动扫描）
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

HEADER_NAME = "x-request-id"


def get_request_id() -> str | None:
    """读取当前上下文 request_id。供业务代码/中间件读取。"""
    return _request_id.get()


def set_request_id(request_id: str | None) -> None:
    """注入 request_id。why：Celery 任务开头恢复链路 id（跨进程不共享 ContextVar），
    任务后续日志/LLM 调用自动携带；request_id 为空则保持原上下文（不污染）。"""
    if request_id:
        _request_id.set(request_id)


def reset_request_id() -> None:
    """清空当前上下文。why：测试隔离（ContextVar 线程级，避免串染后续用例）。"""
    _request_id.set(None)


def new_request_id() -> str:
    return uuid.uuid4().hex


class RequestIdFilter(logging.Filter):
    """日志 Filter：为每条日志 record 附加 request_id 字段，供 formatter 格式化。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True


class RequestIdFormatter(logging.Formatter):
    """why：request_id 字段缺失（Filter 未装/直连 handler）时兜底 '-'，避免 format KeyError。"""

    def format(self, record: logging.LogRecord) -> str:
        if not getattr(record, "request_id", None):
            record.request_id = "-"
        return super().format(record)


class RequestIdMiddleware:
    """ASGI 中间件：读 X-Request-ID（缺省生成 UUID）→ 注入 ContextVar → 响应头回写。

    why：客户端可自带 request_id 与自身追踪体系对齐（如前端/网关），缺省服务端生成保证恒有值；
    响应头回写让调用方拿到本次链路 id，报障时可携带定位。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = new_request_id()
        for name, value in scope.get("headers") or []:
            if name == b"x-request-id":
                rid = value.decode("latin-1").strip() or rid
                break
        token = _request_id.set(rid)

        async def _send(message) -> None:
            # 响应头回写 X-Request-ID（响应体经过时仅 response.start 需要注入）
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append(
                    (HEADER_NAME.encode("latin-1"), rid.encode("latin-1"))
                )
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            _request_id.reset(token)
