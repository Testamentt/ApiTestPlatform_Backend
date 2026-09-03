# 应用入口。why：路由聚合 + 统一异常 handler + /static 挂载；lifespan 启动时同步建表（仅一次，先建表再收请求）。
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.database import init_db
from app.core.exceptions import AppError
from app.core.logging import setup_logging
from app.middleware.request_id import RequestIdMiddleware
from app.utils.prompt_util import validate_prompts

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    Path("data").mkdir(exist_ok=True)
    Path(settings.execution.workspace_dir).mkdir(exist_ok=True)
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.app.debug)
    validate_prompts()  # §3.2：prompt 缺文件/缺占位符启动即抛，不在生成任务运行期才失败（R3 批次）
    # why：StaticFiles 挂载要求目录已存在，故在 create_app 时确保目录就绪
    Path("data").mkdir(exist_ok=True)
    Path(settings.execution.workspace_dir).mkdir(exist_ok=True)
    # why：docs_url 用配置开关（§10.5 生产设 docs_enabled=false 即关 /docs），默认开供演示开箱即用
    app = FastAPI(
        title=settings.app.name,
        version=settings.app.version,
        lifespan=lifespan,
        docs_url="/docs" if settings.app.docs_enabled else None,
        redoc_url="/redoc" if settings.app.docs_enabled else None,
    )
    # why：CORS 按需挂载——frontend.cors_origins 为空（MVP 零前端）时不发 CORS 头，符合 §10.5 白名单原则
    if settings.frontend.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.frontend.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials=False,
        )
    app.include_router(api_router)
    # why：request_id 中间件要求「最先注入」（最外层）——CORS 之后 add 的中间件在最外层，
    # 保证请求一进来即生成/透传 request_id，响应头回写对任何路由/异常 handler 都生效（§6.2）
    app.add_middleware(RequestIdMiddleware)

    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "detail": exc.detail},
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        # why：兜底 500 不向客户端泄漏堆栈，只记日志
        logger.exception("未处理异常: %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"code": "INTERNAL_ERROR", "message": "服务器内部错误", "detail": None},
        )

    # why：/static 只挂 reports/ 子目录——测试文件（tasks/{id}/test_*.py）含请求参数/请求体，
    # 全目录挂载会无鉴权暴露敏感数据（review H2）；report_link 仍为 /static/{task_id}/report.html
    reports_dir = Path(settings.execution.workspace_dir) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=reports_dir), name="static")

    @app.get("/", include_in_schema=False)
    def _root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    return app


app = create_app()
