# 任务路由。why：POST /tasks 走 Lookup-Create（存在即返回）+ Celery 异步，立即 202。
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
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
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
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


@router.post("/tasks/{task_id}/cancel", response_model=ApiResponse[TaskRead])
def cancel_task(task_id: int, db: Session = Depends(get_db)) -> ApiResponse[TaskRead]:
    """取消未开始的任务（review N4）。why：PENDING 此前没有任何出口，永久占死 run_id。"""
    return ApiResponse(data=TaskService(db).cancel_task(task_id))


@router.post("/tasks/{task_id}/retry", response_model=ApiResponse[TaskRead], status_code=202)
def retry_task(task_id: int, db: Session = Depends(get_db)) -> ApiResponse[TaskRead]:
    """人工重试失败任务（RULES §8.5：重试仅人工触发）。why：取消/超时回收后必须有重跑出口。"""
    return ApiResponse(data=TaskService(db).retry_task(task_id))


@router.get("/tasks/{task_id}/report", response_class=HTMLResponse)
def get_task_report(task_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    """任务 HTML 报告（带鉴权，review N3）。

    why：报告含用例名与失败信息，此前走无鉴权 /static 静态挂载、task_id 自增即可枚举；
    本路由挂在带 verify_token 的 router 下（dependencies 已统一鉴权），响应为原始 HTML 而非
    业务 JSON 包装——报告本就是给人看的文档，套一层 {code,message,data} 反而要前端再解包。
    """
    return HTMLResponse(content=TaskService(db).get_report_html(task_id))
