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
    # why：case.name 只进「文件头注释」且必须 repr——它允许换行/引号/#，
    # 若进入 docstring/代码区可闭合字符串注入任意 Python 代码（匿名 RCE）。
    # repr 保证无裸换行（换行转义为 \n 字面量），注释不解析转义序列，二者叠加后注入必然失效。
    # 其余字段（path/method/params/body/base_url）已用 !r 进字符串字面量，无注入风险。
    safe_name = f"{case.name!r}"
    return (
        f"# case {case.id}: {safe_name}\n"
        "import httpx\n\n"
        f"def test_{case.id}():\n"
        f"    url = {base_url!r} + {case.path!r}\n"
        f"    r = httpx.request({case.method!r}, url, params={params!r}, "
        f"json={body!r}, timeout={timeout})\n"
        f"    assert r.status_code == {case.expected_status}  # MVP 只校验状态码\n"
    )
