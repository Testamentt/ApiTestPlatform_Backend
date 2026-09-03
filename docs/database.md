# 数据库设计（database.md）· Phase 1-3 简化版

> 规则引用：`RULES.md` §2.1（SQLite 连接工厂与短事务）、§5（数据模型与迁移）。本设计所有字段名/表名与 API、执行引擎文档保持一致。
> **Phase 1-3 简化（面试导向）**：Phase 1 建 `test_cases` + `tasks`（执行闭环）；Phase 2 建 `api_definitions` + `impact_analyses`（影响分析）；Phase 3 建 `generation_tasks` + `generation_logs`（AI 生成）并给 `test_cases`/`impact_analyses` 加字段。软删除 / 多环境 / 结果明细表已砍，后续再补。RULES.md §5 相关 MUST 已放宽为 MVP 例外（见 `RULES.md` §5.1/§5.2）。

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
| trust_score | INTEGER | NOT NULL DEFAULT 100 | **血缘可信度 0-100（Phase 3）**：手工=100、AI 校验通过=80、AI 带 warnings=60；赋值位置：`generation_service._run_generation` 逐 operation 落库时写入；`CaseRead` API 可观测；低分需重点 Review，动态降权 Phase 4 |
| created_at / updated_at | DATETIME | NOT NULL, server_default=now / onupdate | |

索引：`idx_test_cases_operation_id(operation_id)`、`idx_test_cases_status(status)`。

### 2.2 tasks（执行任务表，结果直接落 result_summary JSON）

| 字段 | 类型 | 约束/默认 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK | |
| run_id | VARCHAR(64) | NOT NULL, **UNIQUE** | `sha256(sorted case_ids + timeout_seconds)` 指纹（Lookup-Create 幂等键） |
| case_ids | JSON | NOT NULL | 用例 id 快照（执行时冻结） |
| timeout_seconds | INTEGER | NOT NULL DEFAULT 300 | **任务级执行超时**（review M3/B4）：真正控制 subprocess 超时与僵尸扫描阈值（缺省 `execution.pytest_timeout`），API 可配 1–3600 |
| status | VARCHAR(16) | NOT NULL DEFAULT 'pending' | pending/running/success/failed（StrEnum） |
| pid | INTEGER | NULL | **subprocess 主进程 PID，超时劫持权威 kill 依据**（mark_running 写入） |
| celery_task_id | VARCHAR(64) | NULL | Celery task uuid |
| error_stage | VARCHAR(32) | NULL | parse/subprocess/timeout/internal/dispatch/command（失败阶段定位；command=命令未过白名单，review L7） |
| error_msg | TEXT | NULL | 失败原因（含 stdout 尾部） |
| started_at / finished_at | DATETIME | NULL | |
| result_summary | JSON | NULL | `{total, passed, failed, skipped, duration_ms, results:[{case_id, status, failure_msg}]}`（name 由接口层默认空串兜底，junit 不提取） |
| report_link | VARCHAR(1024) | NULL | `/static/{task_id}/report.html`（简单 HTML 报告） |
| created_at / updated_at | DATETIME | NOT NULL, server_default=now / onupdate | |

索引：`idx_tasks_status(status)`、`UNIQUE(run_id)`。

> **Lookup-Create 模式（review H4 修订）**：`POST /api/v1/tasks` 先算 `run_id`，查 `tasks.run_id`——已 **SUCCESS** 直接复用（不重复执行）；已 **FAILED** 重置 PENDING 重新入队（同输入可重试）；PENDING/RUNNING 返回现状。SQLite `UNIQUE(run_id)` 兜底并发。入队失败（Redis/Celery 不可用）→ 落 `FAILED(error_stage="dispatch")` + 503，不残留 PENDING 孤儿。

### 2.3 api_definitions（Swagger 版本快照，Phase 2 影响分析）

| 字段 | 类型 | 约束/默认 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK, autoincrement | |
| version | VARCHAR(64) | NOT NULL, **UNIQUE** | 用户标签或 auto `v{n}`；**reparse 覆盖**（同 version 再解析先删旧插新，CI 幂等不膨胀） |
| hash_version | INTEGER | NOT NULL DEFAULT 1 | 哈希算法版本（config `swagger.hash_version`）；升级时旧快照不重建，diff 版本不一致公共接口保守全标 changed |
| operation_ids | JSON | NOT NULL | 血缘全集 `[operation_id, ...]` |
| operation_hashes | JSON | NOT NULL | **分段** `{op_id: {"request": md5, "response": md5}}`；response 含状态码指纹（F1：200→202 必命中） |
| operation_contracts | JSON | NOT NULL | `{op_id: {"required": [...], "signature": {field_path: {"type","enum"}}, "response_status_codes": [...]}}`——breaking 联合判定输入（F2/F1：含响应状态码集合，200→202 变化可判 breaking） |
| created_at / updated_at | DATETIME | TimestampMixin | |

索引：`UNIQUE(version)`。

### 2.4 impact_analyses（影响分析结果，Phase 2）

| 字段 | 类型 | 约束/默认 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK, autoincrement | |
| old_version / new_version | VARCHAR(64) | NULL / NOT NULL | 对比版本对（首次分析 old 为空） |
| added_ops / removed_ops / changed_ops | JSON | NOT NULL | 三态集合 |
| breaking_changed_ops | JSON | NOT NULL | **唯一触发回归圈定**（required 收紧/type 变/字段删/枚举删减，F2） |
| affected_case_ids | JSON | NOT NULL | breaking 变更的 **active** 用例 id 快照 |
| affected_count | INTEGER | NOT NULL | 冗余计数；模型层 `@validates("affected_case_ids")` **整体赋值时同步**（写入口收敛整体替换） |
| orphaned_case_ids | JSON | NOT NULL | removed 接口绑定的用例（迁移清单，不自动回归） |
| suggested_remap | JSON | NOT NULL | `{removed_op: added_op}` 相似名配对（difflib，只建议不自动重绑） |
| untested_ops | JSON | NOT NULL | 未绑 active 用例的接口清单（首次=全部，Phase 3 铺路） |
| affected_summary | JSON | NOT NULL | `{total}`（快照期；回归时按执行时真实口径重算） |
| last_regression_at / last_regression_task_id / last_regression_executed_count | DATETIME / VARCHAR / INTEGER | NULL | **回归结果持久化**（每次一键回归更新，可追溯） |
| ai_fix_hint | JSON | NULL | **修复建议（Phase 3 预留）**：breaking 变更的一句话 LLM 建议；`POST /impact/{id}/fix-hints` 按需生成（best-effort），不阻塞 analyze 纯规则秒回 |
| created_at / updated_at | DATETIME | TimestampMixin | |

### 2.5 generation_tasks（AI 生成任务，Phase 3，独立于执行 tasks 表）

| 字段 | 类型 | 约束/默认 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK, autoincrement | |
| run_id | VARCHAR(64) | NOT NULL, **UNIQUE** | `sha256(document + operation_ids)` 指纹，Lookup-Create 幂等（重复提交不重复调 LLM） |
| status | VARCHAR(16) | NOT NULL DEFAULT 'pending' | pending/running/success/failed |
| celery_task_id | VARCHAR(64) | NULL | **已持久化**（P2-3）：`dispatcher.dispatch_generation` 统一入队返回 AsyncResult.id 后写入，卡死时可经 Celery 定位/revoke |
| document | JSON | NOT NULL | 源 Swagger（≤2MB 落库；任务入参只传 task_id，RULES §8.4） |
| operation_ids | JSON | NULL | 定向生成子集；NULL=全量/untested |
| operation_count | INTEGER | NOT NULL DEFAULT 0 | |
| prompt_version | VARCHAR(16) | NULL | 预留列；版本溯源由 `generation_logs.prompt_version` 与 `result_summary.prompt_version` 承载（任务列当前恒 NULL） |
| error_stage / error_msg | VARCHAR/TEXT | NULL | parse/internal/timeout/dispatch（单接口失败不入任务级，记 generation_logs） |
| result_summary | JSON | NULL | `{generated, draft_created, rejected, rejected_detail:[{operation_id, reason}], skipped_by_filter, skipped_detail, prompt_version, cost_total}` |
| started_at / finished_at | DATETIME | NULL | |
| created_at / updated_at | DATETIME | TimestampMixin | |

索引：`UNIQUE(run_id)`、`idx_generation_tasks_status(status)`。

### 2.6 generation_logs（逐 operation LLM 调用日志，成本 + 置信度）

| 字段 | 类型 | 约束/默认 | 说明 |
| --- | --- | --- | --- |
| id | INTEGER | PK | |
| generation_task_id | INTEGER | **可空**, FK + index | 关联生成任务；**fix-hints 的 LLM 调用无生成任务 → NULL**（model='fix_hint' 区分来源） |
| operation_id | VARCHAR(255) | NOT NULL | 本次生成接口（fix-hints 场景恒 'fix_hint'） |
| model / prompt_version | VARCHAR | NOT NULL | 区分来源（生成=model，建议=fix_hint） |
| status | VARCHAR(16) | NOT NULL | success / validation_failed / error |
| ai_confidence | FLOAT | NOT NULL DEFAULT 1.0 | 置信度（复用 UI 项目体系：校验通过=1.0、失败=0.0） |
| usage | JSON | NULL | `{prompt_tokens, completion_tokens, total_tokens}` |
| latency_ms | INTEGER | NULL | |
| cost_estimate | FLOAT | NULL | `total_tokens × llm.cost_per_1k_tokens / 1000`（估算） |
| raw_response | TEXT | NULL | 校验失败时保存 LLM 原始响应（可追溯） |
| error_msg | TEXT | NULL | **具体校验错误**（如「字段 expected_status 类型错误」） |
| created_at / updated_at | DATETIME | TimestampMixin | |

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
pending/running ──(超时劫持 scan_stale_tasks)──▶ failed（error_stage=timeout）
failed ──(同输入重新提交 POST /tasks，review H4)──▶ pending（重置后重新入队）
pending ──(入队失败，review M2)──▶ failed（error_stage=dispatch）
```

顺序迁移 + 显式 if 守卫；失败必记 `error_stage`/`error_msg`；扫描本身不自动重试（重试由用户同输入重新提交触发）。

## 4. 迁移策略

- **MVP（Phase 1-4 现状，无增量迁移）**：`init_db()` 调 `Base.metadata.create_all(engine)`，main lifespan 启动时同步执行一次（阻塞无妨）。测试库用内存 SQLite + create_all。
- **Alembic 未纳入**（Phase 4 已确认维持 create_all，见 `sessions/2026-08-10-phase4-production.md`）：**create_all 只建不存在的表，不会 ALTER 已存在表**。模型新增列后，旧 dev 库缺列 → 接口 500（2026-08-10 实测 `trust_score` 漂移导致 `GET /cases` 500）。
- **处置标准（模型变更后二选一）**：
  1. **重置（推荐）**：`scripts\reset_db.bat` 删除 `data/platform.db*`，重启后 create_all 重建全量新 schema。dev 数据可丢弃（`data/` 已 gitignore）。
  2. **无损补列（需保留数据）**：手动 `ALTER TABLE <t> ADD COLUMN <col> <type> <default>`（trust_score 即用此法：`INTEGER NOT NULL DEFAULT 100`）。
- 新环境（clone 后 `data/` 为空）首次启动 create_all 建全库，不会踩；踩只发生在「本地旧库 + 新模型」。
- **复杂变更**（改列/删列/加约束）create_all 无法表达，需人工迁移或另行评估 Alembic（当前未纳入，面试导向权衡见 §1）。

## 5. 关键设计决策与理由

| 决策 | 理由 |
| --- | --- |
| `operation_id` NOT NULL + 索引 | Phase 2 影响分析血缘（面试前瞻卖点：Diff+SQL 反向检索） |
| `tasks.pid` | 超时劫持权威 kill 依据——scan 从 DB 读 pid 杀整棵树 |
| `run_id` = sha256 + UNIQUE（Lookup-Create） | 防重复提交：SUCCESS 复用 / FAILED 重置重试（review H4），SQLite 唯一约束兜底（面试锚点） |
| `case_ids` JSON 快照 | 执行冻结语义：运行中用例被改/删不污染结果 |
| 结果落 `result_summary` JSON（无 CaseResult 表） | Phase 1 简化：总览即可；Phase 2 需逐用例明细时再拆表 |
| base_url 写死 `config/settings.yaml`（无 Environment 表） | MVP 简化：Phase 2 再补多环境管理 |
| 分段 hash + operation_contracts（api_definitions） | O(1) diff（只存 hashes 不存原文）+ breaking 联合判定（F1/F2） |
| `affected_case_ids` 快照 + `last_regression_*`（impact_analyses） | 回归依据冻结 + 结果持久化可追溯（F3）；count 由 @validates 写时同步 |
| reparse 覆盖（version UNIQUE） | 同 version 再解析先删旧插新，CI 重复触发不膨胀版本表 |

## 6. 后续表（延迟，当前不建）

- **environments**（多环境管理）：name/base_url/global_headers/timeout_seconds。
- **case_results**（需逐用例明细时再拆）：task_id/case_id 外键，UNIQUE(task_id, case_id) 幂等。
- **api_definitions / impact_analyses / generation_tasks / generation_logs**：**Phase 2/3 已建**（见 §2.3-§2.6）。
