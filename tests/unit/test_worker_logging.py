# Worker 日志接管测试（review N1）。why：全链路 request_id 的可观测性靠"Worker 进程真的装了
# Filter/Formatter 且格式串含 request_id"这一事实维持——此前只有 Web 侧安装，Worker 日志既无统一格式
# 也无 request_id；且实测发现"只换 Formatter 对象、保留 Celery 的 format 串"等于没接管（格式里没有占位符）。
from __future__ import annotations

import io
import logging

from app.core import logging as core_logging
from app.middleware.request_id import (
    RequestIdFilter,
    RequestIdFormatter,
    reset_request_id,
    set_request_id,
)


def _isolated_root(monkeypatch, stream: io.StringIO) -> logging.Handler:
    """把 root 的 handlers 换成测试独占的 handler，避免污染 pytest 自己的捕获 handler。"""
    handler = logging.StreamHandler(stream)
    monkeypatch.setattr(logging.getLogger(), "handlers", [handler])
    return handler


def test_setup_logging_installs_request_id_formatter(monkeypatch):
    stream = io.StringIO()
    handler = _isolated_root(monkeypatch, stream)
    monkeypatch.setattr(core_logging, "_configured", False)
    try:
        core_logging.setup_logging(False)
        set_request_id("rid-worker-1")
        logging.getLogger("app.services.execution_service").info("任务开始")
    finally:
        reset_request_id()
    output = stream.getvalue()
    assert "[rid-worker-1]" in output  # request_id 真进了格式串（不只是 Filter 挂着）
    assert "app.services.execution_service" in output  # logger:lineno 统一格式
    assert isinstance(handler.filters[0], RequestIdFilter)
    assert isinstance(handler.formatter, RequestIdFormatter)


def test_missing_request_id_falls_back_to_dash(monkeypatch):
    stream = io.StringIO()
    _isolated_root(monkeypatch, stream)
    monkeypatch.setattr(core_logging, "_configured", False)
    reset_request_id()
    core_logging.setup_logging(False)
    logging.getLogger("app.utils.llm_client").info("无上下文调用")
    assert "[-]" in stream.getvalue()  # 无请求上下文（如 Worker 启动扫描）不抛 KeyError


def test_apply_formatting_overrides_foreign_format(monkeypatch):
    """N1 回归核心：接管 Celery 风格 handler 时**必须换掉格式串**。

    why：Celery 默认 format 是 `[时间: 级别/进程名] 消息`，没有 `%(request_id)s`——
    只替换 Formatter 对象、沿用它的 format，日志里永远打不出 request_id（首次修复的真实翻车点）。
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s: %(levelname)s/%(processName)s] %(message)s")
    )
    logger = logging.getLogger("celery.fake_for_test")
    monkeypatch.setattr(logger, "handlers", [handler])
    core_logging.apply_request_id_formatting(logger)
    try:
        set_request_id("rid-celery-style")
        logger.info("celery 风格样本")
    finally:
        reset_request_id()
    output = stream.getvalue()
    assert "[rid-celery-style]" in output
    assert "celery.fake_for_test" in output
    assert "processName" not in output  # Celery 的格式串必须被替换掉


def test_apply_formatting_is_idempotent(monkeypatch):
    handler = logging.StreamHandler(io.StringIO())
    logger = logging.getLogger("celery.fake_idem")
    monkeypatch.setattr(logger, "handlers", [handler])
    core_logging.apply_request_id_formatting(logger)
    core_logging.apply_request_id_formatting(logger)  # 反复调用（信号可能多次触发）
    assert sum(isinstance(f, RequestIdFilter) for f in handler.filters) == 1
    assert isinstance(handler.formatter, RequestIdFormatter)


def test_apply_formatting_none_logger_is_noop():
    core_logging.apply_request_id_formatting(None)  # 信号可能不带 logger（不抛即通过）


def test_setup_logging_is_idempotent(monkeypatch):
    stream = io.StringIO()
    handler = _isolated_root(monkeypatch, stream)
    monkeypatch.setattr(core_logging, "_configured", False)
    core_logging.setup_logging(False)
    first_formatter = handler.formatter
    core_logging.setup_logging(False)
    assert handler.formatter is first_formatter  # 第二次调用直接返回，不重复包装


def test_after_setup_logger_hook_configures_logging(monkeypatch):
    """Worker 启动钩子必须真的接管日志（本项目最容易漏的就是这一根线）。"""
    called: list[bool] = []
    monkeypatch.setattr("app.celery_app.configure_worker_logging", lambda: called.append(True))
    from app.celery_app import _on_after_setup_logger, _on_after_setup_task_logger

    _on_after_setup_logger(logger=logging.getLogger())
    _on_after_setup_task_logger(logger=logging.getLogger("celery"))
    assert called == [True, True]


def test_worker_ready_only_triggers_scan(monkeypatch):
    """worker_ready 只负责启动扫描（日志接管已交给 celery 的 logging 信号）。"""

    def _forbidden() -> None:
        raise AssertionError("日志接管不应再挂在 worker_ready")

    monkeypatch.setattr("app.celery_app.configure_worker_logging", _forbidden)
    monkeypatch.setattr("app.tasks.supervisor.scan_stale_tasks", _FakeTask())
    from app.celery_app import _on_worker_ready

    _on_worker_ready()  # 不抛即通过


class _FakeTask:
    def delay(self) -> None:
        return None


# ---------- P3-5：Worker stdio 切 UTF-8 ----------


class _Stream:
    def __init__(self, *, support: bool = True) -> None:
        self.support = support
        self.calls: list[dict] = []

    def reconfigure(self, **kwargs):
        if not self.support:
            raise ValueError("stream closed")
        self.calls.append(kwargs)


def test_worker_streams_reconfigured_to_utf8(monkeypatch):
    """why：Windows 控制台/重定向默认 cp936——Worker 中文日志乱码，编码异常时还会整行丢失（P3-5）。"""
    import sys

    from app.celery_app import _ensure_utf8_streams

    out, err = _Stream(), _Stream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    _ensure_utf8_streams()
    assert out.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert err.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_worker_stream_without_reconfigure_is_skipped(monkeypatch):
    """被替换过的流（如 CI 捕获对象）没有 reconfigure：跳过而不能报错。"""
    import sys

    from app.celery_app import _ensure_utf8_streams

    monkeypatch.setattr(sys, "stdout", object())
    monkeypatch.setattr(sys, "stderr", object())
    _ensure_utf8_streams()  # 不抛即通过


def test_worker_stream_reconfigure_failure_is_tolerated(monkeypatch):
    """流已关闭等异常：记 warning 但不影响启动。"""
    import sys

    from app.celery_app import _ensure_utf8_streams

    monkeypatch.setattr(sys, "stdout", _Stream(support=False))
    monkeypatch.setattr(sys, "stderr", _Stream(support=False))
    _ensure_utf8_streams()  # 不抛即通过
