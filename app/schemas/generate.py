# AI 生成出入参。why：GeneratedCase 是 LLM 输出校验 schema（extra="forbid" 严格校验，RULES §10.2）；
# operation_id 不由 LLM 生成、由服务端注入（防血缘被污染，§11.2）。
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.assertion import AssertionItem
from app.schemas.case import HTTP_METHODS


class GeneratedCase(BaseModel):
    # 【面试锚点】operation_id 不由 LLM 生成，由服务端注入；
    # LLM 输出若包含 operation_id 字段，extra="forbid" 会直接判失败（防血缘被污染）
    model_config = ConfigDict(extra="forbid")

    # max_length 对齐 test_cases 表列长（name 255 / path 1024，review L8）——
    # LLM 超长输出在 Pydantic 校验层拦截，避免落库时被 DB 截断/报错
    name: str = Field(min_length=1, max_length=255)
    method: HTTP_METHODS
    path: str = Field(min_length=1, max_length=1024)
    params: dict = {}
    body: dict | None = None
    expected_status: int
    # why：LLM 断言输出同样逐条过 AssertionItem（path/op 白名单，extra=forbid）——
    # 非法断言在 LLM 校验层即整 case 拒绝，不落库不渲染
    assertions: list[AssertionItem] = []


class GeneratedCaseList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[GeneratedCase]


class GenerateRequest(BaseModel):
    document: dict
    operation_ids: list[str] | None = None  # 定向子集；缺省智能决策
    force_full: bool = False  # 全量重建（忽略 untested，仅在 operation_ids 未传时生效）


class GenerateTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: str
    status: str
    operation_count: int
    celery_task_id: str | None  # 卡死时可通过 Celery 定位/revoke（RULES §8.3）
    prompt_version: str | None
    error_stage: str | None
    error_msg: str | None
    result_summary: dict | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
