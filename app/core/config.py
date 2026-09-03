# 配置管理。why：settings.yaml 管可读默认值，.env / 环境变量管密钥与运行时覆盖；
# 显式合并保证「yaml 默认、env 覆盖」方向正确——pydantic-settings 的 init 参数优先级高于 env，
# 不能直接 Settings(**yaml)（会把 .env 覆盖吞掉）。
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # backend/


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "testplatform"
    debug: bool = False
    version: str = "0.1.0"
    host: str = "0.0.0.0"
    port: int = 8000
    docs_enabled: bool = (
        True  # Swagger UI /docs 开关（§10.5：生产设 TESTPLATFORM_APP_DOCS_ENABLED=false 即关）
    )


class DatabaseSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = "sqlite:///./data/platform.db"
    echo: bool = False


class RedisSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = 6379
    password: str = ""
    db: int = 0
    socket_timeout: float = 2.0  # 健康检查探测超时（RULES §2.3：来自 config，禁止硬编码）


class CelerySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    broker_url: str = ""
    result_backend: str = ""
    soft_time_limit: int = 300
    time_limit: int = 360
    visibility_timeout: int = 3600
    max_retries: int = 3
    result_expires: int = 3600


class ExecutionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_dir: str = ".workspace"
    base_url: str = "http://httpbin.org"
    pytest_timeout: int = 300
    command_whitelist: list[str] = ["python", "pytest"]
    # 被测系统鉴权适配（配置驱动，如管伊佳ERP 的 X-Access-Token）。why：扁平字段而非嵌套——
    # env 合并逻辑（_merge_env_overrides）只支持一级 section.field，嵌套 dict 无法经 env 覆盖；
    # enabled 时执行层生成登录 conftest：session 级登录一次取 token 注入每个请求头，
    # 凭证经 env 名引用（密钥只放 .env，§8），生成的测试文件不含密钥
    auth_enabled: bool = False
    auth_login_path: str = ""  # 登录接口路径（与 base_url 同源拼接）
    auth_login_body: dict = {"username": "{username}", "password": "{password}"}  # 字段名模板
    auth_username_env: str = "ERP_TEST_USERNAME"
    auth_password_env: str = "ERP_TEST_PASSWORD"
    auth_token_header: str = "X-Access-Token"
    auth_token_field: str = "data.token"  # 登录响应里 token 的点路径
    auth_password_encoding: str = "plain"  # plain|md5（部分系统密码需摘要后传输）


class SwaggerSettings(BaseModel):
    """Phase 2 影响分析配置。why：全带 default——settings.yaml/.env 缺失新字段时启动不阻塞（兼容 Phase 1 已部署配置）。"""

    model_config = ConfigDict(extra="forbid")

    max_upload_bytes: int = 2_000_000
    # why=2：L3 指纹算法 md5→sha256 时按本字段语义递增——旧快照（1/md5）与新解析（2/sha256）
    # diff 时保守全标 changed，防跨算法指纹混比（文档漂移审计 2026-09-03）
    hash_version: int = 2
    max_operation_ids_warn: int = 200


class LlmSettings(BaseModel):
    """Phase 3 AI 生成配置。why：密钥经环境变量名引用（api_key_env，只放 .env）；
    cost 为 demo 均价估算（精算留生产）；task_timeout_seconds 是生成任务 Celery 硬超时。"""

    model_config = ConfigDict(extra="forbid")

    api_key_env: str = "DEEPSEEK_API_KEY"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    temperature: float = 0
    max_tokens: int = 4096
    max_input_chars: int = 50000  # 调用前输入长度预检阈值（§9.3：超限拒绝，防上下文裸奔）
    connect_timeout: float = 5
    read_timeout: float = 60
    max_retries: int = 3
    retry_backoff: float = 1
    cost_per_1k_tokens: float = 0.001
    task_soft_timeout_seconds: int = (
        540  # 生成任务软超时（< task_timeout_seconds，留 60s 清理窗口；RULES §8.2）
    )
    task_timeout_seconds: int = 600


class SecuritySettings(BaseModel):
    """Phase 4 Bearer Token 鉴权配置。why：api_token 的 dev 默认仅供本地演示开箱即用；
    生产/CI 必须经 TESTPLATFORM_SECURITY_API_TOKEN 覆盖（§3.1 禁写死密钥，dev 占位默认值不泄真实密钥）。"""

    model_config = ConfigDict(extra="forbid")

    api_token: str = "testplatform-dev-token"


class FrontendSettings(BaseModel):
    """Phase 4 CORS 配置（预留）。why：MVP 零前端，默认空白名单不发 CORS 头（§10.5 禁止 *）；
    前端做时把来源填进 cors_origins 即自动挂 CORSMiddleware。"""

    model_config = ConfigDict(extra="forbid")

    cors_origins: list[str] = []


class Settings(BaseModel):
    """全量配置。extra=forbid：未建模键（含将来 Phase 2+ 的 TESTPLATFORM_LLM_* 等）会启动即报错。
    why：env 合并是手动的（settings.yaml 默认值 + .env/os.environ 覆盖），故用 BaseModel 而非 BaseSettings——
    BaseSettings 的 env_prefix 会把 TESTPLATFORM_APP_DEBUG 当扁平字段注入，与嵌套结构冲突。"""

    model_config = ConfigDict(extra="forbid")

    app: AppSettings = AppSettings()
    database: DatabaseSettings = DatabaseSettings()
    redis: RedisSettings = RedisSettings()
    celery: CelerySettings = CelerySettings()
    execution: ExecutionSettings = ExecutionSettings()
    swagger: SwaggerSettings = SwaggerSettings()
    llm: LlmSettings = LlmSettings()
    security: SecuritySettings = SecuritySettings()
    frontend: FrontendSettings = FrontendSettings()

    @property
    def broker_url(self) -> str:
        if self.celery.broker_url:
            return self.celery.broker_url
        auth = f":{self.redis.password}@" if self.redis.password else ""
        return f"redis://{auth}{self.redis.host}:{self.redis.port}/{self.redis.db}"

    @property
    def result_backend(self) -> str:
        if self.celery.result_backend:
            return self.celery.result_backend
        auth = f":{self.redis.password}@" if self.redis.password else ""
        return f"redis://{auth}{self.redis.host}:{self.redis.port}/{self.redis.db + 1}"


def _load_yaml_defaults() -> dict[str, Any]:
    """读取 config/settings.yaml 作为默认值；文件缺失时回退空 dict（用类默认值）。"""
    path = PROJECT_ROOT / "config" / "settings.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _parse_value(raw: str) -> Any:
    """把 env 字符串尽量转为 int/bool/list，转换失败保留字符串（如密码）。"""
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    if raw.startswith("[") and raw.endswith("]"):
        return [item.strip().strip("\"'") for item in raw[1:-1].split(",") if item.strip()]
    try:
        return int(raw)
    except ValueError:
        return raw


def _merge_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """把 TESTPLATFORM_EXECUTION_PYTEST_TIMEOUT 之类环境变量覆盖进嵌套 data。
    拆分规则：首段为配置段名，其余为字段名（字段名本身可含下划线，如 pytest_timeout）。"""
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.startswith("TESTPLATFORM_"):
            env[key[len("TESTPLATFORM_") :].lower()] = value
    for key, value in env.items():
        section, _, field = key.partition("_")
        data.setdefault(section, {})
        data[section][field] = _parse_value(value)
    return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """单例工厂。why：启动时加载一次，避免每次请求重复解析 yaml/env。"""
    load_dotenv(PROJECT_ROOT / ".env")  # 不覆盖已存在的系统环境变量
    data = _load_yaml_defaults()
    data = _merge_env_overrides(data)
    return Settings.model_validate(data)
