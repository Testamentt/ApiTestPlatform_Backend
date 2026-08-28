# 统一日志。why：格式固定便于 grep 定位（时间/级别/logger:lineno/request_id），
# request_id 由 RequestIdFilter 注入 record（缺失时兜底 '-'，review 批次 C request_id 中间件配套）。
from __future__ import annotations

import logging

from app.middleware.request_id import RequestIdFilter, RequestIdFormatter

_configured = False


def setup_logging(debug: bool = False) -> None:
    global _configured
    if _configured:
        return
    fmt = "%(asctime)s %(levelname)s %(name)s:%(lineno)d [%(request_id)s] %(message)s"
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, format=fmt)
    # why：Filter 挂 root handler——所有子 logger 的 record 冒泡到 root 时统一注入
    # request_id 字段；Formatter 用自定义兜底（record 无字段时 '-'，不抛 KeyError）
    root = logging.getLogger()
    for handler in root.handlers:
        handler.addFilter(RequestIdFilter())
        # basicConfig 总会给首个 handler 配默认 formatter，但防御性取 None 分支
        current_fmt = getattr(handler, "formatter", None)
        handler.setFormatter(RequestIdFormatter(getattr(current_fmt, "_fmt", None) or fmt))
    _configured = True
