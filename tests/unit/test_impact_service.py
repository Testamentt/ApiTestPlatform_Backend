# ImpactService 测试：analyze（首次/二次 diff/breaking 圈定/identical/版本碰撞守卫）+
# regression（宽容降级/无受影响抛错）+ 模型 affected_count 同步。
from __future__ import annotations

import pytest
from app.core.exceptions import AppError
from app.models.enums import CaseStatus
from app.models.impact_analysis import ImpactAnalysis
from app.models.test_case import TestCase
from app.services.impact_service import ImpactService


def _doc(operation_id="listUsers", body_schema=None):
    op = {"operationId": operation_id}
    if body_schema is not None:
        op["requestBody"] = {"content": {"application/json": {"schema": body_schema}}}
    op["responses"] = {"200": {"description": "ok"}}
    return {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1.0"},
        "paths": {"/users": {"get": op}},
    }


def _seed_active_case(session_factory, operation_id="listUsers"):
    with session_factory() as s:
        case = TestCase(
            name="c",
            operation_id=operation_id,
            method="GET",
            path="/users",
            status=CaseStatus.ACTIVE,
        )
        s.add(case)
        s.commit()
        s.refresh(case)
        return case.id


# ---------- analyze ----------


def test_analyze_first_time_all_added(session_factory):
    with session_factory() as s:
        r = ImpactService(s).analyze(_doc())
        assert r.old_version is None
        assert r.added_ops == ["listUsers"]
        assert r.untested_ops == ["listUsers"]  # 首次 = 全部
        assert r.affected_cases == []


def test_analyze_detects_breaking_and_affects_case(session_factory):
    v1 = _doc(body_schema={"type": "object", "properties": {"age": {"type": "integer"}}})
    v2 = _doc(body_schema={"type": "object", "properties": {"age": {"type": "string"}}})
    with session_factory() as s:
        ImpactService(s).analyze(v1)
    case_id = _seed_active_case(session_factory)
    with session_factory() as s:
        r = ImpactService(s).analyze(v2)
        assert r.breaking_changed_ops == ["listUsers"]
        assert [c.case_id for c in r.affected_cases] == [case_id]
        assert r.affected_summary == {"total": 1}


def test_analyze_identical_no_changes(session_factory):
    doc = _doc()
    with session_factory() as s:
        ImpactService(s).analyze(doc)
        r = ImpactService(s).analyze(doc)
        assert r.changed_ops == []
        assert r.breaking_changed_ops == []


def test_analyze_version_collision_raises(session_factory):
    doc = _doc()
    with session_factory() as s:
        ImpactService(s).analyze(doc)
        with pytest.raises(AppError) as exc:
            ImpactService(s).analyze(doc, new_version="v1")  # 与对比基线相同 → 拒绝覆盖
        assert exc.value.code == "VERSION_COLLISION"


def test_analyze_auto_version_skips_existing_v1(session_factory):
    # latest 为非 vN 标签时 auto 不得回退算出已存在的 v1（防覆盖历史快照）
    with session_factory() as s:
        svc = ImpactService(s)
        svc.analyze(_doc(), new_version="v1")
        svc.analyze(_doc(operation_id="x"), new_version="release-1")
        r = svc.analyze(_doc(operation_id="y"))
        assert r.new_version == "v2"


def test_affected_count_synced(session_factory):
    v1 = _doc(body_schema={"type": "object", "properties": {"age": {"type": "integer"}}})
    v2 = _doc(body_schema={"type": "object", "properties": {"age": {"type": "string"}}})
    with session_factory() as s:
        ImpactService(s).analyze(v1)
    _seed_active_case(session_factory)
    _seed_active_case(session_factory)
    with session_factory() as s:
        analysis = ImpactService(s).analyze(v2)
        assert analysis.affected_summary["total"] == 2
        obj = s.get(ImpactAnalysis, analysis.analysis_id)
        assert obj.affected_count == 2
        assert obj.affected_count == len(obj.affected_case_ids)  # @validates 写时同步


# ---------- regression ----------


def test_regression_no_affected_raises(session_factory):
    with session_factory() as s:
        ImpactService(s).analyze(_doc())
        analysis_id = ImpactService(s).analyze(_doc()).analysis_id  # identical → 无 affected
    with session_factory() as s:
        with pytest.raises(AppError) as exc:
            ImpactService(s).regression(analysis_id)
        assert exc.value.code == "NO_AFFECTED_CASES"


def test_regression_partial_drop(session_factory, patch_sessionlocal, fake_execution):
    v1 = _doc(body_schema={"type": "object", "properties": {"age": {"type": "integer"}}})
    v2 = _doc(body_schema={"type": "object", "properties": {"age": {"type": "string"}}})
    with session_factory() as s:
        ImpactService(s).analyze(v1)
    c1 = _seed_active_case(session_factory)
    _seed_active_case(session_factory)
    with session_factory() as s:
        analysis_id = ImpactService(s).analyze(v2).analysis_id
    with session_factory() as s:  # 回归前删除一个受影响用例 → 宽容降级
        s.delete(s.get(TestCase, c1))
        s.commit()

    with session_factory() as s:
        r = ImpactService(s).regression(analysis_id)
        assert r.executed_count == 1
        assert r.dropped_case_ids == [c1]
        assert r.dropped_reasons[c1] == "case deleted"
        assert r.affected_summary == {"total": 2, "executed": 1, "dropped": 1}


def test_regression_all_dropped_raises(session_factory):
    v1 = _doc(body_schema={"type": "object", "properties": {"age": {"type": "integer"}}})
    v2 = _doc(body_schema={"type": "object", "properties": {"age": {"type": "string"}}})
    with session_factory() as s:
        ImpactService(s).analyze(v1)
    c1 = _seed_active_case(session_factory)
    with session_factory() as s:
        analysis_id = ImpactService(s).analyze(v2).analysis_id
    with session_factory() as s:
        s.delete(s.get(TestCase, c1))
        s.commit()

    with session_factory() as s:
        with pytest.raises(AppError) as exc:
            ImpactService(s).regression(analysis_id)
        assert exc.value.code == "NO_ACTIVE_CASES"


def test_regression_over_task_limit_raises_422(session_factory, monkeypatch):
    """P2-1/N7：受影响用例超过单任务上限（500）时必须显式 422，而不是让服务层内部构造
    `TaskCreate` 抛 Pydantic ValidationError 冒泡成 500。

    why：这里用轻量 stub 顶掉仓储，避免为了一个边界断言往库里塞 501 行数据。
    """

    class _StubCase:
        def __init__(self, case_id: int) -> None:
            self.id = case_id

    class _StubAnalysis:
        affected_case_ids = list(range(1, 502))  # 501 > MAX_CASE_IDS

    with session_factory() as s:
        svc = ImpactService(s)
        monkeypatch.setattr(svc.impact_repo, "get_or_raise", lambda _id: _StubAnalysis())
        monkeypatch.setattr(
            svc.case_repo, "find_active_by_ids", lambda ids: [_StubCase(i) for i in ids]
        )
        monkeypatch.setattr(svc.case_repo, "find_by_ids", lambda ids: [_StubCase(i) for i in ids])
        with pytest.raises(AppError) as exc:
            svc.regression(1)
        assert exc.value.code == "TOO_MANY_CASES"
        assert exc.value.status_code == 422
        assert str(501) in str(exc.value.detail)
