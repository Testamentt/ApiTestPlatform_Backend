# 测试用假实现。why：隔离 subprocess（不真跑 pytest/网络）。
from __future__ import annotations

from pathlib import Path

from app.utils.subprocess_util import CmdResult

JUNIT_OK = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="1" failures="0" errors="0" skipped="0" time="0.05">
    <testcase name="test_1" time="0.01"/>
  </testsuite>
</testsuites>"""


def make_fake_run_cmd(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    junit_xml: str | None = None,
    exception: Exception | None = None,
    pid: int = 9999,
):
    """返回一个 fake run_cmd：可写 report.xml、抛异常或返回固定 CmdResult。"""

    def _run(args, timeout, *, check=True, cwd=None, on_start=None):
        if on_start is not None:
            on_start(pid)
        if exception is not None:
            raise exception
        if junit_xml is not None:
            (Path(cwd) / "report.xml").write_text(junit_xml, encoding="utf-8")
        return CmdResult(returncode, stdout, stderr, 100, pid)

    return _run
