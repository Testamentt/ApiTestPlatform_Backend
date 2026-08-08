# junit_parser 单元测试：累加 testsuite / 逐用例 / 损坏兜底。
from __future__ import annotations

from pathlib import Path

import pytest
from app.core.exceptions import AppError
from app.utils.junit_parser import parse_junit_xml

MULTI_SUITE = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="a" tests="2" failures="1" errors="0" skipped="0" time="0.1">
    <testcase name="test_1" time="0.01"/>
    <testcase name="test_2" time="0.02"><failure message="boom"/></testcase>
  </testsuite>
  <testsuite name="b" tests="1" failures="0" errors="0" skipped="1" time="0.2">
    <testcase name="test_3" time="0.03"><skipped/></testcase>
  </testsuite>
</testsuites>"""


def test_parse_sums_all_testsuites(tmp_path):
    path = tmp_path / "report.xml"
    path.write_text(MULTI_SUITE, encoding="utf-8")
    summary, entries = parse_junit_xml(path)
    assert summary.total == 3
    assert summary.failed == 1
    assert summary.skipped == 1
    assert summary.passed == 1  # 3 - 1 failed - 0 errors - 1 skipped
    assert len(entries) == 3
    statuses = {e["case_id"]: e["status"] for e in entries}
    assert statuses[1] == "pass"
    assert statuses[2] == "fail"
    assert statuses[3] == "skipped"


def test_skipped_not_counted_as_passed(tmp_path):
    # pytest 的 junit tests 属性含 skipped：tests=10/failures=1/skipped=3 → passed 应为 6 而非 9
    path = tmp_path / "report.xml"
    path.write_text(
        '<testsuites><testsuite name="pytest" tests="10" failures="1" '
        'errors="0" skipped="3" time="1.0"/></testsuites>',
        encoding="utf-8",
    )
    summary, _ = parse_junit_xml(path)
    assert summary.passed == 6
    assert summary.passed + summary.failed + summary.skipped == summary.total


def test_parse_missing_raises():
    with pytest.raises(AppError) as exc:
        parse_junit_xml(Path("no_such_report.xml"))
    assert exc.value.code == "JUNIT_PARSE_FAILED"
