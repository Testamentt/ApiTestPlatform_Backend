<!-- 本项目编码格式：UTF-8（无 BOM）。 -->
# 路线图（roadmap.md）

> 定位：项目往哪走——原则、当前状态、里程碑与下一步（活文档，随迭代更新）。
> 事实源：本文件（方向）+ `docs/TODO.md`（完成项台账）+ 各专题文档（实现细节） ｜ 最后核对：2026-11-12

## 1. 项目原则

本文件记录方向，[TODO.md](TODO.md) 记录完成项，实现细节落在各专题文档，同一条事实不写两处。

引入任何新技术、依赖或功能前，先评估成本与收益。评估口径有四条：是否服务于已确定的目标（执行闭环、影响分析、AI 生成）；引入成本（依赖数量、部署复杂度、长期维护成本）；收益是否可验证（能用现有测试与观测手段确认）；可回退性（能否在不动数据模型的前提下替换或移除）。讲不清收益、成本明显偏高的内容一律不做或后置。三条已经落地的决策：

| 原则 | 内容 |
| :--- | :--- |
| 执行闭环优先 | 先跑通「接口 → 异步执行 → 查看结果」最小闭环，再补前端与鉴权 |
| 双界面 | 后端 Swagger UI（/docs）+ Vue 3 前端（frontend/，前端已实现 4 页） |
| 纯逻辑先行 | 先做「影响分析」（Diff + SQL，纯规则、不花钱、无幻觉），后做「AI 生成」（LLM） |

## 2. 当前状态

全部交付完成：执行闭环 / 影响分析 / AI 智能生成 / 生产化与 Vue 前端。2026-08 至 2026-10 的四轮代码加固（全量审查、细节加固、交叉验证修复，以及 2026-10-08 全量审查列出的 14 条问题与高优先级修复）均已闭环，逐项见 [TODO.md](TODO.md)。门禁全绿：后端 pytest + slow e2e、前端 vitest、ruff、覆盖率（数值以 CI 为准，见 [README.md](../README.md) §3）。

2026-11-11 至 11-12 完成「测试报告标准化 + Jenkins 集成」批次：两仓库 CI 改为产出标准报告（JUnit XML / coverage XML+HTML / `allure-results`），发布逐用例结果并把 `reports/` 归档为制品，仓库补参数化 `Jenkinsfile`；平台侧抽出执行器（`execution.executor=local|jenkins`）与 `jenkins_client`，Celery Beat 承担周期僵尸回收，并补三条真实全链路 e2e（全部 `slow`，不进默认门禁）。对外口径：已在真实 Jenkins 上完成一次完整验证（build #7/#8），跨机器可复现性依赖 Jenkins + Allure 工具链配置（插件版本、Allure 全局工具安装名、agent 的 Python 3.12）。逐项落点见 [TODO.md](TODO.md)。

容器内可执行路径的验证（此前唯一的未闭环项）已于 2026-08-31 在 CI 中实测闭环；默认门禁没有遗留阻塞，唯一的外部依赖项是 Jenkins 侧的跨机器复现，取决于工具链配置（见 §5）。

## 3. 交付里程碑总览

| 阶段 | 状态 | 交付要点 | 验收一句话 |
| :--- | :--- | :--- | :--- |
| 文档与地基 · 文档与基建 | 完成 | 全套设计文档 + 双仓库 | 设计评审通过 |
| 执行闭环 · 核心执行闭环 | 完成 | 用例 CRUD + 异步执行（Celery 202）+ HTML 报告 + 超时劫持 | Swagger UI：建用例 → confirm → 执行 → 报告 |
| 影响分析 · 变更影响分析 | 完成 | 版本快照 + 分段 hash O(1) diff + breaking 联合判定 + 一键回归 | 新旧 Swagger → diff → 圈定 → 一键回归 |
| AI 生成 · AI 智能生成 | 完成 | llm_client + prompts/v1 + 三层防幻觉护栏 + 审计 | 生成 draft → 审核 → active |
| 生产化与前端 · 生产化与前端 | 完成 | Docker Compose + CI 三 job + Bearer Token + Vue 4 页 | 一键 `docker compose up` + 前端页面可用 |
| 2026-08-12 · 全量代码审查 | 完成 | 命令白名单前缀匹配 / 报告转义 / 僵尸扫描覆盖生成任务 / 失败任务可重试 / 执行超时生效 | 审查项逐条修复 + 门禁全绿 |
| 2026-10-08 · 全量审查与加固 | 完成 | 并发 500 / 假成功 / Worker 日志断环 / 报告鉴权 / 用例更新闸 / 排队孤儿回收 + 取消重试 / 任务内超时 | 真机三阶段验收通过（计数以 CI 为准） |
| 2026-11-11 至 11-12 · 测试报告标准化与 Jenkins 集成 | 完成 | 两仓库 CI 产出并发布 JUnit / coverage / Allure 报告 + 参数化 `Jenkinsfile` + 执行器抽象（local/jenkins）+ Beat 周期回收 + 真实全链路 e2e | 真实 Jenkins build #7/#8 SUCCESS（368 passed / 4 deselected），Allure 页 200，平台冒烟回读 40,182 字节制品 |

## 4. 演示路径

以下三步按能力顺序串成一条演示脚本，每一步都可独立复现。

1. **异步解耦**：`POST /cases` 建用例 → confirm → `POST /tasks` 立即返回 `data.id`（202）→ 轮询至 success → 带鉴权取报告。全程无需前端，Web 不阻塞。
2. **精准回归**：Swagger 1.0 建 3 用例 → 传 Swagger 2.0（改 1 个接口入参）→ `POST /impact/analyze` 返回「变更 1、影响 1」→ 一键回归。
3. **AI 生成**：上传 Swagger → `POST /generate` → 生成 draft（source=ai、trust_score=80/60）→ confirm → active；`POST /impact/{id}/fix-hints` 演示 breaking 修复建议。

被测系统有两种形态（`execution.base_url` 切换，默认值见 [configuration.md](configuration.md) §2.5）：离线 mock（`scripts/mock_target.py`，不依赖外网）与管伊佳ERP（本机 `:9999/jshERP-boot`，真实业务系统；Swagger2 文档经 `scripts/convert_swagger2.py` 转 OpenAPI3 入库 [examples/jsherp-openapi3.json](examples/jsherp-openapi3.json)，执行层自动登录取 `X-Access-Token`，见 [execution-engine.md](execution-engine.md) §8.2）。完整 API 演练见 [api.md](api.md) §5。

## 5. 待解决问题与下一步

**已完成（2026-11-11 至 11-12 · 测试报告标准化与 Jenkins 集成）**

- [x] 报告标准化：两仓库 CI 产出 JUnit XML / coverage XML+HTML / `allure-results`，发布逐用例结果并归档制品；仓库补 `Jenkinsfile`（参数化四阶段 + `junit` / `recordCoverage` / `publishHTML` / `archiveArtifacts` + Allure 步骤）。此前 CI 侧只有控制台文本，门禁失败只能翻整段日志找用例。
- [x] 执行器可切换：`execution.executor=local|jenkins`，平台可把用例投递到 Jenkins 并按轮询回读 JUnit 制品；只做轮询回读、不做入站回调，平台不必对外开放端口。
- [x] 僵尸回收周期化：`celery.stale_scan_interval`（默认 60s）+ 独立 Beat 进程，不再依赖 Worker 重启才扫一次。
- [x] 真实全链路 e2e：真 pytest 子进程、真 LLM、进程级真 uvicorn + worker + Redis 三条用例，全部 `slow`，不进默认门禁。

> 逐项落点、取舍与实测证据见 [TODO.md](TODO.md)「2026-11-11/12 · 测试报告标准化与 Jenkins 集成（已完成）」；执行器契约与 Allure 接入见 [execution-engine.md](execution-engine.md) §10、§11。

**已完成（2026-10-10 · 低优先级加固）**

- [x] 统一入参校验错误响应：框架 422 与服务层校验失败都归一到 `{code, message, detail}`；一键回归超单任务上限改为显式 422 `TOO_MANY_CASES`（不再 500）。
- [x] 前端列表轮询治理：可见性暂停 + 错误指数退避 + 总时长上限 + 请求放大收敛（决策逻辑抽成可测纯函数）。
- [x] 执行中任务取消：`running` 先杀 pytest 进程树再落取消终态；执行层成功/失败/软超时回写改为条件更新，不覆盖取消。
- [x] 生成链路与 LLM 输出侧：生成 deadline 透传（单次调用被剩余预算钳位）｜LLM 输出敏感片段脱敏并留痕｜`/parse` 版本号 TOCTOU 收敛｜输入敏感扫描面收敛到数据面。
- [x] 基础设施与文档门禁：Redis 密码 URL 转义｜队列命名空间隔离｜旧库 `report_link` 迁移脚本｜形状闸只对 active 生效｜Worker stdio UTF-8｜文档结构门禁进 CI｜README 徽章与截图占位补齐｜PostgreSQL 可选依赖 + JWT 通道 + 限流（后两者默认关闭）。

> 逐项落点、验证方式与残留风险见 [TODO.md](TODO.md)「2026-10-10 · 低优先级优化项完成情况」；同一条事实不写两处。

**下一步（未做项与触发条件）**

- 报告契约升级：`junit_parser` 补 `name` / `duration_ms`，失败任务也生成报告，新增 `GET /api/v1/tasks/{id}/report.xml` 与 `/report.json` 导出，再做报告渲染升级。触发条件：报告要被流水线或质量看板直接消费，或失败任务必须能打开报告。现状见 [execution-engine.md](execution-engine.md) §6、§7。
- 参数化用例执行 job：企业接入需在 Jenkins 侧另建带 `TASK_ID` / `CASE_IDS` / `BASE_URL` 参数的执行 job（契约见 [execution-engine.md](execution-engine.md) §10），仓库 `Jenkinsfile` 只承担项目自身 CI。触发条件：需要把平台任务投到企业 Jenkins 上执行。
- 真实环境复现建议：换机器或升级 Jenkins 后先对齐三项再查代码——插件（JUnit / Coverage / HTML Publisher / Allure）、Allure 全局工具的安装名（pipeline 需显式 `commandline`）、agent 的 Python 3.12。触发条件：覆盖率趋势、HTML 或 Allure 报告页在新环境异常。
- 登录/发令牌端点：当前 JWT 只做验签，不负责颁发。
- 未纳入：前端其它页面轮询治理、PostgreSQL 实机压测基线。

**已完成（2026-10-08 · 全量代码审查与加固）**

- [x] 定向 `operation_ids` 全未命中 → 422 `INVALID_OPERATION_IDS`（消除"假成功"）；`GeneratedCaseList.cases` 加 `min_length=1`。
- [x] 并发创建撞 `run_id` 唯一约束 → 查回既有任务（不再 500）。
- [x] Worker 侧日志接管 `request_id`（此前全链路追踪在 Worker 断环）。
- [x] 报告改带鉴权端点 `GET /api/v1/tasks/{id}/report`，摘除无鉴权 `/static` 静态面。
- [x] 用例 update 两层闸（形状闸 + 语义闸降级 draft）；`expected_status` 加 100–599 边界；`name/path` strip。
- [x] 子进程 UTF-8 编解码 + 跨平台杀树（POSIX `killpg`）+ `on_start` 异常清理。
- [x] PENDING 排队孤儿回收（`celery.pending_timeout` + revoke）+ 取消/人工重试端点 + `_mark_running` 条件更新。
- [x] 生成链路任务内超时预算 + 进度快照；执行任务级 soft/time limit 按 `timeout_seconds` 派生。

**已完成（2026-09-01 · 被测系统迁移：管伊佳ERP）**

- [x] Swagger2→OpenAPI3 转换：`scripts/convert_swagger2.py`（线上 `/v2/api-docs` 拉取/本地文件兜底；basePath→servers、definitions→components、in:body→requestBody、参数 schema 包裹），产物 320 paths/338 operations、parse 0 warning。见 [examples/jsherp-openapi3.json](examples/jsherp-openapi3.json)。
- [x] 执行层鉴权适配（不改表结构）：`execution.auth_*` 配置段 + workspace 生成登录 `conftest.py`（session 级登录取 token、凭证仅经 env 引用不入生成文件）+ 用例模板注入 token 请求头；单测覆盖渲染/密钥不落盘/装配。
- [x] ERP 端到端冒烟：parse 338 ops → 定向 AI 生成 3 接口（13 draft、0 rejected、$0.0035）→ confirm 4 条正向用例 → 执行 4/4 passed（登录→token→真实请求 ERP）+ HTML 报告。

**已完成（2026-08-31 · 演示稳定性 + 容器内实测 + prompt 断言强化）**

- [x] 本地 mock 目标服务：`scripts/mock_target.py`（httpbin 兼容子集：回显/`/status/{code}`/`/delay/{n}`/`/bearer`）；`.env.example` 与 compose 默认切本地 mock（`http://mock:9999`），`docker compose up` 全离线可复现。冒烟：2 用例执行 success（passed=2/2）+ HTML 报告，全程 3.4s。见 [execution-engine.md](execution-engine.md) §8.1。
- [x] 容器内实测：本机无 Docker → 搬进 CI——`docker-build` job 构建后 `docker run` 挂载 `scripts/verify_container.py` 容器内验证（真实可执行路径过白名单 + `python3.12` 前缀命中 + run_cmd 真实执行 + 白名单外命令真实拒绝）。
- [x] prompt 断言强约束：真实 LLM 冒烟发现 `assertions=0` → system.md 要求每条用例至少 1 条断言（响应 schema 字段校验，未定义 schema 用 `status_code` 等值）；复核冒烟 9 条用例 `assertions=1` 全命中、0 rejected。
- [x] 断言引擎（2026-09）：`AssertionItem` 校验（path 白名单/op 白名单）接入创建/更新/AI 生成三入口；执行引擎渲染契约层基线 + 字段层点路径断言逐条求值（非法历史条目注释跳过）；system.md 升级为「契约层 1 条 + 字段层 2-4 条」。

**已完成（2026-08 · 细节加固）**

- [x] request_id 全链路追踪、health 端点关闭 Redis 连接、`hmac.compare_digest` 常数时间比较、MD5→sha256、`_BOUNDARY_RULES` 外置 prompts/、openapi 递归深度限制、execution_service 错误分类、GeneratedCase 长度对齐、repository rollback 包装、前端类型安全解包、Python 版本统一、测试 marker 归类、前端 CI（见 [frontend/.github/workflows/ci.yml](https://github.com/Testamentt/ApiTestPlatform_Frontend/blob/master/.github/workflows/ci.yml)）。

**已完成（2026-08-28 · AI 生成真实 LLM 冒烟）**

- [x] 真实 DeepSeek 调用端到端冒烟（`.env` key 生效验证）：`POST /generate` → eager 任务 → `llm_client` 真调 `deepseek-chat` → 2/2 operation 覆盖、9 条 draft 用例（`source=ai`/`trust=80`、operation_id 服务端注入）、0 rejected、成本 ≈$0.002；request_id 全链路日志核验通过。

**范围决策（不做）**

- 自愈看板（Vue 页）：与当前三个目标（执行闭环、影响分析、AI 生成）无关，列入不做；失败定位已由任务状态机、`error_stage` 与任务报告承担，再加一层看板只扩大前端维护面。触发条件：出现「跨任务聚合失败模式并驱动处置」的真实需求时再评估。

## 6. 相关文档

| 文档 | 角色 |
| :--- | :--- |
| [README.md](README.md) | 文档地图与阅读路径（从这里进） |
| [architecture.md](architecture.md) | 总体架构（双界面/三层/进程隔离/异步模型） |
| [database.md](database.md) | 数据模型（6 表字段级 + 状态机） |
| [api.md](api.md) | REST API 设计（端点总表 + 错误码表） |
| [execution-engine.md](execution-engine.md) | Celery 任务 / subprocess / 超时与回收 / 执行器与 Jenkins / 报告 |
| [ai-generation.md](ai-generation.md) | OpenAPI 解析 / Prompt / LLM 护栏 / 审计 |
| [impact-analysis.md](impact-analysis.md) | 影响分析算法 / 一键回归 / fix-hints |
| [configuration.md](configuration.md) | 配置管理（十段 + .env.example 契约） |
| [TODO.md](TODO.md) | 分阶段完成项勾选台账（过程记录） |
| [../README.md](../README.md) | 项目入口：快速开始 / 核心特性 |
