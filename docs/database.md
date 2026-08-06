# 数据库设计（database.md）

> 规则引用：[`.claude/rules/RULES.md`](../.claude/rules/RULES.md) §2.1（SQLite 连接工厂与短事务）、§5（数据模型与迁移）。本设计所有字段名/表名与 API、执行引擎文档保持一致。

## 1. 设计原则（对齐 RULES.md）

| 原则 | 要求 |
| --- | --- |
| 连接工厂 | 所有 SQLite 连接必须经唯一 `get_engine()` 创建，connect 事件统一执行 `PRAGMA journal_mode=WAL` / `busy_timeout=5000` / `foreign_keys=ON` |
| 迁移 | 建表/改表全部走 **Alembic**；禁止 `Base.metadata.create_all` 作为运行期建表手段（仅测试可用） |
| 短事务 | 禁止持有 Session 期间调用 LLM/subprocess/HTTP；统一「先查→提交关事务→算→再开新事务写入」；批量写用 `add_all` |
| 命名 | `snake_case`；主键统一 `id`(INTEGER 自增)；外键 `{目标表}_id` + 索引 |
| 时间 | 一律存 UTC naive datetime，由 DB/ORM 生成，应用层不传时间 |
| 枚举 | 状态列用 `StrEnum` + `String` + 代码层校验（SQLite 无原生 ENUM） |
| 软删除 | `is_deleted`(Boolean, default False) + `deleted_at`；软删除表唯一约束用部分索引规避冲突；repository 统一带 `is_deleted == False` 过滤 |
| 状态机 | 顺序迁移，显式 if 守卫，禁止任意跳转；失败必须记录 `error_stage` 与 `error_msg` |

## 2. ER 关系图

```
environments 1 ──────── N test_cases   (env_id 默认执行环境, 可空)
environments 1 ──────── N tasks        (env_id 执行环境, 执行任务必填)
tasks        1 ──────── N case_results (task_id, CASCADE)
test_cases   1 ──────── N case_results (case_id)
api_definitions 1 ───── N impact_analyses (new_definition_id / old_definition_id)
tasks        1 ──────── N generation_log (task_id)   [LLM 成本日志, 可空]
[逻辑血缘, 非外键]  test_cases.operation_id ∈ api_definitions.operation_ids
```

## 3. 表结构

### 3.1 test_cases（用例表）

| 字段 | 类型 | 约束/默认 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK, autoincrement | |
| name | VARCHAR(255) | NOT NULL | 用例名 |
| method | VARCHAR(16) | NOT NULL | GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS |
| path | VARCHAR(1024) | NOT NULL | 接口路径（不含 base_url） |
| operation_id | VARCHAR(255) | NOT NULL, **索引** | 静态血缘映射，影响分析反向检索依据（Phase 1 Schema 强制必填，③-1） |
| api_version | VARCHAR(64) | NULL | 基于哪个 api_definitions.version 生成 |
| request_schema | JSON | NULL | `{params, query, headers, body}`，执行时渲染请求 |
| expected_status | INTEGER | NOT NULL DEFAULT 200 | 期望状态码 |
| assertions | JSON | NOT NULL DEFAULT '[]' | `[{type, path, expected}]`，type ∈ status/field/business |
| tags | JSON | NULL | `["P0","login",...]` 优先级与模块标签 |
| status | VARCHAR(16) | NOT NULL DEFAULT 'draft' | **draft/active/archived**（StrEnum） |
| source | VARCHAR(16) | NOT NULL DEFAULT 'manual' | manual/ai/swagger |
| env_id | INTEGER | NULL, FK environments.id | 默认执行环境（任务级可覆盖） |
| created_by / updated_by | VARCHAR(64) | NULL | 审计 |
| is_deleted | BOOLEAN | NOT NULL DEFAULT False | 软删除 |
| deleted_at | DATETIME | NULL | 软删除时间 |
| created_at / updated_at | DATETIME | NOT NULL, server_default=now / onupdate | |

索引：`idx_test_cases_operation_id(operation_id)`、`idx_test_cases_status(status)`、`idx_test_cases_method_path(method, path)`、`idx_test_cases_source(source)`。

### 3.2 tasks（执行任务表，RULES.md §8.3 业务状态表）

| 字段 | 类型 | 约束/默认 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK | |
| run_id | VARCHAR(64) | NOT NULL, **UNIQUE** | 幂等键：任务类型 + 输入指纹哈希 |
| task_type | VARCHAR(32) | NOT NULL | execute_cases / generate_cases / impact_analyze |
| title | VARCHAR(255) | NULL | 任务名 |
| env_id | INTEGER | NULL, FK environments.id | 执行类任务必填 |
| input_fingerprint | VARCHAR(255) | NOT NULL | `task_type + params_hash`，配合 run_id 唯一约束去重 |
| case_ids | JSON | NOT NULL | 用例 id 快照（执行时冻结，防运行中改删污染） |
| status | VARCHAR(16) | NOT NULL DEFAULT 'pending' | **pending/running/success/failed/cancelled**（StrEnum，顺序迁移） |
| celery_task_id | VARCHAR(64) | NULL, UNIQUE | Celery task uuid |
| pid | INTEGER | NULL | **subprocess 主进程 PID，超时劫持 kill 依据** |
| trigger | VARCHAR(16) | NOT NULL DEFAULT 'manual' | manual/webhook/scheduled/regression |
| trigger_source | VARCHAR(255) | NULL | webhook 来源 commit_sha / analysis_id |
| timeout_seconds | INTEGER | NOT NULL DEFAULT 300 | 该任务超时阈值（来自 config） |
| retry_count | INTEGER | NOT NULL DEFAULT 0 | 手动重试次数 |
| error_stage | VARCHAR(32) | NULL | parse/llm/save/subprocess（失败阶段定位） |
| error_msg | TEXT | NULL | 失败原因 |
| started_at / finished_at | DATETIME | NULL | |
| result_summary | JSON | NULL | `{total, passed, failed, skipped, killed, duration_ms}` |
| allure_link | VARCHAR(1024) | NULL | 任务级 Allure 报告链接 |
| created_at / updated_at | DATETIME | NOT NULL, server_default=now / onupdate | |

索引：`idx_tasks_status(status)`、`idx_tasks_created_at(created_at DESC)`、`UNIQUE(celery_task_id)`、`idx_tasks_trigger(trigger)`。

> **注意**：`run_id` 唯一约束由 `task_type + input_fingerprint` 生成，重复/并发入队命中唯一键直接复用已存在任务（RULES.md §8.3）。

### 3.3 case_results（单用例执行结果表）

| 字段 | 类型 | 约束/默认 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK | |
| task_id | INTEGER | NOT NULL, FK tasks.id CASCADE, **索引** | |
| case_id | INTEGER | NOT NULL, FK test_cases.id, **索引** | |
| status | VARCHAR(16) | NOT NULL | pass/fail/skipped/error/killed |
| duration_ms | INTEGER | NULL | |
| stdout | TEXT | NULL | 捕获输出（截断上限来自 config，默认 ≤10MB） |
| failure_msg | TEXT | NULL | 断言失败/异常信息 |
| junit_xml | TEXT | NULL | 原始 JUnit XML 片段（溯源） |
| assertions_checked / assertions_passed | INTEGER | NULL DEFAULT 0 | 断言统计 |
| allure_uuid | VARCHAR(64) | NULL | Allure 关联 |
| created_at | DATETIME | NOT NULL, server_default=now | |

索引：`idx_case_results_task_id`、`idx_case_results_case_id`、`idx_case_results_status`；**UNIQUE(task_id, case_id)**（幂等，重跑前先删旧结果）。

### 3.4 environments（环境表）

| 字段 | 类型 | 约束/默认 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK | |
| name | VARCHAR(64) | NOT NULL UNIQUE | dev/staging/prod |
| description | VARCHAR(255) | NULL | |
| base_url | VARCHAR(1024) | NOT NULL | |
| global_headers | JSON | NULL | 认证头模板，支持 `{token}` 占位符 |
| timeout_seconds | INTEGER | NOT NULL DEFAULT 300 | 环境级超时默认值 |
| is_active | BOOLEAN | NOT NULL DEFAULT True | |
| created_at / updated_at | DATETIME | NOT NULL, server_default=now / onupdate | |

### 3.5 api_definitions（Swagger 版本快照表，影响分析数据源）

| 字段 | 类型 | 约束/默认 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK | |
| version | VARCHAR(64) | NOT NULL UNIQUE | 版本号（用户标签或 commit_sha） |
| source | VARCHAR(16) | NOT NULL | upload/webhook/manual |
| commit_sha | VARCHAR(64) | NULL | git commit，webhook 去重 |
| repo_url | VARCHAR(1024) | NULL | webhook 来源仓库 |
| content | TEXT | NOT NULL | 完整 OpenAPI 3.0 JSON 快照 |
| operation_ids | JSON | NOT NULL | 提取的 operationId 集合（含 method/path/summary 轻量索引） |
| operation_hashes | JSON | NOT NULL | `{operation_id: content_hash}`，加速 diff |
| status | VARCHAR(16) | NOT NULL DEFAULT 'active' | active/archived |
| created_at | DATETIME | NOT NULL, server_default=now | |

索引：`idx_api_definitions_version(version)`、`idx_api_definitions_commit(commit_sha)`。

### 3.6 impact_analyses（影响分析结果表）

| 字段 | 类型 | 约束/默认 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK | |
| old_definition_id / new_definition_id | INTEGER | FK api_definitions.id | 新旧快照 |
| added_ops / removed_ops / changed_ops | JSON | NULL | 三态 operationId 集合 |
| affected_case_ids | JSON | NOT NULL | 受影响用例 id 快照 |
| affected_summary | JSON | NULL | 按状态/优先级统计 |
| status | VARCHAR(16) | NOT NULL DEFAULT 'pending' | pending/completed/failed |
| error_msg | TEXT | NULL | |
| created_at | DATETIME | NOT NULL, server_default=now | |

索引：`idx_impact_analyses_new(new_definition_id)`。

### 3.7 generation_log（LLM 成本日志，可选表）

| 字段 | 类型 | 约束/默认 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK | |
| task_id | INTEGER | NULL, FK tasks.id, 索引 | 关联生成任务 |
| model | VARCHAR(64) | NOT NULL | 实际调用模型 |
| prompt_version | VARCHAR(32) | NOT NULL | prompts/ 目录版本 |
| latency_ms | INTEGER | NOT NULL | |
| prompt_tokens / completion_tokens / total_tokens | INTEGER | NOT NULL | usage 明细 |
| cost_estimate | NUMERIC | NULL | 估算成本 |
| retried | BOOLEAN | NOT NULL DEFAULT False | 是否经历重试 |
| created_at | DATETIME | NOT NULL, server_default=now | |

> 来源：RULES.md §9.6（llm_client 每次成功调用后记录 usage 明细）。

## 4. 状态机

### 4.1 test_cases

```
draft ──(人工审核 confirm, 传入 reviewer, 重新 schema 校验)──▶ active
draft/active ──(归档)──▶ archived
active ──(人工编辑/删除)──▶ draft / archived（软删除 is_deleted=True）
```

- AI 生成恒为 `draft`，**禁止任何自动化路径直接置 active**（RULES.md §11.2，防幻觉护栏）。
- 执行引擎**只能选中 active** 用例。

### 4.2 tasks

```
pending ──▶ running ──▶ success
                ├──▶ failed（error_stage + error_msg）
                ├──▶ cancelled（人工取消/超时强杀）
pending/running ──(僵尸: 心跳超时)──▶ failed（原因 worker_restart/timeout, 仅人工重试）
```

顺序迁移 + 显式 if 守卫；禁止任意跳转；失败必记 `error_stage`/`error_msg`（RULES.md §8.3/§8.5）。

### 4.3 case_results

```
pass / fail / skipped / error / killed（单用例终态，无流转）
```

## 5. 迁移策略（Alembic）

1. 初始化：`alembic init` + 一个空 baseline migration。
2. 每次模型变更：`alembic revision --autogenerate` → **人工核对 diff** → `alembic upgrade head`。
3. 部署/启动脚本固定执行 `alembic upgrade head`。
4. 软删除表上的唯一约束用部分索引：`CREATE UNIQUE INDEX ... WHERE is_deleted = 0`。

## 6. 关键设计决策与理由

| 决策 | 理由 |
| --- | --- |
| `operation_id` 索引 + 逻辑血缘 | 影响分析（亮点 ②）核心：`WHERE operation_id IN (...)` 命中索引，<10s、覆盖率 100% |
| `tasks.pid` + `celery_task_id` | 超时劫持（亮点 ③）精确 kill 依据；worker 重启后 scan 仍能按 pid 杀残留 subprocess |
| `case_ids` JSON 快照 | 执行冻结语义：运行中用例被改/删不污染本次结果 |
| `draft/active/archived` 状态机 | AI 防幻觉污染（亮点 ①）：draft 不可执行，人工确认才 active |
| 软删除 `is_deleted` + `deleted_at` | 血缘映射与历史执行结果查询不失效（RULES.md §5.2） |
| `api_definitions` 快照 + `operation_hashes` | diff 无需重新解析，哈希比较 O(1) |
| `case_results` UNIQUE(task_id, case_id) | 幂等写入：worker 崩溃重跑不产生重复行 |
| `tasks` 状态独立于 Celery result backend | 进程隔离（规格要求）：DB 是唯一状态源，Worker 重启任务不丢 |
| `run_id` + `input_fingerprint` 唯一约束 | 重复/并发入队去重，防重复调用 LLM（RULES.md §8.3） |
