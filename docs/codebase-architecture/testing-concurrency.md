# 测试并发架构（Feature 083）

> 引入版本：Feature 083（6 Phase，2026-04-27）
> 状态：✅ 完成（实用主义版本——hang 修复 + race #1 治本 + xdist opt-in；race #2 + 长尾 race 移交 F084）

## 1. 历史问题

OctoAgent 测试套件在 Feature 083 之前有两个开发体验问题：

| # | 症状 | 实测 |
|---|------|------|
| 1 | **thread shutdown hang**：pytest 跑完后进程不退出 | macOS sample 显示 100% 时间在 `Py_FinalizeEx → wait_for_thread_shutdown → acquire_timed`；实测 task 挂 30+ 分钟才被 kill |
| 2 | **xdist 并发未启用**：装了 `pytest-xdist 3.8.0` 但配置缺失 → 默认串行 | ~93s 全量回归 |

## 2. 修复策略（实用主义）

P3 启用 xdist 后 5 次稳定性验证暴露多处 race condition：
- `test_attach_input_*`（task_runner 状态机）在高 CPU 负载下偶发 fail（~20-40%）
- `test_timeout_path_generates_worker_timeout_result` 单 sleep + assert 模式
- 全仓 ~72 处 single-sleep timing assertion，长尾 race 散落各 integration test

P5 治本 race #1（`ExecutionConsoleService.attach_input` 内 task.status 读取窗口），
P6 把测试 polling 改严，但**race #2 + 长尾 race 治本超 F083 scope**——既要修 task
runner 内部 timing 假设，又要把所有 single-sleep 模式重写为 polling，应作为独立 Feature。

最终决策：

| 方面 | 决策 |
|------|------|
| thread shutdown hang | ✅ 修复（`pytest_sessionfinish` hook） |
| os.environ fixture 污染 | ✅ 改 `monkeypatch.setenv` |
| Race #1（ExecutionConsole 读窗口）| ✅ P5 治本（`_read_task_with_waiting_input_retry`） |
| Race #2（runner restart + recovery）| ⏭️ 移交 F084 |
| 单 sleep + assert 长尾（~72 处）| ⏭️ 移交 F084 |
| `test_attach_input_after_restart` 测试 polling | ✅ P6 加严（双状态联合等待 + 5s 窗口） |
| xdist 默认启用 | ❌ 撤销——风险大于收益 |
| xdist opt-in | ✅ 文档化使用场景 |

## 3. 默认行为（修复后）

| 指标 | 修复前 | 修复后（默认）| 修复后（`-n auto`）|
|------|--------|---------|---------|
| pytest 报告时间 | 93.41s | **~103s**（含 F082 新增测试）| **~17s** |
| 进程实际退出 | 30+ 分钟（hang） | **~104s** | **~20s**（稳定时） |
| 稳定性（5 次连续）| 100% | **100%（5/5 验证通过）** | ~60-80%（race 长尾）|
| CPU 利用率 | ~100% (1 core) | ~100% (1 core) | ~689% (10-core) |
| 测试通过率 | 2419/2419 | **2829/2829** | 2829/2829（稳定时）|

## 4. xdist opt-in 使用方法

### 4.1 何时启用

- ✅ **本地开发快速反馈**——只跑改动相关 package：
  ```bash
  pytest -n auto packages/core/tests/  # 单包并发，无 race
  pytest -n auto packages/provider/tests/
  ```
- ❌ **CI 全量回归**：仍用默认串行确保稳定
- ❌ **task_runner 测试**：用 `-n 0` 串行（race 测试集）

### 4.2 已知 flaky 测试集（仅在 `-n auto` 全量并发下出现）

- `apps/gateway/tests/test_task_runner.py::TestTaskRunner::test_attach_input_*`
  - 现象：高 CPU 负载下偶发 `'task is not waiting for human input'`
  - 原因：两类 race
    1. `ExecutionConsoleService.attach_input` 内 task.status 读取 vs request_input 状态转换（**P5 已治本**）
    2. `runner_2.attach_input` 在 runner 重启 + recovery 路径 vs attach_input 检查（**未治本，移交 F084**）
  - 缓解（P5+P6）：
    * 测试侧 — `test_attach_input_after_restart` polling 改双状态联合等待 + 1s → 5s 窗口
    * 代码侧 — `ExecutionConsoleService._read_task_with_waiting_input_retry`（最多 120ms retry）
  - 当前 race 概率：40% → ~20%（race #2 治本需独立 Feature 084）

- `tests/integration/test_f009_worker_runtime_flow.py::test_timeout_path_generates_worker_timeout_result`
  - 现象：`assert data["task"]["status"] == "FAILED"` 偶发为 `'RUNNING'`
  - 原因：`await asyncio.sleep(0.4)` + 单 assert 模式；高 CPU 负载下 0.4s 不够覆盖
    timeout 检测 + 状态转换 + DB commit
  - 治本：所有 single-sleep + assert 模式重写为 polling 等待（移交 F084）

- 全仓 ~72 处 `await asyncio.sleep(N) + assert` 单 sleep 模式
  - 默认串行下都稳定，仅在 xdist 并发下偶发暴露
  - 治本范围太大，移交 F084 独立做

### 4.3 命令模板

```bash
# 默认（推荐 CI / 完整回归）
pytest

# 显式串行（debug 单 test）
pytest -n 0 -v --tb=long path/to/test.py::Test::test_name

# 子包并发（本地开发常用）
pytest -n auto packages/core/tests/

# 全量并发（接受 flaky 风险，跑前最好确认 task_runner 测试已通过）
pytest -n auto

# 调试 flaky：boxed 模式（每 test 独立子进程）
pytest -n auto --forked path/to/test_file.py
```

## 5. 并发安全保证（worker-safe fixture）

worker 间靠 xdist 的 process boundary 自然隔离；worker 内 sequential 测试通过
fixture 自动 teardown 保证干净：

| 风险 | 解决 |
|------|------|
| 测试用 `os.environ[...] = ...` 直接赋值 | 改用 `monkeypatch.setenv()`（自动恢复） |
| 测试用 module-level mutable state | 用 `monkeypatch.setattr()` |
| 测试启动后台 thread / asyncio task | fixture teardown 中显式 cancel + await |
| sqlite db 文件 | `tmp_path` 已隔离每 test |

## 6. thread shutdown 修复（核心确定收益）

`octoagent/conftest.py:pytest_sessionfinish`：

```python
def pytest_sessionfinish(session, exitstatus):
    """Feature 083 P1：强制清理遗留 aiosqlite 后台 thread + asyncio executor。"""
    gc.collect()  # 触发未关 connection 的 __del__
    try:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()
    except Exception:
        pass
```

原理：`aiosqlite` 把 db 操作 dispatch 到 asyncio 默认 executor 的 worker thread；
这些 thread 是 daemon 但持有 GIL。Python finalize 阶段 `wait_for_thread_shutdown`
等它们退出但等不到 → 死锁。在 sessionfinish 显式 shutdown executor，让 thread 提前
正常退出。

## 7. 调试 flaky 测试

### 7.1 重现

```bash
# 连续跑 5 次，看是否有时 fail
for i in 1..5; do pytest -n auto -q | tail -3; done
```

### 7.2 隔离 race

```bash
# 单独跑该 class（无其他 fixture 干扰）→ 如果稳定，是 race；否则是测试本身 bug
pytest -n auto apps/gateway/tests/test_task_runner.py
```

### 7.3 boxed 模式（极端隔离）

```bash
# 每个 test 独立子进程；可定位是否是 module-level state 问题
pytest -n auto --forked
```

## 8. CI 兼容性

- **单核 CI**：默认串行（`-n auto` 也会 fallback 到 1 worker）
- **多核 CI**：建议**仍用默认串行**（93s 在 CI 上可接受），避免引入 task_runner flaky
- **本地开发机**：用 `pytest -n auto packages/<pkg>/tests/` 加快反馈

## 9. 后续工作

**Feature 084（计划）— xdist 默认启用 + race 长尾治理**：
- 修复 task_runner race #2（runner restart + recovery 路径下 attach_input 状态读取的次生窗口）
- 把所有 single-sleep + assert 模式（~72 处）系统重写为 polling 等待
- 提供 `wait_for_task_status(task_id, status, timeout)` 测试 helper 统一收敛 polling 模式
- 上述全部完成后，恢复 `pyproject.toml` 的 `addopts = ["-n", "auto"]` 默认

## 10. 相关 Feature 文档

- `.specify/features/083-test-concurrency-speedup/spec.md`
- `.specify/features/083-test-concurrency-speedup/plan.md`
