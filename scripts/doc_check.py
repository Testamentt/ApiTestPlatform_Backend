# 本项目编码格式：UTF-8（无 BOM）。
# 文档结构门禁：把「文档规范」里可机械判定的部分固化成脚本，由 CI 在 pytest 之前调用。
# why：相对链接失效、代码围栏漏标语言、H2 编号错位这三类漂移靠人工扫必然漏，且一次批量改名
# 就会整仓破；写进脚本后，改完文档当场就能看到红灯，不必等下一轮人工审计。
#
# 检查项（范围：README.md + docs/*.md，不递归；历史归档目录按约定不回改，不纳入）：
#   A1 Markdown 相对链接可达
#   A2 代码围栏均标注允许的语言
#   A3 H2 为 `## N.` 且编号 1..N 连续
#
# 设计约束：纯标准库（门禁自身不引入依赖）；Windows/Linux 同源（只用 pathlib，不拼反斜杠）。
from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- 规则常量

# 允许的围栏语言：docs/README.md §3 第 4 条。出现未列出的语言即规范漂移，需显式加白（不静默放过）。
ALLOWED_FENCE_LANGS = frozenset(
    {"bash", "python", "sql", "yaml", "json", "text", "mermaid", "html"}
)

# H2 编号豁免：追加式台账与模板类文件按日期/条目分组，不强制 `## N.`。
H2_EXEMPT = frozenset({"docs/TODO.md", "docs/session-doc-template.md"})

# 单项失败明细上限：防止大范围漂移时刷屏淹没 CI 日志（超出部分只报计数）。
MAX_DETAILS = 60

FENCE_LINE_RE = re.compile(r"^\s{0,3}(?P<fence>```+|~~~+)(?P<info>.*)$")
LINK_RE = re.compile(r"\[[^\]]*\]\(\s*(?P<target><[^>]*>|[^()\s]+)(?:\s+[\"'][^\"']*[\"'])?\s*\)")
H2_NUMBERED_RE = re.compile(r"^##\s+(?P<num>\d+)\.\s+\S")
# 相对链接跳过的前缀：外链、邮件、纯锚点（仓库内链接才用相对路径）。
LINK_SKIP_PREFIXES = ("http://", "https://", "mailto:", "#")


# ---------------------------------------------------------------- 数据结构


@dataclass(frozen=True)
class Problem:
    """一条失败明细。why：门禁报告必须能直接定位到「文件:行」，否则修不动。"""

    where: str  # 相对路径
    line: int  # 行号，1 起；0 表示整篇（如文件不可读）
    message: str

    def render(self) -> str:
        """渲染成 `文件:行 说明`；行号为 0（整篇级问题）时不打印行号。"""
        loc = f"{self.where}:{self.line}" if self.line else self.where
        return f"    {loc} {self.message}"


@dataclass
class RuleResult:
    """一项检查的结果。why：报告按「检查项」聚合，才看得出是哪条规范破了。"""

    code: str  # A1 / A2 / A3
    title: str
    covered: str = ""  # 覆盖描述（篇数/条数/围栏数），让 [OK] 也带证据
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """是否通过：零失败即通过。"""
        return not self.problems


# ---------------------------------------------------------------- 通用工具


def rel_to(path: Path, base: Path) -> str:
    """计算相对 base 的 posix 路径；不在 base 下则退回绝对路径。why：报告路径要跨平台可回查。"""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def read_doc(path: Path, where: str) -> tuple[str | None, Problem | None]:
    """读 UTF-8 文本，返回 (内容, 读取失败明细)。

    why：单篇文件缺失/编码异常不该让整个门禁崩掉，而应变成一条可读的失败明细。
    """
    try:
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, Problem(where, 0, f"文件不可读或非 UTF-8：{exc}")


def iter_prose_lines(text: str) -> Iterator[tuple[int, str]]:
    """产出正文行 (行号, 内容)，跳过围栏内代码。

    why：编号与格式规则只看正文——代码块里的示例（如命令、旧口径样例）不算违规。
    """
    inside = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if FENCE_LINE_RE.match(line):
            inside = not inside
            continue
        if not inside:
            yield lineno, line


# ---------------------------------------------------------------- A 源文档结构


def source_docs(root: Path) -> list[Path]:
    """受检源文档 = README.md + docs/*.md（不递归）。

    why：历史归档目录按约定「不回改」，纳入门禁只会制造不可修的红灯。
    """
    docs = [root / "README.md"]
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        docs.extend(sorted(docs_dir.glob("*.md")))
    return [path for path in docs if path.is_file()]


def check_links(root: Path, docs: list[Path]) -> RuleResult:
    """A1：Markdown 相对链接可达。why：改名/挪目录后最易留下死链，人工扫必然漏。"""
    result = RuleResult("A1", "Markdown 相对链接可达")
    total = 0
    for doc in docs:
        where = rel_to(doc, root)
        text, failure = read_doc(doc, where)
        if failure is not None:
            result.problems.append(failure)
            continue
        assert text is not None
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in LINK_RE.finditer(line):
                target = match.group("target").strip().strip("<>")
                if not target or target.startswith(LINK_SKIP_PREFIXES):
                    continue
                relative = target.split("#", 1)[0].strip()
                if not relative:
                    continue  # 纯锚点
                total += 1
                if not (doc.parent / relative).exists():
                    result.problems.append(Problem(where, lineno, f"相对链接目标不存在：{target}"))
    result.covered = f"{len(docs)} 篇 / {total} 条相对链接"
    return result


def check_fences(root: Path, docs: list[Path]) -> RuleResult:
    """A2：代码围栏必须标语言且在允许清单内。why：裸围栏渲染后无高亮，是规范里最先破的一条。"""
    result = RuleResult("A2", "代码围栏均标注允许的语言")
    total = 0
    for doc in docs:
        where = rel_to(doc, root)
        text, failure = read_doc(doc, where)
        if failure is not None:
            result.problems.append(failure)
            continue
        assert text is not None
        inside = False
        for lineno, line in enumerate(text.splitlines(), 1):
            match = FENCE_LINE_RE.match(line)
            if match is None:
                continue
            info = match.group("info").strip()
            if inside:
                inside = False  # 闭合围栏
                continue
            inside = True
            total += 1
            if not info:
                result.problems.append(Problem(where, lineno, "裸代码围栏：缺少语言标注"))
            elif info.lower() not in ALLOWED_FENCE_LANGS:
                allowed = "/".join(sorted(ALLOWED_FENCE_LANGS))
                result.problems.append(
                    Problem(where, lineno, f"围栏语言「{info}」不在允许清单（{allowed}）")
                )
    result.covered = f"{len(docs)} 篇 / {total} 个围栏"
    return result


def check_h2_numbering(root: Path, docs: list[Path]) -> RuleResult:
    """A3：H2 为 `## N.` 且编号 1..N 连续。why：层级错位（`## 4.1`）与重复编号人眼扫不出来。"""
    result = RuleResult("A3", "H2 编号连续且为 `## N.` 形式")
    checked: list[str] = []
    exempt: list[str] = []
    for doc in docs:
        where = rel_to(doc, root)
        if where in H2_EXEMPT:
            exempt.append(where)
            continue
        text, failure = read_doc(doc, where)
        if failure is not None:
            result.problems.append(failure)
            continue
        assert text is not None
        checked.append(where)
        numbers: list[tuple[int, int]] = []
        for lineno, line in iter_prose_lines(text):
            if not line.startswith("##") or line.startswith("###"):
                continue
            match = H2_NUMBERED_RE.match(line)
            if match is None:
                result.problems.append(
                    Problem(where, lineno, f"H2 未按 `## N.` 编号：{line.strip()}")
                )
                continue
            numbers.append((lineno, int(match.group("num"))))
        for expected, (lineno, actual) in enumerate(numbers, start=1):
            if actual != expected:
                result.problems.append(
                    Problem(where, lineno, f"H2 编号不连续：期望 {expected}，实际 {actual}")
                )
    result.covered = f"{len(checked)} 篇受检 / {len(exempt)} 篇豁免（{', '.join(exempt)}）"
    return result


# ---------------------------------------------------------------- B 报告与入口


def emit(rule: RuleResult) -> None:
    """打印单项结果：状态行 + 失败明细。why：CI 日志要能一眼看出「哪条规范破了、破在哪一行」。"""
    status = "FAIL" if rule.problems else "OK"
    suffix = f"（{rule.covered}）" if rule.covered else ""
    print(f"[{status}] {rule.code} {rule.title}{suffix}")
    for problem in rule.problems[:MAX_DETAILS]:
        print(problem.render())
    if len(rule.problems) > MAX_DETAILS:
        print(f"    …另有 {len(rule.problems) - MAX_DETAILS} 项未列出")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。why：脚本要能 CI 裸跑，也要能在本地指向任意仓库根做预检。"""
    default_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="文档结构门禁：相对链接 / 围栏语言 / H2 编号")
    parser.add_argument(
        "--root",
        type=Path,
        default=default_root,
        help="文档仓库根，默认脚本父目录的上一级（backend/）",
    )
    return parser.parse_args(argv)


def run(root: Path, docs: list[Path]) -> list[RuleResult]:
    """依次执行三项检查并返回结果。why：main 只负责编排与汇总。"""
    return [
        check_links(root, docs),
        check_fences(root, docs),
        check_h2_numbering(root, docs),
    ]


def main(argv: list[str] | None = None) -> int:
    """门禁入口：打印报告与汇总，返回退出码（0 通过 / 1 有失败 / 2 路径错误）。"""
    args = parse_args(argv)
    root = args.root.expanduser().resolve()
    if not (root / "docs").is_dir():
        print(f"错误：--root 下没有 docs/ 目录，疑似路径不对：{root}", file=sys.stderr)
        return 2

    docs = source_docs(root)
    print("文档结构门禁：相对链接 / 围栏语言 / H2 编号")
    print(f"  源根：{root}（{len(docs)} 篇：README.md + docs/*.md）")
    print()

    rules = run(root, docs)
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
