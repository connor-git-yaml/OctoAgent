# Feature 011 实现检查清单

**特性**: Feature 011 — Watchdog + Task Journal + Drift Detector
**生成日期**: 2026-03-03
**基于文档**: `spec.md`（v Draft）、`research/tech-research.md`
**用途**: 实现阶段质量门控，每个条目在代码实现/测试完成后打勾

---

## 维度 1：功能完整性（FR Coverage）

确保每个 Functional Requirement 均有对应的实现条目和可验证的测试场景。

### 1.1 事件类型扩展（FR-001～FR-003）

- [ ] **FR-001-IMPL** `EventType` 枚举新增 `TASK_HEARTBEAT`、`TASK_MILESTONE`、`TASK_DRIFT_DETECTED` 三个值
- [ ] **FR-001-COMPAT** 新增枚举值不破坏现有 `get_events_for_task`、`get_events_after` 等查询接口的向后兼容性
- [ ] **FR-001-TEST** 单元测试：验证三个新枚举值可序列化/反序列化，不影响现有事件查询
- [ ] **FR-002-IMPL** `TASK_DRIFT_DETECTED` 事件 payload 包含完整字段：`drift_type`、`detected_at`、`task_id`、`trace_id`、`last_progress_ts`、`stall_duration_seconds`、`suggested_actions`
- [ ] **FR-002-SCHEMA** payload 中详细诊断信息通过 `artifact_id` 引用存储，不内联长内容（符合 Constitution 原则 11）
- [ ] **FR-002-TEST** 单元测试：验证 DRIFT 事件 payload 结构校验（Pydantic model 验证必填字段）
- [ ] **FR-003-IMPL** `TASK_HEARTBEAT` 事件 payload 包含：`task_id`、`trace_id`、`loop_step`（可选）、`heartbeat_ts`
- [ ] **FR-003-UTC** `heartbeat_ts` 使用服务端 UTC 时间，禁止使用客户端时间
- [ ] **FR-003-TEST** 单元测试：验证 HEARTBEAT 事件时间戳为 UTC 且不早于写入时间

### 1.2 Watchdog Scanner（FR-004～FR-008）

- [ ] **FR-004-IMPL** Watchdog Scanner 以持久化感知模式运行，通过 APScheduler（推荐锁定 `<4.0`）注册扫描 Job，默认周期 15 秒
- [ ] **FR-004-RECOVER** 进程重启后，Watchdog Scanner 在首次扫描时从 EventStore + TaskStore 重建检测基准，不依赖进程内内存状态
- [ ] **FR-004-TEST** 集成测试：模拟进程重启（重建 Scanner 实例），验证首次扫描能从持久化数据中正确重建基准
- [ ] **FR-005-IMPL** Watchdog Scanner 检测到漂移时，仅调用 `EventStore.append_event_committed` 写入 `TASK_DRIFT_DETECTED` 事件，不调用任何任务状态变更接口
- [ ] **FR-005-TEST** 单元测试（mock）：验证 `WatchdogScanner.scan()` 在漂移场景下不调用 `cancel`、`pause` 或任何 TaskStore 写入方法
- [ ] **FR-006-IMPL** `CooldownRegistry` 实现：为每个 `task_id` 维护独立 cooldown 计数器（默认 60 秒），cooldown 窗口内不重复写入 DRIFT 事件
- [ ] **FR-006-REBUILD** `CooldownRegistry` 在进程重启后，通过查询 EventStore 中最近一条 `TASK_DRIFT_DETECTED` 事件时间戳重建 cooldown 基准
- [ ] **FR-006-TEST** 单元测试：在 cooldown 窗口内连续触发两次扫描，验证第二次不产生新的 DRIFT 事件
- [ ] **FR-006-TEST-RESTART** 集成测试：写入 DRIFT 事件后模拟重启，验证 cooldown 在重建后仍然有效
- [ ] **FR-007-IMPL** 扫描失败（如 SQLite BUSY 异常）时，捕获异常、记录 `log.warning`（含异常详情），不重新抛出异常，等待下一周期重试
- [ ] **FR-007-TEST** 单元测试：mock EventStore 抛出 `aiosqlite.OperationalError`，验证 Scanner 记录警告并继续运行（不退出）
- [ ] **FR-007-NOINTERRUPT** 验证 Watchdog 扫描失败期间，正在运行的任务 Worker 不受任何影响
- [ ] **FR-008-IMPL** 每次扫描产生结构化日志，包含：扫描触发时间、活跃任务数量、检测到的漂移任务数量、扫描耗时（毫秒）
- [ ] **FR-008-TRACEID** 扫描日志绑定当前 Watchdog scan span 的 `trace_id`

### 1.3 漂移检测器（FR-009～FR-013）

- [ ] **FR-009-IMPL** 无进展检测器（`NoProgressDetector`）使用七种进展事件类型作为判断基准：`MODEL_CALL_STARTED`、`MODEL_CALL_COMPLETED`、`TOOL_CALL_STARTED`、`TOOL_CALL_COMPLETED`、`TASK_HEARTBEAT`、`TASK_MILESTONE`、`CHECKPOINT_SAVED`
- [ ] **FR-009-THRESHOLD** 无进展阈值默认 45 秒（= 3 × 15 秒扫描周期），通过 `WatchdogConfig.no_progress_cycles × scan_interval_seconds` 动态计算
- [ ] **FR-009-TEST** 单元测试：写入 RUNNING 任务 + 停止进展事件超过 45 秒，验证产生 `no_progress` 类型 DRIFT 事件
- [ ] **FR-010-IMPL** 无进展检测器显式排除"合法 LLM 等待期"：若最近事件为 `MODEL_CALL_STARTED` 且等待时长 < `model_call_wait_threshold`，不触发无进展漂移
- [ ] **FR-010-TEST** 单元测试：写入 `MODEL_CALL_STARTED` 事件后等待超过 `no_progress_threshold`，但未超过 `model_call_wait_threshold`，验证不产生 DRIFT 事件
- [ ] **FR-011-IMPL** 状态机漂移检测器（`StateMachineDriftDetector`）使用完整内部 `TaskStatus` 非终态枚举集合：`CREATED`、`RUNNING`、`QUEUED`、`WAITING_INPUT`、`WAITING_APPROVAL`、`PAUSED`
- [ ] **FR-011-NO-A2A** 代码中无出现 `active`/`pending` 等 A2A 状态二元划分判断（代码审查项）
- [ ] **FR-011-TEST** 单元测试：任务在 RUNNING 状态驻留超过 `stale_running_threshold`，验证产生 `state_machine_stall` 类型 DRIFT 事件
- [ ] **FR-012-IMPL** 重复失败检测器（`RepeatedFailureDetector`）统计 `failure_window_seconds`（默认 300 秒）内的失败事件：`MODEL_CALL_FAILED`、`TOOL_CALL_FAILED`、`SKILL_FAILED`
- [ ] **FR-012-THRESHOLD** 失败次数达到 `repeated_failure_threshold`（默认 3 次）时写入 `repeated_failure` 类型 DRIFT 事件
- [ ] **FR-012-TEST** 单元测试：写入同一任务 3 条失败事件（在 300 秒内），验证产生 `repeated_failure` DRIFT 事件；写入 2 条不产生
- [ ] **FR-013-IMPL** 所有检测器在处理任务前检查 TaskStatus，若为终态（`SUCCEEDED`、`FAILED`、`CANCELLED`、`REJECTED`）则立即跳过
- [ ] **FR-013-TEST** 单元测试：对终态任务执行 Watchdog 扫描，验证不产生任何 DRIFT 事件，EventStore 写入次数为 0

### 1.4 Task Journal 视图（FR-014～FR-016）

- [ ] **FR-014-IMPL** Task Journal 查询接口实现，返回四个分组：`running`、`stalled`、`drifted`、`waiting_approval`
- [ ] **FR-014-TEST** 集成测试：在各状态下写入测试任务，调用 Journal API 验证四个分组分类正确
- [ ] **FR-015-IMPL** 每条任务记录包含：`task_id`、`task_status`（内部 TaskStatus）、`journal_state`、`last_event_ts`、`drift_summary`（可选）、`suggested_actions`
- [ ] **FR-015-NO-A2A** 响应中 `task_status` 字段使用完整内部 TaskStatus 枚举，不映射为 A2A 状态
- [ ] **FR-015-TEST** 单元测试：验证 `TaskJournalEntry` Pydantic model 字段完整性
- [ ] **FR-016-IMPL** 诊断详情遵循"摘要 + artifact 引用"模式：API 响应仅含 `drift_summary` 字段，完整诊断通过 `drift_artifact_id` 引用
- [ ] **FR-016-TEST** 集成测试：验证漂移任务的 Journal 响应中详细诊断通过 artifact_id 引用，不内联原始事件列表

### 1.5 可配置阈值（FR-017～FR-018）

- [ ] **FR-017-IMPL** `WatchdogConfig`（Pydantic BaseModel）包含五个配置项并有明确默认值：`scan_interval_seconds=15`、`no_progress_cycles=3`、`cooldown_seconds=60`、`failure_window_seconds=300`、`repeated_failure_threshold=3`
- [ ] **FR-017-TEST** 单元测试：验证 `WatchdogConfig()` 不传参时所有默认值正确
- [ ] **FR-018-IMPL** 支持通过环境变量覆盖，命名规范 `WATCHDOG_{KEY}`（大写）
- [ ] **FR-018-INVALID** 遇到无效值（负数、零、非整数）时，记录 `log.warning` 并回退到默认值，不抛出异常、不中断启动
- [ ] **FR-018-TEST** 单元测试：设置 `WATCHDOG_SCAN_INTERVAL_SECONDS=-1`，验证使用默认值 15 并产生警告日志

### 1.6 可观测性要求（FR-019～FR-021）

- [ ] **FR-019-IMPL** 所有 `TASK_DRIFT_DETECTED` 事件携带：`task_id`、`trace_id`（继承被检测任务）、`span_id`（预留字段，F012 前为空字符串）
- [ ] **FR-020-IMPL** Task Journal 视图及 Watchdog 扫描日志全链路透传 `task_id` 和 `trace_id`
- [ ] **FR-020-TEST** 集成测试（GATE-M15-WATCHDOG）：验证从漂移检测到 Journal 查询的完整链路，`task_id` 和 `trace_id` 无断链
- [ ] **FR-021-IMPL** DRIFT 事件 payload 包含 `watchdog_span_id` 字段，F012 前值为空字符串占位，Schema 无需在 F012 时修改

### 1.7 策略动作（FR-022～FR-024）

- [ ] **FR-022-IMPL** Policy Engine 能消费 `TASK_DRIFT_DETECTED` 事件，支持四种动作：`alert`（结构化日志）、`demote`（降低优先级）、`pause`（迁移到 PAUSED）、`cancel`（终止到 CANCELLED）
- [ ] **FR-022-TEST** 单元测试：mock 写入 DRIFT 事件，验证 Policy Engine 路由逻辑调用对应动作处理器
- [ ] **FR-023-IMPL** `pause` 和 `cancel` 动作通过两阶段门控：Plan（产生 DRIFT 信号）-> Gate（Policy/用户确认）-> Execute（状态变更）
- [ ] **FR-023-TEST** 集成测试：触发漂移后，验证 Watchdog 本身不直接修改任务状态，状态变更仅发生在 Policy Engine Execute 阶段
- [ ] **FR-024-IMPL** 策略动作执行结果（成功或失败）写入 EventStore 独立事件，包含：`drift_event_id`、动作类型、执行时间、执行结果
- [ ] **FR-024-TEST** 集成测试：触发 alert 动作，验证 EventStore 中存在关联动作事件，且通过事件查询接口可检索

---

## 维度 2：安全与权限（Constitution 原则 4 + 原则 7）

### 2.1 Two-Phase Side-effect 检查（原则 4）

- [ ] **SEC-01** Watchdog Scanner 代码中无直接调用 `TaskStore.update_task`、`TaskStore.cancel_task`、`TaskRunner.cancel` 或任何修改任务状态的方法（代码审查项，验证 `_emit_drift_event` 是唯一的副作用）
- [ ] **SEC-02** `pause` 和 `cancel` 动作的 Execute 阶段由 Policy Engine 独立实现，Watchdog 模块不导入（import）Policy Engine 的 Execute 层代码
- [ ] **SEC-03** DRIFT 事件写入本身是 append-only 操作，不修改任何已存在事件（符合 EventStore 不可变性）
- [ ] **SEC-04** 单元测试（mock）：断言 `WatchdogScanner` 实例的方法集合中不存在 `cancel`、`pause`、`update_status` 等副作用方法

### 2.2 User-in-Control 检查（原则 7）

- [ ] **SEC-05** `pause` 和 `cancel` 两种高风险动作的 Gate 阶段需要用户确认或通过 Policy Profile 配置明确启用，不自动执行
- [ ] **SEC-06** Policy Engine 路由漂移动作时，`demote` 和 `alert` 可自动执行，`pause`/`cancel` 须有明确的授权检查
- [ ] **SEC-07** DRIFT 事件的 `suggested_actions` 字段为建议列表（字符串数组），不是自动执行指令（设计上为只读建议）
- [ ] **SEC-08** 集成测试：模拟 Policy Engine 消费 DRIFT 事件，验证在未配置自动 pause/cancel 的情况下，任务状态不被自动变更

---

## 维度 3：可观测性（事件记录、trace_id 透传）

### 3.1 事件持久化

- [ ] **OBS-01** `TASK_DRIFT_DETECTED` 事件通过 `EventStore.append_event_committed` 写入，不直接操作 SQLite（遵循存储层抽象）
- [ ] **OBS-02** `TASK_HEARTBEAT` 和 `TASK_MILESTONE` 事件由 Worker/TaskRunner 在关键节点主动写入，不在 Watchdog 内伪造
- [ ] **OBS-03** 所有 Watchdog 产生的事件均可通过 `get_events_for_task(task_id)` 查询到（集成测试验证）

### 3.2 trace_id 透传链路

- [ ] **OBS-04** DRIFT 事件的 `trace_id` 字段值等于被检测任务的 `trace_id`（格式：`f"trace-{task_id}"`），而非 Watchdog 自身的 trace_id
- [ ] **OBS-05** Task Journal API 响应中每条记录的 `task_id` 可与 EventStore 中对应任务的事件记录一一对应（无孤立记录）
- [ ] **OBS-06** 集成测试：构造完整链路（任务创建 -> 漂移检测 -> DRIFT 事件 -> Journal 查询），验证 `trace_id` 在每个阶段保持一致
- [ ] **OBS-07** `watchdog_span_id` 字段在 F012 前为空字符串，F012 实装后无需修改事件 Schema（字段预留设计验证）

### 3.3 扫描日志可观测性

- [ ] **OBS-08** 每次 Watchdog 扫描完成后，structlog 记录包含：`event="watchdog.scan_completed"`、`active_task_count`、`drifted_task_count`、`scan_duration_ms`、`triggered_at`
- [ ] **OBS-09** 扫描失败时，structlog 记录 `event="watchdog.scan_failed"`，包含异常类型和扫描时间，级别为 WARNING
- [ ] **OBS-10** 单元测试：验证正常扫描和失败扫描均产生预期结构的 structlog 输出（使用 structlog.testing.capture_logs）

---

## 维度 4：数据一致性（EventStore 事务边界，append-only）

### 4.1 EventStore 写入一致性

- [ ] **DATA-01** DRIFT 事件写入使用 `append_event_committed`（已有任务级 Lock），不绕过 Lock 直接写 SQLite
- [ ] **DATA-02** 同一任务的多个检测算法（`NoProgressDetector`、`StateMachineDriftDetector`、`RepeatedFailureDetector`）在同一扫描周期内，若同时触发，各自独立写入 DRIFT 事件（类型不同），不合并为单条事件
- [ ] **DATA-03** 边界情况：任务在 Watchdog 即将写入 DRIFT 事件的同时写入终态事件，Watchdog 必须在写入前再次检查任务状态，确保不对终态任务写入 DRIFT 事件

### 4.2 CooldownRegistry 一致性

- [ ] **DATA-04** CooldownRegistry 使用 `task_id` 为 key 的 dict，跨任务独立，不共享 cooldown 计数器
- [ ] **DATA-05** 进程重启后，CooldownRegistry 通过 EventStore 查询最近一条 `TASK_DRIFT_DETECTED` 事件时间戳重建，不使用进程内初始化默认值（如 `now()` 或 `epoch`）
- [ ] **DATA-06** 集成测试：写入 DRIFT 事件后 30 秒（< 60 秒 cooldown）重建 CooldownRegistry，验证同一任务不再触发新 DRIFT 事件

### 4.3 EventStore 新增接口一致性

- [ ] **DATA-07** 新增 `get_latest_event_ts(task_id)` 接口同步更新 `EventStore Protocol`（`protocols.py`），不只更新 `SqliteEventStore` 实现
- [ ] **DATA-08** 新增 `get_events_by_types_since(task_id, event_types, since_ts)` 接口同步更新 Protocol
- [ ] **DATA-09** 新增 SQLite 索引 `idx_events_type_ts ON events(task_id, type, ts)` 在 `sqlite_init.py` 中添加 `CREATE INDEX IF NOT EXISTS`（幂等）
- [ ] **DATA-10** 单元测试：验证 `get_events_by_types_since` 过滤逻辑正确（只返回指定类型且时间戳在范围内的事件）

---

## 维度 5：错误处理（Watchdog 失败时系统降级）

### 5.1 Watchdog 自身扫描失败

- [ ] **ERR-01** APScheduler job 的异常处理：扫描函数内部 catch 所有 Exception，记录 WARNING 日志后正常返回（不 re-raise），保证 APScheduler 不因单次 job 失败而停止调度
- [ ] **ERR-02** SQLite BUSY 异常（`aiosqlite.OperationalError: database is locked`）时，Watchdog 不累积重试（无指数退避循环），等待下一个 15 秒周期自然重试
- [ ] **ERR-03** 单元测试：mock APScheduler job 执行器，验证扫描抛出异常后 APScheduler 在下一周期继续触发 job

### 5.2 主任务执行不受 Watchdog 影响

- [ ] **ERR-04** Watchdog Scanner 与 Worker 运行在同一 asyncio event loop，但 Watchdog 的 DB 查询失败不影响 Worker 的 `append_event_committed` 操作（SQLite WAL 支持并发读写）
- [ ] **ERR-05** 集成测试：Watchdog 扫描和 Worker 事件写入并发执行 100 次，验证无 deadlock 且所有 Worker 事件均成功写入
- [ ] **ERR-06** `PRAGMA busy_timeout = 5000` 配置在 SQLite 初始化中已设置，Watchdog 读取操作最多等待 5 秒后超时（不无限阻塞）

### 5.3 大量任务同时漂移

- [ ] **ERR-07** 批量漂移场景（多任务同时触发）时，Watchdog 为每个任务独立写入 DRIFT 事件，不使用批量事务（保持各任务事件独立性，避免批量失败回滚影响其他任务）
- [ ] **ERR-08** 集成测试：构造 10 个同时漂移的任务，验证所有 10 个任务均产生各自的 DRIFT 事件，无事件缺失

### 5.4 无效配置降级

- [ ] **ERR-09** `WatchdogConfig` 加载无效环境变量时，使用默认值并产生 WARNING 日志，不抛出 `ValidationError` 或 `SystemExit`
- [ ] **ERR-10** 单元测试：设置三种无效配置（负数、零、字符串），验证均回退到默认值并产生对应警告

---

## 维度 6：性能（APScheduler 扫描频率，SQLite WAL 并发）

### 6.1 扫描性能

- [ ] **PERF-01** Watchdog 每次扫描通过 `TaskStore.list_tasks(non_terminal)` 获取活跃任务列表，不使用 `get_all_events()` 全量扫描（避免 O(N×M) 复杂度）
- [ ] **PERF-02** `get_latest_event_ts` 接口利用 `idx_events_type_ts` 索引，查询复杂度为 O(log N)，而非全表扫描
- [ ] **PERF-03** SC-007 验收：Task Journal API 在活跃任务数量 < 100 时，响应时间 < 2 秒（集成测试用 100 个测试任务验证）
- [ ] **PERF-04** SC-001 验收：Watchdog 检测延迟不超过 1 个扫描周期（15 秒），即从任务停止进展到 DRIFT 事件写入，实际延迟 < 15 + 45 = 60 秒

### 6.2 SQLite WAL 并发安全

- [ ] **PERF-05** Watchdog 的读操作（查询活跃任务、查询事件）不获取写锁，利用 SQLite WAL 快照隔离（只读事务）实现与 Worker 写操作的并发安全
- [ ] **PERF-06** DRIFT 事件写入通过 `append_event_committed`（已有 task_id 级别 Lock），不引入新的锁层级
- [ ] **PERF-07** 性能测试：并发 10 个 Worker 写入事件 + 1 个 Watchdog 扫描，验证总体 P99 写入延迟 < 500ms

### 6.3 扫描开销控制

- [ ] **PERF-08** Watchdog Scanner 应支持分批处理（`chunk_size=50`），当活跃任务超过 50 时分批查询，避免单次扫描占用 DB 连接过长
- [ ] **PERF-09** Watchdog 扫描周期（15 秒）远大于单次扫描耗时，不出现扫描堆积（下一周期触发时上一次已完成）

---

## 维度 7：测试覆盖（E2E / 集成 / 单元）

### 7.1 单元测试（Unit Tests）

覆盖独立函数/类，使用 mock 隔离外部依赖：

- [ ] **TEST-UNIT-01** `NoProgressDetector.check()`: 正常进展 / 超阈值卡死 / LLM 等待期排除 三种场景
- [ ] **TEST-UNIT-02** `StateMachineDriftDetector.check()`: 非终态超时触发 / 终态跳过 / 各非终态枚举覆盖
- [ ] **TEST-UNIT-03** `RepeatedFailureDetector.check()`: 达阈值触发 / 低于阈值不触发 / 时间窗口外事件不计入
- [ ] **TEST-UNIT-04** `WatchdogConfig`: 默认值覆盖 / 环境变量覆盖 / 无效值回退
- [ ] **TEST-UNIT-05** `CooldownRegistry`: cooldown 内不重复告警 / cooldown 后允许再次告警 / 重建逻辑
- [ ] **TEST-UNIT-06** `DriftResult` 值对象：字段完整性校验（Pydantic model）
- [ ] **TEST-UNIT-07** `TaskJournalEntry` 值对象：四个分组状态字段覆盖
- [ ] **TEST-UNIT-08** Watchdog Scanner 边界情况：扫描失败不退出（ERR-01）、终态任务跳过（FR-013）

### 7.2 集成测试（Integration Tests）

使用 in-memory SQLite，覆盖跨层交互：

- [ ] **TEST-INT-01** EventStore 新增接口：`get_latest_event_ts` 和 `get_events_by_types_since` 查询正确性（真实 SQLite）
- [ ] **TEST-INT-02** Task Journal 四分组逻辑：各状态任务正确分组（`running`/`stalled`/`drifted`/`waiting_approval`）
- [ ] **TEST-INT-03** 漂移恢复：任务写入新 HEARTBEAT 事件后，Journal 中从 `drifted` 回到 `running`（US-2 验收场景 3）
- [ ] **TEST-INT-04** cooldown 跨重启一致性：DRIFT 事件写入后重建 Scanner，cooldown 仍然有效（DATA-06）
- [ ] **TEST-INT-05** 并发安全：Watchdog 扫描 + Worker 写入并发 100 次无 deadlock（ERR-05）
- [ ] **TEST-INT-06** 批量漂移：10 个任务同时漂移，验证 10 条独立 DRIFT 事件（ERR-08）
- [ ] **TEST-INT-07** 策略动作审计：DRIFT 事件触发 alert 动作后，EventStore 中存在关联动作事件（FR-024）

### 7.3 E2E 测试（End-to-End，对应 US-7 / F011-T06）

使用 in-memory SQLite + 时间注入，模拟完整运行环境：

- [ ] **TEST-E2E-01** （US-7 场景 1）：注入停止产生进展事件的 RUNNING 任务，超过 `no_progress_threshold`，验证 `TASK_DRIFT_DETECTED` 事件类型为 `no_progress`
- [ ] **TEST-E2E-02** （US-7 场景 2）：注入反复失败的任务（失败次数超阈值），验证 `TASK_DRIFT_DETECTED` 类型为 `repeated_failure`
- [ ] **TEST-E2E-03** （US-7 场景 3）：注入 RUNNING 状态长时间驻留任务，验证 `TASK_DRIFT_DETECTED` 类型为 `state_machine_stall`
- [ ] **TEST-E2E-04** （SC-004）：无任何配置启动 Watchdog，验证默认阈值（15s/45s/60s）全部生效
- [ ] **TEST-E2E-05** （SC-005）：模拟 SQLite BUSY，验证下一周期自动恢复扫描，无任务中断
- [ ] **TEST-E2E-06** （SC-006）：模拟进程重启（重建所有 Store 和 Scanner），验证首次扫描从持久化数据正确恢复，无检测盲窗
- [ ] **TEST-E2E-07** （GATE-M15-WATCHDOG）：完整链路 trace_id 透传验证，从任务创建到 DRIFT 事件到 Journal 响应，`task_id` 和 `trace_id` 无断链

---

## 维度汇总（Quick Reference）

| 维度 | 总条目 | 状态 |
|------|-------|------|
| 1. 功能完整性（FR Coverage） | 56 | 待实现 |
| 2. 安全与权限（Constitution 原则 4 + 7） | 8 | 待实现 |
| 3. 可观测性（事件记录、trace_id 透传） | 10 | 待实现 |
| 4. 数据一致性（EventStore 事务边界） | 10 | 待实现 |
| 5. 错误处理（Watchdog 降级） | 10 | 待实现 |
| 6. 性能（APScheduler / SQLite WAL） | 9 | 待实现 |
| 7. 测试覆盖（E2E / 集成 / 单元） | 22 | 待实现 |
| **合计** | **125** | **0 / 125 通过** |

---

## 附：关键风险提醒

根据 `tech-research.md` 技术风险清单，以下风险项在实现时须重点关注：

1. **LLM 等待期误报（风险 2，概率高）** — `FR-010` 是最高优先级缓解项，必须在 `NoProgressDetector` 中首先实现 `MODEL_CALL_STARTED` 排除逻辑，并通过 `TEST-UNIT-01` 验证
2. **Policy Engine 动作路由未完成（风险 4，MVP 阶段概率高）** — `FR-022～FR-024` 可作为 P1 独立 PR 交付，P0 阶段以"写 DRIFT 事件 + 结构化日志 alert"作为最小可用交付
3. **APScheduler 版本兼容（风险 6）** — 必须锁定 `apscheduler>=3.10,<4.0`，不引入 4.x API
4. **漂移误报引发误动作（风险 8，影响高）** — `SEC-01～SEC-04` 的两阶段检查是防御核心，Watchdog 代码中绝对不得出现直接状态变更调用

---

*本检查清单为实现阶段工具，逐项完成后打勾，全部 125 项通过方可提交 Feature 011 为 Done。*
