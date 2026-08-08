# 模型注册入口。why：core.database.init_db() 依赖此处 import，确保 Base.metadata 已收集全部表。
from app.models.api_definition import ApiDefinition
from app.models.generation_log import GenerationLog
from app.models.generation_task import GenerationTask
from app.models.impact_analysis import ImpactAnalysis
from app.models.task import Task
from app.models.test_case import TestCase

__all__ = [
    "TestCase",
    "Task",
    "ApiDefinition",
    "ImpactAnalysis",
    "GenerationTask",
    "GenerationLog",
]
