<!-- 本项目编码格式：UTF-8（无 BOM）。 -->
# 执行引擎设计（execution-engine.md）

> 定位：用例怎么被执行——执行器（local / Jenkins）、Celery 任务、subprocess 封装、超时与僵尸回收、报告产物。
> 事实源：`app/tasks/`、`app/services/execution_service.py`、`app/services/executors/`、`app/utils/jenkins_client.py`、`app/services/dispatcher.py`、`app/utils/subprocess_util.py`、`app/utils/junit_parser.py`、`app/utils/report_util.py` ｜ 最后核对：2026-11-12

## 1. Celery 任务清单（执行闭环-3）

| 任务名 | 输入 | 输出/副作用 | 状态流转 |
| :--- | :--- | :--- | :--- |
| `execute_cases(task_id)` | task_id（从 DB 读上下文） | 按 `execution.executor` 选执行器：local 渲染 test_xxx.py 并起 pytest，jenkins 投递构建并回读制品 → JUnit 解析 → 写 tasks.result_summary + report_link | pending→running→success/failed |
| `generate_cases(task_id)` | task_id（从 DB 读 document） | parse → 逐 operation LLM 生成 → draft 落库 + generation_logs 审计 → result_summary | pending→running→success/failed（见 [ai-generation.md](ai-generation.md)） |
| `scan_stale_tasks()` | 无 | 扫描 running 超时的执行任务（taskkill 杀树）+ 生成任务 → 迁移 failed；PENDING 孤儿按队列位置回收 | 运维任务（Beat 周期 `celery.stale_scan_interval` 秒 + Worker 启动补扫） |

## 2. Celery 配置要点（值全部来自 Settings，禁止硬编码）

```python
# app/celery_app.py（显式读 get_settings() 拼 broker/backend URL——Celery 不会自动加载 FastAPI Settings）
task_acks_late = True
worker_prefetch_multiplier = 1
task_reject_on_worker_lost = True
task_soft_time_limit = settings.celery.soft_time_limit  # 全局默认 300；执行任务派发时按 timeout_seconds 派生（缺省 → 330）
task_time_limit = settings.celery.time_limit  # 全局默认 360（缺省派生结果同为 360）
broker_transport_options = {"visibility_timeout": settings.celery.visibility_timeout}  # 已接线
result_expires = settings.celery.result_expires  # 3600（来自 config）
broker_connection_retry_on_startup = True
# generate_cases_task 单独覆盖：
#   soft_time_limit=llm.task_soft_timeout_seconds(540) / time_limit=llm.task_timeout_seconds(600)
```

- **acks_late 与 time_limit 必须配套**：acks_late 保证 worker 崩溃不丢任务；没有 time_limit 时卡死任务永不结束。
- **visibility_timeout（1h）大于 time_limit**：经 `broker_transport_options` 显式接线（3600 > 600），否则运行中的任务会被重复投递。
- **软超时捕获**：`execute_cases_task`/`generate_cases_task` 均捕获 `SoftTimeLimitExceeded` → `force_fail_timeout` 落 failed（error_stage=timeout）+ best-effort 清理。服务层兜底对软超时显式放行，否则 `except Exception` 会把 handler 截胡成不可达。
- **solo 池不派发 soft/hard timeout**：celery solo 池不派发时间限制（`concurrency/solo.py` 直接 apply_target），因此 `--pool=solo` 部署下 Celery 层超时不生效，执行任务侧靠 `run_cmd` 自身的 `communicate(timeout)` 兜底；prefork 部署下 handler 真实可达。生成链路没有 subprocess，不存在这层兜底，由任务内 deadline 自守：逐 operation 检查 `llm.task_timeout_seconds`，超时落 `failed(timeout)` 并留进度快照。
- **重试**：执行任务 `autoretry_for=(瞬时异常,)` + `retry_backoff=True`/`max_retries=settings.celery.max_retries`；生成任务不设 autoretry（LLM 瞬时重试已在 llm_client 内部收敛，Celery 层重跑会重复生成 draft）；业务/参数错误直接 failed。
- **Beat 与 Worker 读同一份配置**：`beat_schedule` 在 `configure_celery` 里由 Settings 生成（间隔来自 `celery.stale_scan_interval`），Beat 进程与 Worker 用同一个 `celery_app` 实例，口径不会分叉。
- **单写者**：Windows 本地用 `--pool=solo`（或 `--concurrency=1`）串行化写 SQLite。

## 3. subprocess 统一封装（run_cmd）

`app/utils/subprocess_util.py` 提供唯一函数 `run_cmd(args, timeout, *, check=True, cwd=None, on_start=None)`，业务代码禁止各自 subprocess.run：

- **参数必须为列表，复核白名单**：禁止 `shell=True`；命令在配置白名单内（`[python, python3*, pytest]`，`*` 尾缀=前缀匹配，兼容 Linux/Docker 的 `python3.12`），参数逐项校验。
- 用 `Popen + communicate(timeout=)`（`capture_output=True` 语义、`creationflags=CREATE_NO_WINDOW`、显式 `encoding="utf-8" + errors="replace"` + 子进程 `PYTHONIOENCODING=utf-8`，POSIX 另加 `start_new_session=True`）；`on_start(pid)` 在进程启动后立即回调，执行引擎用它写 `tasks.pid` + running（超时劫持的权威依据）；`on_start` 抛错时先杀子进程再抛出，避免孤儿。
- 捕获 `TimeoutExpired` → `kill_process_tree(proc.pid)`（best-effort）→ `proc.kill()` → 抛 `AppError("SUBPROCESS_TIMEOUT")`。
- 返回前校验 `returncode`（`check=True` 时非 0 抛 `AppError("SUBPROCESS_FAILED")`，detail 带 stdout 尾部）；执行引擎传 `check=False` 自行解读。

> 关键约束：`subprocess.run(timeout)` 在 Windows 只杀父进程，孙进程可能残留。权威兜底是 `scan_stale_tasks`（见 §5）：它从 DB 读 `mark_running` 阶段写入的 `tasks.pid`，按平台杀整棵树。run_cmd 内的 kill 只是尽力而为，失败不影响任务终态。

## 4. execute_cases 执行流程（短事务分界）

```text
1. 读 task → active 用例列表（case_ids 快照；draft 不在此列）——短事务，读完 commit
2. get_executor() 按 execution.executor 取执行器（local|jenkins）；编排层只依赖协议，不关心报告来自本地文件还是 Jenkins 制品
3. local：清空并重建按 task_id 隔离的 workspace .workspace/tasks/{task_id}/（auth_enabled 时写 conftest），
   逐用例 case_generator 生成 test_{case_id}.py（结构化字段 repr 插值，无 Jinja2；契约层基线
   `assert r.status_code == expected_status` 恒渲染 + assertions 逐条渲染：`status_code` 走 `r.status_code`、
   字段层走 `_dig(payload, '点路径')` 取值，`AssertionItem` path/op 白名单已在校验层拦截注入面，非法历史条目渲染为注释跳过）
4. 执行器的 on_start 回写 tasks.pid + status=running + started_at —— commit
   （条件更新 pending→running；Jenkins 无本地进程可杀，pid 传 None）
5. local：run_cmd([python, -m, pytest, 全部 test_*.py, --junitxml=report.xml,
                  -o, addopts=, -p, no:cacheprovider], timeout=task.timeout_seconds（缺省 pytest_timeout）, check=False)
   jenkins：buildWithParameters(TASK_ID, CASE_IDS, BASE_URL) → 队列轮询 → 构建轮询 → 下载制品到 jenkins-junit.xml
6. local returncode 终态检查：非 (0,1)（2 中断/3 内部/4 usage/5 收集失败）→ 抛 PYTEST_EXIT_CODE → failed(error_stage=subprocess)，
   避免 pytest 自身异常产出缺测试的 junit 被误判 SUCCESS；1=有用例失败但 junit 有效，继续
   jenkins 不看 pytest 退出码：构建结果为 FAILURE/UNSTABLE/ABORTED 时都先尝试回读制品，只有制品缺失或非法才判失败
7. 解析 JUnit（junit_parser 遍历全部 <testsuite> 累加）→ (JunitSummary, entries)
8. 组 result_summary {total, passed, failed, skipped, duration_ms, results, executor, build_url}
9. 成功：条件更新 running→success → 写 report.html + report_link（best-effort）→ 同一事务提交
10. 失败/超时/软超时/未预期异常/入队失败 → status=failed + error_stage
    （command/subprocess/timeout/parse/jenkins/internal/dispatch/pending_timeout；
     用户取消由 cancel 端点写 failed(cancelled)，执行层在启动瞬间发现已取消时只放弃回写，不覆盖终态；
     Jenkins 新增 jenkins 阶段，其余见 [database.md](database.md) §3.2）
    + error_msg（local 含 stdout 尾部，Jenkins 含构建号与错误 detail）—— commit；
    扫描不自动重试，用户可同输入重新提交或调 `POST /tasks/{id}/retry` 触发重试
```

> 终态条件更新：第 9/10 步与软超时兜底都改为 `UPDATE ... WHERE status IN ('pending','running')`（成功路径为 `WHERE status='running'`）——影响 0 行即放弃回写并记 info。人工取消可能发生在执行途中的任意时刻，无条件覆盖会把 `cancelled` 改写成 `subprocess`/`timeout`/`success`，取消语义与用户预期一起丢失。`POST /tasks/{id}/cancel` 对 `running` 任务会先杀 pytest 进程树，再以"读到的那个状态"为前置条件落取消终态（`pending` 额外 revoke 排队消息，`running` 的消息已被消费故不 revoke）。

执行安全边界：只运行人工确认过的 active 用例；test 文件由结构化字段渲染（非自由文本）；`run_cmd` 命令白名单 + `shell=False`。

### 4.1 generate_cases 执行流程（短事务分界）

> 完整设计见 [ai-generation.md](ai-generation.md) §7。要点：
> PENDING→RUNNING 置位（短事务）→ `parse_openapi`（无 Session，CPU 密集不占连接）→ 定向过滤 operation_ids（未命中记 skipped；显式 ids 全未命中在入口即 422）→ 逐 operation **串行**调 LLM（宽容：单接口失败/校验失败记 `generation_logs` 继续，不拖垮整批）→ draft 落库（operation_id 服务端注入 + trust_score）+ 审计 → result_summary + SUCCESS。
> 终态兜底：解析失败 FAILED(parse)；意外异常外层兜底 FAILED(internal)；任务内 deadline 超时 → FAILED(timeout) 并留进度快照（Celery 软超时仅在 prefork 池可达，见 §2），保证不卡 RUNNING。

## 5. 超时劫持机制（scan_stale_tasks）

**触发（两条入口，同一份逻辑）**：

- **Beat 周期调度**：`celery_app.beat_schedule` 注册 `scan-stale-tasks`，间隔 `celery.stale_scan_interval`（默认 60 s）；带 `expires`，上一轮没跑完时下一次 tick 直接丢弃而不堆叠。`stale_scan_interval=0` 即关闭周期扫描。
- **Worker 启动补扫**：`@worker_ready.connect` 触发一次，覆盖 Beat 还没跑第一个 tick 的空窗。

> why 必须加 Beat：此前只有启动扫描一次，Worker 长期不重启时被强杀的 running 任务会一直挂着、排队孤儿会永久占用 run_id，故障要等下一次人工重启才自愈——「兜底」被绑死在重启频率上。Beat 需要单独进程：`celery -A app.celery_app:celery_app beat`（compose 里是 `beat` 服务，本地脚本见 `scripts/start_beat.bat`）。

```text
scan_stale_tasks():
  now = datetime.now(timezone.utc).replace(tzinfo=None)
  # 执行任务：阈值 = 任务级 timeout_seconds（缺省 pytest_timeout），与 run_cmd 同源
  running = SELECT * FROM tasks WHERE status='running'
  stale = [t for t in running if started_at and (now - started_at).total_seconds() > threshold]
  for t in stale:
    1. kill_process_tree(t.pid)   # 读 DB 写入的 pid，权威清理（Windows taskkill /T；POSIX killpg）
    2. 迁移 failed，error_stage='timeout'，error_msg=被杀时间
  # PENDING 孤儿：消息入队却无人领取（broker 丢消息/Worker 长期缺席/队列被清）
  # 计时用 updated_at —— 重试会把老行重置为 pending，用 created_at 会立刻误杀刚重试的任务
  # 阈值按队列位置放宽：allowance = celery.pending_timeout + Σ(队首到本任务之前的 timeout_seconds)
  pending = [t for t in SELECT * FROM tasks WHERE status='pending' ORDER BY created_at, id
             if now - t.updated_at > allowance(t)]
  for t in pending:
    1. revoke_task(t.celery_task_id)          # broker 里的消息一并撤销，避免 Worker 领取后空跑
    2. 迁移 failed，error_stage='pending_timeout'（error_msg 写实际 allowance，可人工重试）
  # 生成任务：阈值 = llm.task_timeout_seconds；Worker 被强杀后卡 RUNNING 会污染 run_id
  gen_running = SELECT * FROM generation_tasks WHERE status='running'
  stale_gen = [t for t in gen_running if started_at and (now - started_at).total_seconds() > gen_threshold]
  for t in stale_gen:
    迁移 failed，error_stage='timeout'，error_msg=被杀时间（生成任务无 pid，不杀树）
  # 扫描不自动重试——重试由用户同输入重新提交触发
```

**PENDING 为什么不按全局固定值判**：`pending_timeout` 的语义是「**没人领取**」，不是「排队太久」。solo 单 Worker 串行执行时，排在第三个的 300 s 任务正常也要等约 900 s——用全局 600 s 会把它当成孤儿误杀成 `failed(pending_timeout)`，而 failed 只能人工重试，等于把正常排队变成故障。故宽限按队列位置叠加前序任务各自的 `timeout_seconds`（前面没有任务时退化为全局值，孤儿判定语义不变）。实现见 `app/tasks/supervisor.py:pending_allowance_seconds`。

**孤儿任务兜底**：`status='running'` 但 broker 中已无对应任务的，靠 `started_at` 超时条件自然覆盖。

## 6. JUnit XML 解析（汇总 + 逐用例）

- local 执行由子进程 pytest 带 `--junitxml=report.xml`；Jenkins 执行下载构建制品，两者最终都交给同一个解析器。
- `junit_parser.parse_junit_xml(path)`：遍历全部 `<testsuite>` 累加 `tests`/`failures`/`errors`/`skipped`（pytest-xdist 会产出多个 testsuite），返回 `(JunitSummary, 逐用例结果列表)` 二元组（列表供 `GET /tasks/{id}/results` 展示）。
- **汇总口径**：`failed = failures + errors`，`passed = tests - failures - errors - skipped`。pytest 的 junitxml 中 `tests` 含被 skip 的用例，不扣减会把跳过算成通过、通过率虚高；逐用例层面把 `<skipped/>` 单独标为 `skipped`，两层口径一致。
- **损坏 XML 兜底链路**：捕获 `AppError("JUNIT_PARSE_FAILED")` → 置 `error_stage="parse"` → 用 local 的 `run_cmd` stdout 尾部（空则回退异常 detail）写入 `error_msg` → 终态 failed，不自动重试。失败任务不写 HTML 报告，`report_link` 为空，报告端点返回 404 `REPORT_NOT_FOUND`（见 §7）。

## 7. 平台自包含 HTML 报告（不依赖 Allure）

- 任务成功后 `report_util.write_report_html` 写 `.workspace/reports/{task_id}/report.html`：单文件 HTML，含总数/通过/失败与 case_id、name、status、failure_msg 四列逐用例结果（name 当前由接口层空串兜底，解析器不回填）。所有插值经 `html.escape`，不引入外部 CSS/JS/CDN，报告在内网或离线环境都能直接打开。
- 为什么平台侧不接 Allure：任务报告要单文件、带鉴权、生成过程不依赖 Java 工具链与 Jenkins 插件；Allure 的定位是流水线侧的可交互报告（见 §11），两者是分工而不是替代关系。JUnit XML 是与外部交换的事实格式，HTML 只是它的一种渲染。
- 报告端点：`tasks.report_link = /api/v1/tasks/{task_id}/report`，由带鉴权的路由读文件系统返回原文。更早的版本走无鉴权 `/static/{task_id}/report.html`，task_id 自增即可枚举用例名与失败信息；现在报告目录与测试文件目录分离，`test_*.py` 始终不对外。
- 写入时机与失败语义：只有成功终态写报告。`_finish_success` 先条件更新 `running→success`，再写 HTML（包在 try 中，写失败只记日志，不影响任务状态），最后同一事务提交 `report_link`。失败/超时/取消任务不生成报告，`report_link` 为空，`GET /report` 返回 404 `REPORT_NOT_FOUND`；`write_report_html` 里的「执行失败，无有效结果」是空结果的防御分支，当前只有成功路径调用。
- **重试必须连报告一起清**：报告端点读文件系统而不是读 DB（`TaskService.get_report_html` → `report_util.report_path`）。重试重置时既要把 `result_summary`/`report_link`/`started_at` 置空（否则 `/results` 与任务详情会回放上一轮），也必须删掉磁盘上的 `report.html`（`report_util.remove_report`，best-effort），否则 `/report` 仍返回旧内容。
- 自愈看板：列入不做。与当前三个目标（执行闭环、影响分析、AI 生成）无关，失败定位已由任务状态机、`error_stage` 与任务报告承担，再加一层看板只扩大前端维护面；若将来出现「跨任务聚合失败模式并驱动处置」的真实需求再评估，届时从 `result_summary` 的逐用例结果派生，而不是另起一套数据源。

## 8. Windows 注意事项

- Celery worker 本地必须 `--pool=solo`（Windows 不支持 prefork 池）；同时该池会静默关闭 Celery 的 soft/hard time_limit（见 §2）。
- 杀进程树按平台分派：Windows `taskkill /T /F /PID`（rc≠0 记 warning）；POSIX `killpg`（仅当 `pid` 是会话组长，否则退化为单进程击杀，避免误杀 Worker 自身进程组）。失败不再静默：记 warning 并由 `scan_stale_tasks` 兜底。
- 子进程启动加 `CREATE_NO_WINDOW`（避免 pytest 弹控制台窗口）；输出按 UTF-8 解码（`errors="replace"`），子进程注入 `PYTHONIOENCODING=utf-8`。

### 8.1 本地 mock 目标服务（演示稳定性）

- 背景：默认 `base_url` 曾指向 httpbin.org，外网抖动会让演示当场失败（roadmap 的「演示稳定性」待办）。
- 方案：`scripts/mock_target.py` 提供 httpbin 兼容子集——`/get`、`/post`（PUT/PATCH/DELETE 同形状回显）、
  `/status/{code}`（任意状态码）、`/delay/{n}`（上限 10s，配合调小 `tasks.timeout_seconds` 演示超时劫持）、
  `/bearer`（无凭证 401）、`/headers`、`/health`。
- 本地：`python scripts/mock_target.py`（默认 127.0.0.1:9999）+ `TESTPLATFORM_EXECUTION_BASE_URL=http://127.0.0.1:9999`。
- Compose：内置 `mock` 服务（复用后端镜像 + 脚本只读挂载），api/worker 默认 `http://mock:9999`，
  `docker compose up` 即全离线可复现；联真实外网用环境变量覆盖 base_url。

### 8.2 被测系统接入：管伊佳ERP（真实业务系统）

- 文档：Swagger 2.0（`/v2/api-docs`）→ `scripts/convert_swagger2.py` 转 OpenAPI3，产物
  [examples/jsherp-openapi3.json](examples/jsherp-openapi3.json)（320 paths / 338 operations，parse 0 warning）。
- 鉴权适配（`execution.auth_*`，settings.yaml；凭证只放 .env 的 `ERP_TEST_USERNAME/PASSWORD`）：
  登录 `POST /user/login`（密码 MD5 后传输）→ `data.token` → 执行层 session 级登录一次，
  以 `X-Access-Token` 头注入全部用例请求；无 token/token 失效 ERP 返回 **HTTP 500 + "loginOut"（非 401）**。

### 8.3 账号体系（业务领域关键信息）

| 账号 | 角色 | 边界 |
| :--- | :--- | :--- |
| `admin` | 平台运维用户（超级管理员） | 只能配置平台菜单、创建/管理租户；不能编辑任何业务数据 |
| `jsh`（测试账号） | 租户管理员（真正的业务管理员） | 租户内全部业务数据增删改查；凭证在 .env |

业务用例的生成、执行与造数据一律用租户账号 `jsh`；`admin` 登录后也无法编辑业务数据。统一带 token 的执行层会让「鉴权异常」类用例失败，演示时挑查询类正向用例（幂等无副作用）。

## 9. 验收指标

- 单任务内 10+ 用例串行执行（任务之间串行：单 Worker `--pool=solo --concurrency=1`）；Web 响应稳定 <50ms（异步解耦，不阻塞在 pytest 上）。
- 死循环用例被超时强杀，任务进入明确终态，系统不卡死。
- Worker 重启后（或 Beat 周期到点）：运行中超时任务、排队超时任务被迁移 failed；失败任务可人工重试。
- 回归口径：默认 `pytest -q` 实测 497 passed / 4 deselected（slow 用例被 `addopts` 的 `-m 'not slow'` 排除，需 `pytest -m slow` 单独跑）；全项目覆盖率见最新 CI（2026-11-12 本地全量实测，以最新 CI 运行为准）。slow 里含 `tests/e2e/test_real_full_chain.py` 的 3 个真链路用例（真 pytest 子进程 / 真 LLM / 进程级全链路）。

## 10. 执行器抽象与 Jenkins 集成

**为什么抽执行器**：原 `_execute_cases` 把「建 workspace → 起 pytest → 解析 JUnit」写死在一处，而企业里执行机常由 CI 统一管理（Jenkins / K8s），平台需要能「投递」而不只是「自己跑」。现在执行器只负责「跑 + 解析」，状态机与事务边界仍留在 `ExecutionService`（`_mark_running` 条件更新、`_fail` 分类、取消竞态语义都不变）。

| 执行器 | 配置 | 行为 | 适用场景 |
| :--- | :--- | :--- | :--- |
| `local`（默认） | `execution.executor=local` | 渲染用例到 `.workspace/tasks/{task_id}/` → 子进程 pytest → 解析 `report.xml` | 本地开发 / 单机部署 |
| `jenkins` | `execution.executor=jenkins` | 触发 Jenkins job → 轮询构建 → 下载 JUnit 制品 → 同一解析器 | 执行机由 CI 统一管理 |

- 实现：`app/services/executors/{base,local,jenkins}.py`，`base.py` 定义 `Executor` 协议与 `ExecutionResult(summary, entries, build_url)`；工厂 `get_executor()` 按 `execution.executor` 选择，未知值抛 `EXECUTOR_NOT_SUPPORTED`（配置写错时由任务外层兜底落 `failed(internal)`，不会静默回落到 local）。
- HTTP 细节收敛在 `app/utils/jenkins_client.py`（外部调用唯一封装，超时全部来自 `jenkins.*` 配置；凭据按 `jenkins.api_token_env`（默认 `JENKINS_API_TOKEN`）从 `.env` 读取，只进 `Authorization` 头，不拼 URL、不入日志）。

**触发契约**（Jenkins 侧 job 必须参数化，平台按 `buildWithParameters` 调用）：

| 参数 | 含义 |
| :--- | :--- |
| `TASK_ID` | 平台任务 id（构建日志/回传定位） |
| `CASE_IDS` | 逗号分隔的用例 id |
| `BASE_URL` | 被测系统 base_url |

> 仓库自带的 `Jenkinsfile` 是项目 CI（参数 `PYTHON_BIN`/`RUN_SLOW`/`RUN_DOCKER`），不接收 `TASK_ID`/`CASE_IDS`；要让平台投递执行，需要另建一个参数化用例执行 job，按上表契约取参，并在构建里归档 `reports/junit.xml`（路径与 `jenkins.artifact_path` 对齐）。

**结果回读**：`GET /job/{job}/{number}/artifact/{jenkins.artifact_path}` 取 JUnit XML 后交给既有 `junit_parser`，口径见 §10.1。

### 10.1 本地执行与 Jenkins 执行的口径一致性

| 维度 | local | jenkins |
| :--- | :--- | :--- |
| 用例来源 | 平台读 DB 的 active 用例，渲染 `test_{case_id}.py` | 平台只传 `TASK_ID`/`CASE_IDS`/`BASE_URL`，由 job 自己 checkout 与生成用例文件 |
| 执行位置 | 平台子进程（`run_cmd`） | Jenkins agent |
| 原始结果 | `.workspace/tasks/{id}/report.xml` | 构建制品 `{jenkins.artifact_path}`，落到 `jenkins-junit.xml` |
| 解析 | `parse_junit_xml` | 同一个 `parse_junit_xml` |
| 超时 | 任务 `timeout_seconds` → `communicate(timeout)` | 任务 `timeout_seconds` 作为整条链 deadline；单请求另受 `jenkins.connect_timeout`/`read_timeout` 约束 |
| 结果键 | `executor=local`，`build_url=null` | `executor=jenkins`，`build_url=<构建页地址>` |

共同口径有三条：

- 多 `<testsuite>` 累加（pytest-xdist 会产出多个 suite）。
- `passed = tests - failures - errors - skipped`（`tests` 含 skipped，必须扣减）。
- 构建结果为 FAILURE/UNSTABLE/ABORTED 时仍先回读制品：有用例失败时 Jenkins 就是 FAILURE，直接判任务失败会让平台拿不到「哪条用例红了」；只有制品缺失或非法才落 `jenkins`/`parse` 阶段。

因此 `total/passed/failed/skipped/duration_ms` 在两种执行器下语义一致，报告消费方不需要按执行器分支处理。

**错误码与落库**：

| error_stage | 错误码 | 含义 |
| :--- | :--- | :--- |
| `jenkins` | `JENKINS_NOT_CONFIGURED` / `JENKINS_UNAVAILABLE` / `JENKINS_TRIGGER_FAILED` / `JENKINS_HTTP_ERROR` / `JENKINS_BUILD_CANCELLED` / `JENKINS_NO_REPORT` | 配置缺失、网络不可达、触发响应缺 Location、HTTP 4xx/5xx、队列中取消、构建未产出制品 |
| `timeout` | `JENKINS_TIMEOUT` | 排队或构建等待超过任务 `timeout_seconds`，或单请求 connect/read 超时 |
| `parse` | `JENKINS_REPORT_INVALID` | 制品存在但不是合法 JUnit XML |

与本地执行的 `command|subprocess|timeout|parse` 同级可区分。`JENKINS_NOT_CONFIGURED` 在标 running 之前抛出，任务直接失败而不是停在 running 等回收（见 `app/services/executors/jenkins.py` 的 `is_configured` 前置判断）。`result_summary` 增 `executor` 与 `build_url` 两个键：本地执行时 `build_url` 为 null，Jenkins 执行时为构建页地址。

**安全边界**：平台只需能出网访问 Jenkins（不需要开放入站回调端口），Jenkins 也不需要持有平台令牌；token 轮换只改 `.env`。

**取消与重试的边界**：`cancel_task` 只杀本地 `tasks.pid`。Jenkins 任务没有本地进程，取消写平台终态后远端构建仍会跑完，Worker 也要等到轮询结束或 `timeout_seconds` 到点；要真正中止远端构建需调用 Jenkins 的 stop API，当前未实现。重试不锁定上一轮执行器，按当前 `execution.executor` 重新选择。

**取舍**：只做轮询回读、不做入站回调——平台不必对外开放端口，凭据与签名面收敛；代价是端到端延迟受 `jenkins.poll_interval_seconds` 影响，任务总时长受 `timeout_seconds` 约束。若确有「Jenkins 主动推结果」的需求，再补 HMAC 回调（时间戳防重放 + `build_number` 幂等），并把来源限制与审计日志一起做。

**验证状态（一次真实环境验证，不等于生产级持续集成）**：

| 层次 | 状态 | 证据 |
| :--- | :--- | :--- |
| 客户端逻辑（crumb/触发/排队/轮询/取制品/错误码） | 单测覆盖，无网络 | `tests/unit/test_jenkins_client.py`（`httpx.MockTransport`） |
| 执行器分派与回读契约 | 单测覆盖，假客户端 | `tests/unit/test_executors.py`（含 Jenkins 无本地 pid、制品缺失、非法 XML） |
| 真实 Jenkins 端到端 | 已在本机 Jenkins 2.580.2 上完成一次完整验证 | 本机等价 job 构建 #7/#8 SUCCESS（368 passed / 4 deselected）；`scripts/jenkins_smoke.py` 触发 #8 → 回读 40,182 字节 junit → `total=368 passed=368` |
| 跨机器复现 | 依赖工具链与 job 配置，不是开箱即用 | 需 Jenkins + JUnit/Coverage/HTML Publisher/Allure 插件、Allure CLI 2.32.0、agent Python 3.12+；参数化 job 契约见本节 |

> 结论口径：集成代码、单测与一次真实 Jenkins 实跑都已完成；跨机器可复现性依赖上表工具链，因此对外表述用「已在真实 Jenkins 上完成一次完整验证（build #7/#8）」，不要写成「生产集成已完成」。

**`scripts/jenkins_smoke.py` 怎么用**（验证入口）：走「触发 → 轮询 → 取制品 → 解析」全链路，不依赖 Celery/Redis，用来单独确认 Jenkins 侧配置（`jenkins.*` + `JENKINS_API_TOKEN`）正确；跑通会打印构建号、制品字节数与 JUnit 汇总，这三项可直接作为联调证据。

## 11. Allure 报告（Jenkins / CI 侧）

平台自身的 HTML 报告不依赖 Allure（见 §7，保持单文件、零外部依赖）。Allure 用在**流水线侧**，把 `pytest --alluredir` 的结果渲染成可交互报告；仓库的 GitHub Actions（`.github/workflows/ci.yml`）与 `Jenkinsfile` 都产出 `reports/allure-results`，Actions 侧 CLI 固定 2.32.0，Jenkins 侧用全局工具 `allure-cli`（本机验证环境为 2.32.0）。本机实测通过（Jenkins 2.580.2 + Allure plugin 3.0.0 + Allure CLI 2.32.0）：构建 #7/#8 SUCCESS，`GET /job/{job}/{build}/allure/` 返回 HTTP 200 且页面含 `Allure Report`。

```text
// 1) 测试阶段产出结果目录
.venv/bin/pytest --alluredir=reports/allure-results --clean-alluredir ...
// 2) 发布阶段：必须显式指定安装名，否则插件报 "No Allure installation found"
allure includeProperties: false, jdk: '', commandline: 'allure-cli', results: [[path: 'reports/allure-results']]
```

**踩过的坑（复现时可直接避开）**：

| 现象 | 根因 | 做法 |
| :--- | :--- | :--- |
| `No Allure installation found` | Allure 3.x 插件包名从 `hudson.plugins.allure` 改为 `org.allurereport.jenkins`；且 pipeline 未指定安装名 | 注册工具后，步骤里显式 `commandline: 'allure-cli'` |
| `ClassCastException: [Lhudson.tools.ToolInstallation;` | 手写的 `<installations>` 缺 `class="...AllureCommandlineInstallation-array"`，XStream 按基类数组反序列化 | 数组节点必须带该 `class` 属性 |
| 工具注册后仍不生效 | `<properties>` 里塞了 `InstallSourceProperty`（自动安装语义），与「手工填 home」冲突导致整项被丢弃 | 该结构不写 `<properties>`，只留 `name` + `home` |
| 构建成功但报告页 404 | 错误的 URL 形态（`/allure-report/`） | 正确入口是 `/job/{job}/{build}/allure/` |

产物：`allure-results/`（原始数据，可归档）+ 生成的 `allure-report/`（HTML）。`Jenkinsfile` 里 allure 步骤包 `try/catch`：未配全局工具时只 echo 警告，不让报告层把已通过的测试门禁判红。

CI 侧的标准报告产物（两处同源）：`reports/junit.xml`（逐用例）、`reports/coverage.xml` 与 `reports/htmlcov`（覆盖率）、`reports/allure-results`（Allure 原始数据）。GitHub Actions 用 `EnricoMi/publish-unit-test-result-action` 发布逐用例结果、`upload-artifact` 归档 `reports/`（14 天）；Jenkins 用 `junit` / `recordCoverage` / `publishHTML` / `allure` / `archiveArtifacts`。两侧都带守卫（`hashFiles`、`fileExists`、`try/catch`）：收集期崩溃时不产 `junit.xml`，报告步骤不能把「测试失败」伪装成「报告发布失败」。

仓库 `Jenkinsfile` 是项目 CI，与 GitHub Actions 等价：参数 `PYTHON_BIN` / `RUN_SLOW` / `RUN_DOCKER`，阶段按四组组织（准备环境 → 静态检查 + 文档门禁 → 测试与覆盖率 → 慢测试与镜像构建，末组是两个可选 stage）。覆盖率门槛与 CI 同源：`pyproject.toml` 的 `fail_under=60`，`services` + `utils` 核心模块单独 `--fail-under=80`。

## 12. 相关文档

- [api.md](api.md) — `POST /tasks`、取消/重试/报告端点契约
- [database.md](database.md) — `tasks` 表字段与状态机
- [architecture.md](architecture.md) — 进程隔离与故障边界
- [configuration.md](configuration.md) — 超时/白名单等执行相关配置项
- [README.md](README.md) — 文档地图与阅读路径
- [../README.md](../README.md) — 项目入口（快速开始 / 常见问题）
