# e2e 集成冒烟（-m slow，不随默认 CI）：AI 生成全链路串连——
# POST /generate → eager 生成 → draft 入库(trust_score) → confirm → 执行 → 审计。
# 隔离：FakeLlmClient + fake run_cmd + 内存库 + Celery eager，不碰真实 API/Redis/子进程。
from __future__ import annotations

import pytest
from app.models.generation_log import GenerationLog
from tests.fakes import JUNIT_OK, FakeLlmClient, make_fake_run_cmd

pytestmark = pytest.mark.slow


def _doc():
    return {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1"},
        "paths": {
            "/users": {
                "get": {"operationId": "listUsers", "responses": {"200": {"description": "ok"}}},
                "post": {"operationId": "createUser", "responses": {"200": {"description": "ok"}}},
            }
        },
    }


def _patch_isolations(monkeypatch, session_factory):
    # why：eager 下任务用真实 SessionLocal/LlmClient/run_cmd，需指向内存库 + Fake（不真调 API/子进程）
    monkeypatch.setattr("app.tasks.generate_cases.SessionLocal", session_factory)
    monkeypatch.setattr("app.tasks.execute_cases.SessionLocal", session_factory)
    monkeypatch.setattr("app.services.generation_service.LlmClient", lambda: FakeLlmClient())
    monkeypatch.setattr(
        "app.services.execution_service.run_cmd", make_fake_run_cmd(junit_xml=JUNIT_OK)
    )


def test_ai_generate_to_confirm_to_execute(client, session_factory, monkeypatch):
    _patch_isolations(monkeypatch, session_factory)

    # 1. AI 生成（无分析历史 → 智能决策全量）
    r = client.post("/api/v1/generate", json={"document": _doc()})
    assert r.status_code == 202
    tid = r.json()["data"]["id"]
    task = client.get(f"/api/v1/generate/{tid}").json()["data"]
    assert task["status"] == "success"
    assert task["result_summary"]["generated"] == 2
    assert task["result_summary"]["draft_created"] == 2
    assert task["celery_task_id"] is not None  # P2-3：celery_task_id 持久化

    # 2. draft 用例：source=ai、trust_score=80、status=draft（防幻觉三层护栏）
    cases = client.get("/api/v1/cases?status=draft").json()["data"]["items"]
    ai_cases = [c for c in cases if c["source"] == "ai"]
    assert len(ai_cases) == 2
    assert all(c["trust_score"] == 80 for c in ai_cases)
    assert all(c["status"] == "draft" for c in ai_cases)
    assert {c["operation_id"] for c in ai_cases} == {"listUsers", "createUser"}  # 服务端注入

    # 3. 人工 confirm → active（防幻觉第 2 层）
    cid = ai_cases[0]["id"]
    r = client.post(f"/api/v1/cases/{cid}/confirm", json={"reviewer": "demo"})
    assert r.json()["data"]["status"] == "active"

    # 4. 执行（Lookup-Create 幂等 + fake run_cmd）
    r = client.post("/api/v1/tasks", json={"case_ids": [cid]})
    assert r.status_code == 202
    exec_task = client.get(f"/api/v1/tasks/{r.json()['data']['id']}").json()["data"]
    assert exec_task["status"] == "success"
    assert exec_task["result_summary"]["passed"] == 1

    # 5. 审计：逐 operation 写 generation_logs（usage/cost/confidence）
    with session_factory() as s:
        logs = s.query(GenerationLog).all()
        assert len(logs) == 2
        assert all(log.status == "success" for log in logs)
        assert all(log.ai_confidence == 1.0 for log in logs)
