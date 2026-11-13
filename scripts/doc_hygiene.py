# 本项目编码格式：UTF-8（无 BOM）。
"""文档与注释的「去 AI 味」纪律门禁：把可机械判定的部分固化成脚本。

规则源头见 `RULES.md` §20。本脚本只做机器能判的部分，语义判断仍靠人：
  蓄意写歪的动机说明、编造的数字、错的技术事实，正则一律抓不到。

## 检查项

| 代号 | 检查内容 | 范围 |
| :--- | :--- | :--- |
| D1 | 内部工单 / 评审批次编号（`review H4`、`P3-6`、`R2-H1`、`F3`、`A1`…） | 文档正文 + 代码注释/docstring |
| D2 | 协作过程叙述（审查发现 / 评审后 / 本轮 / 本批 / 复核加固…） | 文档正文 |
| D3 | 自贬与自曝措辞（短板 / 练手 / 仅供学习 / 学习交流 / 半成品…） | 文档正文 + 代码注释 |
| D4 | 生成式套话（你的第 N 点 / 如上所述 / 综上所述 / 众所周知…） | 文档正文 + 代码注释 |
| D5 | 指向未公开文件与本机路径（`RULES.md`、`.claude/`、`docs/sessions/`、`E:\\Project`…） | 文档正文 |

## 为什么不用「全仓正则」

AutoAiSelfHeal 用 `[TMHVCBALRD][0-9]` 全量扫描，结果把 HTTP 状态码（`202`/`500`）、
表号、构建号（`build #8`）、徽章 URL 全部判成违规——逼着作者改测试数据与文档事实。
那是为规则服务，不是为代码服务。

本脚本因此只匹配**编号真实出现时的形状**：括号内 / `review X1` / 日期+批次号。
`（202）`（HTTP 202）、`（§2.5）`（章节号）、`build #7/#8`（真实构建号）、
`执行闭环`（交付阶段）、`JENKINS_TIMEOUT`（错误码）都不在匹配范围内。

设计约束：纯标准库（门禁自身不引入依赖）；Windows/Linux 同源（只用 pathlib）。
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import sys
import tokenize
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- 规则常量

# D1 内部编号：只在「编号真实出现」的形状上匹配，避免误伤 HTTP 码与章节号。
#   ① 括号内的短编号：review H4 / P3-6 / R2-H1 / F3 / A1 / N13
#   ② 日期+批次号：2026-10-08 C1
#   ③ 「见 X1」式引用
BATCH_ID_RE = re.compile(
    r"[（(]\s*(?:"
    r"review\s*[A-Z]{1,2}\d{1,2}"  # （review H4）
    r"|[A-Z]{1,2}\d{1,2}(?:[-–][A-Z0-9]{1,3})?"  # （P3-6）（R2-H1）（F3）
    r"|\d{4}-\d{2}-\d{2}\s*[A-Z]\d{1,2}"  # （2026-10-08 C1）
    r")\s*[)）]"
)
# 例外：这些是公开语义，不是工单号。
BATCH_ALLOWED = frozenset(
    {
        "（P0）",
        "（P1）",
        "（P2）",  # 优先级语义，公开文档允许描述优先级
        "（L1）",
        "（L2）",
        "（L3）",
        "（L4）",  # 预留：层级术语
        # 门禁检查代号本身（§21.1 白名单 C 类）。写成 `（E1）` 是在引用检查项，
        # 不是用编号指代工作；漏了它会逼着脚本把自己的检查结果描述成「第一项」这类模糊说法。
        "（A1）",
        "（A2）",
        "（A3）",
        "（D1）",
        "（D2）",
        "（D3）",
        "（D4）",
        "（D5）",
        "（D6）",
        "（E1）",
        "（E2）",
        "（E3）",
        "（E4）",
    }
)

# D2 协作过程叙述：只匹配明确在讲「谁在什么时候审了谁」的句子成分。
PROCESS_RE = re.compile(
    r"审查(?:发现|后|新增|时|中)"
    r"|评审(?:发现|后|新增|时|中)"
    r"|复核(?:后|发现|加固)"
    r"|独立复核"
    r"|收口轮"
    r"|本轮|本批|该批次|本批次"
    r"|立项|审计复现"
    r"|根据.{0,8}(?:review|审查|评审)"
    r"|review\s*[A-Z]?\d"
)

# D3 自贬与自曝：把项目说成练手作业、把边界说成能力缺陷。
SELF_DEPRECATE_RE = re.compile(
    r"短板|自认空白|没做完|未做完|微不足道|玩具项目|简陋|凑合|"
    r"能力不足|不成熟|半成品|练手|试验品|仅供学习|学习交流|个人练手|"
    r"差强人意|拿不出手"
)

# D4 生成式套话。
AI_PHRASE_RE = re.compile(
    r"你的第\s*(?:[0-9]+|[一二三四五六七八九十]+)\s*点"
    r"|你说得对"
    r"|如上所述"
    r"|希望对你有帮助"
    r"|综上所述"
    r"|值得一提的是"
    r"|不难发现"
    r"|显而易见"
    r"|众所周知"
    r"|需要注意的是"
    r"|总的来说"
    r"|接下来我们"
)

# D5 未公开文件与本机路径。why 不含 docs/sessions|plans|reviews：公开文档需要说明这些目录
# 的定位（本地过程记录、不入库），点名目录不等于引用内容；真正不能出现的是私有规则文件、
# 镜像工作区与绝对路径。
UNPUBLIC_RE = re.compile(
    r"RULES\.md|CLAUDE\.md|memory\.md|\.claude/|docs-study|[A-Za-z]:\\\\|[A-Za-z]:\\Project"
)

# D6 已废除的交付阶段编号。why 单列一条：Phase 是被「清完又长回来」最多的编号族，
# 且它总带一个空格式的替换目标（能力名），适合机器直接拦。
# 不匹配 `Phase` 后面不接数字的用法（如英文动词短语以外的正常行文）。
PHASE_RE = re.compile(r"Phase\s*[0-9]+|(?<![A-Za-z])MVP(?![\s]*(?:例外|简化|版本))")

# 文档正文里允许出现的行内豁免声明：写了 `doc-hygiene: ignore` 的行整行跳过。
IGNORE_MARKER = "doc-hygiene: ignore"

# 本门禁自身：源码里就要写出被禁止的字面量（作为规则说明），扫描它等于自指。
SELF_EXEMPT = frozenset({"scripts/doc_hygiene.py"})

# D2 豁免：追加式台账的记录体例就是「哪个批次做了什么」，正文改写成设计叙述会丢时间线。
# D6 同样豁免该文件：台账要记录「本轮清退了哪些编号」，被清退的编号必须作为**对象**被引用
# （「全量清退 `Phase 1`–`Phase 4`」），这与「用编号指代工作」是两回事。
D2_EXEMPT = frozenset({"docs/TODO.md"})
D6_EXEMPT = frozenset({"docs/TODO.md"})

# D5 豁免：这些文档本身已被 .gitignore 忽略（不公开），在其中写私有规则路径是必要说明。
D5_EXEMPT = frozenset({"docs/README.internal.md", "docs/session-doc-template.md"})

# 整篇豁免：内部规范文档要用被禁字面量当反例（「这样写不行」），扫描它等于让规则描述自证违规。
# 该文件已被 .gitignore 忽略，不会公开，故不参与 D1–D5 任何一项。
DOC_EXEMPT_ALL = frozenset({"docs/README.internal.md"})

CODE_SUFFIXES = {".py", ".yml", ".yaml", ".toml", ".bat"}
DOC_GLOBS = ("README.md", "docs/*.md")


# ---------------------------------------------------------------- 数据结构


@dataclass(frozen=True)
class Problem:
    """一条失败明细。why：门禁报告必须能直接定位到「文件:行:列」，否则修不动。"""

    where: str
    line: int
    code: str
    message: str
    sample: str = ""

    def render(self) -> str:
        """渲染成 `文件:行 [D2] 说明 ← 原文片段`。"""
        loc = f"{self.where}:{self.line}" if self.line else self.where
        tail = f"  ← {self.sample}" if self.sample else ""
        return f"    {loc} [{self.code}] {self.message}{tail}"


@dataclass
class RuleResult:
    """一项检查的结果。why：按检查项聚合才看得出「哪条纪律破了」。"""

    code: str
    title: str
    covered: str = ""
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """零失败即通过。"""
        return not self.problems


# ---------------------------------------------------------------- 文本提取


def rel_to(path: Path, base: Path) -> str:
    """相对 base 的 posix 路径；不在 base 下则退回绝对路径。"""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def iter_docs(root: Path) -> Iterator[Path]:
    """受检文档：仓库根 README.md + docs/*.md（不递归进归档目录）。"""
    for pattern in DOC_GLOBS:
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                yield path
    frontend = root.parent / "frontend" / "README.md"
    if frontend.is_file():
        yield frontend


def iter_code(root: Path) -> Iterator[Path]:
    """受检代码：app/ tests/ scripts/ 下的源文件（只看注释与 docstring）。"""
    for sub in ("app", "tests", "scripts"):
        base = root / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix in CODE_SUFFIXES and path.is_file():
                yield path


def python_comment_blocks(src: str) -> list[tuple[int, str]]:
    """取出 .py 的全部 `#` 注释与模块/类/函数 docstring，带行号。

    why 按 tokenize + ast 而不是全文正则：字符串字面量里有测试数据、CSS 颜色、
    模型版本号（`v4.1`），全文正则会误伤它们，逼着作者改测试数据。
    """
    out: list[tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                out.append((tok.start[0], tok.string))
    except (tokenize.TokenError, IndentationError):
        pass
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            value = first.value.value
            if isinstance(value, str):
                out.append((first.lineno, value))
    return out


def hash_comment_blocks(src: str) -> list[tuple[int, str]]:
    """YAML / TOML / bat 的 `#` 注释行（含缩进注释）。"""
    return [
        (lineno, line)
        for lineno, line in enumerate(src.splitlines(), 1)
        if line.lstrip().startswith("#")
    ]


def code_comment_blocks(path: Path) -> list[tuple[int, str]]:
    """按后缀分派取注释块；读不了的文件返回空（门禁不该因单个文件崩掉）。"""
    try:
        src = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    if path.suffix in {".py"}:
        return python_comment_blocks(src)
    return hash_comment_blocks(src)


def snippet(text: str, start: int, end: int, width: int = 46) -> str:
    """截取命中位置前后的片段，供报告直接引用原文。"""
    left = max(0, start - width)
    right = min(len(text), end + width)
    return text[left:right].replace("\n", " ").strip()


# ---------------------------------------------------------------- 检查实现


def scan_text(
    text: str,
    where: str,
    line_base: int,
    patterns: tuple[tuple[str, re.Pattern[str], str], ...],
    result_by_code: dict[str, RuleResult],
) -> None:
    """在一段文本上跑指定规则集，把命中记进对应 RuleResult。

    why 统一入口：文档正文、Python 注释、YAML 注释的扫描逻辑一致，避免两套实现漂移。
    """
    for lineno_offset, raw_line in enumerate(text.splitlines()):
        if IGNORE_MARKER in raw_line:
            continue
        lineno = line_base + lineno_offset
        for code, pattern, message in patterns:
            for match in pattern.finditer(raw_line):
                if match.group(0) in BATCH_ALLOWED:
                    continue
                result_by_code[code].problems.append(
                    Problem(
                        where=where,
                        line=lineno,
                        code=code,
                        message=message,
                        sample=snippet(raw_line, match.start(), match.end()),
                    )
                )


DOC_PATTERNS = (
    ("D1", BATCH_ID_RE, "出现内部工单/评审批次编号，改为自解释描述"),
    ("D2", PROCESS_RE, "出现协作过程叙述，改为「为什么这样做」的设计表述"),
    ("D3", SELF_DEPRECATE_RE, "出现自贬/自曝措辞，改为「边界 + 取舍原因 + 下一步」"),
    ("D4", AI_PHRASE_RE, "出现生成式套话，删除"),
    ("D5", UNPUBLIC_RE, "指向未公开文件或本机路径，改为公开文档路径或不写"),
    ("D6", PHASE_RE, "出现已废除的交付阶段编号，改用能力名（见 RULES.md §21）"),
)

CODE_PATTERNS = (
    ("D1", BATCH_ID_RE, "注释里出现内部工单编号，改为机理描述"),
    ("D3", SELF_DEPRECATE_RE, "注释里出现自贬措辞"),
    ("D4", AI_PHRASE_RE, "注释里出现生成式套话"),
    ("D6", PHASE_RE, "注释里出现已废除的交付阶段编号，改用能力名"),
)


def build_results() -> dict[str, RuleResult]:
    """初始化六项规则结果（含标题），供各项检查共用。"""
    return {
        "D1": RuleResult("D1", "无内部工单 / 评审批次编号"),
        "D2": RuleResult("D2", "文档正文无协作过程叙述"),
        "D3": RuleResult("D3", "无自贬 / 自曝措辞"),
        "D4": RuleResult("D4", "无生成式套话"),
        "D5": RuleResult("D5", "不引用未公开文件与本机路径"),
        "D6": RuleResult("D6", "无已废除的交付阶段编号"),
    }


def run(root: Path) -> list[RuleResult]:
    """执行全部检查。why：main 只做编排与汇总。"""
    results = build_results()
    docs = list(iter_docs(root))
    for doc in docs:
        where = rel_to(doc, root)
        try:
            text = doc.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            results["D1"].problems.append(
                Problem(where=where, line=0, code="D1", message=f"文件不可读或非 UTF-8：{exc}")
            )
            continue
        patterns = tuple(
            item
            for item in DOC_PATTERNS
            if where not in DOC_EXEMPT_ALL
            and not (item[0] == "D2" and where in D2_EXEMPT)
            and not (item[0] == "D5" and where in D5_EXEMPT)
            and not (item[0] == "D6" and where in D6_EXEMPT)
        )
        scan_text(text, where, 1, patterns, results)

    code_count = 0
    for path in iter_code(root):
        where = rel_to(path, root)
        if where in SELF_EXEMPT:
            continue
        blocks = code_comment_blocks(path)
        if not blocks:
            continue
        code_count += 1
        for lineno, block in blocks:
            scan_text(block, where, lineno, CODE_PATTERNS, results)

    exempt = sorted(D2_EXEMPT | D5_EXEMPT)
    results["D1"].covered = f"{len(docs)} 篇文档 / {code_count} 个源码文件"
    for code in ("D2", "D3", "D4", "D5", "D6"):
        results[code].covered = f"{len(docs)} 篇文档"
    if exempt:
        results["D2"].covered += f"；豁免 {', '.join(exempt)}"
    return [results[code] for code in ("D1", "D2", "D3", "D4", "D5", "D6")]


# ---------------------------------------------------------------- 报告与入口

MAX_DETAILS = 40


def emit(rule: RuleResult) -> None:
    """打印单项结果与失败明细。why：CI 日志要能一眼看出「哪条纪律破了、破在哪一行」。"""
    status = "FAIL" if rule.problems else "OK"
    suffix = f"（{rule.covered}）" if rule.covered else ""
    print(f"[{status}] {rule.code} {rule.title}{suffix}")
    for problem in rule.problems[:MAX_DETAILS]:
        print(problem.render())
    if len(rule.problems) > MAX_DETAILS:
        print(f"    …另有 {len(rule.problems) - MAX_DETAILS} 项未列出")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析参数。why：默认指向脚本父目录的上一级（backend/），也允许指向任意仓库根做预检。"""
    parser = argparse.ArgumentParser(
        description="文档与注释的「去 AI 味」纪律门禁（D1–D5，规则见 RULES.md §20）"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="仓库根，默认脚本父目录的上一级（backend/）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """门禁入口：打印报告与汇总，返回退出码（0 通过 / 1 有失败 / 2 路径错误）。"""
    args = parse_args(argv)
    root = args.root.expanduser().resolve()
    if not (root / "docs").is_dir():
        print(f"错误：--root 下没有 docs/ 目录，疑似路径不对：{root}", file=sys.stderr)
        return 2

    print("文档与注释「去 AI 味」纪律门禁：内部编号 / 过程叙述 / 自贬 / 套话 / 未公开引用")
    print(f"  根目录：{root}")
    print()

    rules = run(root)
    for rule in rules:
        emit(rule)

    failed = [rule for rule in rules if not rule.ok]
    print()
    print("== 汇总 ==")
    detail = f"{len(rules) - len(failed)}/{len(rules)} 项通过"
    if failed:
        detail += "：" + "、".join(f"{rule.code}（{len(rule.problems)} 项）" for rule in failed)
    print(f"[{'FAIL' if failed else 'OK'}] {detail}")
    exit_code = 1 if failed else 0
    total = sum(len(rule.problems) for rule in failed)
    print(f"exit code = {exit_code}" + (f"（失败明细共 {total} 项）" if failed else ""))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
