# 统一响应与分页。why：所有接口成功统一 {code, message, data}，列表统一分页结构。
from __future__ import annotations

from pydantic import BaseModel


class ApiResponse[T](BaseModel):
    code: int = 0
    message: str = "ok"
    data: T


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int
