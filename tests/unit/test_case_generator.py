# case_generator 单元测试：渲染合法 Python + 断言状态码。
from __future__ import annotations

from app.models.test_case import TestCase
from app.utils.case_generator import render_test_file


def test_renders_valid_python():
    case = TestCase(
        id=1, name="get users", method="GET", path="/get",
        operation_id="httpbin_get", expected_status=200, status="active",
    )
    src = render_test_file(case)
    compile(src, "<test>", "exec")  # 语法必须合法
    assert f"def test_{case.id}" in src
    assert "assert r.status_code == 200" in src


def test_body_injected():
    case = TestCase(
        id=2, name="post", method="POST", path="/post", operation_id="x",
        body={"a": 1}, expected_status=201, status="active",
    )
    src = render_test_file(case)
    assert "{'a': 1}" in src
