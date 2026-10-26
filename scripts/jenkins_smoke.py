# Jenkins 集成冒烟：按配置触发一次构建、等待结束、回读 JUnit 并打印汇总。
# 用法（在 backend/ 目录）：
#   python scripts/jenkins_smoke.py
# 依赖环境变量（见 .env.example 的 Jenkins 段）：TESTPLATFORM_JENKINS_* 与 JENKINS_API_TOKEN。
# why：集成链路（触发→排队→轮询→取制品→解析）最容易在真实环境下失败，这个脚本可脱离
# Celery/Redis 单独验证 Jenkins 侧配置是否正确。
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接以 `python scripts/jenkins_smoke.py` 运行（把仓库根加进 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.exceptions import AppError  # noqa: E402
from app.utils.jenkins_client import JenkinsClient  # noqa: E402
from app.utils.junit_parser import parse_junit_xml  # noqa: E402


def main() -> int:
    settings = get_settings()
    client = JenkinsClient(settings)
    if not client.is_configured():
        print("[FAIL] Jenkins 未配置：检查 TESTPLATFORM_JENKINS_* 与 JENKINS_API_TOKEN")
        return 2

    cfg = settings.jenkins
    print(f"[1/4] 触发 {cfg.base_url} job={cfg.job_name}")
    try:
        build = client.build_and_wait(
            task_id=0,
            case_ids=[],
            base_url=settings.execution.base_url,
            timeout_seconds=settings.execution.pytest_timeout,
        )
        print(
            f"[2/4] 构建 #{build.number} result={build.result} ({build.duration_ms} ms) {build.url}"
        )
        raw = client.download_artifact(build.number, cfg.artifact_path)
    except AppError as e:
        print(f"[FAIL] {e.code}: {e.detail}")
        return 1

    workspace = Path(settings.execution.workspace_dir).resolve() / "jenkins-smoke"
    workspace.mkdir(parents=True, exist_ok=True)
    junit_path = workspace / "junit.xml"
    junit_path.write_bytes(raw)
    print(f"[3/4] 已下载制品 {len(raw)} 字节 -> {junit_path}")

    summary, entries = parse_junit_xml(junit_path)
    print(
        f"[4/4] 解析成功 total={summary.total} passed={summary.passed} "
        f"failed={summary.failed} skipped={summary.skipped} duration_ms={summary.duration_ms} "
        f"entries={len(entries)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
