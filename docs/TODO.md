<!-- 本项目编码格式：UTF-8（无 BOM）。 -->
# 待办清单（TODO.md）

> 定位：按里程碑分组的完成项台账（过程记录）；方向见 [roadmap.md](roadmap.md)，实现细节见各专题文档。
> 事实源：各专题文档 + 本文件 ｜ 最后核对：2026-11-12

计数口径：各历史条目里的用例数为当时数值；当前门禁数值一律以 CI 为准（后端 pytest + slow e2e、前端 vitest、ruff、覆盖率），不要引用历史条目里的数字。

## 文档与地基 · 文档与基建（已完成）

- [x] 项目文档（architecture / database / api / execution-engine / ai-generation / impact-analysis / configuration / roadmap / TODO）
- [x] 工程规则与配置约定（config/settings.example.yaml + .env.example）
- [x] 双仓库结构（backend/ + frontend/，frontend 前端已实现）
- [x] 用户评审确认全部文档（进入编码的闸门）→ 2026-08-06 确认进入 执行闭环

## 执行闭环 · 核心执行闭环（已完成）

> 执行闭环 取舍：先不做 Alembic（`create_all` 替代）、request_id 中间件、Allure（自研 HTML 报告替代）、多环境 `{{base_url}}`（base_url 写死 config），僵尸回收只在 Worker 启动时扫一次。理由是第一版只跑「接口 → 异步执行 → 看结果」这一条链，依赖越少越容易验证。补齐情况：request_id 见「2026-08 · 细节加固」，僵尸回收周期化见「2026-11-11/12」批次，Allure 落在 CI 与 Jenkins 侧（平台 HTML 报告仍不依赖它），Alembic 与多环境 base_url 仍未做。

- [x] pyproject.toml（依赖 + ruff + pytest 配置）+ app/ 分层骨架
- [x] 配置加载（BaseModel 手动合并 yaml/env + get_engine + 日志 + AppError）
- [x] 用例 CRUD + confirm 审核（operation_id 必填；draft→active 防幻觉护栏）
- [x] 执行引擎：run_cmd（白名单 + timeout + on_start 落 pid）+ case_generator + junit_parser（累加 testsuite）+ report_util（HTML）
- [x] Celery + Redis 异步化（execute_cases + scan_stale_tasks 启动扫描，无 Beat）
- [x] Lookup-Create 幂等（run_id=sha256 + UNIQUE；SUCCESS 复用 / FAILED 可重试）
- [x] tests/unit + tests/api + tests/tasks（fakes: FakeSubprocess）+ 31 用例全绿 + ruff 全绿
- [x] 验收：/docs「创建用例 → confirm → 触发执行(202) → 轮询 → results + HTML 报告」闭环 + 真实异步端到端验证

## 影响分析 · 变更影响分析（已完成）

> 纯规则引擎（无 AI）。

- [x] api_definitions 表（operation_id / path / method / request_schema_hash，`hashlib.md5` 指纹）
- [x] `POST /api/v1/parse`：Swagger 解析入库（$ref / allOf / oneOf 递归 + reparse 覆盖）
- [x] `POST /api/v1/impact/analyze`：schema_hash 对比 → 变更 operation_id 列表 + breaking 五场景判定
- [x] 反向检索：`SELECT * FROM test_cases WHERE operation_id IN (...)`（核心 SQL）
- [x] 一键回归：受影响用例创建执行任务（复用 执行闭环 引擎，宽容降级 + 真实口径 + last_regression）

## AI 生成 · AI 智能生成（已完成）

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
- [x] Step 8 验证 + 文档核对 + 提交
- [x] confirm 审核接口（复用，AI 用例 draft → active）

## 生产化与前端 · 生产化与前端增强（已完成）

- [x] Docker Compose（FastAPI + Redis + Worker + SQLite，named volume + 非 root + solo 单写者）
- [x] GitHub Actions 三 job（门禁 ruff + pytest + coverage 60/80 + slow e2e + docker-build 验证）
- [x] 基础鉴权（Bearer Token，值在配置中；401/403 区分，health 免鉴权，/docs 开关）
- [x] Vue 前端 4 页（仪表盘/用例管理/任务执行/AI 生成）+ 41 单测 + 构建门禁（dev 走 Vite 代理）
- [ ] （可选延后）PostgreSQL 迁移 / JWT 鉴权
- [x] 范围决策：不做自愈看板（与执行闭环、影响分析、AI 生成三个目标无关；失败定位已由任务状态机、`error_stage` 与任务报告承担；触发条件：出现跨任务聚合失败模式并驱动处置的真实需求时再评估）

## 2026-08-12 · 全量代码审查修复（已完成）

- [x] 命令白名单 `python3*` 前缀匹配（容器执行路径）；HTML 报告转义 + `/static` 收敛 reports/；`scan_stale_tasks` 覆盖生成任务；FAILED 任务同输入可重试（Lookup-Create 语义修订）
- [x] 前端时间补 Z（UTC naive 显示偏移）；入队失败落 FAILED(dispatch) + 503；`tasks.timeout_seconds` 真正控制执行超时（dev 库已 reset）；`page_size`≤100 / `case_ids`≤500；仪表盘按 status 统计
- [x] 后端 6 commit + 前端 2 commit（每 commit 独立跑通门禁）；174+1 pytest、41 vitest、ruff、91% 覆盖率全绿

## 2026-08 · 细节加固（已完成）

- [x] health 端点关闭 Redis 连接；`hmac.compare_digest` 常数时间 token 比对；合同指纹 MD5→sha256（旧数据 re-parse 覆盖）
- [x] generation_task_repository commit 补 rollback 包装（与 TaskRepository 对齐）；`_BOUNDARY_RULES` 外置 `prompts/v1/boundary_rules.md`
- [x] openapi 递归深度限制（>50 截断 + warning）；执行错误分类（command/timeout/subprocess 分阶段）；`GeneratedCase` max_length 对齐表列长
- [x] 前端响应解包改类型安全 `request<T>`（去双断言）；前端 CI（.github/workflows/ci.yml）
- [x] Python 版本统一 3.12+（与 pyproject 对齐）；api/tasks/integration marker 声明即用（conftest 自动归类）
- [x] request_id 全链路追踪中间件：X-Request-ID 生成/透传/响应头回写 + ContextVar + 日志 Filter + Celery 任务与 LLM 日志透传

## 2026-09-01 · 被测系统迁移：管伊佳ERP（已完成）

> 演示形态二选一：离线 mock（兜底）/ 管伊佳ERP（真实业务系统）。

- [x] Swagger2→OpenAPI3 转换脚本 `scripts/convert_swagger2.py` + 产物 `docs/examples/jsherp-openapi3.json`（320 paths/338 ops，parse 0 warning）+ 解析片段单测
- [x] 执行层鉴权适配：`execution.auth_*` 配置（登录路径/体模板/密码编码 md5/token 头与提取路径）+ workspace 登录 conftest（session 级一次登录，凭证仅 env 引用）+ 用例模板 token 头注入；单测含密钥不落盘断言
- [x] ERP 端到端冒烟：parse → 定向 AI 生成（13 draft/0 rejected）→ confirm → 执行 4/4 passed（真实登录取 X-Access-Token 请求 ERP）+ HTML 报告
- [x] 账号体系口径确认（2026-09-02 补充）：admin 仅是平台运维用户（只配菜单、建租户，不能编辑业务数据）；业务管理员是租户 jsh。演示凭证已切 jsh 并重验执行 4/4 passed；口径并入 [execution-engine.md](execution-engine.md) §8.2。

## 2026-08-31 · 演示稳定性 + 容器内实测 + prompt 断言（已完成）

- [x] 本地 mock 目标服务 `scripts/mock_target.py`（httpbin 兼容子集）；.env.example/compose 默认本地 mock，`docker compose up` 全离线；执行冒烟 2/2 passed + HTML 报告（3.4s）
- [x] 容器内实测搬进 CI：docker-build job 构建后 `docker run` 挂载 `scripts/verify_container.py` 容器内验证白名单（真实路径/python3.12 前缀/真实执行/越权拒绝四断言）
- [x] prompt v1 断言强约束：每条用例至少 1 条 assertions（system.md 规则 4 + 输出契约/白名单更新 + test_prompts 断言）；真实 LLM 冒烟复核 9/9 `assertions=1`
- [x] CI lint 门禁修复：ruff format 全仓 + PEP695 + 版本落锁（commit ff05fd3）

## 2026-09-09 · 断言引擎上线（已完成）

- [x] `AssertionItem` schema（path 白名单正则 / op 白名单 / extra=forbid）接入 CaseCreate/CaseUpdate/GeneratedCase 三入口，非法断言 422 或整 case 校验拒绝
- [x] 执行引擎渲染断言：契约层 status_code 基线恒渲染 + 字段层 `_dig` 点路径逐条求值；非法历史条目注释跳过；失败消息带 case id/path/op/expected 进 JUnit
- [x] system.md 升级「契约层 1 条 + 字段层 2-4 条」；测试同批（unit/api/schema 校验 + 渲染产物真实 exec 执行），全量 256 测试绿

## 2026-10-08 · 全量代码审查与加固（已完成）

- [x] 审查：全量源码逐条核验 + 真机端到端；新发现 14 条（1 High / 5 Medium）
- [x] 并发同输入撞 `UNIQUE(run_id)` → 查回赢家返回（task/generate 两链路，不再 500）
- [x] 定向 `operation_ids` 全未命中 → 422 `INVALID_OPERATION_IDS`（message 带合法示例）；`GeneratedCaseList.cases` 加 `min_length=1`；前端「0 草稿」改 warning + 展示跳过明细
- [x] Worker 侧日志接管（celery `after_setup_logger`/`after_setup_task_logger` + 强制统一格式），`request_id` 全链路在 Worker 侧可见
- [x] 摘除无鉴权 `/static`，新增带鉴权 `GET /api/v1/tasks/{id}/report`；前端改带 token 取 HTML + blob 打开
- [x] 用例 update 两层闸（形状闸重校验 + 语义闸降级 draft 并清 reviewer）；`expected_status` 100–599；`name/path` strip
- [x] `run_cmd` 显式 UTF-8 + `errors=replace` + 子进程 `PYTHONIOENCODING`；跨平台杀树（POSIX `killpg` + 组长守卫）；`on_start` 异常先清理
- [x] `scan_stale_tasks` 增加 `pending` 排队孤儿回收（按 `updated_at` + revoke）；新增 `POST /tasks/{id}/cancel`、`/retry`；`_mark_running` 改条件更新
- [x] 生成链路任务内 deadline + 进度快照；执行任务 soft/time limit 按 `timeout_seconds` 派生并向 `visibility_timeout` 收敛；文档口径同步修正
- [x] 门禁：ruff 全绿；后端 301 passed + 1 slow、覆盖率 92.4%（核心 91%）；前端 46 tests + vue-tsc/build 通过；真机三阶段验收（计数口径以最新 CI 运行为准）

## 2026-10-08 · 文档治理（格式统一 / 纠错 / 收敛）

- [x] 新增 [docs/README.md](README.md)：文档地图 + 统一格式规范（10 条）+ 四查核对清单
- [x] 统一格式：全部专题文档加文件头定位块、H2 编号、代码块语言标注、结尾「相关文档」
- [x] 纠错：`POST /tasks`、`POST /generate` 响应字段 `task_id`→`data.id`；`time_limit > visibility_timeout` 方向写反；`idx_test_cases_operation_id`→`ix_`；`error_stage` 补 `cancelled`/`pending_timeout`；补 20+ 业务错误码表；补 `celery.pending_timeout` 配置项与 env 清单
- [x] 收敛：评审与报告类产物不再进入仓库，本地留存；根目录 README 去重为「跨仓库导航」，项目介绍以 `backend/README.md` 为唯一事实源
- [x] 状态补记：roadmap 里程碑表与待解决清单补齐 2026-10-08 的进展；断言引擎与代码审查的计数改为「以 CI 为准」口径

## 低优先级优化项（已完成，保留原评估口径）

> 状态更新（2026-10-10）：下表 15 项已全部完成，逐项落点见文末「2026-10-10 · 低优先级优化项完成情况」。表格保留原评估口径（当时为何判为低优先级），便于回溯判断是否合理。

> 定性：均为已知、可验证、不阻塞当前交付的加固与体验项。已评估为低优先级——收益真实但触发条件尚未出现；`触发条件` 列写明「什么时候值得做」，避免提前返工。
> 来源：2026-10-08 全量代码审查（14 条新发现）、当轮修复的残留项与文档治理的待办项。
> 维护：做完一项即移入上面的完成项台账。

### 契约与健壮性加固

| 项 | 现象 / 风险 | 触发条件 | 涉及位置 |
| :--- | :--- | :--- | :--- |
| 统一入参校验错误响应 | `RequestValidationError` 与服务层 `ValidationError` 未纳入 `{code, message, detail}`：前端需兜底文案；回归受影响用例 >500 时会绕过上限直接 500 | 对外开放 API / 交付契约文档时 | `app/main.py`、`app/services/impact_service.py` |
| 前端列表轮询治理 | tasks 页 2s 拉整页快照、无 `document.hidden` 暂停、无最大时长与退避（generate 页已加错误计数与手动恢复） | 任务量 >100 或多用户并发时 | `frontend/src/views/tasks/index.vue` |
| 执行中任务取消 | 当前仅 `pending` 可取消；RUNNING 取消需杀 pid + 与执行层约定「谁写终态」 | 出现「跑太久想中止」真实需求时 | `app/services/task_service.py`、`execution_service.py` |
| 生成链路超时粒度 | deadline 检查点在 operation 之间 → 最坏多花一个 operation 的 LLM 预算（连接 5s + 读 60s × 重试 3）；要更紧需把剩余预算传进 llm_client | 单次生成 token 成本敏感时 | `app/services/generation_service.py`、`app/utils/llm_client.py` |
| LLM 输出侧敏感防线 | 输出只剔危险标签与控制字符，不扫 token / 内网 URL；上游错误原文（含掩码 key 尾部）直接落 `generation_logs.error_msg` 与 `rejected_detail` | 接入真实生产文档源时 | `app/services/generation_service.py` |
| 同类 TOCTOU 收敛 | `/parse` 的 `UNIQUE(api_definitions.version)` 并发撞车仍可能 500（`/tasks`、`/generate` 已收敛为「查回赢家」） | 多人同时上传同名 version 时 | `app/repositories/api_definition_repository.py` |
| 输入扫描面收敛 | `_scan_prompt_risk` 扫的是整段渲染后 prompt（含模板 / JSON Schema 文本）——模板里出现关键词会误拦全部 operation | 调整 prompt 模板后出现莫名拦截时 | `app/services/generation_service.py` |

### 可选优化（低收益 / 低优先）

| 项 | 说明 | 触发条件 |
| :--- | :--- | :--- |
| Redis 密码 URL 转义 | `broker_url` 字符串拼接未转义，密码含 `@ : / #` 会拼坏 URL | 生产 Redis 使用复杂密码时 |
| broker 命名空间隔离 | dev / CI / 多环境共用 `db 0` + 默认 `celery` 队列，消息跨环境互窜（实测：新 Worker 消费到旧库消息并 skip） | 出现多环境同机部署时 |
| 旧库 `report_link` 兼容 | 历史任务行仍是 `/static/...`（该静态面已摘除）→ 打开即 404；处置：`scripts\reset_db.bat` 或一次性迁移脚本 | 需要保留历史任务报告时 |
| 形状闸的修复式更新 | 历史坏行必须「带坏字段一起提交」才能修（当前有意取舍）；可选加显式 repair 语义 | 出现批量历史脏数据时 |
| Worker 日志编码 | 重定向捕获时中文受控制台代码页影响显示乱码（应用侧字符串与 API 返回均正确）；可给 Worker 显式配 UTF-8 输出 | 需要把 Worker 日志落文件 / 集中采集时 |
| 文档漂移自动拦截 | 把文档结构校验（相对链接、围栏语言、H2 编号）固化成脚本并接进 CI | 下一轮文档批量改动前（建议优先） |
| 文档装饰内容补齐 | README 的 shields 徽章墙与截图 TODO 注释未补齐（当前只保留 CI 徽章） | README 展示需要与设计稿一致时 |
| 可选基础设施 | PostgreSQL 迁移 / JWT 鉴权 / 限流（Redis 固定窗口）——ROI 排序：限流 > JWT > PostgreSQL | 需要对外提供正式 API 或多人共用时 |

## 2026-10-10 · 低优先级优化项完成情况

> 门禁：`ruff check app tests scripts` 全绿 ｜ `pytest` 356 passed（新增 58 个用例；计数口径以最新 CI 运行为准）｜`scripts/doc_check.py` 全 OK ｜前端 `pnpm test` 64 passed + `pnpm build` 通过。

| 落点（改了什么） | 关键设计取舍 | 验证 |
| :--- | :--- | :--- |
| `app/main.py` 注册 `RequestValidationError` 与 pydantic `ValidationError` handler，统一 `{code,message,detail}`（`detail` 为 `[{field,message,type}]`，不回显输入原文）；`schemas/task.py` 抽出 `MAX_CASE_IDS`，`impact_service.regression` 超限抛 422 `TOO_MANY_CASES` | 只暴露字段级错误：框架原始 `input` 会回显密码/大对象。内部校验失败兜底 422 而非 500，但根因在调用点显式拦 | `tests/api/test_errors.py`（4）+ `test_impact_service.py::test_regression_over_task_limit_raises_422` |
| `frontend/src/utils/polling.ts`（纯决策：`nextDelay`/`shouldKeepPolling`）+ `views/tasks/index.vue` 接线：可见性暂停、错误 2s→30s 指数退避、连续 5 次停止、单会话 30min 上限、`setInterval`→`setTimeout` 自续期 | 决策逻辑抽成纯函数才可测；会话时长只算"可见时间"（后台挂机不该被判超时）；被护栏终止后需手动入口才续命 | 前端 64 passed（polling 14 + 视图接线 4） |
| `task_service.cancel_task` 支持 `running`（先 `kill_process_tree` 再条件更新；`running` 不 revoke）；`execution_service._fail`/`force_fail_timeout`/`_finish_success` 全部改条件更新（仅 pending/running，成功路径仅 running）；前端 running 也出「取消」并改文案 | 用条件更新而非"读-判-写"：取消可能发生在执行途中的任意一刻，无条件覆盖会把 `cancelled` 改写成 subprocess/success | `test_tasks.py`（3）+ `test_execute_cases.py`（4，含"不覆盖取消"三连） |
| `llm_client.chat_json(..., deadline=)`：预算耗尽不发起调用（504 `LLM_DEADLINE_EXCEEDED`）、读超时按剩余量钳位、退避会睡过 deadline 即放弃；`generation_service` 透传同一 deadline | 只在需要钳位时才显式传 `timeout`——SDK 里 `timeout=None` 是"不限时"，误传会反向放大风险 | `test_llm_client.py`（4）+ `test_generation_service.py::test_run_generation_passes_deadline_to_llm` |
| `_redact_sensitive` + `_sanitize_llm_field(counter=)` 把密钥形态/内网地址/注入句式替换为 `[REDACTED]`，命中数进 `result_summary.redacted` 并写 warning | 选"脱敏并留痕"而非"拒绝该用例"：数据面已在入口拦截，输出侧是纵深防御；但改写用户数据必须可追溯 | `test_generation_service.py`（3） |
| `api_definition_repository.upsert` 捕获 `IntegrityError` → 回滚 → 查回赢家行返回 | 与任务/生成链路同一收敛策略（并发语义 = 串行语义），不新增重试队列 | `test_swagger_service.py::test_parse_version_collision_returns_existing` |
| `generation_service` 敏感扫描只喂 `_operation_to_json(op)`（数据面），不再扫渲染后整段 prompt | 模板/JSON Schema 属受控文本：它们命中关键词会误拦全部 operation（扫描面过宽的反面教训） | `test_generation_service.py`（2） |
| `Settings._redis_auth()` 用 `quote(password, safe="")` 拼 broker/result URL | 密码是任意字符串：`@ : / #` 会拼坏 URL（`p@ss` 的 `@` 被当 userinfo 分隔符） | `test_config.py`（3） |
| `celery.queue`（默认 `testplatform`）→ `task_default_queue` | 队列名即环境命名空间；默认值带前缀而非 Celery 默认 `celery`，避免历史串扰再发生 | `test_config.py::test_celery_queue_default_and_env_override` |
| `scripts/fix_report_links.py`（纯函数 `rewrite_report_link` + `migrate(dry_run=)`） | 幂等、可 dry-run；旧行不强改（`None`/已新形态原样返回），避免"迁移脚本改坏数据" | `test_report_links.py`（6） |
| `case_service.update_case` 形状闸只对 active 生效；draft 允许修复式更新 | draft 的坏数据由 confirm 激活闸兜底（进执行池的唯一入口）；闸门冲掉修复路径等于"坏行永远修不回" | `test_cases.py`（3：拒绝/放行/带字段修复） |
| `celery_app._ensure_utf8_streams()` 在 Worker 启动时把 stdio 切 UTF-8（无 `reconfigure` 的流跳过，失败仅 warning） | 只解决输出通道；应用侧字符串本就是 UTF-8。不在 Web 进程做（避免动 pytest/uvicorn 的流） | `test_worker_logging.py`（3） |
| `scripts/doc_check.py`（源文档相对链接 / 代码围栏语言 / H2 编号）+ CI `test-fast` 新增门禁步；CI ruff 覆盖 `scripts` | 只查可机械判定的三类结构问题，语义仍靠人工复核；纯标准库、Windows/Linux 同源 | 本地 exit 0；CI `test-fast` 已接入该步 |
| `backend/README.md` 补齐 shields 徽章墙与截图 TODO 注释 | 装饰性内容原样搬运，不与正文改写混在一起 | `doc_check.py` + 人工比对 |
| a) `database.py` 抽出 `is_sqlite_url`/`engine_kwargs` 纯函数 + `pyproject` 增 `[postgres]` extra；b) `deps._verify_jwt`（HS256 + `exp` + 可选 iss/aud + `algorithms` 白名单）；c) `utils/rate_limit.py` + `deps._enforce_rate_limit`（固定窗口，Redis 不可用放行） | 三项都做成默认关闭/可选：JWT 只验签不颁发（登录端点另行）、限流降级为放行（保护性能力不该升级成全站故障）、PG 只换驱动与 URL | `test_database_dialect.py`（3）+ `test_auth.py`（7）+ `test_rate_limit.py`（6）+ `test_config.py`（1） |

**本批未纳入（保持低优先级判断，非遗漏）**：前端其它页面（dashboard/generate/cases）的轮询治理、PostgreSQL 实机压测与迁移演练。

## 2026-11-11/12 · 测试报告标准化与 Jenkins 集成（已完成）

> 门禁：`pytest -q` 368 passed / 4 deselected ｜ `ruff check app tests scripts` + `ruff format --check .` 全绿 ｜ `scripts/doc_check.py` exit 0 ｜ 前端 `pnpm run test:ci` 9 文件 64 用例、`reports/junit.xml` 可解析 ｜ 真实 Jenkins build #7/#8 SUCCESS。计数口径以最新 CI 运行为准。
> 范围：报告标准产物与发布归档、`Jenkinsfile`、本机 Jenkins 实测（含 Allure）、执行器抽象与 Jenkins 集成、Beat 周期回收、真实全链路 e2e、`.env` 编码事故修复与 LLM provider 切换、全仓注释重写。本轮结束时不执行 commit / push（见文末「待提交 / 待跟进」）。

| 落点（改了什么） | 关键设计取舍 | 验证 |
| :--- | :--- | :--- |
| `backend/.github/workflows/ci.yml` 的 pytest 步骤产出 `reports/junit.xml`、`coverage.xml`、`htmlcov`、`allure-results`，新增 `EnricoMi/publish-unit-test-result-action@v2` 发布逐用例结果与 `actions/upload-artifact@v4` 归档 `reports/`（14 天），顶层补 `permissions`（checks / pull-requests write）；`frontend/package.json` 增 `test:ci`（vitest 双 reporter 落 `reports/junit.xml`），前端 CI 改走该脚本并同样发布与归档；后端 `.gitignore` 原有 `reports/`，前端补上 | 发布与归档都挂 `if: always()`，并用 `hashFiles` 守卫：pytest 收集期崩溃不产 `junit.xml`，无条件发布会把「测试失败」改写成「发布步骤失败」，掩盖真实原因；发布步骤再加 `continue-on-error`，权限或服务抖动不该把已通过的门禁判红。本地 `pnpm test` 保持不落文件，只有 CI 走 `test:ci`，避免开发机被报告产物污染 | 本地等价命令 exit 0：后端 356 passed、覆盖率 92.68%（356 用例时的实测值）、`junit.xml` 356 个 testcase 可解析；前端 exit 0、9 文件 64 用例、`junit.xml` 可解析。CI 首次运行的实际产物以最新 CI 运行为准 |
| 新增 `backend/Jenkinsfile`：声明式流水线，参数 `PYTHON_BIN` / `RUN_SLOW` / `RUN_DOCKER`，四阶段（准备环境 → 静态检查 + 文档门禁 → 测试与覆盖率 → 慢测试 / 镜像构建），报告步骤 `junit` + `recordCoverage` + `publishHTML` + `archiveArtifacts`，Allure 步骤放在 `post` 段 | 与 GitHub Actions 三段门禁等价，不另立第二套口径；`recordCoverage` 包 `fileExists` 守卫，收集期崩溃时 `coverage.xml` 可能不存在，无条件调用会把测试失败改写成流水线脚本错误；慢测试与镜像构建做成布尔开关，没有 docker 的 agent 只跑门禁；`.venv` 已存在时复用，参数经环境变量交给 shell 展开，避免 Groovy 插值带来的引号嵌套 | Jenkins 官方声明式 linter 校验通过；本机 Windows 演示 job 复跑 build #7/#8 均为 SUCCESS |
| 本机 Jenkins 2.580.2（便携 JDK 21 + WAR，只监听 127.0.0.1:8080）配齐 Pipeline / JUnit / Coverage / HTML Publisher / Allure 3.0.0 等插件，Allure CLI 2.32.0 注册为全局工具 `allure-cli`；`Jenkinsfile` 与 CI 均产出 `allure-results` 并发布 Allure 报告；Windows 等价演示 job 只在本机，不写入仓库；接入形态与踩坑写进 [execution-engine.md](execution-engine.md) §11 | 演示 job 不入库：仓库 `Jenkinsfile` 依赖 Linux agent 的 `sh` 与 `.venv/bin`，Windows 上只能验证报告发布链路；Allure 步骤包 `try/catch` 且显式传 `commandline: 'allure-cli'`，未配工具时只 echo 警告，展示层不把测试门禁判红；四个坑按「现象 → 根因 → 做法」固化：插件 3.x 包名改为 `org.allurereport.jenkins`、手写 `<installations>` 缺 array class 属性、`InstallSourceProperty` 与手工 home 冲突、pipeline 未显式指定安装名 | build #7/#8 SUCCESS（368 passed / 4 deselected，测试阶段约 51s）；日志出现 `Using Allure CLI 2.32.0 from installation 'allure-cli'` 与 `Report successfully generated`；`GET /job/TestPlatform-P0-windows-demo/8/allure/` 返回 200 且页面含 `Allure Report`；`allure-results/`、`allure-report/`、`junit.xml`、`coverage.xml`、`htmlcov/` 均入构建归档 |
| `app/services/executors/`（`base` 协议 + `local` + `jenkins` + `get_executor` 工厂，编排层只留状态机与事务边界）、`app/utils/jenkins_client.py`（CSRF crumb + 会话，触发 → 队列 → 轮询 → 下载制品，超时全部来自 `jenkins.*`）、配置三处同步（`app/core/config.py`、`config/settings.example.yaml`、`.env.example` 各加 `execution.executor` 与 `jenkins` 段，全带默认值）、`JENKINS_NOT_CONFIGURED` / `UNAVAILABLE` / `TRIGGER_FAILED` / `HTTP_ERROR` / `BUILD_CANCELLED` / `TIMEOUT` / `NO_REPORT` / `REPORT_INVALID` 落 `error_stage` 的 `jenkins` / `timeout` / `parse`、`result_summary` 增 `executor` 与 `build_url`、`scripts/jenkins_smoke.py`、单测 `test_executors.py` + `test_jenkins_client.py` | 只做轮询回读、不做入站回调：平台不必对外开放端口，凭据与签名面收敛；HMAC 回调留到确有「Jenkins 主动推」需求时再补（届时需时间戳防重放 + `build_number` 幂等）。抽象只切「执行 + 解析」，`_mark_running` 条件更新、取消竞态、`_fail` 错误分类都不动，`local` 行为与拆分前一致；token 只从 `.env` 的环境变量名读取，不拼 URL、不入日志；构建结果 FAILURE 时仍回读制品，否则「哪条用例红了」拿不到 | 12 条单测：`test_executors`（工厂路由、未知执行器、制品解析、未配置、制品缺失、非法 XML）+ `test_jenkins_client`（MockTransport 覆盖 crumb、触发、排队、超时、取消、HTTP 错误、网络不可达）；真实联调 `scripts/jenkins_smoke.py` 触发 #8 → SUCCESS（72.7s）→ 回读 junit 40,182 字节 → 解析 `total=368 passed=368` |
| `celery.stale_scan_interval`（默认 60s，`0` 关闭周期扫描）+ `app/celery_app.py` 的 `beat_schedule`（任务 `scan-stale-tasks`，`expires` 与间隔同值，慢扫描不排队）+ `scripts/start_beat.bat` + compose `beat` 服务（`--schedule` 落 dbdata 卷）+ `scripts/start_all.bat` 一次拉起 api / worker / beat 三个窗口 | 节拍只在 `celery_app.beat_schedule` 定义一次，运维脚本与 compose 不另写，否则「为什么没扫」无法从代码回答；Beat 独立进程并用文件锁保证同机单实例；周期取小于最短 pending 判定的值，扫得太晚等于没扫；保留 `0` 关闭，兼容无 Beat 的部署形态，Worker 启动仍补扫一次 | 单测 `test_beat_schedule_registers_periodic_scan`、`test_celery_conf_wires_beat_schedule`、`test_stale_scan_interval_default_and_env_override` 与 `pending_allowance_seconds` 的排队叠加判定；compose 配置由 CI `docker-build` job 校验 |
| `tests/e2e/test_real_full_chain.py` 三例：① 真 pytest 子进程 + 真 HTTP + 真报告（不需要 key）② 真 LLM（CommandCode）生成 1 接口 3 条 draft → 审核 → 真执行 → 报告 ③ 进程级真 uvicorn + 真 Celery worker + 真 Redis 跨进程全链路（`REAL_E2E_PROCESS=1`） | 三条全部标 `slow` + `integration`，不进默认门禁：默认门禁必须离线可复现、不烧 API 额度；缺 key 只 skip LLM 用例，纯执行链路照常跑；进程级用例用环境变量显式开关，等待统一走轮询加超时（180s，LLM 300s），结束不留 uvicorn / worker 子进程 | 本机 3 条全过；LLM 用例实测 1695 tokens、$0.001695；进程级用例结束无遗留子进程 |
| 按 `.env.example` 结构重建 `backend/.env`（UTF-8 + CRLF，48 键），值用正则从原文恢复（行首匹配优先于注释内匹配），密钥只比较长度不打印；备份留 `.env.bak-encoding-*` 与 `.env.bak-rebuild-*`（均被 gitignore）；`tests/unit/test_llm_client.py` 去掉硬编码 `deepseek-chat`，改为断言透传 `settings.llm.model` | 重建而不是逐行修补：字面 `?` 吞换行、GBK 双编码、CRLF/LF 混用三种损坏叠加，逐行改无法保证 48 键齐全；密钥只比长度不打印，避免修复动作本身泄漏；测试改成断言「配置值透传」，换 provider 不再挂测试 | 48 键、0 乱码标记、密钥长度前后一致；`pytest -q` exit 0 |
| `.env` 切到 CommandCode：`TESTPLATFORM_LLM_BASE_URL=https://api.commandcode.ai/provider/v1`、`TESTPLATFORM_LLM_MODEL=deepseek/deepseek-v4.1-flash`，key 仍在 `DEEPSEEK_API_KEY`；代码侧 `llm_client` 按 `llm.base_url` 与 `llm.model` 透传，`config.py` 默认值仍是 `api.deepseek.com` 与 `deepseek-chat` | 换 provider 只改配置不改代码（OpenAI 兼容协议）；成本估算仍按 `llm.cost_per_1k_tokens` 单价乘 usage，不做 provider 差异化定价，估算只服务演示与审计口径 | `/chat/completions` 实调返回 200（46 tokens）；真实 e2e 生成链路 3 draft、0 rejected；`test_llm_client` 断言 `model` 取自 `get_settings().llm.model` |
| 全仓注释与 docstring 重写（`app/`、`tests/`、`scripts/`），语义未改：注释解释「为什么这样取舍」，不复述代码 | 只改文本不改行为，门禁结果与改前一致才能说明零语义变更；不做逐文件人工比对，靠 ruff + pytest + `doc_check.py` 三道门禁兜底；注释里不写内部评审编号与规则章节引用，避免对外文档出现内部过程痕迹 | `pytest -q` 368 passed / 4 deselected（当次）；`ruff check` + `ruff format --check` 全绿；`doc_check.py` exit 0 |

**本批未纳入**：报告契约升级（`junit_parser` 的 `name` 与 `duration_ms`、失败任务报告、`/api/v1/tasks/{id}/report.xml` 与 `/report.json` 导出、报告渲染升级）；企业接入所需的参数化用例执行 job（契约见 [execution-engine.md](execution-engine.md) §10）；Windows 演示 job 不写入仓库。

## 2026-11-12 · 文档公开版 / 内部版拆分（已完成）

| 落点（改了什么） | 关键设计取舍 | 验证 |
| :--- | :--- | :--- |
| `docs/README.md` 拆成两份：公开版只保留文档地图、项目与文档说明、阅读路径、示例数据与过程记录口径、相关文档；写作规范（10 条）、四查清单、机器门禁口径移入新增 `docs/README.internal.md`，并在 `backend/.gitignore` 忽略该文件；同步更新 `README.md`、`docs/` 六篇专题文档与 `roadmap.md` 里指向该页的描述（「文档地图与阅读路径」） | 拆分标准是「读者是谁」：公开页服务外部读者（先读哪篇、事实源在哪），维护规范服务协作者与 AI（格式、四查、门禁）。内部文件不入库，公开仓库不再暴露内部流程；`.gitignore` 用固定路径而非通配，避免误伤同名公开文档 | `git check-ignore docs/README.internal.md` 命中 `.gitignore:54`；`python scripts/doc_check.py` 3/3 exit 0（本地 13 篇受检，CI 检出为 12 篇）；公开页内无「双轨 / 镜像 / 面试 / RULES / 四查 / 印记」残留；doc-study 镜像已同步为新公开版内容 |

## 2026-11-12 · 文档与注释「去 AI 味」纪律 + 机器门禁（已完成）

| 落点（改了什么） | 关键设计取舍 | 验证 |
| :--- | :--- | :--- |
| 新增 `scripts/doc_hygiene.py`（D1–D5 五项：内部批次编号 / 协作过程叙述 / 自贬措辞 / 生成式套话 / 未公开引用），扫描 `README.md` + `docs/*.md` 正文与 `app/`、`tests/`、`scripts/` 的注释与 docstring；Python 走 `tokenize` + `ast` 取注释与 docstring，YAML/TOML 取 `#` 行；CI `test-fast` 与 `Jenkinsfile` 的「静态检查 + 文档门禁」阶段均接入 | 不用 `[TMHVCBALRD][0-9]` 这类全仓正则：它把 HTTP 状态码（202/500/504）、表号、构建号（build #7/#8）、徽章 URL 全判成违规，逼着作者改事实。本门禁只匹配「编号真实出现时的形状」（括号内 / `review X1` / 日期+批次号），并显式放行优先级语义（P0/P1/P2）与章节号。豁免精确到文件且总量受限（`docs/TODO.md` 免 D2、`docs/README.internal.md` 整篇豁免、脚本自身免扫描），单测里加了「豁免总量不得膨胀」的断言 | `python scripts/doc_hygiene.py` 5/5 exit 0；首次运行暴露真实违规 37 项（含 `scripts/doc_hygiene.py` 自指与 `roadmap.md` 两处过程叙述），逐项改完后清零 |
| 新增 `tests/unit/test_doc_hygiene.py`（24 例，属 `-m unit` 门禁）：① 仓库当前必须全通过 ② 五条规则各自构造违规必须被抓 ③ 十类正常技术内容（状态码/章节号/阈值/构建号/配置键/测试计数）必须放行 ④ 注释提取不误伤字符串字面量 ⑤ 豁免清单只对声明文件生效且总量受限 | 门禁最常见事故是「空转的绿色」——正则写歪了全通过但违规照样进仓。所以除「当前干净」外，必须同时证明「真能抓」与「不误伤」；误伤比漏判更糟，会逼着作者改事实。规则源码里就要写出被禁字面量作反例，故脚本自身免扫描（自指） | 24 passed；`ruff check` / `ruff format --check` 通过 |
| 措辞纪律写入内部规则体系（`§20`：五类禁止内容表 + 提交信息规范 + 注释规范 + 提交前自检 + 豁免说明），AI 协作约定同步加索引与核心约定各一条；内部维护规范补「措辞纪律」小节与两条门禁的说明 | 规则与门禁分开写：规则文档解释「为什么禁、怎么改」，脚本只判可机械判定的部分。语义类问题（编造数字、技术事实错误、刻意歪写的动机说明）正则抓不到，必须在文档里写明仍需人工复核，避免给人「跑绿了就没事」的错觉 | `doc_check.py` 3/3 + `doc_hygiene.py` 5/5，均 exit 0；全量 `pytest -q` 412 passed / 4 deselected（当时口径；后续已增至 497），覆盖率 93.14% |
| 公开文档按新规则清理：`roadmap.md` 两处协作过程叙述改为设计表述（「审查发现 14 条新问题」→「审查列出的 14 条问题」、「本批未纳入」→「未纳入」） | 只改措辞不动事实：问题数量、批次范围、结论全部保留，去掉的是「谁在什么时候发现的」这类对外无意义的协作痕迹 | `doc_hygiene.py` 复跑清零；`doc_check.py` 链接与编号未受影响 |

## 2026-11-12 · AI 生成语义校验与真实质量评分（第一批已完成）

> 方案与实测数据见 [plans/2026-11-12-generation-semantic-validation.md](plans/2026-11-12-generation-semantic-validation.md)。

- [x] 第一批：格式底噪修复 → 语义校验（`generation_validators.py`）→ 逐条 `trust_score` + `quality_flags` → 删除 `ai_confidence` → 前端展示扣分原因 → 去重与 `max_length`
- [x] confirm 侧语义校验：`_extract_contract` 补 `method`/`path`/`response_schemas`，`contract_from_snapshot` 适配器，`_revalidate_for_active` 跑 `validate_case`（error 级拒绝激活，无快照/旧快照/未知 op 降级放行）
- [ ] 第二批：交叉一致性检查 + `unfalsifiable` 标记
- [ ] 后续：P3 生成质量评测脚本

### 不可证伪用例（立项，待评估）

生成的「鉴权异常 / 资源不存在」类用例在执行层拿不到预期的 4xx：`test_cases` 没有 `headers` 列，`render_test_file` 对每条用例注入同一个有效 token，导致 401/403/404 用例与 200 用例渲染出逐字节相同的请求（实测 `smoke_jsherp.db` 的 `getAccountUsingGET` 四条用例渲染结果完全一致）。这类用例结构上不可证伪，执行必然失败或恒真。

- 触发条件：需要在真实系统上验证鉴权与资源不存在分支时。
- 现状处置：判 `warn` + `unfalsifiable` 标记（第二批），并改 prompt 禁止生成无法表达前置条件的此类用例。
- 彻底修复：`test_cases` 增 `headers` 列 + `case_type` 枚举，让用例能承载「无 token / 错 token / 不存在的 id」；涉及模型、渲染层、执行层与 prompt，约 1 天。

## 2026-11-12 · 编号体系废除（Phase 编号全量清退 + D6 门禁）（已完成）

| 落点（改了什么） | 关键设计取舍 | 验证 |
| :--- | :--- | :--- |
| **全量清退 `Phase 1`–`Phase 4` 交付阶段编号**：20 个文件（`README.md`、`docs/` 八篇、`app/` 六个模块、`tests/` 三处、脚本一处），把编号换成能力名——执行闭环 / 影响分析 / AI 生成 / 生产化与前端；`MVP` 作为阶段代称的 8 处代码注释同步改写（改为「零前端部署」「当前用 create_all」等事实表述）。`tests/unit/test_config.py::test_phase4_defaults` 改名 `test_frontend_defaults` | 不做机械全局替换：直接删 `Phase N` 会产生「影响分析链路（影响分析，纯规则）」这类叠词与「复用执行闭环 执行引擎」的空格残留，因此分三档处理——括号独立引用整块删、`Phase N + 名词` 保留名词、其余换能力名，再单独跑一轮修叠词。理由：编号本身不承载信息，它唯一的用途是「让人知道指的是哪段工作」；那段工作已经有名字（影响分析/AI 生成），编号就是纯冗余 | `grep -rn "Phase [0-9]"` 在 docs、`app/`、`tests/`、脚本、两份 README 全部 0 命中；`doc_check.py` 3/3、`doc_hygiene.py` 6/6 均 exit 0；AST 对比确认 11 个源码文件仅注释变化（剥离 docstring 后与 HEAD 完全一致） |
| **新增 D6 门禁**（`scripts/doc_hygiene.py`）：拦截已废除的交付阶段编号，同时扫描文档正文与 `app/`、`tests/`、`scripts/` 注释；`MVP` 仅在其作为阶段代称时命中（`MVP 例外` 这类既有契约名放行） | 单列一条而不并进 D1：Phase 是被「清完又长回来」最多的编号族，且替换目标明确（能力名），适合机器直接拦；并入 D1 会让 D1 的批次编号正则越写越复杂 | D6 首跑抓到 8 处代码注释里的 `MVP`，逐条改写后清零；自证用例证明 `Phase 2 已实现…`、`MVP 零前端` 会被抓，`MVP 例外持续生效` 放行 |
| **规则写入内部规范 §21「编号体系」**：保留白名单只留三类（外部标准编号如 HTTP 状态码 / 机器可读标识如错误码与配置键 / 稳定文档锚点如章节号与门禁代号），其余一律废除；给出「删掉编号读者还知道说的是哪段工作吗」的判定小抄与三种描述式句式 | 一劳永逸的关键不是「再清一次」，而是**取消编号的表达资格**：只要允许用编号指代工作，新编号就会继续产生。保留的三类都有外部依赖（外部标准、代码字面量、脚本/链接引用），删了会破坏可引用性；其余全是内部记账，删了只损失「谁在什么时候做的」这类对外无意义的痕迹 | 规则与门禁分离：§21 解释为什么与怎么改，D6 只判可机械判定的部分 |

## 待提交 / 待跟进

- [x] **2026-11-13 全量复跑口径**：`pytest -q` 506 passed / 4 deselected、覆盖率 93.63%、`ruff check` / `ruff format --check`、`scripts/doc_check.py`、`scripts/doc_hygiene.py` 全绿。本批门禁数字以此为准；上节记录里的 368 passed / 92.68% 是 2026-11-11 批次与 Jenkins build #7/#8 当时的实测值，不是同一时间点的口径。覆盖率随被测代码增长而变化，引用时以最新 CI 运行为准。
- [x] **文档与门禁侧改动已提交**：`README.md`、`docs/` 十一篇、`scripts/doc_check.py`、`scripts/doc_hygiene.py`、`tests/unit/test_doc_hygiene.py`、`Jenkinsfile` 与 `ci.yml` 的门禁步骤、`.gitignore` 均已随 `7b07ca2`（编码与注释措辞统一、两项门禁接入）等提交入库；`docs/README.internal.md` 按设计被 `.gitignore` 忽略，不随提交。
- [ ] **推送**：两个仓库本地领先远端，未执行 `git push`；推送属对外动作，需明确授权后执行。
- [ ] **Jenkins 侧不入库**：Windows 演示 job 配置、本机 Jenkins 安装目录与 Allure CLI 位置只在本机记录；仓库 `Jenkinsfile` 仍是项目 CI，不含 `TASK_ID` / `CASE_IDS` 参数。
- [x] 代码侧高优先级修复（2026-10-08 / 10-10 批次）：已与当轮低优先级项合并为一次本地提交（含 `config/settings.example.yaml`、`.env.example` 的示例值与注释修正）。
- [ ] 推送（2026-10 批次）：backend / frontend 两个仓库该批次不推送（本地提交即可）。
- [ ] `doc-system-kit/`：保留可复用的文档迁移工具包（非项目文档）；若多项目复用频繁，可独立成仓库。
