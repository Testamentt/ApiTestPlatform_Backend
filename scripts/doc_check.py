# 文档漂移自动拦截（P3-6）。why：文档与实现/镜像的错位此前靠三轮一次性人工对账
# （链接与编号 doc_lint、镜像原子块 mirror_verify、镜像文风 mirror_style_check）——只在治理当天跑过
# 一次，没有门禁就只能等下一个人肉审计再发现。本脚本把这三类机械校验固化为仓库内可复用入口，由
# .github/workflows/ci.yml 的 test-fast job 在 ruff 之后、pytest 之前调用：
#   A 源文档结构：相对链接可达 / 围栏必须标语言 / H2 编号连续（docs/README.md §3 硬性约定）
#   B 学习版镜像：版本印记 / 原子块存在性 / 废弃口径残留 / 学习文风虚词
#   C 报告：每项 [OK]/[FAIL] + 明细「文件:行 说明」+ 汇总 + 退出码（任一 FAIL → 1）
# 设计约束：纯标准库（门禁自身不引入依赖）；Windows/Linux 同源（只用 pathlib，不拼反斜杠）；
# 镜像目录缺失（CI 只检出 backend）时 B 类整体跳过并打印 skip，不误报。
from __future__ import annotations

import argparse
import os
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

# H2 编号豁免：历史/过程记录类（docs/README.md §3 第 2 条例外），按追加式分组，不强制 `## N.`。
H2_EXEMPT = frozenset({"docs/TODO.md", "docs/session-doc-template.md"})

# 镜像目录按仓库路径 1:1 布局：backend/ 对应当前仓库，frontend/ 对应兄弟仓库（CI 检不到）。
MIRROR_BACKEND_PREFIX = "backend/"
MIRROR_FRONTEND_PREFIX = "frontend/"

# 学习文档工作区：全项目共用且全盘唯一（RULES.md §19.0-3 / §19.4）。
# 源码布局为 <容器>/TestPlatform/backend，故工作区根 = 源根上溯两级 / docs-study。
# 唯一性由规则保证：禁止项目目录内副本，也禁止为单项目再克隆第二份。
WORKSPACE_DIRNAME = "docs-study"
MIRROR_PROJECT_DIRNAME = "TestPlatform"

# 版本印记允许出现在 H1 / 徽章之后，故限定「头部 N 行」内命中即可。
STAMP_HEAD_LINES = 15
# 单项失败明细上限：防止大范围漂移时刷屏淹没 CI 日志（超出部分只报计数）。
MAX_DETAILS = 60

FENCE_LINE_RE = re.compile(r"^\s{0,3}(?P<fence>```+|~~~+)(?P<info>.*)$")
LINK_RE = re.compile(r"\[[^\]]*\]\(\s*(?P<target><[^>]*>|[^()\s]+)(?:\s+[\"'][^\"']*[\"'])?\s*\)")
H2_NUMBERED_RE = re.compile(r"^##\s+(?P<num>\d+)\.\s+\S")
STAMP_RE = re.compile(r"^>\s*源版本[:：]\s*[0-9a-f]{7,40}\s*\|\s*同步日期[:：]\s*\d{4}-\d{2}-\d{2}")
# 相对链接跳过的前缀：外链、邮件、纯锚点（docs/README.md §3 第 8 条：仓库内链接才用相对路径）。
LINK_SKIP_PREFIXES = ("http://", "https://", "mailto:", "#")

# B5 原子块：源文档里「可机械比对」的事实碎片，逐个断言镜像中存在。
ENV_ATOM_RE = re.compile(r"\bTESTPLATFORM_[A-Z0-9_]*[A-Z0-9]\b")
ENDPOINT_ATOM_RE = re.compile(r"(?<![\w/])/api/v1/[A-Za-z0-9_{}\-/]*")
CONFIG_ATOM_RE = re.compile(
    r"\b(?:app|database|redis|celery|execution|swagger|llm|security|frontend)"
    r"\.[a-z0-9_]*[a-z0-9]\b"
)
# 配置段名与文件扩展名同名（`database.md` / `database.py`）：形如 `段.<扩展名>` 的是文件名，不是配置键。
FILE_SUFFIXES = frozenset(
    {
        "py",
        "md",
        "json",
        "ts",
        "tsx",
        "vue",
        "yml",
        "yaml",
        "toml",
        "cfg",
        "ini",
        "bat",
        "sh",
        "txt",
        "html",
        "sql",
        "db",
        "sqlite",
        "log",
        "env",
    }
)
# 端点尾部标点：正文里 `/api/v1/tasks`、结尾常带句读，比对前剥掉。
ENDPOINT_TRAILING_CHARS = "/-.,;:、。）】”\"'"

# B6 废弃口径：镜像里出现即失败（2026-10-08 文档治理已纠正、此后不得回流的错口径）。
DEPRECATED_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"idx_test_cases_operation_id"),
        "废弃索引名 idx_test_cases_operation_id（SQLAlchemy index=True 默认前缀，应为 ix_）",
    ),
    (
        re.compile(r"time_limit[^\n]{0,30}?必须\s*(?:>|＞|大于)\s*visibility"),
        "废弃超时口径「time_limit 必须 > visibility_timeout」（方向写反，实为 visibility_timeout 更大）",
    ),
    (re.compile(r"覆盖率\s*实测\s*91\s*%"), "废弃覆盖率「实测 91%」（实测 92%，核心模块 91%）"),
    (re.compile(r"41\s*个单测"), "废弃单测计数「41 个单测」（实测 46 个）"),
)
# 单篇专属废弃口径：key 为镜像内相对路径。
DEPRECATED_RULES_BY_FILE: dict[str, tuple[tuple[re.Pattern[str], str], ...]] = {
    "backend/docs/ai-generation.md": (
        (
            re.compile(r"202\s*\{\s*task_id\s*\}"),
            "废弃响应口径「202 {task_id}」（生成入口返回 ApiResponse[TaskRead]，标识是 data.id）",
        ),
    ),
}

# B7 学习文风：虚词出现即失败（学习版要求短句 + 动词复活，不靠虚词填充）。
STYLE_WORDS = ("众所周知", "显而易见", "总的来说", "需要注意的是", "基本上")

# B 类检查项标题集中在此：镜像缺失时按同一份标题生成 skip 结果，避免两处字面量漂移。
MIRROR_RULE_TITLES: dict[str, str] = {
    "B4": "镜像头部含版本印记（源版本 + 同步日期）",
    "B5": "源文档原子块在镜像中存在",
    "B6": "已废弃口径不残留（索引名/超时方向/计数）",
    "B7": "镜像正文无禁用虚词",
}


# ---------------------------------------------------------------- 数据结构


@dataclass(frozen=True)
class Problem:
    """一条失败明细。why：门禁报告必须能直接定位到「文件:行」，否则修不动。"""

    where: str  # 相对路径（源根或镜像根，取决于所在检查项）
    line: int  # 行号，1 起；0 表示整篇（如文件不可读）
    message: str

    def render(self) -> str:
        """渲染成 `文件:行 说明`；行号为 0（整篇级问题）时不打印行号。"""
        loc = f"{self.where}:{self.line}" if self.line else self.where
        return f"    {loc} {self.message}"


@dataclass
class RuleResult:
    """一项检查的结果。why：报告按「检查项」聚合，才看得出是哪条规范破了。"""

    code: str  # A1 / A2 / A3 / B4..B7
    title: str
    covered: str = ""  # 覆盖描述（篇数/处数/原子数），让 [OK] 也带证据
    problems: list[Problem] = field(default_factory=list)
    skip_reason: str | None = None  # 非 None 表示跳过（不算失败）

    @property
    def ok(self) -> bool:
        """是否通过：跳过或零失败都算通过。"""
        return self.skip_reason is not None or not self.problems


@dataclass(frozen=True)
class MirrorDoc:
    """一篇镜像文档及其 1:1 源文档。why：B 类各项检查都以「镜像 ↔ 源」配对为前提。"""

    path: Path
    rel: str  # 相对镜像根，posix 形式
    source: Path | None  # None = 镜像根索引等无源文档，不参与一致性断言


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

    why：编号与文风规则只看正文——代码块里的示例（如命令、旧口径样例）不算违规。
    """
    inside = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if FENCE_LINE_RE.match(line):
            inside = not inside
            continue
        if not inside:
            yield lineno, line


def first_line(text: str, index: int) -> int:
    """字符下标 → 行号。why：明细必须给行号，而正则只能给偏移。"""
    return text.count("\n", 0, index) + 1


def line_hits(text: str, pattern: re.Pattern[str]) -> Iterator[int]:
    """产出正则命中的行号。why：报告统一走「文件:行 说明」格式。"""
    for match in pattern.finditer(text):
        yield first_line(text, match.start())


# ---------------------------------------------------------------- A 源文档结构


def source_docs(root: Path) -> list[Path]:
    """受检源文档 = README.md + docs/*.md（不递归）。

    why：历史目录 sessions/plans/reviews/ 按约定「不回改」，纳入门禁只会制造不可修的红灯。
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


# ---------------------------------------------------------------- B 学习版镜像一致性


def collect_mirror_docs(mirror_root: Path, root: Path) -> list[MirrorDoc]:
    """枚举镜像文档并配对源文档。

    what：镜像 `backend/<rel>` → `<root>/<rel>`；`frontend/<rel>` → 兄弟仓库 `<root>/../frontend/<rel>`；
    镜像根其余文件（索引 README.md）无 1:1 源文档，不参与一致性断言。
    why：镜像按仓库路径 1:1 布局，显式映射才能把「源有、镜像缺」的原子块指回具体源文件与行号。
    """
    docs: list[MirrorDoc] = []
    for path in sorted(mirror_root.rglob("*.md")):
        rel = path.relative_to(mirror_root).as_posix()
        if rel.startswith(MIRROR_BACKEND_PREFIX):
            source = root / rel[len(MIRROR_BACKEND_PREFIX) :]
        elif rel.startswith(MIRROR_FRONTEND_PREFIX):
            source = root.parent / rel
        else:
            source = None
        docs.append(MirrorDoc(path=path, rel=rel, source=source))
    return docs


def check_mirror_stamp(mirror_docs: list[MirrorDoc]) -> tuple[RuleResult, list[MirrorDoc]]:
    """B4：每篇镜像头部含 `> 源版本: <hash> | 同步日期:` 印记。

    what：只检「有 1:1 源文档」的镜像（索引页不镜像任何源文档）；印记须落在头部 N 行内。
    why：印记是双轨可追溯性的唯一凭据，缺了就无法判断镜像对齐的是哪一版源文档。
    """
    result = RuleResult("B4", MIRROR_RULE_TITLES["B4"])
    in_scope: list[MirrorDoc] = []
    skipped: list[str] = []
    for doc in mirror_docs:
        if doc.source is None:
            skipped.append(doc.rel)
            continue
        in_scope.append(doc)
        text, failure = read_doc(doc.path, doc.rel)
        if failure is not None:
            result.problems.append(failure)
            continue
        assert text is not None
        head = text.splitlines()[:STAMP_HEAD_LINES]
        if not any(STAMP_RE.match(line) for line in head):
            result.problems.append(
                Problem(
                    doc.rel,
                    1,
                    f"头部 {STAMP_HEAD_LINES} 行内缺「> 源版本: <hash> | 同步日期:」印记",
                )
            )
    result.covered = f"{len(in_scope)} 篇镜像"
    if skipped:
        result.covered += f"；跳过无源文档：{', '.join(skipped)}"
    return result, in_scope


def extract_atoms(text: str) -> list[tuple[str, int]]:
    """抽取源文档原子块，返回 [(原子, 首次出现行号)]。

    what：三类事实碎片——`TESTPLATFORM_*` 环境变量、`/api/v1/...` 端点、`段.字段` 配置键。
    why：这些是「键名/路径」级事实，改写镜像时最容易被顺手改掉，必须逐字断言存在。
    """
    atoms: dict[str, int] = {}

    def add(token: str, index: int) -> None:
        if token and token not in atoms:
            atoms[token] = first_line(text, index)

    for match in ENV_ATOM_RE.finditer(text):
        add(match.group(0), match.start())
    for match in ENDPOINT_ATOM_RE.finditer(text):
        token = match.group(0).rstrip(ENDPOINT_TRAILING_CHARS)
        if len(token) > len("/api/v1/"):  # 光秃前缀不算资源
            add(token, match.start())
    for match in CONFIG_ATOM_RE.finditer(text):
        token = match.group(0)
        if token.rsplit(".", 1)[-1] in FILE_SUFFIXES:
            continue  # `database.md` / `database.py` 是文件名
        if text[match.end() : match.end() + 1] == ":":
            continue  # `app.main:app` 是模块路径
        add(token, match.start())
    return sorted(atoms.items(), key=lambda item: (item[1], item[0]))


def check_mirror_atoms(root: Path, mirror_docs: list[MirrorDoc]) -> RuleResult:
    """B5：源文档原子块逐个断言镜像中存在。

    what：仅比对「源文档在本地存在」的镜像；跨仓库（frontend/）或未检出的源计入跳过。
    why：镜像铁律是「只改表达形式，不改键名/路径/数值」，原子块是这条铁律唯一可机械验证的部分。
    """
    result = RuleResult("B5", MIRROR_RULE_TITLES["B5"])
    compared = 0
    total_atoms = 0
    skipped: list[str] = []
    for doc in mirror_docs:
        source = doc.source
        if source is None or not source.is_file():
            skipped.append(doc.rel)
            continue
        where = rel_to(source, root)
        source_text, source_failure = read_doc(source, where)
        if source_failure is not None:
            result.problems.append(source_failure)
            continue
        mirror_text, mirror_failure = read_doc(doc.path, doc.rel)
        if mirror_failure is not None:
            result.problems.append(mirror_failure)
            continue
        assert source_text is not None and mirror_text is not None
        compared += 1
        for atom, lineno in extract_atoms(source_text):
            total_atoms += 1
            if atom not in mirror_text:
                result.problems.append(
                    Problem(where, lineno, f"镜像缺原子块「{atom}」（镜像文件 {doc.rel}）")
                )
    result.covered = f"{compared} 篇配对 / {total_atoms} 个原子"
    if skipped:
        result.covered += f"；跳过源不在检出范围：{', '.join(skipped)}"
    return result


def check_mirror_deprecated(mirror_docs: list[MirrorDoc]) -> RuleResult:
    """B6：已废弃口径不得残留。why：旧口径回流是文档治理最常见的退化方式，只能靠机器拦。"""
    result = RuleResult("B6", MIRROR_RULE_TITLES["B6"])
    checked = 0
    extra = sum(len(rules) for rules in DEPRECATED_RULES_BY_FILE.values())
    for doc in mirror_docs:
        if doc.source is None:
            continue
        checked += 1
        text, failure = read_doc(doc.path, doc.rel)
        if failure is not None:
            result.problems.append(failure)
            continue
        assert text is not None
        for pattern, message in DEPRECATED_RULES + DEPRECATED_RULES_BY_FILE.get(doc.rel, ()):
            for lineno in line_hits(text, pattern):
                result.problems.append(Problem(doc.rel, lineno, message))
    result.covered = f"{checked} 篇镜像 / {len(DEPRECATED_RULES) + extra} 条废弃口径"
    return result


def check_mirror_style(mirror_docs: list[MirrorDoc]) -> RuleResult:
    """B7：镜像正文无虚词。why：虚词是「学习版」文风的退化物，出现即失败（不设容忍度）。"""
    result = RuleResult("B7", MIRROR_RULE_TITLES["B7"])
    checked = 0
    for doc in mirror_docs:
        if doc.source is None:
            continue
        checked += 1
        text, failure = read_doc(doc.path, doc.rel)
        if failure is not None:
            result.problems.append(failure)
            continue
        assert text is not None
        for lineno, line in iter_prose_lines(text):
            for word in STYLE_WORDS:
                if word in line:
                    result.problems.append(Problem(doc.rel, lineno, f"禁用虚词「{word}」"))
    result.covered = f"{checked} 篇镜像 / {len(STYLE_WORDS)} 个禁用词"
    return result


# ---------------------------------------------------------------- C 报告与入口


def emit(rule: RuleResult) -> None:
    """打印单项结果：状态行 + 失败明细。why：CI 日志要能一眼看出「哪条规范破了、破在哪一行」。"""
    if rule.skip_reason is not None:
        print(f"[skip] {rule.code} {rule.title}（{rule.skip_reason}）")
        return
    status = "FAIL" if rule.problems else "OK"
    suffix = f"（{rule.covered}）" if rule.covered else ""
    print(f"[{status}] {rule.code} {rule.title}{suffix}")
    for problem in rule.problems[:MAX_DETAILS]:
        print(problem.render())
    if len(rule.problems) > MAX_DETAILS:
        print(f"    …另有 {len(rule.problems) - MAX_DETAILS} 项未列出")


def resolve_roots(args: argparse.Namespace) -> tuple[Path, Path]:
    """解析源根与镜像根。

    what：源根取 `--root`（默认脚本父目录的上一级 = backend/）；镜像根优先级为
    `--mirror-root` > 环境变量 `DOCS_STUDY_ROOT` > `<源根>/../../docs-study/TestPlatform`
    （源码布局为 `<容器>/TestPlatform/backend`，故上溯两级即全局唯一工作区 `E:\\Project\\docs-study`）。
    why：CI 只检出 backend，镜像路径必须可外部注入；本地跨仓库调试又要能直接指定。
    why：默认值只指向全局唯一工作区，**禁止**回退到「项目目录旁的 docs-study」——多份工作副本
    会各自提交、互不可见，镜像必然分叉（RULES.md §19.0-3 / §19.4）。
    """
    root = args.root.expanduser().resolve()
    if args.mirror_root is not None:
        return root, args.mirror_root.expanduser().resolve()
    env_value = os.environ.get("DOCS_STUDY_ROOT", "").strip()
    if env_value:
        return root, Path(env_value).expanduser().resolve()
    return root, (root.parent.parent / WORKSPACE_DIRNAME / MIRROR_PROJECT_DIRNAME).resolve()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。why：同一脚本要既能 CI 裸跑，也能本地指定任意源根/镜像根。"""
    default_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="文档漂移门禁：A 源文档结构 + B 学习版镜像一致性",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=default_root,
        help="源文档仓库根，默认脚本父目录的上一级（backend/）",
    )
    parser.add_argument(
        "--mirror-root",
        type=Path,
        default=None,
        help=(
            "学习版镜像根，默认 ../../docs-study/TestPlatform（全局唯一工作区 E:\\Project\\docs-study），"
            "可用环境变量 DOCS_STUDY_ROOT 覆盖"
        ),
    )
    return parser.parse_args(argv)


def run(
    root: Path, docs: list[Path], mirror_root: Path
) -> tuple[list[RuleResult], list[RuleResult]]:
    """执行 A / B 两类检查，返回 (A 类结果, B 类结果)。why：main 只负责编排与汇总。"""
    source_rules = [
        check_links(root, docs),
        check_fences(root, docs),
        check_h2_numbering(root, docs),
    ]

    if not mirror_root.is_dir():
        # 只带短原因：完整说明（含路径与覆盖方式）由 main 打印一次，避免每项重复刷屏。
        return source_rules, [
            RuleResult(code, title, skip_reason="镜像目录不存在")
            for code, title in MIRROR_RULE_TITLES.items()
        ]

    mirror_docs = collect_mirror_docs(mirror_root, root)
    stamp_rule, in_scope = check_mirror_stamp(mirror_docs)
    mirror_rules = [
        stamp_rule,
        check_mirror_atoms(root, in_scope),
        check_mirror_deprecated(in_scope),
        check_mirror_style(in_scope),
    ]
    return source_rules, mirror_rules


def main(argv: list[str] | None = None) -> int:
    """门禁入口：打印三类报告与汇总，返回退出码（0 通过 / 1 有失败 / 2 参数或路径错误）。"""
    args = parse_args(argv)
    root, mirror_root = resolve_roots(args)
    if not (root / "docs").is_dir():
        print(f"错误：--root 下没有 docs/ 目录，疑似路径不对：{root}", file=sys.stderr)
        return 2

    docs = source_docs(root)
    print("文档漂移门禁（P3-6）：A 源文档结构 + B 学习版镜像一致性")
    print(f"  源根  ：{root}（{len(docs)} 篇：README.md + docs/*.md）")
    print(f"  镜像根：{mirror_root}（{'存在' if mirror_root.is_dir() else '不存在 → B 类跳过'}）")
    print()

    source_rules, mirror_rules = run(root, docs, mirror_root)

    print("== A 源文档结构 ==")
    for rule in source_rules:
        emit(rule)
    print()
    print("== B 学习版镜像一致性 ==")
    if not mirror_root.is_dir():
        print(
            f"  跳过原因：镜像目录不存在：{mirror_root}"
            "（CI 只检出 backend，属预期；本地用 DOCS_STUDY_ROOT 或 --mirror-root 指定镜像根，"
            f"唯一工作区见 {WORKSPACE_DIRNAME}/README.md §0）"
        )
    for rule in mirror_rules:
        emit(rule)

    print()
    print("== 汇总 ==")
    failed: list[RuleResult] = []
    for label, rules in (("A 源文档结构", source_rules), ("B 学习版镜像一致性", mirror_rules)):
        bad = [rule for rule in rules if not rule.ok]
        failed.extend(bad)
        if all(rule.skip_reason is not None for rule in rules):
            print(f"[skip] {label}（镜像未纳入本次检查）")
            continue
        detail = f"{len(rules) - len(bad)}/{len(rules)} 项通过"
        if bad:
            detail += "：" + "、".join(f"{rule.code}（{len(rule.problems)} 项）" for rule in bad)
        print(f"[{'FAIL' if bad else 'OK'}] {label}（{detail}）")

    exit_code = 1 if failed else 0
    total = sum(len(rule.problems) for rule in failed)
    print(f"exit code = {exit_code}" + (f"（失败明细共 {total} 项）" if failed else ""))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
