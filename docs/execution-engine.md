# 执行引擎设计（execution-engine.md）· Phase 1 简化版

> 规则引用：[`.claude/rules/RULES.md`](../.claude/rules/RULES.md) §2.3（统一超时）、§2.4（subprocess 规范，MVP 已放宽）、§8（Celery 任务治理）。
> **Phase 1 简化（面试导向）**：仅 `execute_cases` + `scan_stale_tasks` 两个 Celery 任务；subprocess 用 `subprocess.run(timeout)` + 白名单；报告用简单 HTML；无 Beat。

## 1. Celery 任务清单（Phase 1）

| 任务名 | 输入 | 输出/副作用 | 状态流转 |
| --- | --- | --- | --- |
| `execute_cases(task_id)` | task_id（从 DB 读上下文） | 动态生成 test_xxx.py → subprocess pytest → JUnit 解析 → 写 tasks.result_summary + report_link | pending→running→success/failed |
| `scan_stale_tasks()` | 无 | 扫描 running 超时任务 → taskkill → 迁移 failed | 运维任务（Worker 启动触发一次，**无 Beat**） |

> `generate_cases` / `impact_analyze` 属 Phase 2/3，本期不建。

## 2. Celery 配置要点（值全部来自 Settings，禁止硬编码）

```python
# app/celery_app.py（显式读 get_settings() 拼 broker/backend URL——Celery 不会自动加载 FastAPI Settings）
task_acks_late = True
worker_prefetch_multiplier = 1
task_reject_on_worker_lost = True
task_soft_time_limit = settings.celery.soft_time_limit      # 300
task_time_limit = settings.celery.time_limit                # 360
broker_connection_retry_on_startup = True
result_expires = 3600                                          # result backend 仅短期状态
```

- **acks_late + time_limit 必须配套**：acks_late 保证 worker 崩溃不丢任务；没有 time_limit 时卡死任务永不结束（RULES.md §16.7 面试防守点）。
- **visibility_timeout（默认 1h）> time_limit（360s）**：否则运行中的任务被重复投递。
- **重试**：`autoretry_for=(瞬时异常,)` + `retry_backoff=True`/`max_retries=3`；只对瞬时异常重试，业务/参数错误直接 failed。
- **单写者**：Windows 本地 `--pool=solo`（或 `--concurrency=1`）串行化写 SQLite。

## 3. subprocess 统一封装（run_cmd，MVP 简化版）

`app/utils/subprocess_util.py` 提供唯一函数 `run_cmd(args, timeout, *, check=True, cwd=None, on_start=None)`，**业务代码禁止各自 subprocess.run**：

- 参数必须为列表，**禁止 `shell=True`**；命令在配置白名单内（`python`/`pytest`），参数逐项校验。
- 用 `Popen + communicate(timeout=)`（`capture_output=True` 语义、`creationflags=CREATE_NO_WINDOW`）；`on_start(pid)` 在进程启动后立即回调——执行引擎用它写 `tasks.pid` + running（超时劫持的权威依据）。
- 捕获 `TimeoutExpired` → `kill_process_tree(proc.pid)`（**best-effort**：父进程已死，`proc.pid` 杀不到孙进程）→ 抛 `AppError("SUBPROCESS_TIMEOUT")`。
- 返回前校验 `returncode`（`check=True` 时非 0 抛 `AppError("SUBPROCESS_FAILED")`，detail 带 stdout 尾部）；执行引擎传 `check=False` 自行解读。

> **关键认知（面试防守）**：`subprocess.run(timeout)` 在 Windows 只杀父进程，孙进程可能残留。**权威兜底是 `scan_stale_tasks`**——它从 DB 读 `mark_running` 阶段写入的 `tasks.pid`（pytest 进程），`taskkill /T /F` 杀整棵树。run_cmd 内的 kill 只是尽力而为，失败不影响任务终态。

## 4. execute_cases 执行流程（短事务分界）

```
1. 读 task → active 用例列表（case_ids 快照；draft 不在此列）——短事务，读完 commit
2. 建按 task_id 隔离的 workspace: .workspace/tasks/{task_id}/（test 文件 + report.xml 均在此，防多任务互相覆盖）
3. 逐用例 case_generator 生成 test_{case_id}.py（结构化字段 repr 插值，无 Jinja2；只断言 expected_status）
4. `run_cmd` 的 `on_start` 回调写 tasks.pid + status=running + started_at —— commit
5. run_cmd([python, -m, pytest, 全部 test_*.py, --junitxml=report.xml,
            -o, addopts=, -p, no:cacheprovider], timeout=settings.execution.pytest_timeout, check=False)
6. 解析 report.xml（junit_parser 累加各 testsuite 总数）→ 组 result_summary {total,passed,failed,...}
7. report_util 写 report.html（best-effort，无有效结果也生成「执行失败，无有效结果」）→ report_link
8. 写 tasks.result_summary + report_link + status=success + finished_at —— commit
9. 失败/超时 → status=failed + error_stage(parse/subprocess/timeout) + error_msg（含 stdout 尾部）—— commit，禁自动重试
```

**执行安全边界**：只运行**人工确认过的 active 用例**；test 文件由结构化字段渲染（非自由文本）；`run_cmd` 命令白名单 + `shell=False`。

## 5. 超时劫持机制（scan_stale_tasks，MVP 简化版）

**触发**：`@worker_ready.connect` 在 Worker 启动时扫描一次（**无 Beat，无每 5 分钟周期**）。

```
scan_stale_tasks():
  now = datetime.now(timezone.utc).replace(tzinfo=None)
  threshold = settings.execution.pytest_timeout   # 与 run_cmd 同源；timeout 不落库，run_id 指纹已含
  running = SELECT * FROM tasks WHERE status='running'
  stale = [t for t in running if started_at and (now - started_at).total_seconds() > threshold]
  for t in stale:
    1. os.system(f"taskkill /T /F /PID {t.pid}")   # 读 DB 写入的 pid，权威清理
    2. 迁移 failed，error_stage='timeout'，error_msg=被杀时间
       （禁止置回 pending，禁止自动重试——重试仅人工触发）
```

**孤儿任务兜底**：`status='running'` 但 broker 中已无对应任务的，靠 `started_at` 超时条件自然覆盖。

## 6. JUnit XML 解析（MVP：只读总数 + stdout fallback）

- 子进程 pytest 带 `--junitxml=report.xml`。
- `junit_parser.parse_junit_xml(path)`：**遍历全部 `<testsuite>` 累加** `tests`/`failures`/`errors`/`skipped`（pytest-xdist 会产出多个 testsuite），返回 `JunitSummary` dataclass。
- **损坏 XML 兜底链路**（execute_cases 内）：捕获 `AppError("JUNIT_PARSE_FAILED")` → 置 `error_stage="parse"` → 从 `run_cmd` 返回的 stdout/stderr 尾部写入 `error_msg` → 生成「执行失败，无有效结果」报告 → 终态 failed，不自动重试。
- 面试话术：「报告生成是 best-effort，即使 pytest 失败我们也尽量给用户一个可查看的 HTML。」

## 7. HTML 报告（替代 Allure，MVP）

- 任务结束后 `report_util.write_report_html` 写 `.workspace/reports/{task_id}/report.html`（自包含 HTML：总数/通过/失败/逐用例列表 + 失败信息）。
- `tasks.report_link = /static/reports/{task_id}/report.html`（main.py 挂 `/static`）。
- **必须处理无有效结果**：pytest 失败（如测试文件语法错误）导致 report.xml 缺失时，生成「执行失败，无有效结果」报告；调用 wrapped in try（best-effort，失败不影响任务状态更新）。
- 面试话术：「对接了报告，MVP 用简单 HTML，后续可换 Allure。」
- 「自愈看板」：**不做**——AI UI 项目卖点，不重复造轮子。

## 8. Windows 注意事项

- Celery worker 本地必须 `--pool=solo`（Windows 不支持 prefork 池）。
- 杀进程树用 `os.system(f"taskkill /T /F /PID {pid}")`（忽略返回值，128=已消失属预期）。
- 子进程启动加 `CREATE_NO_WINDOW`（避免 pytest 弹控制台窗口）。

## 9. 验收指标

- 10+ 用例并发执行；Web 响应稳定 <50ms（异步解耦）。
- 死循环用例被超时强杀，任务进入明确终态，系统不卡死。
- Worker 重启后：运行中超时任务被启动扫描迁移 failed。
