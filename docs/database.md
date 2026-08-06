# 数据库设计（database.md）· Phase 1 简化版

> 规则引用：[`.claude/rules/RULES.md`](../.claude/rules/RULES.md) §2.1（SQLite 连接工厂与短事务）、§5（数据模型与迁移）。本设计所有字段名/表名与 API、执行引擎文档保持一致。
> **Phase 1 简化（面试导向）**：仅 2 张表（`test_cases` + `tasks`）；软删除 / 多环境 / 结果明细表已砍，Phase 2+ 再补。RULES.md §5 相关 MUST 已放宽为 MVP 例外（见 `.claude/rules/RULES.md` §5.1/§5.2）。

## 1. 设计原则（Phase 1 简化）

| 原则 | 要求 |
| --- | --- |
| 连接工厂 | 所有 SQLite 连接经唯一 `get_engine()` 创建，connect 事件统一执行 `PRAGMA journal_mode=WAL` / `busy_timeout=5000` / `foreign_keys=ON`；**SQLite 必加 `connect_args={"check_same_thread": False}`**（FastAPI 线程池跨线程用 SQLite 会报 "created in a thread"） |
| 建表 | **MVP 用 `Base.metadata.create_all(engine)`**（main lifespan 启动时同步执行一次，阻塞无妨）；Phase 4 切 Alembic |
| 短事务 | 禁止持有 Session 期间调用 subprocess/HTTP；统一「先查→提交关事务→算→再开新事务写入」 |
| 删除 | **物理删除 `db.delete()`**；MVP 不需要回收站（软删除已砍） |
| 命名 / 时间 / 枚举 | `snake_case`；主键统一 `id`；时间存 UTC naive（DB/ORM 生成）；状态列 `StrEnum` + `String` |
| 状态机 | 顺序迁移 + 显式 if 守卫；失败必须记 `error_stage` 与 `error_msg` |

## 2. Phase 1 表结构（2 张）

### 2.1 test_cases（用例表）

| 字段 | 类型 | 约束/默认 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK, autoincrement | |
| name | VARCHAR(255) | NOT NULL | 用例名 |
| method | VARCHAR(16) | NOT NULL | GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS（白名单校验） |
| path | VARCHAR(1024) | NOT NULL | 接口路径（不含 base_url） |
| operation_id | VARCHAR(255) | NOT NULL, **索引** | 血缘映射（创建时 Schema 必填，③-1；Phase 2 影响分析依据） |
| params | JSON | NULL | query 参数 |
| body | JSON | NULL | 请求体 |
| expected_status | INTEGER | NOT NULL DEFAULT 200 | 期望状态码（MVP 只校验它） |
| assertions | JSON | NULL | MVP 留空；Phase 3 填 status/field/business |
| status | VARCHAR(16) | NOT NULL DEFAULT 'draft' | draft/active/archived（StrEnum） |
| source | VARCHAR(16) | NOT NULL DEFAULT 'manual' | manual/ai/swagger |
| created_at / updated_at | DATETIME | NOT NULL, server_default=now / onupdate | |

索引：`idx_test_cases_operation_id(operation_id)`、`idx_test_cases_status(status)`。

### 2.2 tasks（执行任务表，结果直接落 result_summary JSON）

| 字段 | 类型 | 约束/默认 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK | |
| run_id | VARCHAR(64) | NOT NULL, **UNIQUE** | `sha256(sorted case_ids + timeout_seconds)` 指纹（Lookup-Create 幂等键） |
| case_ids | JSON | NOT NULL | 用例 id 快照（执行时冻结） |
| status | VARCHAR(16) | NOT NULL DEFAULT 'pending' | pending/running/success/failed（StrEnum） |
| pid | INTEGER | NULL | **subprocess 主进程 PID，超时劫持权威 kill 依据**（mark_running 写入） |
| celery_task_id | VARCHAR(64) | NULL | Celery task uuid |
| error_stage | VARCHAR(32) | NULL | parse/subprocess/timeout（失败阶段定位） |
| error_msg | TEXT | NULL | 失败原因（含 stdout 尾部） |
| started_at / finished_at | DATETIME | NULL | |
| result_summary | JSON | NULL | `{total, passed, failed, skipped, duration_ms, results:[{case_id, status, failure_msg}]}`（name 由接口层默认空串兜底，junit 不提取） |
| report_link | VARCHAR(1024) | NULL | `/static/reports/{task_id}/report.html`（简单 HTML 报告） |
| created_at / updated_at | DATETIME | NOT NULL, server_default=now / onupdate | |

索引：`idx_tasks_status(status)`、`UNIQUE(run_id)`。

> **Lookup-Create 模式**：`POST /api/v1/tasks` 先算 `run_id`，查 `tasks.run_id` 已存在则**直接返回已有任务**（不重复执行）；否则创建 + 派发。SQLite `UNIQUE(run_id)` 兜底并发。

## 3. 状态机

### 3.1 test_cases

```
draft ──(人工 confirm, 传入 reviewer, 重新校验)──▶ active
draft/active ──(删除)──▶ 物理删除
```

- AI 生成恒为 `draft`，禁止任何自动化路径直接置 active（防幻觉护栏，RULES.md §11.2）。
- 执行引擎**只能选中 active** 用例。

### 3.2 tasks

```
pending ──▶ running ──▶ success
                ├──▶ failed（error_stage + error_msg）
pending/running ──(超时劫持 scan_stale_tasks)──▶ failed（error_stage=timeout，仅人工重试）
```

顺序迁移 + 显式 if 守卫；失败必记 `error_stage`/`error_msg`。

## 4. 迁移策略

- **MVP（Phase 1）**：`init_db()` 调 `Base.metadata.create_all(engine)`，main lifespan 启动时同步执行一次（阻塞无妨）。测试库用内存 SQLite + create_all。
- **Phase 4**：切 Alembic（`alembic init` + baseline + autogenerate），RULES.md §5.1 已放宽标注。

## 5. 关键设计决策与理由

| 决策 | 理由 |
| --- | --- |
| `operation_id` NOT NULL + 索引 | Phase 2 影响分析血缘（面试前瞻卖点：Diff+SQL 反向检索） |
| `tasks.pid` | 超时劫持权威 kill 依据——scan 从 DB 读 pid 杀整棵树 |
| `run_id` = sha256 + UNIQUE（Lookup-Create） | 防重复提交：存在即返回，SQLite 唯一约束兜底（面试锚点） |
| `case_ids` JSON 快照 | 执行冻结语义：运行中用例被改/删不污染结果 |
| 结果落 `result_summary` JSON（无 CaseResult 表） | Phase 1 简化：总览即可；Phase 2 需逐用例明细时再拆表 |
| base_url 写死 `config/settings.yaml`（无 Environment 表） | MVP 简化：Phase 2 再补多环境管理 |

## 6. Phase 2+ 表（延迟，当前不建）

- **environments**（Phase 2 多环境管理）：name/base_url/global_headers/timeout_seconds。
- **case_results**（Phase 2 需逐用例明细时再拆）：task_id/case_id 外键，UNIQUE(task_id, case_id) 幂等。
- **api_definitions / impact_analyses**（Phase 2 影响分析）：Swagger 版本快照 + operation_hashes + diff 结果。
- **generation_log**（Phase 3 LLM 成本日志）：usage 明细 + cost_estimate。
