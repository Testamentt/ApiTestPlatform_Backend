# 架构设计（architecture.md）· Phase 1-4 实现版

> 规则引用：本项目的所有实现必须遵守 `RULES.md`（§1-§18 权威规则）。本文是总体架构基准文档，描述双界面、后端三层、进程隔离与异步模型；与规则冲突时以 RULES.md 为准。
> **Phase 1-4 均已实现**：Phase 1「用例 + 任务执行」闭环；Phase 2「影响分析」——parse/impact 纯规则引擎（Diff+SQL）；Phase 3「AI 生成」——OpenAPI→LLM→draft 用例（复用解析器 + llm_client + 三层防幻觉护栏）；Phase 4「生产化」——Docker Compose 一键跑 + GitHub Actions CI + Bearer Token 鉴权（见 [roadmap.md](roadmap.md)）。

## 1. 目标与非目标

**目标**：交付可运行、可测试、可讲清楚（面试防守）的 MVP——「创建用例 → 异步执行 → 查看结果」闭环，扩展「接口变更自动圈定影响」与「AI 辅助用例生成」。

**非目标（当前不包含）**：
- ~~前端界面~~（**双界面已实现**：Swagger UI + `frontend/` Vue 3 四页——仪表盘/用例管理/任务执行/AI 生成）
- **限流**（Phase 4 已补 Bearer Token 鉴权；Token/IP 限流仍延后，见 roadmap「待解决问题」）
- 多环境管理（base_url 写死 config）

**Phase 2 已新增**：影响分析（parse/impact，纯规则 Diff+SQL 秒回）——见 [impact-analysis.md](impact-analysis.md)。
**Phase 3 已新增**：AI 智能生成（generate/fix-hints，LLM 异步 + 三层防幻觉护栏）——见 [ai-generation.md](ai-generation.md)。

## 2. 架构总览（双界面 + 后端三层）

```
用户 / CI 系统 / Vue 前端（frontend/，Phase 4 已实现 4 页）
   │ REST /api/v1
   ▼
┌──────────────────────────────────────────────────────────────┐
│ 【界面层】FastAPI Swagger UI（/docs）+ Vue 3（frontend/）      │
│   Vue dev 走 Vite 代理（/api、/static、/docs）同源访问后端      │
└──────────────────────────┬───────────────────────────────────┘
                           │ HTTP JSON
                           ▼
┌──────────────────────────────────────────────────────────────┐
│ 【Web 服务层】 FastAPI + SQLAlchemy（同步 def 端点 + 线程池）    │
│   ├─ 用例 CRUD + 审核    /api/v1/cases                         │
│   ├─ 任务触发/查询       /api/v1/tasks                          │
│   ├─ Swagger 解析/影响    /api/v1/parse、/api/v1/impact/*        │
│   ├─ AI 生成             /api/v1/generate（异步 202）            │
│   └─ 健康检查            /api/v1/health                        │
│     ① 写 DB 任务记录(status=PENDING)   ② send_task 入队       │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
┌──────────────────────────┐
│ 【Redis Broker】          │ ← 消息中转（Celery 队列）
│  ── ③ Worker 拉取任务     │
└──────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│ 【执行引擎层】 Celery Worker（独立进程，--pool=solo）            │
│   execute_cases: 动态生成 test_xxx.py → subprocess pytest      │
│      （run_cmd 封装：白名单 + timeout + taskkill /T /F 兜底）   │
│      解析 JUnit XML → 结果写 tasks.result_summary → HTML 报告  │
│   generate_cases: parse → 逐 op LLM → draft 落库 → 审计        │
│      （llm_client 唯一封装，短事务分界，三层防幻觉护栏）         │
└──────────────────────────────────────────────────────────────┘
```

## 3. 进程隔离与故障边界

- **Web 服务 ≠ 执行引擎**：pytest 在独立 Worker 进程内通过 subprocess 运行，FastAPI 挂掉不影响正在执行的测试。
- **任务不丢**：任务状态持久化在 SQLite `tasks` 表，**DB 是唯一状态源**；Celery result backend 仅辅助（`result_expires=3600`）。
- **超时兜底**：`tasks.pid` 记录 pytest 进程 PID（mark_running 写入）；`scan_stale_tasks` 从 DB 读 pid `taskkill /T /F` 杀整棵树（**权威兜底**）——执行任务与生成任务均被扫描（阈值：执行=任务级 `timeout_seconds`，生成=`llm.task_timeout_seconds`）。

## 4. 异步任务与超时兜底模型

统一异步模式（execute 执行 + generate 生成，两套独立任务表与状态机）：

1. Web 层算 `run_id`（sha256(输入指纹)）→ **查重（review H4：SUCCESS 复用 / FAILED 重置 PENDING 重新入队 / 其余返回现状）** → 否则写 DB 记录（PENDING）→ `send_task` → **立即返回 202**；入队失败落 `FAILED(dispatch)` + 503
2. 客户端轮询 `GET /api/v1/tasks/{id}` 或 `GET /api/v1/generate/{id}` 获取状态与结果
3. Worker 内短事务写入结果（先查→提交关事务→算→再开新事务写入，RULES.md §2.1）

Celery 关键配置：`task_acks_late=True` + `worker_prefetch_multiplier=1` + `task_reject_on_worker_lost=True`；执行任务 `soft_time_limit`/`time_limit`（300/360）；生成任务 `soft_time_limit`/`time_limit`（540/600，软超时捕获 `force_fail_timeout` 落 FAILED）；`visibility_timeout`(1h) > 各任务 `time_limit`。
- **任务幂等**：`run_id`（sha256）+ `UNIQUE`；Lookup-Create 语义 = SUCCESS 复用 / FAILED 可重试（重置 PENDING 重新入队，任务函数有状态守卫）/ PENDING/RUNNING 返回现状（review H4），防重复执行/重复调 LLM 花钱。
- **超时劫持**：Worker 启动扫描一次（无 Beat），覆盖执行任务（任务级 `timeout_seconds` 阈值，taskkill 杀树）与生成任务（`llm.task_timeout_seconds` 阈值）；running 超时 → 迁移 `failed`（error_stage=timeout），**扫描不自动重试**（用户可同输入重新提交触发重试）。生成任务软超时 → `force_fail_timeout` 落 FAILED。

## 5. 核心数据流时序（执行链路，Phase 1）

```
1. POST /api/v1/tasks {case_ids, timeout_seconds?}（base_url 写死 config）
2. Web: 校验用例均 active（draft 禁止执行）→ run_id 查重（SUCCESS 复用 / FAILED 重置重试 / 其余返回现状）→ 写 PENDING → send_task → 202
3. Worker execute_cases:
   a. 读 task → active 用例列表（case_ids 快照）
   b. 建按 task_id 隔离的 workspace: .workspace/tasks/{task_id}/（test 文件 + report.xml 均在此）
   c. 逐用例生成 test_{case_id}.py（结构化字段 repr 渲染，只断言 expected_status）
   d. run_cmd([python -m pytest test_*.py --junitxml=report.xml -o addopts= -p no:cacheprovider],
             timeout=task.timeout_seconds（缺省 pytest_timeout）, check=False)
   e. 写 tasks.pid + status=running + started_at（mark_running）
   f. 超时 → run_cmd best-effort kill + 抛错 → failed(error_stage=timeout)；权威清理靠 scan_stale_tasks
   g. 正常结束 → junit_parser 累加各 testsuite → 组 result_summary {total,passed,failed,...}
   h. report_util 写 report.html（best-effort，无结果也生成）→ report_link → status=success
4. 客户端轮询 GET /api/v1/tasks/{id} → 进度/结果/HTML 报告链接

### Phase 2 影响分析链路（纯规则，同步 <1s，无 AI）

```
1. POST /api/v1/parse {document, version?} → 解析 → 分段 hashes + contracts → 落库 api_definitions（reparse 覆盖）→ 201
2. POST /api/v1/impact/analyze {document} → 解析新版 → diff + breaking 联合判定
   → SQL 反向检索（breaking 变更的 active 用例 / removed 的 orphaned / 未绑的 untested）→ 落库 impact_analyses → 200
3. POST /api/v1/impact/{analysis_id}/regression → 宽容降级过滤失效用例 → 复用 Phase 1 执行引擎 → 202 {task_id} → 轮询
```

### Phase 3 AI 生成链路（Celery 异步，LLM 不阻塞 Web）

```
1. POST /api/v1/generate {document, operation_ids?, force_full?} → run_id=sha256(document+operation_ids) Lookup-Create 幂等
   → 落库 generation_tasks(PENDING, document) → generate_cases_task.delay(task_id) → 202
2. Worker generate_cases_task（time_limit=llm.task_timeout_seconds=600）：
   → parse_openapi → 过滤定向 operation_ids（未命中的记 skipped_detail，不静默）
   → 逐 operation：prompts/v1 渲染 → llm_client.chat_json（response_format + _extract_json）→ Pydantic 严格校验
   → 通过：operation_id 服务端注入 + trust_score(warnings?60:80) → 落库 draft
   → 失败：generation_log(validation_failed + raw_response + confidence=0) → 不建坏用例
   → result_summary {generated, draft_created, rejected, rejected_detail, skipped_*, cost_total} → SUCCESS
3. GET /cases?status=draft 查生成用例 → POST /cases/{id}/confirm（reviewer）→ active → 可执行
4. POST /impact/{id}/fix-hints（联动）：breaking 变更按需生成一句话修复建议（复用 llm_client，best-effort）
```

## 6. 关键设计决策（面试防守）

| 决策 | 理由 |
| --- | --- |
| 异步解耦 | Web 只写 DB + send_task 立即返回 202；pytest/LLM 在独立 Worker，Web 不阻塞（面试锚点） |
| 超时兜底双保险 | `run_cmd(timeout)` + **`scan_stale_tasks` 从 DB 读 pid 杀树**（权威）；生成任务软超时捕获落 FAILED |
| run_id Lookup-Create | sha256 + UNIQUE；SUCCESS 复用、FAILED 重置重试、其余返回现状，防重复执行/重复调 LLM 花钱（面试锚点） |
| `operation_id` NOT NULL | Phase 2 影响分析血缘 + Phase 3 服务端注入（前瞻卖点） |
| breaking 五场景判定 | required/type/enum/字段删除/状态码替换联合判定，放宽不误圈（精准回归卖点） |
| 三层防幻觉护栏 | extra="forbid" 严格校验 + draft 恒为人工 confirm + operation_id 服务端注入 |
| LLM 唯一封装 | llm_client 收敛超时/重试/长度预检/成本，测试可注入 Fake（评审红线） |
| 双界面 | Swagger UI（`/docs`）+ Vue 3 四页（frontend/，dev 走 Vite 代理） |

## 7. 前端形态（双界面，Phase 4 已实现）

- **后端界面 = FastAPI Swagger UI（`/docs`）**：所有操作（创建用例 / 审核 draft / 触发执行 / 查看任务 / HTML 报告链接）可直接在 Swagger UI 完成。**自带 Authorize 按钮**（Bearer Token 鉴权）；`/docs` 由 `app.docs_enabled` 配置开关控制，生产设 `TESTPLATFORM_APP_DOCS_ENABLED=false` 即关（RULES.md §10.5）。
- **Vue 3（Phase 4 已实现）**：`frontend/` 独立仓库（Vue 3 + TS + Element Plus + Pinia），4 页——仪表盘（流水线/统计/健康）、用例管理（CRUD + confirm）、任务执行（2s 轮询 + 结果抽屉 + 报告）、AI 生成（Swagger 粘贴/上传 + 草稿审核激活）；`vitest` 单测 + `vue-tsc` 构建门禁；dev 经 Vite 代理（`/api`、`/static`、`/docs`）同源访问后端，不依赖 CORS。

## 8. 目录结构（backend/ 仓库，对齐 RULES.md §4）

```
backend/
├── pyproject.toml / .env / config/settings.yaml / alembic(延后)
├── Dockerfile / docker-compose.yml / .dockerignore   # Phase 4 容器化（一键 docker compose up，§3.3）
├── .github/workflows/ci.yml                          # Phase 4 CI（门禁测试 + slow + docker-build 验证）
├── app/
│   ├── main.py               # 应用入口（路由聚合 + 异常 handler + /static 挂载 + lifespan 建表）
│   ├── celery_app.py         # Celery app（显式读 Settings 拼 broker/backend + worker_ready 触发 scan）
│   ├── core/                 # config / database / logging / exceptions
│   ├── api/v1/               # 路由 + deps（cases / tasks / health / parse / impact / generate）
│   ├── models/               # SQLAlchemy ORM（test_cases / tasks / api_definitions / impact_analyses / generation_tasks / generation_log）
│   ├── schemas/              # Pydantic 出入参（case / task / swagger / impact / generate）
│   ├── services/             # 业务编排（case / task / execution / swagger / impact / generation）
│   ├── repositories/         # 数据访问（case / task / api_definition / impact_analysis / generation_task，物理删除）
│   ├── tasks/                # Celery 任务（execute_cases / scan_stale_tasks / generate_cases）
│   └── utils/                # subprocess_util / openapi_parser / llm_client / impact_diff / case_generator / junit_parser / report_util
├── prompts/v1/               # 版本化 Prompt（system/user/fix_hint_{system,user}.md，占位符注入，写死 v1）
├── docs/  tests/  scripts/
└── .claude/                  # rules/RULES.md（§1-§18）、skills/（仅本地，不入库）
```

依赖单向：`api → service → repository → model`；外部调用（subprocess / HTTP / LLM）统一走 `app/utils/` 封装。`frontend/` 为独立 git 仓库（Phase 4 已实现，Vue 3 四页）。

## 9. 预期与风险

- 预期：Web 响应稳定 <50ms；支持 10+ 用例并发；死循环用例被超时强杀，系统不卡死；AI 生成 draft 供人工审核。
- 风险：SQLite 双进程并发写（WAL + busy_timeout + 单 Worker 串行规避）；网络依赖（演示 httpbin.org 外网不稳，测试用 mock；LLM 依赖 DeepSeek 可用性，失败宽容降级记 log）；Windows 杀树不完全（scan_stale_tasks 兜底）。

## 10. 相关文档

- [database.md](database.md) — 数据模型（Phase 1-3：6 表 test_cases/tasks/api_definitions/impact_analyses/generation_tasks/generation_logs）
- [api.md](api.md) — REST API 设计（Phase 1-3：cases/tasks/health/parse/impact/generate）
- [execution-engine.md](execution-engine.md) — Celery 任务与执行引擎（execute + generate）
- [ai-generation.md](ai-generation.md) — AI 用例生成（Phase 3 已实现：Prompt 管理 + llm_client + 三层护栏 + 审计）
- [impact-analysis.md](impact-analysis.md) — 影响分析算法（Phase 2 已实现，含 fix-hints）
- [configuration.md](configuration.md) — 配置管理
- [roadmap.md](roadmap.md) — 迭代路线
