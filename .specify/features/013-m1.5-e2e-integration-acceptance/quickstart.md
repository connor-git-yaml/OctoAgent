# 快速上手指南: Feature 013 — M1.5 E2E 集成验收

**Date**: 2026-03-04
**面向对象**: 负责编写和运行 F013 集成测试的开发者

---

## 前置条件检查

在开始 F013 编码前，确认以下门禁条件均已满足：

```bash
# 1. 确认当前在正确分支
cd /Users/connorlu/Desktop/.workspace2.nosync/AgentsStudy
git branch --show-current
# 期望输出: feat/013-m1.5-e2e-integration-acceptance

# 2. 运行现有全量测试，确认 F002~F012 无失败
uv run pytest octoagent/ -q
# 期望: 所有现有测试通过，无失败（GATE 门禁满足）

# 3. 确认 logfire[testing] 可用
uv run python -c "from logfire.testing import capfire; print('capfire OK')"
# 期望输出: capfire OK
# 若失败，切换到 OTel InMemorySpanExporter 降级方案（见下文）
```

---

## 第一步: 运行现有集成测试（基线验证）

```bash
# 运行现有集成测试，确认基线健康
uv run pytest octoagent/tests/integration/ -v

# 预期：所有现有测试通过（test_f008_*, test_f009_*, test_f010_*, test_sc*.py 等）
```

---

## 第二步: 扩展 conftest.py

在 `octoagent/tests/integration/conftest.py` 末尾新增三个 fixture（参见 `plan.md` §实现规范）：

```bash
# 打开 conftest.py
open octoagent/tests/integration/conftest.py
```

新增内容（按 plan.md 规范）：
1. `app_with_checkpoint` fixture（场景 B 使用）
2. `watchdog_integration_app` fixture（场景 C 使用）
3. `watchdog_client` fixture（场景 C 使用）
4. `poll_until()` 工具函数（替代 `asyncio.sleep`）

**验证 fixture 可加载**：

```bash
uv run pytest octoagent/tests/integration/ --collect-only -q 2>&1 | head -30
# 确认新 fixture 无 import 错误
```

---

## 第三步: 新增四个测试文件

在 `octoagent/tests/integration/` 下新增以下文件（参见 plan.md §实现规范中的代码骨架）：

| 文件 | 对应场景 | 关键断言 |
|------|---------|---------|
| `test_f013_e2e_full.py` | 场景 A: 消息路由全链路 | ORCH_DECISION + WORKER_DISPATCHED + WORKER_RETURNED 事件均存在，task.status == SUCCEEDED |
| `test_f013_checkpoint.py` | 场景 B: 断点恢复 | ResumeEngine.try_resume() 返回 ok=True，resumed_from_node 正确 |
| `test_f013_watchdog.py` | 场景 C: 无进展告警 | TASK_DRIFT_DETECTED 事件写入 EventStore，cooldown 防止重复告警 |
| `test_f013_trace.py` | 场景 D: 全链路追踪 | capfire 捕获到 span，所有事件通过 task_id 串联 |

---

## 第四步: 逐场景运行验证

### 场景 A — 消息路由全链路

```bash
uv run pytest octoagent/tests/integration/test_f013_e2e_full.py -v
# 期望: 2 tests passed
```

**调试提示**：若 ORCH_DECISION 事件缺失，检查 `integration_app` fixture 是否初始化了 `TaskRunner`（含 OrchestratorService）。现有 `integration_app` 不含 TaskRunner，需升级 fixture 或使用 `f008_app` 模式（参见 test_f008_orchestrator_flow.py）。

> **注意**: 经代码检查，现有 `integration_app` fixture 未初始化 TaskRunner。场景 A 应复用 `f008_app` fixture 模式，或在 conftest.py 中新增包含 TaskRunner 的轻量 app fixture。参见 plan.md §conftest.py 扩展规范。

### 场景 B — 断点恢复

```bash
uv run pytest octoagent/tests/integration/test_f013_checkpoint.py -v
# 期望: 2 tests passed（正常恢复 + 幂等验证）
```

**调试提示**：若 `ResumeEngine._resume_locks` 在测试间泄漏，在 fixture teardown 中添加：
```python
from octoagent.gateway.services.resume_engine import ResumeEngine
ResumeEngine._resume_locks.clear()
```

### 场景 C — 无进展告警

```bash
uv run pytest octoagent/tests/integration/test_f013_watchdog.py -v
# 期望: 2 tests passed（首次告警 + cooldown 防重复）
```

**调试提示**：若 `get_events_by_types_since` 查询返回空，确认：
1. `WATCHDOG_NO_PROGRESS_CYCLES=1` 和 `WATCHDOG_SCAN_INTERVAL_SECONDS=1` 已设置（threshold = 1 秒）
2. `asyncio.sleep(1.1)` 等待时间足够（稍大于 threshold）
3. `TaskStore.list_tasks_by_statuses(NON_TERMINAL_STATUSES)` 能查询到 RUNNING 状态任务

### 场景 D — 全链路追踪

```bash
uv run pytest octoagent/tests/integration/test_f013_trace.py -v
# 期望: 2 tests passed（追踪断言 + 降级不影响业务）
```

**capfire 降级方案**（若 capfire 不可用）：
```python
# 替换 capfire fixture，使用 OTel InMemorySpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
import logfire

@pytest.fixture
def in_memory_spans():
    exporter = InMemorySpanExporter()
    # 通过 logfire.configure 注入（参见 logfire 文档）
    yield exporter
    exporter.clear()
```

---

## 第五步: 全量回归测试

```bash
# 运行全量测试 + 覆盖率报告（此步骤验证 SC-005：M1 能力不退化）
uv run pytest octoagent/ --cov=octoagent --cov-report=term-missing --cov-report=html -q

# 关注指标：
# - Feature 002~007 测试通过率: 100%（无新增失败）
# - 总体覆盖率: 不低于 F013 前基线
```

---

## 第六步: 产出 M1.5 验收报告

所有测试通过后，填写验收报告：

```bash
# 创建验收报告目录
mkdir -p /Users/connorlu/Desktop/.workspace2.nosync/AgentsStudy/.specify/features/013-m1.5-e2e-integration-acceptance/verification/
```

报告模板路径: `.specify/features/013-m1.5-e2e-integration-acceptance/verification/m1.5-acceptance-report.md`

报告必须包含（参见 spec FR-008 和 SC-006）：
- [ ] 场景 A 测试结果（SC-001 映射）
- [ ] 场景 B 测试结果（SC-002 映射）
- [ ] 场景 C 测试结果（SC-003 映射）
- [ ] 场景 D 测试结果（SC-004 映射）
- [ ] 全量回归执行摘要（SC-005 映射）
- [ ] 技术风险清单最终状态（research.md 中 8 条风险的最终状态）
- [ ] M2 准入结论声明

---

## 常见问题排查

### 问题 1: integration_app 中 Orchestrator 事件缺失

**现象**: 场景 A 中 ORCH_DECISION 事件不在 EventStore，但 task.status == SUCCEEDED。

**原因**: 现有 `integration_app` fixture 仅初始化 `LLMService`（旧路径），未走 `TaskRunner` + `OrchestratorService` 路径。

**解决**: 参考 `test_f008_orchestrator_flow.py` 的 `f008_app` fixture，确保 `TaskRunner` 已初始化并调用 `startup()`。

---

### 问题 2: WatchdogScanner 无法检测到 RUNNING 任务

**现象**: 场景 C 中调用 `scanner.scan()` 后，EventStore 无 `TASK_DRIFT_DETECTED` 事件。

**检查步骤**：
```python
# 在测试中临时添加调试断点
tasks = await store_group.task_store.list_tasks_by_statuses(NON_TERMINAL_STATUSES)
print(f"活跃任务数: {len(tasks)}")  # 应为 1

config = watchdog_integration_app.state.watchdog_scanner._config
print(f"阈值: {config.no_progress_threshold_seconds}s")  # 应为 1

# 若任务的 updated_at 小于 1 秒前，等待时间不足
```

---

### 问题 3: poll_until 超时

**现象**: `TimeoutError: poll_until 超时（5.0s）`

**原因**: Echo 模式下 LLM 处理通常 < 100ms，超时意味着后台任务未启动或 TaskRunner 未初始化。

**解决**: 确认 `TaskRunner.startup()` 已调用（检查 `app.state.task_runner`）。

---

### 问题 4: ResumeEngine 并发锁冲突

**现象**: 场景 B 中 `try_resume()` 挂起或返回意外结果。

**原因**: `ResumeEngine._resume_locks` 是类变量，前一个测试未清理锁。

**解决**:
```python
# 在场景 B fixture teardown 中添加
from octoagent.gateway.services.resume_engine import ResumeEngine
ResumeEngine._resume_locks.clear()
```

---

## 关键文件路径速查

| 文件 | 路径 |
|------|------|
| 现有 conftest | `octoagent/tests/integration/conftest.py` |
| F013 场景 A | `octoagent/tests/integration/test_f013_e2e_full.py` |
| F013 场景 B | `octoagent/tests/integration/test_f013_checkpoint.py` |
| F013 场景 C | `octoagent/tests/integration/test_f013_watchdog.py` |
| F013 场景 D | `octoagent/tests/integration/test_f013_trace.py` |
| WatchdogScanner | `octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/scanner.py` |
| WatchdogConfig | `octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/config.py` |
| CooldownRegistry | `octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/cooldown.py` |
| CheckpointStore | `octoagent/packages/core/src/octoagent/core/store/checkpoint_store.py` |
| ResumeEngine | `octoagent/apps/gateway/src/octoagent/gateway/services/resume_engine.py` |
| Gateway main | `octoagent/apps/gateway/src/octoagent/gateway/main.py` |
| 验收报告模板 | `.specify/features/013-m1.5-e2e-integration-acceptance/verification/m1.5-acceptance-report.md` |

---

## 执行测试

所有 F013 测试文件已在 `octoagent/tests/integration/` 下完成实现，可用以下命令独立或全量运行。

### 单场景运行（各自独立，无相互依赖）

```bash
# 场景 A — 消息路由全链路（SC-001）
uv run pytest octoagent/tests/integration/test_f013_e2e_full.py -v
# 预期输出：
# tests/integration/test_f013_e2e_full.py::TestF013ScenarioA::test_message_routing_full_chain PASSED
# tests/integration/test_f013_e2e_full.py::TestF013ScenarioA::test_task_result_non_empty PASSED
# 2 passed in ~0.5s

# 场景 B — 系统中断后断点恢复（SC-002）
uv run pytest octoagent/tests/integration/test_f013_checkpoint.py -v
# 预期输出：
# tests/integration/test_f013_checkpoint.py::TestF013ScenarioB::test_resume_from_checkpoint_after_restart PASSED
# tests/integration/test_f013_checkpoint.py::TestF013ScenarioB::test_resume_idempotency PASSED
# tests/integration/test_f013_checkpoint.py::TestF013ScenarioB::test_resume_with_corrupted_checkpoint PASSED
# 3 passed in ~0.2s

# 场景 C — 长时间无进展任务自动告警（SC-003）
uv run pytest octoagent/tests/integration/test_f013_watchdog.py -v
# 注意：每个测试含 asyncio.sleep(1.1)，预计耗时约 3.5 秒
# 预期输出：
# tests/integration/test_f013_watchdog.py::TestF013ScenarioC::test_watchdog_detects_stalled_task PASSED
# tests/integration/test_f013_watchdog.py::TestF013ScenarioC::test_watchdog_cooldown_prevents_duplicate_alerts PASSED
# tests/integration/test_f013_watchdog.py::TestF013ScenarioC::test_watchdog_threshold_config_override PASSED
# 3 passed in ~3.5s

# 场景 D — 全链路执行过程完整可追溯（SC-004）
uv run pytest octoagent/tests/integration/test_f013_trace.py -v
# 预期输出：
# tests/integration/test_f013_trace.py::TestF013ScenarioD::test_full_trace_spans_across_all_layers PASSED
# tests/integration/test_f013_trace.py::TestF013ScenarioD::test_trace_chain_continuity PASSED
# tests/integration/test_f013_trace.py::TestF013ScenarioD::test_trace_unaffected_when_backend_unavailable PASSED
# 3 passed in ~0.5s
```

### 全量运行（含 F013 新增 + M1 基线回归）

```bash
# 运行所有集成测试（75 个，含 F013 新增 11 个）
uv run pytest octoagent/tests/integration/ -v
# 预期输出末行：75 passed in ~15s

# 运行全量测试 + 覆盖率报告（SC-005 回归验收）
uv run pytest octoagent/tests/ -q --cov=octoagent --cov-report=term-missing
# 预期输出：
# 75 passed in ~15s
# TOTAL   ... 66%  （M1.5 验收基线覆盖率）
```

### 稳定性验证（FR-009 时序稳定性）

```bash
# 连续运行两次，确认无随机失败
uv run pytest octoagent/tests/integration/ -q && uv run pytest octoagent/tests/integration/ -q
# 预期：两次均输出 75 passed
```

### 仅运行 F013 新增测试

```bash
uv run pytest octoagent/tests/integration/ -k "f013" -v
# 预期：11 passed（场景 A 2 + 场景 B 3 + 场景 C 3 + 场景 D 3）
```
