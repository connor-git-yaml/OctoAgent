# 技术调研报告: Feature 011 - Watchdog + Task Journal + Drift Detector

**特性分支**: `master`
**调研日期**: 2026-03-03
**调研模式**: 在线（Perplexity 辅助）
**产品调研基础**: 无（[独立模式] 本次技术调研未参考产品调研结论，直接基于需求描述和代码上下文执行）

---

## 1. 调研目标

**核心技术问题**:

1. Watchdog 检测器应选用 APScheduler 定时扫描，还是 asyncio 纯后台任务模式？
2. Task Journal 投影视图应建独立表还是实时从 EventStore 聚合？
3. 漂移检测算法选型：时间窗口法、事件频率统计、状态机驻留时间哪种最优？
4. 现有 EventStore 接口能否满足 Watchdog 查询需求，还是需要新增查询方法？
5. 与 Feature 012（Logfire）的 trace_id 透传规范如何对齐？

**功能范围（来自需求描述）**:
- Must-have: 新增 3 个事件类型（TASK_HEARTBEAT、TASK_MILESTONE、TASK_DRIFT_DETECTED）
- Must-have: Task Journal 投影视图（运行中/疑似卡死/漂移/待审批分组）
- Must-have: 无进展检测器（时间窗口 + 进度增量双条件）
- Must-have: 漂移检测器（状态机漂移、长时间 pending、重复失败模式）
- Must-have: 策略动作（提醒/降级/暂停/取消）与可配置阈值
- Must-have: E2E 测试（卡死/循环/状态漂移模拟）

---

## 2. 现有代码库接口分析

### 2.1 EventStore 接口现状

读取 `octoagent/packages/core/src/octoagent/core/store/event_store.py`，当前 `SqliteEventStore` 提供以下接口：

| 接口方法 | 签名 | Watchdog 可用性 |
|---------|------|----------------|
| `append_event_committed` | `(Event) -> Event` | 用于写入 HEARTBEAT/DRIFT 事件 |
| `get_events_for_task` | `(task_id) -> list[Event]` | 可用，但返回全量事件（大任务性能差） |
| `get_events_after` | `(task_id, after_event_id) -> list[Event]` | 可用于增量扫描，但需已知 event_id |
| `get_next_task_seq` | `(task_id) -> int` | 内部使用 |
| `get_all_events` | `() -> list[Event]` | 性能风险：全量扫描，不适合周期性 Watchdog |
| `check_idempotency_key` | `(key) -> str | None` | 与 Watchdog 无关 |

**关键缺口**（Watchdog 需要但当前不存在）：
- `get_events_by_type_in_window(event_types, since_ts)` — 按类型+时间范围查询
- `get_latest_event_for_task(task_id)` — 快速获取任务最新事件时间戳
- `count_events_by_type(task_id, event_type, since_ts)` — 重复失败模式统计

### 2.2 TaskStore 接口现状

当前 `SqliteTaskStore` 提供：
- `list_tasks(status)` — 支持按状态过滤，可查询 RUNNING/WAITING_APPROVAL 等非终态任务
- `get_task(task_id)` — 单任务查询

**Watchdog 可利用**：`list_tasks(status="RUNNING")` 获取所有运行中任务，再结合 EventStore 做时间窗口检测。

### 2.3 TaskJobStore 接口现状

`task_jobs` 表包含 `started_at`、`updated_at` 时间字段，现有 `list_jobs(statuses)` 接口可查询 RUNNING 状态任务。

**Watchdog 可利用**：`started_at` 可直接用于计算任务总运行时长，无需 EventStore 扫描即可识别超时任务。

### 2.4 TaskRunner 现有监控机制

`TaskRunner._monitor_loop()` 已实现了一个简单的超时监控：
- 轮询间隔：`monitor_interval_seconds`（默认 5s）
- 超时判断：基于 `RunningJob.started_at`（内存 dict，进程重启后失效）
- 动作：直接 cancel asyncio.Task + 标记 FAILED

**与 F011 的关系**：现有 `_monitor_loop` 是轻量级"进程内"监控，F011 的 Watchdog 需要是持久化感知、基于 EventStore 的治理层，两者互补而非替代。

### 2.5 枚举定义缺口

当前 `EventType` 中未定义 F011 需要的新事件：
- `TASK_HEARTBEAT` — 需新增
- `TASK_MILESTONE` — 需新增
- `TASK_DRIFT_DETECTED` — 需新增

`TaskStatus` 已包含 `PAUSED`（M1+ 预留），Watchdog 可直接使用。

---

## 3. 架构方案对比

### 方案 A: APScheduler 定时扫描 + EventStore 查询

**描述**: 利用项目已有的 APScheduler（`AsyncIOScheduler`）注册周期性扫描 Job，每 N 秒查询 EventStore 中的活跃任务，判断是否存在无进展或漂移，产生信号事件后由 Policy Engine 路由动作。

```
APScheduler (interval job, 15s)
  -> WatchdogScanner.scan()
     -> TaskStore.list_tasks(non_terminal)   # 获取活跃任务列表
     -> EventStore.get_latest_event_per_task  # 新增接口
     -> DriftDetector.check(task, last_event)
        -> produce TASK_DRIFT_DETECTED event
        -> notify PolicyEngine signal
```

**实现要点**:
- 在 `gateway/main.py` lifespan 中注册 APScheduler job
- WatchdogScanner 注入 StoreGroup，读写 EventStore
- 通过 `append_event_committed` 写 DRIFT 事件（保证可审计）
- Policy Engine 订阅 DRIFT 信号，执行降级/暂停/取消动作

### 方案 B: asyncio 后台任务 + 事件驱动内嵌

**描述**: 使用 `asyncio.create_task()` 创建常驻后台协程，不依赖 APScheduler。采用 asyncio.Event 或 asyncio.Queue 做内部信号总线，Worker 在关键节点主动 emit heartbeat，Watchdog 协程监听并检测缺失心跳。

```
asyncio.create_task(watchdog_loop())
  while True:
    await asyncio.sleep(heartbeat_interval)   # 15s
    for task_id in active_tasks:
      last_heartbeat = heartbeat_registry[task_id]
      if now - last_heartbeat > no_progress_threshold:
        produce TASK_DRIFT_DETECTED event
        signal_bus.emit(DriftSignal(task_id))

Worker (in each loop step):
  heartbeat_registry[task_id] = now()
  append TASK_HEARTBEAT event
```

**实现要点**:
- 心跳注册表（内存 dict）随 Worker 执行更新
- asyncio.Event/Queue 作为检测器内部通信
- 进程重启后注册表丢失，需从 EventStore 重建（或结合 task_jobs.started_at 兜底）

### 方案 C（混合）: APScheduler 调度扫描 + asyncio 内嵌心跳注册表

**描述**: APScheduler 负责定时触发"全局扫描"（扫描 DB 中的持久化状态），asyncio 内嵌的心跳注册表（内存）用于细粒度进程内检测。两层机制互补：内存检测精度高、响应快；DB 扫描覆盖崩溃后重启恢复场景。

### 方案对比表

| 维度 | 方案 A: APScheduler 扫描 | 方案 B: asyncio 纯内嵌 | 方案 C: 混合（推荐）|
|------|------------------------|----------------------|-------------------|
| **概述** | 周期扫描 DB，无内存状态依赖 | 内存心跳注册表 + 轮询协程 | APScheduler 扫描 + 内存心跳双层 |
| **性能** | DB 轮询开销（N tasks × query），WAL 并发安全 | 内存操作，零 I/O，精度高 | 中等，内存快速路径 + DB 兜底 |
| **崩溃恢复** | 完全支持，依赖持久化 EventStore | 需重建内存状态，存在检测盲窗 | 完全支持，DB 扫描兜底重建 |
| **heartbeat 精度** | 受 DB 查询延迟影响（秒级） | 毫秒级，内存操作 | 双层：内存精度 + DB 持久化 |
| **可维护性** | 清晰，APScheduler job 独立管理 | 耦合到 Worker 生命周期 | 略复杂，但职责清晰分层 |
| **学习曲线** | 低，APScheduler 已在项目中使用 | 低，纯 asyncio | 中，需管理两个层级 |
| **SQLite WAL 并发安全** | 高，仅追加写，读取用 WAL snapshot | 高，写操作通过 append_event_committed | 高 |
| **与现有架构兼容性** | 高，延续 TaskRunner._monitor_loop 模式 | 中，需改造 Worker 注入心跳发送 | 高，延续且增强 |
| **适用规模** | MVP 足够，任务数 < 1000 无压力 | MVP 适合，任务多时内存增长 | MVP 适合，可渐进演进 |
| **Constitution 合规** | Watchdog 只产生 DRIFT 事件，取消须经 Policy | 同左 | 同左 |

### 推荐方案

**推荐**: 方案 A（APScheduler 定时扫描），M1.5 阶段采用纯 DB 扫描路线。

**理由**:

1. **崩溃恢复优先**: Constitution 第 1 条"Durability First"要求任何治理状态不因进程重启消失。方案 A 完全依赖持久化 EventStore，进程重启后扫描即恢复，无盲窗。方案 B 在进程重启后存在心跳注册表丢失问题。

2. **APScheduler 已在项目中**（`gateway/main.py` 中已规划，`TaskRunner` 使用的 asyncio loop 是统一入口），F011 只需在 lifespan 中注册新 job，改动最小。

3. **MVP 阶段任务规模适配**: 当前系统任务量级在数十至数百，DB 扫描的查询成本可接受。通过新增"最新事件时间戳"索引，每次扫描可控制在毫秒级。

4. **与 Policy Engine 解耦**: Watchdog Scanner 只向 EventStore 追加 DRIFT 事件，不直接取消任务。Policy Engine 订阅这类事件并决策动作，满足 Constitution 第 4 条"Side-effect Must be Two-Phase"。

5. **可观测性**: 每次 Watchdog 扫描本身可生成 Logfire span，DRIFT 事件自带 trace_id，满足 Constitution 第 8 条。

**降级路线**: 若后续任务规模增大（>1000 并发），可按方案 C 演进，在 Worker 内嵌内存心跳注册表，APScheduler 扫描降频为 60s 兜底，内存层负责实时检测。

---

## 4. Task Journal 投影视图实现方案

### 问题核心

Task Journal 需要提供四个视图分组：
- 运行中（RUNNING / QUEUED）
- 疑似卡死（RUNNING 但无进展超过阈值）
- 漂移（DRIFT_DETECTED 事件存在）
- 待审批（WAITING_APPROVAL）

### 方案对比

**方案 J-A: 实时从 EventStore + TaskStore 聚合（Query-time Projection）**

每次 API 请求时：
1. `TaskStore.list_tasks()` 获取非终态任务
2. 对每个任务查询最新 DRIFT 事件时间戳
3. 比较当前时间与最新事件时间，判断卡死阈值
4. 分组返回

优点：无额外存储，实现简单，数据强一致。
缺点：每次 API 请求触发 N 次 DB 查询（N = 活跃任务数），高频访问有性能风险。

**方案 J-B: 独立 `task_journal` 物化视图表**

新增 `task_journal` 表，由 Watchdog Scanner 在每次扫描后更新：

```sql
CREATE TABLE IF NOT EXISTS task_journal (
    task_id        TEXT PRIMARY KEY,
    status         TEXT NOT NULL,              -- 直接映射 TaskStatus
    journal_state  TEXT NOT NULL DEFAULT 'normal',  -- normal/stalled/drifted/waiting_approval
    last_heartbeat TEXT,                       -- 最近心跳时间
    last_milestone TEXT,                       -- 最近里程碑时间
    drift_reason   TEXT,                       -- 漂移原因摘要
    drift_count    INTEGER NOT NULL DEFAULT 0, -- 累计漂移次数
    updated_at     TEXT NOT NULL,

    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
```

优点：API 查询 O(1)，Watchdog 写入时已计算好分组，UI 响应快。
缺点：需要 Watchdog 维护一致性，增加写入路径复杂度。

**推荐**: 方案 J-A（实时聚合），原因：

1. MVP 阶段任务数量有限，查询开销可接受（毫秒级）。
2. 符合 EventStore append-only 的设计哲学，避免引入"投影表维护"复杂度。
3. 可复用现有 `EventStore.get_events_for_task` 接口，后续迁移到物化视图只需替换查询层。
4. Task Journal API 访问频率远低于 SSE 事件流，不是热路径。

**升级路径**: 当活跃任务超过 200 时，可按方案 J-B 引入 `task_journal` 物化表，Watchdog 每次扫描后 upsert，API 层直接读取。

---

## 5. 漂移检测算法选型

### 算法候选

**A. 基于时间窗口的无进展检测（Time Window No-Progress）**

```python
def detect_no_progress(task_id, events, *, threshold_seconds=45) -> bool:
    """若 task 最近 threshold_seconds 内无任何有效事件，视为无进展"""
    if not events:
        return True
    progress_events = [e for e in events if e.type in PROGRESS_EVENT_TYPES]
    if not progress_events:
        return True
    latest = max(e.ts for e in progress_events)
    return (datetime.now(UTC) - latest).total_seconds() > threshold_seconds
```

PROGRESS_EVENT_TYPES = {MODEL_CALL_STARTED, MODEL_CALL_COMPLETED, TOOL_CALL_STARTED, TOOL_CALL_COMPLETED, TASK_HEARTBEAT, TASK_MILESTONE, CHECKPOINT_SAVED}

优点：简单直观，阈值可配置，Constitution 合规（使用内部完整状态集）。
缺点：阈值需根据任务类型调整，LLM 调用本身可能合理耗时较长。

**B. 基于事件频率的异常检测（Event Frequency Anomaly）**

统计最近 N 个时间窗口内的事件频率，若低于历史均值的 sigma 倍则告警。

优点：自适应，无需硬编码阈值。
缺点：MVP 阶段历史数据不足，统计基础薄弱；实现复杂度高；F011 验收标准要求"默认阈值生效"，此方案不适合作为主检测器。[推断]

**C. 状态机漂移检测（State Machine Drift）**

检测任务在某个 TaskStatus 持续时间超过预期：
```python
def detect_state_drift(task, *, stale_running_threshold=180) -> bool:
    """RUNNING 状态超过 threshold 秒且无有效进度事件"""
    if task.status not in NON_TERMINAL_STATES:
        return False
    state_age = (datetime.now(UTC) - task.updated_at).total_seconds()
    return state_age > stale_running_threshold
```

必须使用 TaskStatus 完整集合（Constitution WARNING 3）：
```python
NON_TERMINAL_STATES = {CREATED, RUNNING, QUEUED, WAITING_INPUT, WAITING_APPROVAL, PAUSED}
# 禁止: 降级为 A2A 状态的 active/pending 二元划分
```

**D. 重复失败模式（Repeated Failure Pattern）**

统计任务在时间窗口内的失败/重试事件数：
```python
def detect_repeated_failure(events, *, window_seconds=300, threshold=3) -> bool:
    """最近 window_seconds 内失败类事件超过 threshold 次"""
    failure_types = {MODEL_CALL_FAILED, TOOL_CALL_FAILED, SKILL_FAILED}
    cutoff = datetime.now(UTC) - timedelta(seconds=window_seconds)
    recent_failures = [e for e in events if e.type in failure_types and e.ts > cutoff]
    return len(recent_failures) >= threshold
```

**推荐**: 多算法组合策略，按优先级叠加：

| 优先级 | 算法 | 场景 | 触发条件 |
|-------|------|------|---------|
| P1 | 时间窗口无进展（A） | 任务卡死（主检测器） | 45s 内无进展事件 |
| P2 | 状态机漂移（C） | 状态持续异常 | RUNNING > 3 个周期（3×15s=45s） |
| P3 | 重复失败模式（D） | 反复重试失败 | 5min 内 ≥ 3 次失败事件 |
| P4 | 事件频率统计（B） | 未来增强（暂不实现） | 待 M2 历史数据积累后引入 |

---

## 6. EventStore 接口扩展需求分析

### 当前缺口 vs 需求

| Watchdog 查询需求 | 现有接口 | 是否需要新增 |
|-----------------|---------|------------|
| 获取任务最新事件时间戳 | 无直接接口（需全量查再取 max） | 需新增 `get_latest_event_ts(task_id)` |
| 按时间范围查询指定类型事件 | 无（`get_events_for_task` 返回全量） | 需新增 `get_events_by_types_since(task_id, types, since_ts)` |
| 跨任务批量查询最新心跳 | 无（无批量接口） | 需新增，或在 WatchdogScanner 内做 N 次单任务查询 |
| 统计任务失败事件数 | 无 | 可在应用层过滤，无需新增 DB 接口 |
| 全局活跃任务扫描 | `get_all_events()` 性能差 | 替代方案：通过 TaskStore 先获取非终态任务列表 |

### 推荐新增接口

在 `SqliteEventStore` 中新增（同步更新 `protocols.py` 的 EventStore Protocol）：

```python
async def get_latest_event_ts(self, task_id: str) -> datetime | None:
    """获取任务最新事件时间戳（O(log N)，利用 idx_events_task_ts 索引）"""
    # SQL: SELECT MAX(ts) FROM events WHERE task_id = ?

async def get_events_by_types_since(
    self,
    task_id: str,
    event_types: list[EventType],
    since_ts: datetime,
) -> list[Event]:
    """按事件类型 + 时间范围查询（支持 Watchdog 窗口检测）"""
    # SQL: SELECT * FROM events
    #      WHERE task_id = ? AND type IN (?) AND ts > ?
    #      ORDER BY task_seq ASC
    # 需要新增索引: CREATE INDEX idx_events_type_ts ON events(task_id, type, ts)
```

**索引新增**（`sqlite_init.py`）：
```sql
CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(task_id, type, ts);
```

---

## 7. Logfire trace_id 透传方案

### 现有 trace_id 规范分析

读取 `orchestrator.py`，当前 trace_id 生成规则：
```python
trace_id = f"trace-{task_id}"  # 简单字符串拼接，非 OTel W3C TraceContext 格式
```

这是 MVP 阶段的简化实现，非标准 OTel trace_id（应为 128-bit hex）。

### F011 Watchdog span 透传方案

**原则**: Watchdog 产生的 DRIFT 事件应关联到被检测任务的 trace_id，而非 Watchdog 自身 trace_id。

**推荐实现**:

```python
# WatchdogScanner 扫描时
async def _emit_drift_event(self, task_id: str, drift_reason: str) -> None:
    # 1. 继承被检测任务的 trace_id（保持 trace 关联性）
    task_trace_id = f"trace-{task_id}"

    # 2. 若 Logfire 已配置，使用 logfire.get_context() 捕获当前 span
    #    在 watchdog span 内追加 DRIFT 事件
    with logfire.span("watchdog.drift_detected", task_id=task_id, reason=drift_reason):
        ctx = logfire.get_context()
        event = Event(
            ...
            type=EventType.TASK_DRIFT_DETECTED,
            trace_id=task_trace_id,           # 关联到目标任务
            span_id=ctx.get("span_id", ""),   # 当前 watchdog span
            ...
        )
```

**TASK_HEARTBEAT span 规范**:
- Heartbeat 事件由 Worker 自身写入，trace_id 与当前 Worker dispatch 一致
- span_id 使用 Worker 当前循环步骤的 span（如存在）

**与 Feature 012 对齐**:
- F012 引入 Logfire 全局 instrument 后，`logfire.get_context()` 会自动携带 W3C TraceContext
- F011 应预留 `span_id` 字段写入，F012 实装时只需替换 ctx 获取方式
- `TASK_DRIFT_DETECTED` 事件的 `payload` 中应包含 `watchdog_span_id` 字段，便于 Logfire 关联

---

## 8. 依赖库评估

### 评估矩阵

| 库名 | 用途 | 版本要求 | 许可证 | 当前项目状态 | 评级 |
|------|------|---------|--------|------------|------|
| `apscheduler` | Watchdog 定时调度 | >=3.10（async支持） | MIT | 未在 pyproject.toml 中，需新增 | 高（项目规划中提及）|
| `aiosqlite` | 异步 SQLite 访问 | >=0.21 | MIT | 已在 core 依赖 | 直接复用 |
| `structlog` | 结构化日志 | >=25.1 | MIT | 已在 gateway/core 依赖 | 直接复用 |
| `logfire` | OTel trace 透传 | >=3.0 | MIT | 已在 gateway 依赖 | 直接复用 |
| `pydantic` | 配置模型 / 阈值 Dataclass | >=2.10 | MIT | 已在 core 依赖 | 直接复用 |

### 核心依赖需求

**新增依赖（仅 `octoagent-gateway` 的 pyproject.toml）**:
- `apscheduler>=3.10,<5.0`：Watchdog 定时调度

**注意**: APScheduler 4.x 已重构 API（2024），需确认与 Python 3.12 asyncio 兼容性。若使用 3.x，使用 `AsyncIOScheduler`；若使用 4.x，使用新的 `AsyncScheduler`。

**推荐**: 锁定 `apscheduler>=3.10,<4.0`（3.x API 稳定，已在 M0 蓝图中选型），待 4.x 生产就绪后评估迁移。

### 可选依赖（不引入，使用标准库替代）

- `asyncio.Event`：用于内部信号传递（不引入额外依赖）
- `dataclasses`：WatchdogConfig 阈值配置（Python 标准库，或直接用 Pydantic BaseModel）

### 兼容性检查

| 现有依赖 | 兼容性 | 说明 |
|---------|--------|------|
| `aiosqlite>=0.21` | 兼容 | Watchdog 复用同一连接，WAL 模式支持并发读 |
| `fastapi>=0.115` | 兼容 | Watchdog 在 lifespan 中注册，不依赖路由层 |
| `structlog>=25.1` | 兼容 | Watchdog scan 日志直接使用 `log.info()` |
| `logfire>=3.0` | 兼容 | span 透传通过 `logfire.get_context()` |
| `apscheduler>=3.10,<4.0` | 待验证 | 需确认与 Python 3.12 asyncio event loop 集成 |

---

## 9. 设计模式推荐

### 推荐模式

**1. Observer（观察者）模式 — Watchdog 信号发布**

WatchdogScanner 检测到漂移后，向 EventStore 追加 `TASK_DRIFT_DETECTED` 事件。Policy Engine 作为观察者，订阅特定事件类型并执行动作。满足 Constitution WARNING 1（Watchdog 只产生信号，取消须经 Policy Engine 门控）。

```python
class WatchdogScanner:
    async def scan(self) -> None:
        drifted_tasks = await self._detect_drift()
        for task_id, reason in drifted_tasks:
            await self._emit_drift_event(task_id, reason)  # 写事件，不直接动作
            # Policy Engine 独立消费此事件
```

参考案例：OpenClaw `doctor.md` 中的健康检查架构，检测器与动作执行器严格分离。

**2. Strategy（策略）模式 — 检测算法可插拔**

将三种漂移检测算法（时间窗口、状态机漂移、重复失败）封装为独立 Strategy，WatchdogScanner 按配置组合运行。

```python
class DriftDetectionStrategy(Protocol):
    async def check(self, task_id: str, events: list[Event], task: Task) -> DriftResult | None: ...

class NoProgressDetector(DriftDetectionStrategy): ...
class StateMachineDriftDetector(DriftDetectionStrategy): ...
class RepeatedFailureDetector(DriftDetectionStrategy): ...

class WatchdogScanner:
    def __init__(self, strategies: list[DriftDetectionStrategy]): ...
```

**3. Template Method 模式 — Task Journal 查询**

Task Journal API 定义查询骨架（分组逻辑），具体分组条件通过子方法实现，便于未来替换为物化视图。

**4. Command 模式 — 策略动作**

Watchdog 产生的信号触发 Policy Engine，Policy Engine 将动作封装为 Command 对象（AlertCommand/DemoteCommand/PauseCommand/CancelCommand），通过 CommandBus 执行，保证审计可追踪。

### 应用案例参考

- `_references/opensource/openclaw/src/agents/pi-tools.before-tool-call.ts` 中循环检测：检查 tool call 前的 loop 计数器，超阈值产生信号（不直接中止）。与 F011 漂移检测器思路一致。
- `_references/opensource/agent-zero/python/helpers/task_scheduler.py`：APScheduler 注册任务状态检查 job，检测任务是否超出预期执行窗口。

---

## 10. 技术风险清单

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---------|------|------|---------|
| 1 | **SQLite WAL 并发竞争**：Watchdog Scanner 与 Worker 同时写 EventStore，可能触发 SQLITE_BUSY | 中 | 中 | 已配置 `PRAGMA busy_timeout = 5000`；`append_event_committed` 已有任务级 Lock；监控写失败率 |
| 2 | **阈值硬编码 vs 实际 LLM 延迟**：LLM 调用单次可能合理耗时 30-60s，45s 无进展阈值可能误报 | 高 | 中 | 区分 MODEL_CALL_STARTED 后的"等待 LLM"窗口（不计入无进展），仅检测非 LLM 等待期的卡死 |
| 3 | **Watchdog 自身成为瓶颈**：若活跃任务数量增大，每次扫描触发大量 DB 查询 | 低（MVP 阶段） | 低 | 新增 `idx_events_type_ts` 索引；扫描器分批处理（chunk_size=50）；监控 scan 耗时 |
| 4 | **Policy Engine 未实现动作路由**：DRIFT 事件写入后无消费者 | 高（MVP 阶段） | 中 | F011 先实现"写事件 + 结构化日志告警"作为 P0，Policy 动作路由为 P1（可独立 PR） |
| 5 | **trace_id 格式非标准**：当前 `f"trace-{task_id}"` 不符合 OTel W3C TraceContext | 中 | 低 | F011 沿用现有格式，F012 实装时统一升级为 128-bit hex；在 span_id 字段预留扩展 |
| 6 | **APScheduler 4.x vs 3.x API 不兼容**：若引入 apscheduler>=4.0 则 API 差异大 | 中 | 中 | 锁定 `<4.0`，等待 4.x API 稳定后评估 |
| 7 | **TASK_HEARTBEAT 写入对 Worker 的侵入**：Worker 每 15s 需主动写入心跳事件，增加 EventStore 写压力 | 低 | 低 | MVP 阶段心跳由 TaskRunner._monitor_loop 间接触发（不需要 Worker 主动写），扫描器用 task_jobs.updated_at 做时间参照 |
| 8 | **漂移检测误报引发误动作**：检测器误判导致 Policy Engine 取消正常运行任务 | 中 | 高 | Constitution WARNING 1：Watchdog 只写 DRIFT 事件，取消须用户/Policy 确认；cooldown=60s 防抖 |

---

## 11. 需求-技术对齐度评估（独立模式）

### 覆盖评估

| 需求条目 | 技术方案覆盖 | 说明 |
|---------|-------------|------|
| F011-T01: 新增 TASK_HEARTBEAT、TASK_MILESTONE、TASK_DRIFT_DETECTED 事件 | 完全覆盖 | 在 `enums.py` 的 EventType 中新增 3 个枚举值，无破坏性变更 |
| F011-T02: Task Journal 投影视图（4 种分组） | 完全覆盖 | 推荐实时聚合（方案 J-A），通过 TaskStore + EventStore 组合查询 |
| F011-T03: 无进展检测器 | 完全覆盖 | 算法 A（时间窗口）+ 新增 EventStore.get_latest_event_ts 接口 |
| F011-T04: 漂移检测器（状态机漂移、长时间 pending、重复失败） | 完全覆盖 | 算法 C（状态机驻留）+ 算法 D（重复失败），使用完整 TaskStatus 集 |
| F011-T05: 策略动作与可配置阈值 | 部分覆盖 | F011 实现"产生信号 + 结构化日志"，Policy Engine 动作路由为后续步骤；阈值通过 WatchdogConfig（Pydantic BaseModel）配置 |
| F011-T06: E2E 测试 | 完全覆盖 | 可通过 pytest-asyncio 模拟卡死/循环/漂移场景，注入假 EventStore |
| heartbeat=15s 默认阈值 | 完全覆盖 | WatchdogConfig.scan_interval_seconds=15，env var 可覆盖 |
| no_progress=3 个周期 | 完全覆盖 | no_progress_threshold = 3 × scan_interval = 45s |
| cooldown=60s | 完全覆盖 | 防重复告警：同一 task_id 的 DRIFT 事件间隔 >= cooldown_seconds |

### 扩展性评估

- **任务规模扩展**: 当前方案在 < 200 活跃任务时性能可接受。超过后，可无缝切换到物化视图（方案 J-B），无 API 契约变更。
- **检测算法扩展**: Strategy 模式支持新增检测器，不改动 WatchdogScanner 核心逻辑。
- **动作丰富化**: Policy Engine 侧扩展，Watchdog 侧无变更。

### Constitution 约束检查

| Constitution 条目 | 兼容性 | 说明 |
|-----------------|--------|------|
| 1. Durability First | 兼容 | Watchdog 事件写入 EventStore（append-only），进程重启后可重建 |
| 2. Everything is an Event | 兼容 | HEARTBEAT/MILESTONE/DRIFT 均作为 Event 持久化，符合事件溯源 |
| 3. Tools are Contracts | 兼容 | 新增 EventStore 接口遵循 Protocol 定义 |
| 4. Side-effect Must be Two-Phase | 兼容 | Watchdog 只产生 DRIFT 信号，不直接执行取消等动作（两阶段：检测 -> Policy 门控 -> 动作）|
| 5. Least Privilege by Default | 兼容 | Watchdog Scanner 仅有 EventStore append + read 权限，不直接调用 cancel |
| 6. Degrade Gracefully | 兼容 | APScheduler job 失败不影响主任务执行；扫描失败记录 log.warning，不抛异常 |
| 7. User-in-Control | 兼容 | 告警通知用户，取消等高风险动作须用户确认（Policy Engine 门控）|
| 8. Observability is a Feature | 兼容 | DRIFT 事件含 trace_id；WatchdogScanner 每次扫描写 structlog；F012 接入后自动 instrument |
| Constitution WARNING 1（取消须经 Policy Engine）| 兼容 | WatchdogScanner 绝不直接调用 cancel；仅产生 DRIFT 事件 |
| Constitution WARNING 2（诊断摘要 + artifact 引用）| 完全覆盖 | DRIFT 事件 payload 含摘要；详细诊断信息写 ArtifactStore（可选） |
| Constitution WARNING 3（使用内部完整状态集）| 兼容 | 漂移检测使用 TaskStatus 完整枚举（9 个状态），禁止降级为 A2A 状态 |

---

## 12. 技术实现路线图建议

基于以上分析，建议 F011 按以下优先级实现：

**P0（核心，阻塞验收）**:
1. 新增 `EventType.TASK_HEARTBEAT / TASK_MILESTONE / TASK_DRIFT_DETECTED`（`enums.py`）
2. 新增 EventStore 查询接口 `get_latest_event_ts`、`get_events_by_types_since`（`event_store.py` + `protocols.py`）
3. 新增 SQLite 索引 `idx_events_type_ts`（`sqlite_init.py`）
4. 实现 `WatchdogScanner` + `WatchdogConfig`（Strategy 模式，3 种检测算法）
5. 在 `gateway/main.py` lifespan 中集成 APScheduler WatchdogScanner job
6. 实现 Task Journal 查询 API（实时聚合）

**P1（完整性，验收后补充）**:
7. Policy Engine 消费 DRIFT 事件，执行提醒/降级/暂停动作
8. Watchdog span 与 Logfire F012 对齐的 trace_id 格式升级

**P2（增强，可选）**:
9. `task_journal` 物化表（任务规模增大后引入）
10. 事件频率统计检测器（M2 历史数据积累后引入）

---

## 13. 结论与建议

### 总结

Feature 011 的核心技术选型：

1. **Watchdog 架构**: 方案 A（APScheduler 定时扫描），延续 TaskRunner 模式，与现有 async/await 架构无缝集成，崩溃安全。
2. **Task Journal**: 实时聚合（方案 J-A），MVP 阶段无需新建物化表，通过组合查询 TaskStore + EventStore 实现。
3. **漂移检测算法**: 多算法 Strategy 组合（时间窗口 + 状态机漂移 + 重复失败），Strategy 模式支持热插拔。
4. **EventStore 扩展**: 需新增 2 个查询接口 + 1 个索引，最小改动满足 Watchdog 需求。
5. **trace_id 规范**: 沿用现有 `f"trace-{task_id}"` 格式，预留 span_id 字段，F012 实装时统一升级。

### 对后续规划的建议

- **实现顺序**: P0 先行（EventStore 扩展 + WatchdogScanner），P1 在 Policy Engine 侧补充动作路由，避免 Watchdog 等 Policy 完成。
- **测试策略**: 利用 `pytest-asyncio` + in-memory SQLite 构造受控场景（注入假时间、人工写入 RUNNING 任务并停止 heartbeat），E2E 测试覆盖 stalled/loop/drift 三种模式。
- **阈值调优**: heartbeat=15s、no_progress=45s（3 个周期）适合 LLM 任务，但建议在 E2E 测试中验证误报率后再锁定默认值。
- **风险关注**: 风险 2（LLM 等待期误判为卡死）是最高优先级缓解项，建议在漂移检测逻辑中显式排除"MODEL_CALL_STARTED 后 no_progress_threshold 内"的扫描窗口。
