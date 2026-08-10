# 任务路由。why：POST /tasks 走 Lookup-Create（存在即返回）+ Celery 异步，立即 202。
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db, verify_token
from app.schemas.common import ApiResponse, Page
from app.schemas.task import TaskCreate, TaskRead, TaskResults
from app.services.task_service import TaskService

router = APIRouter(tags=["tasks"], dependencies=[Depends(verify_token)])


@router.post("/tasks", response_model=ApiResponse[TaskRead], status_code=202)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)) -> ApiResponse[TaskRead]:
    task = TaskService(db).create_execution_task(payload)
    return ApiResponse(data=task)


@router.get("/tasks", response_model=ApiResponse[Page[TaskRead]])
def list_tasks(
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    db: Session = Depends(get_db),
) -> ApiResponse[Page[TaskRead]]:
    items, total = TaskService(db).list_tasks(page=page, page_size=page_size, status=status)
    return ApiResponse(data=Page(items=items, total=total, page=page, page_size=page_size))


@router.get("/tasks/{task_id}", response_model=ApiResponse[TaskRead])
def get_task(task_id: int, db: Session = Depends(get_db)) -> ApiResponse[TaskRead]:
    task = TaskService(db).get_task(task_id)
    return ApiResponse(data=task)


@router.get("/tasks/{task_id}/results", response_model=ApiResponse[TaskResults])
def get_task_results(task_id: int, db: Session = Depends(get_db)) -> ApiResponse[TaskResults]:
    results = TaskService(db).get_task_results(task_id)
    return ApiResponse(data=results)
