# 待办清单（TODO.md）

> 按里程碑分组（面试导向，见 [roadmap.md](roadmap.md)）。完成即勾选并关联 `docs/sessions/` 沉淀（RULES.md §17-R5）。

## Phase 0 · 文档与基建（当前）

- [x] 项目文档（architecture / database / api / execution-engine / ai-generation / impact-analysis / configuration / roadmap / TODO）
- [x] 规则与配置契约（.claude/rules/RULES.md §1-§18 + config/settings.example.yaml + .env.example）
- [x] 双仓库结构（backend/ + frontend/，frontend Phase 4 可选）
- [ ] 用户评审确认全部文档（**进入编码的闸门**）

## Phase 1 · 核心执行闭环（2 周，重中之重）

- [ ] pyproject.toml（依赖 + ruff + pytest 配置）+ app/ 分层骨架
- [ ] Alembic 初始化 + baseline migration
- [ ] 配置加载（pydantic-settings + get_engine + 日志 + request_id 中间件 + AppError）
- [ ] 用例 CRUD + 环境管理（`{{base_url}}` 替换）
- [ ] 本地同步执行（先不加 Celery）：API 直接调 subprocess 跑 pytest
- [ ] Celery + Redis：execute_cases 异步化（broker/backend 配置）
- [ ] 超时劫持：scan_stale_tasks（Worker 启动 + 每 5 分钟）
- [ ] JUnit XML 解析 + 结果回写 DB + Allure 报告链接
- [ ] 基础鉴权（Bearer Token，值在配置中；不做限流 / OAuth2）
- [ ] tests/unit + tests/api + tests/tasks（fakes: FakeSubprocess）+ CI 两段式
- [ ] 验收：/docs 上「创建用例 → 触发执行 → 轮询 task_id → pass/fail」闭环

## Phase 2 · 变更影响分析（1 周，核心卖点 1）

- [ ] api_definitions 表（operation_id / path / method / request_schema_hash，`hashlib.md5` 指纹）
- [ ] `POST /api/v1/parse`：Swagger 解析入库（$ref / allOf / oneOf 递归）
- [ ] `POST /api/v1/impact/analyze`：schema_hash 对比 → 变更 operation_id 列表
- [ ] 反向检索：`SELECT * FROM test_cases WHERE operation_id IN (...)`（核心 SQL）
- [ ] 一键回归：受影响用例创建执行任务（复用 Phase 1 引擎）

## Phase 3 · AI 智能生成（1 周，核心卖点 2）

- [ ] OpenAPI 解析器完善（required / enum / format / min-max）
- [ ] prompts/v1/ + 占位符注入 + 版本一致性断言
- [ ] llm_client（结构化输出 + 重试 + generation_log）
- [ ] generate_cases 任务 + draft 落库 + 校验失败落库
- [ ] confirm 审核接口 + AI 采纳率埋点

## Phase 4 · 生产化与前端增强（可选，锦上添花）

- [ ] Docker Compose（FastAPI + Redis + Worker + SQLite）
- [ ] GitHub Actions 两段式 CI
- [ ] （可选）PostgreSQL 迁移 / JWT 鉴权
- [ ] （可选）Vue 前端：仅用例列表 + 任务看板两页
- [ ] 不做：自愈看板（AI UI 项目卖点，不重复造轮子）
