# 待办清单（TODO.md）

> 按里程碑分组（面试导向，见 [roadmap.md](roadmap.md)）。完成即勾选并关联 `docs/sessions/` 沉淀（RULES.md §17-R5）。

## Phase 0 · 文档与基建（已完成）

- [x] 项目文档（architecture / database / api / execution-engine / ai-generation / impact-analysis / configuration / roadmap / TODO）
- [x] 规则与配置契约（RULES.md §1-§18 + config/settings.example.yaml + .env.example）
- [x] 双仓库结构（backend/ + frontend/，frontend Phase 4 已实现）
- [x] 用户评审确认全部文档（进入编码的闸门）→ 2026-08-06 确认进入 Phase 1

## Phase 1 · 核心执行闭环（已完成，MVP 简化）

> MVP 简化（面试导向）：砍掉 Alembic（create_all 替代）、request_id 中间件、Allure（HTML 报告替代）、每 5 分钟扫描（仅启动扫描一次）、`{{base_url}}` 环境管理（base_url 写死 config）。沉淀：[docs/sessions/2026-08-06-phase1-mvp.md](sessions/2026-08-06-phase1-mvp.md)。

- [x] pyproject.toml（依赖 + ruff + pytest 配置）+ app/ 分层骨架
- [x] 配置加载（BaseModel 手动合并 yaml/env + get_engine + 日志 + AppError）
- [x] 用例 CRUD + confirm 审核（operation_id 必填 ③-1；draft→active 防幻觉护栏）
- [x] 执行引擎：run_cmd（白名单 + timeout + on_start 落 pid）+ case_generator + junit_parser（累加 testsuite）+ report_util（HTML）
- [x] Celery + Redis 异步化（execute_cases + scan_stale_tasks 启动扫描，无 Beat）
- [x] Lookup-Create 幂等（run_id=sha256 + UNIQUE；SUCCESS 复用 / FAILED 可重试，review H4 修订）
- [x] tests/unit + tests/api + tests/tasks（fakes: FakeSubprocess）+ 31 用例全绿 + ruff 全绿
- [x] 验收：/docs「创建用例 → confirm → 触发执行(202) → 轮询 → results + HTML 报告」闭环 + 真实异步端到端验证

## Phase 2 · 变更影响分析（已完成，核心卖点 1）

> 纯规则引擎（无 AI）。沉淀：[docs/sessions/2026-08-08-review-fixes.md](sessions/2026-08-08-review-fixes.md)。

- [x] api_definitions 表（operation_id / path / method / request_schema_hash，`hashlib.md5` 指纹）
- [x] `POST /api/v1/parse`：Swagger 解析入库（$ref / allOf / oneOf 递归 + reparse 覆盖）
- [x] `POST /api/v1/impact/analyze`：schema_hash 对比 → 变更 operation_id 列表 + breaking 五场景判定
- [x] 反向检索：`SELECT * FROM test_cases WHERE operation_id IN (...)`（核心 SQL）
- [x] 一键回归：受影响用例创建执行任务（复用 Phase 1 引擎，宽容降级 + 真实口径 + last_regression）

## Phase 3 · AI 智能生成（已完成，核心卖点 2）

> 实现 + 评审修复（软超时捕获/终态兜底/fix-hints 审计与 prompt 外置/长度预检/文档字段剥离/celery_task_id/e2e 层），174 测试全绿 + ruff 全绿。

- [x] 回合 1：文档锁定（ai-generation/configuration/database/api/architecture/roadmap + config 三件套 + .env.example llm 段）
- [x] 回合 2：代码生成清单确认
- [x] Step 1 依赖 + config（openai SDK + LlmSettings 全带 default）
- [x] Step 2 models（generation_tasks + generation_log + test_cases.trust_score + impact_analyses.ai_fix_hint）
- [x] Step 3 prompts/v1/（含 fix-hints 模板）+ 版本一致性断言
- [x] Step 4 llm_client（_extract_json + 重试 + 长度预检 + usage + 成本）
- [x] Step 5 generation_service（幂等 + 定向优先级 + trust_score/confidence/rejected_detail + 异常兜底）
- [x] Step 6 generate_cases_task（soft/time_limit + 软超时捕获）+ api（POST/GET generate + fix-hints）
- [x] Step 7 api/tasks 测试（202/幂等/422/空文档 failed(parse)/校验失败落库/fix-hints 审计与注入防护）
- [x] Step 8 验证 + 3.5-C 核对 + 沉淀 + 提交
- [x] confirm 审核接口（Phase 1 复用，AI 用例 draft → active）

## Phase 4 · 生产化与前端增强（已完成）

- [x] Docker Compose（FastAPI + Redis + Worker + SQLite，named volume + 非 root + solo 单写者）
- [x] GitHub Actions 三 job（门禁 ruff + pytest + coverage 60/80 + slow e2e + docker-build 验证）
- [x] 基础鉴权（Bearer Token，值在配置中；401/403 区分，health 免鉴权，/docs 开关）
- [x] Vue 前端 4 页（仪表盘/用例管理/任务执行/AI 生成）+ 41 单测 + 构建门禁（dev 走 Vite 代理）
- [ ] （可选延后）PostgreSQL 迁移 / JWT 鉴权
- [ ] 不做：自愈看板（AI UI 项目卖点，不重复造轮子）

## 2026-08-12 · 全量 Code Review 修复（批次 A/B，已完成）

> 评审：[docs/reviews/2026-08-12-full-code-review.md](reviews/2026-08-12-full-code-review.md)；计划：[docs/plans/2026-08-12-fix-plan.md](plans/2026-08-12-fix-plan.md)。

- [x] H1 命令白名单 `python3*` 前缀匹配（容器执行路径）；H2 HTML 报告转义 + /static 收敛 reports/；H3 `scan_stale_tasks` 覆盖生成任务；H4 FAILED 任务同输入可重试（Lookup-Create 语义修订）
- [x] M1 前端时间补 Z（UTC naive 显示偏移）；M2 入队失败落 FAILED(dispatch) + 503；M3/B4 `tasks.timeout_seconds` 真正控制执行超时（dev 库已 reset）；M5 `page_size`≤100 / `case_ids`≤500；M7 仪表盘按 status 统计
- [x] 后端 6 commit + 前端 2 commit（每 commit 独立跑通门禁）；174+1 pytest、41 vitest、ruff、91% 覆盖率全绿

## 2026-08-·批次 C（Minor 加固，已全部完成）

> 沉淀：[docs/sessions/2026-08-batch-c.md](sessions/2026-08-batch-c.md)；评审明细 [reviews/2026-08-12-full-code-review.md](reviews/2026-08-12-full-code-review.md) §3.3。

- [x] L1 health redis close；L2 `hmac.compare_digest` 常数时间 token 比对；L3 合同指纹 MD5→sha256（旧数据 re-parse 覆盖）
- [x] L4 generation_task_repository commit 补 rollback 包装（与 TaskRepository 对齐）；L5 `_BOUNDARY_RULES` 外置 `prompts/v1/boundary_rules.md`
- [x] L6 openapi 递归深度限制（>50 截断 + warning）；L7 执行错误分类（command/timeout/subprocess 分阶段）；L8 `GeneratedCase` max_length 对齐表列长
- [x] L9 前端响应解包改类型安全 `request<T>`（去双断言）；L10 frontend CI（.github/workflows/ci.yml）
- [x] L11 Python 版本统一 3.12+（RULES 对齐 pyproject）；L12 api/tasks/integration marker 声明即用（conftest 自动归类）
- [x] request_id 全链路追踪中间件（§6.2 明文要求）：X-Request-ID 生成/透传/响应头回写 + ContextVar + 日志 Filter + Celery 任务与 LLM 日志透传
