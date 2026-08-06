# 配置管理设计（configuration.md）

> 规则引用：[`.claude/rules/RULES.md`](../.claude/rules/RULES.md) §3.1（配置与密钥）。配置统一放 `config/`，用 Pydantic Settings 管理，支持 `.env` 覆盖。

## 1. 配置分层

| 优先级（低→高） | 来源 | 说明 |
| --- | --- | --- |
| 1 | `config/settings.example.yaml` | 默认值 + 注释（提交仓库） |
| 2 | `config/settings.yaml` | 本地自定义（gitignored，复制 example 使用） |
| 3 | 环境变量 `.env`（`TESTPLATFORM_` 前缀） | 密钥与运行时覆盖，优先级最高 |

- **密钥只放 `.env`**：禁止入库、入日志、入 Prompt、入 git（RULES.md §3.1 红线 8）。
- 仓库只提交 `.env.example`（占位符 + 注释说明）。
- 新增配置项必须同步更新 `.env.example`。

## 2. Pydantic Settings 字段树

采用 pydantic-settings 2.x，`extra="forbid"`（未建模键直接报错，防配置悄悄失效），`api_key_env` 模式（配置只存环境变量**名**，运行时 `os.getenv` 取密钥）。

### 2.1 App

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| app.name | testplatform | |
| app.debug | false | 开发模式开启 /docs |
| app.version | 0.1.0 | |
| app.host | 0.0.0.0 | |
| app.port | 8000 | |
| app.docs_enabled | true | 生产环境关闭或鉴权（RULES.md §10.5） |
| app.cors_origins | []（开发加 `http://localhost:5173`） | CORS 白名单，禁止 `*`；生产同源托管则无需 |

### 2.2 Database

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| database.url | sqlite:///./data/platform.db | 生产替换 postgresql:// |
| database.echo | false | SQL 回显 |
| database.busy_timeout_ms | 5000 | 对应 `PRAGMA busy_timeout` |

> `get_engine()` 连接工厂统一执行 WAL / busy_timeout / foreign_keys（RULES.md §2.1）。

### 2.3 Redis

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| redis.host | 127.0.0.1 | |
| redis.port | 6379 | |
| redis.password | ""（经 `TESTPLATFORM_REDIS_PASSWORD`） | 本地 1234abcd，禁止提交 |
| redis.db | 0 | |

### 2.4 Celery

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| celery.broker_url | redis://127.0.0.1:6379/0 | 由 redis 段组合生成 |
| celery.result_backend | redis://127.0.0.1:6379/1 | 仅短期状态，`result_expires=3600` |
| celery.soft_time_limit | 300 | 任务软超时 |
| celery.time_limit | 360 | 任务硬超时（必须 > visibility） |
| celery.visibility_timeout | 3600 | 必须 > time_limit（RULES.md §8.2） |
| celery.worker_pool | solo | Windows 本地必须 solo |
| celery.worker_concurrency | 1 | SQLite 单写者（RULES.md §3.3） |
| celery.max_retries | 3 | 仅瞬时异常重试 |
| celery.retry_backoff_max | 300 | |

### 2.5 Execution

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| execution.workspace_dir | .workspace | 动态测试文件/Allure 结果 |
| execution.pytest_timeout | 300 | subprocess 超时阈值（来自 config，禁止硬编码） |
| execution.max_stdout_bytes | 10485760 | 输出捕获限容 10MB |
| execution.keep_workspace | false | 调试用，保留工作目录 |
| execution.allure_enabled | true | |
| execution.command_whitelist | [python, pytest, allure] | run_cmd 命令白名单 |

### 2.6 LLM

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| llm.provider | deepseek | deepseek / qwen / openai |
| llm.base_url | https://api.deepseek.com | 兼容端点（改 base_url 即切通义） |
| llm.model_chain | [deepseek-chat] | primary + fallback 调用链（RULES.md §9.4） |
| llm.api_key_env | DEEPSEEK_API_KEY | 只存环境变量名，不写明文 |
| llm.temperature | 0.0 | 生成类固定 0（可复现） |
| llm.connect_timeout | 5 | |
| llm.read_timeout | 60 | |
| llm.max_tokens | 4096 | 用例生成默认 |
| llm.max_retries | 3 | 指数退避 1s/3s/9s + 抖动 |
| llm.rate_limit_rpm | 30 | 客户端级限流（令牌桶） |

### 2.7 Security

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| security.api_token_env | TESTPLATFORM_API_TOKEN | Bearer Token 来源（非硬编码） |
| security.rate_limit_window | 60 | Redis 固定窗口（秒） |
| security.rate_limit_max | 30 | 窗口内最大请求数 |

### 2.8 Frontend（Vue 前后端分离）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| frontend.dev_port | 5173 | Vite 开发服务器端口 |
| frontend.dev_proxy_target | http://127.0.0.1:8000 | 开发代理目标（FastAPI） |
| frontend.dist_dir | frontend/dist | 构建产物目录（生产由 FastAPI 静态托管或 nginx） |

## 3. .env.example 变量清单

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
TESTPLATFORM_REDIS_PASSWORD=1234abcd                    # 本地 Redis 密码（示例值，真实值勿提交）
TESTPLATFORM_REDIS_DB=0

# ---- Celery ----
TESTPLATFORM_CELERY_WORKER_POOL=solo                    # Windows 本地必须 solo
TESTPLATFORM_CELERY_CONCURRENCY=1                       # SQLite 单写者

# ---- LLM（密钥必填）----
TESTPLATFORM_LLM_BASE_URL=https://api.deepseek.com
TESTPLATFORM_LLM_MODEL_CHAIN=deepseek-chat
TESTPLATFORM_LLM_TEMPERATURE=0.0
DEEPSEEK_API_KEY=<填入你的 DeepSeek API Key>           # 密钥：仅放 .env，禁止入库/入 git

# ---- Security（MVP 鉴权）----
TESTPLATFORM_API_TOKEN=<填入你的访问 Token>

# ---- Frontend（Vue 前后端分离）----
TESTPLATFORM_APP_CORS_ORIGINS=http://localhost:5173   # 开发前端来源；生产同源托管则留空
TESTPLATFORM_FRONTEND_DEV_PORT=5173                   # Vite 开发端口
TESTPLATFORM_FRONTEND_PROXY_TARGET=http://127.0.0.1:8000   # 开发代理目标（FastAPI）
```

## 4. settings.example.yaml 契约

与 `.env.example` 配套：`config/settings.example.yaml` 列出全部配置段、默认值与说明，注释标明「复制为 settings.yaml 使用（已被 .gitignore 忽略）」。完整内容见 [config/settings.example.yaml](../config/settings.example.yaml)。

## 5. 本地与生产差异

| 维度 | 本地（Windows） | 生产（Docker） |
| --- | --- | --- |
| 前端 | Vite dev（5173，代理到 8000） | `frontend/dist` 由 FastAPI 静态托管或 nginx |
| 数据库 | SQLite（data/platform.db） | PostgreSQL（postgresql:// 连接串） |
| Redis | 3.2 本地（127.0.0.1:6379） | Redis 7+（compose 服务） |
| Worker | `--pool=solo` | `--pool=prefork --concurrency=4` |
| /docs | 开放 | 关闭或鉴权保护 |
| 备份 | — | `sqlite3 <db> ".backup"` 或 `VACUUM INTO`（RULES.md §3.3） |
