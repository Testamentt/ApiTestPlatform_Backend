# memory.md — 跨会话持久化偏好与项目级事实

> 本文件用于跨会话记忆，随项目演进更新。更新纪律见 RULES.md §17-R5：与变更同批沉淀。

## 沟通语言

- **始终使用中文**回答用户问题、撰写说明文档与 commit 信息。
- 代码、命令、标识符、文件路径保持英文原样，不做翻译。

## 项目事实

- 项目：智能化接口测试效能平台（TestPlatform），定位「AI 提效 + 异步解耦 + 精准回归」。
- 技术栈：**Vue 3 + Element Plus + Vite（前端，Phase 4 可选）** / FastAPI 0.115+ / SQLAlchemy 2.0+ / Celery 5.3+ / Redis / pytest 8.0+ / OpenAI 兼容 SDK / Pydantic Settings 2.0+ / Allure / Docker / GitHub Actions。
- 本地环境：Windows 10、Python 3.12.6、Redis 3.2 运行于 127.0.0.1:6379（requirepass=`1234abcd`）、Docker 未安装。
- **仓库结构（2026-08-06 起）**：`E:\Project\TestPlatform` 为容器目录（非 git 仓库）；`backend/`（FastAPI + 项目文档/规则/配置）与 `frontend/`（Vue 3）为**两个独立 git 仓库**，分别提交。git 提交**只保留本人署名**，禁止 `Co-Authored-By: Claude` 等 AI 协作者署名（见 RULES.md §13）。
- 本地启动 Celery worker 必须加 `--pool=solo`（Windows 不支持 prefork 池）。

## 项目原则（面试导向）

- **本项目是针对面试的作品**：引入任何新内容（技术/依赖/功能）前，先严格评估**学习成本 vs 面试收益**；低收益高成本的内容不做或后置。
- 三大决策：①执行闭环优先（先跑通「接口→异步执行→结果」，再补前端/鉴权）②MVP 零前端（Swagger UI；Vue 为 Phase 4 可选，面试不扣分）③纯逻辑先行（先影响分析 Diff+SQL，后 AI 生成 LLM）。

## 关键决策（与 docs/architecture.md 保持一致）

- MVP 界面 = FastAPI Swagger UI（零前端代码）；Vue 3（frontend/）降级为 Phase 4 可选增强，前期不做。
- 后端三层 + 进程隔离：Web 服务 ≠ 执行引擎；任务状态以 DB 为单一事实源，不依赖 Celery result backend。
- AI 用例默认 `draft`，人工确认后转 `active` 才可执行（防幻觉污染）。
- `test_cases.operation_id` 建立静态血缘，支撑影响分析反向检索。
- 执行任务 300s 超时，`taskkill /T /F` 强杀进程树，防死循环卡死系统。
- LLM 默认 DeepSeek（base_url=https://api.deepseek.com，模型 deepseek-chat），密钥走 `api_key_env` 环境变量。

## 文档纪律

- 遵循 [.claude/rules/RULES.md](.claude/rules/RULES.md)（§1-§18：代码/技术规则 + 开发流程 R1-R6 + 文档沉淀）。
- `docs/` 下 `plans/` `sessions/` `reviews/` 沉淀；会话文档五要素（当前目标 / 关键约束 / 已达成结论 / 待解决问题 / 下一步计划）。
