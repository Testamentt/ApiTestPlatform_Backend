# 用例出入参。why：operation_id 必填（Phase 2 血缘）；method 白名单；status 不允许外部直接改。
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.schemas.assertion import AssertionItem

HTTP_METHODS = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

# why：strip_whitespace 先于长度校验执行——纯空白串在 min_length=1 处被拒（min_length 单独用会放过 "   "，
# 而 name/path/operation_id 会进生成代码与血缘检索，空白值等于脏数据）；长度上限对齐表列长（review L8）
NameStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
PathStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]
# why：HTTP 状态码有效区间——无界时 99999 这类值会静默进执行池（断言必然失败，且排障时看不出是脏数据）
STATUS_CODE_MIN = 100
STATUS_CODE_MAX = 599
ExpectedStatus = Annotated[int, Field(ge=STATUS_CODE_MIN, le=STATUS_CODE_MAX)]


class CaseCreate(BaseModel):
    name: NameStr
    method: HTTP_METHODS
    path: PathStr
    operation_id: NameStr
    params: dict | None = None
    body: dict | None = None
    expected_status: ExpectedStatus = 200
    # why：逐条 AssertionItem 结构校验（path 白名单/op 白名单）——渲染进生成代码前消灭注入面
    assertions: list[AssertionItem] | None = None

    @field_validator("path")
    @classmethod
    def _path_starts_with_slash(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("path 必须以 / 开头")
        return v


class CaseUpdate(BaseModel):
    name: NameStr | None = None
    method: HTTP_METHODS | None = None
    path: PathStr | None = None
    operation_id: NameStr | None = None
    params: dict | None = None
    body: dict | None = None
    expected_status: ExpectedStatus | None = None
    assertions: list[AssertionItem] | None = None

    @field_validator("path")
    @classmethod
    def _path_starts_with_slash(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("/"):
            raise ValueError("path 必须以 / 开头")
        return v


class CaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    method: str
    path: str
    operation_id: str
    params: dict | None
    body: dict | None
    expected_status: int
    assertions: list | None
    status: str
    source: str
    trust_score: (
        int  # 血缘可信度（手工100 / AI 校验通过80 / AI 带 warnings60），审核按此优先 Review
    )
    reviewer: str | None
    created_at: datetime
    updated_at: datetime


class ConfirmBody(BaseModel):
    reviewer: str = Field(min_length=1)

    @field_validator("reviewer")
    @classmethod
    def _reviewer_not_blank(cls, v: str) -> str:
        # why：min_length 不拦纯空白——批准人审计字段不能收 "  "（R3 批次）；strip 后落库
        if not v.strip():
            raise ValueError("reviewer 不能为空白")
        return v.strip()


class ConfirmResult(BaseModel):
    case_id: int
    status: str
