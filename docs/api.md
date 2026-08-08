# REST API 设计（api.md）· Phase 1 简化版

> 规则引用：`RULES.md` §6（错误处理）、§7（接口规范）。所有端点前缀 `/api/v1`。
> **Phase 1 简化（面试导向）**：仅 cases/tasks/health 三类端点；无鉴权、无环境管理、无 parse/generate/impact（Phase 2/3）。

## 1. 通用约定

- **前缀**：`/api/v1`。
- **响应模型**：每个路由显式声明 `response_model`（Pydantic schema，`from_attributes=True`），禁止返回裸 dict/ORM 对象。
- **成功响应**：`{code: 0, message: "ok", data: ...}`；`data` 承载资源或分页结构。
- **错误响应**：`{code, message, detail}`；业务错误由 service 层 `raise AppError`，注册统一 exception handler（兜底 500 不泄漏堆栈）。参数校验保留 FastAPI 默认 422。
- **分页**：列表统一 `page`（默认 1）/`page_size`（默认 20，上限 100），响应 `{items, total, page, page_size}`。
- **状态码语义**：POST 创建 201、DELETE 204、异步任务 202、错误走异常体系（400/404/422/500）。
- **鉴权**：**Phase 1 无鉴权**（面试演示开箱即用）；Phase 4 加 Bearer Token（方案 A，见 roadmap）。
- **日志**：标准 logging，日志带 uuid 前缀串联即可（不引入 request_id 中间件/contextvar，MVP 简化）。

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

> Phase 2+ 端点（generate / webhook / environments）本期不暴露。
> **parse/analyze 为纯规则同步**（`def` 端点线程池，<1s），不引入 Celery 任务；regression 复用 Phase 1 执行引擎。

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
- 执行前校验：所有 case 必须 `status=active`（draft 禁止执行）。
- **Lookup-Create 幂等**：`run_id = sha256(sorted(case_ids) + str(timeout_seconds))` → 查 `tasks.run_id` 已存在 → **直接返回已有 `{task_id}`**（不重复执行）；不存在才创建 `pending` 并派发。
- 行为：创建 `tasks` → `send_task(execute_cases, args=[task_id])` → **202 `{task_id, status: "pending"}`**。

**GET /tasks/{task_id}** — 响应任务全字段：`status/result_summary/report_link/celery_task_id/pid/error_stage/error_msg/started_at/finished_at`。客户端按 2s 间隔轮询直至终态（`pending→running→success/failed`）。

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

## 4. 与 Celery 交互模式（统一异步模式）

1. `POST /tasks`：**先写 DB 记录（pending）→ `send_task` → 立即返回 202**；pytest 在独立 Worker 进程执行，Web 不阻塞。
2. Web 层**不依赖 Celery result backend** 读结果——状态一律以 DB 为准（单一事实源，Worker 重启不丢）。
3. 任务入参只传 `task_id`（Worker 内再从 DB 读上下文）。
4. 重复/并发入队命中 `run_id` 唯一约束**返回已存在任务**（Lookup-Create）。

## 5. 前端使用说明（MVP 零前端）

- **MVP 界面 = Swagger UI（`/docs`）**：所有操作直接在 Swagger UI 完成——创建用例（operation_id 写死如 `httpbin_get`）、确认 active、触发执行（返回 `task_id` 后轮询 `GET /tasks/{id}` 看 `pending→running→success`）、查看 results + HTML 报告链接。
- **Vue（Phase 4 可选）**：`frontend/` 独立仓库，若做仅 2 页（用例列表 + 任务看板），其余继续用 Swagger UI。
- 演示流：`POST /cases`（2 条）→ `POST /cases/{id}/confirm` → `POST /tasks`（202 task_id）→ 轮询 `GET /tasks/{id}` → `GET /tasks/{id}/results` → 打开 `report_link`。
