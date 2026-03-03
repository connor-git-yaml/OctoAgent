# 技术决策记录: Feature 011 — Watchdog + Task Journal + Drift Detector

**特性目录**: `.specify/features/011-watchdog-task-journal`
**创建日期**: 2026-03-03
**依据**: `research/tech-research.md`（调研报告全文）
**状态**: Final

---

## 概述

本文档记录 Feature 011 实现过程中每个关键技术决策的结论、理由和被否决的替代方案。
所有决策以调研报告结论为权威基础，与 `spec.md` 功能范围保持一致。

---

## 决策 1: Watchdog 调度架构

### Decision

采用 **APScheduler 定时扫描**（方案 A）：在 `gateway/main.py` lifespan 中注册 `AsyncIOScheduler` interval job，每 N 秒触发 `WatchdogScanner.scan()`，扫描依赖完全持久化的 EventStore 和 TaskStore，不维护进程内内存状态。

### Rationale

1. **Durability First 合规**：Constitution 原则 1 要求进程重启后治理状态不消失。APScheduler + EventStore 方案完全依赖持久化存储，扫描器在进程重启后随 lifespan 自动恢复，从 EventStore 重建检测基准，无盲窗。
2. **最小改动原则**：APScheduler (`AsyncIOScheduler`) 已在 blueprint 技术栈规划中明确选型，gateway 已有 lifespan 管理，注册新 job 只需在 `lifespan()` 末尾添加约 10 行代码。
3. **MVP 规模适配**：当前系统任务量级为数十至数百，每次扫描 DB 查询成本可控。通过新增 `idx_events_type_ts` 索引，单次扫描耗时可控制在 10ms 以内。
4. **Constitution 第 4 条合规**：Watchdog 只向 EventStore 追加 DRIFT 信号事件，取消/暂停等动作由 Policy Engine 门控，实现两阶段分离。
5. **与现有 TaskRunner 互补**：`TaskRunner._monitor_loop()` 是进程内轻量超时监控（5s 间隔，内存状态），F011 Watchdog 是持久化感知治理层（15s 间隔，EventStore 状态），两者角色互补，不冲突。

### Alternatives Rejected

- **方案 B（asyncio 纯内嵌 + 内存心跳注册表）**：进程重启后心跳注册表丢失，存在检测盲窗，违反 Constitution 原则 1。虽然精度更高（毫秒级），但 MVP 阶段 15s 扫描间隔已足够。
- **方案 C（混合双层）**：开发复杂度高，需同时管理 APScheduler job 和 asyncio 内嵌心跳注册表，职责边界模糊。MVP 阶段可见性价值不高，留作 M2 任务规模扩大后演进路线。

---

## 决策 2: Task Journal 投影视图实现

### Decision

采用 **实时聚合（Query-time Projection）**：每次 API 请求时组合查询 `TaskStore.list_tasks_by_statuses()` 和 `EventStore.get_latest_event_ts()`，在应用层完成四分组逻辑（running/stalled/drifted/waiting_approval）。不建立独立 `task_journal` 物化表。

### Rationale

1. **MVP 规模可接受**：活跃任务 <= 200 时，每次 Journal 请求触发约 200 次 DB 查询，利用 `idx_events_type_ts` 索引后总耗时 < 500ms，满足 SC-007（< 2 秒响应）。
2. **符合 EventStore append-only 哲学**：避免引入物化视图维护的写入一致性复杂度，Watchdog 扫描路径不需要额外更新 `task_journal` 表。
3. **降低实现耦合**：Task Journal API 逻辑与 Watchdog 扫描逻辑完全解耦，两者均独立查询 EventStore，不共享内存中间状态。
4. **明确升级路径**：当活跃任务超过 200 时，迁移至 `task_journal` 物化表（方案 J-B）只需替换 `TaskJournalService` 的查询层，API 契约无需变更。

### Alternatives Rejected

- **方案 J-B（独立 `task_journal` 物化表）**：Watchdog 扫描后需 upsert 物化表，引入"扫描事务 + 物化更新"的复合事务边界，增加崩溃恢复复杂度。MVP 阶段过早优化。

---

## 决策 3: 漂移检测算法组合策略

### Decision

采用 **多算法 Strategy 组合**，按优先级分三类检测器并行运行：

| 优先级 | 检测器 | 触发条件 | 对应 FR |
|--------|--------|---------|---------|
| P0 | `NoProgressDetector`（时间窗口） | 45s 内无进展类事件 | FR-009, FR-010 |
| P1 | `StateMachineDriftDetector`（状态驻留） | 非终态驻留超过 `stale_running_threshold`（3 × 15s = 45s） | FR-011 |
| P1 | `RepeatedFailureDetector`（重复失败） | 5 分钟内 >= 3 次失败类事件 | FR-012 |

每个检测器实现 `DriftDetectionStrategy` Protocol，`WatchdogScanner` 按配置组合运行并聚合结果。

### Rationale

1. **Strategy 模式**：检测器可插拔，新增检测维度不需要修改 `WatchdogScanner` 核心逻辑，符合开闭原则。
2. **P0/P1 优先级分层**：无进展检测是 M1.5 验收门禁的核心要求，其他两种检测器实现复杂度略高，分层交付降低阻塞风险。
3. **独立 cooldown 跟踪**：每个任务的 cooldown 防抖状态从 `CooldownRegistry` 获取，进程重启后通过查询 EventStore 最近 DRIFT 事件时间戳重建，保证跨重启一致性（FR-006）。

### Alternatives Rejected

- **事件频率统计检测（算法 B）**：需要历史基线数据，MVP 阶段数据量不足，统计置信度低。预留为 M2 增强，届时可作为第 4 个 Strategy 插入。
- **单一时间窗口检测器**：无法覆盖状态机异常驻留和重复失败两种漂移模式，spec 要求三种模式全覆盖。

---

## 决策 4: LLM 等待期豁免策略

### Decision

**显式排除 MODEL_CALL_STARTED 后的合法 LLM 等待窗口**：若任务最近事件为 `MODEL_CALL_STARTED`，且等待时长 < `no_progress_threshold`（默认 45s），则 `NoProgressDetector` 不触发告警。豁免窗口长度复用 `no_progress_threshold`，不引入独立配置项（FR-010）。

### Rationale

1. **误报缓解优先级最高**：调研报告明确标识为"最高优先级缓解项"。单次 LLM 调用合理耗时 30-60s，若不排除等待期，45s 阈值会产生大量误报，告警疲劳会导致操作者忽视真实异常。
2. **复用阈值简化配置**：使用同一 `no_progress_threshold` 作为豁免窗口，避免引入 `model_call_wait_threshold` 等额外配置项，降低配置复杂度（符合 spec FR-010 明确规定）。
3. **实现简单可测**：在 `NoProgressDetector.check()` 中只需添加约 5 行逻辑：检查最近进展事件是否为 `MODEL_CALL_STARTED`，若是则跳过该任务。

### Alternatives Rejected

- **独立 `model_call_wait_threshold` 配置项**：增加配置复杂度，实际上 LLM 调用超时已在 Provider 层控制（`timeout_s`），Watchdog 层不需要独立的 LLM 超时概念。
- **不排除 LLM 等待期**：误报率过高，违背 spec 显式要求（FR-010 MUST）。

---

## 决策 5: EventStore 接口扩展策略

### Decision

向 `SqliteEventStore` 新增 **2 个查询接口** + **1 个 SQLite 索引**：

```python
# 接口 1: 获取任务最新事件时间戳（O(log N)）
async def get_latest_event_ts(self, task_id: str) -> datetime | None

# 接口 2: 按事件类型 + 时间范围查询（支持 Watchdog 窗口检测）
async def get_events_by_types_since(
    self,
    task_id: str,
    event_types: list[EventType],
    since_ts: datetime,
) -> list[Event]
```

```sql
-- 索引: 支持 Watchdog 窗口查询和重复失败统计
CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(task_id, type, ts);
```

同步更新 `EventStore` Protocol（`protocols.py`）和 `SqliteEventStore` 实现。

### Rationale

1. **最小接口扩展**：两个接口完全满足 WatchdogScanner 的所有查询需求，避免引入过度设计的批量接口（如"跨任务批量查询最新心跳"会增加接口复杂度）。
2. **利用现有索引结构**：新增 `idx_events_type_ts` 覆盖`(task_id, type, ts)` 三列，使 `get_latest_event_ts` 和 `get_events_by_types_since` 均可利用索引，避免全表扫描。
3. **向后兼容**：新增方法不修改现有 `get_events_for_task`、`get_events_after` 等接口签名，历史代码无需改动。

### Alternatives Rejected

- **修改现有 `get_events_for_task` 添加过滤参数**：破坏现有接口签名，影响所有调用方。
- **在 WatchdogScanner 内做 N 次 `get_events_for_task` 调用后应用层过滤**：性能差，每次扫描对每个活跃任务返回全量事件，内存压力大。

---

## 决策 6: TaskStore 接口扩展

### Decision

向 `SqliteTaskStore` 新增 **1 个查询接口**，保持原接口向后兼容：

```python
async def list_tasks_by_statuses(
    self,
    statuses: list[TaskStatus],
) -> list[Task]
```

Task Journal 查询必须使用此接口一次性获取多状态任务列表（FR-005 的 spec WARNING 3），禁止通过多次 `list_tasks(status=...)` 串行调用替代（存在竞态窗口）。

### Rationale

1. **消除竞态**：Task Journal 需要同时获取 CREATED、RUNNING、WAITING_APPROVAL 等多个非终态任务，若分多次查询，任务状态可能在查询间隙发生迁移，导致分组不一致。
2. **单次 SQL 查询**：`WHERE status IN (?, ?, ...)` 比 N 次 `WHERE status = ?` 查询性能更好，减少数据库往返次数。
3. **原接口保留**：`list_tasks(status: str | None)` 保持不变，所有现有调用方无需修改。

### Alternatives Rejected

- **多次 `list_tasks(status=...)` 串行调用**：spec 明确在 spec WARNING 3 中禁止，存在竞态窗口。

---

## 决策 7: cooldown 防抖机制实现

### Decision

`CooldownRegistry` 维护 `dict[str, datetime]`（task_id -> 最近 DRIFT 事件时间戳），进程重启后通过查询 `EventStore` 中最近一次 `TASK_DRIFT_DETECTED` 事件的时间戳重建。cooldown 判断逻辑：若 `(now - last_drift_ts) < cooldown_seconds`，则跳过本次 DRIFT 写入，仅记录 structlog 警告。

### Rationale

1. **跨重启一致性**：Constitution 原则 1 要求 cooldown 不因进程重启失效（FR-006 明确要求）。通过 EventStore 重建而非硬编码内存 dict，保证一致性。
2. **实现简单**：cooldown 重建只需在 `WatchdogScanner.startup()` 时查询一次 `get_events_by_types_since(TASK_DRIFT_DETECTED, since_ts=now-cooldown_seconds)`，时间复杂度 O(活跃任务数)。
3. **per-task 隔离**：每个 task_id 独立维护 cooldown 计数器，避免批量漂移时互相影响（spec 边界情况 3）。

### Alternatives Rejected

- **进程内纯内存 cooldown dict（不持久化）**：进程重启后 cooldown 失效，可能产生连续告警轰炸（spec 边界情况 6）。
- **写入独立 cooldown SQLite 表**：过度设计，EventStore 已有足够信息支持重建，不需要额外表。

---

## 决策 8: 依赖库引入

### Decision

新增唯一外部依赖：`apscheduler>=3.10,<4.0`，仅添加到 `octoagent-gateway` 的 `pyproject.toml`。锁定 3.x（API 稳定，`AsyncIOScheduler` 已被项目 blueprint 选型）。

### Rationale

1. **最小化新依赖**：所有其他组件均复用现有依赖（`aiosqlite`、`structlog`、`pydantic`、`logfire`）。
2. **版本锁定安全**：APScheduler 4.x（2024 年重构）API 变化较大，锁定 `<4.0` 规避兼容性风险，待 4.x API 稳定后评估迁移。
3. **asyncio event loop 集成**：`AsyncIOScheduler` 与 FastAPI + asyncio 共享 event loop，无需额外线程。

### Alternatives Rejected

- **APScheduler 4.x**：API 重构大，与现有 3.x 使用方式不兼容，风险较高。
- **纯 `asyncio.create_task` 替代 APScheduler**：丢失 APScheduler 的 job store 持久化能力，进程重启后 job 注册状态丢失（虽然 F011 的 WatchdogScanner 在 lifespan 中重新注册，但会增加启动逻辑复杂度）。

---

## 决策 9: trace_id 透传策略

### Decision

DRIFT 事件的 `trace_id` 继承被检测任务的 `trace_id`（`f"trace-{task_id}"`），`span_id` 字段在 F012 实装前保持空字符串占位。DRIFT 事件 payload 中预留 `watchdog_span_id` 字段，F012 接入 Logfire 后填充实际 span_id。

### Rationale

1. **沿用现有规范**：当前项目 trace_id 格式为 `f"trace-{task_id}"`，F011 沿用此格式，F012 接入时统一升级为 128-bit hex OTel 格式，改动集中在一处。
2. **关联性保持**：DRIFT 事件关联到被检测任务的 trace_id，而非 Watchdog 自身 trace_id，保证告警可追溯到原始任务（Constitution 原则 8）。
3. **预留扩展字段**：`watchdog_span_id` 占位字段确保 F012 接入时无需修改事件 schema，保证 schema 向后兼容（FR-021）。

### Alternatives Rejected

- **DRIFT 事件使用独立 Watchdog trace_id**：会断开 DRIFT 告警与原始任务 trace 的关联，降低可追溯性。
