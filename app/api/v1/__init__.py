# 本项目编码格式：UTF-8（无 BOM）。
from fastapi import APIRouter

from app.api.v1 import cases, generate, health, impact, parse, tasks

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(cases.router)
api_router.include_router(tasks.router)
api_router.include_router(health.router)
api_router.include_router(parse.router)
api_router.include_router(impact.router)
api_router.include_router(generate.router)
