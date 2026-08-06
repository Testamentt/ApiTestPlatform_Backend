# 用例出入参。why：operation_id 必填（Phase 2 血缘）；method 白名单；status 不允许外部直接改。
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HTTP_METHODS = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


class CaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    method: HTTP_METHODS
    path: str = Field(min_length=1, max_length=1024)
    operation_id: str = Field(min_length=1, max_length=255)
    params: dict | None = None
    body: dict | None = None
    expected_status: int = 200
    assertions: list | None = None

    @field_validator("path")
    @classmethod
    def _path_starts_with_slash(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("path 必须以 / 开头")
        return v


class CaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    method: HTTP_METHODS | None = None
    path: str | None = Field(default=None, min_length=1, max_length=1024)
    operation_id: str | None = Field(default=None, min_length=1, max_length=255)
    params: dict | None = None
    body: dict | None = None
    expected_status: int | None = None
    assertions: list | None = None

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
    created_at: datetime
    updated_at: datetime


class ConfirmBody(BaseModel):
    reviewer: str = Field(min_length=1)


class ConfirmResult(BaseModel):
    case_id: int
    status: str
