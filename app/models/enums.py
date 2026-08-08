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


class GenerationStatus(StrEnum):
    """AI 生成任务状态机（对齐执行任务 TaskStatus 的简单四态）。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
