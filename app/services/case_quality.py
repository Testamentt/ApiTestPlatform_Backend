# 本项目编码格式：UTF-8（无 BOM）。
# 用例质量评分：把 trust_score 从「文档级固定标签」变成「单条用例的真实质量」。
# why：原口径 60/80 只由 document 级 parse warnings 决定，同一文档内所有 operation 的所有用例
# 分数完全相同——它回答不了「这条用例凭什么 80 分」，也给不出「哪条更值得先看」。
# 设计约束：只扣「文档能判定」的分。文档自身缺信息（裸 object 响应、自由 map 请求体）不该
# 算在用例头上，否则评分会退化成对文档质量的重复计分。
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.generation_validators import Violation

# 起评分。why：满足契约 + 有字段层断言的用例应接近满分，留出扣分空间表达具体缺陷
BASE_SCORE = 100

# 各规则的扣分权重。数值对应「这条问题对人工审核与执行的影响程度」：
# 期望码未声明说明用例可能永远失败；断言落在未知字段说明断言可能恒真；
# 字段层断言缺失说明正向用例验不出业务正确性
PENALTY_UNDECLARED_STATUS = 20
PENALTY_UNKNOWN_ASSERTION = 10
PENALTY_UNKNOWN_ASSERTION_CAP = 30
PENALTY_NO_FIELD_ASSERTION = 10
PENALTY_PARSE_WARNINGS = 20
PENALTY_DUPLICATE = 10


@dataclass(frozen=True)
class QualityFlag:
    """单条扣分记录。why：分数必须能解释到「因为什么扣了几分」，
    否则它又变成一个不可追问的数字。"""

    reason: str
    penalty: int

    def to_dict(self) -> dict:
        return {"reason": self.reason, "penalty": self.penalty}


@dataclass
class CaseQuality:
    score: int
    flags: list[QualityFlag] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"score": self.score, "flags": [f.to_dict() for f in self.flags]}


def score_case(
    violations: list[Violation],
    *,
    has_field_assertion: bool,
    op_has_warnings: bool = False,
    duplicate: bool = False,
) -> CaseQuality:
    """逐条评分。why 参数化而不是直接读库：生成链路能拿到 op 的 warnings，
    去重链路能拿到「是否重复」，把数据来源与计分规则解耦才能单测。"""
    flags: list[QualityFlag] = []

    undeclared = [v for v in violations if v.rule == "expected_status_undeclared"]
    if undeclared:
        flags.append(QualityFlag("期望状态码不在文档声明的响应码中", PENALTY_UNDECLARED_STATUS))

    unknown_assertions = [v for v in violations if v.rule == "assertion_path_unknown"]
    if unknown_assertions:
        penalty = min(
            len(unknown_assertions) * PENALTY_UNKNOWN_ASSERTION, PENALTY_UNKNOWN_ASSERTION_CAP
        )
        flags.append(
            QualityFlag(f"断言字段不在响应 schema 中（{len(unknown_assertions)} 条）", penalty)
        )

    # why：只有接口声明了可枚举的响应字段时才要求字段层断言——216 个 op 的 200 响应是
    # 字符串或裸 object，文档本身给不出可断言的字段，扣分等于惩罚文档质量
    if not has_field_assertion:
        flags.append(QualityFlag("只有状态码断言，无字段层断言", PENALTY_NO_FIELD_ASSERTION))

    if op_has_warnings:
        flags.append(
            QualityFlag("该接口的文档解析存在告警，契约可靠性下降", PENALTY_PARSE_WARNINGS)
        )

    if duplicate:
        flags.append(QualityFlag("与同接口已有用例的请求与期望完全相同", PENALTY_DUPLICATE))

    total_penalty = sum(f.penalty for f in flags)
    return CaseQuality(score=max(BASE_SCORE - total_penalty, 0), flags=flags)
