# Tasks: Feature 011 — Watchdog + Task Journal + Drift Detector

**Input**: `.specify/features/011-watchdog-task-journal/`
**Prerequisites**: spec.md, plan.md, data-model.md, contracts/rest-api.md
**Created**: 2026-03-03
**Status**: Ready

**Task Format**: `- [ ] T{三位数} [P0/P1] [P?] [USN?] 描述 → 文件路径`

- `[P0]` / `[P1]`: 交付优先级（P0 阻塞 M1.5 验收，P1 验收后补充）
- `[P]`: 可并行执行（不同文件，无依赖）
- `[B]`: 阻塞后续关键路径（标注重要阻塞节点）
- `[USN]`: 所属 User Story（US1–US7），Setup/Foundational/Polish 阶段不标注

---

## Phase 1: Setup（项目基础设施）

**目标**: 建立新模块目录结构，补充依赖，确保后续所有任务可以直接开始编写代码

- [x] T001 [P0] [P] 创建 watchdog 子包目录结构（含 `__init__.py`）
  → `octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/__init__.py`

- [x] T002 [P0] [P] 在 `pyproject.toml` 中追加 `apscheduler>=3.10,<4.0` 依赖
  → `octoagent/apps/gateway/pyproject.toml`（或 monorepo 根 `pyproject.toml`）

- [x] T003 [P0] [P] 创建测试目录结构（unit/watchdog + integration/watchdog + e2e）
  → `octoagent/apps/gateway/tests/unit/watchdog/__init__.py`
  → `octoagent/apps/gateway/tests/integration/watchdog/__init__.py`
  → `octoagent/apps/gateway/tests/e2e/__init__.py`（若不存在）

**Checkpoint**: 目录结构就绪，依赖锁定，可进入 Phase 2

---

## Phase 2: Foundational（阻塞性前置依赖）

**目标**: 建立 EventType 枚举、Payload 类型、EventStore/TaskStore 查询扩展和 SQLite 索引
这是所有 User Story 实现的最小共享基础，任何 User Story 均不能先于此 Phase 开始

> **警告**: 此 Phase 未完成之前，不得开始 Phase 3+ 任何任务

### 2.1 枚举与 Payload 扩展（FR-001, FR-002, FR-003）

- [x] T004 [P0] [B] [P] 在 `EventType` 枚举中追加三个新事件类型（`TASK_HEARTBEAT`、`TASK_MILESTONE`、`TASK_DRIFT_DETECTED`），不修改任何已有枚举值
  → `octoagent/packages/core/src/octoagent/core/models/enums.py`

- [x] T005 [P0] [P] 实现 `TaskHeartbeatPayload` Pydantic 模型（字段：`task_id`、`trace_id`、`heartbeat_ts`、`loop_step`、`note`），依赖 T004
  → `octoagent/packages/core/src/octoagent/core/models/payloads.py`

- [x] T006 [P0] [P] 实现 `TaskMilestonePayload` Pydantic 模型（字段：`task_id`、`trace_id`、`milestone_name`、`milestone_ts`、`summary`、`artifact_ref`），依赖 T004
  → `octoagent/packages/core/src/octoagent/core/models/payloads.py`

- [x] T007 [P0] [P] 实现 `TaskDriftDetectedPayload` Pydantic 模型（字段：`drift_type`、`detected_at`、`task_id`、`trace_id`、`last_progress_ts`、`stall_duration_seconds`、`suggested_actions`、`artifact_ref`、`watchdog_span_id`、`failure_count`、`failure_event_types`、`current_status`），依赖 T004
  → `octoagent/packages/core/src/octoagent/core/models/payloads.py`

- [x] T008 [P0] 为三个新 Payload 类型编写单元测试（验证字段校验、默认值、JSON 序列化）
  → `octoagent/packages/core/tests/unit/models/test_watchdog_payloads.py`（新建）

### 2.2 SQLite 索引（性能保障）

- [x] T009 [P0] [B] 在 SQLite 初始化脚本末尾追加 `idx_events_type_ts` 索引（`CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(task_id, type, ts)`）
  → `octoagent/packages/core/src/octoagent/core/store/sqlite_init.py`

### 2.3 EventStore 查询扩展（FR-009 支撑接口）

- [x] T010 [P0] [B] 实现 `get_latest_event_ts(task_id: str) -> datetime | None` 方法，依赖 T009（索引已就绪）
  → `octoagent/packages/core/src/octoagent/core/store/event_store.py`

- [x] T011 [P0] [B] 实现 `get_events_by_types_since(task_id: str, event_types: list[EventType], since_ts: datetime) -> list[Event]` 方法，依赖 T009
  → `octoagent/packages/core/src/octoagent/core/store/event_store.py`

- [x] T012 [P0] 为 `get_latest_event_ts` 和 `get_events_by_types_since` 编写单元测试（覆盖空事件/正常查询/类型过滤/时间边界）
  → `octoagent/packages/core/tests/unit/store/test_event_store_extensions.py`（新建）

### 2.4 TaskStore 查询扩展（spec WARNING 3）

- [x] T013 [P0] [B] 实现 `list_tasks_by_statuses(statuses: list[TaskStatus]) -> list[Task]` 方法（单次 `IN (?)` SQL 原子查询，保持原 `list_tasks` 接口向下兼容）
  → `octoagent/packages/core/src/octoagent/core/store/task_store.py`

- [x] T014 [P0] 为 `list_tasks_by_statuses` 编写单元测试（覆盖多状态过滤/空结果/向下兼容验证）
  → `octoagent/packages/core/tests/unit/store/test_task_store_extensions.py`（新建）

**Checkpoint**: 枚举/Payload/Store 接口全部就绪，Phase 3 可以开始

---

## Phase 3: User Story 1 + US2 — 无进展检测 + Task Journal（Priority: P1）MVP 核心

> US1 和 US2 共享 Phase 3，因为 Task Journal（US2）的 `stalled` 分组直接依赖无进展检测的阈值逻辑，两者在实现层紧密耦合，应一起交付形成最小完整闭环。

**US1 目标**: 系统在规定时间内自动检测 RUNNING 卡死任务，写入 `TASK_DRIFT_DETECTED` 事件，携带诊断摘要
**US2 目标**: 操作者通过 `GET /api/tasks/journal` 获取任务健康分组视图（running/stalled/drifted/waiting_approval）

**独立测试（US1）**: 向 EventStore 写入 RUNNING 任务并停止进展事件 → 等待超过 `no_progress_threshold` → 验证 `TASK_DRIFT_DETECTED` 事件已写入、payload 包含诊断摘要、无需 Policy Engine 参与
**独立测试（US2）**: 调用 `GET /api/tasks/journal` → 验证四分组结构、字段完整性、`task_status` 使用内部 TaskStatus 而非 A2A 状态

### 3.1 WatchdogConfig（US1/US2 共享配置）

- [x] T015 [P0] [US1] [B] 实现 `WatchdogConfig` Pydantic BaseModel（字段：`scan_interval_seconds=15`、`no_progress_cycles=3`、`cooldown_seconds=60`、`failure_window_seconds=300`、`repeated_failure_threshold=3`），含 `from_env()` 类方法和 `_positive_integer` 校验器（无效值回退默认值），实现 `no_progress_threshold_seconds` 属性（FR-017, FR-018）
  → `octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/config.py`（新建）

- [x] T016 [P0] [US1] 编写 `WatchdogConfig` 单元测试（覆盖默认值/env 覆盖/无效值回退/`no_progress_threshold_seconds` 计算）
  → `octoagent/apps/gateway/tests/unit/watchdog/test_config.py`（新建）

### 3.2 WatchdogConfig 用户故事 5 验收所需环境变量支持（US5 在 P2 级别，但 Config 一次实现到位）

> 注：`WatchdogConfig` 在 T015 中已包含 US5 所需的全部功能，US5 仅需单独测试验证

### 3.3 CooldownRegistry（FR-006，进程重启防抖）

- [x] T017 [P0] [US1] [B] 实现 `CooldownRegistry`（内存字典 `_last_drift_ts`，提供 `rebuild_from_store`/`is_in_cooldown`/`record_drift` 方法，跨重启从 EventStore 重建），依赖 T011（`get_events_by_types_since`）
  → `octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/cooldown.py`（新建）

- [x] T018 [P0] [US1] 编写 `CooldownRegistry` 单元测试（覆盖首次检测/cooldown 窗口内/cooldown 过期/重建逻辑）
  → `octoagent/apps/gateway/tests/unit/watchdog/test_cooldown.py`（新建）

### 3.4 Watchdog 内部模型（值对象）

- [x] T019 [P0] [US1] [P] 实现 `DriftResult` dataclass（字段：`task_id`/`drift_type`/`detected_at`/`stall_duration_seconds`/`suggested_actions`/`last_progress_ts`/`failure_count`/`failure_event_types`/`current_status`），实现 `DriftSummary` dataclass 和 `TaskJournalEntry` dataclass（`journal_state: JournalState`），依赖 T004
  → `octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/models.py`（新建）

### 3.5 NoProgressDetector（FR-009, FR-010 核心检测逻辑）

- [x] T020 [P0] [US1] [B] 实现 `DriftDetectionStrategy` Protocol（单方法 `check(task, event_store, config) -> DriftResult | None`）
  → `octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/detectors.py`（新建）

- [x] T021 [P0] [US1] [B] 实现 `NoProgressDetector`（`PROGRESS_EVENT_TYPES` 常量集合、时间窗口查询、LLM 等待期豁免逻辑、`task.updated_at` 降级回退、`DriftResult` 返回），依赖 T019、T020、T010、T011
  → `octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/detectors.py`

- [x] T022 [P0] [US1] 编写 `NoProgressDetector` 单元测试（覆盖以下场景）：
  - 正常进展任务（时间窗口内有进展事件）→ 返回 `None`
  - 超过阈值无进展 → 返回 `DriftResult(drift_type="no_progress")`
  - `MODEL_CALL_STARTED` 豁免窗口内 → 返回 `None`（LLM 等待期排除）
  - 无历史事件降级使用 `task.updated_at` → 正确计算 `stall_duration_seconds`
  - 边界情况 1（终态任务）→ 不触发
  → `octoagent/apps/gateway/tests/unit/watchdog/test_no_progress.py`（新建）

### 3.6 WatchdogScanner（FR-004~FR-008 核心调度器）

- [x] T023 [P0] [US1] [B] 实现 `WatchdogScanner` 类（构造函数接收 `store_group`/`config`/`cooldown_registry`/`detectors`，实现 `startup()`/`scan()`/`_emit_drift_event()` 方法），包含：
  - `startup()`：调用 `cooldown_registry.rebuild_from_store()`（FR-006 跨重启一致性）
  - `scan()`：`try/except` 全包裹（FR-007）、`list_tasks_by_statuses(NON_TERMINAL_STATES)`、遍历并运行 detectors、cooldown 检查、写入 DRIFT 事件、structlog 扫描元数据日志（FR-008）
  - `_emit_drift_event()`：构建 `TaskDriftDetectedPayload`、调用 `append_event_committed()`（FR-002, FR-019）、`cooldown_registry.record_drift()`
  → `octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/scanner.py`（新建）

- [x] T024 [P0] [US1] 编写 `WatchdogScanner` 集成测试（in-memory SQLite，覆盖以下场景）：
  - 单次扫描检测到漂移并写入 `TASK_DRIFT_DETECTED` 事件
  - cooldown 防抖：第二次扫描不重复写入事件
  - 扫描失败（模拟 Store 异常）→ 记录 warning、不抛出、下次扫描可恢复
  - 终态任务被跳过
  - 进程重启（新实例）后 `startup()` 重建 cooldown 注册表
  → `octoagent/apps/gateway/tests/integration/watchdog/test_scanner.py`（新建）

### 3.7 APScheduler 集成（lifespan 注册）

- [x] T025 [P0] [US1] 在 `gateway/main.py` 的 lifespan 函数中注册 APScheduler job：实例化 `WatchdogConfig.from_env()`、`CooldownRegistry()`、`WatchdogScanner`（含 `NoProgressDetector`），调用 `await watchdog_scanner.startup()`，注册 `AsyncIOScheduler` interval job，shutdown 时调用 `scheduler.shutdown(wait=False)`，依赖 T015、T017、T021、T023
  → `octoagent/apps/gateway/src/octoagent/gateway/main.py`

### 3.8 TaskJournalService（FR-014, FR-015, FR-016 实时聚合）

- [x] T026 [P0] [US2] [B] 实现 `TaskJournalService` 类（`NON_TERMINAL_STATUSES` 常量，`get_journal(config) -> JournalResponse` 方法：调用 `list_tasks_by_statuses`、对每个任务聚合 `last_event_ts`/DRIFT 事件历史，按分类规则确定 `journal_state`，组装 `JournalResponse`），依赖 T013、T010、T011、T019
  → `octoagent/apps/gateway/src/octoagent/gateway/services/task_journal.py`（新建）

- [x] T027 [P0] [US2] 实现 `JournalResponse` Pydantic 模型（`generated_at`、`summary`、`groups`），`JournalSummary` 模型（四分组计数），依赖 T019
  → `octoagent/apps/gateway/src/octoagent/gateway/services/task_journal.py`（追加到同文件）

### 3.9 Task Journal API 路由（FR-014 端点实现）

- [x] T028 [P0] [US2] [B] 实现 `GET /api/tasks/journal` FastAPI 路由（处理器调用 `TaskJournalService.get_journal()`，EventStore 不可用时返回 503 降级响应 `JOURNAL_DEGRADED`），注意：此路由必须在 `/api/tasks/{task_id}` 之前注册
  → `octoagent/apps/gateway/src/octoagent/gateway/routes/watchdog.py`（新建）

- [x] T029 [P0] [US2] 在路由注册文件中追加 watchdog router，确保注册顺序在 tasks router 之前
  → `octoagent/apps/gateway/src/octoagent/gateway/routes/__init__.py`（或 `main.py`）

- [x] T030 [P0] [US2] 编写 `TaskJournalService` 单元测试（覆盖四分组分类逻辑、`task_status` 使用内部 TaskStatus 不降级为 A2A、`drift_summary` 摘要字段、`drift_artifact_id` 引用字段、空数据库场景）
  → `octoagent/apps/gateway/tests/unit/watchdog/test_task_journal_service.py`（新建）

- [x] T031 [P0] [US2] 编写 `GET /api/tasks/journal` 端点集成测试（`TestClient`，覆盖正常返回 200、分组分类验证、降级响应 503、路由注册顺序验证）
  → `octoagent/apps/gateway/tests/integration/watchdog/test_journal_api.py`（新建）

**Checkpoint**: P0 核心闭环完整 — WatchdogScanner 运行、NoProgressDetector 检测卡死、Task Journal API 可用、满足 GATE-M15-WATCHDOG 验收门禁

---

## Phase 4: User Story 3 — 状态机漂移检测（Priority: P2）

**目标**: 检测非终态任务长时间驻留而产生 `state_machine_stall` 漂移事件，完善漂移覆盖维度

**独立测试**: 创建 RUNNING 任务并设置 `task.updated_at` 为超过 `stale_running_threshold` 之前，执行扫描，验证 `TASK_DRIFT_DETECTED` 事件类型为 `state_machine_stall`

- [x] T032 [P1] [US3] [B] 实现 `StateMachineDriftDetector`（检查任务在非终态的驻留时长是否超过 `stale_running_threshold = no_progress_cycles × scan_interval_seconds`，写入 `state_machine_stall` 漂移，包含 `current_status` 字段，使用内部完整 `TaskStatus` 枚举不降级为 A2A 状态），依赖 T020
  → `octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/detectors.py`（追加实现类）

- [x] T033 [P1] [US3] 编写 `StateMachineDriftDetector` 单元测试（覆盖 RUNNING/QUEUED/WAITING_INPUT/WAITING_APPROVAL/PAUSED 各状态驻留超阈值、驻留未超阈值、终态任务不触发、使用内部 TaskStatus 枚举验证）
  → `octoagent/apps/gateway/tests/unit/watchdog/test_state_drift.py`（新建）

- [x] T034 [P1] [US3] 在 `WatchdogScanner` 的 detectors 列表中注册 `StateMachineDriftDetector()`，依赖 T032
  → `octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/scanner.py`（修改 detectors 注册段）

- [x] T035 [P1] [US3] 在 `main.py` lifespan 的 detectors 列表中追加 `StateMachineDriftDetector()`
  → `octoagent/apps/gateway/src/octoagent/gateway/main.py`

**Checkpoint**: 状态机漂移检测可独立测试，US3 验收场景全覆盖

---

## Phase 5: User Story 4 — 重复失败检测（Priority: P2）

**目标**: 检测短时间内反复失败并重试的任务，产生 `repeated_failure` 漂移事件，防止无效资源消耗

**独立测试**: 向 EventStore 写入同一任务在 300s 内 3 条以上失败事件，执行扫描，验证 `TASK_DRIFT_DETECTED` 类型为 `repeated_failure`，payload 含 `failure_count` 和 `failure_event_types`

- [x] T036 [P1] [US4] [B] 实现 `RepeatedFailureDetector`（失败事件类型集合 `FAILURE_EVENT_TYPES = {MODEL_CALL_FAILED, TOOL_CALL_FAILED, SKILL_FAILED}`，统计 `failure_window_seconds` 内的失败事件数，超过 `repeated_failure_threshold` 时返回 `DriftResult(drift_type="repeated_failure")`，payload 含 `failure_count` 和 `failure_event_types` 统计），依赖 T011、T020
  → `octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/detectors.py`（追加实现类）

- [x] T037 [P1] [US4] 编写 `RepeatedFailureDetector` 单元测试（覆盖失败次数达阈值/未达阈值/不同失败类型组合/时间窗口边界/仅记录日志不写事件的阈值以下场景）
  → `octoagent/apps/gateway/tests/unit/watchdog/test_repeated_failure.py`（新建）

- [x] T038 [P1] [US4] 在 `WatchdogScanner` detectors 列表中注册 `RepeatedFailureDetector()`，依赖 T036
  → `octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/scanner.py`（修改 detectors 注册段）

- [x] T039 [P1] [US4] 在 `main.py` lifespan 的 detectors 列表中追加 `RepeatedFailureDetector()`
  → `octoagent/apps/gateway/src/octoagent/gateway/main.py`

**Checkpoint**: 重复失败检测可独立测试，US4 验收场景全覆盖

---

## Phase 6: User Story 5 — 可配置阈值验收测试（Priority: P2）

**目标**: 验证 `WatchdogConfig.from_env()` 的环境变量覆盖、默认值行为和无效值回退符合 spec 要求

> 注：`WatchdogConfig` 实现已在 T015 中完成，本 Phase 补充 US5 专属的验收测试场景

**独立测试**: 通过环境变量修改 `WATCHDOG_SCAN_INTERVAL_SECONDS` 后实例化 `WatchdogConfig.from_env()`，验证字段值与环境变量一致

- [x] T040 [P1] [US5] 编写 US5 专属验收测试（覆盖以下场景）：
  - 无配置时使用默认值（15/3/60/300/3）
  - 每个 `WATCHDOG_*` 环境变量单独覆盖验证
  - 无效值（负数/零）触发警告日志并回退默认值，不影响启动
  - `no_progress_threshold_seconds` 属性计算正确（`no_progress_cycles × scan_interval_seconds`）
  → `octoagent/apps/gateway/tests/unit/watchdog/test_config.py`（追加 US5 场景到已有文件）

**Checkpoint**: US5 可配置阈值验收通过

---

## Phase 7: User Story 6 — 策略动作审计（Priority: P2，依赖 Policy Engine）

**目标**: Policy Engine 消费 `TASK_DRIFT_DETECTED` 事件并将动作执行结果写入 EventStore，满足可审计可回放要求

> 注：此 Phase 依赖 Policy Engine 侧（`apps/kernel/` 或 `services/policy/`）的实现，是 Watchdog 与 Policy 的集成验收层

**独立测试**: 手动向 EventStore 写入 `TASK_DRIFT_DETECTED` 事件，触发 Policy Engine 动作路由，验证动作执行后产生关联的动作事件记录（含 `drift_event_id`、动作类型、执行时间）

- [ ] T041 [P1] [US6] [SKIP] 实现 `WatchdogActionRouter`（订阅/消费 `TASK_DRIFT_DETECTED` 事件，路由到 `alert`/`demote`/`pause`/`cancel` 四种策略动作，`pause`/`cancel` 走 Plan -> Gate -> Execute 两阶段），依赖 T004、T007
  → Policy Engine 所在模块（`apps/kernel/src/octoagent/kernel/policy/watchdog_action_router.py`，或 `services/policy/` 下，具体路径以 Policy Engine 现有结构为准）
  **SKIP 原因**: 依赖 Policy Engine（apps/kernel/ 或 services/policy/）尚未实现，待后续 Feature 交付后补充

- [ ] T042 [P1] [US6] [SKIP] 实现策略动作执行后的审计事件写入（`alert`/`demote`/`pause`/`cancel` 各自的执行结果事件，包含 `drift_event_id`/`action_type`/`executed_at`/`task_id`/`trace_id`，FR-024），依赖 T041
  → 同 T041 所在文件
  **SKIP 原因**: 依赖 T041，T041 未完成

- [ ] T043 [P1] [US6] [SKIP] 编写 `WatchdogActionRouter` 单元测试（覆盖 alert 动作写入审计事件、pause/cancel 两阶段门控确认、多 drift 事件时每个独立处理）
  → 测试文件路径与 T041 对应的测试目录
  **SKIP 原因**: 依赖 T041，T041 未完成

**Checkpoint**: US6 策略动作可审计可回放，满足 Blueprint 验收标准

---

## Phase 8: User Story 7 — E2E 三场景测试（Priority: P3）

**目标**: 自动化 E2E 测试覆盖卡死/重复失败/状态漂移三种典型失控场景，防止回归

**独立测试**: 运行 E2E 测试套件（不依赖外部 LLM 或真实 Docker，使用 in-memory SQLite + 时间注入）

- [x] T044 [P1] [US7] 实现 E2E 测试——场景 1「卡死检测」：向 in-memory SQLite 写入 RUNNING 任务，注入停止进展事件，等待时间超过 `no_progress_threshold`，验证 `TASK_DRIFT_DETECTED` 事件类型 `no_progress`，携带 `task_id` 和 `trace_id`
  → `octoagent/apps/gateway/tests/e2e/test_watchdog_e2e.py`（新建）

- [x] T045 [P1] [US7] 实现 E2E 测试——场景 2「重复失败检测」：注入 3 条以上 `TASK_DRIFT_DETECTED` 类失败事件（`MODEL_CALL_FAILED` 等），超过 `repeated_failure_threshold`，验证漂移类型 `repeated_failure`，依赖 T036
  → `octoagent/apps/gateway/tests/e2e/test_watchdog_e2e.py`（追加到同文件）

- [x] T046 [P1] [US7] 实现 E2E 测试——场景 3「状态机漂移」：注入 RUNNING 状态长时间驻留任务，超过 `stale_running_threshold`，验证漂移类型 `state_machine_stall`，依赖 T032
  → `octoagent/apps/gateway/tests/e2e/test_watchdog_e2e.py`（追加到同文件）

- [x] T047 [P1] [US7] 实现 E2E 测试——场景 4「进程重启后 cooldown 恢复」：模拟第一次扫描写入 DRIFT 事件，重建新的 `WatchdogScanner` 实例（模拟进程重启），调用 `startup()` 重建 cooldown，验证第二次扫描不重复写入 DRIFT 事件
  → `octoagent/apps/gateway/tests/e2e/test_watchdog_e2e.py`（追加到同文件）

**Checkpoint**: E2E 测试套件全部通过，三场景防回归覆盖完整

---

## Phase 9: Polish & Cross-Cutting Concerns

**目标**: 跨 Phase 的清理、文档和可观测性完善

- [x] T048 [P0] [P] 验证所有新增 `TASK_DRIFT_DETECTED` 事件均携带 `task_id` + `trace_id`（FR-019/FR-020/SC-008），运行对应断言测试
  → 复核 `T024`、`T044`、`T045`、`T046` 中的断言覆盖

- [x] T049 [P0] [P] 验证所有 Watchdog 结构化日志包含 `trace_id` 绑定（FR-008/FR-020），添加缺失的 `structlog.bind_contextvar("trace_id", ...)` 调用
  → `octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/scanner.py`

- [x] T050 [P0] [P] 确认 `TaskDriftDetectedPayload.watchdog_span_id` 字段在 F012 接入前默认为空字符串（FR-021 预留占位），无需修改 schema，添加代码注释说明
  → `octoagent/packages/core/src/octoagent/core/models/payloads.py`

- [x] T051 [P0] 确认 `GET /api/tasks/journal` 路由注册顺序在 `/api/tasks/{task_id}` 之前，添加注释说明原因（contracts/rest-api.md 明确要求）
  → `octoagent/apps/gateway/src/octoagent/gateway/routes/__init__.py`（或 `main.py`）

- [x] T052 [P1] [P] 运行完整测试套件（`pytest`），确认所有 unit/integration 测试通过，无跨 Phase 回归
  → 全局（无特定文件，验证结果即可）

---

## FR 覆盖映射表

> 确认每条 Functional Requirement 至少有一个对应 Task

| FR | 描述摘要 | 对应 Task ID |
|----|---------|-------------|
| FR-001 | 新增 `TASK_HEARTBEAT`/`TASK_MILESTONE`/`TASK_DRIFT_DETECTED` EventType | T004 |
| FR-002 | `TASK_DRIFT_DETECTED` payload 必填诊断字段 | T007, T023 |
| FR-003 | `TASK_HEARTBEAT` payload 字段规范 | T005 |
| FR-004 | Watchdog 周期扫描，进程重启后从 EventStore 重建检测基准 | T023, T025 |
| FR-005 | Watchdog 仅写 DRIFT 信号，不直接执行取消/暂停 | T023 |
| FR-006 | 每任务独立 cooldown 计数器，跨重启从 EventStore 重建 | T017, T023, T024 |
| FR-007 | 扫描失败记录 warning，不影响主任务，等待下次重试 | T023, T024 |
| FR-008 | 每次扫描产生结构化日志（扫描时间/活跃任务数/漂移数/耗时） | T023, T049 |
| FR-009 | 无进展检测使用 7 种进展事件类型，45s 阈值 | T021, T022 |
| FR-010 | `MODEL_CALL_STARTED` 后豁免 LLM 等待期，复用 `no_progress_threshold` | T021, T022 |
| FR-011 | 使用内部完整 `TaskStatus` 枚举，禁止降级为 A2A 状态 | T032, T033 |
| FR-012 | 重复失败检测（300s 窗口，3 次阈值，`repeated_failure` 类型） | T036, T037 |
| FR-013 | 漂移检测跳过终态任务（SUCCEEDED/FAILED/CANCELLED/REJECTED） | T021, T022, T024 |
| FR-014 | Task Journal 查询接口，四分组固定分类 | T026, T028 |
| FR-015 | Journal 每条记录必填字段（task_id/task_status/journal_state/last_event_ts） | T026, T027 |
| FR-016 | 诊断详情走摘要 + artifact 引用，不内联响应体 | T026, T027, T030 |
| FR-017 | `WatchdogConfig` 强类型模型，5 个可配置项及默认值 | T015 |
| FR-018 | 支持 `WATCHDOG_{KEY}` 环境变量，无效值回退默认值 | T015, T016, T040 |
| FR-019 | DRIFT 事件携带 `task_id`/`trace_id`/`watchdog_span_id` | T007, T023, T048 |
| FR-020 | Journal 视图和扫描日志全链路透传 `task_id`/`trace_id` | T023, T049 |
| FR-021 | `watchdog_span_id` 字段 F012 前为空字符串占位 | T007, T050 |
| FR-022 | Policy Engine 消费 DRIFT 事件，支持 alert/demote/pause/cancel 动作 | T041 |
| FR-023 | `pause`/`cancel` 必须走两阶段门控，Watchdog 不直接执行 | T041, T043 |
| FR-024 | 策略动作执行结果写入 EventStore（drift_event_id/action_type/executed_at） | T042 |

**FR 覆盖率**: 24/24 — 100%

---

## 依赖关系与并行说明

### Phase 依赖关系

```
Phase 1 (Setup)
  └── Phase 2 (Foundational) [B]
        ├── Phase 3 (US1+US2: NoProgress + Journal) [B → GATE-M15-WATCHDOG]
        │     ├── Phase 4 (US3: StateMachineDrift)  [P1，可并行于 Phase 5]
        │     ├── Phase 5 (US4: RepeatedFailure)    [P1，可并行于 Phase 4]
        │     ├── Phase 6 (US5: 配置验收测试)        [P1，可并行于 Phase 4/5]
        │     └── Phase 7 (US6: Policy 审计)         [P1，依赖 Policy Engine]
        │           └── Phase 8 (US7: E2E 三场景)   [P3，依赖 Phase 4+5]
        └── Phase 9 (Polish)                         [最后执行]
```

### User Story 间依赖

| User Story | 优先级 | 前置依赖 | 可并行 |
|-----------|-------|---------|--------|
| US1（无进展检测） | P1 | Phase 2 完成 | 无 |
| US2（Task Journal 视图） | P1 | Phase 2 完成，与 US1 同 Phase | 与 US1 一起交付 |
| US3（状态机漂移） | P2 | Phase 3 完成 | 可与 US4/US5 并行 |
| US4（重复失败检测） | P2 | Phase 3 完成 | 可与 US3/US5 并行 |
| US5（可配置阈值） | P2 | T015 完成 | 仅需补充测试，可随时进行 |
| US6（策略动作审计） | P2 | Phase 3 完成 + Policy Engine | 独立 PR |
| US7（E2E 三场景） | P3 | US3 + US4 完成 | E2E 测试顺序无关 |

### Story 内部并行机会

- **Phase 2 内部**: T004~T007（Payload 类）可并行，T009~T011（Store 扩展）需 T009 先行
- **Phase 3 内部**:
  - T015（Config）可与 T019（models）并行
  - T016（Config 测试）与 T018（Cooldown 测试）可并行
  - T022（NoProgress 测试）与 T030（Journal 测试）可并行
- **Phase 4、5 内部**: 检测器实现与测试各自独立，可并行于不同 Phase

### 推荐实现策略

**MVP First（单开发者）**:
1. 完成 Phase 1 + Phase 2（Setup + Foundational）
2. 完成 Phase 3（US1 + US2，NoProgress + Task Journal）
3. **STOP and VALIDATE**：运行 T024 + T031 集成测试，确认 GATE-M15-WATCHDOG 通过
4. 依次交付 Phase 4、5、6、8

**Incremental（推荐）**:
- PR-1: Phase 1 + Phase 2（基础层，独立可合并）
- PR-2: Phase 3（P0 核心闭环，验收门禁）
- PR-3: Phase 4 + 5 + 6（P1 完整性，可单独 PR 或合并）
- PR-4: Phase 8（E2E 测试，P3 质量层）

---

## 注意事项

- `[P]` 标注的任务可以并行开始，不存在文件冲突或依赖
- 测试任务（T008/T012/T014/T016/T018/T022/T024 等）在对应实现任务完成后立即执行
- Phase 3 的 T028 要特别注意路由注册顺序：`/api/tasks/journal` 必须在 `/api/tasks/{task_id}` 之前注册
- Phase 7（US6）依赖 Policy Engine 侧实现，如 Policy Engine 尚未完工，可先跳过并在后续 Feature 中补充
- 所有新增事件类型不得破坏现有事件查询接口的向后兼容性（T004 实现时需验证）
- `stale_running_threshold`（状态机漂移阈值）复用 `no_progress_threshold_seconds`，不引入独立配置项
