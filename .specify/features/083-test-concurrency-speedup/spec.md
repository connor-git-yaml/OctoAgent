# Feature 083 — 测试并发加速 + thread shutdown 修复

> 作者：Connor
> 日期：2026-04-27
> 上游：Feature 082 收尾时实测发现的回归慢 + thread shutdown hang
> 模式：spec-driver-feature

## 1. 背景

OctoAgent 当前测试套件 **2419 测试 / ~93 秒**——单测平均 38ms 不算慢，但**串行执行 + Python finalize 时 thread shutdown hang** 让开发体验受损：

### 1.1 实测问题

**症状 A：thread shutdown hang**
- 跑完所有测试后，pytest 进程**卡在 `Py_FinalizeEx → wait_for_thread_shutdown → acquire_timed`**（GIL acquire 死锁）
- macOS sample 显示 100% 时间在 `lock_PyThread_acquire_lock`
- 根因：aiosqlite 后台 daemon thread + asyncio event loop close 顺序不当
- 实测：用 `tail -3` 接管道时 pytest 退出失败 → 整个 task 挂 30+ 分钟才被外部 kill

**症状 B：未启用 xdist 并发**
- 已装 `pytest-xdist 3.8.0`
- `pyproject.toml` 没配 `addopts = "-n auto"` → 默认串行
- 10-core M1 上潜在 4-6x 提速空间（~93s → ~15-25s）

### 1.2 并发风险盘点

| 风险点 | 文件 | 严重度 | xdist 隔离能否解决 |
|------|------|------|------|
| `event_loop` session-scope | `octoagent/conftest.py:12` | 中 | ✅（每 worker 一个 loop）|
| `os.environ[...] = ...` 全局污染 | `apps/gateway/tests/conftest.py:25-27` | 中 | ✅（worker 进程隔离）|
| `AgentContextService.set_llm_service / set_provider_router` class-level state | `agent_context.py:557-562` | 中 | ✅（每 worker 独立 module 加载）|
| 共享 `~/.octoagent/` 目录写入（如果有）| - | 高 | ❌（worker 共享 home）|
| sqlite 文件锁 | `tmp_path` 已隔离 | 低 | ✅ |
| aiosqlite thread shutdown 顺序 | `_finalize` hook | 高 | ❌（每 worker 都遇到）|

xdist 的 worker-process 隔离能解决 5/6 风险；剩下 1 个（aiosqlite shutdown）必须独立修。

## 2. 用户故事

- **US-1**（P0）：作为开发者，**`pytest` 全量回归 ≤ 30 秒**（修复后从 ~93s → ~25s）
- **US-2**（P0）：**pytest 退出不卡 finalize**（不再需要 `kill -9`）
- **US-3**（P1）：**单 Phase 改动只跑相关包**（如 `pytest packages/core/tests` ≤ 10s）—— 已是默认行为，仅需文档化
- **US-4**（P1）：作为开发者，**xdist 并发不引入 flaky 测试**（同一 commit 跑 10 次结果一致）

## 3. 功能需求（FR）

### FR-1：启用 pytest-xdist 全局并发

- `pyproject.toml [tool.pytest.ini_options]` 加 `addopts = "-n auto"` 默认 worker = CPU 核数
- 用户可通过 `pytest -n 0` 显式禁用并发（debug 时）
- 用户可通过 `pytest -n 4` 限制 worker 数

### FR-2：修复 fixture 的全局状态污染

- `apps/gateway/tests/conftest.py:25-27` 改用 `monkeypatch.setenv()` 替代 `os.environ[...] = ...`
- `gateway_tmp_dir / app / client` fixture 加 `monkeypatch` 参数依赖
- 确保单 worker 内 fixture 顺序执行也无残留

### FR-3：修复 aiosqlite thread shutdown hang

- 在 root `conftest.py` 加 `pytest_sessionfinish` hook：
  - 强制关闭所有未关 aiosqlite connection
  - asyncio 默认 executor shutdown
- 移除 `event_loop` session-scope fixture（与 `asyncio_mode = "auto"` 不兼容）
- 让 `pytest-asyncio` 用默认 function-scope event loop

### FR-4：xdist-unsafe 测试隔离（如有）

- 跑 `pytest -n auto -p xdist.boxed` 验证；boxed 模式让每个 test 跑在独立子进程
- 检测 flaky 测试（连续 5 次 `pytest -n auto`）
- 标 `@pytest.mark.serial` + 在 conftest 用 `pytest_collection_modifyitems` 让这些跑串行

### FR-5：文档

- `docs/codebase-architecture/testing-concurrency.md`（新增）：
  - xdist 配置说明
  - 哪些 fixture 是 worker-safe
  - 调试 flaky 测试的方法
- `CLAUDE.md` 更新：开发者运行测试的推荐姿势

## 4. 不变量

- **I-1**：现有 2419 个测试全部继续通过
- **I-2**：测试结果不依赖运行顺序（修复 race condition 而不是 mask）
- **I-3**：单 worker（`-n 0`）模式仍工作（兼容 debug）
- **I-4**：CI 上单核机器仍能跑（xdist auto 退化到 1 worker）

## 5. Scope Lock

- ❌ 不重写测试基础设施（不引入新 fixture 框架）
- ❌ 不改 `asyncio_mode = "auto"` 全局策略
- ❌ 不动业务逻辑（runtime 行为不变）
- ❌ 不引入 docker-based test runner

## 6. 风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| xdist 暴露隐藏的 race condition → flaky | 高 | CI 不稳定 | FR-4：连续跑 5 次 + boxed 模式验证；遇到就标 serial |
| `monkeypatch` 替换破坏现有 test | 低 | 部分 test fail | 逐 fixture 改写 + 全量回归 |
| `pytest_sessionfinish` hook 顺序问题 | 中 | 仍 hang | 用 `--forked` 兜底（每 test 独立子进程，Python finalize 隔离）|
| 单核 CI 上 xdist overhead 反而慢 | 低 | CI 慢一点 | xdist auto 在单核上 fallback 串行 |
| 老开发机器（CPU 少）OOM | 低 | 测试 OOM | 文档建议 `-n 4` 上限 |

## 7. 验收准则

- [ ] `pytest` 全量回归 ≤ 30s（10-core M1，含 finalize）
- [ ] `pytest` 退出不卡 finalize（`time pytest ...` 真实退出 ≤ 35s）
- [ ] 连续 5 次全量回归结果一致（无 flaky）
- [ ] `pytest -n 0` 仍工作（兼容 debug）
- [ ] CI 单核机器跑过（自动退化）
- [ ] 文档新增 `testing-concurrency.md`
