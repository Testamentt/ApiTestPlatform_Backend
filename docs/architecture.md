# 架构设计（architecture.md）· Phase 1 简化版

> 规则引用：本项目的所有实现必须遵守 [`.claude/rules/RULES.md`](../.claude/rules/RULES.md)（§1-§18 权威规则）。本文是总体架构基准文档，描述 MVP 零前端、后端三层、进程隔离与异步模型；与规则冲突时以 RULES.md 为准。
> **Phase 1 简化**：仅「用例 + 任务执行」两域；parse/generate/impact 属 Phase 2/3。

## 1. 目标与非目标

**目标**：交付可运行、可测试、可讲清楚（面试防守）的 MVP——「创建用例 → 异步执行 → 查看结果」闭环。

**非目标（Phase 1 不包含）**：
- 前端界面（**MVP 界面 = FastAPI Swagger UI**，零前端代码；Vue 为 Phase 4 可选）
- 鉴权与限流（**均不做**，演示开箱即用；Phase 4 加 Bearer Token）
- 多环境管理（base_url 写死 config）、多环境生产部署

## 2. 架构总览（MVP 零前端 + 后端三层）

```
用户 / CI 系统
   │ REST /api/v1
   ▼
┌──────────────────────────────────────────────────────────────┐
│ 【MVP 界面】FastAPI Swagger UI（/docs，零前端代码）             │
│   Phase 4 可选：Vue 3（frontend/，仅 2 页）                    │
└──────────────────────────┬───────────────────────────────────┘
                           │ HTTP JSON
                           ▼
┌──────────────────────────────────────────────────────────────┐
│ 【Web 服务层】 FastAPI + SQLAlchemy（同步 def 端点 + 线程池）    │
│   ├─ 用例 CRUD + 审核    /api/v1/cases                         │
│   ├─ 任务触发/查询       /api/v1/tasks                          │
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
│   ④ 动态生成 test_xxx.py → subprocess pytest                  │
│      （run_cmd 封装：白名单 + timeout + taskkill /T /F 兜底）   │
│   ⑤ 解析 JUnit XML → 结果写 tasks.result_summary             │
│   ⑥ 生成 HTML 报告链接                                       │
└──────────────────────────────────────────────────────────────┘
```

## 3. 进程隔离与故障边界

- **Web 服务 ≠ 执行引擎**：pytest 在独立 Worker 进程内通过 subprocess 运行，FastAPI 挂掉不影响正在执行的测试。
- **任务不丢**：任务状态持久化在 SQLite `tasks` 表，**DB 是唯一状态源**；Celery result backend 仅辅助（`result_expires=3600`）。
- **超时兜底**：`tasks.pid` 记录 pytest 进程 PID（mark_running 写入）；`scan_stale_tasks` 从 DB 读 pid `taskkill /T /F` 杀整棵树（**权威兜底**）。

## 4. 异步任务与超时兜底模型

统一异步模式（Phase 1 仅 execute）：

1. Web 层算 `run_id`（sha256(case_ids+timeout)）→ **查重：存在即返回已有任务**（Lookup-Create）→ 否则写 DB 记录（PENDING）→ `send_task` → **立即返回 202**
2. 客户端轮询 `GET /api/v1/tasks/{id}` 获取状态与结果
3. Worker 内短事务写入结果（先查→提交关事务→算→再开新事务写入，RULES.md §2.1）

Celery 关键配置：`task_acks_late=True` + `worker_prefetch_multiplier=1` + `task_reject_on_worker_lost=True`；`soft_time_limit`/`time_limit`（300/360）；`visibility_timeout`(1h) > `time_limit`。
- **任务幂等**：`run_id`（sha256）+ `UNIQUE`，Lookup-Create 存在即返回，防重复执行。
- **超时劫持**：Worker 启动扫描一次（无 Beat）；running 超时 → taskkill 杀树 → 迁移 `failed`（error_stage=timeout），**禁止自动重试**。

## 5. 核心数据流时序（执行链路，Phase 1）

```
1. POST /api/v1/tasks {case_ids, timeout_seconds?}（base_url 写死 config）
2. Web: 校验用例均 active（draft 禁止执行）→ run_id 查重（存在即返回）→ 写 PENDING → send_task → 202
3. Worker execute_cases:
   a. 读 task → active 用例列表（case_ids 快照）
   b. 建按 task_id 隔离的 workspace: .workspace/tasks/{task_id}/（test 文件 + report.xml 均在此）
   c. 逐用例生成 test_{case_id}.py（结构化字段 repr 渲染，只断言 expected_status）
   d. run_cmd([python -m pytest test_*.py --junitxml=report.xml -o addopts= -p no:cacheprovider],
             timeout=config, check=False)
   e. 写 tasks.pid + status=running + started_at（mark_running）
   f. 超时 → run_cmd best-effort kill + 抛错 → failed(error_stage=timeout)；权威清理靠 scan_stale_tasks
   g. 正常结束 → junit_parser 累加各 testsuite → 组 result_summary {total,passed,failed,...}
   h. report_util 写 report.html（best-effort，无结果也生成）→ report_link → status=success
4. 客户端轮询 GET /api/v1/tasks/{id} → 进度/结果/HTML 报告链接
```

## 6. 关键设计决策（面试防守）

| 决策 | 理由 |
| --- | --- |
| 异步解耦 | Web 只写 DB + send_task 立即返回 202；pytest 在独立 Worker，Web 不阻塞（面试锚点） |
| 超时兜底双保险 | `run_cmd(timeout)` + **`scan_stale_tasks` 从 DB 读 pid 杀树**（权威） |
| run_id Lookup-Create | sha256 + UNIQUE，存在即返回，防重复执行（面试锚点） |
| `operation_id` NOT NULL | Phase 2 影响分析血缘（前瞻卖点） |
| MVP 零前端 | 界面 = Swagger UI，零前端代码 |

## 7. 前端形态（MVP 零前端，面试导向）

- **MVP 界面 = FastAPI Swagger UI（`/docs`）**：所有操作（创建用例 / 审核 draft / 触发执行 / 查看任务 / HTML 报告链接）直接在 Swagger UI 完成，零前端代码。开发环境开放；生产环境 `/docs` 关闭或鉴权保护（RULES.md §10.5）。
- **Vue 3（Phase 4 可选）**：`frontend/` 独立仓库，若做仅 2 页（用例列表 + 任务看板）。

## 8. 目录结构（backend/ 仓库，对齐 RULES.md §4）

```
backend/
├── pyproject.toml / .env / config/settings.yaml / alembic(Phase 4)
├── app/
│   ├── main.py               # 应用入口（路由聚合 + 异常 handler + /static 挂载 + lifespan 建表）
│   ├── celery_app.py         # Celery app（显式读 Settings 拼 broker/backend + worker_ready 触发 scan）
│   ├── core/                 # config / database / logging / exceptions
│   ├── api/v1/               # 路由 + deps（cases / tasks / health）
│   ├── models/               # SQLAlchemy ORM（test_cases / tasks，仅 2 表）
│   ├── schemas/              # Pydantic 出入参（response_model）
│   ├── services/             # 业务编排（case_service / task_service / execution_service）
│   ├── repositories/         # 数据访问（物理删除）
│   ├── tasks/                # Celery 任务（execute_cases / scan_stale_tasks）
│   └── utils/                # subprocess_util / case_generator / junit_parser / report_util
├── prompts/                  # Phase 3 再建（版本化 + 占位符注入）
├── docs/  tests/  scripts/
└── .claude/                  # rules/RULES.md（§1-§18）、skills/
```

依赖单向：`api → service → repository → model`；外部调用（subprocess / HTTP）统一走 `app/utils/` 封装。`frontend/` 为独立 git 仓库（Phase 4 可选）。

## 9. 预期与风险

- 预期：Web 响应稳定 <50ms；支持 10+ 用例并发；死循环用例被超时强杀，系统不卡死。
- 风险：SQLite 双进程并发写（WAL + busy_timeout + 单 Worker 串行规避）；网络依赖（演示 httpbin.org，测试用 mock）；Windows 杀树不完全（scan_stale_tasks 兜底）。

## 10. 相关文档

- [database.md](database.md) — 数据模型（Phase 1：2 表）
- [api.md](api.md) — REST API 设计（Phase 1：cases/tasks/health）
- [execution-engine.md](execution-engine.md) — Celery 任务与执行引擎
- [ai-generation.md](ai-generation.md) — AI 用例生成（Phase 3 前瞻）
- [impact-analysis.md](impact-analysis.md) — 影响分析算法（Phase 2 前瞻）
- [configuration.md](configuration.md) — 配置管理
- [roadmap.md](roadmap.md) — 迭代路线
