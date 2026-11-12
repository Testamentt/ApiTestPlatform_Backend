<!-- 本项目编码格式：UTF-8（无 BOM）。 -->
# 文档总览

> 定位：`docs/` 的唯一入口：平台技术文档的地图与阅读路径。
> 事实源：`docs/*.md`、`scripts/doc_check.py` ｜ 最后核对：2026-11-12

## 1. 项目与文档

本项目是「智能化接口测试效能平台」的后端：导入 OpenAPI 文档后用 LLM 生成用例草稿，人工审核通过后在独立进程中执行并输出 HTML 报告；另提供接口变更影响圈定与一键回归。

`docs/` 收纳这套实现的技术文档。每篇只回答一类问题，篇首标出它的事实源模块与最后核对日期。文档与代码同批维护：两者不一致时以代码为准。

## 2. 文档地图

| 文档 | 定位（回答什么问题） | 事实源 |
| :--- | :--- | :--- |
| [architecture.md](architecture.md) | 系统长什么样：分层、进程隔离、执行器抽象、异步模型、关键取舍 | `app/main.py`、`app/api/v1/`、`app/services/`（含 `executors/`）、`app/tasks/supervisor.py`、`app/celery_app.py`、`docker-compose.yml` |
| [execution-engine.md](execution-engine.md) | 用例怎么被执行：Celery 任务、执行器（local / Jenkins）、subprocess 封装、超时与僵尸回收、报告 | `app/tasks/`、`app/services/execution_service.py`、`app/services/executors/`、`app/utils/subprocess_util.py`、`app/utils/jenkins_client.py`、`app/utils/junit_parser.py`、`app/utils/report_util.py` |
| [api.md](api.md) | 对外契约：端点、状态码、请求/响应字段、错误码、鉴权 | `app/api/v1/*.py`、`app/schemas/*.py`、`app/core/exceptions.py` |
| [database.md](database.md) | 数据怎么存：六张表、列与约束、索引、状态机、迁移取舍 | `app/models/*.py`、`app/core/database.py`、`app/repositories/` |
| [ai-generation.md](ai-generation.md) | AI 怎么生成用例：解析 → Prompt → LLM → 校验 → draft | `app/services/generation_service.py`、`app/utils/llm_client.py`、`app/utils/openapi_parser.py`、`app/core/config.py`、`prompts/v1/` |
| [impact-analysis.md](impact-analysis.md) | 变更怎么圈定回归：分段 hash、breaking 判定、反向检索、fix-hints | `app/services/impact_service.py`、`app/utils/impact_diff.py`、`app/utils/openapi_parser.py` |
| [configuration.md](configuration.md) | 配置怎么定义与覆盖：十段字段、env 覆盖、三处同步清单 | `app/core/config.py`、`config/settings.example.yaml`、`.env.example` |
| [encoding.md](encoding.md) | 全项目文本文件的编码契约：UTF-8 无 BOM、首行声明、四类门禁与乱码处置 | `scripts/encoding_check.py`、`.editorconfig`、`tests/unit/test_encoding_check.py` |
| [roadmap.md](roadmap.md) | 项目往哪走：原则、当前状态、里程碑、下一步（活文档） | 本文件 + [TODO.md](TODO.md) |
| [TODO.md](TODO.md) | 完成项台账：按里程碑分组的勾选清单（追加式记录） | 各专题文档 |
| [../README.md](../README.md) | 项目入口：项目描述、快速开始、目录结构、常见问题 | `scripts/`、`pyproject.toml`、`docker-compose.yml`、`Jenkinsfile` |

`docs/sessions/`、`docs/plans/`、`docs/reviews/` 是过程记录，不在本文地图内；`docs/examples/` 是数据文件，见 §4。

## 3. 阅读路径

按目标选入口：

- **先看全貌**：从 [architecture.md](architecture.md) 进，再按需读 [execution-engine.md](execution-engine.md)（执行链路）与 [api.md](api.md)（对外契约）。
- **接手维护**：[configuration.md](configuration.md) → [database.md](database.md) → [../README.md](../README.md)，后者有快速开始与常见问题。
- **改执行链路 / 排查任务不终态**：[execution-engine.md](execution-engine.md)，覆盖执行器（local / Jenkins）、超时、僵尸回收与报告。
- **改 AI 生成**：[ai-generation.md](ai-generation.md)。**改变更影响圈定**：[impact-analysis.md](impact-analysis.md)。
- **看项目往哪走**：[roadmap.md](roadmap.md) 与 [TODO.md](TODO.md)。

## 4. 示例数据与过程记录

`docs/examples/jsherp-openapi3.json` 是一份 Swagger/OpenAPI 3 文档，用作 `parse`、`impact/analyze`、`generate` 三个接口 `document` 字段的示例入参。它是数据文件，不是文档：接口契约变化时不需要跟着改它。

`docs/sessions/`、`docs/plans/`、`docs/reviews/` 存放会话沉淀、计划与评审记录，只在本地留存，随代码演进不回改；需要引用时写路径，不写链接。

## 5. 相关文档

- 项目入口：[backend/README.md](../README.md)
- 前端文档：[frontend/README.md](https://github.com/Testamentt/ApiTestPlatform_Frontend/blob/master/README.md)（独立仓库）
- 文档结构门禁：[scripts/doc_check.py](../scripts/doc_check.py)
- 完成项台账：[TODO.md](TODO.md)
