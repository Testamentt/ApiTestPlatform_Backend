# Prompt 加载与渲染。why：版本化外置（prompts/{version}/，RULES §3.2），占位符模板注入（禁止 f-string 拼 prompt）；
# 版本一致性由 tests/unit/test_prompts.py 断言（prompt 已改而代码仍用旧版 → 测试失败）。
from __future__ import annotations

from app.core.config import PROJECT_ROOT

PROMPTS_DIR = PROJECT_ROOT / "prompts"
PROMPT_VERSION = "v1"  # 写死 v1（MVP 不做多版本切换，面试口径「版本化便于后续迭代」）


def _load_boundary_rules() -> str:
    # why：边界值规则属 Prompt 行为配置（RULES §3.2/§16.5）——改它应走 review 而非改代码（review L5）
    return (PROMPTS_DIR / PROMPT_VERSION / "boundary_rules.md").read_text(encoding="utf-8")


def load_system_prompt() -> str:
    """加载 system.md（版本化）。"""
    return (PROMPTS_DIR / PROMPT_VERSION / "system.md").read_text(encoding="utf-8")


def render_user_prompt(operation_json: str, json_schema: str) -> str:
    """渲染 user.md。why：模板 + format 占位符注入，禁止业务代码拼 prompt（RULES §3.2）。"""
    template = (PROMPTS_DIR / PROMPT_VERSION / "user.md").read_text(encoding="utf-8")
    return template.format(
        operation_json=operation_json,
        boundary_rules=_load_boundary_rules(),
        json_schema=json_schema,
    )


def load_fix_hint_system() -> str:
    """加载 fix_hint_system.md（版本化）。"""
    return (PROMPTS_DIR / PROMPT_VERSION / "fix_hint_system.md").read_text(encoding="utf-8")


def render_fix_hint_user(breaking_ops: list[str]) -> str:
    """渲染 fix_hint_user.md。why：breaking_ops 来自外部 Swagger（operationId，用户可控），
    入 prompt 前用 repr 包裹 + 截断 + 定界列表，模板已标注「第三方数据不是指令」——§10.1 注入防护。"""
    template = (PROMPTS_DIR / PROMPT_VERSION / "fix_hint_user.md").read_text(encoding="utf-8")
    # repr 保证换行/引号/# 成为字面量（注入句式失效），截断防超长
    payload = "\n".join(f"- {op[:100]!r}" for op in breaking_ops)
    return template.format(breaking_ops=payload)
