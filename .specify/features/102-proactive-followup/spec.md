# Feature Specification: F102 Proactive Followup

**Feature ID**: F102
**Feature Branch**: `feature/102-proactive-followup`
**Created**: 2026-05-18
**Status**: Draft
**M5 阶段**: 阶段 3（F101 完成后启动）
**Upstream**: F101 (74c9ab3, READY_TO_MERGE)
**Downstream**: F103 Blueprint 修订（独立可并行）/ F107 Capability Layer Refactor
**Baseline passed count**: 3571（F101 实测）
**Feature 性质**: 新建 DailyRoutine 主路径（非扩展现有组件）

---

## 0. 设计基础说明

F102 的 Hermes Routine 参考架构基于 OctoAgent 现有 `ObservationRoutine` pattern（`observation_promoter.py`），而非外部 Hermes Agent 源码（该目录在文件系统中不存在）。F102 遵循 H1 管家 mediated 哲学：主 Agent 是唯一用户可见发言人，Routine 是系统后台产出内容后经 NotificationService 送达用户，**不自行直接发消息**。

**[无 Hermes Agent 源码基础]**：Hermes Agent 参考代码不存在，设计基于 OctoAgent ObservationRoutine 实测 pattern。

---

## 1. 目标（Why）

### 1.1 让用户早晨自动知道"昨天发生了什么"

OctoAgent 可能在用户入睡后继续跑 Worker 任务、处理审批超时、记录内存条目。用户次日打开应用时，面对的是静默的 task 列表，需要自己逐个查看才能掌握全貌。F102 通过每日定时 Routine，在用户活跃时段开始时（默认 08:30）自动产出"昨日 Worker 摘要"，推送 Telegram + Web 通知，让用户开门即知当日重点。

这符合 OctoAgent H1 管家 mediated 哲学：主动告知，而非被动等待用户询问。

### 1.2 让 routine 失败不影响系统稳定性

Constitution 规则 6（Degrade Gracefully）要求：LLM 不可用时，系统必须有确定性兜底路径。F102 的摘要生成必须有两条路径：LLM 汇总路径（高质量）+ deterministic 模板回退路径（LLM 失败时自动切换），确保 routine 永远产出摘要，不因 LLM provider 不可用而静默失败。

---

## 2. 范围声明

### 2.1 In Scope（本 spec 负责）

- **块 B**：DailyRoutine 主路径——每日 `daily_summary_time`（默认 08:30）触发，汇总昨日 Worker 状态，推送摘要通知
- **块 D**：USER.md 新增 3 个机器可读字段（`daily_summary_time` / `routine_active` / `summary_channels`；`weekly_summary_day` 不在 F102 范围，随 WeeklyRoutine 推迟）
- **块 E**：可观测性——4 个新 EventType + `routine_elapsed_ms` metric + `attention_count` 在摘要 payload 集成
- **块 F**：F101 NOTIFICATION_DISPATCHED 集成——daily summary 走 `notify_task_state_change`，`filtered=True` 审计链复用
- **task_store 辅助 API**：新增 `list_tasks_in_time_range(start, end)` 解决 N+1 性能问题（D3 决策）
- **F101 NotificationService 接口微扩展**：为 `notify_task_state_change` 新增 `channels: frozenset[str] | None = None` 可选参数（向后兼容；解决 AC-D3 channel 过滤路径，clarify CQ-1 决议）

### 2.2 Out of Scope（明确排除）

| 排除项 | 归属 | 理由 |
|--------|------|------|
| **WeeklyRoutine**（weekly summary） | F103+ 或独立 Feature | WeeklyRoutine 需要 7 天数据 + 趋势分析 + 更复杂内容组织。DailyRoutine 已覆盖 70% 用户价值，scope 控制为先；spec 自决选择不纳入 F102 |
| dismiss 跨重启持久化 | F107 | F101 已归档，重启清空为 known limitation |
| Blueprint 修订 | F103 | F103 专职文档同步，F102 不触及 |
| D8 control_plane DI 重构 | F107 | F101 Phase E SKIP，F102 不受影响 |
| Worker ↔ Worker 解禁后摘要 | F107+ | 当前 daily summary 只汇报主 Agent 派发的任务 |
| 前端 Routine 配置 UI | M6 | F102 用 USER.md SoT 配置，无需独立 UI |
| 历史 daily summary 查询 API | M6 | event_store 已有 audit chain，UI 层查询推后 |

---

## 3. 关键决策摘要

### 3.1 用户已拍板决策

| ID | 事项 | 结论 |
|----|------|------|
| **D1** | Routine 注册方式 | AutomationSchedulerService + CronTrigger（复用现有 cron 框架 + audit 模型） |
| **D3** | 跨任务查询 | 新增 `task_store.list_tasks_in_time_range(start, end)` 辅助 API，避免全表扫描 + Python 层过滤 N+1 |
| **D4** | 摘要生成 | LLM 汇总（cheap alias）+ **必须有 LLM 失败 fallback**（deterministic 模板渲染） |
| **D5** | quiet hours 处理 | Discard + `NOTIFICATION_DISPATCHED(filtered=True)` event 写入（与 F101 一致，不补发不延迟） |

### 3.2 Spec 自决事项

**SD-1：USER.md 字段格式与默认值**

| 字段名 | 格式 | 默认值 | 解析路径 |
|--------|------|--------|---------|
| `daily_summary_time` | `"HH:MM"`（24h，按用户时区，见 NFR-3）| `"08:30"` | `extract_daily_summary_time_from_user_md()` regex，仿 `extract_active_hours_from_user_md()` |
| `routine_active` | `"true"` / `"false"` | `"true"` | `extract_routine_active_from_user_md()` regex |
| `summary_channels` | 逗号分隔 `"telegram"` / `"web"` / `"telegram,web"`（用户友好写法）| `"telegram,web"`（全渠道）| `extract_summary_channels_from_user_md()` regex，返回 `frozenset[str]`（解析时 `"web"` 映射为内部 `"web_sse"`；详见 FR-B8） |

`weekly_summary_day` 不纳入 F102（WeeklyRoutine 推迟到独立 Feature）。

解析器非法值 fallback：`daily_summary_time` 非法 → 默认 08:30；`routine_active` 非法 → True；`summary_channels` 非法或空集 → 全渠道。三个 fallback 均写 WARNING log，不抛出异常（Constitution C6）。

**SD-2：WeeklyRoutine 是否纳入 F102**

结论：**不纳入**。WeeklyRoutine 推迟到 F103+ 或独立 Feature。理由：① DailyRoutine 已满足 70% 核心价值；② WeeklyRoutine 需要 7 天趋势数据、跨日分析和更复杂的 LLM prompt 设计，scope 大幅增加；③ F102 完成后 DailyRoutine 稳定运行 1-2 周积累的数据，正好作为 WeeklyRoutine 的数据基础。

**SD-3：Hermes Agent 参考缺失的应对**

F102 不引用外部 Hermes Agent 源码。设计完全基于 OctoAgent 内部 pattern：ObservationRoutine（`observation_promoter.py`）的审计 task 占位、feature flag 门控、CancelledError 显式传播三个核心 pattern 完整复用。

**SD-4：LLM fallback 选项选择**

结论：选 **(b) deterministic 模板渲染 fallback**。
- 选 (a)（完全 skip）：用户早晨收不到任何摘要，体验差，且 LLM 偶发不可用时影响用户。
- 选 (b)（模板渲染）：LLM 失败时自动产出结构化文字摘要（"昨日完成 N 个任务，失败 M 个，待关注 K 个"），`ROUTINE_COMPLETED` 事件含 `fallback=true` 字段，audit 链可查。
- 选 (c)（重试 N 次再 skip）：重试逻辑增加复杂度，且最终失败时依然是用户收不到摘要。
- Constitution C6 要求 graceful degrade，(b) 最符合。

**SD-5：`routine_active` 默认值**

结论：**默认 `"true"`**（零配置 onboarding）。用户不需要手动开启即可在 08:30 收到第一条 daily summary。如用户不需要，可在 USER.md 中设置 `routine_active: false`。

**SD-6：channel 过滤接口扩展**（clarify CQ-1 / checklist CHK-3.2 决议）

结论：选 **A — 为 F101 `notify_task_state_change` 新增 `channels: frozenset[str] | None = None` 可选参数**。
- 现状：F101 接口对所有注册 channel 循环 push（`notification.py:561-563`），无 per-call 过滤
- 扩展规则：`channels=None` 时维持现状（向后兼容），`channels={"telegram"}` 时只推 Telegram channel；F102 调用时传入 USER.md `summary_channels` 解析值
- 接口微扩展不视作"F101 NotificationService 核心结构改动"——本 spec §2.2 "Out of Scope" 中"F101 NotificationService 核心结构"是指 quiet hours / priority / dedup / SSE 等核心机制不动，可选参数追加是兼容式扩展

**SD-7：attention_count 计算算法**（clarify CQ-2 决议）

结论：选 **A — 查 task 表当前 `status` 字段，复用 `worker_service.py:1456` 的 attention_statuses 集合**。
- attention_statuses = `{"waiting_input", "waiting_approval", "paused", "escalated", "failed"}`
- `attention_count` = 昨日范围内（`created_at ∈ [yesterday_start, yesterday_end)`）的 task 中、当前 `task.status` ∈ attention_statuses 的数量
- 不查每个 task 最后一条 STATE_TRANSITION（避免 N+1 复杂度）；daily summary 触发时刻（次日早晨 08:30）距昨日结束已有数小时，task.status 已稳定收敛
- 语义说明：表达"昨日开始的任务中、当前仍需关注的数量"——用户视角清晰

**SD-8：空数据推送决策**（clarify CQ-7 决议）

结论：选 **A — 昨日 worker_count=0 时不推送通知，仅写 ROUTINE_COMPLETED event**。
- 仍写 `ROUTINE_COMPLETED(worker_count=0, fallback=false, summary_length=0)` event，audit 链完整
- 不调用 `notify_task_state_change`，避免周末等场景的连续噪音通知
- 用户 onboarding 第一周即使没用 OctoAgent 也不会被打扰

**SD-9：LLM prompt token budget**（clarify CQ-4 决议）

结论：max input tokens = 3000（cheap alias 输入侧）+ 超限时优先保留 failed + attention task 的 events、其余 task 仅保留 title + final status。LLM 输出 `max_tokens=512`。具体 prompt 模板由 plan 阶段细化（FR-B3 实现细节）。

**SD-10：时区语义**（clarify CQ-6 决议）

结论：tasks 表 `created_at` 列存 UTC（OctoAgent 标准）；`yesterday_start` / `yesterday_end` 由调用方按用户本地时区计算后转 UTC datetime 传入 `list_tasks_in_time_range`。`CronTrigger.from_crontab` 用 `user_timezone` 参数（NFR-3）。

---

## 4. Acceptance Criteria

### 块 B — DailyRoutine 主路径

**AC-B1** [可独立测试]
- Given: `routine_active=true`，`daily_summary_time="08:30"`，cron job 在 08:30 触发
- When: DailyRoutine.run() 执行
- Then: 查询前一日 `[yesterday_start, yesterday_end)` 时间范围内的 task 列表，汇总摘要（LLM 路径或 fallback 路径），调用 `notification_service.notify_task_state_change`，`ROUTINE_TRIGGERED` + `ROUTINE_COMPLETED` 事件写入 event_store；整个流程耗时 P50 < 5s（不含 LLM 调用）

**AC-B2** [可独立测试]
- Given: `routine_active=false`
- When: cron 触发时间到达
- Then: DailyRoutine 跳过本次执行，写 `ROUTINE_SKIPPED` 事件（`reason="routine_disabled"`），不推送任何通知

**AC-B3** [可独立测试]
- Given: LLM 调用失败（网络超时 / cheap alias 不可用）
- When: DailyRoutine 摘要生成阶段执行
- Then: 自动切换 deterministic 模板渲染路径，产出结构化摘要；`ROUTINE_COMPLETED` 事件含 `fallback=true` 字段；通知正常推送，用户收到摘要；不写 `ROUTINE_FAILED` 事件

**AC-B4** [可独立测试]
- Given: daily summary 推送时间在 quiet hours 内（`active_hours` 范围外）
- When: `notify_task_state_change` 执行
- Then: 通知被 F101 NotificationService 的 quiet hours 过滤器拦截（D5 决策），channel push 不发送，`NOTIFICATION_DISPATCHED(filtered=True)` 写入 event_store；DailyRoutine 不重试、不延迟，本次静默结束
- **测试方法**（checklist CHK-1.2 决议）：使用真实 NotificationService + mock SnapshotStore 注入含 quiet hours 的 USER.md 内容（`active_hours: "09:00-23:00"`，daily_summary_time 设为 02:00），断言 event_store 中含 `NOTIFICATION_DISPATCHED(filtered=True)` 且 channel.push 未被调用

**AC-B5** [可独立测试]（SD-8 决议：空数据不推送）
- Given: 昨日无任何 Worker 任务（`list_tasks_in_time_range` 返回空列表）
- When: DailyRoutine 执行完整流程
- Then: 写 `ROUTINE_COMPLETED(worker_count=0, failed_count=0, attention_count=0, fallback=False, summary_length=0)` event；**不调用** `notify_task_state_change`，**不推送通知**；不写 `ROUTINE_FAILED`，不抛出异常

**AC-B6** [可独立测试]
- Given: cron 注册路径，`AutomationSchedulerService.add_job` 以 `CronTrigger.from_crontab("30 8 * * *", timezone=user_timezone)` 注册
- When: 系统启动时 `DailyRoutineService.startup()` 执行
- Then: job 在 `AutomationSchedulerService` 的 scheduler 中以 `job_id="_daily_routine"` 存在；`replace_existing=True` 保证重启后不重复注册；startup_recovery 不需要额外处理（APScheduler 已内置 misfire recovery）
- **运行期 USER.md `daily_summary_time` 修改生效语义**（clarify CQ-3 决议）：cron 表达式仅在系统启动时根据 USER.md 当前值注册一次；运行期修改 `daily_summary_time` 后，**需重启 gateway 才能生效**——本 spec 不实现动态 `scheduler.reschedule_job`（YAGNI）

**AC-B7** [可独立测试]
- Given: 摘要 payload 中 `attention_count > 0`（昨日仍有待关注任务）
- When: `notify_task_state_change` 调用时构造优先级
- Then: 通知 priority 提升为 `MEDIUM`（而非 `LOW`），用户更显眼地收到提醒

### 块 D — USER.md 字段扩展

**AC-D1** [可独立测试]
- Given: USER.md 中新增行 `daily_summary_time: "09:00"`
- When: `DailyRoutineService._read_config()` 调用 `extract_daily_summary_time_from_user_md(user_md_content)`
- Then: 返回 `"09:00"`，cron 表达式更新为 `"0 9 * * *"`；非法格式时返回默认 `"08:30"` 并写 WARNING log

**AC-D2** [可独立测试]
- Given: USER.md 中 `routine_active: "false"`
- When: `extract_routine_active_from_user_md()` 解析
- Then: 返回 `False`，DailyRoutine 跳过执行（触发 AC-B2）

**AC-D3** [可独立测试]
- Given: USER.md 中 `summary_channels: "telegram"`
- When: DailyRoutine 触发摘要推送
- Then: 解析 `summary_channels` 返回 `frozenset({"telegram"})`（不含 `"web_sse"`）；调用 `notification_service.notify_task_state_change(..., channels=frozenset({"telegram"}))`；NotificationService 内部 channel routing 仅向 `channel.channel_name == "telegram"` 的 channel push，`channel_name == "web_sse"` 的 channel 不发送；event_store 中 `NOTIFICATION_DISPATCHED` payload 含 `channels=["telegram"]` 字段（SD-6 决议）

**AC-D4** [可独立测试]
- Given: USER.md 中无任何 F102 新增字段
- When: DailyRoutineService 读取配置
- Then: 全部字段使用默认值（`daily_summary_time="08:30"`, `routine_active=True`, `summary_channels={"telegram","web"}`），系统正常运行，不报错

### 块 E — 可观测性

**AC-E1** [可独立测试]
- Given: DailyRoutine 成功执行完整流程
- When: event_store 查询 task_id=`"_daily_routine_audit"`
- Then: event 链包含：`ROUTINE_TRIGGERED`（含触发时间戳）→ `ROUTINE_COMPLETED`（含 `elapsed_ms`, `worker_count`, `failed_count`, `attention_count`, `fallback`）；两个事件均可在 event_store 中以 task_id 检索

**AC-E2** [可独立测试]
- Given: LLM 失败后走 fallback 路径
- When: `ROUTINE_COMPLETED` 事件写入
- Then: payload 含 `"fallback": true`，可在 audit 查询中区分 LLM 路径和 fallback 路径

**AC-E3** [可独立测试]
- Given: DailyRoutine 执行中发生不可恢复异常（非 LLM 超时类）
- When: 异常向上传播并被 routine loop 捕获
- Then: `ROUTINE_FAILED` 事件写入 event_store（含异常描述），`CancelledError` 显式 re-raise（不被吞掉），routine 下次触发时重新执行

**AC-E4** [可独立测试]
- Given: DailyRoutine 执行，构造昨日 5 个 task，分布于不同 status：1×completed / 1×failed / 1×waiting_input / 1×waiting_approval / 1×running
- When: DailyRoutine 计算 `attention_count`（SD-7 算法：task.status ∈ `{"waiting_input", "waiting_approval", "paused", "escalated", "failed"}`）
- Then: `attention_count == 3`（failed + waiting_input + waiting_approval）；`ROUTINE_COMPLETED.payload.attention_count == 3`

### 块 F — F101 NOTIFICATION_DISPATCHED 集成

**AC-F1** [可独立测试]
- Given: DailyRoutine 调用 `notification_service.notify_task_state_change`
- When: 推送路径执行（无论是否被 quiet hours 过滤）
- Then: event_store 中写入 `NOTIFICATION_DISPATCHED` 事件；`filtered=True` 表示被 quiet hours 过滤，`filtered=False` 表示正常推送；`notification_id` = sha256(`"_daily_routine:{date}:ROUTINE_SUMMARY"`)[:16]，同一天的 daily summary 去重为一条

### task_store 新 API

**AC-T1** [可独立测试]
- Given: `task_store.list_tasks_in_time_range(start=yesterday_start, end=yesterday_end)` 调用
- When: SQLite 查询执行
- Then: 返回 `list[Task]`，仅包含 `created_at` 在 `[start, end)` 范围内的任务；单次查询完成，不需要 Python 层时间过滤；查询耗时 < 500ms（NFR-1）

---

## 5. Functional Requirements

### 块 B — DailyRoutine 主路径

**FR-B1** [必须] `DailyRoutineService` MUST 在 `startup()` 时向 `AutomationSchedulerService` 注册 cron job。
- 文件路径：新建 `apps/gateway/src/octoagent/gateway/services/daily_routine.py`（与 F101 notification.py 同包）
- 注册调用样板：
  ```python
  self._scheduler.add_job(
      self._run_daily_summary,
      trigger=CronTrigger.from_crontab("30 8 * * *", timezone=self._user_timezone),
      id="_daily_routine",
      replace_existing=True,
      misfire_grace_time=30,   # 与 automation_scheduler.py:63 现有约定一致（checklist CHK-2.4）
  )
  ```
- cron 表达式从 `daily_summary_time` 字段动态生成（格式 `"HH:MM"` → `"MM HH * * *"`）
- **异常兜底**（checklist CHK-4.2 / Constitution C6）：`add_job` 抛出异常时 MUST catch + 写 ERROR 日志（结构化字段 `cron_register_failed=true`）+ 不向上传播阻塞 gateway 启动；同时写 `ROUTINE_FAILED` 事件（`error_type="cron_register_failed"`）以便 audit
- 关联 AC：AC-B6

**FR-B2** [必须] `DailyRoutineService._run_daily_summary()` MUST 遵循以下执行顺序：
1. 写 `ROUTINE_TRIGGERED` 事件（`_daily_routine_audit` task_id）
2. 读 USER.md 配置（`routine_active` / `summary_channels`）
3. 若 `routine_active=False`，写 `ROUTINE_SKIPPED(reason="routine_disabled")`，return
4. 计算 `yesterday_start` / `yesterday_end`（按用户本地时区，转 UTC datetime），调用 `task_store.list_tasks_in_time_range(yesterday_start, yesterday_end)`
5. 若 `len(tasks) == 0`（SD-8 空数据），写 `ROUTINE_COMPLETED(worker_count=0, failed_count=0, attention_count=0, fallback=False, summary_length=0)`，**直接 return 不推送通知**
6. 对每个 task，调用 `event_store.get_events_by_types_since(task_id, [STATE_TRANSITION, WORKER_DISPATCHED, WORKER_RETURNED, APPROVAL_REQUESTED, APPROVAL_EXPIRED], yesterday_start)` 获取事件详情
7. 汇总 `worker_count`（昨日 task 总数）/ `failed_count`（task.status == "failed"）/ `attention_count`（task.status ∈ attention_statuses，SD-7 算法）
8. LLM 摘要生成（cheap alias，input ≤ 3000 tokens，超限按 SD-9 优先保留 failed + attention task）；失败时 fallback deterministic 模板
9. 调用 `notification_service.notify_task_state_change(..., channels=summary_channels)`（FR-B7）
10. 写 `ROUTINE_COMPLETED` 事件（含 `elapsed_ms`, `worker_count`, `failed_count`, `attention_count`, `fallback`, `llm_elapsed_ms`, `summary_length`）
- 关联 AC：AC-B1, AC-B3, AC-B5, AC-E1, AC-E4

**FR-B3** [必须] LLM 摘要生成 MUST 有 fallback 路径。
- **LLM 路径**：调用 cheap alias（参照 `observation_promoter.py:468` 的 `_call_categorize_model` 模式）
- **LLM input budget**（SD-9）：input ≤ 3000 tokens；超限时**优先保留** failed + attention task 的 events，其余 task 仅保留 `title + final_status`；超限策略实现细节由 plan 阶段细化
- **LLM output budget**：`max_tokens=512`
- fallback 模板格式（示例）：
  ```
  昨日 Worker 摘要（{date}）：
  - 完成任务：{worker_count} 个
  - 失败任务：{failed_count} 个
  - 待关注：{attention_count} 个
  {若有失败任务：失败原因摘要（task title + to_status）}
  ```
- **fallback 触发条件**：LLM 调用抛出任何异常（timeout / provider error / invalid response）或返回非法（空字符串 / 仅空白）
- **`ROUTINE_COMPLETED.fallback`** 字段标记是否走 fallback；**不写** `ROUTINE_FAILED`（fallback 是 graceful degrade，非失败）
- 关联 AC：AC-B3, AC-E2

**FR-B4** [必须] 当 `attention_count > 0` 时，notification priority MUST 为 `MEDIUM`；否则为 `LOW`。
- 接入点：`notification_service.notify_task_state_change` 调用时动态传入 `priority`
- 关联 AC：AC-B7

**FR-B5** [必须] `DailyRoutineService` MUST 设置审计 task 占位，防止 FK 违规：
- `_DAILY_ROUTINE_AUDIT_TASK_ID = "_daily_routine_audit"`
- 启动时调用 `ensure_system_audit_task(task_store, _DAILY_ROUTINE_AUDIT_TASK_ID)`
- 参照 `observation_promoter.py:40`
- 关联 AC：AC-E1

**FR-B6** [必须] `CancelledError` MUST 显式 re-raise，其他异常 catch 后写 `ROUTINE_FAILED` + ERROR 日志，routine loop 继续。
- 不允许宽泛 `except Exception: pass`（Constitution C6 / M-1 broad-catch 教训）
- 关联 AC：AC-E3

**FR-B7** [必须] `DailyRoutineService.notify_task_state_change` 调用样板：
```python
await self._notification_service.notify_task_state_change(
    task_id="_daily_routine_audit",
    event_type="ROUTINE_DAILY_SUMMARY",
    payload={
        "summary": summary_text,
        "worker_count": worker_count,
        "failed_count": failed_count,
        "attention_count": attention_count,
        "date": yesterday_str,           # "YYYY-MM-DD"
        "fallback": is_fallback,
    },
    priority=NotificationPriority.MEDIUM if attention_count > 0 else NotificationPriority.LOW,
    session_id=None,
    state_transition_event_id=routine_event_id,
    channels=summary_channels,           # frozenset[str]，SD-6 / AC-D3 channel 过滤
)
```
- 关联 AC：AC-F1, AC-D3

**FR-B8** [必须，SD-6 / CHK-3.2 BLOCKER 决议] `NotificationService.notify_task_state_change` MUST 新增 `channels: frozenset[str] | None = None` 参数。
- 语义：`channels=None` 时维持现状（向后兼容，对所有 registered channel push），`channels={"telegram"}` 时仅对 `channel.channel_name ∈ channels` 的 channel 调用 `channel.notify(...)`
- 实现位置：`apps/gateway/src/octoagent/gateway/services/notification.py:notify_task_state_change` 内部的 channel 推送循环加 `if channels is not None and channel.channel_name not in channels: continue` 过滤（保持向后兼容性，所有 F101 现有 caller 不传 channels 时行为不变）
- **channel 名称命名校正**（plan Phase A 实测）：`NotificationChannelProtocol` 当前已暴露 `channel_name: str` 属性（**非 `name`**）。Telegram channel 的 `channel_name == "telegram"`，Web SSE channel 的 `channel_name == "web_sse"`
- **USER.md 值域映射**：`summary_channels` 字段写法 `"telegram,web"`（用户友好），`extract_summary_channels_from_user_md()` 解析时必须将 `"web"` 映射为内部值 `"web_sse"`；最终返回 `frozenset({"telegram", "web_sse"})`
- audit：`NOTIFICATION_DISPATCHED` payload 新增 `channels: list[str] | None` 字段标记本次调用过滤范围
- 关联 AC：AC-D3, AC-F1

### 块 D — USER.md 字段扩展

**FR-D1** [必须] `behavior_templates/USER.md` MUST 在"工作习惯"节新增三个字段（HH:MM 格式、true/false、逗号分隔字符串）：
```markdown
- daily_summary_time: "08:30"
- routine_active: "true"
- summary_channels: "telegram,web"
```
- 机器可读字段采用 Markdown 列表格式（与现有 `active_hours` 字段风格一致）
- 关联 AC：AC-D1, AC-D2, AC-D3, AC-D4

**FR-D2** [必须] 新增三个解析函数，路径：`apps/gateway/src/octoagent/gateway/services/daily_routine_config.py`（独立文件，避免 daily_routine.py 单文件过大；checklist CHK-2.1 决议）：
```python
def extract_daily_summary_time_from_user_md(content: str) -> str:
    """解析 daily_summary_time 字段，非法值返回 "08:30" 并写 WARNING log"""

def extract_routine_active_from_user_md(content: str) -> bool:
    """解析 routine_active 字段，非法值返回 True 并写 WARNING log"""

def extract_summary_channels_from_user_md(content: str) -> frozenset[str]:
    """解析 summary_channels 字段，非法值返回 frozenset({"telegram","web"}) 并写 WARNING log"""
```
- 关联 AC：AC-D1, AC-D2, AC-D3, AC-D4

### 块 E — 可观测性

**FR-E1** [必须] `enums.py` 中新增 4 个 EventType 枚举值（无冲突，tech-research §任务 7 实测）：
```python
ROUTINE_TRIGGERED = "ROUTINE_TRIGGERED"
ROUTINE_COMPLETED = "ROUTINE_COMPLETED"
ROUTINE_FAILED = "ROUTINE_FAILED"
ROUTINE_SKIPPED = "ROUTINE_SKIPPED"
```
- 关联 AC：AC-E1, AC-E2, AC-E3, AC-B2

**FR-E2** [必须] `ROUTINE_COMPLETED` payload schema：
```python
class RoutineCompletedPayload(BaseModel):
    routine_type: Literal["daily"] = "daily"
    date: str                    # "YYYY-MM-DD"（昨日日期）
    worker_count: int            # 昨日 Worker 任务总数
    failed_count: int            # 昨日失败任务数
    attention_count: int         # 昨日结束时仍需关注的任务数
    elapsed_ms: int              # routine 执行总耗时（ms）
    llm_elapsed_ms: int = 0      # LLM 调用耗时（ms），fallback 时为 0
    fallback: bool = False       # 是否走 deterministic fallback 路径
    summary_length: int          # 摘要字符数
```
- 关联 AC：AC-E1, AC-E2

**FR-E3** [必须] `ROUTINE_FAILED` payload 必须含 `error_type` + `error_msg` 字段（不含 traceback 原始文本，避免 PII 泄露）。
- 关联 AC：AC-E3

### task_store 辅助 API

**FR-T1** [必须] `task_store.py` 中新增辅助查询方法（D3 决策）：
```python
async def list_tasks_in_time_range(
    self,
    start: datetime,      # MUST be UTC-aware datetime（SD-10 时区语义）
    end: datetime,        # MUST be UTC-aware datetime；range [start, end) 半开区间
    statuses: list[TaskStatus] | None = None,
) -> list[Task]:
    """
    查询 created_at 在 [start, end) 范围内的 task 列表（UTC）。
    若 statuses 不为 None，额外按 status 过滤。
    底层 SQL：SELECT * FROM tasks WHERE created_at >= :start AND created_at < :end [AND status IN ...]
    调用方负责本地时区 → UTC 转换；NaiveDatetime 输入 MUST 触发 ValueError。
    """
```
- 在 `tasks` 表 `created_at` 列**确认索引存在**（plan Phase 0 实测 `sqlite_init.py` 中 tasks DDL，OQ-1）；若无则 F102 范围内新建 `idx_tasks_created_at`
- 关联 AC：AC-T1

### 依赖注入

**FR-DI1** [必须] `DailyRoutineService.__init__` 接受以下显式 DI 参数：
```python
class DailyRoutineService:
    def __init__(
        self,
        scheduler: AutomationSchedulerService,
        task_store: TaskStore,
        event_store: SqliteEventStore,
        notification_service: NotificationService,
        snapshot_store: SnapshotStore,
        provider_router: ProviderRouter,
    ) -> None: ...
```
- 在 `octo_harness._bootstrap_executors`（或等价 bootstrap 步骤）中构造并注入
- 关联 AC：AC-B6

---

## 6. Non-Functional Requirements

**NFR-1（性能）**：
- `task_store.list_tasks_in_time_range` 单次查询 < 500ms（task 量 ≤ 1000 条；索引 `idx_tasks_created_at` 保证）
- `event_store.get_events_by_types_since` 对单 task 查询 < 200ms（索引 `idx_events_type_ts` 已存在）
- **Routine 触发到 notification 推送完成 P50 < 5s（不含 LLM 调用时间），假设昨日 task 量 ≤ 50**（checklist CHK-6.3 阈值）
- 昨日 task 量 > 50 时，FR-B2 步骤 6 N+1 查询累计可能突破 5s 上限；当前 F102 不实现 batch_get_events，仅在 ROUTINE_COMPLETED 中 audit `elapsed_ms` 以便后续基于实际数据决定是否推 F107 batch API（checklist CHK-6.3 决议）

**NFR-2（可靠性）**：
- LLM 不可用时 fallback 路径必须在 1s 内完成（deterministic 模板，无 IO）
- Routine 崩溃不影响 gateway 其他服务（`CancelledError` re-raise，其他异常 catch 后 loop 继续）

**NFR-3（时区，SD-10 决议）**：
- `daily_summary_time` 字段按 USER.md `active_hours` 同一时区解释（用户本地时区）
- cron trigger 使用 `CronTrigger.from_crontab(..., timezone=user_timezone)`，`user_timezone` 从 USER.md 解析（F101 已有 active_hours 解析基础），默认 UTC
- `task_store.list_tasks_in_time_range` 接受 UTC-aware datetime；DailyRoutineService 负责本地时区→UTC 转换（"昨日"定义为用户本地时区 [yesterday_00:00, today_00:00)，转 UTC 后传入）
- tasks 表 `created_at` 列存 UTC（OctoAgent 标准）

**NFR-4（回归基线）**：
- F102 合入后 passed count >= 3571（F101 baseline），0 regression

**NFR-5（Constitution 合规）**：
- Constitution C6：LLM 不可用时系统不得整体不可用（FR-B3 fallback 满足）
- Constitution C8：每次 routine 执行可查看状态、耗时、是否 fallback（ROUTINE_COMPLETED event 满足）
- Constitution C2：每次 LLM 调用产生 MODEL_CALL_STARTED / MODEL_CALL_COMPLETED / MODEL_CALL_FAILED 事件（复用现有 provider_router 审计）

---

## 7. 数据模型变更

### 7.1 USER.md 模板新增字段（`behavior_templates/USER.md`）

在"工作习惯"节（`active_hours` 字段附近）新增：
```markdown
- daily_summary_time: "08:30"
- routine_active: "true"
- summary_channels: "telegram,web"
```

**注**：`weekly_summary_day` 字段（WeeklyRoutine）不在 F102 中新增，推迟到 WeeklyRoutine Feature。

### 7.2 新增 EventType（`core/models/enums.py`）

```python
# F102 Routine 可观测性
ROUTINE_TRIGGERED = "ROUTINE_TRIGGERED"
ROUTINE_COMPLETED = "ROUTINE_COMPLETED"
ROUTINE_FAILED = "ROUTINE_FAILED"
ROUTINE_SKIPPED = "ROUTINE_SKIPPED"
```

### 7.3 task_store 新 API 签名

```python
# packages/core/src/octoagent/core/stores/task_store.py
async def list_tasks_in_time_range(
    self,
    start: datetime,
    end: datetime,
    statuses: list[TaskStatus] | None = None,
) -> list[Task]:
    ...
```

### 7.4 DailyRoutineService 新文件

```
apps/gateway/src/octoagent/gateway/services/
├── daily_routine.py             # 主类（≤ 350 行约束，checklist CHK-2.1）
└── daily_routine_config.py      # 3 个 USER.md 解析函数 + DailyRoutineConfig dataclass
```

**命名说明**（checklist CHK-1.4）：cron job 标识 `job_id="_daily_routine"`（APScheduler 调度标识）和 event audit task 标识 `_DAILY_ROUTINE_AUDIT_TASK_ID="_daily_routine_audit"` 是两个不同概念——前者是 scheduler 中的 job 唯一键，后者是 event_store FK 占位 task；两者命名相似但**互不关联**。

主要类结构：

```python
class DailyRoutineService:
    _DAILY_ROUTINE_AUDIT_TASK_ID = "_daily_routine_audit"

    async def startup(self) -> None: ...          # 注册 cron + ensure audit task
    async def shutdown(self) -> None: ...         # cancel job
    async def _run_daily_summary(self) -> None:   # 主执行路径
    async def _collect_yesterday_data(self) -> DailyData: ...
    async def _generate_summary_llm(self, data: DailyData) -> str: ...
    def _generate_summary_fallback(self, data: DailyData) -> str: ...
    def _read_config(self) -> DailyRoutineConfig: ...
    def _compute_yesterday_range_utc(self, now_local: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]: ...
```

---

## 8. 接入点（与 F101 复用）

### 8.1 AutomationSchedulerService cron 注册

```python
# automation_scheduler.py:63（D1 决策接入点）
scheduler.add_job(
    self._run_daily_summary,
    trigger=CronTrigger.from_crontab(crontab_expr, timezone=user_tz),
    id="_daily_routine",
    replace_existing=True,
    misfire_grace_time=30,  # 与现有约定一致（checklist CHK-2.4）
)
```

### 8.2 NotificationService 推送（F101 接口扩展 + 复用）

```python
# notification.py: notify_task_state_change（F102 新增 channels 参数，向后兼容）
await notification_service.notify_task_state_change(
    task_id="_daily_routine_audit",
    event_type="ROUTINE_DAILY_SUMMARY",
    payload={...},
    priority=NotificationPriority.MEDIUM if attention_count > 0 else NotificationPriority.LOW,
    session_id=None,
    state_transition_event_id=routine_trigger_event_id,
    channels=summary_channels,  # SD-6 / FR-B8 新增参数
)
```

F101 现有 caller（task_runner / approval_manager / ask_back_tools 等）不传 `channels`，行为不变。F102 是唯一传 `channels` 的 caller（v0.1）。

### 8.3 event_store 查询路径

```python
# event_store.py: get_events_by_types_since（已有，复用索引 idx_events_type_ts）
events = await event_store.get_events_by_types_since(
    task_id=task_id,
    event_types=[
        EventType.STATE_TRANSITION,
        EventType.WORKER_DISPATCHED,
        EventType.WORKER_RETURNED,
        EventType.APPROVAL_REQUESTED,
        EventType.APPROVAL_EXPIRED,
    ],
    since_ts=yesterday_start,
)
```

### 8.4 USER.md 读取路径

```python
# snapshot_store.get_live_state（F101 已建立 SoT 机制）
user_md_content = await snapshot_store.get_live_state("USER.md")
summary_time = extract_daily_summary_time_from_user_md(user_md_content)
routine_active = extract_routine_active_from_user_md(user_md_content)
channels = extract_summary_channels_from_user_md(user_md_content)
```

### 8.5 LLM cheap alias 调用（参照 ObservationRoutine）

```python
# 参照 observation_promoter.py:468 _call_categorize_model 模式
result = await self._provider_router.complete(
    model_alias="cheap",
    messages=[{"role": "user", "content": prompt}],
    max_tokens=512,
)
```

---

## 9. 风险与已知 trade-off

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| **cheap alias 不可用**（LLM provider 故障） | MED | FR-B3 fallback 路径（deterministic 模板），ROUTINE_COMPLETED.fallback=true 可审计 |
| **quiet hours 多日丢失**（daily_summary_time 持续在 quiet hours 内） | LOW | D5 决策：不补发不延迟，用户可调整 daily_summary_time 到 active hours 内；handoff 说明中明确注意事项 |
| **N+1 查询性能**（昨日 task 多时逐一查 event_store） | MED | D3 决策：新增 list_tasks_in_time_range 消除第一层 N+1；event_store 查询已有 idx_events_type_ts 索引，单 task 查询 < 200ms；若 task 量 > 200 考虑 batch_get_events API（推 F107） |
| **cron timezone 边界**（用户跨时区时摘要时间偏移） | LOW | CronTrigger.from_crontab 用 user_timezone 参数，fallback UTC；timezone 解析非法时 WARNING log + fallback UTC |
| **tasks 表无 created_at 索引**（FR-T1 前提） | MED | spec 明确要求实施时检查索引，必要时新建 idx_tasks_created_at |
| **attention_count 语义**（Worker 维度 vs 全局） | LOW | F102 使用全局昨日 task 层面的 attention_count（WAITING_INPUT + WAITING_APPROVAL + failed 等状态），不直接复用 WorkerProfileDynamicContext.attention_work_count（Worker 实时计数） |
| **USER.md 解析无 YAML frontmatter**（与结构化配置的 trade-off） | LOW | 沿用 F101 regex 解析模式，保持一致性；长远 F107 可评估引入 frontmatter 结构化解析 |

---

## 10. 依赖关系

**前置依赖**：

| 依赖 | Feature | 具体接口 | 备注 |
|------|---------|---------|------|
| NotificationService（notify_task_state_change / quiet hours） | F101 ✅ | `notification.py:notify_task_state_change` | F102 微扩展 channels 参数（SD-6） |
| NOTIFICATION_DISPATCHED EventType | F101 ✅ | `enums.py:NOTIFICATION_DISPATCHED` | F102 扩展 payload 新增 `channels` 字段 |
| USER.md SoT 机制（snapshot_store.get_live_state） | F084 ✅ | `snapshot_store.get_live_state("USER.md")` | — |
| AutomationSchedulerService + CronTrigger | F086 ✅ | `automation_scheduler.py:add_job` | — |
| SqliteEventStore.get_events_by_types_since | M3 ✅ | `event_store.py:get_events_by_types_since` | — |
| provider_router cheap alias | F081 ✅ | `provider_router.complete(model_alias="cheap")` | **需 plan Phase 0 实测 cheap alias 是否已配置（OQ-2）；未配置时 LLM 路径永远 fallback** |
| NotificationChannelProtocol.name 属性 | F101 ✅（待 plan Phase 0 实测） | `notification.py:NotificationChannelProtocol` | FR-B8 channel routing 依赖 channel.name；若缺失则 F102 范围内补 |

**后续 Feature**：

| Feature | 依赖 F102 的内容 |
|---------|----------------|
| F103 Blueprint 修订 | F102 DailyRoutine 架构描述 |
| F107 Capability Layer Refactor | DailyRoutineService DI 接口稳定性 |
| WeeklyRoutine（独立 Feature） | DailyRoutineService 基础框架可扩展 |

---

## 11. 测试策略

### 单元测试

| 测试文件 | 覆盖 AC | 说明 |
|---------|---------|------|
| `tests/services/test_daily_routine_config.py` | AC-D1, AC-D2, AC-D3（解析侧）, AC-D4 | 三个解析函数（合法/非法/缺失值的全组合） |
| `tests/services/test_daily_routine_summary.py` | AC-B3, AC-B5, **AC-E3, AC-E4** | LLM 路径 + fallback 路径（mock provider_router）+ **CancelledError 显式 re-raise + attention_count 计算（5 task fixture, SD-7 验证）**（checklist CHK-5.1） |
| `tests/services/test_daily_routine_priority.py` | AC-B4, AC-B7 | quiet hours 过滤（**真实 NotificationService + mock SnapshotStore 注入 quiet hours USER.md**，CHK-1.2）+ attention_count 优先级调升 |
| `tests/services/test_notification_channels.py` | **AC-D3**, FR-B8 | F101 NotificationService.notify_task_state_change(channels=...) 路由验证：channels=None 全推 / channels={"telegram"} 只推 Telegram |
| `tests/stores/test_task_store_time_range.py` | AC-T1 | list_tasks_in_time_range SQL + 边界条件（空结果/大量结果/statuses 过滤 / NaiveDatetime ValueError） |

### 集成测试

| 测试文件 | 覆盖 AC | 说明 |
|---------|---------|------|
| `tests/services/test_daily_routine_integration.py` | AC-B1, **AC-B2**, AC-E1, AC-E2, AC-F1 | 真实 SQLite + 真实 event_store + mock LLM + 真实 NotificationService；验证完整事件链 ROUTINE_TRIGGERED → ROUTINE_COMPLETED → NOTIFICATION_DISPATCHED；**AC-B2 显式覆盖 routine_active=false 跳过路径**（checklist CHK-5.3） |
| `tests/services/test_daily_routine_startup.py` | AC-B6 | 验证 cron job 注册到 AutomationSchedulerService + replace_existing 行为 + cron 注册失败兜底（add_job 抛异常时 gateway 仍能启动） |

### e2e_smoke

- F102 不新增独立 e2e_smoke 域（避免 routine cron 时间依赖使测试不稳定）
- `pytest -m e2e_smoke` 回归 F101 已有的 5 个 smoke 域，确保 0 regression
- F102 相关能力通过集成测试覆盖（mock cron trigger，不依赖实时时钟）

---

## 12. 明确排除事项的理由（防止 review 追问）

**"为什么不做 WeeklyRoutine？"**
WeeklyRoutine 需要 7 天历史数据的趋势分析（"本周 vs 上周失败率"），内容组织复杂度远超 DailyRoutine。F102 完成后，运行 1-2 周的 DailyRoutine 数据将成为 WeeklyRoutine 的自然输入，届时实现更有意义。YAGNI 原则要求当前迭代不做。

**"为什么不做前端配置 UI？"**
USER.md SoT 机制（F084/F101 已建立）覆盖了配置读写需求，Agent 可以帮用户更新 USER.md 中的字段。独立配置 UI 是 M6 Surface 扩张的工作。

**"为什么 task_store 只加 list_tasks_in_time_range 不加全局 event_store 时间查询？"**
event_store 的现有 SQL 索引是 `(task_id, type, ts)` 复合索引，跨 task_id 的全局时间查询会导致全表扫描。正确做法是先通过 task_store（task 层）过滤时间范围，再对每个 task 查 events——两层索引各司其职，维持架构整洁。F102 的 D3 决策已按此设计。

**"为什么不把 LLM fallback 做成重试？"**
重试增加复杂度（需要 backoff、retry budget、错误分类），且 LLM 不可用通常是分钟级故障，重试无法在 5s 内恢复。Deterministic fallback 立即可用，用户仍能收到摘要，是更好的 UX 选择。

---

## 13. 遗留 OPEN QUESTION

**OQ-1：tasks 表 `created_at` 列索引现状**
tech-research.md 未实测 `tasks` 表的索引情况。`list_tasks_in_time_range` 的性能依赖 `created_at` 索引存在。plan Phase 0 必须实测 `sqlite_init.py` 中 tasks 表 DDL，确认是否已有该索引。若无，plan 需增加创建 `idx_tasks_created_at` migration 任务。

**OQ-2：provider_router cheap alias 可用性**
F102 依赖 cheap alias（低成本 LLM）用于摘要生成。plan Phase 0 需确认 cheap alias 在当前 ProviderRouter 配置中已定义（`alias_map` 或等价配置）。若未定义，LLM 路径永远 fallback，spec AC-B3 仍可满足但 LLM 路径无法验收。

---

## 14. 复杂度评估（供 GATE_DESIGN 审查）

| 维度 | 值 | 备注 |
|------|-----|------|
| **新增组件数** | 2 | DailyRoutineService（新建）+ task_store 辅助 API（扩展） |
| **修改组件数** | 3 | enums.py（+4 EventType）+ USER.md 模板（+3 字段）+ NotificationService（+channels 参数，SD-6） |
| **新增/修改接口数** | 6 | DailyRoutineService startup/run + 3 个 USER.md 解析函数 + task_store.list_tasks_in_time_range + NotificationService.notify_task_state_change channels 参数（FR-B8） |
| **引入新外部依赖** | 0 | 全部复用现有组件 |
| **跨模块耦合** | 是（轻度）| 新建 daily_routine.py 调用 automation_scheduler / task_store / event_store / notification_service / snapshot_store / provider_router，6 个依赖全部已存在 |
| **复杂度信号** | 1 | LLM 失败 fallback 是状态分支（非状态机，不算状态机信号） |
| **总体复杂度** | **MEDIUM** | 新增组件 2（< 3 → LOW 边界），修改接口 6（4-8 → MEDIUM 区间），1 个复杂度信号 → MEDIUM |

**MEDIUM 复杂度决议**：计划 4-5 Phase（Phase 0 侦察 + Phase B 主路径 + Phase D USER.md + Phase E 可观测性 + Phase F 集成验证），每 Phase 后跑 e2e_smoke，Codex per-Phase review 和 Final cross-Phase review 均必走。
