# 变更影响分析设计（impact-analysis.md）

> 规则引用：[`.claude/rules/RULES.md`](../.claude/rules/RULES.md) §5.2（索引）。本文描述接口变更影响自动圈定的核心算法：版本快照 → Diff → SQL 反向检索 → 一键回归。对应规格书亮点 ②。

## 1. 数据源：api_definitions 版本管理

每次 Swagger 变更（上传 `/parse/import` / Git Webhook 拉取新文件）→ 解析 → 提取 `operation_ids` + `operation_hashes`（每个 operation 的 method+path+parameters+request_body+responses 状态码序列化后哈希）→ 作为新版本 INSERT `api_definitions`。

- `version` UNIQUE：版本号（用户标签或 commit_sha）。
- `commit_sha`：webhook 去重，同 sha 已入库则直接复用。
- **`operation_hashes` 是 O(1) diff 的关键**：无需重新解析两份文档，直接哈希比较。

## 2. Diff 算法步骤

```
old = api_definitions[id_old];  new = api_definitions[id_new]

1. old_ops = set(old.operation_ids);  new_ops = set(new.operation_ids)
2. removed = old_ops - new_ops              # 接口被删除 → 受影响
3. common  = old_ops ∩ new_ops
4. changed = [op for op in common
              if old.operation_hashes[op] != new.operation_hashes[op]]
                                            # 公共接口发生变更（哈希不等）
5. added   = new_ops - old_ops              # 仅提示"可生成新用例"，不算受影响
6. changed_ops = removed + changed          # 受影响 operationId 集合
7. SQL 反向检索:
     SELECT * FROM test_cases
     WHERE operation_id IN (changed_ops)
       AND status = 'active'                # 只圈定可执行的 active 用例
       AND is_deleted = False
8. 写 impact_analyses（affected_case_ids 快照 + 三态集合 + affected_summary）
```

> 字段级差异展示：哈希只回答「变没变」；UI 上展开新旧两份 operation JSON 逐字段对比展示「哪变了」。

## 3. SQL 反向检索

- 检索列 `test_cases.operation_id` 命中索引 `idx_test_cases_operation_id`（[database.md](database.md) §3.1）。
- 只检索 `status='active'` 且未软删除的用例——draft 与已归档用例不参与回归圈定。
- 支持操作级过滤：可按 `changed_ops` 子集、tag/priority 二次筛选。

## 4. 影响分析结果模型

`impact_analyses` 表（见 [database.md](database.md) §3.6）：

```jsonc
{
  "analysis_id": 12,
  "old_version": "v1.0", "new_version": "v1.1",
  "added_ops": ["listUsers"],                       // 新增接口（提示可生成新用例）
  "removed_ops": ["deleteUser"],                    // 被删除接口
  "changed_ops": ["createUser"],                    // 发生变更的公共接口
  "affected_cases": [
    {"case_id": 5, "name": "创建用户-正向", "method": "POST", "path": "/users",
     "status": "active", "priority": "P0"}
  ],
  "affected_summary": {"total": 3, "by_priority": {"P0": 2, "P1": 1}, "by_status": {"active": 3}}
}
```

## 5. Git Webhook 链路

```
Git push → POST /api/v1/webhook/git（校验 X-GitHub-Event / X-Gitlab-Event，建议加签名校验）
  → 提取 head_commit
  → 拉取新 Swagger 文件（或请求体携带；防 SSRF：scheme 仅 http/https、禁内网、限大小/重定向）
  → 解析入库新版本 api_definitions（commit_sha 去重）
  → 创建 impact_analyses(status=pending) → send_task(impact_analyze) → 202 {analysis_id}
  → Vue 前端轮询 GET /api/v1/impact/analyses/{id} 展示受影响用例清单
```

## 6. 一键回归

```
POST /api/v1/impact/{analysis_id}/regression
  → 用 affected_case_ids 快照创建 tasks(trigger=regression, task_type=execute_cases)
  → 202 {task_id}
  → 执行引擎异步运行（流程见 execution-engine.md §4）
```

## 7. 复杂度与验收指标

- 复杂度：集合差 O(n) + 哈希比较 O(|common|) + 索引检索 <10ms → 整体 **<10s**。
- 覆盖率：`operation_id` 血缘覆盖全部已建用例 → 回归范围 **100%**。
- 替代人工经验判断（1~2h）→ 系统自动圈定（<10s）。
- 依赖条件：用例创建时正确填写 `operation_id`（AI 生成自动带；手工用例引导填写）。

## 8. 边界情况

| 场景 | 处理 |
| --- | --- |
| 首次入库（无旧版本） | 不产生 diff；仅建版本快照 |
| `old_version="auto"` | 取最近一个非空版本 |
| 新旧版本相同（哈希全等） | changed_ops 为空，返回「无受影响用例」 |
| operation 改名 | 视为 removed + added（提示人工确认关联） |
| 无 `operation_id` 的手工用例 | 不参与血缘检索（人工补充后可圈定） |
