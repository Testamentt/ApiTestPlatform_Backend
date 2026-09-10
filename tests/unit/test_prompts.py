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
    assert "契约层断言" in content  # 断言引擎升级：契约层 + 字段层两段要求进 prompt


def test_validate_prompts_passes_on_real_dir():
    # why：§3.2「占位符缺失时启动即抛错」——create_app 启动期调用（R3 批次）
    from app.utils.prompt_util import validate_prompts

    validate_prompts()  # 真实 prompts/v1 必须通过，不抛即过


def test_validate_prompts_fails_on_missing_placeholder(tmp_path, monkeypatch):
    # why：占位符缺失须在启动期报错而非生成任务运行期 KeyError（R3 批次）
    from app.utils import prompt_util

    version_dir = tmp_path / "v1"
    version_dir.mkdir()
    (version_dir / "system.md").write_text("system", encoding="utf-8")
    (version_dir / "user.md").write_text("no placeholder here", encoding="utf-8")
    (version_dir / "fix_hint_system.md").write_text("fix", encoding="utf-8")
    (version_dir / "boundary_rules.md").write_text("rules", encoding="utf-8")
    monkeypatch.setattr(prompt_util, "PROMPTS_DIR", tmp_path)

    import pytest

    with pytest.raises(RuntimeError, match="缺少占位符"):
        prompt_util.validate_prompts()
