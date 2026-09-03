# 智能化接口测试效能平台

[![CI](https://github.com/Testamentt/ApiTestPlatform_Backend/actions/workflows/ci.yml/badge.svg)](https://github.com/Testamentt/ApiTestPlatform_Backend/actions/workflows/ci.yml)

> **AI 提效 + 异步解耦 + 精准回归** —— 让接口用例编写从「重复劳动」变为「审核确认」，让回归范围从「经验猜测」变为「血缘圈定」，让执行从「阻塞卡死」变为「异步兜底」。

基于 FastAPI + Celery + AI 的轻量级、可扩展接口测试效能平台。

## 它解决什么

微服务架构下，接口测试面临三大核心痛点：

| 痛点 | 现状 | 本平台方案 |
| --- | --- | --- |
| 用例编写耗时 | 单接口覆盖正向/逆向/边界值/异常场景，手动编写重复性极高（10~15 min/接口） | **AI 辅助用例智能生成**：解析 OpenAPI → 结构化 Prompt → LLM 生成 → 人工审核（2~3 min/接口） |
| 变更影响不可控 | 接口定义变更后回归范围凭经验判断，漏测风险高（1~2 h） | **接口变更影响自动圈定**：上传新版 Swagger → Diff → 受影响用例反向检索 → 一键回归（<10 s） |
| 执行阻塞 | Web 服务同步调 pytest，长耗时导致请求超时、服务不可用（同步超时率 30%+） | **异步任务解耦 + 超时兜底**：Celery + Redis 异步队列，Web 响应稳定 <50 ms，任务级超时（默认 300s）强杀 |

> 实现进度：**Phase 1 执行闭环** / **Phase 2 影响分析** / **Phase 3 AI 智能生成** / **Phase 4 生产化 + Vue 前端** 均已交付——Docker Compose 一键跑 + GitHub Actions CI 门禁 + Bearer Token 鉴权 + Vue 3 前端四页（仪表盘/用例/任务/AI 生成）。详见 [docs/roadmap.md](docs/roadmap.md)。

## 架构速览

```
用户 / Vue 前端（frontend/） / CI 探针（/health）
   │ REST /api/v1（Bearer Token 鉴权，Phase 4）
   ▼
【界面层】 双界面：FastAPI Swagger UI（/docs）+ Vue 3 前端（frontend/，dev 走 Vite 代理）
   │ HTTP JSON
   ▼
【Web 服务层】 FastAPI + SQLAlchemy   （用例 CRUD / 任务执行 / Swagger 解析 / 影响分析 / 健康检查）
   │ ① 创建任务记录（status=PENDING）  ② send_task 入队
   ▼
【Redis Broker】（Celery 队列）
   │ ③ Worker 拉取任务
   ▼
【执行引擎层】 Celery Worker（独立进程，--pool=solo）
   ④ subprocess 执行 pytest（动态生成 test_xxx.py / 任务级超时劫持 / 解析 JUnit XML）
   ⑤ 结果回写 DB  ⑥ 生成 HTML 报告链接（/static/{task_id}/report.html）
```

后端三层中 Web 服务 ≠ 执行引擎（进程隔离），状态以 DB 为单一事实源，Worker 重启任务不丢。详见 [docs/architecture.md](docs/architecture.md)。

## 目录结构（两个独立 git 仓库）

```
TestPlatform/                # 容器目录（非 git 仓库）
├── backend/                 # 后端仓库：FastAPI + Celery + AI + 项目文档/规则/配置（本仓库）
└── frontend/                # 前端仓库：Vue 3（Phase 4 已实现，4 页：仪表盘/用例/任务/AI 生成）
```

## 快速开始

> ⚠️ 需要本地 Redis（127.0.0.1:6379）；Windows 下 Worker 必须 `--pool=solo`；面试演示可直接用 Docker Compose 一键起（见下）。

**环境要求**：Python 3.12+、Redis（本地 127.0.0.1:6379）；或装 Docker Desktop 走 `docker compose up`（推荐演示）。

```bash
# 以下命令在 backend/ 仓库内执行；前端为独立仓库 ../frontend

# 1. 安装依赖
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"

# 2. 配置（.env.example 默认指向本地 mock 被测服务，全离线可演示）
copy config\settings.example.yaml config\settings.yaml
copy .env.example .env        # 填入 Redis 密码（占位符换成实际值）

# 3. 启动本地 mock 被测服务（httpbin 兼容子集；另开一个终端，保持运行）
python scripts/mock_target.py    # 监听 127.0.0.1:9999

# 4. 启动后端 Web 服务（Swagger UI 即 MVP 界面：http://127.0.0.1:8000/docs）
uvicorn app.main:app --port 8000

# 5. 启动 Worker（Windows 必须 --pool=solo）
celery -A app.celery_app:celery_app worker --pool=solo

# 6. 一键拉起 Web + Worker（两个独立窗口，各自看日志）
scripts\start_all.bat

# 7. （推荐演示）Docker Compose 一键跑：内置 mock 服务 + FastAPI + Redis + Worker + SQLite
#    Swagger 界面带 Authorize 按钮，token 默认 testplatform-dev-token
docker compose up --build

# 8. （可选）Vue 前端：cd ../frontend && pnpm install && pnpm dev（http://localhost:5173，Vite 代理到后端）
```

> ⚠️ **schema 漂移**：`create_all` 只建新表、不 ALTER 旧表。模型新增列后，已有 `data/platform.db` 会缺列（2026-08-10 实测 `trust_score` → `GET /cases` 500）。处置：运行 `scripts\reset_db.bat` 重置 dev 库（数据可丢，已 gitignore），或手动 `ALTER TABLE` 补列。详见 [docs/database.md §4](docs/database.md)。

## 被测系统接入（两种形态，`execution.base_url` 切换）

| 形态 | 说明 | 适用 |
| --- | --- | --- |
| **离线 mock** | `scripts/mock_target.py`（httpbin 兼容子集：`/get`、`/post`、`/status/{code}`、`/delay/{n}`、`/bearer`）；compose 内置 mock 服务（`http://mock:9999`） | 日常演示——全离线可复现，不依赖外网 |
| **管伊佳ERP**（真实业务系统） | 本机 `:9999/jshERP-boot`；Swagger2 文档经 `scripts/convert_swagger2.py` 转 OpenAPI3 入库（[docs/examples/jsherp-openapi3.json](docs/examples/jsherp-openapi3.json)，320 paths/338 operations） | 真实业务链路演示 |

真实系统需要登录态时，执行层按 `execution.auth_*` 配置自动适配（session 级登录取 token → 注入请求头；凭证只放 `.env` 的 `ERP_TEST_USERNAME/PASSWORD`，生成的测试文件不含密钥）。业务演示用租户账号 `jsh`（`admin` 仅运维，不能编辑业务数据）。详见 [docs/execution-engine.md §8](docs/execution-engine.md)。

## 核心特性

- ✅ **异步解耦 + 超时兜底**（Phase 1 已实现）：请求即返回 `task_id`（202），pytest 由独立 Worker 异步执行；`run_id` Lookup-Create 幂等（SUCCESS 复用、**FAILED 同输入可重试**）；**任务级超时**（默认 300s，`timeout_seconds` 可配）强杀进程树（`scan_stale_tasks` 从 DB 读 pid 权威兜底，执行/生成任务均覆盖），死循环不卡系统。
- 📊 **HTML 报告**（Phase 1 已实现）：任务执行后生成自包含 HTML 报告（字段已转义防 XSS），`report_link` 经 `/static/{task_id}/report.html` 访问（仅挂 `reports/` 目录，测试文件不暴露）。
- 🧠 **接口变更影响圈定**（Phase 2 已实现）：`operation_id` 血缘 + **分段 hash O(1) diff** + **breaking 联合判定** + SQL 反向检索，破坏性变更自动圈定受影响用例、给出孤儿迁移清单，一键回归（宽容降级）。
- ✅ **AI 用例智能生成**（Phase 3 已实现）：自研 OpenAPI 3.0 解析器 + 结构化 Prompt 生成正向/逆向/边界值用例；`llm_client` 唯一封装（结构化输出 + 长度预检 + 成本审计）、三层防幻觉护栏（严格校验 + draft 恒为 + operation_id 服务端注入）、`trust_score` 血缘可信度、`POST /generate` 异步生成 + `POST /impact/{id}/fix-hints` 修复建议。后端 pytest + e2e 全绿（用例数以 CI 为准）。
- 🐳 **容器化部署**（Phase 4 已实现）：多阶段 Dockerfile（非 root）+ `docker compose up` 一键拉起 mock + FastAPI + Redis + Worker + SQLite（named volume 持久化、单写者 Worker）；GitHub Actions 三 job 门禁（ruff + pytest + coverage 60/80 + docker 镜像构建验证）。
- 🔐 **Bearer Token 鉴权**（Phase 4 已实现）：`security.api_token` 配置 + HTTPBearer 统一依赖注入（无凭证 401 / 凭证错误 403），health 免鉴权作探针；Swagger UI 自带 Authorize 按钮；`/docs` 由 `app.docs_enabled` 开关控制（生产可关）。
- 🖥️ **Vue 3 前端**（Phase 4 已实现）：`frontend/` 独立仓库 4 页（仪表盘 / 用例管理 / 任务执行 / AI 生成）+ 41 单测 + 生产构建门禁；dev 走 Vite 代理同源访问。
- 🔍 **可观测与质量基建**（持续加固）：`X-Request-ID` 全链路追踪（Web → Celery → LLM 日志贯穿同一 id）；prompt 层强约束每用例至少 1 条断言；confirm 审核落批准人（reviewer）审计；入 prompt 前敏感信息扫描 + 定界符防护。

## 工程规范（节选）

项目以「面试防守」为标准执行硬性工程规范（完整规则在本地 `.claude/rules/RULES.md`，不入库），核心 10 条红线：

1. 注释只写「为什么」，禁止逐行翻译式注释
2. 禁止裸 `except: pass` / 吞异常
3. 所有外部调用必须设 timeout，值来自 config，禁止硬编码
4. SQLite 连接必须开启 WAL + busy_timeout + foreign_keys
5. LLM 调用必须走唯一封装 `llm_client`，禁止业务代码裸调 SDK
6. LLM 输出必须过 Pydantic schema 校验后才能入库
7. AI 生成的用例状态恒为 `draft`，禁止直接/自动置 `active`
8. 密钥只能放 .env，禁止入库/入日志/入 Prompt/入 git
9. 测试必须 mock LLM，禁止测试真调 API
10. MVP 运行期 `create_all` 建表（Alembic 延后；模型变更后需重置 dev 库）

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | 双界面、后端三层、进程隔离、异步模型、核心决策 |
| [docs/database.md](docs/database.md) | 数据模型（6 表字段级设计） |
| [docs/api.md](docs/api.md) | REST API 设计（端点总表） |
| [docs/execution-engine.md](docs/execution-engine.md) | Celery 任务、subprocess 执行、超时劫持、被测系统接入、HTML 报告 |
| [docs/ai-generation.md](docs/ai-generation.md) | OpenAPI 解析、Prompt 设计、draft→active 审核流 |
| [docs/impact-analysis.md](docs/impact-analysis.md) | 变更影响分析算法、一键回归、fix-hints |
| [docs/configuration.md](docs/configuration.md) | 配置管理（Pydantic Settings，九段 + auth 适配） |
| [docs/roadmap.md](docs/roadmap.md) | 迭代路线（活文档：方向/状态/下一步；完成台账见 TODO.md） |
| [docs/TODO.md](docs/TODO.md) | 分阶段完成项勾选台账 |
| [tests/](tests/) | 单元 / 接口 / 任务测试（pytest 门禁，mock 隔离外部依赖） |
| [frontend/README.md](https://github.com/Testamentt/ApiTestPlatform_Frontend/blob/master/README.md) | Vue 前端使用说明（4 页 / 启动 / 契约） |
