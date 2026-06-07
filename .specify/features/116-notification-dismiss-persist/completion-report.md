# F116 Completion Report — notification dismiss/active 跨重启持久化

> 模式：spec-driver-fix
> 分支：`feature/116-notification-dismiss-persist`（基于 origin/master `543a93b`）
> 状态：✅ 实现 + 验证 + Codex 闭环全部完成；已 commit 到 feature 分支，**未 push，待用户拍板**

## 解决的问题（用户视角）

通知的「已读 / 活跃」状态此前纯进程内存，重启即丢。一旦活跃通知具持久化，重启后已读（dismiss）的 LOW/MED 通知会在 Web 重新冒出来——违反 Constitution #1 Durability，用户可直接感知。本 Feature 把 dismiss 与 active 通知元数据落 SQLite，启动 rehydrate：**重启后已读的不再重现，活跃列表不丢**，且 Web / Telegram 两端 dismiss 共享同一持久化状态。

## 实际做了 vs 计划（plan.md C1–C7）

| 计划 | 实际 | 说明 |
|------|------|------|
| C1 SqliteNotificationStore | ✅ `packages/core/.../store/notification_store.py`（115 行） | record/list dismissal + active，JSON round-trip，INSERT OR REPLACE 去重 |
| C2 schema DDL | ✅ `sqlite_init.py` +2 表 +1 索引 | 无 task FK（对齐 memory_extraction_ledger）；`IF NOT EXISTS` 对老库幂等补表，无 migration 命令 |
| C3 StoreGroup 接入 | ✅ `store/__init__.py` | `notification_store` + `__all__` |
| C4 Service 持久化 + rehydrate | ✅ `notification.py` | `dismiss`/`_record_active` 改 async 落盘；新增 `rehydrate()` + `bind_notification_store()`；`is_dismissed`/`list_active` 保持同步读内存 |
| C5 octo_harness 接线 | ✅ `_bootstrap_executors` bind + `await rehydrate()`（降级容错） | |
| C6 生产 caller 改 await | ✅ `routes/notifications.py` + `services/telegram.py` | 2 处 |
| C7 测试 | ✅ 新 store 单测 7 + service 跨重启单测 7 + 更新 f101 6 处 await | |

**未偏离计划。** 无跳过 Phase。

## 关键决策

1. **async `dismiss`（非 fire-and-forget）**：真 durability = ack-after-persist。拒绝方案 B（sync + `create_task`），因其 ack-before-persist 有丢失窗口且 sync 上下文无 loop。代价：dismiss 公共方法签名由 sync→async（生产仅 2 caller，均 async；测试 6 处已改）。
2. **store 落 core StoreGroup**：复用主连接 + init_db + close 生命周期，与既有 11 个 store 同范式，不新造抽象。
3. **active 表以 notification_id 为 PK**：天然去重；rehydrate 比内存 append 多一层一致性（approval 重复 record 在 DB 去重，重启后无重复 active）——轻微行为增强。

## 范围排除（已知 limitations + 漂移）

- **`_notified_set`（去重集合）不持久化**：去重幂等非用户可感知「已读」状态，重启后最多触发一次重发而非「已读重现」；纳入会扩大范围 + 涉 dedup 语义。明确排除。
- **active 表无 TTL/bound**：个人单用户低量场景可接受；增长由真实通知量界定。记为已知 limitation。
- **Blueprint 漂移修复（本 Feature 内同步）**：`core-design.md` §8.10（dismiss 持久化推迟 F107 → 改为 F116 已实现）、`architecture-audit.md` F101/F102 推迟项、`milestones.md` F116 行状态。

## Codex Adversarial Review 闭环（schema 新增强制）

verdict 首轮 **needs-attention**：2 HIGH + 1 MEDIUM，**全部接受并修复**（决策在主 session）：

| # | severity | finding | 处理 |
|---|----------|---------|------|
| H1 | high | dismiss/_record_active 落盘失败被 warning 吞掉，仍对外报成功 → 与 ack-after-persist/durability 矛盾 | **接受改**：`dismiss` 返回 `bool`（durable 真值）；Web route 透传 `persisted` 字段；fix-report/docs 把"保证 durable"改为"best-effort + 降级诚实暴露"。保留 #6 不升级 500 |
| H2 | high | rehydrate 未恢复 `_notified_set` + dispatch 不查 `_dismissed_set` → 重启后同一 task/event 重放会重复推 Telegram/SSE，甚至重推已 dismiss 的通知 | **接受改**：rehydrate 用 active∪dismissed id 种子 `_notified_set`；`notify_task_state_change` 增 dismissed-guard（approval/CRITICAL 不受影响，待办需可重浮现）。补 2 回归测试 |
| M1 | medium | `notification_active` 无 FK/cascade + 无 delete 方法 → 删 session/task 后重启 rehydrate 复活 stale 通知（泄露 payload 用户数据）| **接受改**：`SqliteNotificationStore.delete_by_task_ids`（连带清 dismissals）接入 `delete_session_cascade`。补 store + service 删除测试 |

二轮自审（grep + 聚焦回归）：H1/H2/M1 修复后聚焦套件 58 passed；无新 HIGH 引入。**0 HIGH 残留**。

## 验证结果

- 聚焦单测（修复后）：notification_store(含 delete) + f116_persist(含 H1/H2/M1) + f101 全量 —— **58 passed**；notification/telegram/control_plane/session 删除 —— 109 + 77 passed。
- 全量回归（`-m "not e2e_live"`，PYTHONPATH 锁 worktree）：**3792 passed / 0 failed**，10 skipped，77 deselected（e2e_live），1 xfailed，1 xpassed（119s）。新增测试已计入；**0 regression**。
- e2e_smoke（pre-commit 门禁）：**8 passed**。
- e2e_live 真 LLM（`test_domain_8_real_llm_delegate_task`）：环境无 SiliconFlow 凭证（provider enabled=False）导致失败，**与本改动无关**（不碰 delegation/LLM），非 regression，已从回归基线排除。

## AC 闭环（tasks.md）

| AC | 绑定测试 | 结果 |
|----|----------|------|
| AC-1 dismiss 跨重启 | `test_dismiss_survives_restart` | PASS |
| AC-2 active 跨重启 + dismissed 过滤 | `test_active_survives_restart` / `test_dismissed_filtered_after_rehydrate` | PASS |
| AC-3 quiet hours 仍写 event 不落 active | `test_quiet_hours_still_writes_event_no_active` + 既有 `test_h6_*` | PASS |
| AC-4 store=None 降级 | `test_store_none_degrades` / `test_rehydrate_failure_degrades` | PASS |
| AC-5 0 regression + e2e_smoke | 全量 3792 passed / 0 failed + e2e_smoke 8 passed | PASS |

## Codex 修复新增 AC（H1/H2/M1）

| AC | 绑定测试 | 结果 |
|----|----------|------|
| H1 dismiss 返回 persisted 真值 | `test_dismiss_returns_persisted_status` / `_false_without_store` / `_false_on_persist_error` | PASS |
| H2 重启后不重复推送已发通知 | `test_no_repush_after_restart_for_sent_notification` | PASS |
| H2 已 dismiss 不再派发 | `test_dismissed_notification_never_dispatched` | PASS |
| M1 删除 task 后重启不复活 | `test_deleted_task_not_resurrected_after_restart` + store `test_delete_by_task_ids_*` | PASS |
