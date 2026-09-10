# case_generator 单元测试：渲染合法 Python + 断言状态码 + 注入防护 + 鉴权模式渲染 + 断言引擎。
from __future__ import annotations

import ast
import sys
import types
from types import SimpleNamespace

import pytest
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


# ---------- 断言引擎渲染 ----------


def _case_with_assertions(case_id: int, assertions) -> TestCase:
    return TestCase(
        id=case_id,
        name="assert case",
        method="GET",
        path="/get",
        operation_id="x",
        expected_status=200,
        status="active",
        assertions=assertions,
    )


def _exec_with_fake_response(monkeypatch, status_code=200, payload=None):
    """exec 渲染产物（假 httpx 无网络），返回命名空间。why：验证断言逻辑真实可执行。"""
    # why：本仓库 .env auth_enabled=true（jshERP 被测系统）——渲染签名默认带 token fixture，
    # 断言执行测试统一关闭鉴权，与免鉴权用例口径一致
    monkeypatch.setattr(
        "app.utils.case_generator.get_settings", lambda: _settings_with(auth_enabled=False)
    )
    fake = types.ModuleType("httpx")
    fake.request = lambda *a, **k: SimpleNamespace(
        status_code=status_code, json=lambda: payload
    )
    monkeypatch.setitem(sys.modules, "httpx", fake)
    return None


def test_assertions_rendered_dig_and_message():
    case = _case_with_assertions(
        11,
        [
            {"path": "data.id", "op": "eq", "value": 7},
            {"path": "status_code", "op": "eq", "value": 200},
        ],
    )
    src = render_test_file(case)
    compile(src, "<test>", "exec")
    assert "_dig(payload, 'data.id') == 7" in src
    assert f"case {case.id} 断言失败: path=data.id op=eq" in src
    assert "payload = r.json()" in src  # 有字段断言才渲染 payload 解析块
    # 模块级结构：import + _dig + test（注入防护的权威断言口径同 test_name_injection_escaped）
    nodes = [type(n).__name__ for n in ast.parse(src).body]
    assert nodes == ["Import", "FunctionDef", "FunctionDef"]


def test_no_field_assertions_no_payload_block():
    case = _case_with_assertions(12, [{"path": "status_code", "op": "eq", "value": 200}])
    src = render_test_file(case)
    compile(src, "<test>", "exec")
    assert "_dig" not in src  # 纯契约层断言不生成取值函数
    nodes = [type(n).__name__ for n in ast.parse(src).body]
    assert nodes == ["Import", "FunctionDef"]


def test_rendered_assertion_fails_with_message(monkeypatch):
    case = _case_with_assertions(13, [{"path": "data.id", "op": "eq", "value": 7}])
    _exec_with_fake_response(monkeypatch, payload={"data": {"id": 1}})
    src = render_test_file(case)
    ns: dict = {}
    exec(compile(src, "<gen>", "exec"), ns)
    with pytest.raises(AssertionError, match="case 13 断言失败: path=data.id op=eq"):
        ns["test_13"]()


def test_rendered_assertion_passes(monkeypatch):
    case = _case_with_assertions(
        14,
        [
            {"path": "data.id", "op": "eq", "value": 1},
            {"path": "data.name", "op": "exists", "value": None},
            {"path": "data.name", "op": "contains", "value": "ok"},
        ],
    )
    _exec_with_fake_response(monkeypatch, payload={"data": {"id": 1, "name": "ok-200"}})
    src = render_test_file(case)
    ns: dict = {}
    exec(compile(src, "<gen>", "exec"), ns)
    ns["test_14"]()  # 不抛即过


def test_non_json_response_field_assertion_fails_not_errors(monkeypatch):
    # why：500 HTML 页 r.json() 抛 ValueError——按取值 None 断言失败（消息可读），非 error
    case = _case_with_assertions(15, [{"path": "data.id", "op": "eq", "value": 1}])
    monkeypatch.setattr(
        "app.utils.case_generator.get_settings", lambda: _settings_with(auth_enabled=False)
    )
    fake = types.ModuleType("httpx")
    # why：status_code 用 200——基线状态码断言先执行，500 会在字段断言前就失败
    fake.request = lambda *a, **k: SimpleNamespace(
        status_code=200, json=lambda: (_ for _ in ()).throw(ValueError("no json"))
    )
    monkeypatch.setitem(sys.modules, "httpx", fake)
    src = render_test_file(case)
    ns: dict = {}
    exec(compile(src, "<gen>", "exec"), ns)
    with pytest.raises(AssertionError, match="断言失败"):
        ns["test_15"]()


def test_assertion_value_injection_neutralized():
    # 恶意 value：若直接拼代码可闭合字符串注入任意 Python——repr 进字面量后必然失效
    malicious = "x'\nimport os\nos.system('echo PWNED')\n#'"
    case = _case_with_assertions(16, [{"path": "data.id", "op": "eq", "value": malicious}])
    src = render_test_file(case)
    tree = ast.parse(src)  # 语法必须合法
    nodes = [type(n).__name__ for n in tree.body]
    assert nodes == ["Import", "FunctionDef", "FunctionDef"]
    system_calls = [
        n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "system"
    ]
    assert not system_calls, "注入的 os.system 调用出现在可执行代码中"


def test_invalid_legacy_assertion_skipped_as_comment():
    # why：历史库可能存有未过校验的断言——渲染为注释跳过，不炸语法不阻断执行
    case = _case_with_assertions(17, [{"path": "a b", "op": "eq", "value": 1}])
    src = render_test_file(case)
    compile(src, "<test>", "exec")
    assert "跳过非法断言" in src
    assert "_dig" not in src  # 非法条目不计入字段断言，不渲染 payload 块


def test_eq_none_uses_is_comparison():
    case = _case_with_assertions(18, [{"path": "data.err", "op": "eq", "value": None}])
    src = render_test_file(case)
    compile(src, "<test>", "exec")
    assert "_dig(payload, 'data.err') is None" in src
    assert "== None" not in src
