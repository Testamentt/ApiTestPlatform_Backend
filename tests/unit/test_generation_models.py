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


def test_generation_log_foreign_key_and_confidence(session_factory):
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
        assert log.ai_confidence == 1.0  # 校验通过默认置信度


def test_test_case_trust_score_default_100(session_factory):
    with session_factory() as s:
        case = TestCase(name="c", operation_id="op", method="GET", path="/x")
        s.add(case)
        s.commit()
        s.refresh(case)
        assert case.trust_score == 100  # 手工创建默认满分


def test_generated_case_max_length_matches_db():
    # review L8：name/path 上限与 test_cases 列长（255/1024）对齐——超长输出在校验层拦截
    from app.schemas.generate import GeneratedCase
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GeneratedCase(name="x" * 256, method="GET", path="/p", expected_status=200)
    with pytest.raises(ValidationError):
        GeneratedCase(name="ok", method="GET", path="/" + "p" * 1024, expected_status=200)
    ok = GeneratedCase(name="o" * 255, method="GET", path="/" + "p" * 1023, expected_status=200)
    assert ok.name == "o" * 255
