# 文档总览与写作规范

> **定位**：`docs/` 唯一入口——文档地图、统一格式规范、技术文档与学习文档（双轨）映射。
> **状态**：现行有效 ｜ **最后核对**：2026-10-08（对应实现：工作区最新代码）
> **规则引用**：`RULES.md` §18（文档沉淀）、§19（文档双轨制）、§17-R5 ｜ **双轨镜像**：`E:\Project\docs-study\TestPlatform\backend\docs\README.md`

---

## 1. 文档地图（现行有效，参与双轨）

| 文档 | 定位（回答什么问题） | 事实源锚点 | 学习版镜像 |
| :--- | :--- | :--- | :--- |
| [architecture.md](architecture.md) | 系统长什么样：分层、进程隔离、异步模型、关键取舍 | `app/main.py`、`app/api/v1/`、`app/services/`、`docker-compose.yml` | `E:\Project\docs-study\TestPlatform\backend\docs\architecture.md` |
| [execution-engine.md](execution-engine.md) | 用例怎么被执行：Celery 任务、subprocess 封装、超时与僵尸回收、报告 | `app/tasks/`、`app/services/execution_service.py`、`app/utils/subprocess_util.py`、`app/utils/junit_parser.py`、`app/utils/report_util.py` | 同名镜像 |
| [api.md](api.md) | 对外契约：端点、状态码、请求/响应字段、错误码、鉴权 | `app/api/v1/*.py`、`app/schemas/*.py`、`app/core/exceptions.py` | 同名镜像 |
| [database.md](database.md) | 数据怎么存：六张表、列与约束、索引、状态机 | `app/models/*.py`、`app/core/database.py`、`app/repositories/` | 同名镜像 |
| [ai-generation.md](ai-generation.md) | AI 怎么生成用例：解析 → Prompt → LLM → 校验 → draft | `app/services/generation_service.py`、`app/utils/llm_client.py`、`app/utils/openapi_parser.py`、`prompts/v1/` | 同名镜像 |
| [impact-analysis.md](impact-analysis.md) | 变更怎么圈定回归：分段 hash、breaking 判定、反向检索、fix-hints | `app/services/impact_service.py`、`app/utils/impact_diff.py`、`app/utils/openapi_parser.py` | 同名镜像 |
| [configuration.md](configuration.md) | 配置怎么定义与覆盖：九段字段、env 覆盖、三处同步清单 | `app/core/config.py`、`config/settings.example.yaml`、`.env.example` | 同名镜像 |
| [roadmap.md](roadmap.md) | 项目往哪走：原则、当前状态、里程碑、下一步（活文档） | 本文件 + [TODO.md](TODO.md) | 同名镜像 |
| [TODO.md](TODO.md) | 完成项台账：按里程碑分组的勾选清单（过程记录，**不参与双轨**） | 各专题文档 + `docs/sessions/` | 无（历史记录目录） |
| [../README.md](../README.md) | 项目入口：亮点、快速开始、目录结构、常见问题 | `scripts/`、`pyproject.toml`、`docker-compose.yml` | `E:\Project\docs-study\TestPlatform\backend\README.md` |

## 2. 不参与双轨的目录（历史 / 过程记录，禁改既有内容）

| 位置 | 内容 | 说明 |
| :--- | :--- | :--- |
| [sessions/](sessions/) | 会话沉淀（五要素） | 过程记录，按时间追加，不回改 |
| [plans/](plans/) | 计划文档 | 实施前的计划，不回改 |
| [reviews/](reviews/) | 评审记录（含全量代码审查报告） | 结论性历史，不回改 |
| [session-doc-template.md](session-doc-template.md) | 会话文档模板 | 模板，稳定 |
| [TODO.md](TODO.md) | 完成项台账 | 只勾选、不回改描述 |
| [examples/](examples/) | 示例数据（Swagger 产物） | 数据文件，非文档 |

> 归档约定：**任何评审/报告类产物（如代码审查报告）一律归入 `docs/reviews/`**，不留在仓库根或容器根。

## 3. 统一格式规范（10 条硬性约定）

1. **文件头定位块**：标题下紧跟引用块，依次给出 `定位`（一句话）、`覆盖范围` + `状态` + `最后核对` 日期、`事实源锚点` + `双轨镜像`、`规则引用`。凡参与双轨的文档必须有该块（4 行，顺序固定）。
2. **章节编号**：H2 统一 `## N. 标题`（从 1 连续编号）；H3 用 `### N.M 标题`。禁止 H2 无编号与 `## 4.1` 这类层级错位。**例外**：历史/过程记录类（`TODO.md`、`session-doc-template.md`、`docs/sessions|plans|reviews/`）按追加式分组，不强制编号。
3. **表格样式**：统一 `| 列 | 说明 |` + 分隔行 `| :--- | :--- |`；表格内不放长段落。
4. **代码块必须标语言**：`bash` / `python` / `sql` / `yaml` / `json` / `text` / `mermaid`；禁止裸 ` ``` `。
5. **图优先 Mermaid**：状态机、时序、流程用 `mermaid` 代码块（可渲染、可镜像），不用纯 ASCII 画图。
6. **事实必须可回查**：写端点、配置键、阈值、表列、状态枚举时，同段落给出模块路径或 `文件:行`；不确定的数值写 `[待确认: xxx]`，禁止编造。
7. **数值唯一事实源**：配置默认值以 `config/settings.example.yaml` + `app/core/config.py` 为准；超时/上限等阈值以代码为准，文档只做镜像。
8. **结尾固定「相关文档」**：所有专题文档以 `## N. 相关文档` 收尾，**仓库内**链接用相对路径（禁止写 GitHub 绝对 URL——分支名会漂移）；**跨仓库**链接（如 `frontend/`、学习版镜像仓库）是唯一例外，用 GitHub 绝对 URL。
9. **术语统一**：`被测系统`（不写"目标系统"）、`用例`（不写"测试用例"混用）、`任务`（执行任务）/`生成任务`（AI 生成区分）、`草稿 draft` / `已激活 active`。
10. **单篇 ≤ 单主题**：同一事实只在一处详述，其他文档用一句话 + 链接指回（避免多文档重复漂移）。

## 4. 维护与同步

**何时更新**：代码合入即同批更新对应专题文档（`RULES.md` §18 / §19.4）；只改一边视为任务未完成。

**四查核对清单**（每次改文档后逐项过）：
1. 端点路径 + HTTP 方法 + 状态码是否与 `app/api/v1/` 一致。
2. 配置键名 + 默认值是否与 `app/core/config.py`、`config/settings.example.yaml`、`.env.example` 三处一致。
3. 数字阈值（超时、上限、重试、页大小、覆盖率门槛）是否与代码/配置一致。
4. 状态枚举与迁移是否与 `app/models/enums.py`、状态写入点一致。

**机器门禁（P3-6）**：改完文档跑 `python scripts/doc_check.py`——它把上面四条里可机械判定的部分固化下来（源文档相对链接可达 / 代码围栏必标语言 / H2 编号连续；镜像的版本印记、原子块缺失、废弃口径回流、虚词），CI 的 `test-fast` 已接入。人只需复核语义（端点语义、设计意图、类比是否贴切）。

**学习文档同步**：按 `RULES.md` §19.3 SOP 执行——Diff 识别 → 冲突熔断 → 类比刷新 → 版本印记（`源版本: <commit> | 同步日期: <UTC>`）→ 三重校验 → 推送 `docs_study`。镜像路径与源文档一一对应（见第 1 节表）。

**镜像工作区唯一性**：学习文档工作区全盘唯一，恒为 `E:\Project\docs-study`（`RULES.md` §19.0-3 / §19.4）。本仓库内**不得**出现 `docs-study` 文件夹；需要同步时直接进入该工作区，禁止再克隆第二份。

## 5. 相关文档

- 规则与流程：[backend/.claude/rules/RULES.md](../.claude/rules/RULES.md)
- 项目入口：[backend/README.md](../README.md)
- 前端文档：[frontend/README.md](https://github.com/Testamentt/ApiTestPlatform_Frontend/blob/master/README.md)（独立仓库）
- 学习文档镜像仓库：[docs_study](https://github.com/Testamentt/docs_study)（本地工作区 `E:\Project\docs-study`，全项目唯一）
