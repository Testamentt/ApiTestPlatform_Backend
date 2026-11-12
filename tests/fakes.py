# 本项目编码格式：UTF-8（无 BOM）。
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
    """Fake LLM：内部调真实 schema.model_validate（不跳过校验链路）；
    失败模式抛 AppError 带 raw_response；LlmUsage 固定 100/50/150。

    why 默认按 prompt 里的 operation 描述生成匹配用例：语义校验会核对
    「用例的 method/path 是否属于该接口」，固定返回一条 GET /users 会让除 listUsers 外的
    所有 operation 全被拒，成功路径测试失去意义。这里从 user prompt 反解契约
    （prompt 由 generation_service 以 JSON 渲染，格式稳定），仅用于测试替身。
    """

    def __init__(self, *, data=None, raise_error=None):
        self.data = data
        self.raise_error = raise_error
        self.settings = SimpleNamespace(model="fake-model", cost_per_1k_tokens=0.001)
        self.calls: list[dict] = []

    @staticmethod
    def _operation_from_prompt(user: str) -> dict | None:
        """从渲染后的 user prompt 里取出 operation 的 method/path/响应码。

        prompt 结构固定为「定界符 + JSON + 模板文本」，取第一段可解析 JSON 即可；
        解析失败返回 None，由调用方退回默认用例（不因 fake 解析失败而中断测试）。
        """
        start = user.find("{")
        while start != -1:
            try:
                candidate, _ = json.JSONDecoder().raw_decode(user[start:])
            except ValueError:
                start = user.find("{", start + 1)
                continue
            if isinstance(candidate, dict) and "method" in candidate and "path" in candidate:
                return candidate
            start = user.find("{", start + 1)
        return None

    def _cases_for(self, user: str) -> dict:
        op = self._operation_from_prompt(user)
        if op is None:
            method, path, status = "GET", "/users", 200
        else:
            method = str(op["method"])
            path = str(op["path"])
            codes = sorted(op.get("responses") or {})
            status = int(codes[0]) if codes else 200
        # path 模板里的 {id} 必须实例化，否则语义校验的 path 归一化会判不匹配
        concrete = path.replace("{id}", "1").replace("{pluginId}", "1")
        for token in [t for t in concrete.split("/") if t.startswith("{")]:
            concrete = concrete.replace(token, "1")
        return {
            "cases": [
                {
                    "name": "正向",
                    "method": method,
                    "path": concrete,
                    "expected_status": status,
                    "assertions": [
                        {"path": "status_code", "op": "eq", "value": status},
                    ],
                }
            ]
        }

    def chat_json(self, system, user, *, schema, deadline=None):
        # why：deadline 是新增的可选参数——fake 必须同签名，否则真实调用点传参时直接 TypeError
        self.calls.append({"system": system, "user": user, "deadline": deadline})
        if self.raise_error is not None:
            raise self.raise_error
        payload = self._cases_for(user) if self.data is None else self.data
        try:
            parsed = schema.model_validate(payload)  # 真实 Pydantic 校验链路
        except ValidationError as e:
            exc = AppError(
                "LLM_VALIDATION_FAILED", status_code=502, detail=f"Pydantic 校验失败: {e}"
            )
            exc.raw_response = json.dumps(payload, ensure_ascii=False)
            raise exc from e
        return parsed, LlmUsage(100, 50, 150)
