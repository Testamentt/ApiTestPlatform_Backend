# 状态枚举。why：状态列用 StrEnum + String（SQLite 无原生 ENUM），代码层校验。
from enum import StrEnum


class CaseStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class CaseSource(StrEnum):
    MANUAL = "manual"
    AI = "ai"
    SWAGGER = "swagger"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
