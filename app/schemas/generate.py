# 本项目编码格式：UTF-8（无 BOM）。
# AI 生成出入参。why：GeneratedCase 是 LLM 输出校验 schema（extra="forbid" 严格校验）；
# operation_id 不由 LLM 生成、由服务端注入（防血缘被污染）。
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.assertion import AssertionItem
from app.schemas.case import HTTP_METHODS, STATUS_CODE_MAX, STATUS_CODE_MIN

# 单次生成的用例条数上限。why：prompt 只要求每接口 2-5 条，无上限时一次输出 50 条会
# 把 LLM 成本与人工审核量同时放大，且大概率是重复的边界组合
MAX_CASES_PER_OPERATION = 10


class GeneratedCase(BaseModel):
    # operation_id 不由 LLM 生成，由服务端注入；
    # LLM 输出若包含 operation_id 字段，extra="forbid" 会直接判失败（防血缘被污染）
    model_config = ConfigDict(extra="forbid")

    # max_length 对齐 test_cases 表列长（name 255 / path 1024）——
    # LLM 超长输出在 Pydantic 校验层拦截，避免落库时被 DB 截断/报错
    name: str = Field(min_length=1, max_length=255)
    method: HTTP_METHODS
    path: str = Field(min_length=1, max_length=1024)
    params: dict = {}
    body: dict | None = None
    # why：区间与 CaseCreate 的 ExpectedStatus 同口径——无界时 0/99999 这类值会静默进 draft，
    # 执行时必然断言失败，排障时还看不出是脏数据
    expected_status: int = Field(ge=STATUS_CODE_MIN, le=STATUS_CODE_MAX)
    # why：LLM 断言输出同样逐条过 AssertionItem（path/op 白名单，extra=forbid）——
    # 非法断言在 LLM 校验层即整 case 拒绝，不落库不渲染
    assertions: list[AssertionItem] = []

    @field_validator("path")
    @classmethod
    def _path_starts_with_slash(cls, v: str) -> str:
        """why：path 会原样拼进执行请求的 URL，无此约束时 `/../internal/x`、
        `http://evil/x` 这类值均能通过。CaseCreate 早有同名校验，LLM 输出侧缺了它。"""
        if not v.startswith("/"):
            raise ValueError("path 必须以 / 开头")
        return v


class GeneratedCaseList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # why：min_length=1——空数组是"LLM 什么也没产出"，不该伪装成一个成功接口；
    # 无此约束时 {"cases": []} 会让任务记 SUCCESS/generated+1 而草稿为 0
    cases: list[GeneratedCase] = Field(min_length=1, max_length=MAX_CASES_PER_OPERATION)


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
    celery_task_id: str | None  # 卡死时可通过 Celery 定位/revoke
    prompt_version: str | None
    error_stage: str | None
    error_msg: str | None
    result_summary: dict | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
