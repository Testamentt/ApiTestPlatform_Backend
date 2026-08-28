# 全局测试夹具。why：内存 SQLite + dependency_overrides 隔离；Celery eager 不连真实 Redis。
from __future__ import annotations

import pytest
from app.api.v1.deps import get_db, verify_token
from app.celery_app import celery_app
from app.core.database import Base
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Celery eager：任务同步执行，测试不连真实 Redis
celery_app.conf.task_always_eager = True
celery_app.conf.task_eager_propagates = True


def pytest_collection_modifyitems(config, items):
    # why：分层 marker 声明即用（review L12）——按目录自动归类 api/tasks/integration，
    # 支持 `-m api`/`-m tasks` 独立分层跑；e2e 已自行标 slow，这里再补 integration 归类。
    # 逐文件写 pytestmark 易遗漏且新文件忘标，集中在此单点维护。
    for item in items:
        nodeid = item.nodeid
        if nodeid.startswith("tests/api/"):
            item.add_marker(pytest.mark.api)
        elif nodeid.startswith("tests/tasks/"):
            item.add_marker(pytest.mark.tasks)
        elif nodeid.startswith("tests/e2e/"):
            item.add_marker(pytest.mark.integration)


@pytest.fixture()
def engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture()
def session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def client(session_factory):
    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    # Phase 4：现有测试绕过鉴权（test_auth.py 用独立 auth_client 走真实 verify_token）
    app.dependency_overrides[verify_token] = lambda: None
    # 不用上下文管理器：避免触发 lifespan 去动真实 DB
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def patch_sessionlocal(session_factory, monkeypatch):
    """why：execute_cases_task 内部用 SessionLocal 读库，测试需指向内存库。"""
    monkeypatch.setattr("app.tasks.execute_cases.SessionLocal", session_factory)


@pytest.fixture()
def fake_execution(monkeypatch):
    """why：假 run_cmd 写 report.xml，避免真实 subprocess + 网络。"""
    from .fakes import JUNIT_OK, make_fake_run_cmd

    monkeypatch.setattr(
        "app.services.execution_service.run_cmd",
        make_fake_run_cmd(junit_xml=JUNIT_OK),
    )
