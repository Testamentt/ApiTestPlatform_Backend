# 执行器单测：工厂选择 + Jenkins 执行器回读链路（假客户端，无网络）。
# why：执行器是「在哪里跑」的唯一分叉点——这里锁死 local 不被误路由、Jenkins 结果与失败码不失真。
from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.core.config import Settings
from app.core.exceptions import AppError
from app.services.executors import get_executor
from app.services.executors.jenkins import JenkinsExecutor
from app.services.executors.local import LocalExecutor
from app.utils.jenkins_client import JenkinsBuild

from tests.fakes import JUNIT_FAIL


def _settings(executor: str = "local") -> Settings:
    settings = Settings()
    settings.execution.executor = executor
    settings.execution.workspace_dir = ".workspace"
    settings.jenkins.base_url = "http://jenkins.test"
    settings.jenkins.username = "admin"
    settings.jenkins.job_name = "cases"
    settings.jenkins.artifact_path = "reports/junit.xml"
    return settings


def _task() -> SimpleNamespace:
    return SimpleNamespace(id=4242, case_ids=[1, 2])


class _FakeClient:
    def __init__(
        self,
        *,
        configured: bool = True,
        result: str = "SUCCESS",
        artifact: bytes = JUNIT_FAIL.encode("utf-8"),
        artifact_error: AppError | None = None,
    ) -> None:
        self._configured = configured
        self._result = result
        self._artifact = artifact
        self._artifact_error = artifact_error
        self.build_kwargs: dict = {}

    def is_configured(self) -> bool:
        return self._configured

    def build_and_wait(self, **kwargs) -> JenkinsBuild:
        self.build_kwargs = kwargs
        return JenkinsBuild(
            number=7, url="http://jenkins.test/job/cases/7/", result=self._result, duration_ms=100
        )

    def download_artifact(self, build_number: int, artifact_path: str) -> bytes:
        assert build_number == 7
        if self._artifact_error is not None:
            raise self._artifact_error
        return self._artifact


def test_factory_routes_by_execution_executor():
    assert isinstance(get_executor(settings=_settings("local")), LocalExecutor)
    assert isinstance(get_executor(settings=_settings("jenkins")), JenkinsExecutor)


def test_factory_rejects_unknown_executor():
    with pytest.raises(AppError) as exc:
        get_executor(settings=_settings("k8s"))
    assert exc.value.code == "EXECUTOR_NOT_SUPPORTED"


def test_jenkins_executor_parses_artifact(tmp_path):
    settings = _settings("jenkins")
    settings.execution.workspace_dir = str(tmp_path)
    client = _FakeClient(result="FAILURE")  # 有用例失败时构建是 FAILURE，仍要回读结果
    executor = JenkinsExecutor(settings, client)
    started: list[int | None] = []

    outcome = executor.run(
        task=_task(),
        cases=[],
        timeout_seconds=120,
        on_start=started.append,
    )

    # Jenkins 侧无本地进程：on_start 必须传 None（否则编排层会把无关 pid 记进任务）
    assert started == [None]
    assert client.build_kwargs["task_id"] == 4242
    assert client.build_kwargs["case_ids"] == [1, 2]
    assert outcome.summary.total == 1
    assert outcome.summary.failed == 1
    assert outcome.build_url == "http://jenkins.test/job/cases/7/"
    assert (tmp_path / "tasks" / "4242" / "jenkins-junit.xml").is_file()


def test_jenkins_executor_requires_configuration(tmp_path):
    settings = _settings("jenkins")
    settings.execution.workspace_dir = str(tmp_path)
    executor = JenkinsExecutor(settings, _FakeClient(configured=False))
    with pytest.raises(AppError) as exc:
        executor.run(task=_task(), cases=[], timeout_seconds=60, on_start=lambda _pid: None)
    assert exc.value.code == "JENKINS_NOT_CONFIGURED"


def test_jenkins_executor_missing_artifact_maps_error(tmp_path):
    settings = _settings("jenkins")
    settings.execution.workspace_dir = str(tmp_path)
    client = _FakeClient(
        artifact_error=AppError("JENKINS_HTTP_ERROR", status_code=502, detail="404")
    )
    executor = JenkinsExecutor(settings, client)
    with pytest.raises(AppError) as exc:
        executor.run(task=_task(), cases=[], timeout_seconds=60, on_start=lambda _pid: None)
    assert exc.value.code == "JENKINS_NO_REPORT"


def test_jenkins_executor_rejects_invalid_junit(tmp_path):
    settings = _settings("jenkins")
    settings.execution.workspace_dir = str(tmp_path)
    executor = JenkinsExecutor(settings, _FakeClient(artifact=b"not-xml"))
    with pytest.raises(AppError) as exc:
        executor.run(task=_task(), cases=[], timeout_seconds=60, on_start=lambda _pid: None)
    assert exc.value.code == "JENKINS_REPORT_INVALID"
