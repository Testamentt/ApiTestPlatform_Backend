# 执行引擎设计（execution-engine.md）· Phase 1-3 实现版

> 规则引用：`RULES.md` §2.3（统一超时）、§2.4（subprocess 规范，MVP 已放宽）、§8（Celery 任务治理）。
> **Phase 1-3（面试导向）**：`execute_cases`（用例执行）+ `generate_cases`（AI 生成）两个 Worker 任务 + `scan_stale_tasks` 超时劫持；subprocess 用 `Popen + communicate(timeout)` + 白名单；报告用简单 HTML；无 Beat。

## 1. Celery 任务清单（Phase 1-3）

| 任务名 | 输入 | 输出/副作用 | 状态流转 |
| --- | --- | --- | --- |
| `execute_cases(task_id)` | task_id（从 DB 读上下文） | 动态生成 test_xxx.py → subprocess pytest → JUnit 解析 → 写 tasks.result_summary + report_link | pending→running→success/failed |
| `generate_cases(task_id)` | task_id（从 DB 读 document） | parse → 逐 operation LLM 生成 → draft 落库 + generation_logs 审计 → result_summary | pending→running→success/failed（见 [ai-generation.md](ai-generation.md)） |
| `scan_stale_tasks()` | 无 | 扫描 running 超时的执行任务（taskkill 杀树）+ 生成任务 → 迁移 failed | 运维任务（Worker 启动触发一次，**无 Beat**） |

## 2. Celery 配置要点（值全部来自 Settings，禁止硬编码）

```python
# app/celery_app.py（显式读 get_settings() 拼 broker/backend URL——Celery 不会自动加载 FastAPI Settings）
task_acks_late = True
worker_prefetch_multiplier = 1
task_reject_on_worker_lost = True
task_soft_time_limit = settings.celery.soft_time_limit  # 300（执行任务默认）
task_time_limit = settings.celery.time_limit  # 360
broker_transport_options = {"visibility_timeout": settings.celery.visibility_timeout}  # 已接线
result_expires = settings.celery.result_expires  # 3600（来自 config）
broker_connection_retry_on_startup = True
# generate_cases_task 单独覆盖：
#   soft_time_limit=llm.task_soft_timeout_seconds(540) / time_limit=llm.task_timeout_seconds(600)
```

- **acks_late + time_limit 必须配套**：acks_late 保证 worker 崩溃不丢任务；没有 time_limit 时卡死任务永不结束（RULES.md §16.7 面试防守点）。
- **visibility_timeout（1h）> time_limit**：经 `broker_transport_options` 显式接线（3600 > 600），否则运行中的任务被重复投递。
- **软超时捕获**：`execute_cases_task`/`generate_cases_task` 均捕获 `SoftTimeLimitExceeded` → `force_fail_timeout` 落 failed（error_stage=timeout）+ best-effort 清理（RULES.md §8.2）。服务层兜底对软超时**显式放行**（R3-1：曾被 `except Exception` 截胡致 handler 不可达）。
- **⚠️ solo 池 time-limit 事实（R3-1）**：celery solo 池不派发 soft/hard timeout（`concurrency/solo.py` 直接 apply_target）——`--pool=solo` 部署下 Celery 层超时不生效，任务侧靠 `run_cmd` 自身 `communicate(timeout)` 兜底；prefork 部署下 handler 真实可达。
- **重试**：执行任务 `autoretry_for=(瞬时异常,)` + `retry_backoff=True`/`max_retries=settings.celery.max_retries`；生成任务**不设 autoretry**（LLM 瞬时重试已在 llm_client 内部收敛，Celery 层重跑会重复生成 draft）；业务/参数错误直接 failed。
- **单写者**：Windows 本地 `--pool=solo`（或 `--concurrency=1`）串行化写 SQLite。

## 3. subprocess 统一封装（run_cmd，MVP 简化版）

`app/utils/subprocess_util.py` 提供唯一函数 `run_cmd(args, timeout, *, check=True, cwd=None, on_start=None)`，**业务代码禁止各自 subprocess.run**：

- 参数必须为列表，**禁止 `shell=True`**；命令在配置白名单内（`[python, python3*, pytest]`，`*` 尾缀=前缀匹配，兼容 Linux/Docker 的 `python3.12`），参数逐项校验。
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
            -o, addopts=, -p, no:cacheprovider], timeout=task.timeout_seconds（缺省 pytest_timeout）, check=False)
6. **returncode 终态检查**：非 (0,1)（2 中断/3 内部/4 usage/5 收集失败）→ failed(error_stage=subprocess)，
   避免 pytest 自身异常产出缺测试的 junit 被误判 SUCCESS；1=有用例失败但 junit 有效，继续
7. 解析 report.xml（junit_parser 累加各 testsuite 总数）→ 组 result_summary {total,passed,failed,...}
8. report_util 写 report.html（best-effort，无有效结果也生成「执行失败，无有效结果」）→ report_link
9. 写 tasks.result_summary + report_link + status=success + finished_at —— commit
10. 失败/超时/软超时/未预期异常/入队失败 → status=failed + error_stage(parse/subprocess/timeout/internal/dispatch/command)
    + error_msg（含 stdout 尾部）—— commit；扫描不自动重试，用户可同输入重新提交触发重试（review H4）；command=命令未过白名单（review L7 分类）
```

**执行安全边界**：只运行**人工确认过的 active 用例**；test 文件由结构化字段渲染（非自由文本）；`run_cmd` 命令白名单 + `shell=False`。

## 4.1 generate_cases 执行流程（短事务分界）

> 完整设计见 [ai-generation.md](ai-generation.md) §7。要点：
> PENDING→RUNNING 置位（短事务）→ `parse_openapi`（无 Session，CPU 密集不占连接）→ 定向过滤 operation_ids（未命中记 skipped）→ 逐 operation **串行**调 LLM（宽容：单接口失败/校验失败记 `generation_logs` 继续，不拖垮整批）→ draft 落库（operation_id 服务端注入 + trust_score）+ 审计 → result_summary + SUCCESS。
> 终态兜底：解析失败 FAILED(parse)；意外异常外层兜底 FAILED(internal)；软超时 `force_fail_timeout` FAILED(timeout)——保证不卡 RUNNING（RULES §8.2/§8.3）。

## 5. 超时劫持机制（scan_stale_tasks，MVP 简化版）

**触发**：`@worker_ready.connect` 在 Worker 启动时扫描一次（**无 Beat，无每 5 分钟周期**）。

```
scan_stale_tasks():
  now = datetime.now(timezone.utc).replace(tzinfo=None)
  # 执行任务：阈值 = 任务级 timeout_seconds（缺省 pytest_timeout），与 run_cmd 同源
  running = SELECT * FROM tasks WHERE status='running'
  stale = [t for t in running if started_at and (now - started_at).total_seconds() > threshold]
  for t in stale:
    1. os.system(f"taskkill /T /F /PID {t.pid}")   # 读 DB 写入的 pid，权威清理
    2. 迁移 failed，error_stage='timeout'，error_msg=被杀时间
  # 生成任务（review H3）：阈值 = llm.task_timeout_seconds；Worker 被强杀后卡 RUNNING 会污染 run_id
  gen_running = SELECT * FROM generation_tasks WHERE status='running'
  stale_gen = [t for t in gen_running if started_at and (now - started_at).total_seconds() > gen_threshold]
  for t in stale_gen:
    迁移 failed，error_stage='timeout'，error_msg=被杀时间（生成任务无 pid，不杀树）
  # 扫描不自动重试——重试由用户同输入重新提交触发（review H4）
```

**孤儿任务兜底**：`status='running'` 但 broker 中已无对应任务的，靠 `started_at` 超时条件自然覆盖。

## 6. JUnit XML 解析（MVP：只读总数 + stdout fallback）

- 子进程 pytest 带 `--junitxml=report.xml`。
- `junit_parser.parse_junit_xml(path)`：**遍历全部 `<testsuite>` 累加** `tests`/`failures`/`errors`/`skipped`（pytest-xdist 会产出多个 testsuite），返回 `JunitSummary` dataclass。
- **损坏 XML 兜底链路**（execute_cases 内）：捕获 `AppError("JUNIT_PARSE_FAILED")` → 置 `error_stage="parse"` → 从 `run_cmd` 返回的 stdout/stderr 尾部写入 `error_msg` → 生成「执行失败，无有效结果」报告 → 终态 failed，不自动重试。
- 面试话术：「报告生成是 best-effort，即使 pytest 失败我们也尽量给用户一个可查看的 HTML。」

## 7. HTML 报告（替代 Allure，MVP）

- 任务结束后 `report_util.write_report_html` 写 `.workspace/reports/{task_id}/report.html`（自包含 HTML：总数/通过/失败/逐用例列表 + 失败信息）。
- `tasks.report_link = /static/{task_id}/report.html`（main.py 挂 `/static` 至 `workspace/reports/`，测试文件目录不暴露）。
- **必须处理无有效结果**：pytest 失败（如测试文件语法错误）导致 report.xml 缺失时，生成「执行失败，无有效结果」报告；调用 wrapped in try（best-effort，失败不影响任务状态更新）。
- 面试话术：「对接了报告，MVP 用简单 HTML，后续可换 Allure。」
- 「自愈看板」：**不做**——AI UI 项目卖点，不重复造轮子。

## 8. Windows 注意事项

- Celery worker 本地必须 `--pool=solo`（Windows 不支持 prefork 池）。
- 杀进程树用 `os.system(f"taskkill /T /F /PID {pid}")`（忽略返回值，128=已消失属预期）。
- 子进程启动加 `CREATE_NO_WINDOW`（避免 pytest 弹控制台窗口）。

## 8.1 本地 mock 目标服务（演示稳定性）

- 背景：默认 `base_url` 曾指向 httpbin.org，外网抖动会让执行演示当场失败（roadmap 待办「演示稳定性」）。
- 方案：`scripts/mock_target.py` 提供 httpbin 兼容子集——`/get`、`/post`（PUT/PATCH/DELETE 同形状回显）、
  `/status/{code}`（任意状态码）、`/delay/{n}`（上限 10s，配合调小 `tasks.timeout_seconds` 演示超时劫持）、
  `/bearer`（无凭证 401）、`/headers`、`/health`。
- 本地：`python scripts/mock_target.py`（默认 127.0.0.1:9999）+ `TESTPLATFORM_EXECUTION_BASE_URL=http://127.0.0.1:9999`。
- Compose：内置 `mock` 服务（复用后端镜像 + 脚本只读挂载），api/worker 默认 `http://mock:9999`，
  `docker compose up` 即全离线可复现；联真实外网用环境变量覆盖 base_url。

## 8.2 被测系统接入：管伊佳ERP（真实业务系统）

- 文档：Swagger 2.0（`/v2/api-docs`）→ `scripts/convert_swagger2.py` 转 OpenAPI3，产物
  [examples/jsherp-openapi3.json](examples/jsherp-openapi3.json)（320 paths / 338 operations，parse 0 warning）。
- 鉴权适配（`execution.auth_*`，settings.yaml；凭证只放 .env 的 `ERP_TEST_USERNAME/PASSWORD`）：
  登录 `POST /user/login`（**密码 MD5 后传输**）→ `data.token` → 执行层 session 级登录一次，
  以 `X-Access-Token` 头注入全部用例请求；无 token/token 失效 ERP 返回 **HTTP 500 + "loginOut"（非 401）**。

### 账号体系（业务领域关键信息）

| 账号 | 角色 | 边界 |
| --- | --- | --- |
| `admin` | **平台运维用户（超级管理员）** | 只能配置平台菜单、创建/管理租户；**不能编辑任何业务数据** |
| `jsh`（测试账号） | **租户管理员（真正的业务管理员）** | 租户内全部业务数据增删改查；凭证在 .env |

**演示口径：业务用例的生成/执行/造数据一律用租户账号 `jsh`（`admin` 登录也无法编辑业务数据）；
「鉴权异常」类用例在统一带 token 的执行层下会失败，演示挑查询类正向用例（幂等无副作用）。
更完整的领域口径见项目技能 `.claude/skills/jsherp-target-domain/SKILL.md`（本地协作知识）。**

## 9. 验收指标

- 10+ 用例并发执行；Web 响应稳定 <50ms（异步解耦）。
- 死循环用例被超时强杀，任务进入明确终态，系统不卡死。
- Worker 重启后：运行中超时任务被启动扫描迁移 failed。
