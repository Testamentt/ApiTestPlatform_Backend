# 路线图（roadmap.md）· 活文档

> 本文是项目**方向**活文档（RULES.md §17-R5）：只维护「原则 / 当前状态 / 下一步」。**完成项台账**由 [TODO.md](TODO.md) 承载，**实现细节**由各专题文档承载（见文末文档索引）——三者分工，避免重复漂移。随每次迭代更新。

## 项目原则（面试导向）

本项目是针对面试的作品：引入任何新技术 / 依赖 / 功能前，先严格评估**学习成本 vs 面试收益**——讲不清、收益低、成本高的内容一律不做或后置。三大决策：

| 原则 | 内容 |
| --- | --- |
| 执行闭环优先 | 先跑通「接口 → 异步执行 → 查看结果」最小闭环，再补前端与鉴权 |
| 双界面 | 后端 Swagger UI（/docs）+ Vue 3 前端（frontend/，Phase 4 已实现 4 页） |
| 纯逻辑先行 | 先做「影响分析」（Diff + SQL，纯规则、不花钱、无幻觉），后做「AI 生成」（LLM） |

## 当前状态

**全部交付完成**：Phase 1 执行闭环 / Phase 2 影响分析 / Phase 3 AI 智能生成 / Phase 4 生产化 + Vue 前端 / 2026-08-12 全量 Code Review 批次 A·B 修复 / **批次 C Minor 加固（L1-L12 全部完成 + request_id 全链路追踪）**。门禁全绿：后端 188 pytest + e2e 1、前端 41+、ruff、覆盖率 91%（核心 90%）。

> 原「唯一未闭环验证项」H1 已于 2026-08-31 经 CI 容器内实测闭环（见「待解决问题」已闭环区）；无遗留验证阻塞。

## 交付里程碑总览

| 阶段 | 状态 | 交付要点 | 验收一句话 |
| --- | --- | --- | --- |
| Phase 0 · 文档与基建 | ✅ 完成 | 全套设计文档 + RULES.md + 双仓库 | 评审通过 |
| Phase 1 · 核心执行闭环 | ✅ 完成 | 用例 CRUD + 异步执行（Celery 202）+ HTML 报告 + 超时劫持 | Swagger UI：建用例 → confirm → 执行 → 报告 |
| Phase 2 · 变更影响分析 | ✅ 完成 | 版本快照 + 分段 hash O(1) diff + breaking 五场景 + 一键回归 | 新旧 Swagger → diff → 圈定 → 一键回归 |
| Phase 3 · AI 智能生成 | ✅ 完成 | llm_client + prompts/v1 + 三层防幻觉护栏 + 审计 | 生成 draft → 审核 → active |
| Phase 4 · 生产化与前端 | ✅ 完成 | Docker Compose + CI 三 job + Bearer Token + Vue 4 页 | 一键 `docker compose up` + 前端页面可用 |
| 2026-08-12 · Code Review 修复 | ✅ 完成 | H1-H4（白名单/报告转义/僵尸扫描/失败重试）+ M1-M5/M7 | 见 [reviews/2026-08-12-full-code-review.md](reviews/2026-08-12-full-code-review.md) |

## 面试演示路径（按卖点）

1. **异步解耦（Phase 1）**：`POST /cases` 建用例 → confirm → `POST /tasks` 立即返回 task_id（202）→ 轮询至 success → 打开 `report_link`。全程无需前端，Web 不阻塞。
2. **精准回归（Phase 2）**：Swagger 1.0 建 3 用例 → 传 Swagger 2.0（改 1 个接口入参）→ `POST /impact/analyze` 返回「变更 1、影响 1」→ 一键回归。
3. **AI 提效（Phase 3）**：上传 Swagger → `POST /generate` → 生成 draft（source=ai、trust_score=80/60）→ confirm → active；`POST /impact/{id}/fix-hints` 演示 breaking 修复建议。

> 完整 API 演练见 [api.md](api.md) §5；前端演示见 [frontend/README.md](../../frontend/README.md)。

## 待解决问题 / 下一步

**待闭环（建议优先级）**
- [ ] （可选）PostgreSQL/JWT、限流（Redis 固定窗口）——按 ROI 评估：限流 > JWT > PostgreSQL。

**已闭环（2026-08-31 · 演示稳定性 + H1 容器实测 + prompt 断言强化）**
- [x] **本地 mock 目标服务**：`scripts/mock_target.py`（httpbin 兼容子集：回显/`/status/{code}`/`/delay/{n}`/`/bearer`）；`.env.example` 与 compose 默认切本地 mock（`http://mock:9999`），`docker compose up` 全离线可复现。冒烟：2 用例执行 success（passed=2/2）+ HTML 报告，全程 3.4s。见 [execution-engine.md](execution-engine.md) §8.1。
- [x] **H1 容器内实测**：本机无 Docker → 搬进 CI——`docker-build` job 构建后 `docker run` 挂载 `scripts/verify_container.py` 容器内验证（真实可执行路径过白名单 + `python3.12` 前缀命中 + run_cmd 真实执行 + 白名单外命令真实拒绝）。
- [x] **prompt 断言强约束**：真实 LLM 冒烟发现 `assertions=0` → system.md 要求每条用例至少 1 条断言（响应 schema 字段校验，未定义 schema 用 `status_code` 等值）；复核冒烟 9 条用例 `assertions=1` 全命中、0 rejected。

**已闭环（原批次 C，2026-08 完成）**
- [x] request_id 全链路追踪（§6.2）、health redis close、`hmac.compare_digest`、MD5→sha256、`_BOUNDARY_RULES` 外置 prompts/、openapi 递归深度限制、execution_service 错误分类、GeneratedCase 长度对齐、repository rollback 包装、前端类型安全解包、Python 版本统一、测试 marker 归类、**前端 CI**（L10——见 [frontend/.github/workflows/ci.yml](../../frontend/.github/workflows/ci.yml)）。

**已闭环（AI 生成真实 LLM 冒烟，2026-08-28）**
- [x] 真实 DeepSeek 调用端到端冒烟（`.env` key 生效验证）：`POST /generate` → eager 任务 → `llm_client` 真调 `deepseek-chat` → 2/2 operation 覆盖、9 条 draft 用例（`source=ai`/`trust=80`、operation_id 服务端注入）、0 rejected、成本 ≈$0.002；request_id 全链路日志核验通过。沉淀：[sessions/2026-08-28-ai-generation-smoke.md](sessions/2026-08-28-ai-generation-smoke.md)。

**已定但未做**
- 自愈看板（Vue 页）：**不做**——AI UI 项目卖点，不重复造轮子（Phase 4 备注）。

## 文档索引

| 文档 | 角色 |
| --- | --- |
| [architecture.md](architecture.md) | 总体架构（双界面/三层/进程隔离/异步模型） |
| [database.md](database.md) | 数据模型（6 表字段级） |
| [api.md](api.md) | REST API 设计（端点总表 + 约定） |
| [execution-engine.md](execution-engine.md) | Celery 任务 / subprocess / 超时劫持 / HTML 报告 |
| [ai-generation.md](ai-generation.md) | OpenAPI 解析 / Prompt / LLM 护栏 / 审计 |
| [impact-analysis.md](impact-analysis.md) | 影响分析算法 / 一键回归 / fix-hints |
| [configuration.md](configuration.md) | 配置管理（九段 + .env.example 契约） |
| [TODO.md](TODO.md) | 分阶段完成项勾选台账 |
| [README.md](../README.md) | 快速开始 / 核心特性 |
