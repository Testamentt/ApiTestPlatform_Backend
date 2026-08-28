# request_id 中间件测试：响应头回写 / 客户端透传 / 日志 Filter 注入（review 批次 C，§6.2）。
from __future__ import annotations

import logging

from app.middleware.request_id import (
    RequestIdFilter,
    get_request_id,
    reset_request_id,
    set_request_id,
)


def test_response_carries_generated_request_id(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.headers.get("x-request-id")  # 未携带时服务端生成并回写


def test_client_request_id_is_echoed(client):
    r = client.get("/api/v1/health", headers={"X-Request-ID": "rid-custom-001"})
    assert r.status_code == 200
    assert r.headers.get("x-request-id") == "rid-custom-001"  # 客户端自带则透传（对齐外部追踪体系）


def test_request_id_isolated_between_requests(client):
    # why：ContextVar 每请求独立——第二个请求不沿用第一个的 id（防串染）
    first = client.get("/api/v1/health").headers.get("x-request-id")
    second = client.get("/api/v1/health").headers.get("x-request-id")
    assert first and second and first != second


def test_set_get_roundtrip():
    try:
        assert get_request_id() is None
        set_request_id("rid-task-9")
        assert get_request_id() == "rid-task-9"
    finally:
        reset_request_id()
    assert get_request_id() is None


def test_request_id_filter_injects_log_field():
    captured: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.addFilter(RequestIdFilter())
    handler.emit = lambda record: captured.append(record)  # type: ignore[method-assign]
    logger = logging.getLogger("test.rid")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        set_request_id("rid-log-x")
        logger.info("hello")
    finally:
        logger.removeHandler(handler)
        reset_request_id()
    assert captured and getattr(captured[0], "request_id", None) == "rid-log-x"


def test_request_id_filter_fallback_dash_when_absent():
    captured: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.addFilter(RequestIdFilter())
    handler.emit = lambda record: captured.append(record)  # type: ignore[method-assign]
    logger = logging.getLogger("test.rid2")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.info("no context")
    finally:
        logger.removeHandler(handler)
    assert getattr(captured[0], "request_id", None) == "-"  # 无请求上下文占位符
