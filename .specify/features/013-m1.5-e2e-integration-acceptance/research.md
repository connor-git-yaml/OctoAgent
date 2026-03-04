# 技术决策研究: Feature 013 — M1.5 E2E 集成验收

**Branch**: `feat/013-m1.5-e2e-integration-acceptance`
**Date**: 2026-03-04
**输入来源**: `research/tech-research.md`（tech-only 调研模式）、现有代码扫描

---

## 决策 1: E2E 测试框架选型

**Decision**: 采用方案 A（分层隔离 + 真实 App Fixture），扩展现有 `tests/integration/` 目录，新增 4 个测试文件。

**Rationale**:

- 现有 `tests/integration/conftest.py` 已沉淀 `integration_app` fixture（httpx.ASGITransport + asyncio_mode=auto），F013 可直接扩展，零学习成本。
- `create_app()` lifespan 自动初始化所有真实组件（WatchdogScanner、TaskRunner、PolicyEngine、SSEHub），无需手动装配依赖图。
- SQLite + tmp_path 方案每测试独立数据库，天然隔离跨测试状态污染。
- 绕过路由层（方案 B）无法覆盖 message.router 请求解析、幂等键校验、SSE 事件推送等关键路径。
- 容器方案（方案 C）CI 启动开销 30~60 秒，且需要 Docker daemon，调试困难，归 M3+ 烟雾测试。

**Alternatives Rejected**:
- 方案 B（微内核单元组合）：不覆盖路由层，FR-002 消息路由全链路验收无法完成。
- 方案 C（全量 Docker E2E）：超出 F013 范围边界（spec §OUT），运维成本过高。

---

## 决策 2: Watchdog 场景测试触发策略

**Decision**: 直接调用 `scanner.scan()` + 环境变量覆盖阈值（`WATCHDOG_NO_PROGRESS_CYCLES=1`，`WATCHDOG_SCAN_INTERVAL_SECONDS=1`），不通过 APScheduler 调度触发，不引入 `time-machine` 库。

**Rationale**:

- `WatchdogScanner.scan()` 是纯异步方法，直接 `await` 调用等价于真实调度执行，验证逻辑完整。
- APScheduler 3.x 的 interval trigger 在 `asyncio_mode=auto` 下调度精度受事件循环影响，测试执行顺序不确定。
- `WatchdogConfig.from_env()` 已实现 `WATCHDOG_{KEY}` 环境变量覆盖机制（config.py 已验证），无需额外库即可将 `no_progress_threshold_seconds` 降为 1 秒（1 cycle × 1 second）。
- 场景 C 新增独立的 `watchdog_integration_app` fixture：走完整 lifespan 初始化（含 WatchdogScanner.startup() 重建 cooldown）但不启动 APScheduler 后台调度，测试结束后清理 CooldownRegistry 状态。

**Alternatives Rejected**:
- APScheduler 触发：时序不稳定，CI 慢机器可能 miss trigger 窗口。
- `time-machine`/`freezegun`：引入新依赖（spec §OUT 要求不引入不必要依赖），而且 `WatchdogConfig` 通过环境变量覆盖 cycles 和 interval 已足够精确控制阈值，无需冻结系统时钟。

---

## 决策 3: Logfire 全链路追踪验证策略

**Decision**: 优先使用 `logfire.testing.capfire` fixture；若运行时 API 不可用，降级为 OTel `InMemorySpanExporter`。

**Rationale**:

- 代码验证（2026-03-03）确认 `logfire.testing` 模块在当前运行时环境中已可用（tech-research §7.2 + spec 歧义解决 §4）。
- `capfire` 是 Logfire 官方推荐测试工具，与 Pydantic AI 集成更紧密，通过 `capfire.exporter.exported_spans_as_dict()` 直接断言 span 字典，无需额外配置。
- 降级方案（OTel InMemorySpanExporter）无需修改生产代码，只需在 fixture 中注入自定义 SpanProcessor，风险可控。
- 场景 D 测试应设置 `LOGFIRE_SEND_TO_LOGFIRE=false`（现有 fixture 已配置），确保追踪数据捕获在内存中进行，不依赖外部 Logfire 后端。

**Alternatives Rejected**:
- 依赖真实 Logfire 后端：测试需网络，CI 环境不稳定，且 spec SC-004 明确要求可观测后端不可用时不影响主流程。

---

## 决策 4: 进程重启模拟策略（场景 B）

**Decision**: 使用 `conn.close()` + 新建 `StoreGroup` 的两阶段模拟方案。不引入进程级重启。

**Rationale**:

- SQLite WAL 模式保证 `conn.close()` 后数据已持久化到磁盘（Constitution 原则 1 验证）。
- 新建 `StoreGroup`（指向同一 `tmp_path` 数据库文件）等价于进程重启后重新打开连接，`ResumeEngine.try_resume()` 可从 SQLite 读取 `CheckpointSnapshot`。
- `SqliteCheckpointStore.get_latest_success()` 实现已确认完整（代码扫描 2026-03-03），风险 #7 已消除，场景 B 可直接进入编写。
- `ResumeEngine` 使用类级别 `_resume_locks: dict[str, asyncio.Lock]`，测试需使用唯一 ULID task_id 避免锁冲突，fixture teardown 中清理残留锁。

**Alternatives Rejected**:
- 真实进程重启（subprocess）：测试隔离性极差，跨进程断言困难，不适合单元集成测试层。
- 仅内存模拟：无法验证 SQLite WAL 持久化路径，Constitution 原则 1 验证缺失。

---

## 决策 5: Watchdog 测试环境隔离策略

**Decision**: 新增 `watchdog_integration_app` fixture，独立于现有 `integration_app`，走完整 lifespan 初始化但显式跳过 APScheduler 调度启动。

**Rationale**:

- 现有 `integration_app` 不走完整 lifespan（直接赋值 app.state），导致 WatchdogScanner 未初始化（tech-research 风险 #3）。
- 完整 lifespan 会启动 APScheduler 后台持续扫描（scan_interval_seconds=15），与手动触发测试产生竞争。
- 独立 fixture 的设计：(a) 初始化 WatchdogScanner 和 CooldownRegistry；(b) 调用 `scanner.startup()` 重建 cooldown 注册表；(c) 不启动 APScheduler；(d) 测试结束清理 `CooldownRegistry._last_drift_ts`。
- 正常流程（场景 A）和全链路追踪场景（场景 D）继续使用轻量 `integration_app` fixture。

**Alternatives Rejected**:
- 使用完整 lifespan（含 APScheduler）：后台调度与测试手动触发竞争，断言结果不确定。
- 复用 `integration_app` 手动注入 scanner：绕过 `startup()` 调用，cooldown 注册表重建逻辑未验证，Constitution 原则 8 可观测性验证不完整。

---

## 决策 6: M1 全量回归验证策略

**Decision**: 执行 `uv run pytest octoagent/ --cov --cov-report=term-missing` 全量覆盖，Feature 002~007 测试通过率 100% 作为 SC-005 门禁条件。

**Rationale**:

- 现有 `pyproject.toml` 的 `testpaths` 已包含所有包目录，`uv run pytest` 可覆盖全量测试（tech-research §5 风险 #6 已缓解）。
- 回归测试应作为 CI 门禁而非人工验收，覆盖率报告纳入 M1.5 验收报告（FR-008）。
- Feature 002~007 范围内的测试文件：`test_f002_*.py`（4 个）、`test_f007_e2e_integration.py`、`tests/unit/policy/`（5 个）、`tests/contract/`（1 个）。

**Alternatives Rejected**:
- 仅人工验收：无法在 CI 中强制执行，M2 准入缺乏自动化依据。
- 子集回归：遗漏 packages 层（provider、tooling、core）的单元测试，回归覆盖不完整。

---

## 决策 7: 测试文件组织

**Decision**: 在 `octoagent/tests/integration/` 下新增 4 个文件，命名以 `test_f013_` 为前缀，每个文件对应一个验收场景。

**Rationale**:

- 与现有命名规范一致（`test_f008_*.py`、`test_f009_*.py`、`test_f010_*.py`）。
- 每个文件独立可运行（`pytest tests/integration/test_f013_e2e_full.py -v`），便于单场景调试。
- `test_f013_regression.py` 可作为回归入口的文档化标记，实际回归通过全量 `uv run pytest` 执行。

**Alternatives Rejected**:
- 单文件组织（`test_f013_all.py`）：场景之间 fixture 污染风险更高，单场景调试不便。
- 在现有 `test_sc*.py` 文件中追加：破坏现有测试历史记录，F013 职责不清晰。

---

## 风险消除记录

| 风险 | 来源 | 消除状态 | 消除方式 |
|------|------|---------|---------|
| #4 logfire.testing.capfire 不可用 | tech-research §5 | **已消除** | 运行时验证确认可用（spec 歧义 §4） |
| #7 CheckpointStore.get_latest_success() 未实现 | tech-research §5 | **已消除** | 代码扫描确认 `SqliteCheckpointStore` 已实现，已注册到 `StoreGroup`（spec 歧义 §1） |

| 风险 | 来源 | 当前状态 | 缓解策略 |
|------|------|---------|---------|
| #1 Watchdog cooldown 跨测试污染 | tech-research §5 | **已缓解** | 独立 `watchdog_integration_app` fixture + teardown 清理 `CooldownRegistry._last_drift_ts` |
| #2 ResumeEngine _resume_locks 泄漏 | tech-research §5 | **已缓解** | 每测试使用 ULID 唯一 task_id + fixture teardown 清理 `ResumeEngine._resume_locks` |
| #3 APScheduler lifespan 冲突 | tech-research §5 | **已缓解** | watchdog 场景专用 fixture 不启动 APScheduler |
| #5 asyncio.sleep 时序不稳定 | tech-research §5 | **待处理** | F013 编码时将 `sleep(0.5)` 替换为 `poll_until(condition, timeout=5)` 轮询等待 |
| #6 M1 回归范围模糊 | tech-research §5 | **已缓解** | 确认 pyproject.toml testpaths 覆盖所有包目录，全量 `uv run pytest` 执行 |
| #8 全链路超时 | tech-research §5 | **低风险** | Echo 模式（LLMService 默认）同步完成，链路延迟可控；必要时设置 `WorkerRuntimeConfig(max_execution_timeout_seconds=5.0)` |
