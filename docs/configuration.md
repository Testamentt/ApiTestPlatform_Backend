# 配置管理设计（configuration.md）· Phase 1-2 简化版

> 规则引用：[`.claude/rules/RULES.md`](../.claude/rules/RULES.md) §3.1（配置与密钥）。配置统一放 `config/`，Pydantic Settings 管理。
> **Phase 1-2 简化**：app/database/redis/celery/execution/swagger 六段；llm/security/frontend 属 Phase 3+，当前不建模。

## 1. 配置分层与加载

| 优先级（低→高） | 来源 | 说明 |
| --- | --- | --- |
| 1 | 代码默认值 | Settings 类字段默认值 |
| 2 | `config/settings.yaml` | 可读默认值（gitignored，复制 settings.example.yaml 使用） |
| 3 | 环境变量 / `.env`（`TESTPLATFORM_` 前缀） | 密钥与运行时覆盖，优先级最高 |

**加载机制**（`get_settings()` 显式合并，约 20 行）：
```python
def get_settings() -> Settings:
    data = _load_yaml("config/settings.yaml")      # ① yaml 作为默认值
    data = _merge_env_overrides(data)              # ② TESTPLATFORM_* 环境变量/.env 覆盖敏感项
    return Settings.model_validate(data)           # ③ extra=forbid 校验
```
> **注意**：不能直接 `Settings(**yaml)`——pydantic-settings 的 init 参数优先级高于 env，会吞掉 `.env` 覆盖。显式合并保证「yaml 默认、env 覆盖」方向正确。

- **密钥只放 `.env`**（禁止入库/入日志/入 git）；仓库只提交 `.env.example`。
- 新增配置项必须同步更新 `.env.example`。

## 2. Pydantic Settings 字段树（Phase 1）

采用 `pydantic.BaseModel`（**非 BaseSettings**）——env 覆盖是手动合并（§1），BaseSettings 的 env_prefix 会把 `TESTPLATFORM_APP_DEBUG` 当扁平字段注入，与嵌套结构冲突；`extra="forbid"`（未建模键启动即报错）。

### 2.1 App
| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| app.name | testplatform | |
| app.debug | false | 开发模式开启 /docs |
| app.version | 0.1.0 | |
| app.host | 0.0.0.0 | |
| app.port | 8000 | |

### 2.2 Database
| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| database.url | sqlite:///./data/platform.db | 生产替换 postgresql:// |
| database.echo | false | SQL 回显 |

> `get_engine()` 连接工厂统一执行 WAL / busy_timeout / foreign_keys，SQLite 必加 `check_same_thread=False`（RULES.md §2.1）。

### 2.3 Redis
| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| redis.host | 127.0.0.1 | |
| redis.port | 6379 | |
| redis.password | ""（经 `TESTPLATFORM_REDIS_PASSWORD`） | 本地按实际填写，禁止提交 |
| redis.db | 0 | |
| redis.socket_timeout | 2.0 | 健康检查探测超时（RULES §2.3：来自 config，禁止硬编码） |

### 2.4 Celery
| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| celery.broker_url | redis://127.0.0.1:6379/0 | 由 redis 段组合生成 |
| celery.result_backend | redis://127.0.0.1:6379/1 | 仅短期状态，`result_expires=3600` |
| celery.soft_time_limit | 300 | 任务软超时 |
| celery.time_limit | 360 | 任务硬超时（必须 > visibility） |
| celery.visibility_timeout | 3600 | **经 `broker_transport_options` 接线**，必须 > time_limit（RULES.md §8.2） |
| celery.max_retries | 3 | 仅瞬时异常重试（任务装饰器读取，非硬编码） |
| celery.result_expires | 3600 | result backend 存活时长（非硬编码） |

### 2.5 Execution
| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| execution.workspace_dir | .workspace | 动态测试文件 / report.xml / HTML 报告 |
| execution.base_url | http://httpbin.org | **写死的被测接口 base_url**（无 Environment 表，Phase 2 再补多环境） |
| execution.pytest_timeout | 300 | subprocess 超时阈值（来自 config，禁止硬编码） |
| execution.command_whitelist | [python, pytest] | run_cmd 命令白名单 |

### 2.6 Swagger（Phase 2 影响分析）
| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| swagger.max_upload_bytes | 2000000 | Swagger 文档大小上限（超限拒绝解析，RULES §10.3） |
| swagger.hash_version | 1 | 哈希算法版本（升级递增，旧快照不重建；diff 版本不一致全标 changed） |
| swagger.max_operation_ids_warn | 200 | operation 数告警阈值（**超限只 warning 继续入库**——IN(...200) 检索仍无碍，仅分析响应变长） |

> **新增字段全部带 default**：`settings.yaml`/`.env` 缺失时 pydantic 用默认值，**启动不阻塞**（兼容已部署的 Phase 1 配置）。

## 3. .env.example 变量清单（Phase 1-2）

```bash
# ---- App ----
TESTPLATFORM_APP_DEBUG=false
TESTPLATFORM_APP_HOST=0.0.0.0
TESTPLATFORM_APP_PORT=8000

# ---- Database ----
TESTPLATFORM_DATABASE_URL=sqlite:///./data/platform.db   # 生产: postgresql://user:pass@host:5432/db

# ---- Redis ----
TESTPLATFORM_REDIS_HOST=127.0.0.1
TESTPLATFORM_REDIS_PORT=6379
TESTPLATFORM_REDIS_PASSWORD=your_redis_password        # 占位符，按实际填写，真实值勿提交
TESTPLATFORM_REDIS_DB=0
TESTPLATFORM_REDIS_SOCKET_TIMEOUT=2.0                  # 健康检查探测超时（秒）

# ---- Celery ----
# Windows 本地 worker 必须 --pool=solo（见 scripts/start_worker.bat）
TESTPLATFORM_CELERY_SOFT_TIME_LIMIT=300
TESTPLATFORM_CELERY_TIME_LIMIT=360
TESTPLATFORM_CELERY_RESULT_EXPIRES=3600                # result backend 仅短期状态，存活时长

# ---- Execution ----
TESTPLATFORM_EXECUTION_BASE_URL=http://httpbin.org     # 被测接口 base_url（写死）
TESTPLATFORM_EXECUTION_PYTEST_TIMEOUT=300

# ---- Swagger（Phase 2 影响分析）----
TESTPLATFORM_SWAGGER_MAX_UPLOAD_BYTES=2000000          # 文档大小上限
TESTPLATFORM_SWAGGER_HASH_VERSION=1                    # 哈希算法版本
TESTPLATFORM_SWAGGER_MAX_OPERATION_IDS_WARN=200        # operation 数告警阈值
```

> llm（DEEPSEEK_API_KEY）、security（TESTPLATFORM_API_TOKEN）、frontend（CORS）相关变量在 Phase 2+ 再加回。

## 4. settings.example.yaml 契约

`config/settings.example.yaml` 列出 Phase 1 全部配置段 + 默认值 + 说明，注释标明「复制为 settings.yaml 使用（已被 .gitignore 忽略）」。与 `.env.example` 一一对应。

## 5. 本地与生产差异

| 维度 | 本地（Windows） | 生产（Docker） |
| --- | --- | --- |
| 数据库 | SQLite（data/platform.db） | PostgreSQL（postgresql:// 连接串） |
| Redis | 3.2 本地（127.0.0.1:6379） | Redis 7+（compose 服务） |
| Worker | `--pool=solo` | `--pool=prefork --concurrency=4` |
| base_url | httpbin.org（演示） | 内网被测服务地址 |
| /docs | 开放 | 关闭或鉴权保护 |
