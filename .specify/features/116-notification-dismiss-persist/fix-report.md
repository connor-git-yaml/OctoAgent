# F116 问题修复报告 — notification dismiss/active 跨重启持久化

> 模式：spec-driver-fix（快速修复）
> 特性目录：`.specify/features/116-notification-dismiss-persist/`
> 特性分支：`feature/116-notification-dismiss-persist`
> baseline：origin/master `543a93b`（含 d2936e0 F114）
> 在线调研：项目未配置 online research → 跳过

## 问题描述

通知的「已读 / 活跃」状态纯进程内存，进程重启后丢失。`NotificationService` 用三个内存结构维护通知状态：

- `_dismissed_set: set[str]`（FR-B5 dismiss 幂等集合，跨 Web / Telegram 共享）
- `_active_notifications: dict[str, list[dict]]`（H-4，session_id → 通知元数据列表，供 Web `list_active` 刷新）
- `_notified_set: set[str]`（FR-B8 去重集合，**本次范围外**）

用户可感知后果：重启后 `_dismissed_set` 清空。一旦活跃通知具备持久化（本 Feature 的目标），重启后已 dismiss 的 LOW/MED 通知会在 Web 重新冒出来——违反 **Constitution #1 Durability**（“进程重启后任务状态不消失”）。

## 5-Why 根因追溯

| 层级 | 问题 | 发现 |
|------|------|------|
| Why 1 | 重启后已读通知为何重现？ | `_dismissed_set` 是进程内存 set，重启清空 |
| Why 2 | dismiss 状态为何只在内存？ | `dismiss()` 仅 `self._dismissed_set.add(id)`，无任何落盘；`_record_active` 同理仅写内存 dict |
| Why 3 | 为何当初只做内存？ | F101 Phase C 引入 dismiss / list_active 时，聚焦优先级 + quiet hours + 跨通道幂等，把持久化作为后续项推迟（CLAUDE.local.md F101「推迟到 F107：dismiss 持久化（重启清空 LOW）」，M6 审计后下沉为独立地基件 F116） |
| Why 4 | 推迟为何成了缺陷？ | M6 端到端 review 对照 Constitution，判定「dismiss/active 纯内存 + 重启重现」是用户可感知的 Durability 违反，不能继续挂账 |
| Why 5 | 为何未被现有机制捕获？ | 既有单测全部在**单进程内**构造 `NotificationService()`，从不模拟「重启 = 新实例 rehydrate」；无跨实例持久化断言，测试盲区 |

**Root Cause**：`NotificationService` 的 dismiss / active 状态从未接入任何持久层（SQLite），且测试从不覆盖跨实例 rehydrate，导致重启即丢失。

**Root Cause Chain**：重启后已读通知重现 → `_dismissed_set` 内存清空 → dismiss/active 仅写内存无落盘 → F101 把持久化推迟 → 测试无跨实例断言未捕获。

## 影响范围扫描

### 同源问题（需同步修复）

| 文件 | 位置 | 模式 | 修复动作 |
|------|------|------|----------|
| `apps/gateway/src/octoagent/gateway/services/notification.py` | `__init__` / `dismiss` / `_record_active` | 三内存结构无落盘 | 注入 `notification_store`；`dismiss` 落盘；`_record_active` 落盘；新增 `rehydrate()` |
| `packages/core/src/octoagent/core/store/` | 新建 `notification_store.py` | 复用现有 `Sqlite*Store(conn)` 范式 | 新建 `SqliteNotificationStore` |
| `packages/core/src/octoagent/core/store/sqlite_init.py` | `init_db` | DDL 集中注册 | 新增 2 张表 DDL + 索引 |
| `packages/core/src/octoagent/core/store/__init__.py` | `StoreGroup` / `create_store_group` / `__all__` | 共享主连接构造各 store | 挂 `notification_store` |
| `apps/gateway/src/octoagent/gateway/harness/octo_harness.py` | `_bootstrap_executors` L928 | 构造 `NotificationService` | bind store + `await rehydrate()` |

### 类似模式（需评估）

| 文件 | 位置 | 模式 | 评估结果 |
|------|------|------|----------|
| `notification.py` `_notified_set` | L230/L522 | 同为内存 set（去重） | **本次范围外**：去重幂等不是用户可感知的「已读状态」，且重启后即便重新推送一次也仅是「通知重发」而非「已读重现」；纳入会扩大范围且涉及 dedup 语义。明确排除，记入 completion 已知项 |
| `dismiss()` 调用方（生产）| `routes/notifications.py:70`（async）、`services/telegram.py:779`（async） | 同步调用 dismiss | 均在 async 上下文 → 改 `await`（dismiss 变 async 以保证 ack-after-persist 真 durability） |
| `dismiss()` 调用方（测试）| `test_f101_notification.py` 6 处 | 同步调用 dismiss | 改 `await` + async test（持久化是契约的一部分） |

### 同步更新清单

- 调用方：`routes/notifications.py` dismiss handler → `await`；`telegram.py` `_handle_dismiss_notification_callback` → `await`
- 测试：`test_f101_notification.py` 中 `svc.dismiss(...)` 6 处改 `await`；新增 F116 跨实例 rehydrate 单测 + store 单测
- 文档：`docs/codebase-architecture/harness-and-context.md`（通知服务一节，若涉及）；Blueprint 通知模型（living-docs 漂移闸核对）

## 修复策略

### 方案 A（推荐）：核心 StoreGroup 新增 SqliteNotificationStore + async dismiss + 启动 rehydrate

- **新 store**：`packages/core/src/octoagent/core/store/notification_store.py` → `SqliteNotificationStore(conn)`，复用主连接（与 `SqliteSideEffectLedgerStore` / `memory_extraction_ledger` 完全同范式）。
- **Schema**（2 张表，无 task FK——通知是 UI 状态，`notification_id` 为 sha256，不绑 task 生命周期，对齐 `memory_extraction_ledger` 无 FK 设计）：
  - `notification_dismissals(notification_id PK, source, dismissed_at)`
  - `notification_active(notification_id PK, session_id, task_id, notification_type, priority, payload JSON, created_at)` + `idx_notification_active_session`
  - `notification_id` 作 active 表 PK → 天然去重（`INSERT OR REPLACE`），比内存 list 多一层一致性（轻微行为增强，记入报告）。
- **Service**：
  - `__init__(..., notification_store=None)` + `bind_notification_store()`（对齐已有 `bind_event_store`/`bind_snapshot_store` 延迟绑定范式）。
  - `dismiss` 改 **async**：先内存 `add`（保证 `is_dismissed` 同步读立即可见），再 `await store.record_dismissal(...)`；返回 `bool`（True=已 durable 落盘；store 为 None 或落盘异常→False，内存仍生效但不谎报 durable，Codex H1）。store 为 None / 异常静默降级（Constitution #6）。
  - `_record_active` 改 **async**：内存 append + `await store.record_active(...)`（仅 `session_id` 非空时落盘，与现有 early-return 一致）。
  - 新增 `async rehydrate()`：从 store 载入 `_dismissed_set` 与 `_active_notifications`。
  - `is_dismissed` / `list_active` **保持同步**（读内存，零 API 破坏，热路径不引入 await）。
- **接线**：`octo_harness._bootstrap_executors` 构造 service 后 `bind_notification_store(store_group.notification_store)` + `await rehydrate()`。
- **durability 语义**（Codex H1 修正后）：dismiss best-effort 落盘并通过返回值 `persisted` 诚实暴露结果——成功→True；store 缺失 / 落盘失败→False（内存仍生效，不 crash 用户操作，亦不谎报 durable，Web route 透传 `persisted` 字段供前端提示降级）。不用 fire-and-forget create_task（避免 ack-before-persist 缝），但也不把 DB 故障升级为 500（Constitution #6 优先级高于"硬保证 durable"）。

### 方案 B（备选，未采纳）：保持 dismiss 同步 + fire-and-forget `asyncio.create_task` 落盘

- 优点：零 API/测试破坏。
- 缺点：ack-before-persist，进程在毫秒窗口崩溃即丢 dismiss；sync 上下文无 running loop 时无法调度；与 Constitution #1「落盘」精神相悖。**拒绝**。

### 方案选择

采用 **方案 A**。理由：真 durability（ack-after-persist）> 测试零改动；生产 dismiss 调用方仅 2 处且均 async，改造成本低；与现有 store / bind / 降级范式一致，无新抽象层。

## Spec 影响

- 无独立 spec.md（fix 模式）。
- 需核对的 living-docs：通知模型相关 Blueprint / codebase-architecture 文档（completion gate 漂移闸处理）。

## Codex Review 触发判定

命中 CLAUDE.local.md「重大架构变更 commit 前」节点——**数据库 schema 新增**（2 张表）。Codex adversarial review **必走**，finding 闭环到 0 HIGH。

## 范围过大检测

受影响文件 6 个生产文件 + 测试，跨 2 个模块（core/store + gateway/services）。未触发 >10 文件 / >3 模块阈值，适合 fix 模式。
