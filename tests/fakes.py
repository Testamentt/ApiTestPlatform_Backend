# 测试用假实现。why：隔离 subprocess / LLM（不真跑 pytest/网络/真调 API）。
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.core.exceptions import AppError
from app.utils.llm_client import LlmUsage
from app.utils.subprocess_util import CmdResult
from pydantic import ValidationError

JUNIT_OK = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="1" failures="0" errors="0" skipped="0" time="0.05">
    <testcase name="test_1" time="0.01"/>
  </testsuite>
</testsuites>"""

JUNIT_FAIL = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="1" failures="1" errors="0" skipped="0" time="0.05">
    <testcase name="test_1" time="0.01"><failure message="assert 200 != 500"/></testcase>
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


class FakeLlmClient:
    """Fake LLM：内部调真实 schema.model_validate（不跳过校验链路，间隙 3）；
    失败模式抛 AppError 带 raw_response；LlmUsage 固定 100/50/150。"""

    def __init__(self, *, data=None, raise_error=None):
        self.data = data
        self.raise_error = raise_error
        self.settings = SimpleNamespace(model="fake-model", cost_per_1k_tokens=0.001)
        self.calls: list[dict] = []

    def chat_json(self, system, user, *, schema):
        self.calls.append({"system": system, "user": user})
        if self.raise_error is not None:
            raise self.raise_error
        if self.data is None:
            self.data = {
                "cases": [
                    {"name": "正向", "method": "GET", "path": "/users", "expected_status": 200}
                ]
            }
        try:
            parsed = schema.model_validate(self.data)  # 真实 Pydantic 校验链路
        except ValidationError as e:
            exc = AppError(
                "LLM_VALIDATION_FAILED", status_code=502, detail=f"Pydantic 校验失败: {e}"
            )
            exc.raw_response = json.dumps(self.data, ensure_ascii=False)
            raise exc from e
        return parsed, LlmUsage(100, 50, 150)
