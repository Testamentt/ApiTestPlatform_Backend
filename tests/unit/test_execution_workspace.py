# 执行 workspace 构建单元测试：auth_enabled 时生成登录 conftest。
from __future__ import annotations

from app.models.test_case import TestCase
from app.services.execution_service import ExecutionService


def _settings_with(**execution_overrides):
    from app.core.config import Settings

    s = Settings()
    return s.model_copy(update={"execution": s.execution.model_copy(update=execution_overrides)})


def _case(case_id: int) -> TestCase:
    return TestCase(
        id=case_id,
        name=f"case{case_id}",
        method="GET",
        path="/get",
        operation_id="x",
        expected_status=200,
        status="active",
    )


def test_workspace_without_auth_has_no_conftest(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.execution_service.get_settings",
        lambda: _settings_with(workspace_dir=str(tmp_path), auth_enabled=False),
    )
    ws = ExecutionService(session_factory=None)._build_workspace(1, [_case(1)])
    assert (ws / "test_1.py").exists()
    assert not (ws / "conftest.py").exists()


def test_workspace_with_auth_writes_conftest(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.execution_service.get_settings",
        lambda: _settings_with(workspace_dir=str(tmp_path), auth_enabled=True),
    )
    ws = ExecutionService(session_factory=None)._build_workspace(2, [_case(1), _case(2)])
    assert (ws / "test_1.py").exists() and (ws / "test_2.py").exists()
    conftest = ws / "conftest.py"
    assert conftest.exists()
    compile(conftest.read_text(encoding="utf-8"), "conftest.py", "exec")  # 生成即合法
