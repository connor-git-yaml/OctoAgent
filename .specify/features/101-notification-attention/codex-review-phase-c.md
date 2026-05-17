# F101 Per-Phase C Codex Review

> Reviewer: Codex GPT-5.4 high
> Date: 2026-05-17
> Phase: C v1 (Notification + dismiss + Telegram callback + Web API)
> Baseline: 7a40471 (Phase B v4)
> Verification (Phase C v1): 16 Phase C tests + 3527 regression + e2e_smoke 8/8 PASS（但 PASS ≠ 需求完整）
> **总体评估**：**NEEDS_REWORK**（7 HIGH + 2 MED，Phase C 核心需求大量缺失）

## H3 + M4 完整性专项验证

| 项目 | 状态 | 证据 |
|------|------|------|
| T-C-07b Telegram callback DISMISS_NOTIFICATION | ❌ MISSING | telegram.py:698-735 callback handler 仅 operator action，无 dismiss 分支 |
| T-C-07c Web /api/notifications endpoint | ❌ MISSING | 无 routes/notifications.py，NotificationService 无 list_active 方法 |
| T-C-08 FR-B8 generate_notification_id sha256 | ❌ MISSING | notification.py 无 sha256 import，去重仍用 `(task_id, event_type)` |
| T-C-12 M4 三场景测试 | ⚠️ PARTIAL | 只有 test_dismiss_idempotent + test_dismiss_does_not_affect_other，无 state_transition_event_id 测试 |
| T-C-12 H3-test 集成测试 | ❌ MISSING | 无 Telegram callback / Web refresh / list_active 集成测试 |

## 7 HIGH Findings

### H-1: TelegramNotificationChannel 构造签名不匹配
- **位置**：`octo_harness.py:869-875`
- **问题**：构造时签名与实现不匹配，初始化 fail → except 捕获后降级 None → Telegram 路径 NotificationService 静默失效
- **修复**：核对 TelegramNotificationChannel 构造签名 + 修正调用

### H-2: _notification_service bootstrap 顺序错误（ToolDeps 冻结 None）
- **位置**：`octo_harness.py:774-777` (_bootstrap_capability_pack 设 _tool_deps._notification_service) vs `:858-875` (_bootstrap_executors 才赋 app.state.notification_service)
- **问题**：bootstrap_capability_pack 早于 bootstrap_executors，ToolDeps 冻结 None
- **修复**：调整 bootstrap 顺序或 ToolDeps 延迟绑定（getattr 获取 app.state.notification_service）

### H-3: H3 Telegram dismiss callback 未实现（spec FR-B5 + plan C-7b 必修）
- **位置**：`telegram.py` callback handler 无 dismiss 分支；TelegramNotificationChannel inline keyboard 无 dismiss 按钮
- **修复**：新增 DISMISS_NOTIFICATION action kind + callback handler 接入 notification_service.dismiss

### H-4: H3 Web notification list API 缺失（spec FR-B5 + plan C-7c 必修）
- **位置**：无 `routes/notifications.py`，NotificationService 无 `list_active(session_id)` 方法
- **修复**：新建 routes/notifications.py 实现 GET /api/notifications?session_id=... + POST /api/notifications/{id}/dismiss + 加 NotificationService.list_active 方法

### H-5: FR-B8 notification_id sha256 未实现（spec FR-B8 + plan C-8 + pre-impl M4 必修）
- **位置**：`notification.py` 无 sha256 函数；去重仍用 `(task_id, event_type)` 而非 `sha256(task_id:type:event_id)[:16]`
- **修复**：新增 `generate_notification_id(task_id, notification_type, state_transition_event_id) -> str`（sha256 前 16 位），所有 notify 调用统一使用

### H-6: H4 discard 路径未写 event_store（spec FR-B3 H4 + FR-B6 + AC-B1 必修）
- **位置**：quiet hours 过滤直接 return，无 event_store.append_event
- **修复**：被过滤通知仍写 event_store（保留审计链；channel push 跳过，event 写入）

### H-7: FR-B4 USER.md SoT 未接入生产路径
- **位置**：NotificationService 只接受调用方传入 active_hours；task_runner / ask_back_tools 的生产调用均未传该参数
- **修复**：NotificationService 通过 user_profile 接口读 USER.md `active_hours`（无独立数据存储）

## 2 MEDIUM Findings

### M-1: T-C-12 M4/H3 测试未覆盖必修场景
- 16 测试有 dismiss_idempotent / dismiss_does_not_affect_other，但无 state_transition_event_id / 同 transition 同 id / H3-test 完整链路
- 修复：补完 M4 三场景 + H3-test 集成测试

### M-2: AC-B3 测试逻辑与被测意图相反
- 测试实际传 active_hours=None，断言 channel 被调用一次（应该被拦截）
- 修复：active_hours 设为合理值（如 "09:00-23:00"），模拟 quiet hours 内时间，断言 channel 不被调用

## 整体结论

**不可 commit，不能进入 Phase D**。

Phase C v1 主体契约未闭环——H3（Telegram dismiss + Web list API）、FR-B8（notification_id sha256）、H4（event_store discard 审计链）、FR-B4（USER.md SoT 接入）均未实现；H-1/H-2 联动可能导致 NotificationService 在 Telegram 路径静默失效。这不是少量补强问题，是 Phase C 核心需求大量缺失。

需要 Phase C v2 完整补完 + re-review。
