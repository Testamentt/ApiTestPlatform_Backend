# 执行引擎设计（execution-engine.md）

> 规则引用：[`.claude/rules/RULES.md`](../.claude/rules/RULES.md) §2.3（统一超时）、§2.4（subprocess 规范）、§8（Celery 任务治理）。本文描述 Celery 任务、subprocess 执行流程与超时劫持机制。

## 1. Celery 任务清单

| 任务名 | 输入 | 输出/副作用 | 状态流转 |
| --- | --- | --- | --- |
| `execute_cases(task_id)` | task_id（从 DB 读上下文） | 动态生成 test_xxx.py → subprocess pytest → JUnit 解析 → 写 case_results/tasks | pending→running→success/failed；超时→killed→failed（仅人工重试） |
| `generate_cases(task_id)` | task_id（含 parse 结果 + operation 选择） | LLM 生成 → Pydantic 严格校验 → 批量落库 draft → 写 generation_log | pending→running→success/failed |
| `impact_analyze(analysis_id)` | analysis_id | diff → 受影响用例 → 写 impact_analyses | pending→completed/failed |
| `scan_stale_tasks()` | 无 | 扫描 running 超时任务 → taskkill → 状态机迁移 failed | 运维任务，无业务状态 |

## 2. Celery 配置要点（对齐 RULES.md §8）

```python
# celery_app.py 配置（示意，实际值来自 config，禁止硬编码）
task_acks_late = True
worker_prefetch_multiplier = 1
task_reject_on_worker_lost = True
task_soft_time_limit = settings.execution.soft_time_limit      # 300
task_time_limit = settings.execution.time_limit                # 360
broker_connection_retry_on_startup = True
result_expires = 3600                                          # result backend 仅短期结果
```

- **acks_late + time_limit 必须配套**：acks_late 保证 worker 崩溃不丢任务；没有 time_limit 时卡死任务永不结束，二者缺一不可（RULES.md §16.7 面试防守点）。
- **visibility_timeout > time_limit**：Redis broker 默认可见性 1h 必须大于任务 time_limit，否则运行中任务被重复投递。
- **重试**：`autoretry_for=(瞬时异常类)` + `retry_backoff=True`/`retry_backoff_max=300`/`retry_jitter=True`/`max_retries=3`。**只对瞬时异常重试**（网络超时、LLM 5xx/429、503）；业务/参数/校验错误禁止重试，直接 failed 并保存失败原因。
- **单写者**：MVP Worker 固定 `--pool=solo`（Windows）或 `--concurrency=1`，串行化写 SQLite（RULES.md §3.3），多 Worker 并发直写 SQLite 被禁止。

## 3. subprocess 统一封装（run_cmd）

`app/utils/subprocess_util.py` 提供唯一函数 `run_cmd(args, timeout)`，**业务代码禁止各自 subprocess.run**（RULES.md §2.4）：

- 参数必须为列表，**禁止 `shell=True`**；命令在配置白名单内（如 `python`, `pytest`, `allure`），参数逐项校验（类型/长度/字符集）。
- 以独立进程组启动：Windows `CREATE_NEW_PROCESS_GROUP` / POSIX `start_new_session=True`。
- 捕获 `TimeoutExpired` 后**结束整棵进程树**：Windows `taskkill /T /F /PID <pid>` / POSIX `os.killpg`，禁止只杀直接子进程。
- 输出捕获限容（单次 ≤10MB 截断），防外部工具大量输出撑爆内存。
- 返回前校验 `returncode`，非 0 抛业务异常并落 `failed`，stdout/stderr 尾部写入任务日志。
- 外部工具路径/超时/环境变量统一进 config。

> 关键点：用 `Popen + 记录 pid` 而非 `subprocess.run(timeout=)`——`run` 的 timeout 在 Windows 只杀父进程，pytest 子进程树可能残留；`pid` 落库后，超时劫持任务**跨进程也能精确 kill**。

## 4. execute_cases 执行流程

```
1. 读 task → env + active 用例列表（case_ids 快照；draft 不在此列）
2. workspace.create(task_id): .workspace/tasks/{task_id}/ + conftest.py（base_url/headers/fixture）
3. 逐用例 case_generator 生成 test_{case_id}.py
   （由 request_schema + assertions 结构化字段渲染，非自由文本；pytest 函数内做请求 + 断言）
4. proc = run_cmd([python, -m, pytest, test_*.py, --junitxml=junit.xml,
                   --alluredir=allure-results, -o, addopts=, -p, no:cacheprovider],
                  timeout=settings.execution.pytest_timeout)   # 300
   → 先写 tasks.pid（proc.pid）+ status=running + started_at（短事务提交）
5. run_cmd 内部轮询 poll()；超时 → killpg/taskkill 整棵树 → 抛 TimeoutError
6. 正常结束 → junit_parser 解析 junit.xml → 映射回 case_id → 写 case_results
   （先删旧结果，UNIQUE(task_id, case_id) 保证幂等）
7. 汇总 result_summary → allure generate → tasks.allure_link → status=success
8. 失败/超时 → status=failed + error_stage(subprocess/timeout) + error_msg
   （禁止自动重试，重试仅人工触发，RULES.md §8.5）
```

**执行安全边界**（RULES.md §10.4 协调）：MVP 禁止服务端自动执行 AI 生成的**原始代码**；执行引擎只运行**人工确认过的 active 用例**，其 pytest 文件由已通过 Pydantic 校验 + 人工审核的结构化字段（request/assertions）渲染，并经 `run_cmd` 隔离（独立进程组 + 超时 + 命令白名单 + `shell=False`）。

## 5. 超时劫持机制（scan_stale_tasks）

**触发**：`worker_ready` signal 触发一次 + Celery Beat 每 60s 周期执行（本地 Windows 用 `--pool=solo --beat`）。

```
scan_stale_tasks():
  stale = SELECT id, pid, timeout_seconds FROM tasks
          WHERE status='running' AND started_at < now() - timeout_seconds
  for task in stale:
    1. taskkill /T /F /PID task.pid    (POSIX: os.killpg)    # 强杀进程树
    2. 未完成的 case_results 置 killed
    3. 状态机迁移 failed，error_stage='timeout'，error_msg 记录被杀时间
       （禁止直接置回 pending，禁止自动重试——重试仅人工触发，RULES.md §8.5）
```

**孤儿任务兜底**：`status='running'` 但 broker 中已无对应 celery_task 的，靠 `started_at` 超时条件自然覆盖——扫描条件本身即为兜底，无需额外探测。

**手动取消**：`POST /api/v1/tasks/{id}/cancel` 复用同一 kill 逻辑，将任务迁移为 `cancelled`。

## 6. JUnit XML 解析

- 子进程 pytest 带 `--junitxml=junit.xml`。
- 解析器将 `<testsuite>/<testcase>` 映射回 `case_id`：testcase 的 `name` 约定为 `test_{case_id}`，`<failure>/<error>` 提取失败信息。
- 字段映射：
  | JUnit 元素 | case_results 字段 |
  | --- | --- |
  | testcase name → case_id | case_id |
  | testsuite tests/failures/errors/skipped | result_summary |
  | testcase time | duration_ms |
  | failure/error message + body | failure_msg |
  | 原始 XML 片段 | junit_xml（溯源） |
- 结果状态：无 failure/error → `pass`；有 failure → `fail`；有 error → `error`；超时未完成 → `killed`；pytest skip → `skipped`。

## 7. Allure 报告

- 子进程 pytest 带 `--alluredir=.workspace/tasks/{task_id}/allure-results`。
- 任务结束：`allure generate <alluredir> -o <allure-report> --clean`（命令走 run_cmd 白名单）。
- 报告挂 FastAPI 静态目录：`/static/allure/{task_id}/index.html`，写入 `tasks.allure_link`；`case_results.allure_uuid` 支持用例级跳转。
- 「自愈看板」：Vue 前端聚合 `case_results` 统计 pass 率/耗时/失败分布，属 roadmap Phase 4 增强。

## 8. Windows 注意事项

- Celery worker 本地必须 `--pool=solo`（Windows 不支持 prefork 池）。
- 杀进程树用 `taskkill /T /F /PID`；POSIX 用 `os.killpg`。
- 子进程启动需 `CREATE_NO_WINDOW`（避免 pytest 弹出控制台窗口）。

## 9. 验收指标

- 10+ 用例并发执行；Web 服务响应稳定 <50ms（异步解耦）。
- 死循环用例被 300s 超时强杀，任务进入明确终态，系统不卡死。
- Worker 重启后：运行中超时任务被 `scan_stale_tasks` 迁移 failed，已成功结果不丢。
