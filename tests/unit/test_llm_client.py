# llm_client 单测（mock OpenAI）：成功/可重试耗尽/校验失败带 raw/非 JSON 兜底/参数断言。
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from app.core.exceptions import AppError
from app.utils.llm_client import LlmClient
from pydantic import BaseModel, ConfigDict


class _FakeSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool


class _FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        # 循环取最后一个响应（重试场景可复用抛异常）
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[idx]()


def _resp(content, tokens=(100, 50, 150)):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=tokens[0], completion_tokens=tokens[1], total_tokens=tokens[2]
        ),
    )


def _make_client(monkeypatch, completions) -> LlmClient:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr("app.utils.llm_client.time.sleep", lambda _s: None)  # 不真实等待
    client = LlmClient()
    client.client.chat.completions = completions
    return client


def test_chat_json_success(monkeypatch):
    fake = _FakeCompletions([lambda: _resp(json.dumps({"ok": True}))])
    client = _make_client(monkeypatch, fake)
    parsed, usage = client.chat_json("sys", "user", schema=_FakeSchema)
    assert parsed.ok is True
    assert usage.total_tokens == 150
    assert usage.prompt_tokens == 100


def test_chat_json_params_fixed(monkeypatch):
    fake = _FakeCompletions([lambda: _resp(json.dumps({"ok": True}))])
    client = _make_client(monkeypatch, fake)
    client.chat_json("sys", "user", schema=_FakeSchema)
    kwargs = fake.calls[0]
    assert kwargs["temperature"] == 0  # 确定性（RULES §9.3）
    assert kwargs["response_format"] == {"type": "json_object"}  # 结构化输出
    assert kwargs["max_tokens"] == 4096
    assert kwargs["model"] == "deepseek-chat"


def test_chat_json_retry_then_fail(monkeypatch):
    # why：openai 异常构造需要有效 response/request（内部访问 headers），mock 全量
    import httpx
    from openai import RateLimitError

    _req = SimpleNamespace(headers=httpx.Headers({}), method="POST", url="http://x")
    _resp = SimpleNamespace(request=_req, headers=httpx.Headers({}), status_code=429)

    def boom():
        raise RateLimitError("rate limited", response=_resp, body=None)

    fake = _FakeCompletions([boom])
    client = _make_client(monkeypatch, fake)
    with pytest.raises(AppError) as ei:
        client.chat_json("sys", "user", schema=_FakeSchema)
    assert ei.value.code == "LLM_FAILED"
    assert len(fake.calls) == 4  # 首次尝试 + 3 次重试（§9.4）


def test_chat_json_retries_5xx_then_success(monkeypatch):
    # 5xx → 可重试 → 重试后成功（APIStatusError 分支）
    import httpx
    from openai import APIStatusError

    _req = SimpleNamespace(headers=httpx.Headers({}), method="POST", url="http://x")
    _err = SimpleNamespace(request=_req, headers=httpx.Headers({}), status_code=500)

    def fivexx():
        raise APIStatusError("server error", response=_err, body=None)

    fake = _FakeCompletions([fivexx, lambda: _resp(json.dumps({"ok": True}))])
    client = _make_client(monkeypatch, fake)
    parsed, _ = client.chat_json("sys", "user", schema=_FakeSchema)
    assert parsed.ok is True
    assert len(fake.calls) == 2  # 1 次失败 + 1 次成功


def test_chat_json_4xx_no_retry(monkeypatch):
    # 4xx（<500）→ 立即拒绝不重试（永久错误不浪费重试预算）
    import httpx
    from openai import APIStatusError

    _req = SimpleNamespace(headers=httpx.Headers({}), method="POST", url="http://x")
    _err = SimpleNamespace(request=_req, headers=httpx.Headers({}), status_code=400)

    def fourxx():
        raise APIStatusError("bad request", response=_err, body=None)

    fake = _FakeCompletions([fourxx])
    client = _make_client(monkeypatch, fake)
    with pytest.raises(AppError) as ei:
        client.chat_json("sys", "user", schema=_FakeSchema)
    assert ei.value.code == "LLM_FAILED"
    assert len(fake.calls) == 1  # 4xx 不重试


def test_chat_json_input_too_long_rejected(monkeypatch):
    # §9.3 输入长度预检：超限调用前拒绝，不裸奔
    from app.core.config import get_settings

    fake = _FakeCompletions([lambda: _resp(json.dumps({"ok": True}))])
    client = _make_client(monkeypatch, fake)
    limit = get_settings().llm.max_input_chars
    with pytest.raises(AppError) as ei:
        client.chat_json("s" * (limit + 1), "user", schema=_FakeSchema)
    assert ei.value.code == "LLM_INPUT_TOO_LONG"
    assert len(fake.calls) == 0  # 未发起调用


def test_chat_json_validation_failed_carries_raw(monkeypatch):
    raw = json.dumps({"wrong_key": 1})  # extra="forbid" → 校验失败
    fake = _FakeCompletions([lambda: _resp(raw)])
    client = _make_client(monkeypatch, fake)
    with pytest.raises(AppError) as ei:
        client.chat_json("sys", "user", schema=_FakeSchema)
    assert ei.value.code == "LLM_VALIDATION_FAILED"
    assert ei.value.raw_response == raw  # 间隙 2：raw_response 随异常携带
    assert len(fake.calls) == 1  # 校验失败不重试


def test_chat_json_extracts_markdown_fenced_json(monkeypatch):
    # 细节 1：DeepSeek 偶发把 JSON 包在 ```json 代码块，_extract_json 剥离后成功
    fenced = "```json\n" + json.dumps({"ok": True}) + "\n```"
    fake = _FakeCompletions([lambda: _resp(fenced)])
    client = _make_client(monkeypatch, fake)
    parsed, _ = client.chat_json("sys", "user", schema=_FakeSchema)
    assert parsed.ok is True


def test_chat_json_invalid_json_raises(monkeypatch):
    fake = _FakeCompletions([lambda: _resp("not json at all")])
    client = _make_client(monkeypatch, fake)
    with pytest.raises(AppError) as ei:
        client.chat_json("sys", "user", schema=_FakeSchema)
    assert ei.value.code == "LLM_VALIDATION_FAILED"
