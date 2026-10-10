# 路线图（roadmap.md）

> **定位**：项目往哪走——原则、当前状态、里程碑与下一步（活文档，随迭代更新）。
> **状态**：活文档 ｜ **最后核对**：2026-10-08
> **事实源锚点**：本文件（方向）+ [TODO.md](TODO.md)（完成项台账）+ 各专题文档（实现细节） ｜ **双轨镜像**：`docs-study/TestPlatform/backend/docs/roadmap.md`
> **规则引用**：`RULES.md` §17-R5（变更沉淀）。三者分工：方向 / 台账 / 细节，避免同一事实两处维护而漂移。

## 1. 项目原则（面试导向）

本项目是针对面试的作品：引入任何新技术 / 依赖 / 功能前，先严格评估**学习成本 vs 面试收益**——讲不清、收益低、成本高的内容一律不做或后置。三大决策：

| 原则 | 内容 |
| :--- | :--- |
| 执行闭环优先 | 先跑通「接口 → 异步执行 → 查看结果」最小闭环，再补前端与鉴权 |
| 双界面 | 后端 Swagger UI（/docs）+ Vue 3 前端（frontend/，Phase 4 已实现 4 页） |
| 纯逻辑先行 | 先做「影响分析」（Diff + SQL，纯规则、不花钱、无幻觉），后做「AI 生成」（LLM） |

## 2. 当前状态

**全部交付完成**：Phase 1 执行闭环 / Phase 2 影响分析 / Phase 3 AI 智能生成 / Phase 4 生产化 + Vue 前端 / 2026-08-12 全量 Code Review 批次 A·B / **批次 C Minor 加固（L1-L12 + request_id 全链路追踪）** / **2026-09-03 R3 子代理交叉验证修复** / **2026-10-08 全量代码审查（14 条新发现）+ P0/P1 修复（9 项）**。门禁全绿：后端 pytest + slow e2e、前端 vitest、ruff、覆盖率（数值以 CI 为准，见 [README.md](../README.md) §3）。

> 原「唯一未闭环验证项」H1 已于 2026-08-31 经 CI 容器内实测闭环（见「待解决问题」已闭环区）；无遗留验证阻塞。已知边界与代价清单见 [reviews/2026-10-08-p0-p1-fix-report.md](reviews/2026-10-08-p0-p1-fix-report.md)（本地归档）。

## 3. 交付里程碑总览

| 阶段 | 状态 | 交付要点 | 验收一句话 |
| :--- | :--- | :--- | :--- |
| Phase 0 · 文档与基建 | ✅ 完成 | 全套设计文档 + RULES.md + 双仓库 | 评审通过 |
| Phase 1 · 核心执行闭环 | ✅ 完成 | 用例 CRUD + 异步执行（Celery 202）+ HTML 报告 + 超时劫持 | Swagger UI：建用例 → confirm → 执行 → 报告 |
| Phase 2 · 变更影响分析 | ✅ 完成 | 版本快照 + 分段 hash O(1) diff + breaking 联合判定 + 一键回归 | 新旧 Swagger → diff → 圈定 → 一键回归 |
| Phase 3 · AI 智能生成 | ✅ 完成 | llm_client + prompts/v1 + 三层防幻觉护栏 + 审计 | 生成 draft → 审核 → active |
| Phase 4 · 生产化与前端 | ✅ 完成 | Docker Compose + CI 三 job + Bearer Token + Vue 4 页 | 一键 `docker compose up` + 前端页面可用 |
| 2026-08-12 · Code Review A/B | ✅ 完成 | H1-H4（白名单/报告转义/僵尸扫描/失败重试）+ M1-M5/M7 | 见 [reviews/2026-08-12-full-code-review.md](reviews/2026-08-12-full-code-review.md) |
| 2026-10-08 · 审查 + P0/P1 修复 | ✅ 完成 | 并发 500 / 假成功 / Worker 日志断环 / 报告鉴权 / 用例更新闸 / 排队孤儿回收 + 取消重试 / 任务内超时 | 见 [reviews/2026-10-08-p0-p1-fix-report.md](reviews/2026-10-08-p0-p1-fix-report.md) |

## 4. 面试演示路径（按卖点）

1. **异步解耦（Phase 1）**：`POST /cases` 建用例 → confirm → `POST /tasks` 立即返回 `data.id`（202）→ 轮询至 success → 带鉴权取报告。全程无需前端，Web 不阻塞。
2. **精准回归（Phase 2）**：Swagger 1.0 建 3 用例 → 传 Swagger 2.0（改 1 个接口入参）→ `POST /impact/analyze` 返回「变更 1、影响 1」→ 一键回归。
3. **AI 提效（Phase 3）**：上传 Swagger → `POST /generate` → 生成 draft（source=ai、trust_score=80/60）→ confirm → active；`POST /impact/{id}/fix-hints` 演示 breaking 修复建议。

被测系统两种形态（`execution.base_url` 切换，默认值见 [configuration.md](configuration.md) §2.5）：**离线 mock**（`scripts/mock_target.py`，稳定兜底）与**管伊佳ERP**（本机 `:9999/jshERP-boot`，真实业务系统——Swagger2 文档经 `scripts/convert_swagger2.py` 转 OpenAPI3 入库 [examples/jsherp-openapi3.json](examples/jsherp-openapi3.json)，执行层自动登录取 `X-Access-Token`，见 [execution-engine.md](execution-engine.md) §8.2）。完整 API 演练见 [api.md](api.md) §5。

## 5. 待解决问题与下一步

**已闭环（2026-10-10 · 低优先级优化批次）**
- [x] **P2-1 统一入参校验错误响应**：框架 422 与服务层校验失败都归一到 `{code, message, detail}`；一键回归超单任务上限改为显式 422 `TOO_MANY_CASES`（不再 500）。
- [x] **P2-2 前端列表轮询治理**：可见性暂停 + 错误指数退避 + 总时长上限 + 请求放大收敛（决策逻辑抽成可测纯函数）。
- [x] **P2-3 执行中任务取消**：`running` 先杀 pytest 进程树再落取消终态；执行层成功/失败/软超时回写改为条件更新，**不覆盖取消**。
- [x] **P2-4 ~ P2-7**：生成 deadline 透传（单次调用被剩余预算钳位）｜LLM 输出敏感片段脱敏并留痕｜`/parse` 版本号 TOCTOU 收敛｜输入敏感扫描面收敛到数据面。
- [x] **P3-1 ~ P3-8**：Redis 密码 URL 转义｜队列命名空间隔离｜旧库 `report_link` 迁移脚本｜形状闸只对 active 生效｜Worker stdio UTF-8｜文档漂移门禁进 CI｜镜像装饰补齐｜PostgreSQL 可选依赖 + JWT 通道 + 限流（后两者默认关闭）。

> 逐项落点、验证方式与残留风险见 [TODO.md](TODO.md)「2026-10-10 · 低优先级优化批次」；同一条事实不写两处。

**下一步（可选方向，非缺陷）**
- 登录/发令牌端点：当前 JWT 只做验签，不负责颁发（P3-8b 留的口子）。
- 本批未纳入：前端其它页面轮询治理、PostgreSQL 实机压测基线。

**已闭环（2026-10-08 · 全量代码审查 + P0/P1 修复）**
- [x] 定向 `operation_ids` 全未命中 → 422 `INVALID_OPERATION_IDS`（消除"假成功"）；`GeneratedCaseList.cases` 加 `min_length=1`。
- [x] 并发创建撞 `run_id` 唯一约束 → 查回既有任务（不再 500）。
- [x] Worker 侧日志接管 `request_id`（此前全链路追踪在 Worker 断环）。
- [x] 报告改带鉴权端点 `GET /api/v1/tasks/{id}/report`，摘除无鉴权 `/static` 静态面。
- [x] 用例 update 两层闸（形状闸 + 语义闸降级 draft）；`expected_status` 加 100–599 边界；`name/path` strip。
- [x] 子进程 UTF-8 编解码 + 跨平台杀树（POSIX `killpg`）+ `on_start` 异常清理。
- [x] PENDING 排队孤儿回收（`celery.pending_timeout` + revoke）+ 取消/人工重试端点 + `_mark_running` 条件更新。
- [x] 生成链路任务内超时预算 + 进度快照；执行任务级 soft/time limit 按 `timeout_seconds` 派生。
- 详情：[sessions/2026-10-08-p0-p1-fixes.md](sessions/2026-10-08-p0-p1-fixes.md) ｜ 修复报告：[reviews/2026-10-08-p0-p1-fix-report.md](reviews/2026-10-08-p0-p1-fix-report.md)

**已闭环（2026-09-01 · 被测系统迁移：管伊佳ERP）**
- [x] **Swagger2→OpenAPI3 转换**：`scripts/convert_swagger2.py`（线上 `/v2/api-docs` 拉取/本地文件兜底；basePath→servers、definitions→components、in:body→requestBody、参数 schema 包裹），产物 320 paths/338 operations、parse 0 warning。见 [examples/jsherp-openapi3.json](examples/jsherp-openapi3.json)。
- [x] **执行层鉴权适配**（不改表结构）：`execution.auth_*` 配置段 + workspace 生成登录 `conftest.py`（session 级登录取 token、凭证仅经 env 引用不入生成文件）+ 用例模板注入 token 请求头；单测覆盖渲染/密钥不落盘/装配。
- [x] **ERP 端到端冒烟**：parse 338 ops → 定向 AI 生成 3 接口（13 draft、0 rejected、$0.0035）→ confirm 4 条正向用例 → 执行 **4/4 passed**（登录→token→真实请求 ERP）+ HTML 报告。

**已闭环（2026-08-31 · 演示稳定性 + H1 容器实测 + prompt 断言强化）**
- [x] **本地 mock 目标服务**：`scripts/mock_target.py`（httpbin 兼容子集：回显/`/status/{code}`/`/delay/{n}`/`/bearer`）；`.env.example` 与 compose 默认切本地 mock（`http://mock:9999`），`docker compose up` 全离线可复现。冒烟：2 用例执行 success（passed=2/2）+ HTML 报告，全程 3.4s。见 [execution-engine.md](execution-engine.md) §8.1。
- [x] **H1 容器内实测**：本机无 Docker → 搬进 CI——`docker-build` job 构建后 `docker run` 挂载 `scripts/verify_container.py` 容器内验证（真实可执行路径过白名单 + `python3.12` 前缀命中 + run_cmd 真实执行 + 白名单外命令真实拒绝）。
- [x] **prompt 断言强约束**：真实 LLM 冒烟发现 `assertions=0` → system.md 要求每条用例至少 1 条断言（响应 schema 字段校验，未定义 schema 用 `status_code` 等值）；复核冒烟 9 条用例 `assertions=1` 全命中、0 rejected。
- [x] **断言引擎（2026-09）**：`AssertionItem` 校验（path 白名单/op 白名单）接入创建/更新/AI 生成三入口；执行引擎渲染契约层基线 + 字段层点路径断言逐条求值（非法历史条目注释跳过）；system.md 升级为「契约层 1 条 + 字段层 2-4 条」。见 [sessions/2026-09-09-assertion-engine.md](sessions/2026-09-09-assertion-engine.md)。

**已闭环（原批次 C，2026-08 完成）**
- [x] request_id 全链路追踪（§6.2）、health redis close、`hmac.compare_digest`、MD5→sha256、`_BOUNDARY_RULES` 外置 prompts/、openapi 递归深度限制、execution_service 错误分类、GeneratedCase 长度对齐、repository rollback 包装、前端类型安全解包、Python 版本统一、测试 marker 归类、**前端 CI**（L10——见 [frontend/.github/workflows/ci.yml](https://github.com/Testamentt/ApiTestPlatform_Frontend/blob/master/.github/workflows/ci.yml)）。

**已闭环（AI 生成真实 LLM 冒烟，2026-08-28）**
- [x] 真实 DeepSeek 调用端到端冒烟（`.env` key 生效验证）：`POST /generate` → eager 任务 → `llm_client` 真调 `deepseek-chat` → 2/2 operation 覆盖、9 条 draft 用例（`source=ai`/`trust=80`、operation_id 服务端注入）、0 rejected、成本 ≈$0.002；request_id 全链路日志核验通过。沉淀：[sessions/2026-08-28-ai-generation-smoke.md](sessions/2026-08-28-ai-generation-smoke.md)。

**已定但未做**
- 自愈看板（Vue 页）：**不做**——AI UI 项目卖点，不重复造轮子（Phase 4 备注）。

## 6. 相关文档

| 文档 | 角色 |
| :--- | :--- |
| [README.md](README.md) | **文档地图 + 统一写作规范**（从这里进） |
| [architecture.md](architecture.md) | 总体架构（双界面/三层/进程隔离/异步模型） |
| [database.md](database.md) | 数据模型（6 表字段级 + 状态机） |
| [api.md](api.md) | REST API 设计（端点总表 + 错误码表） |
| [execution-engine.md](execution-engine.md) | Celery 任务 / subprocess / 超时与回收 / HTML 报告 |
| [ai-generation.md](ai-generation.md) | OpenAPI 解析 / Prompt / LLM 护栏 / 审计 |
| [impact-analysis.md](impact-analysis.md) | 影响分析算法 / 一键回归 / fix-hints |
| [configuration.md](configuration.md) | 配置管理（九段 + .env.example 契约） |
| [TODO.md](TODO.md) | 分阶段完成项勾选台账（过程记录） |
| [../README.md](../README.md) | 项目入口：快速开始 / 核心特性 |
