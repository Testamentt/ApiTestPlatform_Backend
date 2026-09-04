# 任务出入参。why：TaskCreate 只收 case_ids 快照 + 超时；results 读 result_summary（无独立结果表）。
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TaskCreate(BaseModel):
    case_ids: list[int] = Field(min_length=1, max_length=500)  # 上限防 IN(...) 撑爆（review M5）
    # why default=None：缺省回退 config.execution.pytest_timeout（红线 3：timeout 值来自 config，
    # 不在 schema 硬编码副本，review R3-6）
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: str
    case_ids: list
    timeout_seconds: int
    status: str
    pid: int | None
    celery_task_id: str | None
    error_stage: str | None
    error_msg: str | None
    started_at: datetime | None
    finished_at: datetime | None
    result_summary: dict | None
    report_link: str | None
    created_at: datetime
    updated_at: datetime


class TaskResultItem(BaseModel):
    case_id: int
    name: str = ""
    status: str
    failure_msg: str | None = None


class TaskResults(BaseModel):
    task: TaskRead
    results: list[TaskResultItem]
