# 本项目编码格式：UTF-8（无 BOM）。
# Jenkins REST 客户端：外部调用的唯一封装（与 llm_client 同构）。
# why：① 业务代码禁止裸调 httpx，超时集中来自 config；② API Token 只从 .env 的 env 名读取，
# 绝不入日志/入 URL（query 里放凭据会被访问日志与代理记录）；③ 触发→排队→轮询→取制品的
# 状态收敛在这里，执行器只关心「拿回 JUnit 结果」。
from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

# Jenkins 的 CSRF 保护要求 POST 带 crumb；crumb 绑定会话语义，因此复用同一个 httpx.Client（带 Cookie）
_CRUMB_PATH = "/crumbIssuer/api/json"


@dataclass(frozen=True)
class JenkinsBuild:
    """构建终态快照。why：只带执行器需要的字段，避免把 Jenkins 的完整 JSON 泄漏到业务层。"""

    number: int
    url: str
    result: str | None
    duration_ms: int


class JenkinsClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings or get_settings()
        # transport 仅测试注入（httpx.MockTransport），生产走默认网络栈
        self._transport = transport
        self._sleep = sleep
        self._clock = clock

    # ---------------------------------------------------------------- 配置
    def is_configured(self) -> bool:
        cfg = self._settings.jenkins
        return bool(cfg.base_url and cfg.username and cfg.job_name and self._token())

    def _token(self) -> str:
        # why：密钥只放 .env，配置里存的是「环境变量名」——找不到即视为未配置，不回落明文
        return os.getenv(self._settings.jenkins.api_token_env, "").strip()

    def _client(self) -> httpx.Client:
        cfg = self._settings.jenkins
        token = self._token()
        if not (cfg.base_url and cfg.username and token and cfg.job_name):
            raise AppError(
                "JENKINS_NOT_CONFIGURED",
                status_code=503,
                detail=(
                    "Jenkins 未配置：需要 jenkins.base_url / jenkins.username / "
                    f"jenkins.job_name 与 .env 中的 {cfg.api_token_env}"
                ),
            )
        # token 只进 Authorization 头；不拼进 URL、不进日志
        return httpx.Client(
            base_url=cfg.base_url.rstrip("/"),
            auth=(cfg.username, token),
            timeout=httpx.Timeout(
                cfg.read_timeout,
                connect=cfg.connect_timeout,
                write=cfg.connect_timeout,
                pool=cfg.connect_timeout,
            ),
            transport=self._transport,
            follow_redirects=True,
        )

    # ---------------------------------------------------------------- 对外入口
    def build_and_wait(
        self,
        *,
        task_id: int,
        case_ids: list[int],
        base_url: str,
        timeout_seconds: int,
    ) -> JenkinsBuild:
        """触发流水线并等待构建结束。why：平台任务超时即协议超时，避免 Celery 任务被无限期占用。"""
        deadline = self._clock() + max(1, timeout_seconds)
        with self._client() as client:
            queue_url = self._trigger(client, task_id, case_ids, base_url)
            build_number = self._wait_queue(client, queue_url, deadline)
            return self._wait_build(client, build_number, deadline)

    def download_artifact(self, build_number: int, artifact_path: str) -> bytes:
        """取构建制品（JUnit XML）。why：复用平台既有 junit_parser，保证报告口径与本地执行一致。"""
        cfg = self._settings.jenkins
        with self._client() as client:
            url = f"/job/{cfg.job_name}/{build_number}/artifact/{artifact_path}"
            resp = self._request(client, "GET", url)
            return resp.content

    # ---------------------------------------------------------------- 内部步骤
    def _trigger(
        self, client: httpx.Client, task_id: int, case_ids: list[int], base_url: str
    ) -> str:
        cfg = self._settings.jenkins
        params = {
            "TASK_ID": str(task_id),
            "CASE_IDS": ",".join(str(c) for c in case_ids),
            "BASE_URL": base_url,
        }
        resp = self._request(
            client,
            "POST",
            f"/job/{cfg.job_name}/buildWithParameters",
            params=params,
            headers=self._crumb_headers(client),
        )
        queue_url = resp.headers.get("Location", "")
        if not queue_url:
            raise AppError(
                "JENKINS_TRIGGER_FAILED",
                status_code=502,
                detail="触发构建成功但响应缺少 Location（无法定位队列项）",
            )
        logger.info("jenkins 构建已入队 task=%s queue=%s", task_id, queue_url)
        return queue_url

    def _wait_queue(self, client: httpx.Client, queue_url: str, deadline: float) -> int:
        cfg = self._settings.jenkins
        url = f"{queue_url.rstrip('/')}/api/json?tree=executable[number],cancelled"
        while True:
            self._ensure_deadline(deadline, "等待 Jenkins 队列分配构建号")
            data = self._request(client, "GET", url).json()
            if data.get("cancelled"):
                raise AppError(
                    "JENKINS_BUILD_CANCELLED", status_code=409, detail="Jenkins 构建在队列中被取消"
                )
            executable = data.get("executable") or {}
            number = executable.get("number")
            if isinstance(number, int):
                return number
            self._sleep(cfg.poll_interval_seconds)

    def _wait_build(self, client: httpx.Client, build_number: int, deadline: float) -> JenkinsBuild:
        cfg = self._settings.jenkins
        url = (
            f"/job/{cfg.job_name}/{build_number}/api/json?tree=building,result,url,number,duration"
        )
        while True:
            self._ensure_deadline(deadline, f"等待 Jenkins 构建 #{build_number} 完成")
            data = self._request(client, "GET", url).json()
            if not data.get("building"):
                return JenkinsBuild(
                    number=int(data.get("number", build_number)),
                    url=str(data.get("url", "")),
                    result=data.get("result"),
                    duration_ms=int(data.get("duration") or 0),
                )
            self._sleep(cfg.poll_interval_seconds)

    def _crumb_headers(self, client: httpx.Client) -> dict[str, str]:
        # why：CSRF 关闭时该端点 404——此时无需 crumb，不能当成失败（兼容旧版/精简部署）
        resp = self._request(client, "GET", _CRUMB_PATH, allow_404=True)
        if resp.status_code == 404:
            return {}
        data = resp.json()
        field = data.get("crumbRequestField")
        crumb = data.get("crumb")
        return {field: crumb} if field and crumb else {}

    def _ensure_deadline(self, deadline: float, action: str) -> None:
        if self._clock() >= deadline:
            raise AppError(
                "JENKINS_TIMEOUT", status_code=504, detail=f"{action}超时（任务超时阈值）"
            )

    def _request(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        *,
        allow_404: bool = False,
        **kwargs,
    ) -> httpx.Response:
        try:
            resp = client.request(method, url, **kwargs)
        except httpx.TimeoutException as e:
            raise AppError(
                "JENKINS_TIMEOUT", status_code=504, detail=f"Jenkins 请求超时: {url}"
            ) from e
        except httpx.HTTPError as e:
            raise AppError(
                "JENKINS_UNAVAILABLE", status_code=502, detail=f"Jenkins 不可达: {url}"
            ) from e
        if allow_404 and resp.status_code == 404:
            return resp
        if resp.status_code >= 400:
            raise AppError(
                "JENKINS_HTTP_ERROR",
                status_code=502,
                detail=f"Jenkins 返回 {resp.status_code}: {url}",
            )
        return resp
