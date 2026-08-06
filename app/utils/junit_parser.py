# JUnit XML 解析。why：遍历全部 <testsuite> 累加（pytest-xdist 会产出多个）；
# 同时轻量提取逐用例状态（供 /results 展示）；损坏时抛 AppError 由 execution_service 降级。
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from app.core.exceptions import AppError

_CASE_ID_RE = re.compile(r"^test_(\d+)$")


@dataclass(frozen=True)
class JunitSummary:
    total: int
    passed: int
    failed: int
    skipped: int
    duration_ms: int


def parse_junit_xml(path: Path) -> tuple[JunitSummary, list[dict]]:
    """返回 (汇总, 逐用例结果列表)。why：解析失败不能拖垮任务状态——由调用方降级为 stdout 文本。"""
    try:
        root = ET.parse(path).getroot()
    except (FileNotFoundError, ET.ParseError) as e:
        raise AppError("JUNIT_PARSE_FAILED", status_code=502, detail=str(e)) from e

    total = failed = errors = skipped = 0
    duration_ms = 0
    entries: list[dict] = []
    for suite in root.iter("testsuite"):
        total += int(suite.attrib.get("tests", 0))
        failed += int(suite.attrib.get("failures", 0))
        errors += int(suite.attrib.get("errors", 0))
        skipped += int(suite.attrib.get("skipped", 0))
        duration_ms += int(float(suite.attrib.get("time", "0")) * 1000)
        for tc in suite.iter("testcase"):
            match = _CASE_ID_RE.match(tc.attrib.get("name", ""))
            if not match:
                continue
            failure = tc.find("failure")
            err = tc.find("error")
            if tc.find("skipped") is not None:
                status = "skipped"
            elif err is not None:
                status = "error"
            elif failure is not None:
                status = "fail"
            else:
                status = "pass"
            el = failure or err
            msg = ""
            if el is not None:
                msg = (el.attrib.get("message", "") or el.text or "")[:500]
            entries.append(
                {"case_id": int(match.group(1)), "status": status, "failure_msg": msg}
            )

    summary = JunitSummary(
        total=total,
        passed=total - failed - errors,
        failed=failed + errors,
        skipped=skipped,
        duration_ms=duration_ms,
    )
    return summary, entries
