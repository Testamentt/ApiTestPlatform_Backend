# 应用入口。why：路由聚合 + 统一异常 handler + /static 挂载；lifespan 启动时同步建表（仅一次，先建表再收请求）。
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.database import init_db
from app.core.exceptions import AppError
from app.core.logging import setup_logging

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
    # why：StaticFiles 挂载要求目录已存在，故在 create_app 时确保目录就绪
    Path("data").mkdir(exist_ok=True)
    Path(settings.execution.workspace_dir).mkdir(exist_ok=True)
    app = FastAPI(title=settings.app.name, version=settings.app.version, lifespan=lifespan)
    app.include_router(api_router)

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

    app.mount(
        "/static", StaticFiles(directory=settings.execution.workspace_dir), name="static"
    )

    @app.get("/", include_in_schema=False)
    def _root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    return app


app = create_app()
