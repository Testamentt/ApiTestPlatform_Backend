# case_generator 单元测试：渲染合法 Python + 断言状态码 + 注入防护 + 鉴权模式渲染。
from __future__ import annotations

import ast

from app.models.test_case import TestCase
from app.utils.case_generator import render_conftest, render_test_file


def _settings_with(**execution_overrides):
    from app.core.config import Settings

    s = Settings()
    return s.model_copy(update={"execution": s.execution.model_copy(update=execution_overrides)})


def test_renders_valid_python():
    case = TestCase(
        id=1,
        name="get users",
        method="GET",
        path="/get",
        operation_id="httpbin_get",
        expected_status=200,
        status="active",
    )
    src = render_test_file(case)
    compile(src, "<test>", "exec")  # 语法必须合法
    assert f"def test_{case.id}" in src
    assert "assert r.status_code == 200" in src


def test_body_injected():
    case = TestCase(
        id=2,
        name="post",
        method="POST",
        path="/post",
        operation_id="x",
        body={"a": 1},
        expected_status=201,
        status="active",
    )
    src = render_test_file(case)
    assert "{'a': 1}" in src


def _top_level_structure(src: str) -> tuple[int, list[str]]:
    """返回模块级节点数与节点类型列表——注入防护的权威断言。"""
    tree = ast.parse(src)
    return len(tree.body), [type(n).__name__ for n in tree.body]


def test_name_injection_escaped():
    # 恶意 name：若进入 docstring/代码区可闭合字符串注入任意 Python 代码（pytest 收集即执行，RCE）。
    # 修复后 name 只出现在文件头注释（repr 转义 + 注释不解析转义序列），注入必然失效。
    malicious = 'x"""\nimport os\nos.system("echo PWNED")\n# '
    case = TestCase(
        id=9,
        name=malicious,
        method="GET",
        path="/get",
        operation_id="httpbin_get",
        expected_status=200,
        status="active",
    )
    src = render_test_file(case)
    tree = ast.parse(src)  # 语法必须合法（旧实现此步即 SyntaxError）
    count, node_types = _top_level_structure(src)
    # 模块级应只有 import httpx + def test_9；注入的 import os / os.system 调用不得成为节点
    assert count == 2, f"注入代码成为模块级节点: {node_types}"
    assert node_types == ["Import", "FunctionDef"]
    # AST 中不存在对 os.system 的调用
    system_calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "system"
    ]
    assert not system_calls, "注入的 os.system 调用出现在可执行代码中"
    # 首行是注释（保留可读性），且注入内容只存在于注释行内
    first_line = src.splitlines()[0]
    assert first_line.startswith("# case 9:")


def test_auth_mode_renders_token_fixture_and_header(monkeypatch):
    # why：被测系统需登录态（如 ERP X-Access-Token）——签名注入 fixture + headers 注入 token
    monkeypatch.setattr(
        "app.utils.case_generator.get_settings",
        lambda: _settings_with(auth_enabled=True, auth_token_header="X-Access-Token"),
    )
    case = TestCase(
        id=3,
        name="auth get",
        method="GET",
        path="/depot/list",
        operation_id="depot_list",
        expected_status=200,
        status="active",
    )
    src = render_test_file(case)
    compile(src, "<test>", "exec")  # 语法必须合法
    assert f"def test_{case.id}(token):" in src
    assert "headers={'X-Access-Token': token}" in src


def test_render_conftest_valid_and_no_secret_leak(monkeypatch):
    # why：凭证只经环境变量在 pytest 运行时读取（§8）——生成文件里绝不能出现密码明文
    monkeypatch.setenv("ERP_TEST_PASSWORD", "super-secret-123")
    monkeypatch.setattr(
        "app.utils.case_generator.get_settings",
        lambda: _settings_with(
            auth_enabled=True,
            base_url="http://127.0.0.1:9999/jshERP-boot",
            auth_login_path="/user/login",
            auth_login_body={"loginName": "{username}", "password": "{password}"},
            auth_password_encoding="md5",
        ),
    )
    src = render_conftest()
    compile(src, "<conftest>", "exec")
    assert 'scope="session"' in src
    assert "'/user/login'" in src
    assert "md5" in src
    assert "super-secret-123" not in src
    assert "ERP_TEST_PASSWORD" in src  # 凭证经 env 名引用
