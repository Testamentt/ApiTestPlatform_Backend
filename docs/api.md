# REST API 设计（api.md）· Phase 1-4 实现版

> 规则引用：`RULES.md` §6（错误处理）、§7（接口规范）。所有端点前缀 `/api/v1`。
> **Phase 1-4 已实现（面试导向）**：cases/tasks/health + parse/impact/regression/fix-hints + generate；Phase 4 补 Bearer Token 鉴权（health 免鉴权作探针），无环境管理（后续）。

## 1. 通用约定

- **前缀**：`/api/v1`。
- **响应模型**：每个路由显式声明 `response_model`（Pydantic schema，`from_attributes=True`），禁止返回裸 dict/ORM 对象。
- **成功响应**：`{code: 0, message: "ok", data: ...}`；`data` 承载资源或分页结构。
- **错误响应**：`{code, message, detail}`；业务错误由 service 层 `raise AppError`，注册统一 exception handler（兜底 500 不泄漏堆栈）。参数校验保留 FastAPI 默认 422。
- **分页**：列表统一 `page`（默认 1）/`page_size`（默认 20，上限 100），响应 `{items, total, page, page_size}`。
- **状态码语义**：POST 创建 201、DELETE 204、异步任务 202、错误走异常体系（400/404/409/422/502/503；LLM/subprocess 失败 502、入队失败 503 `DISPATCH_FAILED`）。
- **鉴权（Phase 4）**：Bearer Token——除 `health` 外全部端点需 `Authorization: Bearer <token>`（值走配置 `security.api_token`，dev 默认仅供演示）。无凭证 → `401 {code: "AUTH_REQUIRED"}`；凭证错误 → `403 {code: "AUTH_INVALID"}`（§10.3）。Swagger UI 自带 Authorize 按钮。CORS 白名单走 `frontend.cors_origins`（默认空；前端走 Vite 同源代理，不依赖 CORS，§10.5）。
- **日志**：标准 logging 统一格式；**request_id 全链路追踪（批次 C 已实现，§6.2）**——中间件读/生成 `X-Request-ID`（UUID）注入 ContextVar + 响应头回写，经任务参数透传 Celery 任务与 LLM 调用日志，`[request_id]` 字段贯穿全链路。

## 2. 端点总表（Phase 1）

| Method | Path | 作用 | 响应 |
| --- | --- | --- | --- |
| GET | `/api/v1/health` | 健康检查 | `{status, db, redis}` |
| GET | `/api/v1/cases` | 用例列表（分页 + 过滤） | 分页用例 |
| POST | `/api/v1/cases` | 创建用例（operation_id 必填） | 201 用例 |
| GET | `/api/v1/cases/{case_id}` | 用例详情 | 用例 |
| PUT | `/api/v1/cases/{case_id}` | 更新用例 | 用例 |
| DELETE | `/api/v1/cases/{case_id}` | 删除用例（物理删除） | 204 |
| POST | `/api/v1/cases/{case_id}/confirm` | 审核确认 draft→active（需 reviewer） | `{case_id, status: "active"}` |
| POST | `/api/v1/tasks` | 创建执行任务（异步，Lookup-Create 幂等） | 202 `{task_id}` |
| GET | `/api/v1/tasks` | 任务列表（分页 + 过滤） | 分页任务 |
| GET | `/api/v1/tasks/{task_id}` | 任务状态/结果摘要 | 任务全字段 |
| GET | `/api/v1/tasks/{task_id}/results` | 任务结果（读 result_summary.results） | `{task, results: []}` |
| POST | `/api/v1/parse` | 解析 Swagger/OpenAPI 3.x 入库（版本快照，**reparse 覆盖**） | 201 `{version_id, version, ...}` |
| POST | `/api/v1/impact/analyze` | 新版 Swagger vs 最近版本 diff → 影响结果（breaking/orphaned/untested） | 200 影响分析 |
| POST | `/api/v1/impact/{analysis_id}/regression` | 一键回归受影响用例（**宽容降级**） | 202 `{task_id}` |
| POST | `/api/v1/impact/{analysis_id}/fix-hints` | **按需生成 breaking 变更修复建议**（轻量 LLM，复用 llm_client） | 200 `{ai_fix_hint}` |
| POST | `/api/v1/generate` | AI 生成测试用例（异步；定向/全量/智能三种触发） | 202 `{task_id}` |
| GET | `/api/v1/generate/{generation_task_id}` | 生成任务状态/结果摘要 | 任务全字段 |

> Phase 4+ 端点（webhook / environments）本期不暴露。
> **parse/analyze 为纯规则同步**（`def` 端点线程池，<1s），不引入 Celery 任务；regression 复用 Phase 1 执行引擎；**generate 走 Celery 异步**（LLM 调用不得阻塞 Web 线程，RULES §9.5）。

## 3. 分组详述

### 3.1 用例管理 `/api/v1/cases`

**GET /cases** — 查询参数：`page`、`page_size`、`status`（draft/active/archived）、`method`、`operation_id`、`keyword`（模糊匹配 name/path）。响应分页用例。

**POST /cases** — 请求体：
```json
{
  "name": "httpbin-get",
  "method": "GET",
  "path": "/get",
  "operation_id": "httpbin_get",
  "params": {},
  "body": null,
  "expected_status": 200,
  "assertions": null
}
```
约束：`method` 白名单校验；`operation_id` **必填**（min_length=1，Phase 2 血缘映射）；`status` 默认 `draft`；MVP 只校验 `expected_status`，`assertions` 留空。

**POST /cases/{case_id}/confirm** — 请求体 `{reviewer: "alice"}`。仅 `draft→active`；已 active 幂等返回。**禁止任何自动化路径直接置 active**（防幻觉护栏）。

### 3.2 任务管理 `/api/v1/tasks`

**POST /tasks** — 请求体：`{case_ids: [1,2], timeout_seconds?: 300}`。
- 执行前校验：所有 case 必须 `status=active`（draft 禁止执行）；`case_ids` 上限 500；`timeout_seconds` 1–3600。
- **Lookup-Create 幂等（review H4 修订）**：`run_id = sha256(sorted(case_ids) + str(timeout_seconds))` → 查 `tasks.run_id`——已 **SUCCESS** 直接复用（不重复执行）；已 **FAILED** 重置 PENDING 并重新入队（同输入可重试）；PENDING/RUNNING 返回现状；不存在才创建 `pending` 并派发。
- **入队失败兜底（review M2）**：Celery/Redis 不可用时任务落 `failed(error_stage="dispatch")` 并返回 `503 {code: "DISPATCH_FAILED"}`，不残留 PENDING 孤儿。
- **timeout_seconds 真实生效（review M3/B4）**：subprocess 超时与僵尸扫描阈值均取任务级值（缺省 `execution.pytest_timeout`=300）。
- 行为：创建 `tasks` → `send_task(execute_cases, args=[task_id])` → **202 `{task_id, status: "pending"}`**。

**GET /tasks/{task_id}** — 响应任务全字段：`status/result_summary/report_link/celery_task_id/pid/error_stage/error_msg/timeout_seconds/started_at/finished_at`。客户端按 2s 间隔轮询直至终态（`pending→running→success/failed`）。

**GET /tasks/{task_id}/results** — 响应 `{task: {...}, results: [...]}`，`results` 从 `task.result_summary.results` 读取（Phase 1 无独立结果表）。

### 3.3 健康检查 `/api/v1/health`

`{status: "ok", db: "up"|"down", redis: "up"|"down"}`。不做鉴权（供部署探针）。

### 3.4 解析与影响分析（Phase 2，纯规则同步）

**POST /parse** — 请求体：`{document: {...OpenAPI 3.x}, version?: "v1"}`（version 缺省 auto `v{n}`）。
- 解析提取 operation_ids + **分段 hashes** + **contracts** → 落库 `api_definitions`。
- **reparse 覆盖**：同 version 再解析先删旧插新（CI 幂等不膨胀）。
- 大小 > `swagger.max_upload_bytes` → 413/422；非法文档（非 3.x / 无 paths）→ 422。
- 响应 201：`{version_id, version, hash_version, operation_count, operation_ids, warnings}`——warnings 含 $ref 找不到 / 跨文件引用 / **operation_id 重复**（F4）。

**POST /impact/analyze** — 请求体：`{document: {...新版}, new_version?, old_version?}`（old_version 缺省取最近版本）。
- 解析新版 → 落库新版本 → 与旧版本 diff + **breaking 联合判定** → SQL 反向检索 → 落库 `impact_analyses` → 200。
- 响应：`{analysis_id, old_version, new_version, added_ops, removed_ops, changed_ops, breaking_changed_ops, affected_cases, affected_summary, orphaned_case_ids, suggested_remap, untested_ops, warnings}`。
- 首次分析（无旧版本）：added=untested=全部、无 affected，不报错。
- 相同文档重复 analyze → `changed_ops` 空（identical）。

**POST /impact/{analysis_id}/regression** — 一键回归。
- 读 `affected_case_ids` 快照 → **宽容过滤**只执行当前仍 active 的 → 复用 `POST /tasks` 的 Lookup-Create 幂等 → 202。
- 响应：`{analysis_id, task_id, task_status, executed_case_ids, executed_count, dropped_case_ids, dropped_count, dropped_reasons, affected_summary}`——summary 为**执行时真实口径** `{total: executed+dropped, executed, dropped}`（D6）；dropped 附 reason（case deleted / case draft）。
- 无受影响用例 → 422；受影响用例全部失效 → 422。

### 3.5 AI 用例生成（Phase 3，Celery 异步）

**POST /generate** — 请求体：`{document: {...OpenAPI 3.x}, operation_ids?: [...] , force_full?: false}` → **202** `{task_id, status}`。
- **三种触发（优先级）**：① `operation_ids` 显式定向；② `force_full=True` 全量重建；③ 都缺省 → 读最新影响分析 `untested_ops`（无历史分析 → 全量开箱即用；已全覆盖 → `422 NO_UNTESTED_OPS`）。
- **Lookup-Create 幂等（review H4 修订）**：`run_id = sha256(document + operation_ids)`——已 **SUCCESS** 复用（不重复调 LLM）；已 **FAILED** 重置 PENDING 重新入队（同输入可重试）；PENDING/RUNNING 返回现状。入队失败 → `503 DISPATCH_FAILED` + 任务落 `failed(error_stage="dispatch")`。
- 大小 > `swagger.max_upload_bytes` → 422；畸形文档（缺 paths）→ 任务 `failed(error_stage="parse")`，入口拦截不调 LLM。
- 结果 `result_summary`：`{generated, draft_created, rejected, rejected_detail:[{operation_id, reason}], skipped_by_filter, skipped_detail, prompt_version, cost_total}`。

**GET /generate/{id}** — 轮询 `{status: pending→running→success/failed, result_summary, error_msg}`。

**POST /impact/{analysis_id}/fix-hints** — 对 breaking 变更调 LLM 生成一句话修复建议，更新 `ai_fix_hint`（best-effort，失败置 NULL 不阻塞）；`AnalyzeResult` 返回 `has_fix_hint` + `fix_hint_endpoint` 提示入口。

- 生成用例经 `GET /cases?status=draft` 查看（source=ai、trust_score=80/60）；confirm 复用 `POST /cases/{id}/confirm` 转 active 后才可执行。

## 4. 与 Celery 交互模式（统一异步模式）

1. `POST /tasks`：**先写 DB 记录（pending）→ `send_task` → 立即返回 202**；pytest 在独立 Worker 进程执行，Web 不阻塞。
2. Web 层**不依赖 Celery result backend** 读结果——状态一律以 DB 为准（单一事实源，Worker 重启不丢）。
3. 任务入参只传 `task_id`（Worker 内再从 DB 读上下文）。
4. 重复/并发入队命中 `run_id` 唯一约束**返回已存在任务**（Lookup-Create）。

## 5. 前端使用说明（双界面）

- **后端界面 = Swagger UI（`/docs`）**：所有操作直接在 Swagger UI 完成——先点右上角 **Authorize** 输入 Bearer Token（Phase 4 鉴权），再创建用例（operation_id 写死如 `httpbin_get`）、确认 active、触发执行（返回 `task_id` 后轮询 `GET /tasks/{id}` 看 `pending→running→success`）、查看 results + HTML 报告链接。
- **Vue 前端（Phase 4 已实现）**：`frontend/` 独立仓库（Vue 3 + TS + Element Plus），4 页——仪表盘 / 用例管理 / 任务执行 / AI 生成；dev 经 Vite 代理（`/api`、`/static`、`/docs`）同源访问后端；令牌在顶栏「令牌」或仪表盘空态配置（`testplatform-dev-token`）。详见 [frontend/README.md](../../frontend/README.md)。
- 演示流：`POST /cases`（2 条）→ `POST /cases/{id}/confirm` → `POST /tasks`（202 task_id）→ 轮询 `GET /tasks/{id}` → `GET /tasks/{id}/results` → 打开 `report_link`（`/static/{task_id}/report.html`）。
