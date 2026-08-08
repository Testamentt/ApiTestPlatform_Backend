# Phase 2 变更影响分析 · 代码评审（2026-08-08）

> 评审对象：Phase 2「接口变更自动圈定受影响用例」纯规则引擎（parse / impact/analyze / impact/regression）。
> 关联：[RULES.md §14](../.claude/rules/RULES.md)（合入前自查）、[sessions/2026-08-08-review-fixes.md](../sessions/2026-08-08-review-fixes.md)、[impact-analysis.md](../impact-analysis.md)。

## 1. 评审范围与对象

Phase 2 全部新增代码与测试（2026-08-08 状态，含评审后接线与修复）：

| 层 | 文件 |
| --- | --- |
| 解析 | `app/utils/openapi_parser.py`（$ref/allOf/oneOf 递归 + 分段 hash + contracts） |
| diff | `app/utils/impact_diff.py`（O(1) diff + breaking 五场景 + suggested_remap） |
| 服务 | `app/services/impact_service.py`（analyze/regression）、`app/services/swagger_service.py`（parse 落库） |
| 数据 | `app/models/api_definition.py`、`app/models/impact_analysis.py`、`app/repositories/api_definition_repository.py`、`app/repositories/impact_analysis_repository.py` |
| API | `app/api/v1/parse.py`、`app/api/v1/impact.py`（接线） |
| 测试 | `tests/unit/test_openapi_parser.py`、`tests/unit/test_impact_diff.py`、`tests/unit/test_swagger_service.py`、`tests/unit/test_impact_service.py`、`tests/api/test_impact.py` |

## 2. 评审方法

- 全量代码走查 + 62 agent 多维度对抗性审查（影响分析算法 / 文档一致性 / 测试覆盖三视角重点）。
- 关键缺陷**实测复现**（字段级变更 hash 对比、版本覆盖快照、端点 404 枚举）。
- 修复后补 41 个测试（API 12 + swagger 6 + impact service 9 + 解析/diff 回归 14），104 全绿 + ruff 全绿复核。

## 3. 发现的问题与修复状态

### 3.1 Critical（已修复）

**hash 盲区：`_normalize` 剥离 properties 属性名，字段级变更对 diff 完全不可见**（[openapi_parser.py](../../app/utils/openapi_parser.py)）
- 现象：`_normalize` 对每层 dict 执行 `_STRUCT_KEYS` 白名单过滤，properties 的键是属性名（name/age/status）不在白名单内被整体剔除，归一化结果 `properties` 恒为 `{}`。
- 复现：请求体字段 `age: integer → string`、嵌套 enum `[active,pending] → [active]`、字段增删，`operation_hashes` 全部相等；而 `_extract_contract` 的 signature 正确捕获了变化——契约层与 hash 层不一致。后果：diff 的 `changed` 门禁由 hash 决定，字段级变更永不进 changed，`_is_breaking` 对最常见变更类型不可达，受影响用例永不圈定。
- 修复：`_normalize` 保留 properties 属性名键、递归过滤属性内部键；补 4 条字段级 hash 回归测试。

### 3.2 High（已修复）

| 发现 | 位置 | 修复 |
| --- | --- | --- |
| **API 未接线 + 文档谎报交付**：README 声称「Phase 2 已交付」，但 `/api/v1/parse` 与 `/api/v1/impact/*` 全部 404（parse.py 未注册、impact 路由文件缺失） | [api/v1/__init__.py](../../app/api/v1/__init__.py)、[README.md](../../README.md) | 新建 `impact.py`（analyze/regression）+ 注册 parse/impact；OpenAPI 端点 7→10，文档与实现对齐 |
| **版本碰撞覆盖 diff 基线**：`resolve_version` 无守卫 + `upsert` 同 version 先删旧插新，显式 new_version 等于 latest 时销毁对比基线、记录 `old_version==new_version`；latest 为非 vN 时 auto 回退算出已存在 v1 | [impact_service.py](../../app/services/impact_service.py)、[api_definition_repository.py](../../app/repositories/api_definition_repository.py) | analyze 中新版本号与对比基线相同 → 409；`resolve_version` auto 跳过已存在版本 |
| **响应状态码变化永不圈定**：breaking 判定只覆盖请求侧契约，200→202（下游按旧码断言失败）只进 changed 不圈定，与文档 F1 承诺矛盾 | [impact_diff.py](../../app/utils/impact_diff.py) | breaking 扩为五场景：响应状态码删减/替换判 breaking；contract 增加 `response_status_codes` |
| **核心代码零测试**：impact_service/swagger_service/repositories 无任何测试（§11.1/§17.2 违反） | tests/ | 补 41 测试（含宽容降级、版本守卫、字段级检测、affected_count 同步） |

### 3.3 Medium（遗留，接线前已评估）

- **path 级 parameters 被忽略**：OpenAPI Path Item 的公共参数未合并进 operation，其变化不可见（[openapi_parser.py](../../app/utils/openapi_parser.py)）。影响面较小（多数接口参数定义在 operation 层），留 Phase 3 完善。
- **allOf 合并丢约束 / oneOf 只取首分支**：allOf 只并 properties+required，分支的 minimum/maximum/pattern 丢失；oneOf/anyOf 取首个（有 warning 提示）。保守可接受（hash 不完整有告警），文档已说明。
- **e2e/integration 层缺失**：`tests/integration` 目录不存在，`integration` marker 是死配置。Phase 1 遗留同类问题，已在遗留清单标注。

### 3.4 Minor（遗留）

- 类型注解缺失：`_normalize`、`_is_breaking`、`diff_operations` 的 `old_ids` 等参数无注解（§1.3）。不影响运行，待统一清理。
- `response_status_codes` 为新增 contract 字段，旧版本快照无此键时 diff 用 `get(..., [])` 保守处理，不破坏已有数据。

## 4. §14 合入前自查清单（10 问）

| # | 自查项 | 结论 |
| --- | --- | --- |
| 1 | ruff/linter 通过 | ✅ `ruff check app tests` 全绿 |
| 2 | 无裸 except | ✅ 全部异常有日志/明确处理 |
| 3 | 外部调用带 timeout 且取自 config | ✅ parse/impact 无外部调用（纯规则）；subprocess 走 config timeout |
| 4 | 事务有 rollback/finally 兜底 | ✅ upsert/mark_regression/analyze 写点补 rollback |
| 5 | LLM 输出校验后写库且 status=draft | ➖ Phase 2 无 LLM（Phase 3 实施） |
| 6 | 无硬编码密钥、.env 未入库 | ✅ 密钥已占位符化 |
| 7 | pytest 全绿且不碰真实 LLM | ✅ 104 全绿；subprocess mock 隔离，不连真实 Redis/网络 |
| 8 | 注释只解释 why | ✅ 新增代码均带 why 注释（含 §1.4 强制点） |
| 9 | 配置走 Pydantic 并更新 .env.example | ✅ swagger 段 + socket_timeout/result_expires 同步 |
| 10 | 提交信息合规（Conventional Commits、无 AI 署名） | ✅ 分点小提交，仅本人署名 |

## 5. 评审结论

**有条件通过（修复后复核）**。Phase 2 核心算法设计成立（分段 hash O(1) diff + breaking 联合判定 + SQL 反向检索 + 宽容降级），但评审发现「hash 盲区」使字段级变更（最常见类型）完全不可见、且 API 未接线导致文档谎报交付——两者均属必须修复项，已全部修复并补齐测试。遗留的 path 级参数/类型注解/e2e 层为增量改进项，不阻塞 Phase 2 交付。

**验收链路（已冒烟通过）**：parse(v1) → 建 active 用例 → analyze(v2 字段级变更) → `breaking_changed=[listUsers]`、`affected=1` → regression → 202 + executed=1 → task success + report 生成。

## 6. 遗留问题

- path 级公共 parameters 未纳入提取/hash——Phase 3 完善解析器时补。
- allOf 合并仅保留 properties+required，其余约束丢失（有 warning）——可接受，后续增强。
- `tests/integration`（e2e）层缺失——与 Phase 1 同类，建议 Phase 3 前补「parse→analyze→regression 真实链路冒烟（`-m slow`）」。
- 类型注解补齐（§1.3）——批量清理项。
