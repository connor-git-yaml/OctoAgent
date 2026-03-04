# 技术调研报告: Feature 013 — M1.5 E2E 集成验收

**特性分支**: `feat/013-m1.5-e2e-integration-acceptance`
**调研日期**: 2026-03-03
**调研模式**: 在线（Perplexity 搜索 + 本地代码扫描）
**产品调研基础**: [独立模式] 本次技术调研未参考产品调研结论，直接基于需求描述执行。

---

## 1. 调研目标

**核心问题**:

1. E2E 测试框架选型：如何在现有 pytest + asyncio_mode=auto 体系下组织 M1.5 集成测试？
2. Orchestrator ↔ Worker mock 替换策略：Feature 008 DispatchEnvelope/WorkerResult 如何与 009 Worker Runtime 真实对接？
3. Checkpoint 集成测试：如何在 pytest 中模拟"进程中断 → SQLite checkpoint 持久化 → ResumeEngine 恢复"场景？
4. Watchdog 集成测试：APScheduler 3.x job 在测试环境中如何以可控方式触发？
5. Logfire/OTel trace 验证：如何在测试中断言 span 存在性而不依赖真实 Logfire 后端？

**功能范围（来自需求描述）**:

- F013-T01：去除 008~012 各层 mock，接入真实依赖链路（消息 -> Orchestrator -> Worker -> 回传）
- F013-T02：新增 4 条 E2E 场景测试（正常流、断点恢复、watchdog 触发、全链路 trace）
- F013-T03：M1 既有 002-007 链路回归验证
- F013-T04：M1.5 Verification Report + 技术风险清单产出

---

## 2. 架构方案对比

### 方案对比表

| 维度 | 方案 A: 分层隔离 + 真实 App Fixture | 方案 B: 微内核单元组合 | 方案 C: 全量 Docker E2E |
|------|----------------------------------|----------------------|------------------------|
| 概述 | 复用现有 `integration_app` fixture 模式，通过 `httpx.AsyncClient + ASGITransport` 驱动真实 FastAPI app，各组件不 mock，SQLite tmp_path 提供隔离 | 不走 HTTP 层，直接实例化 `OrchestratorService` + `WorkerRuntime` + `ResumeEngine`，在纯 Python 层组合 | 启动真实 Docker 容器运行 gateway，使用 httpx 请求外部端口 |
| 性能 | 快（in-process，无网络开销）| 最快（无 FastAPI overhead）| 慢（容器启动开销 30~60s）|
| 可维护性 | 高（与现有测试一致，fixture 已有范例）| 中（需手动连接各服务依赖图，不覆盖路由层）| 低（CI 需要 Docker daemon，调试困难）|
| 学习曲线 | 低（现有 conftest.py 已有模板）| 中（需理解各服务构造参数）| 高（Dockerfile + compose 调试）|
| 社区支持 | 高（FastAPI 官方推荐的 ASGITransport 模式）| 高（纯 pytest-asyncio 标准）| 中（需额外 CI 配置）|
| 适用规模 | 适合 M1.5 验收阶段（4 条场景 + 回归）| 适合纯单元集成，不适合路由层验收 | 适合 M3+ 生产 smoke test |
| 与现有项目兼容性 | 完全兼容（asyncio_mode=auto，tmp_path，httpx 已在依赖中）| 完全兼容 | 需引入新工具链 |

### 推荐方案

**推荐**: 方案 A（分层隔离 + 真实 App Fixture）

**理由**:

1. **与现有代码完全对齐**：项目已在 `tests/integration/conftest.py` 和 `apps/gateway/tests/conftest.py` 中沉淀了两套基于 `httpx.ASGITransport` 的 fixture 范式，F013 可直接扩展而非重新设计。
2. **路由层覆盖完整**：F013-T01 要求"替换 mock、接入真实依赖"，方案 A 通过 `create_app()` 启动完整 lifespan，自动初始化 WatchdogScanner、TaskRunner、PolicyEngine，无需手动穿针引线。
3. **隔离性充分**：SQLite + `tmp_path` 每测试独立数据库，不存在跨测试状态污染；`LOGFIRE_SEND_TO_LOGFIRE=false` 已在 fixture 中设置。
4. **方案 B 的核心局限**：绕过路由层意味着 F013-T02 的"消息 → Orchestrator → Worker → 回传"路径无法覆盖 `message.router` 的请求解析、幂等键校验、SSE 事件推送等关键路径。

---

## 3. 依赖库评估

### 评估矩阵

| 库名 | 用途 | 版本约束 | 已在项目中 | 许可证 | 评级 |
|------|------|---------|-----------|--------|------|
| `pytest-asyncio` | async 测试驱动（asyncio_mode=auto）| >=0.24 | 是 | MIT | 推荐 |
| `httpx` | AsyncClient + ASGITransport | >=0.27 | 是 | BSD | 推荐 |
| `logfire[testing]` | `capfire` fixture，in-memory span 捕获 | >=3.0（已引入 logfire）| 是（需确认 testing extra）| MIT | 推荐 |
| `apscheduler` | WatchdogScanner APScheduler 3.x | >=3.10,<4.0 | 是 | MIT | 推荐 |
| `pytest-cov` | 覆盖率统计 | >=6.0 | 是 | MIT | 推荐 |
| `freezegun` / `time-machine` | 时间冻结（watchdog 阈值测试）| >=1.0 | 否 | Apache-2.0 / MIT | 可选 |
| `pytest-xdist` | 并行测试（大型回归套件加速）| >=3.0 | 否 | MIT | 可选 |

### 推荐依赖集

**核心依赖（已存在，无需新增）**:
- `pytest-asyncio>=0.24`：asyncio_mode=auto 已在 pyproject.toml 配置
- `httpx>=0.27`：ASGITransport 方案核心
- `logfire>=3.0`：已引入，`logfire.testing` 模块提供 `capfire` fixture

**建议新增**:
- `time-machine` 或 `freezegun`：Watchdog 阈值测试中需要控制 `datetime.now(UTC)` 返回值。当前 `WatchdogConfig` 默认 `no_progress_threshold_seconds = 45秒`（3 cycles × 15s），测试中若不冻结时间，需等待真实时间或注入极短超时值。`time-machine`（MIT）是更现代的选择，底层使用 `libfaketime`，对 `asyncio` 兼容更好。

**可选依赖**:
- `pytest-xdist`：若 F013 测试套件扩展后超过 30 秒，可并行化加速 CI

### 与现有项目的兼容性

| 现有依赖 | 兼容性 | 说明 |
|---------|--------|------|
| `apscheduler>=3.10,<4.0` | 兼容 | 测试中直接实例化 `WatchdogScanner.scan()` 异步方法，绕过 APScheduler 调度层；或以极短间隔重启调度器。APScheduler 3.x 与 asyncio_mode=auto 兼容 |
| `logfire>=3.0` | 兼容 | `LOGFIRE_SEND_TO_LOGFIRE=false` 已在 fixture 中设置；`logfire.testing.capfire` 提供内存 exporter，无需额外配置 |
| `aiosqlite>=0.21` | 兼容 | SQLite WAL 模式与 tmp_path 方案完全兼容；进程重启模拟通过 `conn.close()` + 新 `create_store_group()` 实现（已在 SC-2 测试中验证） |
| `pytest-asyncio>=0.24` | 兼容 | asyncio_mode=auto 已启用，所有 async 测试和 fixture 自动识别 |

---

## 4. 设计模式推荐

### 4.1 分层 Fixture 模式（Layered Fixture Pattern）

**适用场景**: F013-T01 mock 替换和 F013-T03 回归测试

现有项目已有两层 fixture：
- **层一**（`apps/gateway/tests/conftest.py`）：`gateway_tmp_dir` + `app` + `client`，适合 gateway 单包测试
- **层二**（`tests/integration/conftest.py`）：`integration_app` + `client`，适合跨包集成

F013 应在 `tests/integration/` 下新建 `test_f013_*.py`，复用 `integration_app` fixture，根据场景需要扩展 `conftest.py` 中的 fixture（例如预置 checkpoint 数据）。

```python
# tests/integration/conftest.py 扩展示例
@pytest_asyncio.fixture
async def app_with_checkpoint(integration_app, tmp_path):
    """预置 checkpoint 数据，用于 F013-T02 断点恢复场景"""
    store_group = integration_app.state.store_group
    # 预置 task 和 checkpoint_snapshot...
    yield integration_app
```

### 4.2 直接调用模式（Direct Invocation Pattern）

**适用场景**: Watchdog 集成测试（F013-T02 "无进展 → watchdog 触发"）

不等待 APScheduler 周期触发，直接调用 `watchdog_scanner.scan()`：

```python
async def test_watchdog_detects_stalled_task(integration_app, client):
    # 1. 创建并推进到 RUNNING 状态的任务（不完成它）
    # 2. 设置极短的 no_progress_threshold（通过 WatchdogConfig 覆盖）
    # 3. 直接调用 scanner.scan()
    scanner: WatchdogScanner = integration_app.state.watchdog_scanner
    await scanner.scan()
    # 4. 断言 TASK_DRIFT_DETECTED 事件写入 EventStore
```

**关键点**：`WatchdogConfig` 支持通过环境变量覆盖，测试前设置 `WATCHDOG_NO_PROGRESS_CYCLES=1` + `WATCHDOG_SCAN_INTERVAL_SECONDS=1`，使 `no_progress_threshold_seconds` 降为 1 秒，无需冻结时间。

### 4.3 进程重启模拟模式（Restart Simulation Pattern）

**适用场景**: F013-T02 "中断 → checkpoint 恢复"场景

现有 `test_sc2_durability.py` 已验证了此模式：

```python
async def test_checkpoint_resume_flow(tmp_path):
    db_path = str(tmp_path / "resume.db")
    # 阶段 1: 创建任务，写入 checkpoint
    sg1 = await create_store_group(db_path, ...)
    await sg1.checkpoint_store.save_checkpoint(checkpoint_data)
    await sg1.conn.close()  # 模拟进程退出

    # 阶段 2: 重启，运行 ResumeEngine
    sg2 = await create_store_group(db_path, ...)
    resume_engine = ResumeEngine(sg2)
    result = await resume_engine.try_resume(task_id)
    assert result.ok is True
    assert result.resumed_from_node == "model_call_started"
```

**注意**：ResumeEngine 有异步锁防止并发恢复，测试中每次使用新 `task_id` 避免锁冲突（或在 fixture teardown 中清理 `_resume_locks`）。

### 4.4 Logfire Span 断言模式（Span Assertion Pattern）

**适用场景**: F013-T02 "全链路 trace 可见"场景

`logfire.testing` 提供 `capfire` fixture（pytest plugin 自动注册），内置 `TestExporter` 捕获 in-memory span：

```python
from logfire.testing import capfire  # noqa: F401 - pytest fixture 自动发现

async def test_full_trace_visible(client, capfire):
    resp = await client.post("/api/message", json={"text": "trace test", "idempotency_key": "trace-001"})
    task_id = resp.json()["task_id"]
    await asyncio.sleep(0.5)

    exported = capfire.exporter.exported_spans_as_dict()
    span_names = [s["name"] for s in exported]
    # 断言关键 span 存在
    assert any("task" in name.lower() for name in span_names)
```

**降级方案**：如果 `logfire.testing` 在当前 logfire>=3.0 版本中有 API 变化，可使用 `opentelemetry.sdk.trace.export.InMemorySpanExporter` 直接挂载：

```python
from opentelemetry.sdk.trace.export import InMemorySpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

@pytest.fixture
def span_exporter():
    exporter = InMemorySpanExporter()
    # 通过 logfire.configure(additional_span_processor=...) 挂载
    yield exporter
    exporter.clear()
```

---

## 5. 技术风险清单

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---------|------|------|---------|
| 1 | **Watchdog scan 时序问题**：`scan()` 直接调用后，`TASK_DRIFT_DETECTED` 事件写入是 async 的，但 cooldown 检查可能因跨测试状态污染导致漏检 | 中 | 高 | 使用独立 `CooldownRegistry()` 实例初始化 WatchdogScanner，或在 fixture 中覆盖 `WATCHDOG_COOLDOWN_SECONDS=0` 禁用 cooldown |
| 2 | **ResumeEngine 全局锁泄漏**：`_resume_locks` 是类变量（`dict[str, asyncio.Lock]`），并发测试或测试异常退出可能留下锁 | 中 | 中 | fixture teardown 中显式清空 `ResumeEngine._resume_locks`；或每次测试使用独立 `task_id`（ULID 天然唯一） |
| 3 | **APScheduler lifespan 冲突**：`integration_app` fixture 不走 lifespan（直接赋值 app.state），导致 WatchdogScheduler 未启动；而完整 lifespan 启动会在 fixture scope 内持续扫描 | 高 | 中 | F013 Watchdog 场景采用"直接调用 `scanner.scan()`"模式，不依赖 APScheduler 调度；非 Watchdog 场景继续使用不含 lifespan 的轻量 fixture |
| 4 | **Logfire capfire API 变更**：logfire 3.x 的 `testing` 模块 API 未在 pyproject.toml 中声明 `logfire[testing]` extra，可能导致 `capfire` fixture 不可用 | 中 | 中 | 检查 `logfire` 3.x 是否需要 `pip install logfire[testing]`；若 API 不稳定，降级为 OTel `InMemorySpanExporter` 方案 |
| 5 | **asyncio.sleep() 导致测试不稳定**：现有 SC-1 测试使用 `await asyncio.sleep(0.5)` 等待 LLM echo 处理，在 CI 慢机器上可能不足 | 中 | 中 | 使用轮询 + timeout 代替固定 sleep：`async for _ in poll_until(lambda: get_task_status() == "SUCCEEDED", timeout=5): ...`；或使用 `LLMService` echo 模式（同步完成，sleep 可缩短至 0.1s） |
| 6 | **M1 回归范围模糊**：F013-T03 要求"不回归 002-007"，但 002-007 测试分散在多个 testpath（`packages/provider/tests/`、`packages/tooling/tests/` 等），需明确 CI 全量跑 `pytest` 时的覆盖范围 | 低 | 高 | 在 `pyproject.toml` 的 `testpaths` 中确认已包含所有包；F013 验收阶段执行 `uv run pytest` 全量运行并输出 coverage report |
| 7 | **Checkpoint Store 接口不完整**：`ResumeEngine` 依赖 `checkpoint_store.get_latest_success(task_id)`，需确认 `CheckpointStore` 是否已在 `StoreGroup` 中注册并持久化到 SQLite | 高 | 高 | 扫描 `octoagent/packages/core/src/octoagent/core/store/` 确认 `checkpoint_store` 实现；如未实现，F013-T02 断点恢复场景需要先完成该 store 的最小实现 |
| 8 | **OrchestratorService 全量链路超时**：F013-T01 接入真实 `OrchestratorService.dispatch()`，echo 模式下整条链路（ORCH_DECISION + WORKER_DISPATCHED + InlineBackend + WORKER_RETURNED + STATE_TRANSITION）可能因多次 SQLite 写入积累延迟 | 低 | 低 | 设置 `WorkerRuntimeConfig(max_steps=1, max_execution_timeout_seconds=5.0)`；测试中使用 `LLMService` echo 模式 |

---

## 6. 需求-技术对齐度评估

[独立模式] 以需求描述中的功能范围替代产品 MVP 评估。

### 覆盖评估

| 需求功能 | 技术方案覆盖 | 说明 |
|---------|-------------|------|
| F013-T01: mock 替换，接入真实依赖链路 | 完全覆盖 | 方案 A（真实 App Fixture）通过 `create_app()` lifespan 自动初始化所有真实组件；OrchestratorService 已通过 `LLMWorkerAdapter` → `WorkerRuntime` → `InlineRuntimeBackend` 连接 |
| F013-T02: 消息 → Orchestrator → Worker → 回传 | 完全覆盖 | `POST /api/message` → `TaskRunner` → `OrchestratorService.dispatch()` 链路已在现有 SC-1 测试中覆盖框架；F013 在此基础上断言 ORCH_DECISION + WORKER_DISPATCHED + WORKER_RETURNED 事件 |
| F013-T02: 中断 → checkpoint 恢复 | 部分覆盖（存在风险 #7） | `ResumeEngine` 实现已完整（Feature 010），但 `checkpoint_store.get_latest_success()` 实现需验证；进程重启模拟模式已验证（SC-2） |
| F013-T02: 无进展 → watchdog 触发 | 完全覆盖 | `WatchdogScanner.scan()` 直接调用模式 + 极短 `no_progress_threshold` 设置；`NoProgressDetector` 逻辑已实现 |
| F013-T02: 全链路 trace 可见 | 部分覆盖（存在风险 #4） | `logfire.testing.capfire` 方案可行；需确认 logfire 3.x 的 `testing` extra 可用性 |
| F013-T03: M1 回归（002-007）| 完全覆盖 | `pyproject.toml` 的 `testpaths` 已包含所有包目录；全量 `uv run pytest` 可执行回归 |
| F013-T04: Verification Report + 风险清单 | 完全覆盖 | 技术方案为报告产出提供了结构化的测试场景和验收标准 |

### Constitution 约束检查

| 约束 | 兼容性 | 说明 |
|------|--------|------|
| Durability First | 兼容 | checkpoint 集成测试通过 `conn.close()` + 重新打开验证 SQLite WAL 持久化 |
| Everything is an Event | 兼容 | E2E 测试通过断言 EventStore 中的事件类型和数量验证此约束 |
| Tools are Contracts | 兼容 | 测试不引入新工具，仅断言已有接口行为 |
| Side-effect Must be Two-Phase | 兼容 | Orchestrator 的 gate 决策通过单元测试覆盖；E2E 测试中 LOW risk 任务绕过 gate |
| Observability is a Feature | 兼容 | F013-T02 全链路 trace 测试直接验证此约束 |
| Degrade Gracefully | 兼容 | 测试环境 `LOGFIRE_SEND_TO_LOGFIRE=false` 验证了 logfire 降级不影响主流程 |

---

## 7. 关键技术决策

### 7.1 Watchdog 测试策略：直接调用 vs 调度器触发

**决策**：优先使用直接调用 `scanner.scan()` + 环境变量覆盖阈值，不通过 APScheduler 触发。

**理由**：
- APScheduler 在测试环境中的调度精度受事件循环干扰，`interval` trigger 在 `asyncio_mode=auto` 下可能导致测试执行顺序不确定
- `WatchdogScanner.scan()` 是纯异步函数，直接 `await` 调用等同于真实调度执行
- 阈值通过 `os.environ` 覆盖（`WATCHDOG_NO_PROGRESS_CYCLES=1`，`WATCHDOG_SCAN_INTERVAL_SECONDS=1`）更简洁，避免引入 `freezegun` 等外部库

### 7.2 Logfire Trace 验证策略：capfire vs InMemorySpanExporter

**决策**：优先尝试 `logfire.testing.capfire`；若不可用，降级为 OTel `InMemorySpanExporter`。

**理由**：
- `capfire` 是 Logfire 官方推荐的测试工具，与 Pydantic AI 集成更紧密
- 降级方案无需修改生产代码，只需在 fixture 中注入自定义 SpanProcessor

### 7.3 E2E 场景测试文件组织

建议文件结构：

```
tests/integration/
  conftest.py              # 现有（扩展 checkpoint 预置 fixture）
  test_sc1_e2e.py          # 现有：基础 E2E 链路
  test_sc2_durability.py   # 现有：持久化
  test_f013_e2e_full.py    # 新增：F013-T01 真实链路验证（ORCH_DECISION + WORKER_RETURNED）
  test_f013_checkpoint.py  # 新增：F013-T02 断点恢复场景
  test_f013_watchdog.py    # 新增：F013-T02 watchdog 触发场景
  test_f013_trace.py       # 新增：F013-T02 全链路 trace 场景
  test_f013_regression.py  # 新增：F013-T03 M1 回归执行入口（或直接通过全量 pytest 覆盖）
```

---

## 8. 结论与建议

### 总结

F013 的技术挑战集中在三个层面：

1. **集成层（最低风险）**：E2E 测试框架选型已有成熟范式（方案 A），`httpx.ASGITransport + asyncio_mode=auto` 无需引入新库，直接扩展现有 `tests/integration/` 目录即可。

2. **时序控制层（中等风险）**：Watchdog 和 Checkpoint 场景需要受控的时间/状态控制。推荐"直接调用 + 环境变量覆盖阈值"代替"等待真实调度"，避免 CI 时序不稳定。

3. **可观测层（待确认）**：`logfire.testing.capfire` 的可用性需要在 F013 编码阶段早期验证（风险 #4），若不可用需立即切换到 OTel InMemorySpanExporter 方案。

**最高优先级风险**：风险 #7（CheckpointStore 实现完整性）和风险 #3（APScheduler lifespan 冲突）应在 F013 编码开始前优先核查，否则 F013-T02 的两条核心场景将无法实施。

### 对规划阶段的建议

- **前置核查 CheckpointStore 实现**：在 `octoagent/packages/core/src/octoagent/core/store/` 中确认 `checkpoint_store` 是否已实现 `get_latest_success()` 方法；如未实现，需在 F013 任务中补充该 store 的最小实现
- **E2E fixture 设计先行**：建议在 F013 首日先完成 `conftest.py` 扩展（含 checkpoint 预置 fixture 和 logfire capfire 可用性验证），确保后续各场景测试的基础设施稳固
- **回归测试应作为 CI 门禁**：F013-T03 不应仅作为人工验收，应纳入 CI 全量 `uv run pytest --cov` 命令，覆盖率报告作为 Verification Report 的一部分
- **WatchdogScanner 环境变量覆盖优先**：在 F013-T02 watchdog 测试中，通过 `os.environ` 覆盖 `WATCHDOG_NO_PROGRESS_CYCLES=1` + `WATCHDOG_SCAN_INTERVAL_SECONDS=1`（阈值降为 1 秒），比引入 `time-machine` 库更轻量；如需精确时间控制再引入 `time-machine`
