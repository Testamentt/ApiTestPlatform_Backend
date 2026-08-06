# 架构设计（architecture.md）

> 规则引用：本项目的所有实现必须遵守 [`.claude/rules/RULES.md`](../.claude/rules/RULES.md)（§1-§18 权威规则）。本文是总体架构基准文档，描述 MVP 零前端、后端三层、进程隔离与异步模型；与规则冲突时以 RULES.md 为准。

## 1. 目标与非目标

**目标**：交付一套轻量级、可扩展的接口测试效能平台，以「AI 提效 + 异步解耦 + 精准回归」为核心，降低接口用例编写成本，提升回归测试准确性与执行稳定性。

**非目标（MVP 阶段不包含）**：
- 前端界面（**MVP 界面 = FastAPI Swagger UI**，零前端代码；Vue 为 Phase 4 可选，面试不扣分）
- 限流（Phase 1 只做 Bearer Token 鉴权，不做 Token/IP 限流——面试导向，见 roadmap）
- 多环境生产部署（先本地/Docker 单机）
- 细粒度权限体系（先 Bearer Token 简单鉴权，见 RULES.md §10.3）

## 2. 架构总览（MVP 零前端 + 后端三层）

```
用户 / CI 系统
   │ REST /api/v1（Bearer Token）
   ▼
┌──────────────────────────────────────────────────────────────┐
│ 【MVP 界面】FastAPI Swagger UI（/docs，零前端代码）             │
│   Phase 4 可选：Vue 3（frontend/，仅 2 页）                    │
└──────────────────────────┬───────────────────────────────────┘
                           │ HTTP JSON
                           ▼
┌──────────────────────────────────────────────────────────────┐
│ 【Web 服务层】 FastAPI + SQLAlchemy（同步 def 端点 + 线程池）    │
│   ├─ 用例 CRUD          /api/v1/cases                         │
│   ├─ Swagger 解析       /api/v1/parse（同步预览）              │
│   ├─ AI 用例生成        /api/v1/generate（异步）               │
│   ├─ 任务管理           /api/v1/tasks                          │
│   └─ 影响分析           /api/v1/impact（异步）                 │
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
│   ④ 动态生成 test_xxx.py → subprocess pytest                  │
│      （run_cmd 封装：独立进程组 + 300s 超时 + taskkill /T /F）  │
│   ⑤ 解析 JUnit XML → 结果回写 DB                              │
│   ⑥ 生成 Allure 报告链接                                      │
└──────────────────────────────────────────────────────────────┘
```

## 3. 进程隔离与故障边界

- **Web 服务 ≠ 执行引擎**：pytest 在独立 Worker 进程内通过 subprocess 运行，FastAPI 挂掉不影响正在执行的测试。
- **任务不丢**：任务状态持久化在 SQLite 业务表（`tasks`），**DB 是唯一状态源**；Celery result backend 仅作辅助（`result_expires=3600`），不依赖它读进度。
- **超时兜底**：`tasks.pid` 记录 subprocess 主进程 PID，`scan_stale_tasks` 扫描 running 超过 `timeout_seconds`(默认 300) 的任务，`taskkill /T /F` 强杀进程树（Windows）/ `os.killpg`（POSIX），杜绝死循环卡死系统。

## 4. 异步任务与超时兜底模型

统一异步模式（所有耗时操作：execute / generate / impact analyze）：

1. Web 层写 DB 任务记录（`status=PENDING`，入参只传 ID 快照，见 RULES.md §8.4）
2. `send_task(...)` 入队，**立即返回 `task_id`（HTTP 202）**
3. 客户端轮询 `GET /api/v1/tasks/{id}` 获取状态与结果
4. Worker 内短事务写入结果（先查→提交关事务→算→再开新事务写入，见 RULES.md §2.1）

Celery 关键配置（对齐 RULES.md §8.2/§8.3）：
- `task_acks_late=True` + `worker_prefetch_multiplier=1` + `task_reject_on_worker_lost=True`
- 每个任务 `soft_time_limit`/`time_limit`（如执行任务 300/360s），顶部捕获 `SoftTimeLimitExceeded` 做清理
- `visibility_timeout`（默认 1h）必须大于 `time_limit`
- 任务幂等：`run_id` + 输入指纹唯一约束，重复入队直接复用
- 僵尸任务（心跳超时）：状态机迁移为 `FAILED`（原因 `worker_restart`/`timeout`），**禁止自动重试**，重试仅人工触发

## 5. 核心数据流时序（执行链路）

```
1. POST /api/v1/tasks {case_ids, env_id}
2. Web: 校验用例均为 active（draft 禁止执行）→ 写 tasks(PENDING) → send_task(execute_cases, args=[task_id])
   → 返回 202 {task_id}
3. Worker execute_cases:
   a. 读 task → env + active 用例列表（case_ids 快照）
   b. 建 workspace: .workspace/tasks/{task_id}/ + conftest.py
   c. 逐用例生成 test_{case_id}.py（结构化 request/assertions 渲染）
   d. subprocess run_cmd([python -m pytest ... --junitxml --alluredir]) 
   e. 记录 tasks.pid + status=RUNNING
   f. 轮询 poll()；超时 → run_cmd 内部 killpg/taskkill → 未完成用例标 killed
   g. 正常结束 → 解析 junit.xml → 写 case_results（UNIQUE(task_id,case_id) 幂等）
   h. 汇总 result_summary → allure generate → tasks.allure_link → status=SUCCESS
4. 客户端轮询 GET /api/v1/tasks/{id} → 进度/结果/报告链接
```

## 6. 关键设计决策（对应三大亮点）

| 亮点 | 设计决策 | RULES.md 依据 |
| --- | --- | --- |
| ① AI 提效 | 生成用例恒为 `draft`，仅人工审核接口可转 `active`；LLM 输出过 Pydantic 严格校验后才能入库，失败记录同样落库（status=failed + 原因 + prompt_version + model + 原始响应） | §10.2 / §11.2 |
| ② 影响分析 | `test_cases.operation_id` 索引 + `api_definitions` 版本快照 + `operation_hashes` O(1) diff + SQL 反向检索 | §5.2（索引） |
| ③ 异步解耦 | 状态以 DB 为单一事实源；`tasks.pid` 供跨进程强杀；300s 超时进程树清理 | §8.3 / §8.5 / §2.4 |
| MVP 零前端 | 界面 = FastAPI Swagger UI（`/docs`），零前端代码；Vue 3（`frontend/`）为 Phase 4 可选增强（面试不扣分） | §7（response_model） |

## 7. 前端形态（MVP 零前端，面试导向）

- **MVP 界面 = FastAPI Swagger UI（`/docs`）**：所有操作（创建用例 / 触发执行 / 查看任务 / 审核 draft / 影响分析 / Allure 报告链接）直接在 Swagger UI 完成，零前端代码。开发环境开放；生产环境默认关闭或鉴权保护（RULES.md §10.5）。
- **Vue 3（Phase 4 可选）**：`frontend/` 独立仓库；若做仅 2 页（用例列表 + 任务看板），其余继续用 Swagger UI；面试不扣分，可跳过。开发走 Vite 代理或 CORS 白名单，禁止 `*`（RULES.md §10.5）。
- **CORS**：MVP 同源（Swagger UI）无需 CORS；仅 Phase 4 引入 Vue 时配置 `app.cors_origins` 白名单。

## 8. 目录结构（两个独立 git 仓库，对齐 RULES.md §4）

```
TestPlatform/                     # 容器目录（非 git 仓库）
├── backend/                      # 仓库 A：FastAPI 后端 + 项目文档/规则/配置
│   ├── app/
│   │   ├── main.py               # 应用入口（路由聚合 + 异常 handler + request_id 中间件 + 静态挂载前端 dist）
│   │   ├── celery_app.py         # Celery app（broker/backend/任务注册）
│   │   ├── core/                 # config / logging / 统一异常 AppError
│   │   ├── api/v1/               # 路由 + deps（依赖注入）
│   │   ├── models/               # SQLAlchemy ORM
│   │   ├── schemas/              # Pydantic 出入参（response_model）
│   │   ├── services/             # 业务编排（case_service / task_service / parse_service / generate_service / impact_service）
│   │   ├── repositories/         # 数据访问（统一软删除过滤）
│   │   ├── tasks/                # Celery 任务（execute_cases / generate_cases / impact_analyze / scan_stale_tasks）
│   │   └── utils/                # subprocess_util.run_cmd / llm_client（唯一外部调用封装）
│   ├── prompts/                  # v1/system.md, v1/user.md（版本化 + 占位符注入）
│   ├── config/                   # settings.yaml（复制自 settings.example.yaml）+ .env
│   ├── docs/  tests/  scripts/  docker/
│   ├── pyproject.toml  .env.example  .gitignore
│   ├── README.md  CLAUDE.md  memory.md
│   └── .claude/                  # rules/RULES.md（§1-§18）、skills/
└── frontend/                     # 仓库 B：Vue 3 前端工程（Phase 4 可选，独立 git 仓库）
    └── src/  package.json  vite.config.ts  index.html
```

依赖单向：`api → service → repository → model`；外部调用（subprocess / LLM / HTTP）统一走 `app/utils/` 封装。两个仓库独立提交（git 各自独立），见 [configuration.md](configuration.md) §5 本地/生产差异。

依赖单向：`api → service → repository → model`；外部调用（subprocess / LLM / HTTP）统一走 `app/utils/` 封装。

## 9. 预期与风险

- 预期：单接口用例编写 10~15min → 2~3min；影响评估 1~2h → <10s；Web 响应稳定 <50ms；支持 10+ 用例并发。
- 风险：SQLite 单写者限制（MVP 用 `--pool=solo` 串行化，见 RULES.md §3.3）；LLM 成本不可控（需成本日志，见 RULES.md §9.6）；子进程残留进程（靠超时劫持 + 启动扫描兜底）。

## 10. 相关文档

- [database.md](database.md) — 数据模型
- [api.md](api.md) — REST API 设计
- [execution-engine.md](execution-engine.md) — Celery 任务与执行引擎
- [ai-generation.md](ai-generation.md) — AI 用例生成
- [impact-analysis.md](impact-analysis.md) — 影响分析算法
- [configuration.md](configuration.md) — 配置管理
- [roadmap.md](roadmap.md) — 迭代路线
