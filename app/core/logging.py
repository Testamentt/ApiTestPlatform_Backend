# 统一日志。why：格式固定便于 grep 定位（时间/级别/logger:lineno/request_id），
# request_id 由 RequestIdFilter 注入 record（缺失时兜底 '-'，review 批次 C request_id 中间件配套）。
# 两个接管点：Web 进程在 create_app 调 setup_logging；Worker 进程由 celery 的 after_setup_logger /
# after_setup_task_logger 信号调 apply_request_id_formatting（review N1：必须在 Celery 自己 dictConfig
# **之后**接管，否则被它覆盖，且不能沿用它的 format——Celery 的 format 里没有 request_id 占位符）。
from __future__ import annotations

import logging

from app.middleware.request_id import RequestIdFilter, RequestIdFormatter

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s:%(lineno)d [%(request_id)s] %(message)s"

_configured = False


def apply_request_id_formatting(logger: logging.Logger | None) -> None:
    """给 logger 已有的 handler 统一装 request_id Filter + 固定格式（幂等，可反复调用）。

    why：**强制**用 LOG_FORMAT，而不是沿用原 handler 的 format 串——Celery 默认 format
    （`[时间: 级别/进程名] 消息`）没有 `%(request_id)s`，若只替换 Formatter 对象而保留其 format，
    日志里既不会有 request_id 也不会是 §6.2 要求的格式（实测踩过：接管"成功"但输出照旧）。
    """
    if logger is None:
        return
    for handler in logger.handlers:
        if not any(isinstance(f, RequestIdFilter) for f in handler.filters):
            handler.addFilter(RequestIdFilter())
        handler.setFormatter(RequestIdFormatter(LOG_FORMAT))


def setup_logging(debug: bool = False) -> None:
    """Web 进程专用：首次调用建 handler 并接管格式（幂等，`_configured` 守卫）。"""
    global _configured
    if _configured:
        return
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, format=LOG_FORMAT)
    apply_request_id_formatting(logging.getLogger())
    _configured = True
