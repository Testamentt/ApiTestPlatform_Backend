# 本项目编码格式：UTF-8（无 BOM）。
# 编码格式门禁的守卫测试。
#
# 被测对象：`scripts/encoding_check.py`（规则见 RULES.md 的项目编码格式一节）。
#
# why 必须有这些用例：门禁脚本最常见的事故是「空转的绿色」——正则写歪了、遍历写错了，
# 所有检查都通过，但非 UTF-8 文件照样进仓库。所以这里除了「当前仓库干净」，
# 还要逐条证明：四类问题**真能被抓到**、正常中文**不被误伤**、豁免**只对声明文件生效**。
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "encoding_check.py"

# 测试里显式按 UTF-8 编码：这些用例本身就在验编码行为，不能依赖解释器默认值。
UTF8 = "utf-8"


def _load_module():
    """按文件路径加载门禁脚本。why：scripts/ 不是包，直接 import 会失败。"""
    spec = importlib.util.spec_from_file_location("encoding_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["encoding_check"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


encoding = _load_module()


def _write(path: Path, payload: bytes) -> Path:
    """按字节写文件。why 编码用例必须逐字节构造，文本模式写不出非法 UTF-8 与 BOM。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _header() -> str:
    """标准合规首行（Python 注释符）。"""
    return f"# {encoding.MARKER_TEXT}\n"


def _codes(rule_list) -> set[str]:
    """把结果列表压成「有失败项的代号」集合，供断言直取。"""
    return {rule.code for rule in rule_list if rule.problems}


# ---------------------------------------------------------------- E1 非 UTF-8


def test_e1_flags_gbk_file(tmp_path):
    """GBK 中文必须被判非 UTF-8。why：这正是「脚本是重灾区」的头号成因。"""
    _write(tmp_path / "gbk.py", "# 中文注释\n".encode("gbk"))
    assert "E1" in _codes(encoding.run(tmp_path))


def test_e1_accepts_chinese_utf8(tmp_path):
    """合法 UTF-8 中文不得误伤——误伤会逼着作者改真实内容，比漏判更糟。"""
    _write(tmp_path / "ok.py", (_header() + "# 中文注释：合法\n").encode(UTF8))
    assert "E1" not in _codes(encoding.run(tmp_path))


def test_e1_reports_byte_offset(tmp_path):
    """失败明细要能定位到首个非法字节。why：没有偏移量就只能整文件重存，无法核对改了什么。"""
    _write(tmp_path / "bad.py", b"ab\n\xff\xfe\n")
    rule = next(r for r in encoding.run(tmp_path) if r.code == "E1")
    assert rule.problems and "偏移" in rule.problems[0].message


# ---------------------------------------------------------------- E2 BOM


def test_e2_flags_bom(tmp_path):
    """带 BOM 必须失败。why：BOM 会让首行声明变成不可见字符，shell 执行首行还会直接失败。"""
    _write(tmp_path / "bom.py", b"\xef\xbb\xbf" + _header().encode(UTF8))
    assert "E2" in _codes(encoding.run(tmp_path))


def test_e2_clean_file_passes(tmp_path):
    """无 BOM 的文件不得被判 E2。"""
    _write(tmp_path / "nobom.py", _header().encode(UTF8))
    assert "E2" not in _codes(encoding.run(tmp_path))


# ---------------------------------------------------------------- E3 首行声明


def test_e3_flags_missing_header(tmp_path):
    """首行没有声明必须失败。why：没有文件头，新增文件会持续漏写，规则形同虚设。"""
    _write(tmp_path / "nohdr.py", b"x = 1\n")
    assert "E3" in _codes(encoding.run(tmp_path))


def test_e3_accepts_header_on_first_line(tmp_path):
    """首行即声明时通过。"""
    _write(tmp_path / "hdr.py", (_header() + "x = 1\n").encode(UTF8))
    assert "E3" not in _codes(encoding.run(tmp_path))


def test_e3_rejects_header_on_second_line(tmp_path):
    """声明必须落在首行：第二行不算。why 首行才是文件级元信息的位置。"""
    _write(tmp_path / "second.py", f"x = 1\n{_header()}".encode(UTF8))
    assert "E3" in _codes(encoding.run(tmp_path))


def test_e3_allows_shebang_before_header(tmp_path):
    """带 shebang 的脚本允许声明排第二行。why 第一行必须留给 `#!`，否则脚本不可执行。"""
    _write(tmp_path / "tool.py", f"#!/usr/bin/env python\n{_header()}".encode(UTF8))
    assert "E3" not in _codes(encoding.run(tmp_path))


def test_e3_respects_per_language_comment_syntax(tmp_path):
    """各文件族用自己的注释符。why：给 JSON/HTML 套 `#` 会写出非法语法。"""
    bodies = {
        "a.py": _header(),
        "b.ts": f"// {encoding.MARKER_TEXT}\n",
        "c.vue": f"<!-- {encoding.MARKER_TEXT} -->\n",
        "d.bat": f"@rem {encoding.MARKER_TEXT}\n",
        "e.md": f"<!-- {encoding.MARKER_TEXT} -->\n",
    }
    for name, header in bodies.items():
        _write(tmp_path / name, header.encode(UTF8))
    assert "E3" not in _codes(encoding.run(tmp_path))


def test_e3_wrong_comment_style_is_flagged(tmp_path):
    """注释符错了也算不合规。why：`#` 开头写进 .bat 会被 cmd 当命令执行。"""
    _write(tmp_path / "wrong.bat", f"# {encoding.MARKER_TEXT}\n".encode(UTF8))
    assert "E3" in _codes(encoding.run(tmp_path))


# ---------------------------------------------------------------- E4 乱码残迹


def test_e4_flags_replacement_char(tmp_path):
    """U+FFFD 必须被抓。why：解码失败被替换后，文件仍是「合法 UTF-8」，只有这项能拦住。"""
    _write(tmp_path / "fffd.py", (_header() + "# 拒绝访问\ufffd\ufffd\n").encode(UTF8))
    assert "E4" in _codes(encoding.run(tmp_path))


def test_e4_flags_cp936_mojibake(tmp_path):
    """经典 cp936/latin-1 拆读残迹必须被抓。"""
    _write(tmp_path / "moji.py", (_header() + "# 中文变成了 \u00e2\u20ac\u2122\n").encode(UTF8))
    assert "E4" in _codes(encoding.run(tmp_path))


def test_e4_clean_chinese_passes(tmp_path):
    """正常中文不得被判乱码。"""
    _write(tmp_path / "fine.py", (_header() + "# 中文注释：正常\n").encode(UTF8))
    assert "E4" not in _codes(encoding.run(tmp_path))


# ---------------------------------------------------------------- 遍历与豁免


def test_skips_vendored_dirs(tmp_path):
    """依赖/构建目录不参与。why：node_modules 里第三方文件的编码问题不该算到本项目头上。"""
    for name in ("node_modules", ".venv", "__pycache__", "dist"):
        _write(tmp_path / name / "bad.py", "# 中文\n".encode("gbk"))
    assert "E1" not in _codes(encoding.run(tmp_path))


def test_skips_binary_suffixes(tmp_path):
    """二进制后缀不参与。why：拿 .db/.png 去判 UTF-8 只会制造假红灯。"""
    _write(tmp_path / "x.db", b"\xff\xfe\x00\x00binary")
    assert "E1" not in _codes(encoding.run(tmp_path))


# ---------------------------------------------------------------- 行内豁免标记


def test_ignore_marker_suppresses_e4_on_its_line(tmp_path):
    """带标记的行放行 E4。why 说明规则的文档必须能写出残迹字面量作例子。"""
    _write(
        tmp_path / "doc.md",
        f"<!-- {encoding.MARKER_TEXT} -->\n"
        f"# 乱码形如 \ufffd\ufffd <!-- {encoding.IGNORE_MARKER} -->\n".encode(UTF8),
    )
    assert "E4" not in _codes(encoding.run(tmp_path))


def test_ignore_marker_only_skips_its_own_line(tmp_path):
    """标记只豁免所在行。why 精确到行，同一文件其它行有真乱码必须照样报红。"""
    _write(
        tmp_path / "doc.md",
        f"<!-- {encoding.MARKER_TEXT} -->\n"  # 第 1 行：编码声明
        f"# 举例 \ufffd <!-- {encoding.IGNORE_MARKER} -->\n"  # 第 2 行：豁免
        f"# 这一行是真乱码 \ufffd\ufffd\n".encode(UTF8),  # 第 3 行：应报
    )
    rules = {r.code: r for r in encoding.run(tmp_path)}
    lines = {p.line for p in rules["E4"].problems}
    assert lines == {3}, f"应只报第 3 行，实际 {lines}"


def test_ignore_marker_does_not_bypass_e1_e2_e3(tmp_path):
    """标记不得豁免 E1/E2/E3。why 解码、BOM、首行声明是结构性约束，与行内容无关。"""
    # E1：非 UTF-8 文件——标记连读都读不到，天然不受影响，这里验 E3（首行缺声明）。
    _write(
        tmp_path / "noheader.py",
        f"x = 1  # {encoding.IGNORE_MARKER}\n".encode(UTF8),
    )
    assert "E3" in _codes(encoding.run(tmp_path))

    # E2：带 BOM 的文件即便含标记也必须失败。
    _write(
        tmp_path / "bom.py",
        b"\xef\xbb\xbf" + f"# {encoding.MARKER_TEXT}\n# {encoding.IGNORE_MARKER}\n".encode(UTF8),
    )
    assert "E2" in _codes(encoding.run(tmp_path))


def test_ignore_marker_is_not_a_blanket_file_exemption(tmp_path):
    """标记不是文件级后门：写了标记的文件里，其余行仍受 E4 保护。"""
    body = "".join(f"# 正常行 {i}\n" for i in range(10))
    _write(
        tmp_path / "mixed.py",
        (f"# {encoding.MARKER_TEXT}\n" + body + "# \ufffd\n").encode(UTF8),
    )
    assert "E4" in _codes(encoding.run(tmp_path))


def test_real_repository_is_clean():
    """真实仓库当前必须零失败。why 这是门禁接入 CI 的前提：红灯留在仓库里等于没门禁。"""
    results = encoding.run(ROOT)
    failed = [r for r in results if r.problems]
    detail = "\n".join(p.render() for r in failed for p in r.problems[:10])
    assert not failed, f"编码门禁在真实仓库上失败：\n{detail}"


def test_gate_source_needs_no_own_exemption():
    """门禁脚本自身零豁免也必须干净。

    why：这正是此前的真实 bug——脚本与文档为了说明规则写出残迹字面量，被自己判红，
    于是加了文件级 SELF_EXEMPT 掩盖。现在改成「注释只描述形态 + 行内标记」，
    本用例锁死这一点：一旦有人再往脚本注释里贴残迹字面量、或重新引入文件级豁免，这里就红。
    """
    results = encoding.run(ROOT)
    offenders = {p.where for r in results for p in r.problems if p.code == "E4"}
    assert "scripts/encoding_check.py" not in offenders
