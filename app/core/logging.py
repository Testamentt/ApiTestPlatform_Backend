# 统一日志。why：格式固定便于 grep 定位（时间/级别/logger:lineno），禁用 print 输出日志。
from __future__ import annotations

import logging

_configured = False


def setup_logging(debug: bool = False) -> None:
    global _configured
    if _configured:
        return
    fmt = "%(asctime)s %(levelname)s %(name)s:%(lineno)d %(message)s"
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, format=fmt)
    _configured = True
