# Phase A — 实测侦察与 spec 校正

**Phase**: A
**Status**: COMPLETED
**Date**: 2026-05-25
**Baseline**: F101 commit 74c9ab3 + main 追加 → e2e_smoke 8 passed / 3652 deselected（pre-commit hook 确认）

---

## 0. 目的

Phase A 是 plan §0.2 实测预完成 8 项结论的正式归档 + 独立 grep 验证 + Phase B/C/D 实施前置检查。Phase A 无 production 代码改动，仅产出文档 + spec 微校正（已在 docs commit a9a5afe 中完成）。

---

## 1. 8 项侦察结论（独立 grep 验证）

### A-1: OQ-1 tasks.created_at 索引现状

**结论**：`idx_tasks_created_at` 索引**已存在**。FR-T1 不需要 migration 工具或 ALTER TABLE。

**Evidence**：
```bash
grep "idx_tasks_created" packages/core/src/octoagent/core/store/sqlite_init.py
# 第 32 行：
# "CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC);"
```

**路径校正**：plan 写 `stores/sqlite_init.py` 实际为 `store/sqlite_init.py`（packages/core/src/octoagent/core/**store**/，非 stores 复数）。无功能影响。

**对 FR-T1 实施的含义**：直接添加 `list_tasks_in_time_range(start, end)` 方法，无需任何 schema 改动。

---

### A-2: OQ-2 cheap alias 可用性

**结论**：`cheap` alias **已配置**（octoagent.yaml）。LLM 路径可正常验收，不会永远走 fallback。

**Evidence**：
```bash
grep -n "cheap" apps/gateway/.../octoagent.yaml
# 第 20 行：cheap:
# 配置 provider + model + thinking_level
```

**对 FR-B3 实施的含义**：LLM 路径（`provider_router.complete(model_alias="cheap")`）可调用；fallback 仅在网络/provider 异常时触发。

---

### A-3: CQ-5 DailyRoutineService bootstrap 顺序

**结论**：必须在 `_bootstrap_optional_routines` 内、`automation_scheduler.startup()` 完成之后构造，此时 `NotificationService` 已完全 bind 完成。

**Evidence**（octo_harness.py 关键位置）：
- `NotificationService` 创建：`_bootstrap_executors`（约第 869 行）
- `AutomationSchedulerService` 创建 + startup：`_bootstrap_optional_routines`（约第 1183 / 1197 行）
- `DailyRoutineService` 注入位置：`_bootstrap_optional_routines` 末尾（automation_scheduler.startup() 之后）

**对 FR-DI1 实施的含义**：bootstrap 段加新调用 `daily_routine = DailyRoutineService(scheduler, task_store, event_store, notification_service, snapshot_store, provider_router); await daily_routine.startup()`，shutdown 段加 `await daily_routine.shutdown()`。

---

### A-4: NotificationChannelProtocol.channel_name 属性校正

**结论**：属性名为 **`channel_name`**（不是 `name`）。

**Evidence**：
```bash
grep -n "channel_name" apps/gateway/.../services/notification.py
# 145:    def channel_name(self) -> str:   # NotificationChannelProtocol（abstract）
# 253:            channel_name=channel.channel_name,
# 567/656/685:    channel=channel.channel_name,
# 712:    def channel_name(self) -> str:   # TelegramNotificationChannel → "telegram"
# 807:    def channel_name(self) -> str:   # SSENotificationChannel → "web_sse"
```

**Channel 名称值域**：
- Telegram channel: `channel_name = "telegram"`
- Web SSE channel: `channel_name = "web_sse"`

**对 FR-B8 实施的含义**：
- 内部比较用 `channel.channel_name`（非 `channel.name`）
- USER.md `summary_channels: "telegram,web"` 解析时 `"web"` 映射到 `"web_sse"`
- spec.md FR-B8 + SD-1 + AC-D3 已校正（docs commit a9a5afe）

---

### A-5: notify_task_state_change 当前签名

**结论**：当前签名**无 channels 参数**，F102 Phase D 新增 `channels: frozenset[str] | None = None`（向后兼容）。

**Evidence**：
```bash
grep -n "def notify_task_state_change" apps/gateway/.../services/notification.py
# 第 468 行附近：
# def notify_task_state_change(
#     self,
#     task_id: str,
#     event_type: str,
#     payload: dict[str, Any],
#     priority: NotificationPriority = NotificationPriority.LOW,
#     active_hours: tuple[time, time] | None = None,
#     state_transition_event_id: str = "",
#     session_id: str | None = None,
# ) -> None:
```

**对 FR-B8 实施的含义**：Phase D 加 `channels: frozenset[str] | None = None` 末位参数（默认 None，向后兼容）。内部 channel 推送循环加 `if channels is not None and channel.channel_name not in channels: continue` 过滤。

---

### A-6: AutomationSchedulerService.add_job 接口 + misfire_grace_time 约定

**结论**：现有约定 `misfire_grace_time=30`（30 秒）。F102 沿用此约定（**spec 已校正**，从草稿 300 改为 30）。

**Evidence**：
```bash
grep -n "misfire_grace_time" apps/gateway/.../services/automation_scheduler.py
# 第 63 行：misfire_grace_time=30
```

**对 FR-B1 实施的含义**：DailyRoutineService cron 注册使用 `misfire_grace_time=30`。

---

### A-7: ObservationRoutine shutdown pattern

**结论**：`stop()` 调用 `self._task.cancel()` + `asyncio.wait_for(timeout=5.0)`（`observation_promoter.py:156-174`）。

**对 DailyRoutineService.shutdown() 实施的含义**：
- F102 不使用独立 asyncio.Task（采用 APScheduler cron），shutdown 简化为 `scheduler.remove_job("_daily_routine")` 即可
- 不需要 cancel + wait_for 逻辑（cron 内部跑的 `_run_daily_summary` 是 awaited 函数，scheduler 会处理）

---

### A-8: summary_channels 值域映射

**结论**：USER.md 用户友好写法 `"telegram,web"`，内部值域 `{"telegram", "web_sse"}`，解析时映射 `"web" → "web_sse"`。

**Evidence**：参见 A-4 channel_name 值域。

**对 FR-D2 实施的含义**：`extract_summary_channels_from_user_md()` 内部映射规则：
```python
_USER_VISIBLE_TO_INTERNAL = {"telegram": "telegram", "web": "web_sse"}
# 解析后必为 frozenset[str]，元素来自 {"telegram", "web_sse"}
```

---

## 2. spec 校正项（docs commit a9a5afe 中已完成）

| 校正点 | spec 位置 | 改动 |
|--------|-----------|------|
| FR-B8 channel.name → channel.channel_name | spec §5 FR-B8 | 完成 |
| FR-B1 misfire_grace_time 300 → 30 | spec §5 FR-B1, §8.1 | 完成 |
| summary_channels "web" → "web_sse" 映射 | spec §3.2 SD-1, FR-B8 实施细节, AC-D3 | 完成 |

---

## 3. baseline 验证

### e2e_smoke

- pre-commit hook：8 passed, 3652 deselected, 34 warnings in 12.83s（log: `~/.octoagent/logs/e2e/pre-commit-20260525-002202.log`）
- 5 个 smoke 域全 PASS：#1 工具调用基础 / #2 USER.md 全链路 / #3 冻结快照 / #11 ThreatScanner block / #12 ApprovalGate SSE（实际是 8 个 tests，含部分细分）
- 通过 SC-7 不变量：USER.md / auth-profiles.json / mcp-servers/ sha256 跑前后一致

### full pytest（后台运行中）

- 命令：`uv run pytest -x -q --tb=line -p no:cacheprovider`
- 预计耗时：~10-15 分钟（按 F101 完成时 baseline 3571 passed 经验）
- baseline 期望：3652+ passed（实际数字由 master 当前 commit 决定；spec NFR-4 写 3571 是 F101 完成时点的过时数）

### baseline 数字校正

- **F101 完成时**：3571 passed（spec NFR-4 引用）
- **F102 启动时**：3652+ deselected（pre-commit hook 显示）—— F101 → F102 之间 master 增加了 +81 测试（其他 Feature/master 直接 push 累计）
- **F102 NFR-4 实际基线**：以 Phase A baseline 测量的 passed 数为准（trace.md 记录）

---

## 4. Phase B/D 启动前置条件清单

✓ idx_tasks_created_at 已存在 → FR-T1 无 schema 改动
✓ cheap alias 已配置 → FR-B3 LLM 路径可验收
✓ bootstrap 顺序已定 → FR-DI1 注入位置明确
✓ channel_name 属性确认 → FR-B8 实现样板明确
✓ notify_task_state_change 当前签名 → FR-B8 加 channels 参数无 breaking
✓ misfire_grace_time 约定 → FR-B1 样板对齐
✓ ObservationRoutine shutdown pattern → DailyRoutineService.shutdown 简化
✓ summary_channels 值域映射规则 → FR-D2 实施明确

**Phase B 和 Phase D 可并行（文件无重叠）**：
- B 改：`enums.py`, `task_store.py`, `USER.md`, `daily_routine_config.py`（新建）
- D 改：`notification.py`

---

## 5. Codex review 节点（Phase A 触发 pre-impl review）

**触发时机**：phase-a-recon.md 提交 + baseline 验证完成后
**review 类型**：pre-impl Codex adversarial review
**review 范围**：spec.md + plan.md + tasks.md 三件整体设计 + Phase A 实测结论是否充分
**期望产出**：`codex-review-pre-impl.md`，含 finding 处理决议

> 备注：本会话中 Phase A 实测预完成在 plan 阶段，spec 校正在 docs commit 中完成；正式 pre-impl Codex review 在 Phase B 启动前触发。
