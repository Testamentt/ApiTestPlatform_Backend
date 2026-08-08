# 影响分析出入参。why：AnalyzeResult 完整透出 breaking/orphaned/untested/warnings；
# RegressionResult 带执行时真实口径 summary + dropped 原因（D6/追问 3）。
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AnalyzeRequest(BaseModel):
    document: dict
    new_version: str | None = None
    old_version: str | None = None


class AffectedCase(BaseModel):
    case_id: int
    name: str
    method: str
    path: str


class AnalyzeResult(BaseModel):
    analysis_id: int
    old_version: str | None
    new_version: str
    added_ops: list[str]
    removed_ops: list[str]
    changed_ops: list[str]
    breaking_changed_ops: list[str]
    affected_cases: list[AffectedCase]
    affected_summary: dict
    orphaned_case_ids: list[int]
    suggested_remap: dict
    untested_ops: list[str]
    warnings: list[str] = []
    has_fix_hint: bool = False  # breaking 变更时提示可按需生成修复建议（联动点 2）
    fix_hint_endpoint: str = ""  # 可调用的建议端点路径


class FixHint(BaseModel):
    """LLM 修复建议输出 schema（extra="forbid" 严格校验）。"""

    model_config = ConfigDict(extra="forbid")

    suggestion: str


class FixHintResult(BaseModel):
    analysis_id: int
    ai_fix_hint: dict | None  # None = best-effort 失败（不阻塞 analyze 纯规则秒回）


class RegressionResult(BaseModel):
    analysis_id: int
    task_id: int
    task_status: str
    executed_case_ids: list[int]
    executed_count: int
    dropped_case_ids: list[int]
    dropped_count: int
    dropped_reasons: dict
    affected_summary: dict
