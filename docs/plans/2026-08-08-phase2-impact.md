# Phase 2 变更影响分析 · 实现计划（2026-08-08）

> 本文是 Phase 2 已批准计划的执行沉淀（对齐 `docs/impact-analysis.md` 简化版）。结构遵循 `docs/plans/README.md`：目标 / 现状 / 涉及文件 / 验收标准。

## 1. 目标

接口变更自动圈定受影响用例 → 一键回归。**纯规则引擎**（Diff+SQL，无 AI、零新增依赖、同步秒回），对齐 roadmap「纯逻辑先行」：先做影响分析，后做 AI 生成（Phase 3）。

面试卖点：`operation_id` 血缘（Phase 1 已埋）+ **分段 hash O(1) diff** + **breaking 联合判定** + SQL 反向检索 + 一键回归（宽容降级 + 结果持久化）。

## 2. 现状

- Phase 1 已交付：用例 CRUD + confirm、任务 Lookup-Create 幂等、Celery 异步执行、超时劫持、HTML 报告（31 测试全绿 + 真实异步端到端验证）。
- 数据基础：`test_cases.operation_id` NOT NULL + 索引（血缘依据）、物理删除（无软删除）、`create_all` 建表。
- 本文档实施前，需先完成 `docs/` 各文档降级对齐（impact-analysis / database / api / architecture / configuration / ai-generation，已完成）。

## 3. 涉及文件

**新增（代码）**
```
app/utils/openapi_parser.py            # 自研 OpenAPI 3.x 解析器
app/utils/impact_diff.py               # diff + breaking 纯函数 + suggested_remap
app/models/api_definition.py           # 版本快照表
app/models/impact_analysis.py          # 影响分析表（@validates 同步 affected_count）
app/schemas/swagger.py                 # ParseRequest/ParseResult
app/schemas/impact.py                  # AnalyzeRequest/AnalyzeResult/RegressionResult
app/repositories/api_definition_repository.py   # get_latest/get_by_version/upsert（reparse 覆盖）
app/repositories/impact_analysis_repository.py  # mark_regression（F3 持久化）
app/services/swagger_service.py        # parse_document（大小校验 + 透出 warnings）
app/services/impact_service.py         # analyze / regression（宽容降级 + 审计日志）
app/api/v1/parse.py                    # POST /parse → 201
app/api/v1/impact.py                   # POST /impact/analyze → 200；POST /impact/{id}/regression → 202
```

**修改**
```
app/core/config.py                     # + SwaggerSettings（max_upload_bytes/hash_version/max_operation_ids_warn，全带 default）
app/models/__init__.py                 # 注册 2 新表
app/repositories/case_repository.py    # + find_by_ids / find_by_operation_ids / bound_operation_ids
app/api/v1/__init__.py                 # 注册 parse/impact 路由
config/settings.yaml + settings.example.yaml + .env.example   # + swagger 段
docs/impact-analysis.md database.md api.md architecture.md configuration.md ai-generation.md README.md  # 降级/同步（已完成）
```

**新增（测试）**
```
tests/unit/test_openapi_parser.py      # 提取/$ref/allOf/oneOf/array/归一化/分段 hash/warnings/宽容/非法
tests/unit/test_impact_diff.py         # diff 全分支 + breaking 四场景 + suggested_remap
tests/api/test_parse.py                # 201/reparse 覆盖/超大小/非法/warnings 透出
tests/api/test_impact.py               # analyze 全场景 + regression 降级/持久化/审计
```

**沉淀**
```
docs/sessions/2026-08-08-phase2-impact.md   # 会话五要素
```

## 4. 关键设计决策（面试防守）

| 决策 | 说明 |
| --- | --- |
| 分段 hash + 状态码指纹 | `{op_id: {request, response}}`；response 含 `status_codes` 指纹——200→202 必命中（F1） |
| hash 白名单键 | 只算 type/required/enum/min-max 等结构键；description 不参与——改注释不误报 |
| breaking 联合判定 | required 收紧 / type 变化 / 字段删除 / 枚举删减 → 触发圈定；仅放宽不圈定（D1/F2） |
| orphaned + suggested_remap | removed 接口用例进迁移清单不自动回归；difflib 相似名只建议不自动重绑（D2） |
| untested_ops | 未绑用例接口清单，首次分析=全部，为 Phase 3 铺路（D3） |
| 宽容解析带 warnings | $ref 缺失/跨文件/重复 operation_id 逐条透出，不静默（D4/D5/F4） |
| 回归宽容降级 + 真实口径 | 过滤失效用例、dropped 附 reason；summary=executed+dropped 重算（D6/追问 3） |
| 回归持久化 | last_regression_* 落库 + 结构化日志审计（F3） |
| reparse 覆盖 | 同 version 先删旧插新，CI 幂等不膨胀（取舍 2） |
| hash_version | 算法升级旧快照不重建，diff 不一致全标 changed |

## 5. 验收标准

```bash
pytest -q && ruff check app tests && ruff format --check .
```

**/docs 手动验收（面试演示）**：
1. `POST /parse` Swagger 1.0（3 接口含 operationId）→ 201，operation_count=3。
2. `POST /cases` ×3 绑 op_id → confirm active。
3. `POST /impact/analyze` Swagger 2.0（POST /users 新增 required）→ `breaking_changed_ops=["createUser"]`、`affected_summary.total=1`。
4. `POST /impact/{id}/regression` → 202 → 轮询 success；再查 analysis 见 `last_regression_*` 更新。
5. 重复 analyze 相同文档 → identical。
6. 仅去掉 required（放宽）→ changed 有、breaking 空、无 affected（不误报）。
7. 响应 schema 相同但 200→202 → changed 命中（状态码指纹）。
8. 删除受影响用例 → 再 regression → executed/dropped 分明、summary 真实口径、dropped_reasons 含 `case deleted`。
9. 接口改名 listUsers→getUsers → orphaned_case_ids + `suggested_remap={"listUsers":"getUsers"}`。
10. 两个接口共用 operation_id → warnings 含重复提示。

**测试覆盖**：unit（解析器全分支 + diff 全分支含 breaking 四场景）、api（parse/analyze/regression 正常 + 异常 + 降级 + 持久化 + 审计）。
