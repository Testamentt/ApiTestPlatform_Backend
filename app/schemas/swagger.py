# Swagger 解析出入参。why：ParseResult 透出 warnings（宽容解析不静默，D4/D5）。
from __future__ import annotations

from pydantic import BaseModel


class ParseRequest(BaseModel):
    document: dict
    version: str | None = None


class ParseResult(BaseModel):
    version_id: int
    version: str
    hash_version: int
    operation_count: int
    operation_ids: list[str]
    warnings: list[str] = []
