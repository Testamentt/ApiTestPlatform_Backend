# TestPlatform — AI 测试用例生成平台

FastAPI + SQLAlchemy + SQLite + Celery + Redis + LLM API 驱动的测试用例生成平台（面试导向，MVP 零前端）：导入 Swagger/OpenAPI 文档，解析后用 LLM 自动生成测试用例（默认 `draft`，人工审核后转 `active`）。**MVP 界面 = FastAPI Swagger UI（/docs）**；Vue 3 为 Phase 4 可选增强（frontend/，面试不扣分，见 [docs/roadmap.md](docs/roadmap.md)）。

> 本文件只保留「每次生成代码都必须遵守」的核心内容，完整规则在 [.claude/rules/RULES.md](.claude/rules/RULES.md)，按需渐进读取。

## 不可协商红线（10 条，所有代码必须满足）
1. 注释只写「为什么」，禁止逐行翻译式注释
2. 禁止裸 `except: pass` / 吞异常
3. 所有外部调用必须设 timeout，值来自 config，禁止硬编码
4. SQLite 连接必须开启 WAL + busy_timeout + foreign_keys
5. LLM 调用必须走唯一封装 `llm_client`，禁止业务代码裸调 SDK
6. LLM 输出必须过 Pydantic schema 校验后才能入库
7. AI 生成的用例状态恒为 `draft`，禁止直接/自动置 `active`
8. 密钥只能放 .env，禁止入库/入日志/入 Prompt/入 git
9. 测试必须 mock LLM，禁止测试真调 API
10. 禁止 `Base.metadata.create_all` 建表，必须走 Alembic

## 协作纪律（每次会话）
- 新增/修改文件前先给计划（目标、涉及文件、方案、影响面），经确认后实施；骨架/脚手架文件除外
- 临时方案三步管理：先说明不可行原因 → 代码标 `TODO(workaround): <原因+替换方向>` → 说明残留风险与回收方式
- 测试与代码同批提交：小改动至少单元测试，大模块/核心链路改动单元 + e2e 都要
- 多轮任务需沉淀文档并随进度更新（五要素模板见 RULES.md §18）

## 核心约定（速览）
- 分层：`api → service → repository → model`，路由不写业务逻辑
- 外部调用（subprocess/LLM/HTTP）统一走 `app/utils/` 下的封装
- 事务必须短：禁止持有 DB Session 期间调 LLM/subprocess
- 长耗时生成走 Celery，接口立即返回 `task_id`

## 详细规则（按需读取 RULES.md）
| 主题 | 章节 | 何时读 |
|---|---|---|
| 代码质量（注释/docstring/命名） | §1 | 写任何 Python 代码前 |
| 技术约束（SQLite/Redis/超时/subprocess） | §2 | 涉及 DB、外部调用时 |
| 工程化（config/prompt/docker） | §3 | 加配置、改 prompt、部署时 |
| 目录与分层 | §4 | 新增文件时 |
| 数据模型与迁移 | §5 | 改模型、建表时 |
| 错误处理与日志 | §6 | 写接口、任务、异常时 |
| 接口规范 | §7 | 写路由时 |
| Celery 任务治理 | §8 | 写/改异步任务时 |
| AI 调用（LLM）规范 | §9 | 写任何 LLM 调用时 |
| 安全护栏 | §10 | 处理外部输入/LLM 输出/执行时 |
| 测试与防幻觉护栏 | §11 | 写测试、生成用例时 |
| SOP / Git / 自查清单 | §12–14 | 启动、提交、合入前 |
| 规范示例（正反例） | §15 | 不确定写法时 |
| 面试考点映射 | §16 | 面试前或讲解决策时 |
| 开发流程规则（R1-R6） | §17 | 开始任何改动前（计划/测试/沉淀/收敛） |
| 文档沉淀 | §18 | 多轮任务推进时 |

## 快速启动（在 backend/ 仓库内执行；前端为独立仓库 ../frontend，Phase 4 可选）
```bash
cd backend                       # 从容器根 E:\Project\TestPlatform 进入
pip install -e .                 # Python 3.11+
docker-compose up -d             # Redis + Worker
uvicorn app.main:app --reload    # http://localhost:8000/docs（Swagger UI 即 MVP 界面）
pytest -m "not slow"
# Phase 4 可选：Vue 前端（仅 2 页）
# cd ../frontend && npm install && npm run dev
```
完整 5 步 SOP 见 RULES.md §12。

## 项目技能
存放于 [.claude/skills/](.claude/skills/)（当前为空，格式说明见该目录 README）。
