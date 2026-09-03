# mock 目标服务测试（demo 脚本但属执行链路演示依赖，review R2-M1）：
# /delay 不得阻塞事件循环（time.sleep 会卡死并发请求）+ 状态码/鉴权端点行为。
from __future__ import annotations

import asyncio
import importlib.util
import time
from pathlib import Path

import httpx
import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "mock_target.py"
_spec = importlib.util.spec_from_file_location("mock_target", _SCRIPT)
mock_target = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mock_target)


@pytest.fixture()
def client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mock_target.app), base_url="http://mock"
    )


def test_delay_does_not_block_event_loop(client):
    # why：/delay 曾用 time.sleep 阻塞事件循环（R2-M1）——延迟期间并发请求全部卡死。
    # 计时从「发起 /delay/1 之前」开始：阻塞实现下 /get 被拖到延迟结束（elapsed≈1s+），
    # async 实现下 /get 在 0.1s 后即返回（<0.5s）
    async def scenario():
        async with client as c:
            start = time.monotonic()
            delay_task = asyncio.create_task(c.get("/delay/1"))
            await asyncio.sleep(0.1)  # 让 /delay 先进入 sleep
            r_get = await c.get("/get")
            elapsed = time.monotonic() - start
            r_delay = await delay_task
            return r_get, r_delay, elapsed

    r_get, r_delay, elapsed = asyncio.run(scenario())
    assert r_get.status_code == 200
    assert r_delay.status_code == 200
    assert elapsed < 0.5, f"/get 被 /delay 阻塞了 {elapsed:.2f}s（事件循环被 sleep 卡住）"


def test_status_by_code_drives_expected_status(client):
    # why：4xx/5xx 场景靠 /status/{code} 直接驱动 expected_status 断言
    async def scenario():
        async with client as c:
            return (await c.get("/status/404")).status_code, (
                await c.post("/status/500")
            ).status_code

    assert asyncio.run(scenario()) == (404, 500)


def test_bearer_requires_auth_header(client):
    # why：鉴权异常用例场景——无凭证 401，带任意 Authorization 视为通过
    async def scenario():
        async with client as c:
            no_token = await c.get("/bearer")
            with_token = await c.get("/bearer", headers={"Authorization": "Bearer x"})
            return no_token.status_code, with_token.status_code

    assert asyncio.run(scenario()) == (401, 200)
