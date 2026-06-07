# F116 修复任务清单

> 顺序：C1→C2→C3（core 自底向上）→ C4→C5→C6（gateway）→ C7（测试）→ 验证。

| ID | 任务 | 文件 | AC↔test 绑定 |
|----|------|------|--------------|
| T1 | 新建 `SqliteNotificationStore`（record/list dismissal + active，JSON 序列化，INSERT OR REPLACE） | `packages/core/.../store/notification_store.py` | `test_notification_store.py::test_*` |
| T2 | DDL 2 表 + 索引，注册进 `init_db` | `.../store/sqlite_init.py` | store 单测建表即覆盖 |
| T3 | `StoreGroup` 挂 `notification_store` + `__all__` | `.../store/__init__.py` | e2e_smoke bootstrap |
| T4 | Service：`bind_notification_store` + async `dismiss` 落盘 + async `_record_active` 落盘 + `rehydrate()` | `apps/gateway/.../services/notification.py` | `test_f116_notification_persist.py` |
| T5 | octo_harness bind + `await rehydrate()`（降级容错） | `apps/gateway/.../harness/octo_harness.py` | e2e_smoke |
| T6 | 2 生产 caller 改 `await dismiss` | `routes/notifications.py`、`services/telegram.py` | 既有 + 新单测 |
| T7 | 更新 `test_f101_notification.py` 6 处 dismiss 改 await/async | `apps/gateway/tests/test_f101_notification.py` | 自身 |
| T8 | 新增 F116 service rehydrate 单测 + store 单测 | `test_f116_notification_persist.py`、`test_notification_store.py` | 自身 |

## AC（验收标准）

- **AC-1**：dismiss 后新实例 rehydrate → `is_dismissed(id)` 为 True（跨重启不重现）。→ `test_f116_notification_persist.py::test_dismiss_survives_restart`
- **AC-2**：record active 后新实例 rehydrate → `list_active(session_id)` 返回该通知；若已 dismiss 则不返回。→ `::test_active_survives_restart` / `::test_dismissed_filtered_after_rehydrate`
- **AC-3**：quiet hours 被过滤的通知仍写 `NOTIFICATION_DISPATCHED` event（filtered=True）。→ `::test_quiet_hours_still_writes_event`
- **AC-4**：`notification_store=None` 时 dismiss/_record_active/rehydrate 不抛（Constitution #6 降级）。→ `::test_store_none_degrades`
- **AC-5**：0 regression vs 543a93b + e2e_smoke 通过。→ 全量回归
