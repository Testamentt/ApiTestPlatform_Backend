# 待办清单（TODO.md）

> 按里程碑分组（面试导向，见 [roadmap.md](roadmap.md)）。完成即勾选并关联 `docs/sessions/` 沉淀（RULES.md §17-R5）。

## Phase 0 · 文档与基建（已完成）

- [x] 项目文档（architecture / database / api / execution-engine / ai-generation / impact-analysis / configuration / roadmap / TODO）
- [x] 规则与配置契约（.claude/rules/RULES.md §1-§18 + config/settings.example.yaml + .env.example）
- [x] 双仓库结构（backend/ + frontend/，frontend Phase 4 可选）
- [x] 用户评审确认全部文档（进入编码的闸门）→ 2026-08-06 确认进入 Phase 1

## Phase 1 · 核心执行闭环（已完成，MVP 简化）

> MVP 简化（面试导向）：砍掉 Alembic（create_all 替代）、request_id 中间件、Allure（HTML 报告替代）、每 5 分钟扫描（仅启动扫描一次）、`{{base_url}}` 环境管理（base_url 写死 config）。沉淀：[docs/sessions/2026-08-06-phase1-mvp.md](sessions/2026-08-06-phase1-mvp.md)。

- [x] pyproject.toml（依赖 + ruff + pytest 配置）+ app/ 分层骨架
- [x] 配置加载（BaseModel 手动合并 yaml/env + get_engine + 日志 + AppError）
- [x] 用例 CRUD + confirm 审核（operation_id 必填 ③-1；draft→active 防幻觉护栏）
- [x] 执行引擎：run_cmd（白名单 + timeout + on_start 落 pid）+ case_generator + junit_parser（累加 testsuite）+ report_util（HTML）
- [x] Celery + Redis 异步化（execute_cases + scan_stale_tasks 启动扫描，无 Beat）
- [x] Lookup-Create 幂等（run_id=sha256 + UNIQUE，存在即返回）
- [x] tests/unit + tests/api + tests/tasks（fakes: FakeSubprocess）+ 31 用例全绿 + ruff 全绿
- [x] 验收：/docs「创建用例 → confirm → 触发执行(202) → 轮询 → results + HTML 报告」闭环 + 真实异步端到端验证

## Phase 2 · 变更影响分析（已完成，核心卖点 1）

> 纯规则引擎（无 AI）。沉淀：[docs/sessions/2026-08-08-review-fixes.md](sessions/2026-08-08-review-fixes.md)。

- [x] api_definitions 表（operation_id / path / method / request_schema_hash，`hashlib.md5` 指纹）
- [x] `POST /api/v1/parse`：Swagger 解析入库（$ref / allOf / oneOf 递归 + reparse 覆盖）
- [x] `POST /api/v1/impact/analyze`：schema_hash 对比 → 变更 operation_id 列表 + breaking 五场景判定
- [x] 反向检索：`SELECT * FROM test_cases WHERE operation_id IN (...)`（核心 SQL）
- [x] 一键回归：受影响用例创建执行任务（复用 Phase 1 引擎，宽容降级 + 真实口径 + last_regression）

## Phase 3 · AI 智能生成（1 周，核心卖点 2）

- [ ] OpenAPI 解析器完善（required / enum / format / min-max）
- [ ] prompts/v1/ + 占位符注入 + 版本一致性断言
- [ ] llm_client（结构化输出 + 重试 + generation_log）
- [ ] generate_cases 任务 + draft 落库 + 校验失败落库
- [ ] confirm 审核接口 + AI 采纳率埋点

## Phase 4 · 生产化与前端增强（可选，锦上添花）

- [ ] Docker Compose（FastAPI + Redis + Worker + SQLite）
- [ ] GitHub Actions 两段式 CI
- [ ] 基础鉴权（Bearer Token，值在配置中；方案 A：Phase 1 无鉴权，此处补上）
- [ ] （可选）PostgreSQL 迁移 / JWT 鉴权
- [ ] （可选）Vue 前端：仅用例列表 + 任务看板两页
- [ ] 不做：自愈看板（AI UI 项目卖点，不重复造轮子）
