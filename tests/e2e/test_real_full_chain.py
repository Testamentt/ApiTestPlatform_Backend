# 真实全链路 e2e：不替换 LLM 客户端、不伪造 run_cmd、不 mock HTTP 客户端。
#
# 与 test_generate_flow.py（FakeLlmClient + fake run_cmd）相反，本文件外部依赖全部真实：
#   ① DeepSeek API：真调用，真 usage/cost 落 generation_logs
#   ② pytest 子进程：真渲染 test_*.py、真执行、真产出 report.xml 与 HTML 报告
#   ③ HTTP 被测服务：真 socket 请求（默认起本地 scripts/mock_target.py；联调真系统用 REAL_E2E_BASE_URL）
#
# 运行方式（默认门禁排除 slow，必须显式触发）：
#   pytest -m slow tests/e2e/test_real_full_chain.py -s
# 前提：
#   - backend/.env 配真实 DEEPSEEK_API_KEY（缺则仅 LLM 用例 skip，纯执行链路用例照常跑）
#   - 进程级用例额外要求 REAL_E2E_PROCESS=1 + 本机 Redis（真 uvicorn + 真 Celery worker）
#
# why 全部标 slow：默认门禁必须离线可复现、不烧 API 额度；真实外部依赖只能显式触发。
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

pytestmark = [pytest.mark.slow, pytest.mark.integration]

BACKEND = Path(__file__).resolve().parents[2]
STEP_TIMEOUT = 180  # 普通步骤等待上限（秒）
LLM_TIMEOUT = 300  # 真 LLM 生成等待上限（秒）

_DOC = {
    "openapi": "3.0.3",
    "info": {"title": "real-e2e", "version": "1"},
    "paths": {
        "/get": {
            "get": {
                "operationId": "realE2EGet",
                "responses": {"200": {"description": "ok"}},
            }
        }
    },
}


def _real_llm_key() -> str:
    """判断是否配了真实 key（provider 无关：CommandCode 的 key 是 user_ 前缀，官方 DeepSeek 是 sk-）。

    why 显式 load_dotenv：单跑本文件时 app 可能还没导入过；why 不做前缀硬校验：
    key 形态由 provider 决定，用前缀 gate 会把合法的第三方 key 误判成"没配"。
    """
    from dotenv import load_dotenv

    load_dotenv(BACKEND / ".env", override=False)
    for name in ("COMMANDCODE_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        key = os.getenv(name, "").strip()
        if key and "your-" not in key:
            return key
    return ""


needs_llm = pytest.mark.skipif(
    not _real_llm_key(), reason="需要真实 DEEPSEEK_API_KEY（本文件禁止 mock LLM）"
)
needs_process_mode = pytest.mark.skipif(
    os.getenv("REAL_E2E_PROCESS") != "1",
    reason="进程级全链路需 REAL_E2E_PROCESS=1（会拉起 uvicorn + Celery worker）",
)


# ---------------------------------------------------------------- 基础设施
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait(
    fn: Callable[[], object], *, timeout: int = STEP_TIMEOUT, interval: float = 1.0
) -> object:
    """轮询直到 fn 返回真值。why：真实依赖是异步的，固定 sleep 既慢又不稳。"""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except Exception as e:  # noqa: BLE001  # 轮询期任何异常都视为「还没就绪」
            last_error = e
        time.sleep(interval)
    raise AssertionError(f"等待超时（{timeout}s）；最后错误: {last_error!r}")


def _terminate(proc: subprocess.Popen) -> None:
    """按进程树结束子进程。why：Windows 下 uvicorn/celery 会派生子进程，只杀父会留孤儿。"""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def _http_ok(url: str, *, timeout: float = 2.0) -> bool:
    try:
        return httpx.get(url, timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def _gen_done(c: httpx.Client, task_id: int) -> dict | None:
    data = c.get(f"/api/v1/generate/{task_id}").json()["data"]
    return data if data["status"] in ("success", "failed") else None


def _task_done(c: httpx.Client, task_id: int) -> dict | None:
    data = c.get(f"/api/v1/tasks/{task_id}").json()["data"]
    return data if data["status"] in ("success", "failed") else None


def _new_http_case(c: httpx.Client, path: str = "/get") -> int:
    """建一条确定性用例并激活。why：LLM 用例的断言可能对不上目标服务，执行链路需要确定性锚点。"""
    r = c.post(
        "/api/v1/cases",
        json={
            "name": "真实链路 /get 契约",
            "method": "GET",
            "path": path,
            "operation_id": "realE2EGet",
            "expected_status": 200,
            "assertions": [{"path": "status_code", "op": "eq", "value": 200}],
        },
    )
    assert r.status_code == 201, r.text
    case_id = int(r.json()["data"]["id"])
    assert c.post(f"/api/v1/cases/{case_id}/confirm", json={"reviewer": "e2e"}).status_code == 200
    return case_id


def _assert_real_report(client, task_id: int, *, min_passed: int = 1) -> None:
    """校验报告是真实执行产物：workspace 有生成的 test 文件 + junit + 可访问的 HTML。"""
    from app.core.config import get_settings

    task = client.get(f"/api/v1/tasks/{task_id}").json()["data"]
    assert task["status"] == "success", task
    summary = task["result_summary"]
    assert summary["passed"] >= min_passed, summary
    assert summary["total"] >= min_passed, summary

    workspace = Path(get_settings().execution.workspace_dir) / "tasks" / str(task_id)
    assert list(workspace.glob("test_*.py")), f"未生成真实测试文件: {workspace}"
    assert (workspace / "report.xml").is_file(), f"未产出真实 junit: {workspace}"

    report = client.get(f"/api/v1/tasks/{task_id}/report")
    assert report.status_code == 200
    assert "执行报告" in report.text and f"通过: {summary['passed']}" in report.text


# ---------------------------------------------------------------- 真实被测服务夹具
@pytest.fixture()
def real_target(monkeypatch) -> Iterator[str]:
    """真实 HTTP 被测服务。why：默认起本地 mock_target（真 socket + 真 HTTP 语义）；
    联调真系统时用 REAL_E2E_BASE_URL 直接指向它——测试代码不区分两者，不做任何 HTTP 客户端替身。"""
    external = os.getenv("REAL_E2E_BASE_URL", "").strip()
    if external:
        yield external.rstrip("/")
        return

    from app.core.config import get_settings

    port = _free_port()
    env = {**os.environ, "MOCK_TARGET_PORT": str(port)}
    proc = subprocess.Popen(  # noqa: S603  # 固定解释器 + 仓库内脚本
        [sys.executable, "scripts/mock_target.py"],
        cwd=BACKEND,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait(lambda: _http_ok(f"{base_url}/health"), timeout=60)
        # why：本机 settings.yaml 打开了 ERP 鉴权适配，e2e 必须关掉——否则生成脚本会去登 /user/login
        settings = get_settings()
        monkeypatch.setattr(settings.execution, "base_url", base_url)
        monkeypatch.setattr(settings.execution, "auth_enabled", False)
        yield base_url
    finally:
        _terminate(proc)


# ---------------------------------------------------------------- ① 真实执行链路（不需要 LLM key）
def test_real_execution_to_report_without_llm(client, patch_sessionlocal, real_target):
    """真 pytest 子进程 + 真 HTTP 请求 + 真 HTML 报告（不含 LLM，可离线复现）。"""
    case_id = _new_http_case(client)
    r = client.post("/api/v1/tasks", json={"case_ids": [case_id]})
    assert r.status_code == 202, r.text
    _assert_real_report(client, int(r.json()["data"]["id"]))


# ---------------------------------------------------------------- ② 真实 AI 全链路（同一进程，需真 key）
@needs_llm
def test_real_ai_generate_then_execute(
    client, session_factory, patch_sessionlocal, real_target, monkeypatch
):
    """真 DeepSeek 调用 → draft 审核 → 真 pytest 执行 → 真报告 + 真 usage/cost 审计。"""
    from app.core.config import get_settings
    from app.models.generation_log import GenerationLog

    # eager 任务在进程内执行，仍需指向内存库（存储隔离，不是 mock 外部客户端）
    monkeypatch.setattr("app.tasks.generate_cases.SessionLocal", session_factory)

    r = client.post("/api/v1/generate", json={"document": _DOC})
    assert r.status_code == 202, r.text
    gen = _wait(lambda: _gen_done(client, int(r.json()["data"]["id"])), timeout=LLM_TIMEOUT)

    # 审计三连：真模型名 / 真 token / 真成本——任一为 0 就说明调的不是真 API
    with session_factory() as s:
        logs = s.query(GenerationLog).all()
    diagnostics = {
        "status": gen.get("status"),
        "error": gen.get("error_msg"),
        "summary": gen.get("result_summary"),
        "logs": [
            {
                "model": log.model,
                "status": log.status,
                "usage": log.usage,
                "cost": log.cost_estimate,
                "error": (log.error_msg or "")[:300],
                "raw": (log.raw_response or "")[:300],
            }
            for log in logs
        ],
    }
    print("GENERATION DIAGNOSTICS:", diagnostics)  # 真链路失败时唯一可排障的现场，用 -s 可见
    assert gen["status"] == "success", diagnostics
    assert gen["result_summary"]["generated"] >= 1, diagnostics
    assert logs, "未落逐 operation 审计日志"
    assert all(log.status == "success" for log in logs), diagnostics
    assert all(log.model == get_settings().llm.model for log in logs)
    assert sum((log.usage or {}).get("total_tokens", 0) for log in logs) > 0, (
        "真调用应有 token usage"
    )
    assert any((log.cost_estimate or 0) > 0 for log in logs), "真调用应有成本估算"

    drafts = client.get("/api/v1/cases?status=draft").json()["data"]["items"]
    ai_case = next(c for c in drafts if c["source"] == "ai")
    assert (
        client.post(f"/api/v1/cases/{ai_case['id']}/confirm", json={"reviewer": "e2e"}).status_code
        == 200
    )

    det_id = _new_http_case(client)
    r = client.post("/api/v1/tasks", json={"case_ids": [ai_case["id"], det_id]})
    assert r.status_code == 202, r.text
    task_id = int(r.json()["data"]["id"])
    # AI 用例断言可能对不上目标服务（报告里如实体现），确定性用例必须通过
    _assert_real_report(client, task_id, min_passed=1)


# ---------------------------------------------------------------- ③ 进程级全链路（真 uvicorn + 真 worker）
@needs_llm
@needs_process_mode
def test_real_full_chain_process_level(tmp_path):
    """真 uvicorn + 真 Celery worker（真 Redis broker）+ 真 pytest + 真 DeepSeek，全程走网络。"""
    from app.core.config import get_settings

    settings = get_settings()
    token = settings.security.api_token
    queue = f"e2e-{_free_port()}"
    api_port, target_port = _free_port(), _free_port()
    base_url = f"http://127.0.0.1:{target_port}"
    api_url = f"http://127.0.0.1:{api_port}"
    db_file = tmp_path / "e2e.db"
    workspace = tmp_path / "ws"
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    env = {
        **os.environ,
        "TESTPLATFORM_DATABASE_URL": f"sqlite:///{db_file.as_posix()}",
        "TESTPLATFORM_EXECUTION_WORKSPACE_DIR": str(workspace),
        "TESTPLATFORM_EXECUTION_BASE_URL": base_url,
        "TESTPLATFORM_EXECUTION_AUTH_ENABLED": "false",
        "TESTPLATFORM_SECURITY_API_TOKEN": token,
        "TESTPLATFORM_CELERY_QUEUE": queue,
        "TESTPLATFORM_APP_DOCS_ENABLED": "false",
        "MOCK_TARGET_PORT": str(target_port),
    }
    procs: list[subprocess.Popen] = []

    def spawn(args: list[str], name: str) -> None:
        handle = (logs_dir / f"{name}.log").open("w", encoding="utf-8")
        procs.append(
            subprocess.Popen(  # noqa: S603  # 固定解释器 + 仓库内模块
                args, cwd=BACKEND, env=env, stdout=handle, stderr=subprocess.STDOUT
            )
        )

    try:
        spawn([sys.executable, "scripts/mock_target.py"], "target")
        _wait(lambda: _http_ok(f"{base_url}/health"), timeout=60)

        # 先起 API（建表 + 收请求），再起 worker：worker 启动扫描会依赖表已存在
        spawn(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
                "--log-level",
                "warning",
            ],
            "api",
        )
        headers = {"Authorization": f"Bearer {token}"}
        _wait(lambda: _http_ok(f"{api_url}/api/v1/health"), timeout=90)

        spawn(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "app.celery_app:celery_app",
                "worker",
                "--pool=solo",
                "--concurrency=1",
                "-Q",
                queue,
                "--loglevel=WARNING",
            ],
            "worker",
        )

        with httpx.Client(base_url=api_url, headers=headers, timeout=15.0) as c:
            # worker 冷启动几秒，生成任务先投递、由轮询兜住就绪等待
            r = c.post("/api/v1/generate", json={"document": _DOC})
            assert r.status_code == 202, r.text
            gen = _wait(lambda: _gen_done(c, int(r.json()["data"]["id"])), timeout=LLM_TIMEOUT)
            assert gen["status"] == "success", {
                "status": gen["status"],
                "error": gen.get("error_msg"),
                "logs": str(logs_dir),
            }

            drafts = c.get("/api/v1/cases", params={"status": "draft"}).json()["data"]["items"]
            ai_cases = [x for x in drafts if x["source"] == "ai"]
            assert ai_cases, "真 LLM 未产出 draft 用例"
            det_id = _new_http_case(c)
            case_ids = [ai_cases[0]["id"], det_id]
            assert (
                c.post(f"/api/v1/cases/{case_ids[0]}/confirm", json={"reviewer": "e2e"}).status_code
                == 200
            )

            r = c.post("/api/v1/tasks", json={"case_ids": case_ids})
            assert r.status_code == 202, r.text
            task_id = int(r.json()["data"]["id"])
            task = _wait(lambda: _task_done(c, task_id), timeout=STEP_TIMEOUT)
            assert task["status"] == "success", {
                k: task.get(k) for k in ("status", "error_stage", "error_msg")
            }
            assert task["result_summary"]["passed"] >= 1, task["result_summary"]

            report = c.get(f"/api/v1/tasks/{task_id}/report")
            assert report.status_code == 200 and "执行报告" in report.text
            # 跨进程证据：worker 真写了 workspace 与 junit
            assert (workspace / "tasks" / str(task_id) / "report.xml").is_file()
    finally:
        for proc in reversed(procs):
            _terminate(proc)
