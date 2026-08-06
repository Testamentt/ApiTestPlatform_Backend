# 路线图（roadmap.md）· 活文档

> 本文是项目方向活文档，随迭代更新（RULES.md §17-R5：变更沉淀五要素）。设计细节见 [architecture.md](architecture.md) 及各专题文档。

## 当前目标

完成 **Phase 0 文档沉淀**：全部设计文档经用户确认后，进入编码阶段。

## 关键约束

- 初始化分两阶段：**文档先行 → 用户确认 → 编码**（未确认不写代码）。
- 全部实现遵守 [`.claude/rules/RULES.md`](../.claude/rules/RULES.md)（16 节硬性规则）。
- 本地环境：Windows 10 / Python 3.12.6 / Redis 127.0.0.1:6379（requirepass=1234abcd）/ Docker 未安装。
- Windows 本地 Celery worker 必须 `--pool=solo`。

## 阶段路线

| 阶段 | 目标 | 验收 |
| --- | --- | --- |
| **Phase 0 · 文档与确认** | 全部设计文档（本次交付）+ 用户评审确认 | 文档间一致、评审通过 |
| **Phase 1 · MVP 最小闭环** | 项目骨架（pyproject + app 分层 + Alembic + 配置）+ 用例 CRUD + 环境管理 + 执行引擎（动态生成→subprocess→JUnit→回写 + 300s 超时劫持）+ Allure 链接 + **Vue 前端脚手架（frontend/：用例管理 + 任务看板 + draft 审核）** + Swagger UI（API 文档）+ CI | 10+ 用例并发、Web 响应 <50ms、死循环被强杀、`pytest -m "not slow"` 全绿、前端可完成「解析→生成→审核→执行→看结果」闭环 |
| **Phase 2 · AI 生成** | `prompts/v1/` + `llm_client` + OpenAPI 解析器 + draft/active 审核流 + generation_log | 单接口 2~3min、draft 不可执行、失败记录落库 |
| **Phase 3 · 影响分析** | api_definitions 快照 + diff + 反向检索 + Git Webhook + 一键回归 | 影响评估 <10s、覆盖率 100% |
| **Phase 4 · 生产化** | 鉴权加固（JWT）、限流、PostgreSQL、Docker Compose、Beat 周期扫描、自愈看板/监控、并发调优、AI 成本看板、Webhook 签名校验 | 可部署、可观测、可审计 |

## 已达成结论（Phase 0）

- [x] 前后端分离：Vue 3 前端层（frontend/）+ 后端三层架构（architecture.md）
- [x] 6 张业务表字段级设计 + Alembic 迁移策略（database.md）
- [x] 22 个 REST 端点 + 统一异步模式（api.md）
- [x] Celery 任务 + subprocess 执行 + 超时劫持（execution-engine.md）
- [x] Prompt 版本化管理 + llm_client 封装 + 三层防幻觉护栏（ai-generation.md）
- [x] 版本快照 + O(1) diff + SQL 反向检索（impact-analysis.md）
- [x] Pydantic Settings 字段树 + 配置契约（configuration.md + config/）

## 待解决问题

- [ ] 用户确认 Phase 0 全部文档内容（**进入编码的闸门**）
- [ ] 是否有真实可用的 Swagger/OpenAPI 样例接口用于联调（scripts/sample_swagger.py 可生成）
- [ ] 部署形态确认：本地直跑 vs Docker Compose（Docker 未安装，需评估）
- [ ] Webhook 平台确认：GitHub / GitLab / 其他
