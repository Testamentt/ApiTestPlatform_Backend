# 路线图（roadmap.md）· 活文档

> 本文是项目方向活文档，随迭代更新（RULES.md §17-R5：变更沉淀五要素）。设计细节见 [architecture.md](architecture.md) 及各专题文档。

## 项目原则（面试导向）

**本项目是针对面试的作品**：引入任何新技术 / 依赖 / 功能前，先严格评估**学习成本 vs 面试收益**——面试讲不清、收益低、成本高的内容一律不做或后置。三大决策原则：

| 原则 | 内容 |
| --- | --- |
| 执行闭环优先 | 先跑通「接口 → 异步执行 → 查看结果」最小闭环，再补前端与鉴权 |
| MVP 零前端 | Phase 1 用 FastAPI Swagger UI（零前端代码）；Vue 降级为 Phase 4 可选，甚至不做（面试不扣分） |
| 纯逻辑先行 | 先做「影响分析」（Diff + SQL，纯规则引擎、不调 API、不花钱、无幻觉），后做「AI 生成」（LLM） |

## 当前目标

**Phase 1 核心执行闭环已完成**（MVP 简化版，沉淀见 [sessions/2026-08-06-phase1-mvp.md](sessions/2026-08-06-phase1-mvp.md)）；**Phase 2 变更影响分析已完成**（parse/impact/regression 三端点已接线，104 测试全绿，沉淀见 [sessions/2026-08-08-review-fixes.md](sessions/2026-08-08-review-fixes.md)）；当前进入 **Phase 3 AI 智能生成**（LLM，先做纯规则引擎后做生成）。

## 关键约束

- 初始化分两阶段：**文档先行 → 用户确认 → 编码**（未确认不写代码）。
- 全部实现遵守 `RULES.md`（§1-§18 硬性规则；面试导向原则见 §0）。
- 本地环境：Windows 10 / Python 3.12.6 / Redis 127.0.0.1:6379（requirepass 见本地 .env，勿提交）/ Docker 未安装。
- Windows 本地 Celery worker 必须 `--pool=solo`。
- 仓库结构：`backend/` 与 `frontend/` 两个独立 git 仓库（frontend 为 Phase 4 可选；前期界面 = Swagger UI）。

## 阶段路线

| 阶段 | 周期 | 目标 | 验收（面试演示） |
| --- | --- | --- | --- |
| **Phase 0 · 文档与基建** | 已完成 | 全部设计文档 + 双仓库 | 评审通过 |
| **Phase 1 · 核心执行闭环** | 已完成（MVP 简化） | 跑通「创建用例 → 异步执行 → 查看结果」，全程 Swagger UI，**不写一行前端** | 见下方验收清单 |
| **Phase 2 · 影响分析**（核心卖点 1） | 1 周 | 接口变更自动圈定受影响用例（**纯规则引擎，无 AI**） | 新旧 Swagger → diff → 圈定受影响用例 → 一键回归 |
| **Phase 3 · AI 智能生成**（核心卖点 2） | 1 周 | OpenAPI → LLM → draft 用例 → 人工确认转 active | 生成 5 个 draft → 审核 → active |
| **Phase 4 · 生产化与前端增强**（可选） | 锦上添花 | Docker Compose + GitHub Actions；Vue 可选（仅 2 页） | 一键 `docker compose up` 跑通 |

### Phase 1 · 核心执行闭环（已完成，MVP 简化）

> MVP 简化（面试导向）：Alembic→create_all、request_id 中间件→标准日志、Allure→HTML 报告、每 5 分钟扫描→**仅启动扫描一次（无 Beat）**、`{{base_url}}` 环境管理→base_url 写死 config、软删除→物理删除。31 测试全绿 + 真实异步端到端验证通过（uvicorn + celery worker --pool=solo + Redis + 本地 mock）。沉淀：[sessions/2026-08-06-phase1-mvp.md](sessions/2026-08-06-phase1-mvp.md)。

**Phase 1 验收标准（面试演示用）**：
1. 打开 http://localhost:8000/docs
2. 调用 `POST /api/v1/cases` 创建用例
3. 调用 `POST /api/v1/tasks` 传入 case_id，立即返回 `task_id`（202）
4. 轮询 `GET /api/v1/tasks/{task_id}`：`pending → running → pass/fail`

这是面试官最想看的「异步解耦」效果，全程无需前端。

### Phase 2 · 影响分析（已完成，核心卖点 1）

> 纯规则引擎（无 AI）：版本快照 → 分段 hash O(1) diff → breaking 五场景联合判定 → SQL 反向检索 → 一键回归（宽容降级 + 真实口径 + 可追溯）。评审修复（2026-08-08）：修复 `_normalize` 剥离属性名导致字段级变更不可见、响应状态码删减/替换不圈定用例、版本碰撞守卫、API 接线（parse/impact 路由）+ 104 测试全绿。沉淀：[sessions/2026-08-08-review-fixes.md](sessions/2026-08-08-review-fixes.md)。

**Phase 2 验收（面试演示用）**：Swagger 1.0 建 3 用例（绑 3 个 operation_id）→ 传 Swagger 2.0（改 1 个接口入参）→ `POST /api/v1/impact/analyze` 返回「变更 1 个接口、影响 1 个用例」→ `POST /api/v1/impact/{id}/regression` 一键回归。面试官据此认定你有「精准回归」思维。

### Phase 3 · AI 智能生成（核心卖点 2，实施中）

> **Phase 3.5 文档锁定前置**（防「已实现文档未同步」）：先锁定 ai-generation/configuration/database/api/architecture/roadmap 文档 → 契约确认 → 编码 → 3.5-C 编码后逐文档核对。设计见 [ai-generation.md](ai-generation.md)。

| 任务 | 安全护栏（防幻觉） |
| --- | --- |
| ① llm_client 唯一封装（openai SDK + _extract_json + 重试 + 成本） | 结构化输出 response_format + json.loads 兜底 |
| ② prompts/v1/ 模板（占位符注入 + 版本断言） | 防注入：结构化提取 + 定界符 + 第三方数据标注 |
| ③ generation_tasks 独立表 + generate_cases 异步任务 | 幂等 run_id + time_limit=600s 兜底 |
| ④ 生成用例强制 draft + confirm 审核 | 三层护栏：extra="forbid" 校验 + draft 恒为 + **operation_id 服务端注入** |
| ⑤ 校验失败落库（generation_log） | 失败记录同样落库 + raw_response + confidence=0，不建坏用例 |
| ⑥ 联动：定向生成（untested_ops）/ fix-hints / trust_score | 系统识别未覆盖接口 + AI 修复建议 + 血缘可信度 |

**验收**：上传 Swagger → `POST /api/v1/generate` → 轮询 → 生成 draft → `status=draft` 筛选 → confirm → active → 可执行。重点强调「人工审核」，杜绝面试官对幻觉的担忧。

> 砍掉/延后（Phase 4）：AI 采纳率埋点、model_chain 多模型 fallback、客户端令牌桶限流、多版本 prompt、动态信任降权、前端展示。

### Phase 4 · 生产化与前端增强（可选，锦上添花）

| 任务 | 说明 |
| --- | --- |
| ① Docker Compose（FastAPI + Redis + Worker + SQLite） | **必须做**，面试一键跑起来 |
| ② GitHub Actions（两段式 CI） | 简历「已容器化部署 + CI 门禁」 |
| ③ 迁移 PostgreSQL（可选） | 一句话带过即可 |
| ④ 基础鉴权（Bearer Token，值在配置中） | **方案 A：Phase 1 无鉴权**（面试演示开箱即用），Phase 4 补上 |
| ⑤ Vue 前端（可选，若做仅 2 页：用例列表 + 任务看板） | 其他功能继续用 Swagger UI |
| ⑥ JWT 鉴权替换 Bearer Token（可选） | — |

> 若不做 Vue，Phase 4 缩减为「Docker Compose + GitHub Actions」，简历写「已容器化部署」。
> **自愈看板（Vue 页）不再做**——是 AI UI 项目的卖点，不重复造轮子。

## 已达成结论

**Phase 0（文档与基建）**
- [x] 后端三层架构 + MVP 零前端（Swagger UI）设计（architecture.md）
- [x] 2 张业务表字段级设计（test_cases/tasks）+ create_all 迁移策略（database.md，Phase 4 切 Alembic）
- [x] REST 端点 + 统一异步模式（api.md）
- [x] Celery 任务 + subprocess 执行 + 超时劫持（execution-engine.md）
- [x] Prompt 版本化管理 + llm_client + 三层防幻觉护栏（ai-generation.md，Phase 3 前瞻）
- [x] 版本快照 + O(1) diff + SQL 反向检索（impact-analysis.md，Phase 2 前瞻）
- [x] Pydantic 配置字段树 + 配置契约（configuration.md + config/）
- [x] 双仓库结构（backend/ + frontend/，frontend 可选）

**Phase 1（核心执行闭环，已完成）**
- [x] 用例 CRUD + confirm 审核（operation_id 必填、draft 防幻觉护栏）
- [x] 任务 Lookup-Create 幂等（run_id=sha256 + UNIQUE）+ Celery 异步（202 立即返回）
- [x] 执行引擎（run_cmd 白名单/超时/on_start 落 pid + junit 累加解析 + HTML 报告）
- [x] 超时劫持 scan_stale_tasks（DB pid 权威杀树，Worker 启动扫描一次，无 Beat）
- [x] 104 测试全绿 + ruff 全绿 + 真实异步端到端验证

**Phase 2（变更影响分析，已完成）**
- [x] api_definitions / impact_analyses 表（版本快照 + 分段 hash + contracts + 影响结果）
- [x] `POST /api/v1/parse`：Swagger 解析入库（$ref/allOf/oneOf 递归 + reparse 覆盖）
- [x] `POST /api/v1/impact/analyze`：O(1) diff + breaking 五场景联合判定 + SQL 反向检索
- [x] `POST /api/v1/impact/{id}/regression`：一键回归（宽容降级 + 真实口径 + last_regression 可追溯）
- [x] 字段级变更命中（`_normalize` 保留属性名）、响应状态码删减/替换圈定、版本碰撞守卫、v1/v2/v3 连续对比

## 待解决问题

- [ ] 演示 target 稳定性：默认 httpbin.org 外网不稳，面试建议本地 mock（改 `execution.base_url` 即可）
- [ ] Swagger/OpenAPI 样例接口（Phase 3 的 scripts/sample_swagger.py 可生成）
- [ ] 部署形态：本地直跑 vs Docker Compose（Phase 4）
- [ ] Phase 4 是否做 Vue（面试不扣分，可跳过）
