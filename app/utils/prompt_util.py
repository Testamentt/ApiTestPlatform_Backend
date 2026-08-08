# Prompt 加载与渲染。why：版本化外置（prompts/{version}/，RULES §3.2），占位符模板注入（禁止 f-string 拼 prompt）；
# 版本一致性由 tests/unit/test_prompts.py 断言（prompt 已改而代码仍用旧版 → 测试失败）。
from __future__ import annotations

from app.core.config import PROJECT_ROOT

PROMPTS_DIR = PROJECT_ROOT / "prompts"
PROMPT_VERSION = "v1"  # 写死 v1（MVP 不做多版本切换，面试口径「版本化便于后续迭代」）

_BOUNDARY_RULES = """正向（用 default/example 值验证成功响应）
缺参（逐个移除必填参数 → 4xx）
类型错误（string↔int 互换 → 422/400）
枚举合法/非法（枚举每项 / 枚举外值 → 4xx）
越界（minimum-1 / maximum+1、长度超限、format 违规）
鉴权异常（无 token 401 / 错误 token 403）
资源不存在（不存在的资源 ID → 404）"""


def load_system_prompt() -> str:
    """加载 system.md（版本化）。"""
    return (PROMPTS_DIR / PROMPT_VERSION / "system.md").read_text(encoding="utf-8")


def render_user_prompt(operation_json: str, json_schema: str) -> str:
    """渲染 user.md。why：模板 + format 占位符注入，禁止业务代码拼 prompt（RULES §3.2）。"""
    template = (PROMPTS_DIR / PROMPT_VERSION / "user.md").read_text(encoding="utf-8")
    return template.format(
        operation_json=operation_json,
        boundary_rules=_BOUNDARY_RULES,
        json_schema=json_schema,
    )
