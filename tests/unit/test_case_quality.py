# 本项目编码格式：UTF-8（无 BOM）。
# 评分单测：trust_score 从固定标签变成逐条计算，且只扣「文档能判定」的分。
from __future__ import annotations

from app.services.case_quality import (
    PENALTY_DUPLICATE,
    PENALTY_NO_FIELD_ASSERTION,
    PENALTY_PARSE_WARNINGS,
    PENALTY_UNDECLARED_STATUS,
    PENALTY_UNKNOWN_ASSERTION,
    PENALTY_UNKNOWN_ASSERTION_CAP,
    score_case,
)
from app.services.generation_validators import Violation


def _v(rule: str, level: str = "warn", detail: str = "d") -> Violation:
    return Violation(rule=rule, level=level, detail=detail)


def test_clean_case_with_field_assertion_scores_full():
    assert score_case([], has_field_assertion=True).score == 100


def test_missing_field_assertion_penalized():
    """文档支持字段断言但用例只写了状态码 → 扣分（可判的情形才扣）。"""
    q = score_case([], has_field_assertion=False)
    assert q.score == 100 - PENALTY_NO_FIELD_ASSERTION
    assert q.flags[0].penalty == PENALTY_NO_FIELD_ASSERTION


def test_undeclared_status_penalized():
    q = score_case([_v("expected_status_undeclared", "warn")], has_field_assertion=True)
    assert q.score == 100 - PENALTY_UNDECLARED_STATUS


def test_unknown_assertions_capped():
    """断言字段不可判按条扣，但封顶——否则一条全是编造断言的用例会直接被扣成 0。"""
    violations = [_v("assertion_path_unknown") for _ in range(10)]
    q = score_case(violations, has_field_assertion=True)
    assert q.score == 100 - PENALTY_UNKNOWN_ASSERTION_CAP
    total = sum(f.penalty for f in q.flags)
    assert total == PENALTY_UNKNOWN_ASSERTION_CAP


def test_unknown_assertion_single():
    q = score_case([_v("assertion_path_unknown")], has_field_assertion=True)
    assert q.score == 100 - PENALTY_UNKNOWN_ASSERTION


def test_parse_warnings_penalized():
    q = score_case([], has_field_assertion=True, op_has_warnings=True)
    assert q.score == 100 - PENALTY_PARSE_WARNINGS


def test_duplicate_penalized():
    q = score_case([], has_field_assertion=True, duplicate=True)
    assert q.score == 100 - PENALTY_DUPLICATE


def test_penalties_accumulate_and_floor_at_zero():
    q = score_case(
        [_v("expected_status_undeclared")] + [_v("assertion_path_unknown") for _ in range(5)],
        has_field_assertion=False,
        op_has_warnings=True,
        duplicate=True,
    )
    assert q.score >= 0
    assert len(q.flags) == 5


def test_flags_carry_reason_and_penalty():
    """分数必须能解释到「因为什么扣了几分」——否则它又是个不可追问的数字。"""
    q = score_case([_v("expected_status_undeclared")], has_field_assertion=False)
    payload = q.to_dict()
    assert payload["score"] == q.score
    for flag in payload["flags"]:
        assert flag["reason"]
        assert isinstance(flag["penalty"], int)
        assert flag["penalty"] > 0
