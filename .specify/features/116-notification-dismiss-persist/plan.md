# F116 修复规划（精简）

> 基于 fix-report.md 推荐方案 A。聚焦最小化变更范围 + 回归风险 + 验证方案。

## 变更清单（按依赖顺序）

### C1 — 新建 store（core）
`packages/core/src/octoagent/core/store/notification_store.py`
- `class SqliteNotificationStore`，`__init__(self, conn)`。
- `async record_dismissal(notification_id, source)` → `INSERT OR REPLACE INTO notification_dismissals` + commit。
- `async list_dismissed() -> set[str]`。
- `async record_active(entry: dict)` → `INSERT OR REPLACE INTO notification_active(...)`（payload JSON 序列化）+ commit。
- `async list_active_all() -> dict[str, list[dict]]`（按 session_id 聚合，payload JSON 反序列化）。

### C2 — schema DDL（core）
`packages/core/src/octoagent/core/store/sqlite_init.py`
- 新增 `_NOTIFICATION_DISMISSALS_DDL` / `_NOTIFICATION_ACTIVE_DDL` / `_NOTIFICATION_ACTIVE_INDEXES`。
- `init_db`：在表创建段 + 索引段注册（CREATE TABLE/INDEX IF NOT EXISTS → 对既有库幂等，老库自动补表，无需 migration 命令）。
- 两表无 task FK（对齐 `memory_extraction_ledger`）。

### C3 — StoreGroup 接入（core）
`packages/core/src/octoagent/core/store/__init__.py`
- `from .notification_store import SqliteNotificationStore`。
- `StoreGroup.__init__`：`self.notification_store = SqliteNotificationStore(conn)`。
- `__all__` 追加。

### C4 — Service 持久化 + rehydrate（gateway）
`apps/gateway/src/octoagent/gateway/services/notification.py`
- `__init__` 增 `notification_store: Any | None = None` 字段 + `bind_notification_store()`。
- `dismiss` → **async**：内存 add（保同步读可见）后 `await self._notification_store.record_dismissal(...)`（None 降级，try/except 静默）。
- `_record_active` → **async**：内存 append 后 `await self._notification_store.record_active(entry)`（None 降级）。两个调用点（`notify_task_state_change` / `notify_approval_request`）已是 async，改 `await self._record_active(...)`。
- 新增 `async def rehydrate()`：`self._dismissed_set = await store.list_dismissed()`；`self._active_notifications = await store.list_active_all()`；None / 异常降级为空（保持 baseline 行为）。
- `is_dismissed` / `list_active` 不变（同步读内存）。

### C5 — 接线 + rehydrate（gateway）
`apps/gateway/src/octoagent/gateway/harness/octo_harness.py` `_bootstrap_executors`（L928 后）
- `_notif_store = getattr(store_group, "notification_store", None)`；非空则 `bind_notification_store` + `await _notification_service.rehydrate()`（try/except 降级，不阻断 bootstrap）。

### C6 — 生产 caller 改 await（gateway）
- `routes/notifications.py:70` → `await notification_service.dismiss(notification_id, source="web")`。
- `services/telegram.py:779` → `await self._notification_service.dismiss(notification_id, source="telegram")`。

### C7 — 测试
- `test_f101_notification.py`：6 处 `svc.dismiss(...)` → `await`，相应测试函数确保 async（pytest.mark.asyncio）。
- 新增 `apps/gateway/tests/test_f116_notification_persist.py`：
  - 跨实例 rehydrate：实例 A dismiss + record active → 落同一 DB；实例 B rehydrate → `is_dismissed` True、`list_active` 不含已 dismiss。
  - active 持久化：record → 新实例 rehydrate → list_active 返回。
  - quiet hours discard 仍写 event：filtered 路径仍 `await _write_notification_audit_event`（既有断言复核，确保未被破坏）。
  - store=None 降级：dismiss/_record_active/rehydrate 不抛。
- 新增 `packages/core/tests/.../test_notification_store.py`：store CRUD + INSERT OR REPLACE 去重 + JSON round-trip。

## 回归风险评估

| 风险 | 缓解 |
|------|------|
| dismiss 由 sync 改 async 破坏调用方 | 生产仅 2 caller（均 async）+ 测试 6 处，全部已枚举改 `await` |
| 新表破坏既有 init_db 老库 | `CREATE TABLE/INDEX IF NOT EXISTS` 幂等，无 migration；老库首次启动自动补表（空表 = baseline 行为） |
| rehydrate 在 bootstrap 抛异常阻断启动 | try/except 降级为空集合（Constitution #6），等价 baseline |
| 主连接 FK OFF 下两表无 FK | 设计即无 FK（UI 状态），不依赖 task 存在性 |
| worktree .venv symlink 跑到 master src 假 0 | 跑测试 PYTHONPATH 锁 worktree（见 verify 约束） |

## 验证方案

1. 新增/更新单测全绿。
2. 全量回归 vs 543a93b baseline 0 regression（PYTHONPATH 锁 worktree）。
3. `pytest -m e2e_smoke` 必过。
4. Codex adversarial review（schema 新增）→ 0 HIGH 残留。
5. living-docs 漂移闸 → completion-report 记录。
