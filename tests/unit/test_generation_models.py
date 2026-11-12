# 本项目编码格式：UTF-8（无 BOM）。
# 生成模型单测：建表/字段/默认值/run_id 唯一/外键索引/trust_score 默认 100。
from __future__ import annotations

import pytest
from app.core.database import Base
from app.models.generation_log import GenerationLog
from app.models.generation_task import GenerationTask
from app.models.test_case import TestCase
from sqlalchemy.exc import IntegrityError


def test_generation_tables_registered():
    tables = Base.metadata.tables
    assert "generation_tasks" in tables
    assert "generation_logs" in tables


def test_generation_task_run_id_unique(session_factory):
    with session_factory() as s:
        s.add(GenerationTask(run_id="r1", document={"openapi": "3.0.3", "paths": {}}))
        s.commit()
        s.add(GenerationTask(run_id="r1", document={"openapi": "3.0.3", "paths": {}}))
        with pytest.raises(IntegrityError):
            s.commit()  # UNIQUE(run_id) 兜底 Lookup-Create


def test_generation_task_default_status_and_count(session_factory):
    with session_factory() as s:
        task = GenerationTask(run_id="r2", document={})
        s.add(task)
        s.commit()
        s.refresh(task)
        assert task.status == "pending"
        assert task.operation_count == 0


def test_generation_log_foreign_key_and_status(session_factory):
    with session_factory() as s:
        task = GenerationTask(run_id="r3", document={})
        s.add(task)
        s.commit()
        s.refresh(task)
        log = GenerationLog(
            generation_task_id=task.id,
            operation_id="op",
            model="m",
            prompt_version="v1",
            status="success",
        )
        s.add(log)
        s.commit()
        assert log.id is not None
        # why：status 已完整表达「这次调用成没成」，冗余的 ai_confidence 已移除
        assert log.status == "success"
        assert not hasattr(log, "ai_confidence")


def test_test_case_trust_score_default_100(session_factory):
    with session_factory() as s:
        case = TestCase(name="c", operation_id="op", method="GET", path="/x")
        s.add(case)
        s.commit()
        s.refresh(case)
        assert case.trust_score == 100  # 手工创建默认满分
        assert case.quality_flags is None  # 手工用例无扣分原因


def test_generated_case_max_length_matches_db():
    # name/path 上限与 test_cases 列长（255/1024）对齐——超长输出在校验层拦截
    from app.schemas.generate import GeneratedCase
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GeneratedCase(name="x" * 256, method="GET", path="/p", expected_status=200)
    with pytest.raises(ValidationError):
        GeneratedCase(name="ok", method="GET", path="/" + "p" * 1024, expected_status=200)
    ok = GeneratedCase(name="o" * 255, method="GET", path="/" + "p" * 1023, expected_status=200)
    assert ok.name == "o" * 255


def test_generated_case_assertions_validated_itemwise():
    # why：LLM 断言输出逐条过 AssertionItem（path/op 白名单）——非法断言整 case 校验拒绝，
    # 走 validation_failed 落审计，不落库不渲染
    from app.schemas.generate import GeneratedCase
    from pydantic import ValidationError

    ok = GeneratedCase(
        name="ok",
        method="GET",
        path="/p",
        expected_status=200,
        assertions=[{"path": "status_code", "op": "eq", "value": 200}],
    )
    assert ok.assertions[0].path == "status_code"
    with pytest.raises(ValidationError):
        GeneratedCase(
            name="bad",
            method="GET",
            path="/p",
            expected_status=200,
            assertions=[{"path": "a;drop", "op": "eq", "value": 1}],
        )


def test_generated_case_path_must_start_with_slash():
    """why：path 会原样拼进执行请求 URL——`/../internal/x`、`http://evil/x` 这类值
    在 LLM 输出侧此前无任何约束（CaseCreate 有同名校验，生成侧漏了）。"""
    from app.schemas.generate import GeneratedCase
    from pydantic import ValidationError

    for bad in ("users/1", "http://evil.example/x", "../internal/x"):
        with pytest.raises(ValidationError):
            GeneratedCase(name="x", method="GET", path=bad, expected_status=200)
    ok = GeneratedCase(name="x", method="GET", path="/users/1", expected_status=200)
    assert ok.path == "/users/1"


def test_generated_case_status_code_range():
    """why：与 CaseCreate 的 ExpectedStatus 同口径——0/99999 会静默进 draft，
    执行时必然失败，排障还看不出是脏数据。"""
    from app.schemas.generate import GeneratedCase
    from pydantic import ValidationError

    for bad in (0, 99, 600, 99999):
        with pytest.raises(ValidationError):
            GeneratedCase(name="x", method="GET", path="/p", expected_status=bad)
    for good in (100, 200, 404, 599):
        assert (
            GeneratedCase(name="x", method="GET", path="/p", expected_status=good).expected_status
            == good
        )


def test_generated_case_list_caps_case_count():
    """why：prompt 只要求每接口 2-5 条，无上限时一次输出 50 条会同时放大
    成本与人工审核量，且大概率是重复的边界组合。"""
    from app.schemas.generate import MAX_CASES_PER_OPERATION, GeneratedCaseList
    from pydantic import ValidationError

    def case(i: int) -> dict:
        return {
            "name": f"c{i}",
            "method": "GET",
            "path": "/p",
            "expected_status": 200,
        }

    ok = GeneratedCaseList.model_validate(
        {"cases": [case(i) for i in range(MAX_CASES_PER_OPERATION)]}
    )
    assert len(ok.cases) == MAX_CASES_PER_OPERATION
    with pytest.raises(ValidationError):
        GeneratedCaseList.model_validate(
            {"cases": [case(i) for i in range(MAX_CASES_PER_OPERATION + 1)]}
        )
    with pytest.raises(ValidationError):
        GeneratedCaseList.model_validate({"cases": []})  # 空产出不算成功
