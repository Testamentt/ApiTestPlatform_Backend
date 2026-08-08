# AI 生成出入参。why：GeneratedCase 是 LLM 输出校验 schema（extra="forbid" 严格校验，RULES §10.2）；
# operation_id 不由 LLM 生成、由服务端注入（防血缘被污染，§11.2）。
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.case import HTTP_METHODS


class GeneratedCase(BaseModel):
    # 【面试锚点】operation_id 不由 LLM 生成，由服务端注入；
    # LLM 输出若包含 operation_id 字段，extra="forbid" 会直接判失败（防血缘被污染）
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    method: HTTP_METHODS
    path: str = Field(min_length=1)
    params: dict = {}
    body: dict | None = None
    expected_status: int
    assertions: list = []


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
    prompt_version: str | None
    error_stage: str | None
    error_msg: str | None
    result_summary: dict | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
