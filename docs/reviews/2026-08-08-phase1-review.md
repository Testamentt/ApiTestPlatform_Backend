# Phase 1 核心执行闭环 · 代码评审（2026-08-08）

> 评审对象：Phase 1「创建用例 → 异步执行 → 查看结果」闭环（2026-08-06 交付）。
> 关联：[RULES.md §14](../.claude/rules/RULES.md)（合入前自查）、[sessions/2026-08-06-phase1-mvp.md](../sessions/2026-08-06-phase1-mvp.md)、[sessions/2026-08-08-review-fixes.md](../sessions/2026-08-08-review-fixes.md)。

## 1. 评审范围与对象

Phase 1 全部生产代码与测试（2026-08-08 状态，含评审后修复）：

| 层 | 文件 |
| --- | --- |
| 入口/配置 | `app/main.py`、`app/celery_app.py`、`app/core/`（config / database / logging / exceptions） |
| 用例/任务 | `app/models/test_case.py`、`app/models/task.py`、`app/repositories/case_repository.py`、`app/repositories/task_repository.py`、`app/services/case_service.py`、`app/services/task_service.py`、`app/services/dispatcher.py` |
| 执行引擎 | `app/services/execution_service.py`、`app/utils/case_generator.py`、`app/utils/junit_parser.py`、`app/utils/report_util.py`、`app/utils/subprocess_util.py`、`app/tasks/execute_cases.py`、`app/tasks/supervisor.py` |
| API | `app/api/v1/`（cases / tasks / health / deps） |
| 测试 | `tests/unit`、`tests/api`、`tests/tasks`（conftest + fakes） |

## 2. 评审方法

- 全量代码走查 + 62 agent 多维度对抗性审查（执行链路 / 配置 / 规则合规 / 测试质量 / 文档一致性五视角）。
- 关键发现逐一**实测复现**（RCE 注入渲染出 test_1.py 并 compile、junit 统计算数核对、超时链路追踪）。
- 评审后按 P0/P1/P2 分级修复，每项修复补测试，最终 104 测试全绿 + ruff 全绿复核（§14 第 1/7 问）。

## 3. 发现的问题与修复状态

### 3.1 Critical（已修复）

**RCE：`case.name` 未转义拼入测试文件 docstring**（[case_generator.py](../../app/utils/case_generator.py)）
- 现象：`name` 允许换行/引号/#，直接拼进三引号 docstring；path/method/params/body 均用 `!r`，唯独 name 遗漏。恶意 name 可闭合 docstring 注入任意 Python 代码，pytest 收集阶段以 Worker 权限执行。Phase 1 无鉴权，匿名完整链（建 case → confirm → 执行）成立。
- 复现：payload `x"""\nimport os\nos.system("echo PWNED")\n# ` 渲染出的 test_1.py `compile()` 通过，exec 时注入代码执行。
- 修复：name 只进文件头注释且 repr 转义（注释不解析转义 + repr 无裸换行，双重护栏）；补 ast 结构级测试（模块级仅 import + def，且无 `os.system` 调用节点）。

### 3.2 Major（已修复）

| 发现 | 位置 | 修复 |
| --- | --- | --- |
| junit `passed` 未扣减 `skipped`，被跳过用例计为通过、通过率虚高 | [junit_parser.py](../../app/utils/junit_parser.py) | `passed = total - failed - errors - skipped`，补 skipped 算术测试 |
| pytest returncode 未检查：收集失败（exit 5）产出 `tests=0` junit 仍判 SUCCESS | [execution_service.py](../../app/services/execution_service.py) | returncode 非 (0,1) → failed(error_stage=subprocess)；1=有用例失败但 junit 有效，维持 SUCCESS |
| `SoftTimeLimitExceeded` 未捕获，软超时后 DB 可能停 RUNNING | [execute_cases.py](../../app/tasks/execute_cases.py) | 捕获后 `force_fail_timeout` 落 failed(timeout) + best-effort 杀树（RULES §8.2 MUST） |
| health `socket_timeout=2` 硬编码违反 §2.3 | [health.py](../../app/api/v1/health.py) | 改为 config 字段 `redis.socket_timeout` |
| 死配置：`visibility_timeout`/`max_retries`/`result_expires` 定义未接线 | [config.py](../../app/core/config.py)、[celery_app.py](../../app/celery_app.py) | visibility 经 `broker_transport_options`、max_retries/result_expires 取自 config |
| `.env.example` 提交真实 Redis 密码（本地 requirepass）违反红线 #8 | [.env.example](../../.env.example) | 改为占位符，真实值只留 .env |

### 3.3 Medium（已修复）

- **异常终态兜底缺失**：`execute_cases` 只捕 AppError，OSError/ValueError/OperationalError 会卡 PENDING/RUNNING → 外层 try/except 兜底 failed(internal)。
- **短事务违反**：`scan_stale_tasks` 持 Session 期间调 `kill_process_tree`（§2.1）→ 重构为「先查关事务 → 杀树 → 再开事务迁移状态」。
- **事务无 rollback**：`base.py` add/delete、case_service、task_repository、execution_service 各 commit 点 → try/except+rollback+raise（§15 示例 2）。

### 3.4 Minor（已修复/遗留）

- 已修复：ruff 违规（SIM103/I001）、测试写真实 `.workspace` 目录（保留，标记遗留）、health 探测失败静默（补 warning 日志）。
- 遗留：`timeout_seconds` 请求参数只参与 run_id 指纹、不改变实际 subprocess 超时（设计权衡，已在 execution-engine.md 注释说明）；run_id 并发竞态 IntegrityError→500（MVP 单写者场景概率极低，未处理）。

## 4. §14 合入前自查清单（10 问）

| # | 自查项 | 结论 |
| --- | --- | --- |
| 1 | ruff/linter 通过 | ✅ `ruff check app tests` 全绿 |
| 2 | 无裸 except | ✅ 全部 except 有日志/明确处理；health 探测失败 warning 记录 |
| 3 | 外部调用带 timeout 且取自 config | ✅ subprocess/redis 探测均来自 config（pytest_timeout/socket_timeout） |
| 4 | 事务有 rollback/finally 兜底 | ✅ 全部写点 try/except+rollback |
| 5 | LLM 输出校验后写库且 status=draft | ➖ Phase 1 无 LLM（Phase 3 实施） |
| 6 | 无硬编码密钥、.env 未入库 | ✅ .env.example 占位符化，`git check-ignore` 确认 .env 未跟踪 |
| 7 | pytest 全绿且不碰真实 LLM | ✅ 104 全绿；LLM 未接入，subprocess 测试用 FakeSubprocess 隔离 |
| 8 | 注释只解释 why | ✅ 走查通过（含 §1.4 强制 why 注释点） |
| 9 | 配置走 Pydantic 并更新 .env.example | ✅ extra=forbid + 新增项同步 |
| 10 | 提交信息合规（Conventional Commits、无 AI 署名） | ✅ 分点小提交，仅本人署名 |

## 5. 评审结论

**通过（含修复后复核）**。Phase 1 架构成立（分层/幂等/超时双保险/防幻觉护栏），评审发现的 1 个 Critical + 6 个 Major + 3 个 Medium 已全部修复，遗留 2 项 Minor 有明确理由且不阻塞。核心链路「创建用例 → confirm → 触发执行(202) → 轮询 → HTML 报告」104 测试覆盖 + 端到端冒烟通过。

## 6. 遗留问题

- `timeout_seconds` 参数语义（仅幂等指纹、不实际生效）——设计权衡，已在文档说明。
- run_id 并发 IntegrityError 未兜底——MVP 单 Worker 串行写库场景概率极低，Phase 4 生产化时处理。
- 测试写真实 `.workspace`（未 tmp_path 隔离）——minor，Phase 3 前收敛。
- `tests/integration`（e2e）层缺失——详见 Phase 2 评审遗留。
