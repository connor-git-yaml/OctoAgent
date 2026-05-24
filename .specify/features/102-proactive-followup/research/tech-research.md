# F102 Proactive Followup — 技术调研报告

[独立模式] 本次技术调研未参考产品调研结论，直接基于需求描述和代码上下文执行。

---

## 任务 1：APScheduler 现状

### 实测定位

- **AsyncIOScheduler 实例持有点**：`automation_scheduler.py:30`
  ```python
  self._scheduler = AsyncIOScheduler(timezone="UTC")
  ```
- **job 注册 API**：`automation_scheduler.py:63`
  ```python
  self._scheduler.add_job(
      self._run_scheduled_job,
      trigger=trigger,  # CronTrigger / IntervalTrigger / DateTrigger
      args=[job.job_id],
      id=job.job_id,
      replace_existing=True,
      misfire_grace_time=30,
  )
  ```
- **调度器 startup 路径**：`automation_scheduler.py:34`，在 `startup()` 恢复所有 `AutomationJob` 数据库记录。
- **cron 触发支持**：`automation_scheduler.py:191`，`CronTrigger.from_crontab(job.schedule_expr, timezone=job.timezone)` 标准 crontab 格式（5 字段）。
- **单独 asyncio.Task 模式**（ObservationRoutine）：`observation_promoter.py:147`，走 `asyncio.create_task()` 而非 APScheduler。

### 现有 job 类型

现有 `AutomationSchedulerService` 完全基于用户配置的 `AutomationJob` 记录——无硬编码内置 job，全部由数据库中 `list_jobs()` 动态恢复。`ObservationRoutine` 是唯一独立运行的内置后台 routine，走 `asyncio.Task` 循环。

### F102 推荐注册路径

**推荐方案 A：新建 RoutineService，走 AutomationSchedulerService 的 cron 注册**

理由：
1. `AutomationSchedulerService` 已支持 cron trigger（`CronTrigger.from_crontab`），可直接复用。
2. cron 执行路径走 `control_plane_service.execute_action(ActionRequestEnvelope)` 统一 actor/audit 模型，F102 daily summary 走相同路径可天然获得 `CONTROL_PLANE_ACTION_*` 审计事件。
3. 已有 startup recovery（重启后恢复 cron schedule）。

**方案 B（备选）**：参照 `ObservationRoutine` 模式，新建 `DailyRoutine` 独立 asyncio.Task，无需经过 AutomationJob 数据库表，sleep-until-target-time 自行计算下次触发时间。方案 B 更轻量但无 APScheduler 的 misfire recovery。

**关键冲突**：F101 prompt 中提及"从 USER.md `daily_summary_time` 读"，但当前 USER.md 模板（`behavior_templates/USER.md`）中**没有 `daily_summary_time` 字段**，只有 `active_hours`（`USER.md:33`）。该字段需要 F102 新增。

---

## 任务 2：F101 NotificationService 触发入口

### 公共 API 清单

`notification.py` 中 `NotificationService` 的所有公共方法：

| 方法 | 签名（关键参数） | 说明 |
|------|----------------|------|
| `notify_task_state_change` | `task_id, event_type, payload, priority=LOW, active_hours=None, state_transition_event_id="", session_id=None` → `None` | Task 状态变更推送，支持去重 + quiet hours |
| `notify_approval_request` | `task_id, tool_name, ask_reason, payload, priority=CRITICAL, ...` → `None` | 审批请求，默认 CRITICAL，bypass quiet hours |
| `notify_heartbeat` | `task_id, payload` → `None` | 心跳通知，不去重 |
| `dismiss` | `notification_id, source="unknown"` → `None` | 幂等 dismiss |
| `is_dismissed` | `notification_id` → `bool` | 检查 dismiss 状态 |
| `list_active` | `session_id` → `list[dict]` | Web 刷新用活跃通知列表 |
| `bind_snapshot_store` | `snapshot_store` → `None` | 延迟绑定（bootstrap 顺序修复） |
| `bind_event_store` | `event_store` → `None` | 延迟绑定 |
| `register_channel` | `channel: NotificationChannelProtocol` → `None` | 注册通知渠道 |

**四级优先级定义**（`notification.py:116`）：
- `CRITICAL = "approval_pending"` — bypass quiet hours（如审批等待）
- `HIGH = "worker_failed"` — quiet hours 内不推
- `MEDIUM = "worker_long_running"` — quiet hours 内不推
- `LOW = "worker_completed"` — quiet hours 内不推

### F102 推荐调用方式

```python
await notification_service.notify_task_state_change(
    task_id="_routine_daily_summary",   # 占位 task_id，参照 observation audit pattern
    event_type="ROUTINE_DAILY_SUMMARY",
    payload={"summary": "...", "worker_count": N, "failed_count": M},
    priority=NotificationPriority.LOW,  # 昨日摘要为低优先级
    session_id=None,
)
```

### quiet hours 行为确认

`notification.py:338`：无论是否被 quiet hours 过滤，`_write_notification_audit_event` 先写 `NOTIFICATION_DISPATCHED` event（`filtered=True/False`）。daily summary 落在 quiet hours 内 **会被 discard，但 event_store 仍有记录**——行为符合设计，用户次日 active hours 开始时不会补发（当前无重试机制）。

**潜在问题**：daily summary 目标推送时间（如 08:30）可能恰在 active_hours 开始边界，若解析有精度差异可能被过滤。F102 spec 需要明确"触发时间是否需要主动等待 active hours 开始"。

---

## 任务 3：event_store 查询路径

### 现有 query API

`event_store.py` 中 `SqliteEventStore` 全部公共 query 方法：

| 方法 | 参数 | 说明 |
|------|------|------|
| `get_events_for_task(task_id)` | 单 task_id | 返回全量事件，按 task_seq 正序 |
| `get_events_after(task_id, after_event_id)` | task_id + event_id | SSE 断线重连增量查询，ULID 字典序 |
| `get_events_by_types_since(task_id, event_types, since_ts)` | task_id + 类型列表 + 时间下界 | 复合索引 `idx_events_type_ts(task_id, type, ts)` |
| `get_next_task_seq(task_id)` | task_id | 写入辅助 |
| `get_latest_event_ts(task_id)` | task_id | 查最新事件时间 |
| `get_all_events()` | 无 | 全表扫描，仅用于 Projection 重建 |
| `check_idempotency_key(key)` | key | 幂等检查 |

### 关键缺口

**event_store 所有 query API 均要求 task_id——无跨任务时间范围 query**。F102 需要查询"昨日所有 task 的状态变化"，必须走 task_store 层：

1. `task_store.list_tasks(status=None)` — 全量 task 列表（`task_store.py:64`），无时间过滤，返回 `list[Task]`，任务量大时全表扫描
2. `task_store.list_tasks_by_statuses(statuses)` — 按状态集批量查询（`task_store.py:78`）

**推荐的 F102 数据收集路径**：
- Step 1：`task_store.list_tasks()` 拿全量 task，Python 层按 `task.created_at` 过滤昨日范围
- Step 2：对每个匹配 task，`event_store.get_events_by_types_since(task_id, [STATE_TRANSITION, WORKER_DISPATCHED, WORKER_RETURNED], yesterday_start)` 提取状态变化事件
- 注意：`idx_events_type_ts` 索引在 `events(task_id, type, ts)` 上（`sqlite_init.py:66`），跨 task_id 无全局 ts 索引——**全表扫描风险**存在于 task 数量大时

**性能注意**：对昨日有活动的 task 逐一 query event_store 是 N+1 pattern，若 task 量 > 100 可能较慢。spec 阶段需要决定是否在 F102 新增跨任务时间范围 query API，还是接受 Python 层内存过滤。

### F102 摘要相关 EventType（实测）

以下枚举值实际存在（`enums.py:71`）：

| EventType | 枚举值 | F102 是否相关 |
|-----------|--------|--------------|
| `STATE_TRANSITION` | "STATE_TRANSITION" | 是——task 状态变化主要载体 |
| `WORKER_DISPATCHED` | "WORKER_DISPATCHED" | 是——worker 启动时间点 |
| `WORKER_RETURNED` | "WORKER_RETURNED" | 是——worker 返回 |
| `TASK_HEARTBEAT` | "TASK_HEARTBEAT" | 可选——长时间运行任务节点 |
| `APPROVAL_REQUESTED` | "APPROVAL_REQUESTED" | 是——卡在审批的 task |
| `APPROVAL_EXPIRED` | "APPROVAL_EXPIRED" | 是——超时未审批 |
| `NOTIFICATION_DISPATCHED` | "NOTIFICATION_DISPATCHED" | 可选——通知统计 |

**注意**：没有 `TASK_COMPLETED` / `TASK_FAILED` 枚举值——task 终态通过 `STATE_TRANSITION` event 的 payload `to_status` 字段体现，不是独立 EventType。

---

## 任务 4：USER.md 字段实测

### 当前 USER.md 模板字段（`behavior_templates/USER.md`）

模板文件在 `packages/core/src/octoagent/core/behavior_templates/USER.md`：

**机器可读字段（当前只有一个）**：
- `active_hours`: `"HH:MM-HH:MM"` 格式（`USER.md:33`），`NotificationService` 通过 `extract_active_hours_from_user_md()` 解析（`notification.py:87`）

**其余字段**（人类可读，非机器解析）：
- 称呼、时区/地点、主要语言、职业/领域（基本信息节）
- 回复风格、信息组织、确认偏好（沟通偏好节）
- 活跃时段（人类可读）、常用工具/平台、任务偏好（工作习惯节）

### USER.md 解析路径

`notification.py:319`，解析路径：`snapshot_store.get_live_state("USER.md")` → `extract_active_hours_from_user_md()` regex 扫描（`_ACTIVE_HOURS_PATTERN`，`notification.py:73`）。**不是 YAML frontmatter**，是 markdown 行内匹配。

### user_profile.update 工具签名

`user_profile_tools.py:133`：
```python
async def user_profile_update(
    operation: Literal["add", "replace", "remove"],
    content: str,           # add 时新条目内容
    old_text: str = "",     # replace 时原文
    target_text: str = "",  # remove 时目标
) -> UserProfileUpdateResult
```

当前只有 `add` 操作真实执行，`replace`/`remove` 返回 `approval_pending`。

### F102 拟新增字段建议

| 字段名 | 推荐格式 | 说明 |
|--------|---------|------|
| `daily_summary_time` | `"HH:MM"` 24h 制 | 每日摘要推送时间，如 `"08:30"` |
| `routine_active` | `"true"` / `"false"` | 是否启用 daily summary routine |
| `weekly_summary_day` | `"monday"~"sunday"` | 可选，weekly summary 触发日 |
| `summary_channels` | `"telegram,web"` 逗号分隔 | 可选，摘要推送渠道限定 |

**注意**：`approval_timeout_seconds` 字段在 `policy/models.py:159` 中存在，但它是 PolicyRule 模型字段，**不在 USER.md 中**——F101 prompt 描述有误，USER.md 目前只有 `active_hours` 一个机器可读字段，`approval_timeout_seconds` 走 policy 配置路径。

---

## 任务 5：Hermes Agent Routine 模式摘录

**冲突警告**：`_references/opensource/hermes-agent/` 目录在本 worktree 和主仓库中均**不存在**。`CLAUDE.local.md` 中提到的 Hermes Agent 参考源码无法在文件系统中找到（路径 `_references/opensource/` 下只有 `memU` 子仓库）。以下为基于 OctoAgent 现有 `ObservationRoutine` 实现提取的 design pattern，标注 `[推断]`。

### 从 ObservationRoutine 提取的现有 Routine design pattern

`observation_promoter.py` 体现了 OctoAgent 内置 Routine 的完整设计：

1. **asyncio.Task 独立循环**（`observation_promoter.py:147`）：`asyncio.create_task(_run_loop())`，不经 APScheduler，适合内置固定 interval routine。
2. **feature flag 门控**（`observation_promoter.py:130`）：`if not self._feature_enabled: return`，支持配置文件控制启停。
3. **CancelledError 显式 re-raise**（`observation_promoter.py:203`）：CancelledError 向上传播，其他异常 catch 后写 ERROR 日志，loop 继续——Constitution C6。
4. **审计 task 占位 pattern**（`observation_promoter.py:40`）：`_OBSERVATION_AUDIT_TASK_ID = "_observation_routine_audit"` + `ensure_system_audit_task()`，防 FK violation。
5. **stop() 超时不丢引用**（`observation_promoter.py:167`）：5s wait_for 超时后保留 `self._task` 引用，不允许 silent 丢失。
6. **DI 注入 + None 降级**（`observation_promoter.py:95`）：`conn / event_store / provider_router` 均可为 None，各路径显式降级（Constitution C6）。
7. **[推断] 执行上下文隔离**：`ObservationRoutine` 不访问当前活跃用户 session，通过 DB 直接读历史数据——F102 daily summary 应遵循同样隔离原则，不进入 active LLM session。

### F102 应吸收的 pattern

- 复用审计 task 占位（`_daily_routine_audit` task_id）
- 复用 feature flag 门控（`routine_active` 字段读 USER.md）
- **不复用** asyncio.Task sleep loop（daily 精确时间触发用 APScheduler CronTrigger 更合适）
- **需新增**：摘要生成走 LLM（utility model / "cheap" alias），ObservationRoutine 有 `_call_categorize_model` 先例（`observation_promoter.py:468`）

---

## 任务 6：attention_work_count 信号集成

### 实测定位

- **字段定义**：`control_plane/agent.py:55`
  ```python
  class WorkerProfileDynamicContext(BaseModel):
      attention_work_count: int = Field(default=0, ge=0)
  ```
- **计算路径**：`control_plane/worker_service.py:1456`
  ```python
  attention_statuses = {"waiting_input", "waiting_approval", "paused", "escalated", "failed"}
  attention_works = [item for item in works if item.status.value in attention_statuses]
  # ...
  attention_work_count=len(attention_works)  # line 1472
  ```
- **暴露层**：`worker_service.py:369`，在 worker 列表 API 中返回 `attention_count`。

### F102 集成建议

`attention_work_count` 是 Worker 维度的实时计数，而非全局 attention 信号。F102 daily summary 是批处理摘要，两者语义不同：

- **不应用于跳过推送**：daily summary 的价值在于提供昨日全貌，包括当前仍处于 attention 状态的 task（恰恰是最需要汇报的）。
- **应用于内容组织**：daily summary payload 中应包含 `attention_count`（昨日结束时仍有多少 task 需要关注），帮助用户快速判断今日优先级。
- **priority 建议**：若 `attention_count > 0`，priority 提升为 `MEDIUM`；否则保持 `LOW`。

---

## 任务 7：EventType 冲突实测

### 现有 EventType 完整清单（`enums.py:71-238`）

现有 38 个 EventType 值，**无任何 `ROUTINE_` 前缀**。完整列表：

TASK_CREATED, USER_MESSAGE, MODEL_CALL_STARTED, MODEL_CALL_COMPLETED, MODEL_CALL_FAILED, CONTEXT_COMPACTION_COMPLETED, MEMORY_RECALL_SCHEDULED, MEMORY_RECALL_COMPLETED, MEMORY_RECALL_FAILED, STATE_TRANSITION, ARTIFACT_CREATED, ERROR, CREDENTIAL_LOADED, CREDENTIAL_EXPIRED, CREDENTIAL_FAILED, OAUTH_STARTED, OAUTH_SUCCEEDED, OAUTH_FAILED, OAUTH_REFRESHED, OAUTH_REFRESH_TRIGGERED, OAUTH_REFRESH_FAILED, OAUTH_REFRESH_RECOVERED, OAUTH_REFRESH_EXHAUSTED, OAUTH_ADOPTED_FROM_EXTERNAL_CLI, TOOL_CALL_STARTED, TOOL_CALL_COMPLETED, TOOL_CALL_FAILED, SKILL_STARTED, SKILL_COMPLETED, SKILL_FAILED, POLICY_DECISION, APPROVAL_REQUESTED, APPROVAL_APPROVED, APPROVAL_REJECTED, APPROVAL_EXPIRED, APPROVAL_DECIDED, POLICY_CONFIG_CHANGED, ORCH_DECISION, WORKER_DISPATCHED, WORKER_RETURNED, A2A_MESSAGE_SENT, A2A_MESSAGE_RECEIVED, CHECKPOINT_SAVED, RESUME_STARTED, RESUME_SUCCEEDED, RESUME_FAILED, TASK_HEARTBEAT, TASK_MILESTONE, TASK_DRIFT_DETECTED, EXECUTION_STATUS_CHANGED, EXECUTION_LOG, EXECUTION_STEP, EXECUTION_INPUT_REQUESTED, EXECUTION_INPUT_ATTACHED, EXECUTION_CANCEL_REQUESTED, OPERATOR_ACTION_RECORDED, BACKUP_STARTED, BACKUP_COMPLETED, BACKUP_FAILED, CHAT_IMPORT_STARTED, CHAT_IMPORT_COMPLETED, CHAT_IMPORT_FAILED, CONTROL_PLANE_RESOURCE_PROJECTED, CONTROL_PLANE_RESOURCE_REMOVED, CONTROL_PLANE_ACTION_REQUESTED, CONTROL_PLANE_ACTION_COMPLETED, CONTROL_PLANE_ACTION_REJECTED, CONTROL_PLANE_ACTION_DEFERRED, TOOL_INDEX_SELECTED, WORK_CREATED, WORK_STATUS_CHANGED, PIPELINE_RUN_UPDATED, PIPELINE_CHECKPOINT_SAVED, SKILL_USAGE_REPORT, RESOURCE_LIMIT_HIT, TOOL_BATCH_STARTED, TOOL_BATCH_COMPLETED, CONTEXT_COMPACTION_FAILED, PRESET_CHECK, APPROVAL_OVERRIDE_HIT, TOOL_SEARCH_EXECUTED, TOOL_PROMOTED, TOOL_DEMOTED, TOOL_INDEX_DEGRADED, MEMORY_ENTRY_ADDED, MEMORY_ENTRY_REPLACED, MEMORY_ENTRY_REMOVED, MEMORY_ENTRY_BLOCKED, OBSERVATION_OBSERVED, OBSERVATION_STAGE_COMPLETED, OBSERVATION_PROMOTED, OBSERVATION_DISCARDED, SUBAGENT_SPAWNED, SUBAGENT_RETURNED, SUBAGENT_COMPLETED, CONTROL_METADATA_UPDATED, AGENT_SESSION_TURN_PERSISTED, BEHAVIOR_PACK_LOADED, BEHAVIOR_PACK_USED, NOTIFICATION_DISPATCHED

### F102 拟新增 EventType 冲突分析

| 拟新增值 | 冲突 | 说明 |
|---------|------|------|
| `ROUTINE_TRIGGERED` | 无 | 可安全添加 |
| `ROUTINE_COMPLETED` | 无 | 可安全添加 |
| `ROUTINE_FAILED` | 无 | 可安全添加 |
| `ROUTINE_SKIPPED` | 无 | 可选，用于 quiet hours 内跳过推送审计 |

---

## 关键决策建议（spec 阶段必须拍板）

| 编号 | 事项 | 建议 / 选项 |
|------|------|------------|
| D1 | **Routine 注册方式** | 推荐走 AutomationSchedulerService cron 注册（复用 APScheduler + audit 模型），对应 `CronTrigger.from_crontab("30 8 * * *")`；备选新建 asyncio.Task sleep-until。**必须在 spec 前拍板**，两条路径架构差异大。 |
| D2 | **USER.md daily_summary_time 新增字段** | 需新增到模板（`behavior_templates/USER.md`），并在 `notification.py` 类似 `extract_active_hours_from_user_md()` 新增解析函数。spec 需确认字段格式（`"HH:MM"` + 可选 timezone offset）。 |
| D3 | **event_store 跨任务查询** | 当前无全局时间范围 query API，需要在 `task_store.list_tasks()` + Python 层过滤后逐 task 查 events（N+1）。若预期昨日 task 量 > 50，应在 F102 新增 `task_store.list_tasks_in_time_range(start, end)` 辅助 API。 |
| D4 | **LLM 摘要生成** | daily summary 是否用 LLM 汇总（走 "cheap" alias，参照 `observation_promoter.py:468`），还是 deterministic 模板渲染（拼接 task title + status + duration）。LLM 方案质量高但增加延迟 + token 成本。 |
| D5 | **quiet hours 边界处理** | 若 daily_summary_time 设为 08:30 而 active_hours 为 "09:00-23:00"，推送会被 discard。spec 需明确"是否等待 active hours 后延迟推送"还是"直接 discard + 下次触发补发"。当前 NotificationService 无重试 / 补发机制。 |

---

## 冲突记录（仓库现状与 F102 prompt 描述不一致）

1. **Hermes Agent 源码不存在**：`CLAUDE.local.md` 指向 `_references/opensource/hermes-agent/` 但该目录在文件系统中不存在（只有 `memU`），无法直接参考 Hermes Routine 实现。
2. **USER.md 缺少 `daily_summary_time` 字段**：当前模板只有 `active_hours` 一个机器可读字段，`daily_summary_time` 需要 F102 新建 + 解析函数。
3. **`approval_timeout_seconds` 不在 USER.md**：F101 关于该字段"写入 USER.md"的描述与实际不符——该字段在 `policy/models.py:159` 的 PolicyRule 模型中，不是 USER.md 字段。
4. **无 TASK_COMPLETED / TASK_FAILED EventType**：F102 prompt 中提到查询"TASK_COMPLETED / TASK_FAILED 等事件"，但这两个枚举值不存在——实际需查 `STATE_TRANSITION` event 并过滤 payload `to_status`。
