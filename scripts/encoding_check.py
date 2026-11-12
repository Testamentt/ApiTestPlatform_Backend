# 本项目编码格式：UTF-8（无 BOM）。
# 编码格式门禁：把「本项目统一 UTF-8」固化成可机械判定的检查，由 CI 在 pytest 之前调用。
# 本项目编码格式：UTF-8（无 BOM）。
#
# why：中文乱码几乎都来自三处可机械判定的漂移——文件不是 UTF-8（GBK/cp936 存盘）、
# 带了 BOM（工具按带 BOM 解析，或拼接后 BOM 落到文件中段）、以及新文件忘了写文件头声明。
# 乱码一旦落库/落报告/落子进程输出，排查成本远高于在提交时拦下；而且这类漂移靠人工扫必然漏，
# 脚本化后才能「改完当场看到红灯」，不必等演示现场翻车。
#
# 检查项：
#   E1 UTF-8 可解码（非 UTF-8 一律失败，报首个非法字节偏移）
#   E2 无 BOM（文件首字节禁止 EF BB BF）
#   E3 首行即编码声明（`本项目编码格式：UTF-8`）
#   E4 无乱码残迹（U+FFFD 替换字符、UTF-8 被按 cp936 解读的典型字节串）
#
# 设计约束：纯标准库（门禁自身不引入依赖）；Windows/Linux 同源（只用 pathlib）。
# 只读字节、只判首行，绝不改写文件——门禁不该顺手改动工作区。
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 编码声明行：三种扩展名共用同一句中文，便于 grep 与人工核对。
# 语法标记（`#` / `//` / `rem`）由各文件族的注释符决定，判定时只认「声明正文」这一段。
MARKER_TEXT = "本项目编码格式：UTF-8"
MARKER_RE = re.compile(re.escape(MARKER_TEXT))

# 首行允许的前置前缀：编码声明必须落在首行，允许前面只有注释符与空白。
# why 允许 `<!-- ... -->`：HTML/Markdown 的注释符包住同一句声明，正文仍然一致。
MARKER_PREFIXES = (
    "#",
    "//",
    "rem ",
    "REM ",
    "<!--",
    "/*",
    "*",
    '"',
    "'",
)

# 乱码残迹：U+FFFD 是「解码失败被替换」，其余是 UTF-8 字节被按 cp936/cp1252 解读的典型产物。
# why 不用更宽的正则：单个高位拉丁字母在正常中文/西文文本里不会成串出现，但宽泛匹配会误伤
# 数据文件里的合法拉丁文（如示例 OpenAPI 的西语描述），故只收窄到可确认的成串残迹。
# why 全用 \\u 转义书写、注释只作形态描述：本文件的每一行也要过 E4，
# 直接写出残迹字面量会让门禁扫到自己（说明文档必须举例时用行内豁免标记，见 IGNORE_MARKER）。
MOJIBAKE_RE = re.compile(
    "\u951f\u65a4\u62f7"  # 三个汉字：GBK 与 UTF-8 互转两轮的经典产物
    "|\u00ef\u00bf\u00bd"  # 拉丁字母 + 倒数问号：U+FFFD 被按 latin-1 再存一次
    "|\ufffd"  # U+FFFD：解码失败后的替换字符
    "|\u00e2\u20ac"  # 带抑扬符的 a + 欧元符号：UTF-8 三字节序列被按 latin-1 拆读
    "|\u00c3[\u00a4\u00a5\u00a9\u00a8\u00ad\u00b6]"  # 带波浪符的 A 接高位字节：中文被按 latin-1 拆读
    "|\u00e6\u2013\u2021"  # 拉丁字母连字符号串：GBK 被误当 UTF-8 后再拆读
)

# 逐文件族判定「首行长什么样才算合规」。why 分族而不是统一前缀：不同语言的注释符不同，
# 统一前缀会让 HTML/JSON 被迫写非法注释。
SUFFIX_MARKER_PREFIX: dict[str, tuple[str, ...]] = {
    ".py": ("#",),
    ".ts": ("//", "/*", "*"),
    ".js": ("//", "/*", "*"),
    ".mjs": ("//", "/*", "*"),
    ".vue": ("<!--", "//", "/*"),
    ".scss": ("//", "/*", "*"),
    ".css": ("/*", "*"),
    ".yml": ("#",),
    ".yaml": ("#",),
    ".toml": ("#",),
    ".conf": ("#",),
    ".bat": ("@rem ", "@REM ", "rem ", "REM "),
    ".cmd": ("@rem ", "@REM ", "rem ", "REM "),
    ".sh": ("#",),
    ".ps1": ("#",),
    ".md": ("<!--",),
    ".html": ("<!--",),
    ".txt": ("#",),
    ".env": ("#",),
    ".gitignore": ("#",),
    ".dockerignore": ("#",),
    ".editorconfig": ("#",),
    ".gitattributes": ("#",),
}

# 无后缀文件的判定：按文件名精确映射（这是唯一能区分 Dockerfile 与数据文件的办法）。
NAME_MARKER_PREFIX: dict[str, tuple[str, ...]] = {
    "Dockerfile": ("#",),
    "Jenkinsfile": ("/*", "*"),
    ".env": ("#",),
    ".env.example": ("#",),
    ".gitignore": ("#",),
    ".dockerignore": ("#",),
    ".editorconfig": ("#",),
    ".gitattributes": ("#",),
    ".gitleaks.toml": ("#",),
}

# 不参与门禁的目录：依赖、构建产物、缓存、运行时数据、虚拟环境。
# why 逐条列出而不是只排除 node_modules：本仓历史上把 venv/、.venv/、.workspace/ 都放在仓内，
# 扫进去只会把第三方文件的编码问题算到本项目头上。
SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".idea",
        ".vscode",
        "data",
        ".workspace",
        ".tmp_diff",
        "htmlcov",
        "reports",
        "allure-results",
        "allure-report",
        ".venv.bak",
        "site-packages",
        "testplatform_backend.egg-info",
    }
)

# 不参与门禁的文件：二进制与运行时数据、第三方样例数据。
SKIP_SUFFIXES = frozenset(
    {
        ".db",
        ".db-shm",
        ".db-wal",
        ".sqlite",
        ".sqlite3",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".webp",
        ".svg",
        ".pdf",
        ".lock",
        ".log",
        ".tsbuildinfo",
        ".pyc",
        ".pyo",
        ".pyd",
        ".exe",
        ".dll",
        ".zip",
        ".gz",
        ".whl",
    }
)

# 不参与门禁的精确文件名：锁文件、工具生成的二进制清单与覆盖率数据库（内容不由人写）。
# why .coverage 单列：它是没有后缀的 SQLite 库，只有按名排除才不会每个环境都报一次假失败。
SKIP_NAMES = frozenset(
    {
        "pnpm-lock.yaml",
        "package-lock.json",
        "yarn.lock",
        "PKG-INFO",
        "SOURCES.txt",
        ".coverage",
        "coverage.xml",
        "junit.xml",
    }
)

MAX_DETAILS = 60

# 行内豁免标记：所在行跳过 E4 的乱码判定（其余 E1/E2/E3 仍照常检查）。
#
# why 需要它：说明「乱码长什么样」的文档与规则，必须把残迹字面量写出来才有意义——
# 纯正则分不清「在举例」与「真乱码」，于是门禁会扫到自己的说明文档。这不是文档写错了，
# 是门禁缺少「这一行是引用而非事故」的表达手段。与 doc_hygiene.py 的 `doc-hygiene: ignore`
# 同一套思路：确定性豁免必须显式、可见、可 grep 审计，而不是藏在脚本里的文件白名单。
#
# why 只豁免 E4 且只豁免单行：
#   - E1/E2/E3 是结构性约束（解码、BOM、首行声明），与行内容无关，不该被文本豁免；
#   - 精确到行才不会让整篇文档失去保护——真有乱码混进同一文件的其他行，照样报红。
IGNORE_MARKER = "encoding-check: ignore"


@dataclass(frozen=True)
class Problem:
    """一条失败明细。why 必须能定位到「文件:行」，否则拿到报告也修不动。"""

    where: str
    line: int
    code: str
    message: str

    def render(self) -> str:
        """渲染成 `文件:行 [代号] 说明`；行号为 0（整篇级问题）时不打印行号。"""
        loc = f"{self.where}:{self.line}" if self.line else self.where
        return f"    {loc} [{self.code}] {self.message}"


@dataclass
class RuleResult:
    """一项检查的结果。why 按代号聚合，才看得出是哪条约束破了。"""

    code: str
    title: str
    covered: str = ""
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """零失败即通过。"""
        return not self.problems


def rel_to(path: Path, base: Path) -> str:
    """相对 base 的 posix 路径；不在 base 下则退回绝对路径。"""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def iter_files(root: Path) -> list[Path]:
    """递归收集受检文件，跳过依赖目录、二进制与锁文件。

    why 先剪枝再遍历：node_modules 一棵树就有上万文件，走进去再过滤会让门禁从毫秒级变秒级。
    """
    out: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name in SKIP_DIRS:
                    continue
                stack.append(entry)
                continue
            if entry.suffix in SKIP_SUFFIXES or entry.name in SKIP_NAMES:
                continue
            out.append(entry)
    return sorted(out)


def expected_prefixes(path: Path) -> tuple[str, ...] | None:
    """该文件应使用的合法注释前缀；None 表示此文件族不参与文件头检查。"""
    if path.name in NAME_MARKER_PREFIX:
        return NAME_MARKER_PREFIX[path.name]
    return SUFFIX_MARKER_PREFIX.get(path.suffix)


def decode_utf8(data: bytes) -> tuple[str | None, int]:
    """严格按 UTF-8 解码，返回 (文本, 首个非法字节偏移)。

    why 不用 errors="replace"：替换字符会让「非 UTF-8」静默变成「可解码但全是问号」，
    门禁就失去意义；这里要拿到偏移量，报告才能指向真实出错位置。
    """
    try:
        return data.decode("utf-8"), -1
    except UnicodeDecodeError as exc:
        return None, exc.start


def check_decode(path: Path, data: bytes, where: str) -> tuple[RuleResult, str | None]:
    """E1：严格 UTF-8 解码。返回该文件的检查结果与解码文本（失败为 None）。"""
    text, offset = decode_utf8(data)
    result = RuleResult("E1", "文件为合法 UTF-8")
    if text is None:
        line = data[:offset].count(b"\n") + 1 if offset >= 0 else 0
        result.problems.append(
            Problem(where, line, "E1", f"非 UTF-8 编码（首个非法字节偏移 {offset}）")
        )
    return result, text


def check_bom(data: bytes, where: str) -> RuleResult:
    """E2：禁止 BOM。why BOM 会让首行声明在部分工具里变成不可见字符，也让 shell 执行首行失败。"""
    result = RuleResult("E2", "文件无 BOM")
    if data.startswith(b"\xef\xbb\xbf"):
        result.problems.append(Problem(where, 1, "E2", "文件以 UTF-8 BOM 开头，应保存为无 BOM"))
    return result


def marker_allowed(line: str, prefixes: tuple[str, ...]) -> bool:
    """首行是否「以合法注释符引出编码声明」。

    why 只判前缀与声明正文：注释符后允许追加 `— 说明` 之类的补充，不强制整行等值。
    """
    stripped = line.strip()
    if not stripped:
        return False
    # 必须最先出现注释符（允许前后空白），再出现声明正文。
    for prefix in prefixes:
        if not stripped.startswith(prefix):
            continue
        body = stripped[len(prefix) :].strip()
        if MARKER_RE.search(body):
            return True
    return False


def check_header(text: str, path: Path, where: str) -> RuleResult:
    """E3：首行即编码声明。why 声明在首行才能被编辑器/工具当成文件级元信息读到。"""
    result = RuleResult("E3", f"首行声明「{MARKER_TEXT}」")
    prefixes = expected_prefixes(path)
    if prefixes is None:
        return result  # 该文件族不参与（如 JSON 数据文件）
    lines = text.splitlines()
    # why 放行 shebang：可执行脚本的第一行必须是 `#!`，编码声明只能排到第二行。
    # 只对确实以 `#!` 开头的文件生效，不会变成「声明可以随便挪」的后门。
    start = 1 if lines and lines[0].startswith("#!") else 0
    first = lines[start] if len(lines) > start else ""
    if not marker_allowed(first, prefixes):
        expect = " / ".join(prefixes)
        result.problems.append(
            Problem(where, start + 1, "E3", f"首行缺少编码声明（应为 {expect} {MARKER_TEXT}）")
        )
    return result


def check_mojibake(text: str, where: str) -> RuleResult:
    """E4：乱码残迹。

    why 单列一项：文件可能「是合法 UTF-8」但内容是乱码——GBK 字节被当 UTF-8 读进来再存回去，
    此后解码永远成功，只有人眼能看出。这里抓的是出现频率最高的几种残迹。
    """
    result = RuleResult("E4", "无乱码残迹")
    for lineno, line in enumerate(text.splitlines(), 1):
        # why 先看豁免标记再看正则：标记是给人看的显式声明，比藏在脚本里的文件白名单可审计。
        if IGNORE_MARKER in line:
            continue
        match = MOJIBAKE_RE.search(line)
        if match:
            result.problems.append(
                Problem(where, lineno, "E4", f"疑似乱码残迹「{match.group(0)}」")
            )
    return result


def run(root: Path) -> list[RuleResult]:
    """执行全部检查。why main 只负责编排与汇总。"""
    results = {code: RuleResult(code, title) for code, title in TITLES.items()}
    files = iter_files(root)
    # 单文件结果先各自聚合，最后合并进按代号分组的结果里——这样报告既能按文件定位，
    # 也能按代号看出「是哪条约束普遍破了」。
    for path in files:
        where = rel_to(path, root)
        try:
            data = path.read_bytes()
        except OSError as exc:
            results["E1"].problems.append(Problem(where, 0, "E1", f"文件不可读：{exc}"))
            continue
        results["E2"].problems.extend(check_bom(data, where).problems)
        decode_result, text = check_decode(path, data, where)
        results["E1"].problems.extend(decode_result.problems)
        if text is None:
            # 解不出来就别再判首行与乱码：同一根因报三遍只会淹没真正的修复点。
            continue
        results["E3"].problems.extend(check_header(text, path, where).problems)
        # why 没有「整文件豁免」：说明规则的文档与门禁脚本自身也一视同仁，
        # 只对被显式标注 `encoding-check: ignore` 的那一行放行——豁免范围越小，
        # 真乱码被漏掉的窗口就越窄。本脚本自己的正则定义行因此也要加标记。
        results["E4"].problems.extend(check_mojibake(text, where).problems)

    results["E1"].covered = f"{len(files)} 个文件"
    for code in ("E2", "E3", "E4"):
        results[code].covered = f"{len(files)} 个文件"
    return [results[code] for code in ("E1", "E2", "E3", "E4")]


TITLES = {
    "E1": "文件为合法 UTF-8",
    "E2": "文件无 BOM",
    "E3": f"首行声明「{MARKER_TEXT}」",
    "E4": "无乱码残迹",
}


def emit(rule: RuleResult) -> None:
    """打印单项结果与失败明细。why CI 日志要能一眼看出「哪条约束破了、破在哪一行」。"""
    status = "FAIL" if rule.problems else "OK"
    suffix = f"（{rule.covered}）" if rule.covered else ""
    print(f"[{status}] {rule.code} {rule.title}{suffix}")
    for problem in rule.problems[:MAX_DETAILS]:
        print(problem.render())
    if len(rule.problems) > MAX_DETAILS:
        print(f"    …另有 {len(rule.problems) - MAX_DETAILS} 项未列出")


def force_utf8_stdio() -> None:
    """把 stdout/stderr 切到 UTF-8，并对无法编码的字符降级替换。

    why：本门禁的输出本身全是中文。Windows 控制台默认 cp936，一旦报告里出现乱码残迹
    （正是本门禁要报的内容），print 会直接抛 UnicodeEncodeError——门禁在报错现场崩掉，
    比不报还糟。这里不靠调用方设 PYTHONIOENCODING，脚本自己兜住。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # why 吞掉：极少数被重定向到不可重配的流时，宁可保持原样继续跑完检查。
            continue


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析参数。why 默认指向 backend/，也允许指向 frontend/ 或容器根做跨仓预检。"""
    default_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=f"编码格式门禁（{MARKER_TEXT}）")
    parser.add_argument(
        "--root",
        type=Path,
        default=default_root,
        help="受检根目录，默认脚本父目录的上一级（backend/）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """门禁入口：打印报告与汇总，返回退出码（0 通过 / 1 有失败 / 2 路径错误）。"""
    args = parse_args(argv)
    root = args.root.expanduser().resolve()
    force_utf8_stdio()
    if not root.is_dir():
        print(f"错误：--root 不是目录：{root}", file=sys.stderr)
        return 2

    print("编码格式门禁：UTF-8 解码 / 无 BOM / 首行声明 / 无乱码残迹")
    print(f"  受检根：{root}")
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
    print(
        f"exit code = {exit_code}" + f"（失败明细共 {total} 项）"
        if failed
        else f"exit code = {exit_code}"
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
