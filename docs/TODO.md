# 待办清单（TODO.md）

> **定位**：按里程碑分组的**完成项台账**（过程记录，不参与双轨镜像）；方向见 [roadmap.md](roadmap.md)，实现细节见各专题文档。
> **状态**：现行有效（追加式，不回改历史条目） ｜ **最后核对**：2026-10-08
> **计数口径**：各历史条目里的用例数为**当时**数值；当前门禁数值一律以 CI 为准（后端 pytest + slow e2e、前端 vitest、ruff、覆盖率），不要引用历史条目里的数字。

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

## 2026-09-01 · 被测系统迁移：管伊佳ERP（已完成）

> 沉淀：[docs/sessions/2026-09-01-jsherp-migration.md](sessions/2026-09-01-jsherp-migration.md)。演示形态二选一：离线 mock（兜底）/ 管伊佳ERP（真实业务系统）。

- [x] Swagger2→OpenAPI3 转换脚本 `scripts/convert_swagger2.py` + 产物 `docs/examples/jsherp-openapi3.json`（320 paths/338 ops，parse 0 warning）+ 解析片段单测
- [x] 执行层鉴权适配：`execution.auth_*` 配置（登录路径/体模板/密码编码 md5/token 头与提取路径）+ workspace 登录 conftest（session 级一次登录，凭证仅 env 引用）+ 用例模板 token 头注入；单测含密钥不落盘断言
- [x] ERP 端到端冒烟：parse → 定向 AI 生成（13 draft/0 rejected）→ confirm → 执行 4/4 passed（真实登录取 X-Access-Token 请求 ERP）+ HTML 报告
- [x] 账号体系专家信息沉淀（2026-09-02 补充）：**admin 仅平台运维用户（不能编辑业务数据，只配菜单/建租户）；业务管理员是租户 jsh**——演示凭证已切 jsh 并重验执行 4/4 passed；知识落点：业务 SKILL `.claude/skills/jsherp-target-domain/`（本地）+ execution-engine.md §8.2（入库）

## 2026-08-31 · 演示稳定性 + H1 闭环 + prompt 断言（已完成）

> 沉淀：[docs/sessions/2026-08-31-demo-stability-h1.md](sessions/2026-08-31-demo-stability-h1.md)；另含 CI lint 门禁修复（ruff format 全仓 + PEP695 + 版本落锁，commit ff05fd3）。

- [x] 本地 mock 目标服务 `scripts/mock_target.py`（httpbin 兼容子集）；.env.example/compose 默认本地 mock，`docker compose up` 全离线；执行冒烟 2/2 passed + HTML 报告（3.4s）
- [x] H1 容器内实测搬进 CI：docker-build job 构建后 `docker run` 挂载 `scripts/verify_container.py` 容器内验证白名单（真实路径/python3.12 前缀/真实执行/越权拒绝四断言）
- [x] prompt v1 断言强约束：每条用例至少 1 条 assertions（system.md 规则 4 + 输出契约/白名单更新 + test_prompts 断言）；真实 LLM 冒烟复核 9/9 `assertions=1`

## 2026-09-09 · 断言引擎上线（已完成）

> 沉淀：[docs/sessions/2026-09-09-assertion-engine.md](sessions/2026-09-09-assertion-engine.md)

- [x] `AssertionItem` schema（path 白名单正则 / op 白名单 / extra=forbid）接入 CaseCreate/CaseUpdate/GeneratedCase 三入口，非法断言 422 或整 case 校验拒绝
- [x] 执行引擎渲染断言：契约层 status_code 基线恒渲染 + 字段层 `_dig` 点路径逐条求值；非法历史条目注释跳过；失败消息带 case id/path/op/expected 进 JUnit
- [x] system.md 升级「契约层 1 条 + 字段层 2-4 条」；测试同批（unit/api/schema 校验 + 渲染产物真实 exec 执行），全量 256 测试绿

## 2026-10-08 · 全量代码审查 + P0/P1 修复（已完成）

> 沉淀：[docs/sessions/2026-10-08-p0-p1-fixes.md](sessions/2026-10-08-p0-p1-fixes.md) ｜ 审查报告 + 修复报告见 `docs/reviews/`（本地归档）。本轮只改文档口径的部分见「待办清单维护」一节。

- [x] 审查：全量源码 + 已知问题清单逐条核验（A1/C1/C3 等）+ 真机端到端；新发现 14 条（1 High / 5 Medium）
- [x] P0 C1：并发同输入撞 `UNIQUE(run_id)` → 查回赢家返回（task/generate 两链路，不再 500）
- [x] P0 A1：定向 `operation_ids` 全未命中 → 422 `INVALID_OPERATION_IDS`（message 带合法示例）；`GeneratedCaseList.cases` 加 `min_length=1`；前端「0 草稿」改 warning + 展示跳过明细
- [x] P0 N1：Worker 侧日志接管（celery `after_setup_logger`/`after_setup_task_logger` + 强制统一格式），`request_id` 全链路在 Worker 侧可见
- [x] P0 N3：摘除无鉴权 `/static`，新增带鉴权 `GET /api/v1/tasks/{id}/report`；前端改带 token 取 HTML + blob 打开
- [x] P1 C3：用例 update 两层闸（形状闸重校验 + 语义闸降级 draft 并清 reviewer）；`expected_status` 100–599；`name/path` strip
- [x] P1 N2/N5：`run_cmd` 显式 UTF-8 + `errors=replace` + 子进程 `PYTHONIOENCODING`；跨平台杀树（POSIX `killpg` + 组长守卫）；`on_start` 异常先清理
- [x] P1 N4/C2：`scan_stale_tasks` 增加 `pending` 排队孤儿回收（按 `updated_at` + revoke）；新增 `POST /tasks/{id}/cancel`、`/retry`；`_mark_running` 改条件更新
- [x] P1 N13：生成链路任务内 deadline + 进度快照；执行任务 soft/time limit 按 `timeout_seconds` 派生并向 `visibility_timeout` 收敛；文档口径修正
- [x] 门禁：ruff 全绿；后端 301 passed + 1 slow、覆盖率 92.4%（核心 91%）；前端 46 tests + vue-tsc/build 通过；真机三阶段验收

## 2026-10-08 · 文档治理（格式统一 / 纠错 / 收敛归档）

- [x] 新增 [docs/README.md](README.md)：文档地图 + 统一格式规范（10 条）+ 双轨映射 + 四查核对清单
- [x] 统一格式：全部专题文档加文件头定位块（定位/覆盖范围/状态/最后核对/事实源锚点/双轨镜像）、H2 编号、代码块语言标注、结尾「相关文档」
- [x] 纠错：`POST /tasks`、`POST /generate` 响应字段 `task_id`→`data.id`；`time_limit > visibility_timeout` 方向写反；`idx_test_cases_operation_id`→`ix_`；`error_stage` 补 `cancelled`/`pending_timeout`；补 20+ 业务错误码表；补 `celery.pending_timeout` 配置项与 env 清单
- [x] 收敛：报告类产物统一归档 `docs/reviews/`（本次两份审查/修复报告已移入）；容器根 README 去重为「跨仓库导航」，项目介绍以 `backend/README.md` 为唯一事实源
- [x] 状态补记：roadmap 里程碑表与待解决清单补 2026-10-08 批次；断言引擎/Code Review 计数改为「以 CI 为准」口径

## 低优先级优化项（P2/P3 · 未排期）

> **状态更新（2026-10-10）：下表 P2/P3 共 15 项已全部完成**，逐项落点见文末「2026-10-10 · 低优先级优化批次」。表格保留原评估口径（当时为何判为低优先级），便于回溯判断是否合理。

> **定性**：均为**已知、可验证、不阻塞当前交付**的加固与体验项。已评估为低优先级——收益真实但触发条件尚未出现；`触发条件` 列写明「什么时候值得做」，避免提前返工。
> **来源**：2026-10-08 全量代码审查（14 条新发现）+ P0/P1 修复报告「残留与后续」+ 文档治理报告「反思与残留」；详细背景见 `docs/reviews/2026-10-08-*.md`（本地归档）。
> **维护**：做完一项即移到上面的完成项台账，并在 `docs/sessions/` 留沉淀。

### P2 · 契约与健壮性加固

| # | 项 | 现象 / 风险 | 触发条件 | 涉及位置 |
| :--- | :--- | :--- | :--- | :--- |
| P2-1 | 统一入参校验错误响应 | `RequestValidationError` 与服务层 `ValidationError` 未纳入 `{code, message, detail}`：前端需兜底文案；回归受影响用例 >500 时会绕过上限直接 500 | 对外开放 API / 交付契约文档时 | `app/main.py`、`app/services/impact_service.py` |
| P2-2 | 前端列表轮询治理 | tasks 页 2s 拉整页快照、无 `document.hidden` 暂停、无最大时长与退避（E3/E4；generate 页已加错误计数与手动恢复） | 任务量 >100 或多用户并发时 | `frontend/src/views/tasks/index.vue` |
| P2-3 | 执行中任务取消 | 当前仅 `pending` 可取消；RUNNING 取消需杀 pid + 与执行层约定「谁写终态」 | 出现「跑太久想中止」真实需求时 | `app/services/task_service.py`、`execution_service.py` |
| P2-4 | 生成链路超时粒度 | deadline 检查点在 operation 之间 → 最坏多花一个 operation 的 LLM 预算（连接 5s + 读 60s × 重试 3）；要更紧需把剩余预算传进 llm_client | 单次生成 token 成本敏感时 | `app/services/generation_service.py`、`app/utils/llm_client.py` |
| P2-5 | LLM 输出侧敏感防线 | 输出只剔危险标签与控制字符，不扫 token / 内网 URL；上游错误原文（含掩码 key 尾部）直接落 `generation_logs.error_msg` 与 `rejected_detail` | 接入真实生产文档源时 | `app/services/generation_service.py` |
| P2-6 | 同类 TOCTOU 收敛 | `/parse` 的 `UNIQUE(api_definitions.version)` 并发撞车仍可能 500（`/tasks`、`/generate` 已收敛为「查回赢家」） | 多人同时上传同名 version 时 | `app/repositories/api_definition_repository.py` |
| P2-7 | 输入扫描面收敛 | `_scan_prompt_risk` 扫的是整段渲染后 prompt（含模板 / JSON Schema 文本）——模板里出现关键词会误拦全部 operation | 调整 prompt 模板后出现莫名拦截时 | `app/services/generation_service.py` |

### P3 · 可选优化（低收益 / 低优先）

| # | 项 | 说明 | 触发条件 |
| :--- | :--- | :--- | :--- |
| P3-1 | Redis 密码 URL 转义 | `broker_url` 字符串拼接未转义，密码含 `@ : / #` 会拼坏 URL | 生产 Redis 使用复杂密码时 |
| P3-2 | broker 命名空间隔离 | dev / CI / 多环境共用 `db 0` + 默认 `celery` 队列，消息跨环境互窜（实测：新 Worker 消费到旧库消息并 skip） | 出现多环境同机部署时 |
| P3-3 | 旧库 `report_link` 兼容 | 历史任务行仍是 `/static/...`（该静态面已摘除）→ 打开即 404；处置：`scripts\reset_db.bat` 或一次性迁移脚本 | 需要保留历史任务报告时 |
| P3-4 | 形状闸的修复式更新 | 历史坏行必须「带坏字段一起提交」才能修（当前有意取舍）；可选加显式 repair 语义 | 出现批量历史脏数据时 |
| P3-5 | Worker 日志编码 | 重定向捕获时中文受控制台代码页影响显示乱码（应用侧字符串与 API 返回均正确）；可给 Worker 显式配 UTF-8 输出 | 需要把 Worker 日志落文件 / 集中采集时 |
| P3-6 | 文档漂移自动拦截 | 把本轮沉淀的三个校验脚本（链接与编号 `doc_lint`、镜像原子块 `mirror_verify`、镜像文风 `mirror_style_check`）搬进 CI | **下一轮文档批量改动前（建议优先）** |
| P3-7 | 镜像装饰内容补齐 | 源 README 的 shields 徽章墙与截图 TODO 注释未搬入学习版镜像（沿用旧镜像仅保留 CI 徽章） | 需要学习版与源完全逐字时 |
| P3-8 | 可选基础设施 | PostgreSQL 迁移 / JWT 鉴权 / 限流（Redis 固定窗口）——ROI 排序：限流 > JWT > PostgreSQL | 按面试收益评估 |

## 2026-10-10 · 低优先级优化批次（P2/P3 全部闭环）

> 门禁：`ruff check app tests scripts` 全绿 ｜ `pytest` **356 passed**（新增 58 个用例）｜`scripts/doc_check.py` 全 OK ｜前端 `pnpm test` 64 passed + `pnpm build` 通过。

| # | 落点（改了什么） | 关键设计取舍 | 验证 |
| :--- | :--- | :--- | :--- |
| P2-1 | `app/main.py` 注册 `RequestValidationError` 与 pydantic `ValidationError` handler，统一 `{code,message,detail}`（`detail` 为 `[{field,message,type}]`，**不回显输入原文**）；`schemas/task.py` 抽出 `MAX_CASE_IDS`，`impact_service.regression` 超限抛 422 `TOO_MANY_CASES` | 只暴露字段级错误：框架原始 `input` 会回显密码/大对象。内部校验失败兜底 422 而非 500，但根因在调用点显式拦 | `tests/api/test_errors.py`（4）+ `test_impact_service.py::test_regression_over_task_limit_raises_422` |
| P2-2 | `frontend/src/utils/polling.ts`（纯决策：`nextDelay`/`shouldKeepPolling`）+ `views/tasks/index.vue` 接线：可见性暂停、错误 2s→30s 指数退避、连续 5 次停止、单会话 30min 上限、`setInterval`→`setTimeout` 自续期 | 决策逻辑抽成纯函数才可测；会话时长只算"可见时间"（后台挂机不该被判超时）；被护栏终止后需手动入口才续命 | 前端 64 passed（polling 14 + 视图接线 4） |
| P2-3 | `task_service.cancel_task` 支持 `running`（先 `kill_process_tree` 再条件更新；`running` 不 revoke）；`execution_service._fail`/`force_fail_timeout`/`_finish_success` 全部改**条件更新**（仅 pending/running，成功路径仅 running）；前端 running 也出「取消」并改文案 | 用条件更新而非"读-判-写"：取消可能发生在执行途中的任意一刻，无条件覆盖会把 `cancelled` 改写成 subprocess/success | `test_tasks.py`（3）+ `test_execute_cases.py`（4，含"不覆盖取消"三连） |
| P2-4 | `llm_client.chat_json(..., deadline=)`：预算耗尽不发起调用（504 `LLM_DEADLINE_EXCEEDED`）、读超时按剩余量钳位、退避会睡过 deadline 即放弃；`generation_service` 透传同一 deadline | 只在需要钳位时才显式传 `timeout`——SDK 里 `timeout=None` 是"不限时"，误传会反向放大风险 | `test_llm_client.py`（4）+ `test_generation_service.py::test_run_generation_passes_deadline_to_llm` |
| P2-5 | `_redact_sensitive` + `_sanitize_llm_field(counter=)` 把密钥形态/内网地址/注入句式替换为 `[REDACTED]`，命中数进 `result_summary.redacted` 并写 warning | 选"脱敏并留痕"而非"拒绝该用例"：数据面已在入口拦截，输出侧是纵深防御；但改写用户数据必须可追溯 | `test_generation_service.py`（3） |
| P2-6 | `api_definition_repository.upsert` 捕获 `IntegrityError` → 回滚 → 查回赢家行返回 | 与任务/生成链路同一收敛策略（并发语义 = 串行语义），不新增重试队列 | `test_swagger_service.py::test_parse_version_collision_returns_existing` |
| P2-7 | `generation_service` 敏感扫描只喂 `_operation_to_json(op)`（数据面），不再扫渲染后整段 prompt | 模板/JSON Schema 属受控文本：它们命中关键词会误拦全部 operation（扫描面过宽的反面教训） | `test_generation_service.py`（2） |
| P3-1 | `Settings._redis_auth()` 用 `quote(password, safe="")` 拼 broker/result URL | 密码是任意字符串：`@ : / #` 会拼坏 URL（`p@ss` 的 `@` 被当 userinfo 分隔符） | `test_config.py`（3） |
| P3-2 | `celery.queue`（默认 `testplatform`）→ `task_default_queue` | 队列名即环境命名空间；默认值带前缀而非 Celery 默认 `celery`，避免历史串扰再发生 | `test_config.py::test_celery_queue_default_and_env_override` |
| P3-3 | `scripts/fix_report_links.py`（纯函数 `rewrite_report_link` + `migrate(dry_run=)`） | 幂等、可 dry-run；旧行不强改（`None`/已新形态原样返回），避免"迁移脚本改坏数据" | `test_report_links.py`（6） |
| P3-4 | `case_service.update_case` 形状闸**只对 active 生效**；draft 允许修复式更新 | draft 的坏数据由 confirm 激活闸兜底（进执行池的唯一入口）；闸门冲掉修复路径等于"坏行永远修不回" | `test_cases.py`（3：拒绝/放行/带字段修复） |
| P3-5 | `celery_app._ensure_utf8_streams()` 在 Worker 启动时把 stdio 切 UTF-8（无 `reconfigure` 的流跳过，失败仅 warning） | 只解决输出通道；应用侧字符串本就是 UTF-8。不在 Web 进程做（避免动 pytest/uvicorn 的流） | `test_worker_logging.py`（3） |
| P3-6 | `scripts/doc_check.py`（源文档链接/围栏/H2 编号 + 镜像印记/原子块/废弃口径/虚词）+ CI `test-fast` 新增门禁步；CI ruff 覆盖 `scripts` | 镜像缺失时整类 `skip`（CI 只检出 backend 不误报）；原子块只做存在性断言 | 本地 exit 0；含负向 fixture 自证（16 项命中即 exit 1） |
| P3-7 | 镜像 `backend/README.md` 补齐 shields 徽章墙与截图 TODO 注释（docs-study 侧） | 装饰性内容按同步 SOP 原样搬运，不与学习化改写混在一起 | `doc_check.py` 镜像检查 + 人工比对 |
| P3-8 | a) `database.py` 抽出 `is_sqlite_url`/`engine_kwargs` 纯函数 + `pyproject` 增 `[postgres]` extra；b) `deps._verify_jwt`（HS256 + `exp` + 可选 iss/aud + `algorithms` 白名单）；c) `utils/rate_limit.py` + `deps._enforce_rate_limit`（固定窗口，Redis 不可用放行） | 三项都做成**默认关闭/可选**：JWT 只验签不颁发（登录端点另行）、限流降级为放行（保护性能力不该升级成全站故障）、PG 只换驱动与 URL | `test_database_dialect.py`（3）+ `test_auth.py`（7）+ `test_rate_limit.py`（6）+ `test_config.py`（1） |

**本批未纳入（保持低优先级判断，非遗漏）**：前端其它页面（dashboard/generate/cases）的轮询治理、PostgreSQL 实机压测与迁移演练、镜像装饰之外的 docs-study 其它项目。

## 待提交 / 待跟进

- [x] **代码侧 P0/P1 修复批次**：已与 2026-10-10 低优先级批次合并为一次本地提交（含 `config/settings.example.yaml`、`.env.example` 的示例值与注释修正）。
- [ ] **推送**：按用户要求，backend / frontend / docs-study 三个仓库本轮**均不推送**（本地提交即可）。
- [ ] **`doc-system-kit/`**：保留容器根（可复用迁移工具包，非项目文档）；若多项目复用频繁，可独立成仓库。
