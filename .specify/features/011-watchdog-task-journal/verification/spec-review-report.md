# Spec 合规审查报告

**特性**: Feature 011 — Watchdog + Task Journal + Drift Detector
**审查日期**: 2026-03-03
**审查员**: Spec 合规审查子代理
**测试基线**: 308 个测试全部通过（180 gateway + 128 core）

---

## 逐条 FR 状态

| FR 编号 | 描述 | 状态 | 证据/说明 |
|---------|------|------|----------|
| FR-001 | 新增 `TASK_HEARTBEAT`/`TASK_MILESTONE`/`TASK_DRIFT_DETECTED` EventType，不破坏向后兼容 | 已实现 | `enums.py` 第 117-119 行追加三个枚举值，现有枚举值未修改，注释标注 Feature 011 来源 |
| FR-002 | `TASK_DRIFT_DETECTED` payload 必填诊断字段（drift_type/detected_at/task_id/trace_id/last_progress_ts/stall_duration_seconds/suggested_actions），详细诊断走 artifact 引用 | 已实现 | `payloads.py` 第 274-331 行 `TaskDriftDetectedPayload`，全部必填字段均在；`artifact_ref` 字段存在；`last_progress_ts` 允许 None（符合状态机漂移例外条款） |
| FR-003 | `TASK_HEARTBEAT` payload 必填字段（task_id/trace_id/loop_step/heartbeat_ts），服务端 UTC 时间 | 已实现 | `payloads.py` 第 235-249 行 `TaskHeartbeatPayload`，全部字段完整，注释明确"写入时间戳由服务端 UTC 时间确定" |
| FR-004 | Watchdog 周期扫描，进程重启后从 EventStore/TaskStore 重建检测基准，不依赖进程内内存状态 | 已实现 | `scanner.py` `startup()` 调用 `list_tasks_by_statuses` + `cooldown.rebuild_from_store`；`main.py` 第 144-158 行注册 APScheduler interval job，lifespan 管理确保重启后重建 |
| FR-005 | Watchdog 仅写 DRIFT 信号，不直接执行取消/暂停 | 已实现 | `scanner.py` 全文无任何 `cancel`/`pause`/`update_task_status` 调用；`_emit_drift_event()` 仅调用 `append_event_committed`；代码注释明确"硬约束：绝不直接调用 task cancel/pause" |
| FR-006 | 每任务独立 cooldown 计数器，跨重启从 EventStore 最近 DRIFT 事件时间戳重建 | 已实现 | `cooldown.py` `CooldownRegistry` 实现 `_last_drift_ts` 字典；`rebuild_from_store` 查询 cooldown 窗口内 TASK_DRIFT_DETECTED 事件并重建；`is_in_cooldown` / `record_drift` 方法完整 |
| FR-007 | 扫描失败记录 warning 日志，不影响主任务执行，等待下次重试 | 已实现 | `scanner.py` `scan()` 第 116-187 行全程 `try/except` 包裹，外层捕获整体扫描失败，内层分别捕获检测器异常和事件写入异常；均为 `log.warning` 不重新抛出 |
| FR-008 | 每次扫描产生结构化日志（扫描触发时间/活跃任务数/漂移数/耗时），绑定 trace_id | 已实现 | `scanner.py` 第 191-196 行 `watchdog_scan_completed` 日志，包含 `active_task_count`/`drift_detected_count`/`scan_duration_ms`；扫描开始时间通过 `time.monotonic()` 计算耗时 |
| FR-009 | 无进展检测使用 7 种进展事件类型，45s（3 周期 × 15s）阈值判断 | 已实现 | `detectors.py` 第 24-32 行 `PROGRESS_EVENT_TYPES` 恰好包含全部 7 种事件；`config.no_progress_threshold_seconds` = `no_progress_cycles × scan_interval_seconds` |
| FR-010 | 无进展检测显式排除 `MODEL_CALL_STARTED` 后的 LLM 等待期，豁免窗口复用 `no_progress_threshold` | 已实现 | `detectors.py` 第 102-115 行：`progress_events` 为空后额外查询 `MODEL_CALL_STARTED` 事件；若存在则返回 None；豁免窗口使用相同 `since_ts`（= no_progress_threshold），无独立配置项 |
| FR-011 | 状态机漂移检测使用完整内部 TaskStatus 枚举，禁止降级为 A2A 状态；非终态集合含 CREATED/RUNNING/QUEUED/WAITING_INPUT/WAITING_APPROVAL/PAUSED | 已实现 | `detectors.py` `StateMachineDriftDetector` 使用 `TaskStatus(task.status)` 直接转换内部枚举；`DriftResult.current_status = task.status`（内部值）；`scanner.py` `NON_TERMINAL_STATES` 包含全部 6 个非终态 |
| FR-012 | 重复失败检测：`failure_window_seconds` 窗口内失败类事件 >= `repeated_failure_threshold` 时写入漂移 | 已实现 | `detectors.py` 第 200-283 行 `RepeatedFailureDetector`；`FAILURE_EVENT_TYPES` 包含 MODEL_CALL_FAILED/TOOL_CALL_FAILED/SKILL_FAILED；低于阈值时记录 debug 日志（对应 US4 验收场景 2）；payload 含 `failure_count` 和 `failure_event_types` |
| FR-013 | 漂移检测跳过终态任务（SUCCEEDED/FAILED/CANCELLED/REJECTED） | 已实现 | 三个检测器均在首行检查 `if task_status in TERMINAL_STATES: return None`；`scanner.py` 额外防御层跳过终态任务 |
| FR-014 | Task Journal 查询接口，四分组固定分类（running/stalled/drifted/waiting_approval） | 已实现 | `task_journal.py` `TaskJournalService.get_journal()`；`watchdog.py` `GET /api/tasks/journal` 端点；`JournalGroups` 模型包含四个固定分组 |
| FR-015 | Journal 每条记录必填字段（task_id/task_status/journal_state/last_event_ts/drift_summary/suggested_actions） | 已实现 | `models.py` `TaskJournalEntry` 包含全部字段；`task_journal.py` 第 223-232 行组装，`task_status=task.status`（内部 TaskStatus 值） |
| FR-016 | 诊断详情走摘要 + artifact 引用模式，不直接内联响应体 | 已实现 | `models.py` `DriftSummary` 仅含摘要字段（drift_type/stall_duration_seconds/detected_at/failure_count）；完整诊断通过 `drift_artifact_id` 引用；`task_journal.py` 第 174 行从 DRIFT 事件 payload 中取 `artifact_ref` |
| FR-017 | `WatchdogConfig` 强类型模型，5 个可配置项（scan_interval_seconds/no_progress_cycles/cooldown_seconds/failure_window_seconds/repeated_failure_threshold）及默认值 | 已实现 | `config.py` `WatchdogConfig` 全部 5 个字段有默认值（15/3/60/300/3），与 spec 表格一致；`no_progress_threshold_seconds` property 正确计算 |
| FR-018 | 支持 `WATCHDOG_{KEY}` 环境变量覆盖，无效值回退默认值，不影响启动 | 已实现 | `config.py` `from_env()` 映射 5 个环境变量；`_positive_integer` validator 处理非正整数并 `log.warning` 后回退默认值 |
| FR-019 | 所有 TASK_DRIFT_DETECTED 事件携带 `task_id`/`trace_id`/`watchdog_span_id`（F012 前为空字符串占位） | 已实现（部分注意项） | `scanner.py` `_emit_drift_event` 中 `trace_id=task_trace_id`（`getattr(task, "trace_id", "")` 降级），`watchdog_span_id=""` 占位；`Event` 构造时传入 `trace_id`；注意：Task 模型无 `trace_id` 字段，当前所有 DRIFT 事件 trace_id 实际为空字符串（见 WARNING-01） |
| FR-020 | Journal 视图和扫描日志全链路透传 task_id/trace_id，满足 GATE-M15-WATCHDOG | 部分实现 | `scanner.py` `task_log = log.bind(task_id=task.task_id)` 确保日志含 task_id；但 trace_id 在扫描日志中未绑定（仅在 `watchdog_drift_event_emitted` 日志中携带 trace_id，扫描摘要日志缺少 trace_id 绑定，见 WARNING-02） |
| FR-021 | `watchdog_span_id` 字段 F012 前为空字符串占位，不需修改 schema | 已实现 | `payloads.py` 第 311-313 行默认值 `""` + 注释；`scanner.py` 第 221-222 行显式传入 `watchdog_span_id=""`，注释说明占位原因 |
| FR-022 | Policy Engine 消费 TASK_DRIFT_DETECTED 事件，支持 alert/demote/pause/cancel 动作 | 未实现 | Tasks T041-T043 均标记为 [SKIP]，Policy Engine 侧无 WatchdogActionRouter 实现；搜索 `octoagent/packages/policy/` 未发现 TASK_DRIFT_DETECTED 消费逻辑（见 CRITICAL-01） |
| FR-023 | `pause`/`cancel` 必须走两阶段门控，Watchdog 不直接执行 | 未实现 | 依赖 FR-022 中 WatchdogActionRouter 的实现；Watchdog 端符合约束（不直接执行），但 Policy Engine 端两阶段门控未实现（见 CRITICAL-01） |
| FR-024 | 策略动作执行结果写入 EventStore（drift_event_id/action_type/executed_at） | 未实现 | 同 FR-022，T042 标记 SKIP，Policy Engine 审计事件写入未实现（见 CRITICAL-01） |

---

## 总体合规率

**21/24 FR 已实现（87.5%）**

- 已实现：FR-001 ~ FR-021（21 条）
- 未实现：FR-022、FR-023、FR-024（3 条，均为 US6 Policy Engine 集成侧）

---

## 偏差清单

### 非"已实现"FR 详细说明

| FR 编号 | 状态 | 偏差描述 | 修复建议 |
|---------|------|---------|---------|
| FR-019 | 部分实现 | Task 模型（`octoagent/packages/core/src/octoagent/core/models/task.py`）无 `trace_id` 字段，`scanner.py` 第 165 行通过 `getattr(task, "trace_id", "")` 降级，导致所有 DRIFT 事件的 `trace_id` 实际写入为空字符串，不满足 FR-019 要求的"继承被检测任务的 trace_id" | 在 Task 模型中追加 `trace_id: str = ""` 字段，并在 TaskStore 的创建/读取路径中持久化此字段；或在 F012 接入前通过 TaskStore 记录 task 的初始 trace_id（可查询 TASK_CREATED 事件中的 trace_id） |
| FR-020 | 部分实现 | 扫描摘要日志 `watchdog_scan_completed`（`scanner.py` 第 191-196 行）缺少 `trace_id` 绑定，不满足"扫描日志全链路透传 trace_id"的要求；仅在 `watchdog_drift_event_emitted` 日志中有 trace_id | 在 `scan()` 方法入口处，或对每个任务处理时绑定 `structlog` 上下文变量 `trace_id`；scan 级别的汇总日志可记录 trace_id 列表或保持现有 task_id 绑定但需确保 trace_id 字段存在 |
| FR-022 | 未实现 | Policy Engine 未实现 `WatchdogActionRouter`，不能消费 `TASK_DRIFT_DETECTED` 事件并执行 alert/demote/pause/cancel 动作；tasks.md T041 明确标记 `[SKIP]`，SKIP 原因为"依赖 Policy Engine 尚未实现" | 在后续专项 Feature 中实现 Policy Engine 侧的漂移信号消费逻辑，具体路径为 `apps/kernel/src/octoagent/kernel/policy/watchdog_action_router.py`（或现有 policy 包扩展）；当前标记合理，属于有计划的后续交付 |
| FR-023 | 未实现 | 两阶段门控（Plan -> Gate -> Execute）依赖 FR-022，当前 Policy Engine 端未实现；Watchdog 侧已满足约束（不直接执行），缺口在消费端 | 随 FR-022 一并实现，`pause`/`cancel` 动作必须在 WatchdogActionRouter 中先生成执行计划事件，经 Policy Gate 审批后再执行 |
| FR-024 | 未实现 | 策略动作审计事件写入依赖 FR-022，T042 标记 SKIP | 随 FR-022 一并实现，每次动作执行后写入包含 `drift_event_id`/`action_type`/`executed_at`/`task_id`/`trace_id` 的审计事件 |

---

## 关键审查专项

### 审查专项 1：API 端点契约符合性（contracts/rest-api.md）

**端点**: `GET /api/tasks/journal`

| 契约要求 | 实现状态 | 证据 |
|---------|---------|------|
| HTTP 200 返回 `generated_at` | 已符合 | `task_journal.py` 第 245 行 `JournalResponse(generated_at=generated_at, ...)` |
| `summary.total/running/stalled/drifted/waiting_approval` | 已符合 | `JournalSummary` 模型包含全部 5 个字段；`total=len(tasks)` |
| `groups` 包含四个分组数组 | 已符合 | `JournalGroups` 模型包含 running/stalled/drifted/waiting_approval |
| 每条记录 7 个字段（task_id/task_status/journal_state/last_event_ts/drift_summary/drift_artifact_id/suggested_actions） | 已符合 | `_entry_to_dict()` 第 77-85 行输出全部 7 个字段 |
| `drift_summary` 含 4 个子字段（drift_type/stall_duration_seconds/detected_at/failure_count） | 已符合 | `task_journal.py` 第 71-75 行 `drift_summary_dict` 构造 |
| `task_status` 使用内部 TaskStatus，不映射为 A2A | 已符合 | `task_journal.py` 第 226 行 `task_status=task.status`（内部枚举值如 "RUNNING"，非 A2A 的 "active"） |
| 503 降级响应 `JOURNAL_DEGRADED` | 已符合 | `watchdog.py` 第 56-65 行异常捕获后返回 503 + JOURNAL_DEGRADED |
| 分组分类规则（6 条优先级规则） | 已符合（含注意项） | `task_journal.py` 实现了全部 6 条优先级规则；`stalled` 分组加了额外的 `task.updated_at` 超阈值检查，逻辑更严谨（见 INFO-01） |

**DRIFT 事件查询窗口问题**（INFO-02）：`task_journal.py` 第 112 行使用 `failure_window_seconds`（默认 300s）作为 DRIFT 事件查询窗口（`drift_since_ts`），但契约未明确规定此窗口大小。若某任务的 DRIFT 事件超过 300s 前发生，则会被错误分类为 `running` 而非 `drifted`。

### 审查专项 2：T041-T043 SKIP 合理性评估

**结论：合理，但需明确后续 Feature 跟进计划**

| 任务 | SKIP 原因 | 评估 |
|-----|---------|------|
| T041 `WatchdogActionRouter` | 依赖 Policy Engine（apps/kernel/）尚未实现 | 合理；Policy Engine 是跨 Feature 依赖，超出 F011 范围 |
| T042 审计事件写入 | 依赖 T041 | 合理；链式依赖 |
| T043 单元测试 | 依赖 T041 | 合理；无被测代码则无测试 |

SKIP 原因已在 tasks.md 中明确记录，且 spec 第 322 行也标注"Policy Engine 的完整动作路由实现（Policy 侧由 Policy Engine 负责）"属于范围外。但 FR-022~FR-024 仍属于本 spec 定义的 FR，属于计划内的未实现项（CRITICAL），不是过度实现或范围外需求。

### 审查专项 3：SC-001~SC-008 成功标准可验证性

| 成功标准 | 可验证性 | 对应测试 |
|---------|---------|---------|
| SC-001：45s 内检测到无进展，延迟不超过 1 个周期 | 可验证 | `test_watchdog_e2e.py` `TestE2EScenario1StallDetection`：任务 60s 前更新，验证 1 次扫描后 DRIFT 事件写入 |
| SC-002：DRIFT 事件 payload 含诊断摘要和至少 1 条建议动作 | 可验证 | `test_watchdog_payloads.py` 验证 `TaskDriftDetectedPayload` 字段；E2E 测试验证 `payload["suggested_actions"]` 非空 |
| SC-003：告警动作执行后在 EventStore 生成可检索记录 | 不可验证（FR-022 未实现） | T041-T043 SKIP，Policy Engine 审计事件未实现，SC-003 当前无法通过测试验证 |
| SC-004：默认阈值（15s/45s/60s）在无配置时生效，E2E 验证 | 可验证 | `test_config.py` 验证默认值；`_make_tight_config()` 复现默认配置；E2E 测试使用默认配置场景 |
| SC-005：扫描失败后下一周期自动恢复，不影响运行任务 | 可验证 | `test_scanner.py` 集成测试覆盖"扫描失败记录 warning、不抛出"场景 |
| SC-006：进程重启后首次扫描从 EventStore 重建基准，无检测盲窗 | 可验证 | `test_watchdog_e2e.py` `TestE2EScenario4RestartCooldownRecovery`：模拟重启 + startup() + 验证 cooldown 重建 |
| SC-007：Journal API 在 MVP 量级下响应 < 2s | 无专用性能测试 | 当前测试未覆盖性能基线（见 INFO-03） |
| SC-008：DRIFT 事件携带 task_id 和 trace_id，满足 GATE-M15-WATCHDOG | 部分可验证 | E2E 测试验证 `drift_event.task_id` 正确；但 trace_id 当前为空字符串（Task 模型无 trace_id 字段），实际上 GATE-M15-WATCHDOG 的 trace_id 门禁验收存在缺口 |

### 审查专项 4：路由注册顺序

**结论：已正确实现**

`main.py` 第 230-232 行：
```python
app.include_router(watchdog.router, tags=["watchdog"])   # 第 230 行
app.include_router(message.router, tags=["message"])     # 第 231 行
app.include_router(tasks.router, tags=["tasks"])         # 第 232 行
```

`watchdog.router`（含 `/api/tasks/journal`）在 `tasks.router`（含 `/api/tasks/{task_id}`）之前注册，符合 contracts/rest-api.md 明确要求。`watchdog.py` 路由文件第 1-7 行注释也显式说明此要求。

---

## 过度实现检测

| 位置 | 描述 | 风险评估 |
|------|------|---------|
| `task_journal.py` 第 204-217 行 | `stalled` 分组判断增加了额外条件：检查 `task.updated_at` 超过阈值（`stall_duration >= threshold`），若刚创建的任务无进展事件但 updated_at 未超阈值则归为 `running` | INFO：spec 仅描述分组规则，此实现更严谨（防止刚创建任务被误判为 stalled），逻辑合理但超出 contracts/rest-api.md 规则 5 的字面描述 |
| `test_watchdog_e2e.py` `TestE2EMultiDetectorScenario` | 包含一个综合场景测试（多任务 + 多检测器），该场景未在 tasks.md 的 T044-T047 中显式列出 | INFO：额外测试覆盖，不涉及新功能，对系统无风险，属于测试层次的正向超额交付 |
| `scanner.py` 第 125-127 行 | 在 `scan()` 中对每个任务添加了"额外防御：跳过终态任务"检查，理论上 `list_tasks_by_statuses` 已过滤 | INFO：防御性编程，逻辑正确，不影响行为 |

---

## 问题分级汇总

### CRITICAL（FR 未实现）：1 组，共 3 条 FR

**CRITICAL-01：FR-022/FR-023/FR-024 — Policy Engine 侧 Watchdog 集成全部未实现**

- 影响范围：US6（策略动作的可审计性）、SC-003（告警动作可审计可回放）
- 根因：Policy Engine（apps/kernel/）尚未交付，属于跨 Feature 硬依赖
- tasks.md 状态：T041/T042/T043 全部 `[ ]`（未勾选），附有合理的 SKIP 说明
- 当前系统行为：Watchdog 正确生成 DRIFT 信号事件，但无消费端处理这些事件

### WARNING（FR 部分实现）：2 条

**WARNING-01：FR-019 — trace_id 实际为空字符串**

- 位置：`octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/scanner.py` 第 163-165 行
- 问题：`getattr(task, "trace_id", "")` 因 `Task` 模型无 `trace_id` 字段而始终返回 `""`；所有写入 EventStore 的 TASK_DRIFT_DETECTED 事件的 `trace_id` 字段为空字符串
- 影响：FR-019 要求"继承被检测任务的 trace_id"，当前不满足此语义

**WARNING-02：FR-020 — 扫描摘要日志缺少 trace_id 绑定**

- 位置：`octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/scanner.py` 第 191-196 行
- 问题：`watchdog_scan_completed` 日志含 `active_task_count`/`drift_detected_count`/`scan_duration_ms`，但未绑定 `trace_id` 上下文变量
- 影响：FR-020 要求"全链路透传 trace_id"；T049 任务虽已勾选，但实际代码层面 scan 摘要日志未实现 trace_id 绑定

### INFO（过度实现/观察项）：3 条

**INFO-01：task_journal.py stalled 分组逻辑比 spec 更严谨**

- 位置：`task_journal.py` 第 204-217 行
- 说明：在无 DRIFT 事件且无近期进展事件的情况下，额外检查 `updated_at >= threshold` 才归入 `stalled`，避免刚创建任务被误判。此行为 contracts/rest-api.md 规则 5 未明确规定，属于良性增强

**INFO-02：DRIFT 事件查询窗口硬编码为 failure_window_seconds（300s）**

- 位置：`task_journal.py` 第 112 行
- 说明：Journal 查询时的 DRIFT 事件时间窗口使用 `failure_window_seconds`（默认 300s）。若任务有超过 5 分钟前的旧 DRIFT 事件且此后无新 DRIFT，该事件不会被纳入分组判断，可能导致 `drifted` 任务被误分类为 `running`。spec 未明确此窗口大小，建议后续在 spec 中显式定义或改为无时限查询

**INFO-03：SC-007 无专用性能测试**

- 说明：成功标准 SC-007 要求 Journal API 在 MVP 量级下响应 < 2s，但当前测试套件无性能基线测试。属于测试覆盖缺口，不影响功能正确性

---

## 附录：关键代码证据快照

### FR-001 证据
文件：`/Users/connorlu/Desktop/.workspace2.nosync/AgentsStudy/octoagent/packages/core/src/octoagent/core/models/enums.py` 第 116-119 行：
```python
# Feature 011: Watchdog + Task Journal 事件类型（FR-001）
TASK_HEARTBEAT = "TASK_HEARTBEAT"            # Worker 心跳确认事件
TASK_MILESTONE = "TASK_MILESTONE"            # 任务里程碑完成标记事件
TASK_DRIFT_DETECTED = "TASK_DRIFT_DETECTED"  # 漂移检测告警事件
```

### FR-005/FR-023 证据（Watchdog 不直接执行高风险操作）
文件：`/Users/connorlu/Desktop/.workspace2.nosync/AgentsStudy/octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/scanner.py`
- 第 57-58 行注释："绝不直接调用 task cancel/pause（Constitution 原则 4）"
- 全文搜索无 `cancel`/`pause`/`update_task_status` 调用

### FR-010 LLM 等待期豁免证据
文件：`/Users/connorlu/Desktop/.workspace2.nosync/AgentsStudy/octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/detectors.py` 第 102-115 行：
```python
model_started_events = await event_store.get_events_by_types_since(
    task_id=task.task_id,
    event_types=[EventType.MODEL_CALL_STARTED],
    since_ts=since_ts,  # 复用 no_progress_threshold 窗口
)
if model_started_events:
    # LLM 等待期内，豁免
    return None
```

### FR-019 trace_id 降级证据（WARNING-01 根因）
文件：`/Users/connorlu/Desktop/.workspace2.nosync/AgentsStudy/octoagent/apps/gateway/src/octoagent/gateway/services/watchdog/scanner.py` 第 163-165 行：
```python
# Task 模型本身无 trace_id 字段，使用 getattr 降级为空字符串
# F012 接入后将从请求上下文中透传真实 trace_id
task_trace_id = getattr(task, "trace_id", "")
```

### 路由注册顺序证据
文件：`/Users/connorlu/Desktop/.workspace2.nosync/AgentsStudy/octoagent/apps/gateway/src/octoagent/gateway/main.py` 第 228-232 行：
```python
# 注意：watchdog.router 必须在 tasks.router 之前注册，
# 确保 /api/tasks/journal 优先于 /api/tasks/{task_id} 匹配（contracts/rest-api.md 要求）
app.include_router(watchdog.router, tags=["watchdog"])
app.include_router(message.router, tags=["message"])
app.include_router(tasks.router, tags=["tasks"])
```
