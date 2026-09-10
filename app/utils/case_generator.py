# 测试文件生成。why：结构化字段 repr 插值（无 Jinja2）；函数名恒 test_{case_id} 便于 JUnit 回映射；
# repr 转义防止用户字段破坏 Python 语法。断言引擎：契约层（status_code 基线恒渲染）+ 字段层
# （assertions 逐条渲染，path/op 已过 AssertionItem 白名单校验，value repr 进字面量——
# 渲染层无注入面）；历史库中的非法断言条目渲染为注释跳过，不阻断执行。
from __future__ import annotations

from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas.assertion import AssertionItem

# why：点路径取值函数随用例文件生成（每个文件自包含，pytest 按模块独立收集）；
# 数字段取数组下标，dict 取键，取不到/非容器一律返回 None——字段断言失败而非 error，
# 保证非 JSON 响应（如 500 HTML 页）下断言消息仍可读
_DIG_SRC = (
    "def _dig(obj, path):\n"
    "    cur = obj\n"
    "    for part in path.split('.'):\n"
    "        if isinstance(cur, list):\n"
    "            try:\n"
    "                cur = cur[int(part)]\n"
    "            except (ValueError, IndexError):\n"
    "                return None\n"
    "        elif isinstance(cur, dict):\n"
    "            cur = cur.get(part)\n"
    "        else:\n"
    "            return None\n"
    "    return cur\n"
)

_PAYLOAD_SRC = (
    "    try:\n"
    "        payload = r.json()\n"
    "    except ValueError:\n"
    "        payload = None  # 非 JSON 响应：字段断言按取值 None 失败，不抛 error\n"
)


def _render_assertion_stmts(case) -> tuple[list[str], bool]:
    """把 case.assertions 渲染为生成的 pytest 断言语句。

    Returns (语句列表, 是否需要 payload 解析块)。非法条目（历史数据/绕过校验落库）渲染为
    注释跳过——渲染层不做第二道校验防线，只保证不炸语法。
    """
    stmts: list[str] = []
    needs_payload = False
    for raw in case.assertions or []:
        try:
            item = AssertionItem.model_validate(raw)
        except ValidationError:
            stmts.append(f"    # 跳过非法断言（历史数据，不阻断执行）: {str(raw)[:200]!r}\n")
            continue
        msg = repr(f"case {case.id} 断言失败: path={item.path} op={item.op} expected={item.value!r}")
        if item.path == "status_code":
            actual = "r.status_code"
        else:
            needs_payload = True
            actual = f"_dig(payload, {item.path!r})"
        # why：value=None 时用 is/is not——与 None 比较的正确写法，避免 `== None` 语法噪音
        if item.op == "eq":
            op_text = "is" if item.value is None else "=="
            stmts.append(f"    assert {actual} {op_text} {item.value!r}, {msg}\n")
        elif item.op == "ne":
            op_text = "is not" if item.value is None else "!="
            stmts.append(f"    assert {actual} {op_text} {item.value!r}, {msg}\n")
        elif item.op == "contains":
            stmts.append(f"    assert {item.value!r} in {actual}, {msg}\n")
        else:  # exists
            stmts.append(f"    assert {actual} is not None, {msg}\n")
    return stmts, needs_payload


def render_test_file(case) -> str:
    ex = get_settings().execution
    base_url = ex.base_url
    timeout = ex.pytest_timeout
    params = case.params or {}
    body = case.body
    # why：case.name 只进「文件头注释」且必须 repr——它允许换行/引号/#，
    # 若进入 docstring/代码区可闭合字符串注入任意 Python 代码（匿名 RCE）。
    # repr 保证无裸换行（换行转义为 \n 字面量），注释不解析转义序列，二者叠加后注入必然失效。
    # 其余字段（path/method/params/body/base_url/header 名）已用 !r 进字符串字面量，无注入风险。
    safe_name = f"{case.name!r}"
    if ex.auth_enabled:
        # why：被测系统需要登录态时，token 由 workspace conftest 的 session fixture 提供，
        # 用例文件只引用 fixture——登录细节/凭证不进用例（§8），AI 生成链路对 headers 无感知
        request_line = (
            f"    r = httpx.request({case.method!r}, url, params={params!r}, "
            f"json={body!r}, headers={{{ex.auth_token_header!r}: token}}, timeout={timeout})\n"
        )
        signature = "(token)"
    else:
        request_line = (
            f"    r = httpx.request({case.method!r}, url, params={params!r}, "
            f"json={body!r}, timeout={timeout})\n"
        )
        signature = "()"
    assertion_stmts, needs_payload = _render_assertion_stmts(case)
    parts = [f"# case {case.id}: {safe_name}\nimport httpx\n\n\n"]
    if needs_payload:
        parts.append(_DIG_SRC)
        parts.append("\n\n")
    parts.append(f"def test_{case.id}{signature}:\n")
    parts.append(f"    url = {base_url!r} + {case.path!r}\n")
    parts.append(request_line)
    parts.append(f"    assert r.status_code == {case.expected_status}  # 契约层基线\n")
    if needs_payload:
        parts.append(_PAYLOAD_SRC)
    parts.extend(assertion_stmts)
    return "".join(parts)


def render_conftest() -> str:
    """被测系统登录 conftest（auth_enabled 时随执行 workspace 生成）。

    why：session 级登录一次取 token，全部用例共享（N 个用例只登 1 次，不压登录接口）；
    凭证在 pytest 运行时从环境变量读、绝不写入生成文件（§8）；登录体字段名/密码编码/token
    提取路径均来自 config——被测系统各异，适配是配置问题而非代码问题。
    """
    ex = get_settings().execution
    return (
        "# 自动生成（execution.auth_enabled）：被测系统登录 fixture，勿手工修改\n"
        "import hashlib\n"
        "import os\n"
        "\n"
        "import httpx\n"
        "import pytest\n"
        "\n"
        f"BASE_URL = {ex.base_url!r}\n"
        f"LOGIN_PATH = {ex.auth_login_path!r}\n"
        f"LOGIN_BODY = {ex.auth_login_body!r}\n"
        f"USERNAME_ENV = {ex.auth_username_env!r}\n"
        f"PASSWORD_ENV = {ex.auth_password_env!r}\n"
        f"TOKEN_FIELD = {ex.auth_token_field!r}\n"
        f"PASSWORD_ENCODING = {ex.auth_password_encoding!r}\n"
        f"LOGIN_TIMEOUT = {ex.pytest_timeout!r}  # 来自 config（§2.3）\n"
        "\n\n"
        '@pytest.fixture(scope="session")\n'
        "def token():\n"
        '    username = os.environ.get(USERNAME_ENV, "")\n'
        '    password = os.environ.get(PASSWORD_ENV, "")\n'
        "    if not username or not password:\n"
        '        pytest.fail(f"登录凭证缺失: {USERNAME_ENV}/{PASSWORD_ENV} 未设置")\n'
        '    if PASSWORD_ENCODING == "md5":\n'
        "        password = hashlib.md5(password.encode()).hexdigest()\n"
        "    body = {\n"
        '        k: v.replace("{username}", username).replace("{password}", password)\n'
        "        for k, v in LOGIN_BODY.items()\n"
        "    }\n"
        "    r = httpx.post(BASE_URL + LOGIN_PATH, json=body, timeout=LOGIN_TIMEOUT)\n"
        "    value = r.json()\n"
        '    for part in TOKEN_FIELD.split("."):\n'
        "        value = value.get(part) if isinstance(value, dict) else None\n"
        "        if value is None:\n"
        "            break\n"
        "    if not value:\n"
        '        pytest.fail(f"登录未获取到 token（HTTP {r.status_code}），路径 {TOKEN_FIELD}")\n'
        "    return value\n"
    )
