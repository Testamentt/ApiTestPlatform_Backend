# 待办清单（TODO.md）

> 按里程碑分组。完成即勾选并关联 `docs/sessions/` 沉淀（RULES.md §17-R5）。

## Phase 0 · 文档沉淀（当前）

- [x] 根级文档 README / memory.md（CLAUDE.md 与 `.claude/rules/RULES.md` 已由既有内容覆盖）
- [x] 锚点文档 architecture / database
- [x] MVP 文档 api / execution-engine / configuration + 配置契约
- [x] AI 与算法文档 ai-generation / impact-analysis
- [x] 过程文档 roadmap / TODO / session-doc-template
- [x] .gitignore + git init + 首次提交
- [ ] 用户评审确认全部文档（进入编码的闸门）

## Phase 1 · MVP 最小闭环

- [ ] pyproject.toml（依赖 + ruff + pytest 配置）
- [ ] app/ 分层骨架（core / models / schemas / repositories / services / api/v1 / tasks / utils）
- [ ] Alembic 初始化 + baseline migration
- [ ] 配置加载（pydantic-settings + get_engine + 日志 + request_id 中间件 + AppError）
- [ ] 用例 CRUD + confirm 审核接口
- [ ] 环境管理接口
- [ ] 执行引擎：case_generator / pytest_runner(run_cmd) / junit_parser / allure
- [ ] Celery：execute_cases + scan_stale_tasks（worker_ready + Beat）
- [ ] API 鉴权（Bearer Token）+ 限流
- [ ] Vue 前端脚手架（frontend/：Vite + Vue 3 + TS + Element Plus + Pinia + Router + Vite 代理）
- [ ] 前端鉴权：token 存储 + Axios 拦截器
- [ ] 前端页面：用例管理（列表/编辑/批量确认）+ draft 审核确认页
- [ ] 前端页面：任务与结果看板（轮询 task_id）+ Allure 报告嵌入
- [ ] tests/unit + tests/api + tests/tasks（fakes: FakeSubprocess）+ CI 两段式
- [ ] 端到端冒烟：parse → 建用例 → 执行 → 结果回写

## Phase 2 · AI 生成

- [ ] OpenAPI 解析器（$ref/allOf/oneOf/array 递归）
- [ ] prompts/v1/ + 占位符注入 + 版本一致性断言
- [ ] llm_client（结构化输出 + 重试 + 限流 + generation_log）
- [ ] generate_cases 任务 + draft 落库 + 校验失败落库

## Phase 3 · 影响分析

- [ ] api_definitions 版本入库 + operation_hashes
- [ ] impact_analyze 任务 + diff + 反向检索
- [ ] Git Webhook 接收 + 一键回归

## Phase 4 · 生产化

- [ ] JWT 鉴权替换 / Docker Compose / PostgreSQL 迁移 / 成本看板 / 自愈看板（Vue 页面）/ 前端生产构建 + 静态托管
