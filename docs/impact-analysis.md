# 变更影响分析设计（impact-analysis.md）· Phase 2-3 实现版

> 规则引用：`RULES.md` §5.2（索引）、§6.2（日志）。本文描述接口变更影响自动圈定的核心算法：版本快照 → 分段 hash O(1) diff → breaking 判定 → SQL 反向检索 → 一键回归 + fix-hints 修复建议。对应规格书亮点 ②。
> **Phase 2 已实现（面试导向，纯规则引擎无 AI）**：`parse / impact/analyze / impact/{id}/regression` 三端点；**Phase 3 新增 `impact/{id}/fix-hints`**（breaking 修复建议，轻量 LLM，best-effort）；Git Webhook、字段级差异 UI、priority 字段均延后。

## 1. 数据源：api_definitions 版本快照

每次 Swagger 变更 → `POST /api/v1/parse` 解析 → 提取 `operation_ids` + **分段 hashes** + **operation_contracts** → 作为新版本 INSERT `api_definitions`。

| 字段 | 说明 |
| --- | --- |
| version | UNIQUE；用户标签或 auto `v{n}`；**reparse 覆盖**（同 version 再解析先删旧插新，CI 重复触发不膨胀） |
| hash_version | 哈希算法版本（config `swagger.hash_version`）；升级时旧快照不重建，diff 版本不一致公共接口保守全标 changed |
| operation_ids | 血缘全集 |
| operation_hashes | **分段** `{op_id: {"request": md5, "response": md5}}`——回答「请求变了还是响应变了」，为 Phase 3 字段级差异留数据口子 |
| operation_contracts | `{op_id: {"required": [...], "signature": {field_path: {"type","enum"}}, "response_status_codes": [...]}}`——breaking 判定的第二层输入（response_status_codes 供状态码变化判定） |

> **分段 hash 关键**：`response` 段含**状态码指纹** `{"status_codes": sorted, "schemas": {...}}`——200→202（异步化改造，语义变响应体不变）也必须被检测，否则用例按 200 断言会静默失败。
> **hash 白名单键**：只对影响用例请求/断言的结构键（type/required/enum/min-max…）规范化，description/example/default 不参与——改注释不误报、改约束必命中。

## 2. breaking 联合判定（不只比 required）

hash 回答「结构变没变」；`operation_contracts` 对比回答「是否破坏性」。`_is_breaking` 五场景命中即 breaking：

| # | 场景 | 判定 |
| --- | --- | --- |
| ① | required 收紧（new 多出必需项，false→true） | breaking |
| ② | 字段 type 变化（string→integer） | breaking |
| ③ | 枚举值删减（["active","pending"] → ["active"]） | breaking |
| ④ | 字段被删除（下游断言 KeyError） | breaking |
| ⑤ | 响应状态码删减/替换（200→202：旧码不再返回，下游按旧码断言失败；F1） | breaking |
| — | 仅放宽（去掉 required、类型兼容、枚举新增、响应码新增） | **non-breaking，不圈定回归**（避免误报） |

> 面试话术：「仅比 required 会漏报 type 变化/字段删除/枚举删减/状态码替换四类破坏性变更——联合判定五场景全命中，放宽不误圈。」

## 3. Diff 算法

```
old = api_definitions[old_version 或最近];  new = 本次解析结果

1. removed = old_ids - new_ids                # 接口删除 → 进迁移清单（不自动回归）
2. added   = new_ids - old_ids                # 新增 → 提示可生成用例
3. common  = old_ids ∩ new_ids
4. changed = [op in common if old_hashes[op] != new_hashes[op]]   # 结构变化
5. breaking_changed = [op in changed if _is_breaking(old.contracts[op], new.contracts[op])]
6. identical = not added and not removed and not changed
```

## 4. SQL 反向检索（面试必问核心 SQL）

```python
# affected（圈定回归）：只查破坏性变更绑定的 active 用例
SELECT * FROM test_cases WHERE operation_id IN (breaking_changed) AND status = 'active'
# orphaned（迁移清单）：removed 接口绑定的用例
SELECT * FROM test_cases WHERE operation_id IN (removed_ops)
# untested（Phase 3 铺路）：new 版本中未绑任何 active 用例的接口
SELECT operation_id FROM api_definitions... WHERE operation_id NOT IN (已绑定集合)
```

命中 Phase 1 已建索引 `idx_test_cases_operation_id`。

## 5. 影响分析结果模型（impact_analyses 表）

| 字段 | 说明 |
| --- | --- |
| old_version / new_version | 对比版本对 |
| added_ops / removed_ops / changed_ops | 三态集合 |
| breaking_changed_ops | **唯一触发回归圈定** |
| affected_case_ids + affected_count | breaking 变更的 active 用例 id 快照；count 由模型层 `@validates` **整体赋值时同步**（写入口收敛整体替换，禁止原地 append） |
| orphaned_case_ids | removed 接口绑定的用例——**迁移清单**，提示人工重绑/删除 |
| suggested_remap | `{removed_op: added_op}` 相似名配对（difflib 规范化，**只建议不自动重绑**，保守避免误绑） |
| untested_ops | 未绑用例的接口清单——**首次分析 = 全部接口**，直接做 Phase 3 批量生成骨架 |
| affected_summary | `{total}`（快照期；**回归时按执行时真实口径重算**） |
| last_regression_at / last_regression_task_id / last_regression_executed_count | **回归结果持久化**：每次一键回归更新，可追溯「为什么这次只跑了 N 个」 |

## 6. 一键回归（宽容降级 + 真实口径 + 审计）

```
POST /impact/{analysis_id}/regression
  → 读 affected_case_ids 快照
  → 宽容过滤：只执行当前仍 active 的，dropped 附 reason（case deleted / case draft）
  → 全失效 → 422；复用 Phase 1 TaskService.create_execution_task（Lookup-Create 幂等）
  → summary 执行时真实口径 {total: executed+dropped, executed, dropped}（不用旧快照，防误导）
  → mark_regression 落库 last_regression_* + 结构化日志审计（analysis_id/task_id/executed/dropped/reasons）
  → 202 {task_id}
```

## 6.1 fix-hints 修复建议（Phase 3 联动，best-effort）

```
POST /impact/{analysis_id}/fix-hints
  → 读 breaking_changed_ops（无则 422）
  → 短事务分界：先查关事务再调 LLM（§2.1）
  → prompts/v1/fix_hint_* 模板渲染（breaking_ops 经 repr 定界注入，§3.2/§10.1）
  → llm_client.chat_json(FixHint schema) → 审计写 generation_logs(model='fix_hint'，成功/失败均落库，§9.6)
  → 成功：ai_fix_hint = {breaking_changed_ops, suggestion}；失败：置 None 不阻塞（best-effort）
```

- `AnalyzeResult` 返回 `has_fix_hint` + `fix_hint_endpoint` 提示入口——analyze 保持纯规则秒回，建议按需生成。
- 设计权衡：fix-hints 为轻量同步建议（Web 请求线程 best-effort，失败置 None），非用例生成，故不引入 Celery（已满足短事务分界，文档明示该权衡）。

## 7. 宽容解析（带 warnings，不静默）

解析器**只提取不校验**：$ref 目标不存在、跨文件 $ref、operation_id 重复等**逐条记入 `warnings`** 并透出到 ParseResult/AnalyzeResult；Service 不阻塞入库但用户可感知「哪些接口的 hash 可能不可靠」。

## 8. 复杂度与验收

- O(1) diff（只存 hashes）+ 索引检索 <10ms → 整体 <10s；`operation_id` 血缘覆盖全部用例 → 回归范围 100%。
- 验收：Swagger 1.0 建 3 用例 → Swagger 2.0 改 1 接口入参 → diff 返回「变更 1、影响 1」→ 一键回归成功。
- 演示场景：非 breaking 不误圈 / 状态码 200→202 命中 / 接口改名给迁移清单 / 退化回归 summary 真实口径 / 重复 id 告警。

## 9. 边界情况

| 场景 | 处理 |
| --- | --- |
| 首次入库（无旧版本） | added=untested=全部、无 affected，不报错 |
| 新旧相同（哈希全等） | identical，无受影响 |
| operation 改名 | removed+added → orphaned_case_ids + suggested_remap 提示迁移 |
| operation_id 缺失/重复 | 缺失自动生成（路径参数归一化 `/users/{id}`→`/users/:param`）；重复进 warnings（血缘可能误圈定） |
| 回归时用例已删/改 draft | 宽容降级只执行 active，dropped 附 reason；全失效 422 |
| 哈希算法升级 | hash_version 区分新旧，diff 不一致全标 changed |

## 10. 面试锚点

| 问题 | 话术 |
|---|---|
| O(1) diff？ | 只存 version+hashes，diff=集合差+哈希比较 |
| 200→202 会漏检？ | 响应 hash 含状态码指纹（F1） |
| breaking 漏报？ | required+type/enum 签名联合判定四场景（F2） |
| 接口改名血缘断裂？ | orphaned 迁移清单 + suggested_remap 可操作建议 |
| 回归可追溯？ | last_regression_* 落库 + 结构化日志 + 真实口径 summary |
| 为什么不用 AI？ | 确定性规则零成本零幻觉；AI 留给 Phase 3 生成 |
