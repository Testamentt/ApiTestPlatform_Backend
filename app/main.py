# 应用入口。why：路由聚合 + 统一异常 handler（业务/入参/兜底三层）；lifespan 启动时同步建表（仅一次，先建表再收请求）。
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import ValidationError

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.database import init_db
from app.core.exceptions import AppError
from app.core.logging import setup_logging
from app.middleware.request_id import RequestIdMiddleware
from app.utils.prompt_util import validate_prompts

logger = logging.getLogger(__name__)


def format_validation_errors(errors: list[dict]) -> list[dict]:
    """把 pydantic/FastAPI 的字段错误整理成前端可直接用的结构。

    why：只保留 field/message/type 三类信息——框架原始错误里带 `input`（回显用户输入，可能是密码或大对象）
    与 `ctx`（可能含异常对象，不可 JSON 序列化），直接透出既有泄漏风险也会让序列化炸掉（P2-1）。
    """
    formatted: list[dict] = []
    for error in errors:
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        formatted.append(
            {
                "field": location or "body",
                "message": str(error.get("msg", "")),
                "type": str(error.get("type", "")),
            }
        )
    return formatted


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

    @app.exception_handler(RequestValidationError)
    async def _request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """入参校验失败 → 统一响应结构（P2-1，此前是框架的 `{"detail": [...]}`）。

        why：契约统一后前端不必再为「框架 422 长什么样」写兜底分支；detail 给出逐字段错误，
        前端可精确高亮。状态码仍是 422，语义不变。
        """
        return JSONResponse(
            status_code=422,
            content={
                "code": "REQUEST_VALIDATION_ERROR",
                "message": "请求参数校验失败",
                "detail": format_validation_errors(exc.errors()),
            },
        )

    @app.exception_handler(ValidationError)
    async def _pydantic_validation_handler(request: Request, exc: ValidationError) -> JSONResponse:
        """服务层内部构造出的 Pydantic 校验失败 → 422 而不是 500（P2-1 兜底）。

        why：典型场景是服务层复用入参 schema 自行组装（如影响分析一键回归把 >500 条用例塞进 TaskCreate）——
        这类错误此前直接冒泡成 500，客户端看不到任何可行动信息。此 handler 只做兜底：
        真正的边界处理在调用点（`ImpactService.regression` 已改为显式 422 `TOO_MANY_CASES`）。
        """
        logger.warning("服务层校验失败: %s", request.url.path)
        return JSONResponse(
            status_code=422,
            content={
                "code": "INTERNAL_VALIDATION_ERROR",
                "message": "内部数据未通过校验，请检查输入规模或联系维护者",
                "detail": format_validation_errors(exc.errors()),
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        # why：兜底 500 不向客户端泄漏堆栈，只记日志
        logger.exception("未处理异常: %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"code": "INTERNAL_ERROR", "message": "服务器内部错误", "detail": None},
        )

    # why：不再挂载 /static——报告改由带鉴权的 GET /api/v1/tasks/{id}/report 读取（review N3）：
    # 无鉴权静态挂载会随 task_id 自增被枚举，而报告含用例名与失败信息；
    # 测试文件（tasks/{id}/test_*.py 含请求参数/请求体）本来就必须留在挂载面之外（review H2）

    @app.get("/", include_in_schema=False)
    def _root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    return app


app = create_app()
