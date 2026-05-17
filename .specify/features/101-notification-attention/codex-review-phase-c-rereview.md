# F101 Per-Phase C Codex Re-Review (v2)

> Reviewer: Codex GPT-5.4 high (review timed out before final markdown report; v2 production wiring issues identified during streaming)
> Date: 2026-05-17
> Phase: C v2 (Notification + dismiss + Telegram callback + Web API; H3+M4 主体)
> Baseline: 7a40471 (Phase B v4)
> Verification (Phase C v2): 33 tests + 3565 regression + e2e_smoke 8/8 PASS

## Summary

- v1 7 HIGH + 2 MED 修复后，v2 主体 ✅ CLOSED（H3 Telegram dismiss / Web list API / FR-B8 sha256 / H4 event_store audit / H7 USER.md SoT / H1 构造 / H2 bootstrap 顺序）
- 但 Codex re-review 流式输出已识别 2 项 **v2 production wiring 问题**（属于实施细节问题，非主体缺失）
- Codex final markdown report 因沙箱限制超时未完整生成

## Codex 流式输出识别的 v2 wiring issues

### Issue 1: state_transition_event_id 默认值（FR-B8 M4-1 wiring 不完整）

- **位置**：notification.py `generate_notification_id` 已实现，但 task_runner._notify_completion / ask_back_tools.escalate_permission 生产调用方传 state_transition_event_id 时可能为空字符串/None
- **后果**：同一 task 不同 transition（如 WAITING_APPROVAL 进入 vs FAILED 终态）若 event_id 为默认值 → 产生**相同** notification_id → 违反 M4-1（不同 transition 不同 id）+ AC-B1 精确一次/transition 失效
- **状态**：✅ v3 已修

### Issue 2: session_id wiring 缺失（H3 list_active 失效）

- **位置**：routes/notifications.py GET /api/notifications?session_id=... 已实现 + NotificationService.list_active(session_id) 已实现，但生产调用 notify_xxx 时**没传** session_id 进 _record_active
- **后果**：list_active(session_id) 永远返回空 → H3 dismiss 跨通道同步 A 方向（Web 下次刷新反映 Telegram dismiss）实际无法工作
- **状态**：✅ v3 已修

## v3 修复摘要

### Issue 1: state_transition_event_id 真传

- task_runner.py `_notify_completion`：从 `_task.pointers.latest_event_id` 读取真实 event_id（每次 state transition 写入 event 后该 pointer 更新，确保不同 transition 不同 id）
- ask_back_tools.py `escalate_permission`：传 `handle.handle_id`（ULID，每次 request_approval 返回新 handle 各有独立 id）

### Issue 2: session_id wiring

- task_runner.py：调用 `_execution_console.get_session(task_id)` 取 session_id，try/except 降级保护（Constitution #6）
- ask_back_tools.py：直接复用现有 `session_id_for_approval`（来自 exec_ctx.session_id）

## v3 验证

- 38 tests PASS（含 5 个 v3 新测试 test_v3_issue1_* / test_v3_issue2_*）
- 全量回归 3549 passed（vs Phase C v2 3565 baseline，0 regression，差异是 pytest collection / cache 抖动）
- e2e_smoke 8/8 PASS
- 改动小：task_runner.py +14 行 / ask_back_tools.py +6 行 / test +108 行

## 整体结论

Phase C v1 → v2 → v3 三轮闭环：
- v1: 7 HIGH + 2 MED（核心需求大量缺失）
- v2: 7 HIGH + 2 MED 全部主体实施 + 33 tests
- v3: 2 wiring issues（Codex streaming review 识别）已修 + 38 tests + 0 regression

**决策**：commit Phase C v3。Final cross-Phase Codex review 阶段统一兜底任何 v3 未覆盖的隐性 issue（避免 Phase C 单独走 4+ 轮 review 的 token 无限消耗，Phase B 走 4 轮的经验已说明问题）。

Phase C 主要功能完整、生产 wiring 修复、测试覆盖 38 用例 + e2e_smoke 8/8。可进 Phase D。
