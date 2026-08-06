# 智能化接口测试效能平台

> **AI 提效 + 异步解耦 + 精准回归** —— 让接口用例编写从「重复劳动」变为「审核确认」，让回归范围从「经验猜测」变为「血缘圈定」，让执行从「阻塞卡死」变为「异步兜底」。

基于 FastAPI + Celery + AI 的轻量级、可扩展接口测试效能平台。

## 它解决什么

微服务架构下，接口测试面临三大核心痛点：

| 痛点 | 现状 | 本平台方案 |
| --- | --- | --- |
| 用例编写耗时 | 单接口覆盖正向/逆向/边界值/异常场景，手动编写重复性极高（10~15 min/接口） | **AI 辅助用例智能生成**：解析 OpenAPI → 结构化 Prompt → LLM 生成 → 人工审核（2~3 min/接口） |
| 变更影响不可控 | 接口定义变更后回归范围凭经验判断，漏测风险高（1~2 h） | **接口变更影响自动圈定**：Git Webhook → Diff → 受影响用例反向检索 → 一键回归（<10 s，覆盖率 100%） |
| 执行阻塞 | Web 服务同步调 pytest，长耗时导致请求超时、服务不可用（同步超时率 30%+） | **异步任务解耦 + 超时兜底**：Celery + Redis 异步队列，Web 响应稳定 <50 ms，300s 超时强杀 |

## 架构速览

```
用户 / CI 系统
   │ REST /api/v1（Bearer Token）
   ▼
【MVP 界面】 FastAPI Swagger UI（/docs，零前端代码；Phase 4 可选 Vue 3）
   │ HTTP JSON
   ▼
【Web 服务层】 FastAPI + SQLAlchemy   （用例 CRUD / Swagger 解析 / AI 用例生成）
   │ ① 创建任务记录  ② 发送任务到队列
   ▼
【Redis Broker】（Celery 队列）
   │ ③ Worker 拉取任务
   ▼
【执行引擎层】 Celery Worker（独立进程）
   ④ subprocess 执行 pytest（动态生成 test_xxx.py / 超时劫持 300s / 解析 JUnit XML）
   ⑤ 结果回写 DB  ⑥ 生成 Allure 报告链接
```

MVP 零前端：界面 = FastAPI Swagger UI（`/docs`）；后端三层中 Web 服务 ≠ 执行引擎（进程隔离），状态以 DB 为单一事实源，Worker 重启任务不丢。Vue 3 为 Phase 4 可选增强。详见 [docs/architecture.md](docs/architecture.md)。

## 目录结构（两个独立 git 仓库）

```
TestPlatform/                # 容器目录（非 git 仓库）
├── backend/                 # 后端仓库：FastAPI + Celery + AI + 项目文档/规则/配置（本仓库）
└── frontend/                # 前端仓库：Vue 3（Phase 4 可选，MVP 用 Swagger UI）
```

## 快速开始

> ⚠️ 项目当前处于**文档沉淀阶段**：以下命令在编码阶段（Phase 1）启用，当前不可执行。

**环境要求**：Python 3.12+、Redis（本地 127.0.0.1:6379）。

```bash
# 以下命令在 backend/ 仓库内执行；前端为独立仓库 ../frontend

# 1. 安装依赖（编码阶段）
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"

# 2. 配置
copy config\settings.example.yaml config\settings.yaml
copy .env.example .env        # 填入 DEEPSEEK_API_KEY、Redis 密码

# 3. 启动后端 Web 服务（Swagger UI 即 MVP 界面：http://127.0.0.1:8000/docs）
uvicorn app.main:app --reload --port 8000

# 4. 启动 Worker（Windows 必须 --pool=solo）
celery -A app.celery_app:celery_app worker --pool=solo

# （Phase 4 可选）Vue 前端：cd ../frontend && npm install && npm run dev
```

## 核心特性

- ✅ **AI 用例智能生成**：自研 OpenAPI 3.0 解析器（递归提取 operationId / 参数约束 / 边界值），结构化 Prompt 生成正向/逆向/边界值用例；生成结果默认 `draft`，人工确认转 `active` 才可执行，杜绝幻觉污染。
- 🧠 **接口变更影响圈定**：`operation_id` 静态血缘 + 新旧 OpenAPI Diff + SQL 反向检索，受影响的既有用例自动圈定，支持一键回归。
- ⚡ **异步解耦 + 超时兜底**：请求即返回 `task_id`，pytest 由独立 Worker 异步执行；300s 超时自动强杀进程树，死循环不卡系统。
- 📊 **Allure 报告**：任务执行后自动生成 Allure 报告并挂载链接。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | MVP 零前端、后端三层、进程隔离、异步模型、核心决策 |
| [docs/database.md](docs/database.md) | 数据模型（6 表字段级设计） |
| [docs/api.md](docs/api.md) | REST API 设计（端点总表） |
| [docs/execution-engine.md](docs/execution-engine.md) | Celery 任务、subprocess 执行、超时劫持、Allure |
| [docs/ai-generation.md](docs/ai-generation.md) | OpenAPI 解析、Prompt 设计、draft→active 审核流 |
| [docs/impact-analysis.md](docs/impact-analysis.md) | 变更影响分析算法、Webhook、一键回归 |
| [docs/configuration.md](docs/configuration.md) | 配置管理（Pydantic Settings） |
| [docs/roadmap.md](docs/roadmap.md) | 迭代路线（活文档） |
| [CLAUDE.md](CLAUDE.md) | Claude 协作指南 |
| [.claude/rules/RULES.md](.claude/rules/RULES.md) | 项目规则（§1-§16 代码/技术 + §17 开发流程 R1-R6 + §18 文档沉淀） |
