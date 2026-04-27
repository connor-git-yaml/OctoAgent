# Feature 083 — 测试并发加速 · 实施计划

> 上游：spec.md
> 模式：spec-driver-feature

## 0. 总览

```
当前：pytest -q ... → 串行 93s + finalize hang
        ↓
P1: 修 thread shutdown hang → 串行 ~93s 但 finalize 干净退出
        ↓
P2: 修 fixture os.environ 全局污染 → 仍串行但 worker-safe
        ↓
P3: 启用 xdist auto → ~20-25s（4-6x 提速）
        ↓
P4: 验收 + 文档 + 标 serial 隔离少量 unsafe 测试
```

**核心原则**：先修底层 hazard（hang + env 污染），再开 xdist，最后审计 flaky。

---

## 1. Phase 划分

| Phase | 内容 | 估时 | 风险 |
|-------|------|------|------|
| **P1 修 thread shutdown hang** | 加 `pytest_sessionfinish` hook + 移除 session-scope event_loop fixture | 1h | 中（可能引入新 hang 模式） |
| **P2 修 os.environ fixture 污染** | gateway/tests/conftest.py 用 monkeypatch 替代 os.environ[...]=... | 30m | 低 |
| **P3 启用 xdist + 跑全量回归** | `addopts = "-n auto"` + 跑 5 次确认稳定 | 1h | 高（暴露 race condition） |
| **P4 隔离 flaky + 文档** | 标 `@pytest.mark.serial` + 文档 + 验收 | 1h | 低 |

**总计 ~3.5h / 4 个独立 commit**

---

## 2. Phase 1 — 修 thread shutdown hang

### 2.1 移除 session-scope event_loop fixture

`octoagent/conftest.py`：
```python
# 删除：
# @pytest.fixture(scope="session")
# def event_loop():
#     loop = asyncio.new_event_loop()
#     yield loop
#     loop.close()
```

`pytest-asyncio` 在 `asyncio_mode = "auto"` 下默认每个 test 用 function-scope event loop（更安全，每 test 用完即关）。session-scope event_loop 是历史遗留，与 `asyncio_default_fixture_loop_scope` 设置冲突。

### 2.2 加 pytest_sessionfinish hook

`octoagent/conftest.py` 末尾追加：
```python
def pytest_sessionfinish(session, exitstatus):
    """Feature 083 P1：强制关闭遗留 aiosqlite 后台 thread + asyncio executor。

    历史问题：pytest 跑完后 Py_FinalizeEx 阶段 wait_for_thread_shutdown 死锁
    （aiosqlite daemon thread 持有 GIL 等 main 释放）。
    解决：在 sessionfinish 显式 shutdown 默认 executor，让 thread 提前退出。
    """
    import asyncio
    import gc

    # 强制 GC 收割未关闭的 aiosqlite connection
    gc.collect()

    # 显式 shutdown asyncio 默认 executor
    try:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()
    except Exception:
        pass
```

### 2.3 验证

```bash
time pytest packages/core/tests/ -q --tb=line
```

期望：进程在显示 `XX passed` 后**立即**退出，不再 hang。

---

## 3. Phase 2 — 修 fixture 全局污染

### 3.1 改写 gateway_tmp_dir / app fixture

`apps/gateway/tests/conftest.py`：
```python
@pytest_asyncio.fixture
async def app(gateway_tmp_dir: Path, monkeypatch):
    """创建测试用 FastAPI app 实例。

    Feature 083 P2：用 monkeypatch 替代 os.environ[...]=...
    避免 worker 内多 test 互相污染（xdist worker-process 隔离能解决跨 worker
    的污染，但单 worker 内顺序 test 仍需 monkeypatch 自动清理）。
    """
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(gateway_tmp_dir / "sqlite" / "test.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(gateway_tmp_dir / "artifacts"))
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")

    from octoagent.gateway.main import create_app

    application = create_app()
    yield application
    # monkeypatch 自动清理 env vars——不需要手动 pop
```

### 3.2 grep 检查其他 conftest 是否也有 os.environ 直接赋值

```bash
grep -rn "os\.environ\[.*\]\s*=" octoagent --include="conftest.py" --include="test_*.py"
```

发现的全部改成 monkeypatch.setenv。

---

## 4. Phase 3 — 启用 xdist + 全量回归

### 4.1 pyproject.toml

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = [...]
consider_namespace_packages = true
# Feature 083 P3：默认启用 xdist 并发；用户可 -n 0 禁用
addopts = ["-n", "auto"]
```

### 4.2 跑 5 次稳定性验证

```bash
for i in 1 2 3 4 5; do
  echo "=== run $i ==="
  pytest -q --tb=line 2>&1 | tail -3
done
```

期望：5 次结果都是 `2419 passed`，零 flaky。

### 4.3 如果有 flaky → 进入 P4

---

## 5. Phase 4 — 隔离 flaky + 文档 + 验收

### 5.1 标记 serial-required 测试

如果 P3 暴露了 flaky test，定位后加 mark：

```python
@pytest.mark.serial
async def test_problematic(...):
    ...
```

`conftest.py` 加 `pytest_collection_modifyitems`：
```python
def pytest_collection_modifyitems(config, items):
    """Feature 083 P4：标 @pytest.mark.serial 的测试强制串行（xdist=0）。"""
    for item in items:
        if item.get_closest_marker("serial"):
            item.add_marker(pytest.mark.xdist_group(name="serial"))
```

### 5.2 文档

`docs/codebase-architecture/testing-concurrency.md`（新增）：
- xdist auto 行为说明
- 如何 debug flaky test（boxed 模式 / -n 0）
- @pytest.mark.serial 使用场景
- aiosqlite + asyncio 已知坑

`CLAUDE.md`：
- 加 Feature 083 修复列表
- "运行测试" 章节加 pytest 命令推荐

### 5.3 全量验收

```bash
# 1. 默认并发（应该 ~25s）
time pytest -q --tb=line

# 2. 强制串行（兼容 debug）
time pytest -q --tb=line -n 0

# 3. 连续 5 次稳定
for i in 1..5; do pytest -q --tb=line | tail -3; done
```

---

## 6. Scope Lock

- 不改业务测试逻辑（仅 fixture / conftest 调整）
- 不重写 asyncio fixture 框架
- 不引入 docker / k8s 测试隔离

## 7. 总结

预计净改动 ~50 行（fixture 改写 + conftest hook + pyproject.toml）+ ~150 行（文档 + 测试 mark）。收益：本地开发回归从 93s → 25s（**3-4x**），CI 从 ~2min → ~30s。
