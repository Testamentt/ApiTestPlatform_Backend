# 本地 mock 目标服务（httpbin 兼容子集）。why：roadmap「演示稳定性」——httpbin.org 外网不稳会让
# 执行链路演示当场翻车；本服务让 base_url 指向 127.0.0.1 即全离线可复现。
# 只覆盖执行演示常用端点（回显/状态码/延迟/鉴权）；独立脚本不入 app/（非业务代码，不进镜像 COPY）。
from __future__ import annotations

import asyncio
import json
import os

from fastapi import FastAPI, Request, Response

app = FastAPI(title="mock-target", docs_url=None, redoc_url=None)

# why：与 httpbin 对齐的响应形状（args/headers/origin/url），演示讲解时无需两套话术


def _echo_base(request: Request) -> dict:
    return {
        "args": dict(request.query_params),
        "headers": dict(request.headers),
        "origin": request.client.host if request.client else "",
        "url": str(request.url),
    }


async def _with_body(request: Request) -> dict:
    # why：JSON 解析失败降级为 data 文本回显——演示坏 body 场景仍能拿到完整请求痕迹
    raw = await request.body()
    payload = _echo_base(request)
    try:
        payload["json"] = json.loads(raw) if raw else None
    except ValueError:
        payload["json"] = None
    payload["data"] = raw.decode("utf-8", errors="replace")
    return payload


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "mock-target"}


@app.get("/get")
async def echo_get(request: Request) -> dict:
    return _echo_base(request)


@app.api_route(
    "/status/{code}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
def status_by_code(code: int, response: Response) -> dict:
    # why：4xx/5xx 场景直接驱动 expected_status 断言（如 401/403/404/500），无需真实业务逻辑
    response.status_code = code
    return {"code": code}


@app.api_route("/delay/{seconds}", methods=["GET", "POST"])
async def delay(seconds: float, request: Request) -> dict:
    # why：演示「任务超时劫持」——tasks.timeout_seconds 调小 + 本端点长延迟即触发 SUBPROCESS_TIMEOUT；
    # 上限 10s 防 mock 自身被长请求拖死。await asyncio.sleep（而非 time.sleep）：不阻塞事件循环，
    # 延迟期间其他演示请求照常服务（R2-M1，§7.2 async def 内禁阻塞调用）
    await asyncio.sleep(min(seconds, 10))
    return _echo_base(request)


@app.api_route("/bearer", methods=["GET"])
def bearer(request: Request, response: Response) -> dict:
    # why：鉴权异常用例场景——无/错误凭证 401，带任意 Authorization 头视为通过
    if not request.headers.get("authorization"):
        response.status_code = 401
        return {"detail": "UNAUTHORIZED"}
    return {"authenticated": True, **_echo_base(request)}


@app.get("/headers")
async def headers_echo(request: Request) -> dict:
    return {"headers": dict(request.headers)}


@app.post("/post")
async def echo_post(request: Request) -> dict:
    return await _with_body(request)


@app.put("/put")
@app.patch("/patch")
@app.delete("/delete")
async def echo_body(request: Request) -> dict:
    return await _with_body(request)


if __name__ == "__main__":
    import uvicorn

    # why：host 经 env 注入——compose 内网需 0.0.0.0，本地默认仅回环（不对外暴露）
    uvicorn.run(
        app,
        host=os.environ.get("MOCK_TARGET_HOST", "127.0.0.1"),
        port=int(os.environ.get("MOCK_TARGET_PORT", "9999")),
    )
