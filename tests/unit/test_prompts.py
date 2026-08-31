# Prompt 版本一致性断言（RULES §3.2）：代码引用版本与 prompts/ 实际文件一致 + 占位符齐全。
from __future__ import annotations

from app.utils.prompt_util import PROMPT_VERSION, PROMPTS_DIR


def test_prompt_files_exist_for_version():
    assert (PROMPTS_DIR / PROMPT_VERSION / "system.md").exists()
    assert (PROMPTS_DIR / PROMPT_VERSION / "user.md").exists()
    assert (PROMPTS_DIR / PROMPT_VERSION / "boundary_rules.md").exists()  # review L5 外置


def test_user_prompt_placeholders_all_present():
    content = (PROMPTS_DIR / PROMPT_VERSION / "user.md").read_text(encoding="utf-8")
    for placeholder in ("{operation_json}", "{boundary_rules}", "{json_schema}"):
        assert placeholder in content, f"占位符缺失: {placeholder}"


def test_system_prompt_has_output_contract():
    content = (PROMPTS_DIR / PROMPT_VERSION / "system.md").read_text(encoding="utf-8")
    assert '"cases"' in content
    assert "不得凭空编造" in content  # 防幻觉约束在 Prompt 层也显式声明


def test_system_prompt_requires_assertions():
    # why：真实 LLM 冒烟发现生成用例 assertions 恒为空——prompt 层强约束「至少 1 条断言」
    content = (PROMPTS_DIR / PROMPT_VERSION / "system.md").read_text(encoding="utf-8")
    assert "至少 1 条" in content
    assert '"op"' in content  # 断言结构契约（path/op/value）需在白名单中定义
