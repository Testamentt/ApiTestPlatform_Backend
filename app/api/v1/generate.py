# AI 生成路由。why：POST 立即 202（异步解耦，LLM 不阻塞 Web）+ GET 轮询进度。
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db, verify_token
from app.schemas.common import ApiResponse
from app.schemas.generate import GenerateRequest, GenerateTaskRead
from app.services.generation_service import GenerationService

router = APIRouter(tags=["generate"], dependencies=[Depends(verify_token)])


@router.post("/generate", response_model=ApiResponse[GenerateTaskRead], status_code=202)
def create_generation(
    payload: GenerateRequest, db: Session = Depends(get_db)
) -> ApiResponse[GenerateTaskRead]:
    task = GenerationService(db).create_generation_task(
        payload.document, payload.operation_ids, payload.force_full
    )
    return ApiResponse(data=task)


@router.get("/generate/{generation_task_id}", response_model=ApiResponse[GenerateTaskRead])
def get_generation(
    generation_task_id: int, db: Session = Depends(get_db)
) -> ApiResponse[GenerateTaskRead]:
    task = GenerationService(db).get_generation_task(generation_task_id)
    return ApiResponse(data=task)
