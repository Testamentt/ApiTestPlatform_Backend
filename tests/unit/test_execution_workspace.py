# 本项目编码格式：UTF-8（无 BOM）。
# 执行 workspace 构建单元测试：auth_enabled 时生成登录 conftest。
# why 归属 LocalExecutor：workspace 构建随执行器抽象下沉（Jenkins 执行器不需要本地 workspace），
# 编排层不再承担「怎么准备执行环境」。
from __future__ import annotations

from app.models.test_case import TestCase
from app.services.executors.local import LocalExecutor


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


def _executor(**execution_overrides) -> LocalExecutor:
    # run_cmd 不会被调用（这些用例只验证 workspace 生成），用占位实现避免真跑子进程
    return LocalExecutor(_settings_with(**execution_overrides), run_cmd=lambda *a, **k: None)


def test_workspace_without_auth_has_no_conftest(tmp_path):
    ws = _executor(workspace_dir=str(tmp_path), auth_enabled=False)._build_workspace(1, [_case(1)])
    assert (ws / "test_1.py").exists()
    assert not (ws / "conftest.py").exists()


def test_workspace_with_auth_writes_conftest(tmp_path):
    ws = _executor(workspace_dir=str(tmp_path), auth_enabled=True)._build_workspace(
        2, [_case(1), _case(2)]
    )
    assert (ws / "test_1.py").exists() and (ws / "test_2.py").exists()
    conftest = ws / "conftest.py"
    assert conftest.exists()
    compile(conftest.read_text(encoding="utf-8"), "conftest.py", "exec")  # 生成即合法
