# 本项目编码格式：UTF-8（无 BOM）。
# Jenkins 执行器：平台只投递任务与回读结果，pytest 真正跑在 Jenkins agent 上。
# why：企业里测试资产与执行机多由 CI 统一管理，平台不必自建执行机；平台仍是任务状态唯一事实源，
# Jenkins 只提供「构建 + JUnit 制品」，所以这里只做触发、轮询、取制品，不复制 Jenkins 的数据。
from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import Settings
from app.core.exceptions import AppError
from app.models.task import Task
from app.models.test_case import TestCase
from app.services.executors.base import ExecutionResult, StartCallback
from app.utils.jenkins_client import JenkinsClient
from app.utils.junit_parser import parse_junit_xml

logger = logging.getLogger(__name__)


class JenkinsExecutor:
    name = "jenkins"

    def __init__(self, settings: Settings, client: JenkinsClient | None = None) -> None:
        self._settings = settings
        self._client = client or JenkinsClient(settings)

    def run(
        self,
        *,
        task: Task,
        cases: list[TestCase],
        timeout_seconds: int,
        on_start: StartCallback,
    ) -> ExecutionResult:
        # why：先判配置再标 running——缺配置时任务应快速失败，不能停在 running 等超时回收
        if not self._client.is_configured():
            raise AppError(
                "JENKINS_NOT_CONFIGURED",
                status_code=503,
                detail="Jenkins 执行器未配置（jenkins.base_url/username/job_name + .env token）",
            )

        # Jenkins 侧没有本地子进程可杀，pid 传 None；条件更新仍由编排层负责（取消竞态守卫不变）
        on_start(None)

        build = self._client.build_and_wait(
            task_id=task.id,
            case_ids=[int(cid) for cid in task.case_ids],
            base_url=self._settings.execution.base_url,
            timeout_seconds=timeout_seconds,
        )
        if build.result not in ("SUCCESS", "UNSTABLE"):
            # why：构建失败也要尝试取制品——pytest 有用例失败时构建是 FAILURE，但 junit 里带着逐用例结果，
            # 直接判任务失败会让平台拿不到"哪条用例红了"的信息
            logger.warning(
                "jenkins 构建 #%s 结果为 %s，仍尝试回读测试制品（task=%s url=%s）",
                build.number,
                build.result,
                task.id,
                build.url,
            )

        artifact_path = self._settings.jenkins.artifact_path
        try:
            raw = self._client.download_artifact(build.number, artifact_path)
        except AppError as e:
            raise AppError(
                "JENKINS_NO_REPORT",
                status_code=502,
                detail=f"构建 #{build.number} 未产出测试制品 {artifact_path}: {e.detail}",
            ) from e

        junit_path = self._download_path(task.id)
        junit_path.write_bytes(raw)
        try:
            summary, entries = parse_junit_xml(junit_path)
        except AppError as e:
            # why：制品不是合法 JUnit（如构建中断、脚本未跑测试）→ 明确归入解析失败，而不是"全部通过"
            raise AppError(
                "JENKINS_REPORT_INVALID",
                status_code=502,
                detail=f"构建 #{build.number} 的制品 {artifact_path} 不是合法 JUnit XML: {e.detail}",
            ) from e
        return ExecutionResult(summary=summary, entries=entries, build_url=build.url)

    def _download_path(self, task_id: int) -> Path:
        # why：制品落到按任务隔离的 workspace，复用既有 junit_parser 的路径语义，也便于排障时回看原始 XML
        workspace = Path(self._settings.execution.workspace_dir).resolve() / "tasks" / str(task_id)
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace / "jenkins-junit.xml"
