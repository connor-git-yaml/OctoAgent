# Feature Specification: Feature 011 — Watchdog + Task Journal + Drift Detector

**特性目录**: `.specify/features/011-watchdog-task-journal`
**特性分支**: `master`
**创建日期**: 2026-03-03
**状态**: Draft
**调研基础**: `research/tech-research.md`（tech-only 模式）

---

## 一句话目标

实现任务运行治理层，通过持久化感知的周期扫描检测无进展与状态漂移任务，产生可审计的信号事件，并为操作者提供可执行的诊断视图，保障长任务不失控。

---

## 背景与价值

OctoAgent 的长任务（LLM 调用链、多步 Skill Pipeline、外部工具执行）在运行过程中存在两类失控风险：

1. **卡死**：任务进入某状态后停止产生进展事件，既不成功也不失败，永远 RUNNING。
2. **漂移**：任务持续产生失败并自动重试，或在异常状态长时间驻留，消耗资源但无实质进展。

当前系统（M1 基线）已有 `TaskRunner._monitor_loop()`，但它是进程内内存监控，进程重启后状态丢失，且不基于持久化事件，不满足 Constitution 原则 1（Durability First）。

Feature 011 在此基础上构建**持久化感知的治理层**：
- 用事件记录所有检测动作（原则 2：Everything is an Event）
- 仅产生信号事件，高风险动作（取消/暂停）由 Policy Engine 门控（原则 4：Side-effect Must be Two-Phase）
- 诊断输出走摘要 + artifact 引用（原则 11：上下文卫生）
- 进程重启后扫描即恢复，无检测盲窗（原则 1：Durability First）

---

## 参与者（Actors）

| 参与者 | 角色描述 |
|-------|---------|
| **操作者**（用户） | 通过 Task Journal 查看任务健康状态，接收告警通知，决策是否手动干预 |
| **Watchdog Scanner** | 系统自动化角色，周期性扫描任务状态并产生信号事件 |
| **Policy Engine** | 消费 DRIFT 信号事件，根据策略配置决定执行提醒/降级/暂停/取消动作 |
| **Worker** | 在执行关键节点主动写入 HEARTBEAT 和 MILESTONE 事件 |

---

## User Stories

### User Story 1 — 操作者发现无进展任务（Priority: P1）

作为系统操作者，当某个 Worker 任务由于程序错误或外部依赖阻塞而陷入卡死状态时，我希望系统能在规定时间内自动检测到并通知我，让我得以及时介入，而不是等到无限期超时。

**优先级理由**: 这是 Feature 011 的核心价值交付。无进展检测是阻塞 M1.5 验收的"watchdog 告警"验收条目。若该 Story 不可用，Watchdog 对操作者毫无意义。

**独立测试**: 可通过以下独立场景测试：在 EventStore 中写入一个 RUNNING 任务并停止写入进展事件；等待超过 `no_progress_threshold`（3×15s=45s）；验证 TASK_DRIFT_DETECTED 事件已被写入，事件 payload 中包含诊断摘要。无需 Policy Engine 参与即可完整验证。

**验收场景**:

1. **Given** 一个 RUNNING 状态的任务在最近 45 秒内未产生任何进展类事件（包括 MODEL_CALL_STARTED/COMPLETED、TOOL_CALL_STARTED/COMPLETED、TASK_HEARTBEAT、TASK_MILESTONE、CHECKPOINT_SAVED），**When** Watchdog Scanner 执行周期扫描（间隔 15s），**Then** 系统向 EventStore 写入 `TASK_DRIFT_DETECTED` 事件，事件 payload 包含：检测触发时间、漂移类型（`no_progress`）、距最近进展事件的时间差、可执行的建议动作列表。

2. **Given** 一个被标记为漂移的任务，**When** 系统在 cooldown 窗口（60s）内再次扫描到同一任务仍无进展，**Then** 系统不重复写入 DRIFT 事件（防抖机制生效），仅更新结构化日志。

3. **Given** 一个任务在 MODEL_CALL_STARTED 事件之后、在 `no_progress_threshold` 内未产生 MODEL_CALL_COMPLETED，**When** Watchdog 扫描，**Then** 系统不触发漂移告警（识别并排除"等待 LLM 响应"的合理等待窗口）。[AUTO-RESOLVED: 调研结论明确指出 LLM 等待期是最高优先级误报缓解项，应在漂移检测中显式排除此窗口]

4. **Given** 系统进程重启后，**When** Watchdog Scanner 随 lifespan 启动，**Then** 扫描能从持久化 EventStore 中重建检测基准，无检测盲窗，不依赖进程内内存状态。

---

### User Story 2 — 操作者通过 Task Journal 获取任务健康全景（Priority: P1）

作为系统操作者，当系统同时运行多个任务时，我希望能通过统一的 Task Journal 视图快速了解哪些任务运行正常、哪些疑似卡死、哪些处于漂移状态、哪些在等待我的审批，而不需要逐一查看每个任务的原始事件流。

**优先级理由**: Task Journal 是 Watchdog 检测结果的可视化入口，是操作者感知系统健康的主要界面。与无进展检测同属 P1，共同构成 MVP 最小可行产品。

**独立测试**: 调用 Task Journal API 端点，验证返回结构包含四个分组（running、stalled、drifted、waiting_approval），每个分组包含对应状态的任务列表及关键诊断字段。可使用测试数据在各状态下验证分组正确性。

**验收场景**:

1. **Given** EventStore 和 TaskStore 中存在处于不同状态的任务（RUNNING 正常、RUNNING 卡死、有 DRIFT 事件、WAITING_APPROVAL），**When** 操作者请求 Task Journal 视图，**Then** 系统返回按四个分组分类的任务列表：`running`（正常运行中）、`stalled`（疑似卡死，超过 `no_progress_threshold`）、`drifted`（存在 DRIFT_DETECTED 事件）、`waiting_approval`（WAITING_APPROVAL 状态）。

2. **Given** Task Journal 中的某个漂移任务，**When** 操作者查看其诊断详情，**Then** 系统返回摘要形式的诊断说明（漂移类型、持续时长、建议动作），详细诊断信息通过 artifact 引用访问，不直接内联在 API 响应中（符合上下文卫生原则）。

3. **Given** 一个任务从漂移状态恢复正常进展（写入了新的 TASK_HEARTBEAT 事件），**When** 操作者刷新 Task Journal，**Then** 该任务从 `drifted` 分组移回 `running` 分组。

---

### User Story 3 — 操作者通过 Task Journal 感知状态机漂移（Priority: P2）

作为系统操作者，当某个任务长时间停留在非终态（如 RUNNING、WAITING_INPUT、WAITING_APPROVAL）且无任何行为表现时，我希望系统能识别为"状态机漂移"并告警，以便我排查是否有代码逻辑 bug 或外部依赖失联。

**优先级理由**: 状态机漂移是无进展检测之外的补充检测维度，覆盖"任务状态本身异常驻留"的场景，优先级略低于直接卡死检测，但属于完整漂移治理的必要组成。

**独立测试**: 创建一个 RUNNING 任务，将 `task.updated_at` 设置为超过 `stale_running_threshold`（3 个扫描周期，即 45s）之前，执行 Watchdog 扫描，验证产生 `TASK_DRIFT_DETECTED` 事件，漂移类型为 `state_machine_stall`。

**验收场景**:

1. **Given** 一个任务处于 RUNNING/QUEUED/WAITING_INPUT/WAITING_APPROVAL/PAUSED 等非终态，且该状态的驻留时长超过 `stale_running_threshold`（默认 3 个扫描周期），**When** Watchdog 扫描，**Then** 系统写入 `TASK_DRIFT_DETECTED` 事件，漂移类型为 `state_machine_stall`，并记录当前状态名称和驻留时长。

2. **Given** 状态机漂移检测，**When** 检测器判断任务状态，**Then** 检测器使用完整的内部 TaskStatus 枚举集合（CREATED/RUNNING/QUEUED/WAITING_INPUT/WAITING_APPROVAL/PAUSED 等），不降级为 A2A 状态的二元划分（如 active/pending）。

3. **Given** 一个终态任务（SUCCEEDED/FAILED/CANCELLED/REJECTED），**When** Watchdog 扫描，**Then** 系统跳过该任务，不产生任何漂移事件。

---

### User Story 4 — 操作者通过 Task Journal 感知重复失败模式（Priority: P2）

作为系统操作者，当某个任务在短时间内反复遭遇失败并不断重试时，我希望系统能识别为"重复失败漂移"并告警，以防止无效资源消耗和潜在的外部 API 滥用。

**优先级理由**: 重复失败模式是第三种漂移形态，防止任务消耗过多资源做无意义重试，完整漂移覆盖的必要环节。可在无进展检测（P1）和状态机漂移（P2）稳定后独立交付。

**独立测试**: 在 EventStore 中写入同一任务的多条失败类事件（MODEL_CALL_FAILED、TOOL_CALL_FAILED、SKILL_FAILED），数量超过阈值（默认 5 分钟内 3 次），执行 Watchdog 扫描，验证 DRIFT 事件产生且漂移类型为 `repeated_failure`。

**验收场景**:

1. **Given** 一个任务在最近 300 秒内产生了 3 条或以上失败类事件（MODEL_CALL_FAILED、TOOL_CALL_FAILED、SKILL_FAILED），**When** Watchdog 扫描，**Then** 系统写入 `TASK_DRIFT_DETECTED` 事件，漂移类型为 `repeated_failure`，payload 包含失败次数和失败事件类型统计。

2. **Given** 一个任务的失败次数处于阈值以下，**When** Watchdog 扫描，**Then** 系统不产生漂移事件，但在结构化日志中记录当前失败计数（用于趋势观测）。

---

### User Story 5 — 操作者配置 Watchdog 阈值（Priority: P2）

作为系统操作者，我希望能通过配置文件或环境变量调整 Watchdog 的检测阈值（扫描间隔、无进展窗口、cooldown 时长），而不需要修改代码，以便针对不同任务类型（快响应任务 vs 长耗时任务）优化告警灵敏度。

**优先级理由**: 可配置阈值是防止告警疲劳的重要机制，且 Blueprint 验收标准明确要求"默认阈值生效"并支持配置。可在核心检测器稳定后独立交付。

**独立测试**: 通过环境变量修改 `WATCHDOG_SCAN_INTERVAL_SECONDS`，启动 Watchdog，验证其扫描频率与配置值一致。验证默认值（15s/45s/60s）在无配置时生效。

**验收场景**:

1. **Given** 系统未设置任何 Watchdog 配置，**When** Watchdog Scanner 启动，**Then** 使用默认阈值：扫描间隔 15 秒、无进展阈值 45 秒（3 个周期）、cooldown 60 秒。

2. **Given** 操作者通过环境变量设置了自定义阈值，**When** Watchdog Scanner 启动，**Then** 自定义阈值覆盖默认值，配置项包含：`WATCHDOG_SCAN_INTERVAL_SECONDS`、`WATCHDOG_NO_PROGRESS_CYCLES`、`WATCHDOG_COOLDOWN_SECONDS`。

3. **Given** 配置了无效值（如负数或零），**When** 系统启动，**Then** Watchdog 使用默认值并在日志中记录配置修正警告，不因无效配置导致启动失败。

---

### User Story 6 — 策略动作的可审计性（Priority: P2）

作为系统操作者，当 Policy Engine 基于 Watchdog 漂移信号执行了提醒、降级、暂停或取消动作时，我希望这些动作全部可追溯、可回放，以便事后分析 Watchdog 是否产生了误动作。

**优先级理由**: 可审计性是 Constitution 原则 8（Observability is a Feature）和 Blueprint 验收标准（"告警动作可审计可回放"）的直接要求，但此 Story 依赖 Policy Engine 侧的实现，属于 Watchdog 与 Policy 的集成验收层。

**独立测试**: 在 EventStore 中手动写入一个 TASK_DRIFT_DETECTED 事件，触发 Policy Engine 动作路由，验证动作执行后产生了关联的动作事件记录（包含 drift_event_id、动作类型、执行时间），且该事件通过 TaskStore 的事件查询接口可检索。

**验收场景**:

1. **Given** Watchdog 产生了 TASK_DRIFT_DETECTED 事件，**When** Policy Engine 消费该事件并执行提醒动作，**Then** 系统写入策略动作事件，该事件包含：关联的 drift_event_id、执行的动作类型、执行时间、关联 task_id 和 trace_id。

2. **Given** Watchdog 产生 TASK_DRIFT_DETECTED 事件，**When** Policy Engine 决策执行高风险动作（暂停/取消），**Then** 该动作必须经过 Policy Engine 的门控流程（两阶段：检测信号产生 -> Policy 门控 -> 动作执行），Watchdog Scanner 自身不直接执行取消或暂停操作。

3. **Given** 历史时间段内发生的漂移告警及后续动作，**When** 操作者通过事件查询接口检索相关事件，**Then** 能完整重现：漂移检测触发 -> Policy 决策 -> 动作执行的完整事件链。

---

### User Story 7 — E2E 验收场景（Priority: P3）

作为开发团队，我希望通过自动化 E2E 测试覆盖卡死、重复失败、状态漂移三种典型失控场景，确保 Watchdog 在各场景下均能正确检测和告警，防止回归。

**优先级理由**: E2E 测试是 Blueprint 验收条目的直接要求（F011-T06），保证上述所有 Story 在完整运行环境中端到端可验证。属于质量门控层，在核心 Story 实现后交付。

**独立测试**: 运行 E2E 测试套件，不依赖外部 LLM 或真实 Docker 环境，通过 in-memory SQLite 和时间注入模拟各种失控场景。

**验收场景**:

1. **Given** 测试中注入一个停止产生进展事件的 RUNNING 任务（模拟卡死），**When** 等待时间超过 `no_progress_threshold`，**Then** 测试验证 TASK_DRIFT_DETECTED 事件被写入且类型为 `no_progress`。

2. **Given** 测试中注入一个反复产生失败事件的任务（模拟失败循环），**When** 失败次数超过 `repeated_failure_threshold`，**Then** 测试验证 TASK_DRIFT_DETECTED 事件被写入且类型为 `repeated_failure`。

3. **Given** 测试中注入一个在 RUNNING 状态长时间驻留的任务（模拟状态漂移），**When** 驻留时长超过 `stale_running_threshold`，**Then** 测试验证 TASK_DRIFT_DETECTED 事件被写入且类型为 `state_machine_stall`。

---

### Edge Cases

- **边界情况 1：任务在检测窗口内恰好完成** — 若任务在 Watchdog 即将触发 DRIFT 事件的同时写入了终态事件（SUCCEEDED/FAILED），Watchdog 必须识别终态并跳过，不产生漂移事件。（关联 FR-003）

- **边界情况 2：Watchdog 自身扫描失败** — 若某次扫描抛出异常（如 SQLite BUSY），扫描失败不影响主任务执行，Watchdog 记录 log.warning，等待下一个扫描周期重试，不累积重试。（关联 FR-007）

- **边界情况 3：大量任务同时漂移** — 若多个任务同时进入漂移状态，系统为每个任务独立写入 DRIFT 事件，每个任务独立维护 cooldown 计数器，不会因批量写入导致 EventStore 竞争死锁。（关联 FR-006）

- **边界情况 4：新增事件类型的历史兼容** — 在旧版本 EventStore 数据中不存在 TASK_HEARTBEAT 事件，漂移检测需降级为使用 task_jobs.updated_at 作为时间参照，不因缺少 HEARTBEAT 事件而误报。（关联 FR-002）

- **边界情况 5：MODEL_CALL_STARTED 之后的等待期** — 任务在等待 LLM 响应期间不应被判定为卡死，即使该期间超过 `no_progress_threshold`。检测器必须将 MODEL_CALL_STARTED 之后、MODEL_CALL_COMPLETED 之前的时间段视为合法等待期排除。（关联 FR-004）

- **边界情况 6：cooldown 防抖与跨重启一致性** — 进程重启后，cooldown 计数器需从 EventStore 中重建（通过查询最近一次 DRIFT 事件时间戳），不能因重启而让 cooldown 失效，导致连续告警轰炸。（关联 FR-009）

---

## Functional Requirements

### 核心事件与数据契约

- **FR-001** [MUST]: 系统必须在 EventType 枚举中新增三种事件类型：`TASK_HEARTBEAT`（Worker 心跳确认）、`TASK_MILESTONE`（里程碑完成标记）、`TASK_DRIFT_DETECTED`（漂移检测告警）。新增事件类型不得破坏现有事件查询接口的向后兼容性。（对应 F011-T01）

- **FR-002** [MUST]: `TASK_DRIFT_DETECTED` 事件的 payload 必须包含以下字段：`drift_type`（枚举：`no_progress` / `state_machine_stall` / `repeated_failure`）、`detected_at`（检测时间）、`task_id`、`trace_id`、`last_progress_ts`（距最近进展事件时间戳）、`stall_duration_seconds`（卡死持续时长）、`suggested_actions`（可执行建议动作列表，字符串数组）。详细诊断信息通过 artifact 引用存储，不直接内联于 payload。（Constitution 原则 11）

  [例外] 当 `drift_type` 为 `state_machine_stall` 时，`last_progress_ts` 允许为 None——此时 `stall_duration_seconds` 基于 `task.updated_at`（任务最后状态更新时间）计算，与边界情况 4 保持一致。

- **FR-003** [MUST]: `TASK_HEARTBEAT` 事件的 payload 必须包含：`task_id`、`trace_id`、`loop_step`（当前执行步骤编号，可选）、`heartbeat_ts`。写入时间戳必须使用服务端 UTC 时间，不依赖客户端时间。

### Watchdog Scanner

- **FR-004** [MUST]: Watchdog Scanner 必须通过持久化感知的周期扫描运行，扫描周期基于可配置参数（默认 15 秒），进程重启后自动恢复扫描，检测基准从 EventStore 和 TaskStore 重建，不依赖进程内内存状态。（Constitution 原则 1）

- **FR-005** [MUST]: Watchdog Scanner 在检测到漂移时，必须且只能向 EventStore 追加 `TASK_DRIFT_DETECTED` 事件，绝对不得直接执行任务的暂停、取消或任何状态变更操作。所有高风险动作必须由 Policy Engine 门控执行。（Constitution 原则 4）

- **FR-006** [MUST]: Watchdog Scanner 必须为每个任务维护独立的 cooldown 计数器（默认 60 秒），在 cooldown 窗口内不对同一任务重复写入 DRIFT 事件。cooldown 基准时间从 EventStore 中最近一次 DRIFT 事件时间戳重建，保证跨重启一致性。

- **FR-007** [MUST]: Watchdog Scanner 扫描失败（如 SQLite 繁忙、查询超时）时，必须记录结构化警告日志并跳过本次扫描，等待下一周期重试。扫描失败不得导致主任务执行中断或 Watchdog 进程退出。（Constitution 原则 6）

- **FR-008** [MUST]: Watchdog Scanner 的每次扫描执行必须产生结构化日志记录，包含：扫描触发时间、检查的活跃任务数量、本次检测到的漂移任务数量、扫描耗时。相关日志必须绑定 trace_id。（Constitution 原则 8）

### 漂移检测器

- **FR-009** [MUST]: 无进展检测器（No-Progress Detector）必须使用以下进展事件类型作为判断基准：`MODEL_CALL_STARTED`、`MODEL_CALL_COMPLETED`、`TOOL_CALL_STARTED`、`TOOL_CALL_COMPLETED`、`TASK_HEARTBEAT`、`TASK_MILESTONE`、`CHECKPOINT_SAVED`。若任务在 `no_progress_threshold`（默认 45 秒，等于 3 个扫描周期）内无上述任何事件，则判定为无进展漂移。（对应 F011-T03）

- **FR-010** [MUST]: 无进展检测器必须显式排除"合法 LLM 等待期"：若任务最近事件为 `MODEL_CALL_STARTED`，且等待时长未超过 LLM 等待期豁免窗口，则不触发无进展漂移。LLM 等待期豁免窗口（`model_call_wait`）复用 `no_progress_threshold` 的值，不引入独立配置项。即：当检测到 `MODEL_CALL_STARTED` 事件后，豁免窗口 = `no_progress_threshold` 当前配置值（默认 45 秒）。实现者无需在 `WatchdogConfig` 中新增字段。

- **FR-011** [MUST]: 状态机漂移检测器（State Machine Drift Detector）必须使用 OctoAgent 完整的内部 TaskStatus 枚举集合进行判断，禁止降级为 A2A 标准状态（如 active/pending 二元划分）。完整非终态集合为：`CREATED`、`RUNNING`、`QUEUED`、`WAITING_INPUT`、`WAITING_APPROVAL`、`PAUSED`。（Constitution 原则 14，F011 Constitution WARNING 3）

  **F011 漂移检测范围说明**：F011 漂移检测范围限定为当前有实际消费者的非终态状态：`CREATED`、`RUNNING`、`WAITING_APPROVAL`。`QUEUED`、`WAITING_INPUT`、`PAUSED` 在 F011 范围内为声明性覆盖——若任务进入这些状态则同样监控其驻留时长，但这些状态需要后续 Feature 激活消费者后才有实际意义。

- **FR-012** [MUST]: 重复失败检测器（Repeated Failure Detector）必须统计任务在 `failure_window_seconds`（默认 300 秒）内的失败类事件数量，失败事件类型包含：`MODEL_CALL_FAILED`、`TOOL_CALL_FAILED`、`SKILL_FAILED`。当计数达到 `repeated_failure_threshold`（默认 3 次）时，写入漂移事件，类型为 `repeated_failure`。（对应 F011-T04）

- **FR-013** [MUST]: 漂移检测器必须跳过处于终态（`SUCCEEDED`、`FAILED`、`CANCELLED`、`REJECTED`）的任务，不产生任何检测或告警事件。

### Task Journal 视图

- **FR-014** [MUST]: 系统必须提供 Task Journal 查询接口，返回当前所有非终态任务按健康状态分组的视图。分组固定为四类：`running`（正常运行中）、`stalled`（疑似卡死）、`drifted`（已检测到漂移事件）、`waiting_approval`（WAITING_APPROVAL 状态）。（对应 F011-T02）

- **FR-015** [MUST]: Task Journal 接口的每条任务记录必须包含：`task_id`、`task_status`（使用完整内部 TaskStatus，不映射为 A2A 状态）、`journal_state`（四类分组标签）、`last_event_ts`（最近事件时间）、`drift_summary`（若存在漂移，包含漂移类型和持续时长摘要）、`suggested_actions`（建议动作列表）。

- **FR-016** [SHOULD]: Task Journal 接口的诊断详情必须遵循"摘要 + artifact 引用"模式：API 响应中只包含摘要字段，完整的诊断详情（事件历史、失败原因分析、详细建议）通过 artifact_id 引用访问，不直接内联于响应体。（Constitution 原则 11）

### 可配置阈值

- **FR-017** [MUST]: Watchdog 配置必须以强类型配置模型定义，支持以下可配置项，并提供明确的默认值：

  | 配置项 | 默认值 | 说明 |
  |--------|--------|------|
  | `scan_interval_seconds` | 15 | Watchdog 扫描周期 |
  | `no_progress_cycles` | 3 | 无进展判定周期数（实际阈值 = cycles × interval） |
  | `cooldown_seconds` | 60 | 同一任务漂移告警 cooldown 时长 |
  | `failure_window_seconds` | 300 | 重复失败统计时间窗口 |
  | `repeated_failure_threshold` | 3 | 重复失败触发漂移的次数阈值 |

- **FR-018** [SHOULD]: 上述配置项应支持通过环境变量覆盖默认值，遵循 `WATCHDOG_{KEY}` 命名规范。配置加载时若遇到无效值（如非正整数），记录警告日志并回退到默认值，不影响系统启动。

### 可观测性要求

- **FR-019** [MUST]: 所有 Watchdog 产生的 `TASK_DRIFT_DETECTED` 事件必须携带完整的可追溯字段：`task_id`、`trace_id`（继承被检测任务的 trace_id）、`span_id`（当前 Watchdog 扫描 span，预留字段，F012 接入后填充）。（Constitution 原则 2 和原则 8）

- **FR-020** [MUST]: Task Journal 视图及 Watchdog 扫描日志必须保证 `task_id` 和 `trace_id` 全链路透传，作为 M1.5 集成验收（Feature 013）的 watchdog 告警链路可观测性要求的验收基准。

- **FR-021** [SHOULD]: Watchdog Scanner 应预留与 Feature 012（Logfire 接入）的集成接口：DRIFT 事件 payload 中的 `watchdog_span_id` 字段在 F012 实装前保持空字符串占位，F012 接入后填充实际 Logfire span_id，不需要修改事件 schema。

### 策略动作（Policy Engine 侧）

- **FR-022** [MUST]: Policy Engine 必须能消费 `TASK_DRIFT_DETECTED` 事件并支持以下策略动作：`alert`（结构化日志告警）、`demote`（降低任务优先级或资源配额）、`pause`（迁移任务到 PAUSED 状态）、`cancel`（终止任务到 CANCELLED 状态）。（对应 F011-T05）

- **FR-023** [MUST]: `pause` 和 `cancel` 属于高风险动作，Policy Engine 执行前必须通过 Plan -> Gate -> Execute 两阶段模式，或等待用户确认。Watchdog Scanner 本身不得直接调用这两种动作。（Constitution 原则 4 和原则 7）

- **FR-024** [SHOULD]: 策略动作执行结果（无论成功或失败）必须写入 EventStore 作为独立事件，包含：关联的 `drift_event_id`、执行的动作类型、执行时间、执行结果。保证告警动作可审计可回放。（Blueprint F011 验收标准）

---

## Key Entities

- **WatchdogConfig**（配置实体）: 承载所有可配置阈值，包含 `scan_interval_seconds`、`no_progress_cycles`、`cooldown_seconds`、`failure_window_seconds`、`repeated_failure_threshold`。支持从环境变量加载并校验有效性。

- **DriftResult**（检测结果值对象）: Watchdog 检测器产生的中间结果，包含 `task_id`、`drift_type`（枚举）、`detected_at`、`last_progress_ts`、`stall_duration_seconds`、`suggested_actions`。用于传递给 DRIFT 事件写入器，不持久化。

- **TaskJournalEntry**（Journal 视图记录）: Task Journal API 的返回单元，包含 `task_id`、`task_status`（内部 TaskStatus）、`journal_state`（四类分组标签）、`last_event_ts`、`drift_summary`（可选摘要）、`drift_artifact_id`（详细诊断 artifact 引用，可选）、`suggested_actions`。

- **CooldownRegistry**（防抖注册表）: 记录每个 task_id 最近一次 DRIFT 事件的写入时间，用于 cooldown 判断。进程重启后从 EventStore 最近 DRIFT 事件时间戳重建。

- **SqliteTaskStore 接口扩展**（实现约束）: F011 实现时需向 `SqliteTaskStore` 新增 `list_tasks_by_statuses(statuses: list[TaskStatus]) -> list[Task]` 接口，保持原 `list_tasks(status: str | None)` 接口向下兼容。此接口是 Task Journal 投影视图（FR-014/FR-015）的查询基础，禁止用串行多次调用 `list_tasks()` 替代（存在竞态窗口）。

---

## Success Criteria

### 可测量结果

- **SC-001**: Watchdog 在任务停止产生进展事件后，在 `no_progress_threshold`（默认 45 秒）以内检测到无进展并写入 TASK_DRIFT_DETECTED 事件，检测延迟不超过 1 个扫描周期（15 秒）。

- **SC-002**: TASK_DRIFT_DETECTED 事件的 payload 包含明确的诊断摘要和至少一条可执行的建议动作，操作者可依据此信息独立决策是否介入。

- **SC-003**: 告警动作（alert/demote/pause/cancel）执行后，均在 EventStore 中生成可检索的关联事件记录，满足"可审计可回放"要求。

- **SC-004**: 默认阈值在系统无任何配置的情况下生效：扫描间隔 15 秒、无进展阈值 45 秒、cooldown 60 秒，并通过 E2E 测试验证。

- **SC-005**: Watchdog Scanner 扫描失败（模拟 SQLite BUSY）后，下一周期自动恢复扫描，期间不影响任何正在运行的任务。

- **SC-006**: 系统进程重启后，Watchdog 在首次扫描时能从 EventStore 正确重建检测基准，不因重启产生检测盲窗（可通过 E2E 测试中模拟进程重启场景验证）。

- **SC-007**: Task Journal API 在活跃任务数量在 MVP 量级（数十至百级）时，响应时间在可接受范围内（不超过 2 秒），不因 Journal 查询影响主任务执行性能。

- **SC-008**: 所有 TASK_DRIFT_DETECTED 和关联动作事件均携带 `task_id` 和 `trace_id`，满足 F013（M1.5 E2E 集成验收）的全链路可观测性门禁（GATE-M15-WATCHDOG）。

---

## 约束与边界

### Constitution 强制约束（规范中明确体现的要求）

| Constitution 条目 | 在本 Spec 中的体现 |
|-----------------|------------------|
| 原则 1（Durability First） | FR-004 要求进程重启后从 EventStore 重建检测基准，FR-006 要求 cooldown 跨重启一致 |
| 原则 2（Everything is an Event） | FR-001～FR-003 新增三种事件类型；FR-019 要求所有 DRIFT 事件持久化 |
| 原则 4（Side-effect Must be Two-Phase） | FR-005 严禁 Watchdog 直接执行动作；FR-023 要求 pause/cancel 走两阶段门控 |
| 原则 6（Degrade Gracefully） | FR-007 要求扫描失败不影响主任务执行，自动重试 |
| 原则 7（User-in-Control） | FR-023 要求高风险动作须用户确认或 Policy 门控 |
| 原则 8（Observability is a Feature） | FR-008、FR-019、FR-020 要求所有检测动作可观测、可追溯 |
| 原则 11（上下文卫生） | FR-002、FR-016 要求诊断输出走摘要 + artifact 引用，不内联长内容 |
| 原则 14（A2A 兼容） | FR-011 要求使用内部完整 TaskStatus，禁止降级为 A2A 状态 |

### 功能范围边界

- **范围内（WHAT）**: 新增事件类型定义、周期扫描检测逻辑、三种漂移算法、Task Journal 查询视图、可配置阈值、事件写入与可观测性、cooldown 防抖。
- **范围外（非 WHAT 的 HOW）**: 具体使用哪种调度框架实现扫描（不在 spec 中规定）、EventStore 查询接口的 SQL 实现细节、APScheduler job 注册方式——这些属于实现层。
- **范围外（功能）**: Policy Engine 的完整动作路由实现（Policy 侧由 Policy Engine 负责，Watchdog 侧只负责产生信号）；Task Journal 的物化视图实现（MVP 阶段为实时聚合）；事件频率统计检测器（留待 M2 历史数据积累后引入）。

---

## 歧义与待确认项

所有歧义已基于调研结论和 Constitution 约束自动解决，共 2 处 [AUTO-RESOLVED]：

1. **[AUTO-RESOLVED: LLM 等待期排除策略]** — 无进展检测中，MODEL_CALL_STARTED 之后的等待期是否应排除：选择排除（见 FR-010），理由为调研报告明确标识为"最高优先级缓解项"，Blueprint 阈值 45s 对单次 LLM 调用可能误报。

2. **[AUTO-RESOLVED: Task Journal 实现方式]** — 选择实时聚合（Query-time Projection）而非物化视图（task_journal 表），理由为 MVP 阶段任务量级可接受实时聚合开销，符合 EventStore append-only 哲学，升级路径明确（活跃任务 > 200 时迁移物化视图）。

---

## 实现优先级建议（P0/P1/P2 分层）

本节仅供实现阶段参考，不属于功能规范范畴。

| 优先级 | 功能范围 | 对应 FR |
|--------|---------|---------|
| P0（核心，阻塞验收） | 新增三种 EventType；Watchdog Scanner 核心扫描逻辑；无进展检测器；Task Journal 查询接口 | FR-001～FR-010, FR-013～FR-015, FR-017～FR-020 |
| P1（完整性，验收后补充） | 状态机漂移检测；重复失败检测；Policy Engine 动作消费与审计事件 | FR-011, FR-012, FR-022～FR-024 |
| P2（增强，可选） | Logfire F012 集成 span_id 填充；物化视图升级；事件频率统计检测器 | FR-021（部分） |
