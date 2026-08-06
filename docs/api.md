# REST API 设计（api.md）

> 规则引用：[`.claude/rules/RULES.md`](../.claude/rules/RULES.md) §6（错误处理）、§7（接口规范）、§10.3（鉴权限流）。所有端点前缀 `/api/v1`。

## 1. 通用约定

- **前缀**：`/api/v1`。
- **响应模型**：每个路由显式声明 `response_model`（Pydantic schema，`from_attributes=True`），禁止返回裸 dict/ORM 对象。
- **成功响应**：`{code: 0, message: "ok", data: ...}`；`data` 承载资源或分页结构。
- **错误响应**：`{code, message, detail}`；业务错误由 service 层 `raise AppError`，注册统一 exception handler（兜底 500 不泄漏堆栈）。参数校验保留 FastAPI 默认 422。
- **分页**：列表统一 `page`（默认 1）/`page_size`（默认 20，上限 100）查询参数，响应 `{items, total, page, page_size}`；禁止无上限全量返回。
- **状态码语义**：POST 创建 201、DELETE 204、异步任务 202、错误走异常体系（400/404/409/422/500/429）。
- **鉴权**（MVP）：全部业务接口要求 `Authorization: Bearer <token>`，token 值在配置中（`security.api_token_env` 指向的 .env/配置，非硬编码在代码，RULES.md §10.3）；**不做 OAuth2、不做限流**（面试导向，见 roadmap）。Swagger UI 可直接在 Authorize 填入调试。
- **CORS**：MVP 同源（Swagger UI）无需 CORS；仅 Phase 4 引入 Vue 时配置白名单 `app.cors_origins`，禁止 `*`（RULES.md §10.5）。
- **请求 ID**：中间件生成 `request_id`（UUID）注入上下文，透传 Celery 任务与 LLM 日志（RULES.md §6.2）。

## 2. 端点总表

| Method | Path | 作用 | 鉴权 | 响应 |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/health` | 健康检查 | 否 | `{status, db, redis}` |
| GET | `/api/v1/cases` | 用例列表（分页 + 过滤） | 是 | 分页用例 |
| POST | `/api/v1/cases` | 创建用例（manual） | 是 | 201 用例 |
| GET | `/api/v1/cases/{case_id}` | 用例详情 | 是 | 用例 |
| PUT | `/api/v1/cases/{case_id}` | 更新用例 | 是 | 用例 |
| DELETE | `/api/v1/cases/{case_id}` | 删除用例（软删除） | 是 | 204 |
| POST | `/api/v1/cases/{case_id}/confirm` | 审核确认 draft→active（需 reviewer） | 是 | `{case_id, status: "active"}` |
| POST | `/api/v1/cases/batch-confirm` | 批量确认 | 是 | `{confirmed: []}` |
| POST | `/api/v1/cases/batch` | 批量创建（AI 结果落地） | 是 | 201 `{created: []}` |
| POST | `/api/v1/parse` | Swagger 解析（同步预览，不落库） | 是 | 解析结果 |
| POST | `/api/v1/parse/import` | 上传/导入 Swagger → 入库为版本快照 | 是 | 201 api_definition |
| GET | `/api/v1/parse/operations` | 查询已入库版本的操作集合 | 是 | `{operation_ids: []}` |
| POST | `/api/v1/generate` | AI 用例生成（异步） | 是 | 202 `{task_id}` |
| POST | `/api/v1/tasks` | 创建执行任务（异步） | 是 | 202 `{task_id}` |
| GET | `/api/v1/tasks` | 任务列表（分页 + 过滤） | 是 | 分页任务 |
| GET | `/api/v1/tasks/{task_id}` | 任务状态/结果摘要 | 是 | 任务全字段 |
| POST | `/api/v1/tasks/{task_id}/cancel` | 取消/强杀任务 | 是 | `{task_id, status: "cancelled"}` |
| GET | `/api/v1/tasks/{task_id}/results` | 任务结果明细 | 是 | `{task, results: []}` |
| POST | `/api/v1/impact/analyze` | 影响分析（异步） | 是 | 202 `{analysis_id}` |
| GET | `/api/v1/impact/analyses/{analysis_id}` | 分析结果 | 是 | `{added_ops, removed_ops, changed_ops, affected_cases}` |
| POST | `/api/v1/impact/{analysis_id}/regression` | 一键回归（受影响用例建任务） | 是 | 202 `{task_id}` |
| POST | `/api/v1/webhook/git` | Git Webhook 接收（GitHub/GitLab） | 是 | 202 `{message}` |
| GET | `/api/v1/environments` | 环境列表 | 是 | `[]` |
| POST | `/api/v1/environments` | 创建环境 | 是 | 201 环境 |
| GET | `/api/v1/reports/allure/{task_id}` | Allure 报告 | 是 | 302 → `/static/allure/{task_id}/index.html` |

## 3. 分组详述

### 3.1 用例管理 `/api/v1/cases`

**GET /cases** — 查询参数：`page`、`page_size`、`status`（draft/active/archived）、`method`、`operation_id`、`keyword`（模糊匹配 name/path）、`source`。响应分页用例。

**POST /cases** — 请求体：
```json
{
  "name": "创建用户-正向",
  "method": "POST",
  "path": "/users",
  "operation_id": "createUser",
  "request_schema": {"params": {}, "query": {}, "headers": {}, "body": {"username": "alice", "email": "a@b.com"}},
  "expected_status": 201,
  "assertions": [{"type": "field", "path": "id", "expected": null}],
  "tags": ["P0", "user-management"],
  "env_id": 1
}
```
约束：`method` 白名单校验；`status` 默认 `draft`（AI 来源强制 draft，见 3.3）；`assertions.type` ∈ status/field/business。

**POST /cases/{case_id}/confirm** — 请求体 `{reviewer: "alice"}`。仅 `draft→active`；转 active 时**重新执行 schema 校验**（RULES.md §11.2）；已 active 幂等返回。**禁止任何自动化路径直接置 active**。

**POST /cases/batch** — 请求体 `{source: "ai", cases: [...]}`。批量创建，逐条校验，失败条目返回原因（不整体回滚）。AI 来源强制 `status=draft`。

### 3.2 Swagger 解析 `/api/v1/parse`

**POST /parse** — 同步预览（本地解析 <1s，不落库）。请求体：
```json
{"content": "<OpenAPI 3.0 JSON/YAML 文本>", "url": null, "version": "v1.0"}
```
`content` 与 `url` 二选一。安全：大小 ≤2MB、类型 json/yaml；若支持 URL 拉取，校验 scheme 仅 http/https、目标不在内网保留段、限重定向与响应大小（防 SSRF，RULES.md §10.3）。响应：`{openapi, server_base_url, operations: [...]}`（结构见 [ai-generation.md](ai-generation.md) §2）。

**POST /parse/import** — 解析 + 入库为 `api_definitions` 新版本（生成 `operation_ids` + `operation_hashes`），供影响分析复用。201 返回 `{definition_id, version, operation_count}`。

### 3.3 AI 用例生成 `/api/v1/generate`

**POST /generate** — 请求体：
```json
{
  "definition_id": 3,
  "operation_ids": ["createUser", "listUsers"],
  "env_id": 1
}
```
行为：创建 `tasks(task_type=generate_cases)` → `send_task` → **202 `{task_id}`**。客户端轮询 `GET /tasks/{task_id}`。任务完成：`result_summary = {generated, draft_created, rejected, prompt_version}`；用例以 `draft` 落库，供人工 `confirm`。

**防幻觉护栏**（RULES.md §11.2）：生成结果恒为 `draft`；LLM 输出严格 Pydantic 校验，schema 之外多余字段判失败；校验失败的记录同样落库（status=failed + 原因 + prompt_version + model + 原始响应）。

### 3.4 任务管理 `/api/v1/tasks`

**POST /tasks** — 请求体：`{case_ids: [1,2,3] 或 case_id: 1, env_id: 1, timeout_seconds?: 300}`。
- 执行前校验：所有 case 必须 `status=active` 且未软删除（draft 禁止执行）。
- 行为：创建 `tasks(task_type=execute_cases, case_ids 快照)` → `send_task(execute_cases, args=[task_id])` → **202 `{task_id, status: "pending"}`**。

**GET /tasks/{task_id}** — 响应任务全字段：`status/result_summary/celery_task_id/pid/error_stage/error_msg/allure_link/started_at/finished_at`。轮询模式：客户端按 2s 间隔轮询直至终态。

**POST /tasks/{task_id}/cancel** — 触发取消：置 `status=cancelled` 并强杀当前 subprocess（复用超时劫持 kill 逻辑）。

**GET /tasks/{task_id}/results** — 响应 `{task: {...}, results: [{case_id, status, duration_ms, failure_msg, assertions_checked, assertions_passed, allure_uuid}]}`。

### 3.5 影响分析 `/api/v1/impact`

**POST /impact/analyze** — 请求体：`{new_content 或 new_definition_id, old_version?: "auto"|"v1.0"}`。`old_version="auto"` 取最近一个非空版本。行为：创建 `impact_analyses(pending)` → `send_task(impact_analyze)` → **202 `{analysis_id}`**。

**GET /impact/analyses/{analysis_id}** — 响应 `{added_ops, removed_ops, changed_ops, affected_cases: [{case_id, name, path, method, status, priority}], affected_summary}`。

**POST /impact/{analysis_id}/regression** — 用 `affected_case_ids` 快照创建 `tasks(trigger=regression)` → **202 `{task_id}`**。

### 3.6 Git Webhook `/api/v1/webhook/git`

**POST /webhook/git** — 校验 `X-GitHub-Event` / `X-Gitlab-Event` 头（RULES.md §10.3 建议加签名校验）。请求体携带 payload，提取 `head_commit` → 拉取新 Swagger 文件（或请求体携带）→ 解析入库新版本 → 创建 `impact_analyses` → `send_task` → **202 `{message: "analysis queued", analysis_id}`**。`commit_sha` 去重（同 sha 已入库则直接返回已有分析）。

### 3.7 环境 `/api/v1/environments`

**GET /environments** — `[{id, name, base_url, timeout_seconds, is_active}]`（global_headers 不回传）。
**POST /environments** — `{name, base_url, global_headers?, timeout_seconds?}`。

### 3.8 健康检查 `/api/v1/health`

`{status: "ok", db: "up"|"down", redis: "up"|"down"}`。不做鉴权（供部署探针）。

## 4. 与 Celery 交互模式（统一异步模式）

1. 所有耗时操作（execute / generate / impact analyze）走同一模式：**先写 DB 记录（status=pending）→ `send_task` → 立即返回 202**。
2. Web 层**不依赖 Celery result backend** 读结果——状态一律以 DB 为准（单一事实源，Worker 重启不丢、FastAPI 挂不影响 Worker）。
3. 任务入参只传 ID（`task_id` / `analysis_id`），禁止传大对象（RULES.md §8.4）。
4. 重复/并发入队命中 `run_id` 唯一约束直接复用已存在任务（RULES.md §8.3）。

## 5. 前端使用说明（MVP 零前端）

- **MVP 界面 = Swagger UI（`/docs`）**：所有操作直接在 Swagger UI 完成——创建用例、触发执行（返回 `task_id` 后轮询 `GET /tasks/{id}` 看 `pending→running→pass/fail`）、draft 审核确认、影响分析、Allure 报告链接。开发环境开放；生产环境 `/docs` 关闭或鉴权保护（RULES.md §10.5）。
- **Vue（Phase 4 可选）**：`frontend/` 独立仓库，若做仅 2 页（用例列表 + 任务看板），其余继续用 Swagger UI（见 [architecture.md](architecture.md) §7）。
- 用例确认流：`POST /parse`（预览）→ `POST /parse/import`（入库）→ `POST /generate`（异步生成）→ `GET /tasks/{id}`（轮询）→ `GET /cases?status=draft`（审阅）→ `POST /cases/{id}/confirm`（确认）→ `POST /tasks`（执行）→ `GET /tasks/{id}/results`。
