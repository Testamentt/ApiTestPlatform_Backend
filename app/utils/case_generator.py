# 测试文件生成。why：结构化字段 repr 插值（无 Jinja2）；函数名恒 test_{case_id} 便于 JUnit 回映射；
# repr 转义防止用户字段破坏 Python 语法。MVP 只断言 expected_status。
from __future__ import annotations

from app.core.config import get_settings


def render_test_file(case) -> str:
    settings = get_settings()
    base_url = settings.execution.base_url
    timeout = settings.execution.pytest_timeout
    params = case.params or {}
    body = case.body
    return (
        f'"""case {case.id}: {case.name}"""\n'
        "import httpx\n\n"
        f"def test_{case.id}():\n"
        f"    url = {base_url!r} + {case.path!r}\n"
        f"    r = httpx.request({case.method!r}, url, params={params!r}, "
        f"json={body!r}, timeout={timeout})\n"
        f"    assert r.status_code == {case.expected_status}  # MVP 只校验状态码\n"
    )
