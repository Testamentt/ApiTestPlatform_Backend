<!-- 本项目编码格式：UTF-8（无 BOM）。 -->
# REST API 设计（api.md）

> 定位：对外接口契约，包括端点、状态码、请求与响应字段、错误码和鉴权语义。
> 事实源：`app/api/v1/*.py`、`app/schemas/*.py`、`app/core/exceptions.py`、`app/services/executors/` ｜ 最后核对：2026-11-12

鉴权在路由层统一挂依赖（见 [architecture.md](architecture.md) §2），任务状态机见 [database.md](database.md) §3.2。

## 1. 通用约定

- 前缀：`/api/v1`。
- 响应模型：每个路由显式声明 `response_model`（Pydantic schema，`from_attributes=True`），禁止返回裸 dict 或 ORM 对象。
- 成功响应：`{code: 0, message: "ok", data: ...}`，`data` 承载资源或分页结构。
- 错误响应：`{code, message, detail}`。业务错误由 service 层 `raise AppError`，注册统一 exception handler，兜底 500 不泄漏堆栈。入参校验 422 也已归一（见 §1.1），前端只需认一种结构。
- 分页：列表统一 `page`（默认 1）/`page_size`（默认 20，上限 100），响应 `{items, total, page, page_size}`。
- 状态码语义：POST 创建 201、DELETE 204、异步任务 202，错误走异常体系（400/404/409/422/429/502/503/504）。LLM 与 subprocess 失败返回 502；Jenkins 相关错误码按 502/503/504 分级（多数由 Worker 落库，不直接回给 HTTP 调用方）；入队失败返回 503 `DISPATCH_FAILED`，限流返回 429 `RATE_LIMITED`。
- 鉴权：Bearer Token。除 `health` 与 Swagger 文档路由（`/docs`、`/redoc`、`/openapi.json`、`/`）外，全部业务端点需 `Authorization: Bearer <token>`。凭证接受两种形态：配置里的静态 `security.api_token`（dev 默认值仅供本地演示），或启用 `security.jwt_secret` 后的 HS256 JWT（要求带 `exp`，可选校验 `iss`/`aud`）。无凭证返回 401 `AUTH_REQUIRED`，凭证无效返回 403 `AUTH_INVALID`。Swagger UI 自带 Authorize 按钮。CORS 白名单走 `frontend.cors_origins`，默认空表示不挂中间件；前端走 Vite 同源代理，不依赖 CORS。
- 限流（可选）：`security.rate_limit_per_minute > 0` 时按「令牌哈希（无令牌则客户端 IP）+ 固定窗口」计数，超限返回 429 `RATE_LIMITED`；默认关闭。Redis 不可用时放行并告警，保护性能力不升级为全站故障。
- 日志：标准 logging 统一格式。request_id 全链路追踪由中间件读取或生成 `X-Request-ID`（UUID），注入 ContextVar 并回写响应头，经任务参数透传到 Celery 任务与 LLM 调用日志，Web 与 Worker 两侧日志的 `[request_id]` 字段贯穿同一链路。

### 1.1 业务错误码表

所有错误（含 FastAPI 入参校验 422）统一为 `{code, message, detail}`。`detail` 对校验类错误是逐字段结构 `[{field, message, type}]`，不回流用户输入原文。

| code | HTTP | 触发场景 | 来源 |
| :--- | :--- | :--- | :--- |
| `AUTH_REQUIRED` / `AUTH_INVALID` | 401 / 403 | 缺凭证 / 凭证错误 | `app/api/v1/deps.py` |
| `NOT_FOUND` | 404 | 资源不存在（用例/任务/分析/生成任务） | `app/repositories/base.py` |
| `REPORT_NOT_FOUND` | 404 | 任务存在但报告未生成（未成功或报告写盘失败） | `app/services/task_service.py` |
| `CASES_NOT_ACTIVE` | 422 | `POST /tasks` 含不存在或非 active 用例 | `app/services/task_service.py` |
| `TASK_CONFLICT` / `GENERATION_CONFLICT` | 409 | 并发创建撞唯一约束且查不到赢家（极端路径） | `app/services/task_service.py` / `generation_service.py` |
| `TASK_NOT_CANCELLABLE` | 409 | 取消非 `pending`/`running` 任务，或状态在取消瞬间已变化（含已跑完） | `app/services/task_service.py` |
| `TASK_NOT_RETRYABLE` | 409 | 重试非 failed 任务 | `app/services/task_service.py` |
| `RATE_LIMITED` | 429 | 超过 `security.rate_limit_per_minute` 限额（默认关闭） | `app/api/v1/deps.py` |
| `REQUEST_VALIDATION_ERROR` | 422 | 入参校验失败（归一后的框架 422），`detail` 为逐字段错误 | `app/main.py` |
| `INTERNAL_VALIDATION_ERROR` | 422 | 服务层内部构造的数据未过校验（兜底；典型触发点已改为显式业务码） | `app/main.py` |
| `TOO_MANY_CASES` | 422 | 一键回归受影响用例超过单任务上限 `MAX_CASE_IDS` | `app/services/impact_service.py` |
| `TASK_CANCELLED` | 409 | 执行启动瞬间发现任务已被取消（内部信号，不直接返回给客户端） | `app/services/execution_service.py` |
| `DISPATCH_FAILED` | 503 | Celery/Redis 不可用，入队失败（任务落 `failed(dispatch)`） | `app/services/dispatcher.py` |
| `CASE_INVALID` | 422 | 更新后合并结果未通过 schema 校验（形状闸） | `app/services/case_service.py` |
| `CASE_INVALID_FOR_ACTIVE` | 422 | 激活前形状重校验未通过 | `app/services/case_service.py` |
| `CASE_CONTRACT_MISMATCH` | 422 | 激活前语义校验未通过（method/path/params 与接口契约不符） | `app/services/case_service.py` |
| `INVALID_CONFIRM` | 409 | 非 draft 用例执行 confirm | `app/services/case_service.py` |
| `SWAGGER_TOO_LARGE` | 422 | 文档超过 `swagger.max_upload_bytes` | `app/services/swagger_service.py` / `impact_service.py` / `generation_service.py` |
| `INVALID_SWAGGER` | 422 | 非 OpenAPI 3.x / 缺 paths / 无有效 operation | `app/utils/openapi_parser.py` |
| `VERSION_COLLISION` | 409 | 新版本号与对比基线相同（会覆盖历史快照） | `app/services/impact_service.py` |
| `NO_BREAKING_CHANGES` / `NO_AFFECTED_CASES` / `NO_ACTIVE_CASES` | 422 | fix-hints 无 breaking / 回归无受影响用例 / 受影响用例全部失效 | `app/services/impact_service.py` |
| `NO_UNTESTED_OPS` | 422 | 智能决策下所有接口已有 active 用例 | `app/services/generation_service.py` |
| `INVALID_OPERATION_IDS` | 422 | 定向 `operation_ids` 全部不在文档中（message 带合法示例） | `app/services/generation_service.py` |
| `LLM_INPUT_TOO_LONG` | 422 | 输入超 `llm.max_input_chars` | `app/utils/llm_client.py` |
| `LLM_FAILED` / `LLM_VALIDATION_FAILED` | 502 | 上游失败（重试耗尽）/ 输出未过 schema | `app/utils/llm_client.py` |
| `LLM_DEADLINE_EXCEEDED` | 504 | 任务级时间预算耗尽：不再发起调用、也不再退避重试 | `app/utils/llm_client.py` |
| `SUBPROCESS_TIMEOUT` / `SUBPROCESS_FAILED` | 502 | pytest 超时被杀 / 非零退出 | `app/utils/subprocess_util.py` |
| `COMMAND_NOT_ALLOWED` / `INVALID_ARGS` | 400 | 命令不在白名单 / 参数非法 | `app/utils/subprocess_util.py` |
| `JUNIT_PARSE_FAILED` | 502 | report.xml 缺失或属性畸形 | `app/utils/junit_parser.py` |
| `PYTEST_EXIT_CODE` | 502 | pytest 退出码非 0/1（2 中断 / 3 内部 / 4 usage / 5 收集失败），任务落 `error_stage=subprocess`（执行器内部信号，不直接返回客户端） | `app/services/executors/local.py` |
| `EXECUTOR_NOT_SUPPORTED` | 500 | `execution.executor` 不是 `local`/`jenkins`（配置写错），任务由外层兜底落 `error_stage=internal` | `app/services/executors/__init__.py` |
| `JENKINS_NOT_CONFIGURED` / `JENKINS_UNAVAILABLE` / `JENKINS_HTTP_ERROR` | 503 / 502 / 502 | Jenkins 未配置 / 网络不可达 / 返回 4xx-5xx，任务落 `error_stage=jenkins`（内部信号） | `app/utils/jenkins_client.py`、`app/services/executors/jenkins.py` |
| `JENKINS_TRIGGER_FAILED` / `JENKINS_BUILD_CANCELLED` / `JENKINS_NO_REPORT` | 502 / 409 / 502 | 触发响应缺 `Location` / 队列中取消 / 构建未产出 JUnit 制品，任务落 `error_stage=jenkins`（内部信号） | 同上 |
| `JENKINS_TIMEOUT` | 504 | 排队或构建等待超过任务 `timeout_seconds`，或单请求 connect/read 超时，任务落 `error_stage=timeout`（内部信号） | `app/utils/jenkins_client.py` |
| `JENKINS_REPORT_INVALID` | 502 | 制品存在但不是合法 JUnit XML，任务落 `error_stage=parse`（内部信号） | `app/services/executors/jenkins.py` |
| `INTERNAL_ERROR` | 500 | 未捕获异常的统一兜底，不返回堆栈 | `app/main.py` |

## 2. 端点总表

| Method | Path | 作用 | 响应 |
| :--- | :--- | :--- | :--- |
| GET | `/api/v1/health` | 健康检查（免鉴权） | `{status, db, redis}` |
| GET | `/api/v1/cases` | 用例列表（分页 + `status`/`method`/`operation_id`/`keyword` 过滤） | 分页用例 |
| POST | `/api/v1/cases` | 创建用例（operation_id 必填） | 201 用例 |
| GET | `/api/v1/cases/{case_id}` | 用例详情 | 用例 |
| PUT | `/api/v1/cases/{case_id}` | 更新用例（形状闸 + 语义闸，见 §3.1） | 用例 |
| DELETE | `/api/v1/cases/{case_id}` | 删除用例（物理删除） | 204 |
| POST | `/api/v1/cases/{case_id}/confirm` | 审核确认 draft→active（需 reviewer） | `{case_id, status: "active"}` |
| POST | `/api/v1/tasks` | 创建执行任务（异步，Lookup-Create 幂等） | 202 `{id, run_id, status, ...}` |
| GET | `/api/v1/tasks` | 任务列表（分页 + `status` 过滤） | 分页任务 |
| GET | `/api/v1/tasks/{task_id}` | 任务状态/结果摘要 | 任务全字段 |
| GET | `/api/v1/tasks/{task_id}/results` | 任务结果（读 result_summary.results） | `{task, results: []}` |
| GET | `/api/v1/tasks/{task_id}/report` | 任务 HTML 报告原文（带鉴权） | `text/html` |
| POST | `/api/v1/tasks/{task_id}/cancel` | 取消 `pending` 或 `running` 任务（local 执行器下 running 会先杀 pytest 进程树） | 任务（`failed/cancelled`） |
| POST | `/api/v1/tasks/{task_id}/retry` | 人工重试 `failed` 任务 | 202 任务（重置 `pending`） |
| POST | `/api/v1/parse` | 解析 Swagger/OpenAPI 3.x 入库（版本快照，reparse 覆盖） | 201 `{version_id, version, ...}` |
| POST | `/api/v1/impact/analyze` | 新版 Swagger 与最近版本 diff，产出影响结果（breaking/orphaned/untested） | 200 影响分析 |
| POST | `/api/v1/impact/{analysis_id}/regression` | 一键回归受影响用例（宽容降级） | 202 `{task_id, ...}` |
| POST | `/api/v1/impact/{analysis_id}/fix-hints` | 按需生成 breaking 变更修复建议（轻量 LLM，复用 llm_client） | 200 `{ai_fix_hint}` |
| POST | `/api/v1/generate` | AI 生成用例（异步；定向/全量/智能三种触发） | 202 `{id, run_id, status, ...}` |
| GET | `/api/v1/generate/{generation_task_id}` | 生成任务状态/结果摘要 | 任务全字段 |

> 异步端点统一返回 `ApiResponse[<TaskRead>]`，任务标识字段名为 `data.id` 而不是 `task_id`；`task_id` 仅出现在 `RegressionResult` 里，表示本次回归创建的执行任务 id。
> 尚未提供的端点：webhook 回调与 environments 多环境。前者只在需要 Jenkins 主动推送结果时才需要（当前走轮询回读，平台不必对外开放入站端口）；后者依赖多环境数据模型，目前 `execution.base_url` 是单值、切换靠 env 覆盖。
> parse/analyze 为纯规则同步（`def` 端点线程池，耗时 <1s），不引入 Celery 任务；regression 复用执行闭环执行引擎；generate 走 Celery 异步，LLM 调用不得阻塞 Web 线程。

## 3. 分组详述

### 3.1 用例管理 `/api/v1/cases`

**GET /cases**：查询参数 `page`、`page_size`、`status`（draft/active/archived）、`method`、`operation_id`、`keyword`（模糊匹配 name/path），响应为分页用例。

**POST /cases**：请求体：

```json
{
  "name": "httpbin-get",
  "method": "GET",
  "path": "/get",
  "operation_id": "httpbin_get",
  "params": {},
  "body": null,
  "expected_status": 200,
  "assertions": [
    {"path": "status_code", "op": "eq", "value": 200},
    {"path": "data.id", "op": "eq", "value": 1}
  ]
}
```

约束：`method` 白名单校验；`operation_id` 必填（min_length=1，血缘映射）；`status` 默认 `draft`；`assertions` 逐条过 `AssertionItem` 校验（`path` 仅允许 `status_code` 或 `a.b.0.c` 形式的点路径白名单正则、`op` ∈ eq/ne/contains/exists、`extra="forbid"`），非法即 422。校验通过后由执行引擎渲染进生成的 pytest 文件逐条求值。

**POST /cases/{case_id}/confirm**：请求体 `{reviewer: "alice"}`。仅 `draft→active`，已 active 时幂等返回。禁止任何自动化路径直接置 active（防幻觉护栏）。

激活前过两把尺子：形状校验（`CaseCreate`）保证结构合法，语义校验用 `api_definitions` 的契约快照核对 method/path/params/body 是否属于该 `operation_id`。语义校验只拒绝 error 级违规（能明确判定打错接口的情形）；库里没有快照、快照缺 method/path、或 `operation_id` 不在最新快照内时一律放行——校验的目的是挡住确证的错配，不是让历史数据或手工用例无法激活。

### 3.2 任务管理 `/api/v1/tasks`

**POST /tasks**：请求体 `{case_ids: [1,2], timeout_seconds?: 300}`。

- 执行前校验：所有 case 必须 `status=active`（draft 禁止执行）；`case_ids` 上限 500；`timeout_seconds` 取 1–3600。
- Lookup-Create 幂等：`run_id = sha256(json.dumps({case_ids: sorted(...), timeout: timeout_seconds}, sort_keys=True))`（`app/services/task_service.py:compute_run_id`），再查 `tasks.run_id`。已 SUCCESS 直接复用（不重复执行）；已 FAILED 重置 PENDING 并重新入队（同输入可重试）；PENDING/RUNNING 返回现状；不存在才创建 `pending` 并派发。并发撞唯一约束时查回既有任务返回，不再 500。
- 入队失败兜底：Celery/Redis 不可用时任务落 `failed(error_stage="dispatch")` 并返回 503 `DISPATCH_FAILED`，不残留 PENDING 孤儿。
- timeout_seconds 真实生效：subprocess `communicate(timeout)` 与僵尸扫描阈值均取任务级值（缺省 `execution.pytest_timeout`=300）；`executor=jenkins` 时同一值作为「排队 + 构建」整条链的 deadline。同时按该值派生该任务的 Celery `soft_time_limit`（+30s）/`time_limit`（+60s），并向 `visibility_timeout` 收敛。
- 行为：创建 `tasks`，入队 `execute_cases`（任务入参只传 `task_id` + `request_id`），返回 202 `{id, run_id, case_ids, status: "pending", timeout_seconds, ...}`（完整字段见 `TaskRead`）。

**GET /tasks/{task_id}**：响应任务全字段 `status/result_summary/report_link/celery_task_id/pid/error_stage/error_msg/timeout_seconds/started_at/finished_at`。客户端按 2s 间隔轮询直至终态（`pending→running→success/failed`）。

**result_summary 结构**：成功任务为 `{total, passed, failed, skipped, duration_ms, results, executor, build_url}`。`executor` 取 `local|jenkins`；`build_url` 在 local 执行时为 `null`，Jenkins 执行时为构建页地址；`results` 每项 `{case_id, name, status, failure_msg}`（解析器不回填 `name`，取 schema 默认空串）。失败任务不写 `result_summary`，原因看 `error_stage`/`error_msg`。

**GET /tasks/{task_id}/results**：响应 `{task: {...}, results: [...]}`，`results` 从 `task.result_summary.results` 读取（执行闭环 无独立结果表）。

**GET /tasks/{task_id}/report**：任务 HTML 报告原文（`text/html`，非 `{code,message,data}` 包装），需鉴权。此前报告走无鉴权的 `/static` 静态挂载，task_id 自增即可枚举用例名与失败信息。任务不存在返回 404 `NOT_FOUND`；任务存在但报告未生成返回 404 `REPORT_NOT_FOUND`。当前只有成功终态写报告（失败/超时/取消不生成），所以后者是失败任务的常态返回。

**POST /tasks/{task_id}/cancel**：取消任务，`pending` 与 `running` 均可。条件更新以读到的那个状态为前置条件，期间状态变化（已跑完或被重新领取）返回 409 `TASK_NOT_CANCELLABLE`，不谎报成功。`running` 会先 best-effort 杀掉 pytest 进程树（否则状态改了进程还在跑），并跳过 revoke（消息早已被 Worker 消费）；`pending` 则额外 revoke 已入队消息。`executor=jenkins` 时平台侧没有本地 pid，取消只写平台终态，不会中止远端构建，也不会打断 Worker 的轮询等待（构建结果回来后由条件更新丢弃）。成功落 `failed(error_stage='cancelled')`。语义上用 FAILED 加 `error_stage` 承载取消，不扩状态枚举。执行层的失败与成功回写都是条件更新，不会覆盖取消终态。

**POST /tasks/{task_id}/retry**：人工重试失败任务，重试只由人工触发。仅 `failed`（含取消、排队超时回收）可重试：重置 `pending` 并重新入队（`updated_at` 随之刷新，避免被排队超时回收立刻误杀），返回 202；状态非 failed 返回 409 `TASK_NOT_RETRYABLE`。重试按当前 `execution.executor` 重新选择执行器，不锁定上一轮的执行来源。

重试会把上一轮的执行痕迹**全部清空**：`result_summary`、`report_link`、`started_at`、`pid`、`error_stage/error_msg`、`finished_at` 一并置空，并删除磁盘上的旧报告文件。否则「已排队」的任务仍能从 `GET /tasks/{id}/results` 读到上一轮的通过明细、从 `GET /tasks/{id}/report` 打开上一轮的报告。

### 3.3 健康检查 `/api/v1/health`

`{status: "ok"|"degraded", db: "up"|"down", redis: "up"|"down"}`。任一依赖 down 时 `status="degraded"`（HTTP 仍 200，探针按 body 判级）。不做鉴权，供部署探针使用。

### 3.4 解析与影响分析（纯规则同步）

**POST /parse**：请求体 `{document: {...OpenAPI 3.x}, version?: "v1"}`（version 缺省时自动生成 `v{n}`）。

- 解析提取 operation_ids、分段 hashes 与 contracts，落库 `api_definitions`。
- reparse 覆盖：同 version 再解析时先删旧插新，CI 幂等不膨胀。
- 文档超过 `swagger.max_upload_bytes` 返回 422 `SWAGGER_TOO_LARGE`；非法文档（非 3.x / 无 paths）返回 422 `INVALID_SWAGGER`。
- 响应 201：`{version_id, version, hash_version, operation_count, operation_ids, warnings}`，warnings 含 $ref 找不到、跨文件引用、operation_id 重复三类。

**POST /impact/analyze**：请求体 `{document: {...新版}, new_version?, old_version?}`（old_version 缺省取最近版本）。

- 解析新版并落库，与旧版本 diff 做 breaking 联合判定，再走 SQL 反向检索，结果落库 `impact_analyses`，返回 200。
- 响应：`{analysis_id, old_version, new_version, added_ops, removed_ops, changed_ops, breaking_changed_ops, affected_cases, affected_summary, orphaned_case_ids, suggested_remap, untested_ops, warnings, has_fix_hint, fix_hint_endpoint}`。
- 首次分析（无旧版本）时 added 与 untested 为全部、无 affected，不报错。
- 相同文档重复 analyze 时 `changed_ops` 为空（identical）。

**POST /impact/{analysis_id}/regression**：一键回归。

- 读 `affected_case_ids` 快照，宽容过滤后只执行当前仍 active 的用例，复用 `POST /tasks` 的 Lookup-Create 幂等，返回 202。
- 响应：`{analysis_id, task_id, task_status, executed_case_ids, executed_count, dropped_case_ids, dropped_count, dropped_reasons, affected_summary}`，summary 为执行时真实口径 `{total: executed+dropped, executed, dropped}`，dropped 附 reason（case deleted / case draft）。
- 无受影响用例返回 422 `NO_AFFECTED_CASES`；受影响用例全部失效返回 422 `NO_ACTIVE_CASES`。

**POST /impact/{analysis_id}/fix-hints**：对 breaking 变更调 LLM 生成修复建议，返回 `{analysis_id, ai_fix_hint}`，其中 `ai_fix_hint = {breaking_changed_ops, suggestion}` 并落库（best-effort，失败时 `ai_fix_hint` 为 NULL）；`AnalyzeResult` 返回 `has_fix_hint` 与 `fix_hint_endpoint` 提示入口。无 breaking 变更返回 422 `NO_BREAKING_CHANGES`。

### 3.5 AI 用例生成（Celery 异步）

**POST /generate**：请求体 `{document: {...OpenAPI 3.x}, operation_ids?: [...], force_full?: false}`，返回 202 `{id, run_id, status: "pending", ...}`。

- 触发方式按优先级排列：显式 `operation_ids` 定向；`force_full=True` 全量重建；两者都缺省时读最新影响分析的 `untested_ops`（无历史分析则全量开箱即用，已全覆盖返回 422 `NO_UNTESTED_OPS`）。
- 定向校验：显式 `operation_ids` 先与文档解析结果比对。全部未命中返回 422 `INVALID_OPERATION_IDS`（`message` 带文档中的合法示例）；部分命中照常执行，未命中项记入 `result_summary.skipped_detail`。
- Lookup-Create 幂等：`run_id = sha256(json.dumps({document, operation_ids}, sort_keys=True))`。已 SUCCESS 复用（不重复调 LLM）；已 FAILED 重置 PENDING 重新入队；PENDING/RUNNING 返回现状。入队失败返回 503 `DISPATCH_FAILED`，任务落 `failed(error_stage="dispatch")`。
- 入口与 Worker 的文档校验：走 `operation_ids` 或智能决策时，创建阶段即同步 parse，畸形文档在此直接返回 422 `INVALID_SWAGGER`（不建任务、不调 LLM）；仅 `force_full=True` 跳过入口 parse，畸形文档才由 Worker 落 `failed(error_stage="parse")`。
- 文档超过 `swagger.max_upload_bytes` 返回 422 `SWAGGER_TOO_LARGE`。
- 结果 `result_summary`：`{generated, draft_created, rejected, rejected_detail:[{operation_id, reason}], skipped_by_filter, skipped_detail, prompt_version, cost_total, redacted}`。任务内超时预算耗尽时额外带 `timed_out: true` 与 `timed_out_at`，该次任务落 `failed(error_stage="timeout")`。
- `redacted`：LLM 输出里被脱敏的敏感片段数（密钥形态、内网地址、注入句式替换为 `[REDACTED]`）；大于 0 时用例内容已被改写，Worker 日志同时有 warning。

**GET /generate/{id}**：轮询 `{id, status: pending→running→success/failed, result_summary, error_msg}`。

- 生成用例经 `GET /cases?status=draft` 查看（source=ai、trust_score 逐条计算、`quality_flags` 给出扣分原因）；confirm 复用 `POST /cases/{id}/confirm` 转 active 后才可执行。

## 4. 与 Celery 交互模式

1. `POST /tasks` 先写 DB 记录（pending），再 `send_task`，立即返回 202；pytest 在独立 Worker 进程执行，Web 不阻塞。
2. Web 层不依赖 Celery result backend 读结果，状态一律以 DB 为准（单一事实源，Worker 重启不丢）。
3. 任务入参只传 `task_id`，Worker 内再从 DB 读上下文。
4. 重复或并发入队命中 `run_id` 唯一约束时返回已存在任务（Lookup-Create）。

## 5. 前端使用说明

- 后端界面为 Swagger UI（`/docs`）：所有操作直接在 Swagger UI 完成。先点右上角 Authorize 输入 Bearer Token，再创建用例（operation_id 写死如 `httpbin_get`）、确认 active、触发执行（202 返回 `data.id` 后轮询 `GET /tasks/{id}` 看 `pending→running→success`）、查看 results 并带鉴权取报告。
- Vue 前端（前端已实现）：`frontend/` 独立仓库（Vue 3 + TS + Element Plus），含仪表盘、用例管理、任务执行、AI 生成四个页面。dev 环境经 Vite 代理（`/api`、`/docs`）同源访问后端；令牌在顶栏「令牌」或仪表盘空态配置（`testplatform-dev-token`）。详见 [frontend/README.md](https://github.com/Testamentt/ApiTestPlatform_Frontend/blob/master/README.md)。
- 一次完整调用顺序：`POST /cases`（2 条）→ `POST /cases/{id}/confirm` → `POST /tasks`（202，取响应 `data.id`）→ 轮询 `GET /tasks/{id}` → `GET /tasks/{id}/results` → `GET /tasks/{id}/report`（带鉴权取报告原文）。

## 6. 相关文档

- [database.md](database.md)：任务与用例状态机、字段约束（契约的存储侧）
- [execution-engine.md](execution-engine.md)：任务如何被执行、超时与回收
- [architecture.md](architecture.md)：分层、鉴权与错误处理边界
- [README.md](README.md)：文档地图与阅读路径；[../README.md](../README.md)：项目入口
