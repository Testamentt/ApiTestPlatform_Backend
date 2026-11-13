# 本项目编码格式：UTF-8（无 BOM）。
"""文档与注释「去 AI 味」纪律门禁的守卫测试。

被测对象：`scripts/doc_hygiene.py`（规则见 `RULES.md` §20）。

**为什么要有这些用例**：门禁脚本最常见的事故是「空转的绿色」——正则写歪了，
所有检查都通过，但违规内容照样进仓库。所以这里除了「当前仓库干净」，
还必须证明三件事：

1. 五种违规各自**真能被抓到**（不是只测 D1）；
2. HTTP 状态码、章节号、构建号、错误码这类正常技术内容**不被误伤**
   （误伤比漏判更糟：会逼着作者改事实）；
3. 豁免清单**只对声明过的文件生效**（否则等于给全仓开后门）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "doc_hygiene.py"


def _load_module():
    """按文件路径加载门禁脚本。why：scripts/ 不是包，直接 import 会失败。"""
    spec = importlib.util.spec_from_file_location("doc_hygiene", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["doc_hygiene"] = module
    spec.loader.exec_module(module)
    return module


hygiene = _load_module()


def _scan_text(text: str, patterns) -> dict[str, hygiene.RuleResult]:
    """把一段文本喂给指定规则集，返回按代号聚合的结果。"""
    results = hygiene.build_results()
    hygiene.scan_text(text, "sample.md", 1, patterns, results)
    return results


def _codes_hit(text: str, patterns) -> set[str]:
    """返回这段文本命中的规则代号集合。"""
    results = _scan_text(text, patterns)
    return {code for code, result in results.items() if result.problems}


class TestRealDocumentationIsClean:
    """仓库当前状态：五项必须全通过。这条用例就是 CI 门禁本身。"""

    def test_repository_passes_all_checks(self):
        rules = hygiene.run(ROOT)
        failed = [f"{r.code}（{len(r.problems)} 项）" for r in rules if not r.ok]
        assert not failed, (
            "去 AI 味门禁未通过："
            + "、".join(failed)
            + "\n按 RULES.md §20 改：编号改自解释描述、过程叙述改设计表述、"
            "自贬改「边界 + 取舍 + 下一步」、套话删除、私有路径改公开路径。"
        )


class TestEachRuleCatchesItsShape:
    """五条规则各自的自证：构造典型违规，必须被抓到。"""

    def test_d1_batch_id_in_parens(self):
        """D1：括号里的评审批次号。"""
        for text in (
            "耗时降低 30%（review H4）。",
            "此处按 L1 缓存处理（P3-6）。",
            "并发保护（R2-H1）已合并。",
            "失败重试（2026-10-08 C1）。",
        ):
            assert "D1" in _codes_hit(text, hygiene.DOC_PATTERNS), text

    def test_d2_process_narration(self):
        """D2：谁在什么时候审了谁。"""
        for text in (
            "本轮补了限流。",
            "审查发现任务会卡在 running。",
            "评审后把校验前置。",
            "收口轮又补了一条。",
        ):
            assert "D2" in _codes_hit(text, hygiene.DOC_PATTERNS), text

    def test_d3_self_deprecation(self):
        """D3：把项目说成不值一提的副业、或公开声明只做交流用。"""
        for text in (
            "样本量 3 是我的短板。",
            "本项目仅用于学习交流。",
            "这个模块还是个半成品。",
        ):
            assert "D3" in _codes_hit(text, hygiene.DOC_PATTERNS), text

    def test_d4_generated_phrases(self):
        """D4：生成式套话。"""
        for text in (
            "你的第 3 点说得对。",
            "如上所述，这里不再重复。",
            "综上所述，该方案可行。",
            "众所周知，SQLite 单写。",
        ):
            assert "D4" in _codes_hit(text, hygiene.DOC_PATTERNS), text

    def test_d5_private_references(self):
        """D5：引用未公开文件或本机路径。"""
        for text in (
            "详见 RULES.md §19。",
            "规则见 .claude/rules/。",
            "镜像在 E:\\Project\\docs-study。",
        ):
            assert "D5" in _codes_hit(text, hygiene.DOC_PATTERNS), text

    def test_d6_abolished_phase_ids(self):
        """D6：已废除的交付阶段编号必须拦住（Phase 编号与阶段代称型缩写）。"""
        for text in (
            "Phase 2 已实现影响分析。",
            "兼容已部署的 Phase 1 配置。",
            "这里按 Phase 4 的形态处理。",
            "MVP 零前端，默认不发 CORS 头。",  # doc-hygiene: ignore
        ):
            assert "D6" in _codes_hit(text, hygiene.DOC_PATTERNS), text


class TestNoFalsePositivesOnTechnicalContent:
    """反向自证：正常技术内容一律放行，否则门禁会逼人改事实。"""

    @pytest.mark.parametrize(
        "text",
        [
            "入队后立即返回 202，失败返回 503。",  # HTTP 状态码
            "接口返回 422 / 500 / 502 / 504。",  # 错误码
            "见 architecture.md §2、configuration.md §2.10。",  # 章节号
            "门禁阈值 60 / 80；覆盖率 93.14%。",  # 阈值与实测值
            "真实 Jenkins build #7/#8 SUCCESS。",  # 构建号
            "错误码 `JENKINS_TIMEOUT` 落 `error_stage=timeout`。",  # 常量名
            "`celery.pending_timeout` 默认 600 s。",  # 配置键
            "pytest -q → 386 passed / 4 deselected。",  # 测试计数
            "（P0）优先级最高，（P1）次之。",  # 优先级语义（白名单）
            "把 1695 tokens、$0.001695 写进台账。",  # 成本数字
            "MVP 例外持续生效：运行期 create_all。",  # 「MVP 例外」是既有契约名，不算阶段代称
            "只豁免 E3 一项（E1 / E2 / E4 照常）。",  # 门禁代号，属白名单 C 类
            "门禁分 D1–D6 六项（D1 与 D6 各管一类）。",  # 同上
        ],
    )
    def test_ordinary_content_is_allowed(self, text):
        assert _codes_hit(text, hygiene.DOC_PATTERNS) == set(), text


class TestRuleScopes:
    """规则适用范围。"""

    def test_code_patterns_skip_docs_only_rules(self):
        """代码注释只查 D1/D3/D4/D6；D2（过程叙述）与 D5（私有路径）只约束文档。"""
        codes = {code for code, _, _ in hygiene.CODE_PATTERNS}
        assert codes == {"D1", "D3", "D4", "D6"}


class TestWhitelistPrecision:
    """白名单精度：放过门禁代号，但不能顺手放过真正的评审批次号。"""

    def test_gate_codes_allowed(self):
        """门禁代号（§21.1 白名单 C 类）属正当引用，不该被判成批次编号。"""
        assert _codes_hit("（E1）与（D6）是检查代号。", hygiene.DOC_PATTERNS) == set()

    def test_adjacent_batch_ids_still_caught(self):
        """相邻形态必须仍然命中：P3-6 是真批次号，E1 是代号，只差字母与数字位置。"""
        assert "D1" in _codes_hit("按（P3-6）处理。", hygiene.DOC_PATTERNS)
        assert "D1" in _codes_hit("按（review H4）处理。", hygiene.DOC_PATTERNS)


class TestCommentExtraction:
    """注释提取：只取人写注释，不碰字符串字面量。"""

    def test_python_comments_and_docstrings(self):
        src = (
            '"""模块说明。"""\n'
            "\n"
            "def f():\n"
            '    """函数说明。"""\n'
            "    x = '#old'  # 行尾注释\n"
            "    return x\n"
        )
        blocks = hygiene.python_comment_blocks(src)
        joined = "\n".join(text for _, text in blocks)
        assert "模块说明" in joined
        assert "函数说明" in joined
        assert "行尾注释" in joined
        assert "#old" not in joined, "字符串字面量里的内容不该被当成注释"

    def test_yaml_hash_comments(self):
        src = "# 顶部注释\nkey: value\n  # 缩进注释\n"
        blocks = hygiene.hash_comment_blocks(src)
        assert len(blocks) == 2

    def test_ignore_marker_skips_line(self):
        """行内豁免：写了标记的行整行跳过（用于必须引用字面量的说明句）。"""
        results = _scan_text("本轮补了限流。  <!-- doc-hygiene: ignore -->", hygiene.DOC_PATTERNS)
        assert results["D2"].problems == []


class TestExemptionScopes:
    """豁免清单必须精确到文件：漏一条等于全仓开后门。"""

    def test_todo_is_exempt_from_d2_only(self):
        """追加式台账豁免 D2（记录体例），但不豁免 D1/D3/D4/D5。"""
        assert "docs/TODO.md" in hygiene.D2_EXEMPT
        assert "docs/TODO.md" not in hygiene.D5_EXEMPT

    def test_internal_doc_is_fully_exempt(self):
        """内部规范文档要用被禁字面量当反例，整篇豁免；它本身已被 .gitignore 忽略。"""
        assert "docs/README.internal.md" in hygiene.DOC_EXEMPT_ALL
        assert "docs/README.internal.md" in hygiene.D5_EXEMPT

    def test_self_script_is_exempt_from_code_scan(self):
        """门禁脚本自身要写出被禁字面量作为规则说明，扫描它等于自指。"""
        assert "scripts/doc_hygiene.py" in hygiene.SELF_EXEMPT

    def test_exemption_lists_are_small_and_explicit(self):
        """豁免总量必须极小：一旦膨胀就说明规则写歪了，正在用豁免掩盖误伤。"""
        assert len(hygiene.DOC_EXEMPT_ALL) <= 2
        assert len(hygiene.SELF_EXEMPT) <= 2
        assert len(hygiene.D2_EXEMPT) + len(hygiene.D5_EXEMPT) <= 4
