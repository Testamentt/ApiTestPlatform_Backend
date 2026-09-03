# 配置管理设计（configuration.md）· Phase 1-4 实现版

> 规则引用：`RULES.md` §3.1（配置与密钥）。配置统一放 `config/`，Pydantic Settings 管理。
> **Phase 1-4 已实现**：app/database/redis/celery/execution/swagger/llm/security/frontend 九段；全带 default，缺失不阻塞启动。

## 1. 配置分层与加载

| 优先级（低→高） | 来源 | 说明 |
| --- | --- | --- |
| 1 | 代码默认值 | Settings 类字段默认值 |
| 2 | `config/settings.yaml` | 可读默认值（gitignored，复制 settings.example.yaml 使用） |
| 3 | 环境变量 / `.env`（`TESTPLATFORM_` 前缀） | 密钥与运行时覆盖，优先级最高 |

**加载机制**（`get_settings()` 显式合并）：
```python
def get_settings() -> Settings:
    data = _load_yaml_defaults()  # ① yaml 作为默认值（缺失回退空 dict）
    data = _merge_env_overrides(data)  # ② TESTPLATFORM_* 环境变量/.env 覆盖敏感项
    return Settings.model_validate(data)  # ③ extra=forbid 校验
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
| app.docs_enabled | true | **/docs 开关**（§10.5：生产设 `TESTPLATFORM_APP_DOCS_ENABLED=false` 即关；默认开供演示开箱即用） |

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
| execution.base_url | http://httpbin.org（代码默认） | **被测接口 base_url**：默认本地 mock（`scripts/mock_target.py`，127.0.0.1:9999）或管伊佳ERP（`http://127.0.0.1:9999/jshERP-boot`）；compose 默认 `http://mock:9999` 全离线（无 Environment 表，多环境延后） |
| execution.pytest_timeout | 300 | **默认** subprocess 超时阈值（任务级 `timeout_seconds` 缺省时使用；来自 config，禁止硬编码） |
| execution.command_whitelist | [python, python3*, pytest] | run_cmd 命令白名单；**`*` 尾缀 = 前缀匹配**（兼容 Linux/Docker 的 `python3.12`，review H1） |
| execution.auth_enabled | false | **被测系统鉴权适配开关**：true 时执行层 workspace 生成登录 conftest（session fixture 登录取 token 注入请求头，N 用例只登 1 次） |
| execution.auth_login_path | "" | 登录接口路径（与 base_url 同源拼接） |
| execution.auth_login_body | `{"username": "{username}", "password": "{password}"}` | 登录体字段名模板（`{username}`/`{password}` 运行期替换；**平坦字符串 dict 约束**，嵌套字段不支持） |
| execution.auth_username_env / auth_password_env | ERP_TEST_USERNAME / ERP_TEST_PASSWORD | 登录凭证环境变量名——**密钥只放 .env（§8）**，pytest 运行时读取，生成文件不含凭证 |
| execution.auth_token_header | X-Access-Token | 登录后注入每个请求头的 token 字段名 |
| execution.auth_token_field | data.token | 登录响应里 token 的点路径提取（如 `data.token`） |
| execution.auth_password_encoding | plain | plain\|md5（部分旧系统密码需摘要后传输） |

> why：auth_* 用**扁平字段**而非嵌套 dict——env 合并（`_merge_env_overrides`）只支持一级 `section.field`；登录体是 dict，不支持 env 覆盖，需在 `config/settings.yaml` 配置。

### 2.6 Swagger（Phase 2 影响分析）
| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| swagger.max_upload_bytes | 2000000 | Swagger 文档大小上限（超限拒绝解析，RULES §10.3） |
| swagger.hash_version | 2 | 哈希算法版本（**升级递增**；2=sha256 指纹，L3 起——旧快照 1/md5 与新解析 diff 保守全标 changed，防跨算法混比） |
| swagger.max_operation_ids_warn | 200 | operation 数告警阈值（**超限只 warning 继续入库**——IN(...200) 检索仍无碍，仅分析响应变长） |

### 2.7 LLM（Phase 3 AI 生成）
| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| llm.api_key_env | DEEPSEEK_API_KEY | API key **环境变量名**（密钥只放 .env，RULES §3.1/§8） |
| llm.base_url | https://api.deepseek.com | DeepSeek（OpenAI 兼容协议，openai SDK base_url） |
| llm.model | deepseek-chat | |
| llm.temperature | 0 | **确定性/可复现**（RULES §9.3，生成类固定 0） |
| llm.max_tokens | 4096 | 生成上限 |
| llm.max_input_chars | 50000 | 调用前输入长度预检阈值（§9.3：超限拒绝，防上下文裸奔） |
| llm.connect_timeout / read_timeout | 5 / 60 | 连接/读超时（RULES §2.3：来自 config 禁止硬编码） |
| llm.max_retries | 3 | 瞬时异常重试次数（RULES §9.4） |
| llm.retry_backoff | 1 | 指数退避基数（秒） |
| llm.cost_per_1k_tokens | 0.001 | **成本估算单价**（demo 均价，$/1K tokens；精算留生产） |
| llm.task_soft_timeout_seconds | 540 | **生成任务软超时**（Celery `soft_time_limit`，留 60s 清理窗口；§8.2 捕获落 FAILED） |
| llm.task_timeout_seconds | 600 | **生成任务硬超时**（Celery `time_limit`，200 接口串行 ≈400s 兜底） |

### 2.8 Security（Phase 4 Bearer Token 鉴权）
| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| security.api_token | testplatform-dev-token | **dev 默认仅供本地演示**（开箱即用）；生产/CI 必须 `TESTPLATFORM_SECURITY_API_TOKEN` 覆盖（§3.1 禁写死密钥） |

### 2.9 Frontend（Phase 4 CORS 白名单）
| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| frontend.cors_origins | [] | **CORS 白名单**（默认空 = 不挂中间件不发 CORS 头，§10.5）；Vue 前端已实现但 dev 走 Vite 同源代理，不依赖 CORS；跨域直连时填来源如 `[http://localhost:5173]` |

> **新增字段全部带 default**：`settings.yaml`/`.env` 缺失时 pydantic 用默认值，**启动不阻塞**（兼容已部署的 Phase 1 配置）。

## 3. .env.example 变量清单（Phase 1-4）

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
# 被测接口 base_url：默认本地 mock（先 python scripts/mock_target.py）；ERP 演示改为 :9999/jshERP-boot
TESTPLATFORM_EXECUTION_BASE_URL=http://127.0.0.1:9999
TESTPLATFORM_EXECUTION_PYTEST_TIMEOUT=300
# 被测系统登录凭证（execution.auth_enabled 时使用；密钥只放 .env，§8）
# 鉴权开关/登录体模板在 config/settings.yaml 的 execution.auth_* 段（登录体是 dict，不支持 env 覆盖）
ERP_TEST_USERNAME=admin
ERP_TEST_PASSWORD=your_erp_password

# ---- Swagger（Phase 2 影响分析）----
TESTPLATFORM_SWAGGER_MAX_UPLOAD_BYTES=2000000          # 文档大小上限
TESTPLATFORM_SWAGGER_HASH_VERSION=2                    # 哈希算法版本（2=sha256 指纹，L3 起）
TESTPLATFORM_SWAGGER_MAX_OPERATION_IDS_WARN=200        # operation 数告警阈值

# ---- LLM（Phase 3 AI 生成）----
DEEPSEEK_API_KEY=sk-your-deepseek-api-key              # DeepSeek key（密钥只放 .env，勿提交）
TESTPLATFORM_LLM_BASE_URL=https://api.deepseek.com     # OpenAI 兼容协议
TESTPLATFORM_LLM_MODEL=deepseek-chat
TESTPLATFORM_LLM_TEMPERATURE=0                         # 确定性，可复现
TESTPLATFORM_LLM_MAX_TOKENS=4096
TESTPLATFORM_LLM_MAX_INPUT_CHARS=50000                 # 输入长度预检阈值（超限拒绝）
TESTPLATFORM_LLM_CONNECT_TIMEOUT=5                     # 连接超时
TESTPLATFORM_LLM_READ_TIMEOUT=60                       # 读超时
TESTPLATFORM_LLM_MAX_RETRIES=3                         # 瞬时异常重试次数
TESTPLATFORM_LLM_RETRY_BACKOFF=1                       # 指数退避基数
TESTPLATFORM_LLM_COST_PER_1K_TOKENS=0.001              # 成本估算单价（demo 均价）
TESTPLATFORM_LLM_TASK_SOFT_TIMEOUT_SECONDS=540         # 生成任务软超时（捕获落 FAILED）
TESTPLATFORM_LLM_TASK_TIMEOUT_SECONDS=600              # 生成任务硬超时

# ---- Security（Phase 4 Bearer Token 鉴权）----
TESTPLATFORM_SECURITY_API_TOKEN=testplatform-dev-token  # dev 默认仅供演示；生产必须覆盖（§3.1）

# ---- Frontend（Phase 4 CORS 白名单）----
# TESTPLATFORM_FRONTEND_CORS_ORIGINS=["http://localhost:5173"]   # 前端做时填来源（空=不挂中间件）
```

> security/frontend 变量已在 Phase 4 实现（见上表）。CORS 空 = 不挂中间件（§10.5）。

## 4. settings.example.yaml 契约

`config/settings.example.yaml` 列出 Phase 1-4 全部配置段 + 默认值 + 说明，注释标明「复制为 settings.yaml 使用（已被 .gitignore 忽略）」。与 `.env.example` 一一对应；容器镜像用 example 兜底为 settings.yaml，运行配置走 env 覆盖。

## 5. 本地与生产差异

| 维度 | 本地（Windows） | 生产（Docker） |
| --- | --- | --- |
| 数据库 | SQLite（data/platform.db） | SQLite（/app/data/platform.db，named volume 持久化，§3.3） |
| Redis | 3.2 本地（127.0.0.1:6379） | Redis 7（compose 服务，内网隔离无密码） |
| Worker | `--pool=solo` | `--pool=solo --concurrency=1`（单写者串行写 SQLite，§3.3） |
| base_url | 本地 mock（127.0.0.1:9999）或管伊佳ERP（:9999/jshERP-boot） | 容器内置 mock 服务（`http://mock:9999` 全离线）或 env 覆盖真环境；被测系统鉴权经 `execution.auth_*` 适配 |
| /docs | 默认开（docs_enabled=true） | 生产 `TESTPLATFORM_APP_DOCS_ENABLED=false` 关闭（§10.5） |
| 鉴权 | Bearer Token（dev 默认 token） | Bearer Token（env 注入生产 token） |
