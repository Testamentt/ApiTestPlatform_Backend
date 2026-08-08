# fix-hints API 测试：breaking 变更生成建议 / 无 breaking 422 / best-effort 失败置 None。
from __future__ import annotations

from tests.fakes import FakeLlmClient


def _seed_impact(session_factory, *, breaking=None):
    with session_factory() as s:
        from app.models.impact_analysis import ImpactAnalysis

        a = ImpactAnalysis(
            new_version="v1", added_ops=[], removed_ops=[], changed_ops=breaking or [],
            breaking_changed_ops=breaking or [], affected_case_ids=[], orphaned_case_ids=[],
            suggested_remap={}, untested_ops=[], affected_summary={},
        )
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def test_fix_hints_returns_suggestion(client, session_factory, monkeypatch):
    aid = _seed_impact(session_factory, breaking=["createUser"])
    # why：suggest_fix_hints 惰性创建 LlmClient，monkeypatch 工厂返回 Fake（不真调 API）
    monkeypatch.setattr(
        "app.services.impact_service.LlmClient",
        lambda: FakeLlmClient(data={"suggestion": "建议删除断言中对字段 age 的引用"}),
    )
    r = client.post(f"/api/v1/impact/{aid}/fix-hints")
    assert r.status_code == 200
    hint = r.json()["data"]["ai_fix_hint"]
    assert hint["breaking_changed_ops"] == ["createUser"]
    assert "age" in hint["suggestion"]


def test_fix_hints_no_breaking_422(client, session_factory):
    aid = _seed_impact(session_factory, breaking=[])
    r = client.post(f"/api/v1/impact/{aid}/fix-hints")
    assert r.status_code == 422
    assert r.json()["code"] == "NO_BREAKING_CHANGES"


def test_fix_hints_best_effort_returns_none(client, session_factory, monkeypatch):
    from app.core.exceptions import AppError

    aid = _seed_impact(session_factory, breaking=["createUser"])
    monkeypatch.setattr(
        "app.services.impact_service.LlmClient",
        lambda: FakeLlmClient(raise_error=AppError("LLM_FAILED", status_code=502)),
    )
    r = client.post(f"/api/v1/impact/{aid}/fix-hints")
    assert r.status_code == 200
    assert r.json()["data"]["ai_fix_hint"] is None  # best-effort：失败不阻塞
