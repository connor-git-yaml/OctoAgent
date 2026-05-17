# F101 → F102 Handoff

> From: F101 Notification + Attention Model（完成 commit `d464fdb`, READY_TO_MERGE）
> To: F102 Proactive Followup（Hermes Routine—— daily/weekly 主动产出"昨日 Worker 跑了什么"摘要）
> Date: 2026-05-18

## 1. F101 给 F102 留下的可复用基础设施

### 1.1 Notification 实体 + event_store 审计链

**接入点**：F101 已建立完整 notification event_store 审计链：

```python
from octoagent.core.models.enums import EventType
EventType.NOTIFICATION_DISPATCHED  # F101 新增
```

每条 notification（含 quiet hours 内被过滤的）都写入 event_store，payload 含：
- `notification_id`（sha256(task_id:type:state_transition_event_id)[:16]）
- `priority`（CRITICAL/HIGH/MEDIUM/LOW）
- `filtered`（True/False，True 表示被 quiet hours 过滤但仍记录）
- `task_id` / `session_id` / `notification_type` / `state_transition_event_id`

**F102 可直接查询 event_store**：
```python
notifications = await event_store.query(
    event_type=EventType.NOTIFICATION_DISPATCHED,
    since=yesterday_start,
    until=yesterday_end,
)
# 产出"昨日 Worker 跑了什么"摘要
```

### 1.2 NotificationService（可触发 daily summary 推送）

`notification.py` 实例化路径：
- `octo_harness._bootstrap_executors` 创建 `app.state.notification_service`
- F102 Routine 启动时可通过 `app.state.notification_service` 触发 daily summary 通知（priority=MEDIUM/LOW）

**注意 quiet hours 策略**：F102 daily summary 通常应在 active hours 推送（user_profile.active_hours 内），quiet hours 内会被 discard（按 F101 FR-B3 决议 A）。建议 F102 routine 配置 cron 在 active hours 开始时触发。

### 1.3 attention_work_count 字段（Attention Model 输入信号）

`WorkerProfileDynamicContext.attention_work_count` 字段（`models/control_plane/agent.py:55`）：
- F101 验证已通过 worker_service.py:1472 动态计算（dispatch 开始 +1 / 任务终态 -1）
- F102 可作为 Attention Model 输入：高 attention_work_count 表示用户当前关注度高，可能不需要 daily summary 打扰

### 1.4 Telegram + Web 通知 channel

F101 已建立两个通道：
- **TelegramNotificationChannel**（`notification.py:318`）：支持 inline keyboard + dismiss button
- **SSENotificationChannel**（`notification.py:237`）：通过 SSEHub 推送 Web SSE 事件

F102 daily summary 可复用：
```python
notification_service.notify_task_state_change(
    task_id=daily_summary_task_id,
    new_state=TaskStatus.SUCCEEDED,
    priority=NotificationPriority.MEDIUM,
    session_id=current_session,
    state_transition_event_id=summary_event_id,
)
```

### 1.5 Web /api/notifications endpoint

`routes/notifications.py`：
- `GET /api/notifications?session_id=...`：list_active（自动过滤 dismissed）
- `POST /api/notifications/{id}/dismiss`：dismiss notification

F102 daily summary 通知会自动出现在该 endpoint 的查询结果中。

### 1.6 USER.md SoT 机制

F101 通过 `snapshot_store.get_live_state("USER.md")` 读取 active_hours / approval_timeout_seconds 字段。

F102 可扩展 USER.md：
- `daily_summary_time: "08:30"` — 用户偏好的 daily summary 时间
- `summary_channels: telegram,web` — 用户偏好的通道
- `routine_active: true` — 用户是否启用 Routine

## 2. F101 已知 limitation（F102 可受益于解决）

### 2.1 dismiss 内存 set 重启清空（LOW，已归档 F107）

F102 daily summary 通知可能跨多天保留——F107 持久化 dismissed set 后，F102 用户体验会更好（已 dismiss 的 daily summary 不会重启后重现）。

### 2.2 full recall production telemetry（LOW，建议 F102 实施时加）

ask_back resume 后跑 full recall 是 F100 设计预期，但生产负载下耗时未量化。F102 实施 Routine 时可加：
- F102 Routine 启动时检查 full recall 是否被 force_full_recall hint 触发
- 加 metric 记录耗时（trace key: `force_full_recall_elapsed_ms`）
- 异常长 trace 自动 escalate

## 3. F102 Proactive Followup 设计建议

### 3.1 Routine 触发模型

参考 Hermes Agent Routine 模式（CLAUDE.local.md _references/opensource/hermes-agent/）：
- **daily**：每天 active hours 开始时（如 08:30）跑一次
- **weekly**：每周一 08:30 跑 weekly 摘要

实现方式：
- 复用 OctoAgent F086 APScheduler（已有 cron 框架）
- Routine 内部调 LLM 产出 summary（按 attention_work_count / event_store 查询结果）
- 产出 summary 后调 notification_service.notify_xxx 推送

### 3.2 摘要内容数据源

| 数据源 | 接口 | F101 提供 |
|--------|------|---------|
| 昨日完成 Worker 数 | event_store.query(NOTIFICATION_DISPATCHED, since=yesterday)| ✅ |
| 昨日 approval_pending 数 | event_store.query(APPROVAL_REQUESTED, since=yesterday) | ✅ |
| 用户活跃时段 | snapshot_store.get_live_state("USER.md") active_hours | ✅ |
| Worker attention_work_count 趋势 | event_store + WorkerProfileDynamicContext | ✅ |
| 失败任务详情 | task_store.list_tasks(status=FAILED, since=yesterday) | ✅ |

### 3.3 推送时机

- 用户 active_hours 开始时（用 USER.md `active_hours` 解析后的 start_time）
- 如果 active_hours 不存在或非法 → fallback 08:30
- 跳过周末（用户偏好可配置）

### 3.4 用户控制

- USER.md `routine_active: false` → 跳过 daily/weekly Routine
- USER.md `daily_summary_channels: telegram` → 只推 Telegram，不推 Web
- 摘要 notification 支持 dismiss（按 F101 dismiss 机制）

## 4. F102 实施建议 Phase 顺序

按 F101 教训：

1. **Phase 0 侦察**：实测 F086 APScheduler 是否支持 Routine 注册 / event_store 查询 API（是否支持 daily/weekly 查询）
2. **Phase A Routine 框架**：APScheduler + Routine registration + 触发逻辑
3. **Phase B Summary LLM**：摘要生成 + LLM call + format
4. **Phase C 推送集成**：notification_service.notify_xxx 调用 + USER.md `routine_active` 检查
5. **Phase D 测试 + verify**

每 Phase 后跑 Codex per-Phase review（F101 实证：每 Phase 至少 1-3 finding 价值）。

## 5. F101 → F107 推迟项

| Item | Severity | 描述 |
|------|----------|------|
| dismiss 持久化 | LOW | NotificationService._dismissed 内存 set 改持久化（重启不丢） |
| FR-D4 API 显式 force_full_recall 参数 | SHOULD | 用户/admin 通过 API 显式传 force_full_recall |
| FR-E1 ControlPlaneService.notification_service 参数 | SHOULD | 当前 SKIP，F107 评估是否需要 |
| WorkerProfile/AgentProfile 完全合并 | HIGH | F090 D2 推迟项，与 F107 主范围一致 |

## 6. F101 → F107 跨 Feature 依赖关系

F107 Capability Layer Refactor 起步前应已合入 F101——F101 已建立的接口（NotificationService / ApprovalGate.task_id 参数 / Web /api/notifications routes）应保持稳定。

F107 不应：
- 破坏 NotificationService 公共接口（notify_xxx / dismiss / list_active）
- 破坏 ApprovalGate.request_approval(task_id=...) 签名
- 破坏 NOTIFICATION_DISPATCHED EventType 语义

F107 可改：
- NotificationService 内部 _dismissed 实现（加持久化）
- ControlPlaneService 增 notification_service 参数（如确实需要）
- ToolDeps / Harness layer 重组（D9/D11/D12 架构债）

## 7. 测试基础（F102 可复用）

F101 测试套件（130 测试）位置：
- `apps/gateway/tests/test_chat_force_full_recall.py`（Phase A 26）
- `apps/gateway/tests/test_f101_phase_b.py`（Phase B 44）
- `apps/gateway/tests/test_f101_notification.py`（Phase C 38）
- `apps/gateway/tests/services/test_f101_ask_back_integration.py`（Phase D 14）
- `apps/gateway/tests/services/test_f101_phase_f_acceptance.py`（Phase F 8）

F102 可复用 fixture / mock 模式：
- NotificationService mock 模式
- SQLite + EventStore + ExecutionConsoleService 真实链路 integration test 模式（Phase D）
- spy is_recall_planner_skip 模式（Phase F）

## 8. F102 启动 checklist

- [ ] 等 F101 合入 origin/master（用户拍板 push 后）
- [ ] 创建 F102 worktree：`git worktree add -b feature/102-proactive-followup .claude/worktrees/F102 origin/master`
- [ ] 必读 F101 codex-review-final.md + completion-report.md + 本 handoff.md
- [ ] 启动 spec-driver-feature：`/spec-driver:spec-driver-feature F102 Proactive Followup`
- [ ] spec 阶段必读 Hermes Agent Routine 实现（`_references/opensource/hermes-agent/`）
- [ ] Phase 0 实测 F086 APScheduler 接口
