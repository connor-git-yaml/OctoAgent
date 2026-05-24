# F102 Proactive Followup — 技术实现计划

**Spec**: [spec.md](spec.md)（GATE_DESIGN 通过，SD-1~SD-10 全部拍板）
**Tech Research**: [research/tech-research.md](research/tech-research.md)
**Baseline**: `74c9ab3`（F101 完成，3571 passed）
**Plan date**: 2026-05-24

---

## 0. 总览

### 0.1 背景

F102 是 M5 阶段 3 第二个 Feature，在 F101（NotificationService）完成后启动，新建 `DailyRoutineService`——每日 08:30 触发，汇总昨日 Worker 状态，走 LLM（cheap alias）或 deterministic fallback 生成摘要，通过 NotificationService 推送 Telegram + Web 通知。

本 Feature 核心哲学：**后台主动产出 + H1 管家 mediated**——Routine 不直接发消息，全部经 NotificationService 送达，符合 OctoAgent 单 user-facing speaker 原则。

### 0.2 Phase 0 实测侦察结论（本地 Codebase 实测，已解决所有 OPEN QUESTION）

| 项目 | 结论 |
|------|------|
| **OQ-1 tasks.created_at 索引** | `idx_tasks_created_at` 已存在（`sqlite_init.py:32`：`CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC)`）。FR-T1 无需新建索引，0 迁移工作 |
| **OQ-2 cheap alias 可用性** | `octoagent.yaml:20-24` 已配置 `cheap` alias（provider=openai-codex, model=gpt-5.4, thinking_level=low）。LLM 路径可正常验收，不会永远 fallback |
| **CQ-5 bootstrap 构造顺序** | `NotificationService` 在 `_bootstrap_executors`（第 869 行）创建；`AutomationSchedulerService` 在 `_bootstrap_optional_routines`（第 1183 行）创建并 startup（第 1197 行）。`DailyRoutineService` 必须在 `_bootstrap_optional_routines` 内、`automation_scheduler.startup()` 完成**之后**构造并调用 `daily_routine_service.startup()`，此时 `NotificationService` 已完全就绪 |
| **channel_name 属性校正** | `NotificationChannelProtocol` 暴露的属性名是 `channel_name`（不是 `name`）；`TelegramNotificationChannel.channel_name = "telegram"`，`SSENotificationChannel.channel_name = "web_sse"`。spec FR-B8 中的 `channel.name` 描述需在实现时改用 `channel.channel_name`；spec SD-6 `summary_channels: "telegram,web"` 的 `"web"` 需映射到 `"web_sse"` |
| **F101 notify_task_state_change 签名** | 当前签名：`(task_id, event_type, payload, priority=LOW, active_hours=None, state_transition_event_id="", session_id=None)` — 无 channels 参数，F102 Phase D 新增 `channels: frozenset[str] | None = None`（向后兼容） |
| **ObservationRoutine shutdown pattern** | `stop()` 调用 `self._task.cancel()` + `asyncio.wait_for(timeout=5.0)`（`observation_promoter.py:156-174`）。`DailyRoutineService.shutdown()` 走相同 pattern：APScheduler `remove_job` 即可，无独立 asyncio.Task 需要 cancel |
| **AutomationSchedulerService.add_job 接口** | 接受 `trigger / id / replace_existing / misfire_grace_time` 参数（`automation_scheduler.py:63`）。F102 用 `CronTrigger.from_crontab(expr, timezone=user_tz)` 注册，`misfire_grace_time=30`（与现有约定对齐，而非 spec 草稿中的 300s——CHK-2.4 WARNING 校正） |

### 0.3 Codebase Reality Check

| 目标文件 | 当前 LOC | 公开方法数 | 已知 debt |
|---------|---------|-----------|---------|
| `notification.py`（修改：+channels 参数）| 900+ 行 | 8 个公开方法 | 无直接相关 TODO/FIXME |
| `sqlite_init.py`（无需修改，OQ-1 已确认索引存在）| 1500+ 行 | DDL 函数 | — |
| `task_store.py`（新增 list_tasks_in_time_range）| 200+ 行 | 5 个查询方法 | 无 |
| `enums.py`（+4 EventType）| 200+ 行 | 枚举类 | 无 |
| `octo_harness.py`（bootstrap 新增 DI 构造）| 1200+ 行 | 11 个 bootstrap 段 | 已有 D8 推迟 debt，不在本次范围 |
| `USER.md`（模板新增 3 字段）| 70 行 | 模板文件 | 无 |
| `daily_routine.py`（新建）| 0 → 预估 250-320 行 | 8 个方法 | 新文件 |
| `daily_routine_config.py`（新建）| 0 → 预估 80-120 行 | 3 个解析函数 | 新文件 |

**前置清理规则评估**：无目标文件满足 LOC>500 且新增>50 行同时超 3 个 TODO/FIXME 的条件。无需 CLEANUP 前置 task。

### 0.4 Impact Assessment

| 维度 | 评估 |
|------|------|
| **影响文件数** | 直接修改 6 + 新建 2 = 8 文件；间接受影响（调用方）：octo_harness.py shutdown 段（shutdown DailyRoutineService）|
| **跨包影响** | `apps/gateway/`（新建 daily_routine.py + daily_routine_config.py + 修改 notification.py + octo_harness.py）；`packages/core/`（task_store.py + enums.py）；`behavior_templates/`（USER.md） |
| **数据迁移** | 无。USER.md 字段新增（agent 协助更新，非强制迁移）；sqlite_init.py 无改动（索引已存在） |
| **API/契约变更** | `notify_task_state_change` 新增可选参数（向后兼容）；`task_store.list_tasks_in_time_range` 全新方法（无 breaking change） |
| **风险等级** | **LOW** — 影响文件 < 10，跨包影响 ≤ 2 个顶层边界（gateway + core），无数据迁移，无 breaking API 变更 |

LOW 风险：不强制分阶段，但 spec 已评估 MEDIUM 复杂度，保持 5 Phase 节奏（含 Phase A 侦察）。

### 0.5 Constitution Check

| 原则 | 适用性 | 评估 | 说明 |
|------|--------|------|------|
| C1 Durability First | 适用 | 满足 | ROUTINE_TRIGGERED/COMPLETED 事件写入 SQLite，进程重启后 audit 链不丢 |
| C2 Everything is an Event | 适用 | 满足 | 4 个新 EventType；LLM 调用经 provider_router 自动产生 MODEL_CALL_* 事件 |
| C3 Tools are Contracts | 不适用 | — | F102 不新增 Tool |
| C4 Side-effect Must be Two-Phase | 适用 | 满足 | daily summary 是只读（查询 + 通知），无不可逆 side effect |
| C6 Degrade Gracefully | **重点** | 满足 | LLM 失败 → deterministic fallback；cron 注册失败 → catch + ERROR log + ROUTINE_FAILED 事件，不阻塞 gateway 启动 |
| C7 User-in-Control | 适用 | 满足 | `routine_active: "false"` 可关闭；daily_summary_time 可配置 |
| C8 Observability | 适用 | 满足 | ROUTINE_COMPLETED 含 elapsed_ms/fallback/worker_count；ROUTINE_FAILED 含 error_type/error_msg |
| C9 Agent Autonomy | 不适用 | — | F102 是系统 Routine，不涉及 LLM routing 决策 |
| C10 Policy-Driven Access | 不适用 | — | F102 不新增工具权限控制 |

无 VIOLATION。

### 0.6 Phase 数与复杂度

**5 Phase（A→B→D→C→E→F，其中 B 和 D 可部分并行）**，MEDIUM 复杂度，Codex review 必走（per-Phase + Final cross-Phase）。

---

## 1. Phase A — 实测侦察与 spec 校正（无 production 代码改动）

### 目标

产出 `phase-a-recon.md`，记录上述 0.2 节所有侦察结论，并对 spec 做最小校正（不改 AC/FR 编号）。

### 任务清单

| 任务 | 操作 | 产出 |
|------|------|------|
| A-1 OQ-1 确认 | 读 sqlite_init.py:12-35，确认 `idx_tasks_created_at` | `IDX_TASKS_CREATED_AT = EXISTS` |
| A-2 OQ-2 确认 | 读 octoagent.yaml:14-25，确认 cheap alias | `CHEAP_ALIAS = CONFIGURED` |
| A-3 CQ-5 bootstrap 顺序 | 读 octo_harness.py bootstrap 段顺序 | `DAILY_ROUTINE_BOOTSTRAP_STEP = _bootstrap_optional_routines` |
| A-4 channel_name 校正 | 读 notification.py:137-147, 711-713, 807-808 | `CHANNEL_NAME_ATTR = "channel_name"`, `telegram="telegram"`, `web_sse="web_sse"` |
| A-5 notify_task_state_change 签名 | 读 notification.py:468-478 | 确认无 channels 参数，F102 需新增 |
| A-6 misfire_grace_time 校正 | 读 automation_scheduler.py:63 | 确认为 30s，校正 spec FR-B1 样板 |
| A-7 ObservationRoutine shutdown | 读 observation_promoter.py:156-174 | 确认 APScheduler job remove 即可 |
| A-8 summary_channels 值域映射 | 确认 "web" → "web_sse" 映射规则 | 写入 `extract_summary_channels_from_user_md()` 设计要求 |

### spec 最小校正

- FR-B8 中 `channel.name` 改为 `channel.channel_name`
- FR-B1 `misfire_grace_time=300` 改为 `misfire_grace_time=30`（与现有约定对齐）
- `extract_summary_channels_from_user_md()` 新增"web → web_sse"映射说明（USER.md 写 `"telegram,web"` 但内部比较用 `"web_sse"`）

### 完成条件

- `phase-a-recon.md` 文件存在，8 项侦察结论全部记录
- spec.md 3 处校正已提交（无 AC/FR 编号变化）
- 单测不变，baseline 3571 passed 维持

### Codex Review 节点

Phase A 完成后触发 **pre-impl Codex review**：检查 spec + plan 整体设计一致性，聚焦于 channel_name 校正是否影响 AC-D3 测试可执行性、bootstrap 顺序是否正确。

---

## 2. Phase B — 基础设施（非 notification.py 修改）

**依赖**：Phase A 侦察结论（A-4 channel_name / A-6 misfire_grace_time）

### 任务清单

| 任务 | 文件 | FR/AC |
|------|------|-------|
| B-1 新增 4 个 EventType | `packages/core/.../enums.py` | FR-E1 |
| B-2 新建 `daily_routine_config.py` | `apps/gateway/.../services/daily_routine_config.py` | FR-D2 |
| B-3 `task_store.list_tasks_in_time_range` | `packages/core/.../store/task_store.py` | FR-T1 |
| B-4 `behavior_templates/USER.md` 新增 3 字段 | `behavior_templates/USER.md` | FR-D1 |
| B-5 `RoutineCompletedPayload` schema | `daily_routine_config.py` 或新建 payloads 模块 | FR-E2 / FR-E3 |
| B-6 ensure_system_audit_task 调用 | `daily_routine.py` 的 `startup()` 草稿（仅占位结构）| FR-B5 |

**B-2 daily_routine_config.py 设计**（CHK-2.1 行数约束）：

```python
# apps/gateway/src/octoagent/gateway/services/daily_routine_config.py
from dataclasses import dataclass

@dataclass(frozen=True)
class DailyRoutineConfig:
    daily_summary_time: str            # "HH:MM"
    routine_active: bool
    summary_channels: frozenset[str]   # {"telegram", "web_sse"}（内部表示，已映射）
    user_timezone: str                 # 默认 "UTC"

def extract_daily_summary_time_from_user_md(content: str) -> str:
    """regex 匹配 `daily_summary_time: "HH:MM"`，非法值返回 "08:30" + WARNING log"""

def extract_routine_active_from_user_md(content: str) -> bool:
    """regex 匹配 `routine_active: "true"/"false"`，非法值返回 True + WARNING log"""

def extract_summary_channels_from_user_md(content: str) -> frozenset[str]:
    """regex 匹配 `summary_channels: "telegram,web"`，"web" 映射为 "web_sse"；
    非法或空 → frozenset({"telegram", "web_sse"}) + WARNING log"""

def build_crontab_from_time(daily_summary_time: str) -> str:
    """"HH:MM" → "MM HH * * *" cron 格式"""
```

**B-3 list_tasks_in_time_range SQL**：

```python
async def list_tasks_in_time_range(
    self,
    start: datetime,   # UTC-aware
    end: datetime,     # UTC-aware，range [start, end) 半开区间
    statuses: list[TaskStatus] | None = None,
) -> list[Task]:
    # NaiveDatetime 触发 ValueError
    # SQL: SELECT * FROM tasks WHERE created_at >= :start AND created_at < :end [AND status IN ...]
    # 索引 idx_tasks_created_at (created_at DESC) 已存在（OQ-1 确认）
```

### 对应测试

- `tests/services/test_daily_routine_config.py`：AC-D1/D2/D3（解析侧）/D4
- `tests/stores/test_task_store_time_range.py`：AC-T1（SQL 边界条件 + NaiveDatetime ValueError）

### 完成条件

- B-1~B-6 全部提交，enums + config 模块 + task_store 方法 + USER.md 模板均就绪
- `uv run pytest -x -q tests/services/test_daily_routine_config.py tests/stores/test_task_store_time_range.py` 全绿
- 全量回归 >= 3571 passed，0 regression

### Codex Review 节点

Phase B 完成后触发 per-Phase review：聚焦 `extract_summary_channels_from_user_md` 的 "web"→"web_sse" 映射正确性；`list_tasks_in_time_range` NaiveDatetime ValueError 是否覆盖。

---

## 3. Phase D — F101 接口扩展（channels 参数）

**依赖**：Phase A（channel_name 校正结论）
**注意**：Phase D 与 Phase B 在逻辑上可以并行（两者改不同文件），但实践中建议 Phase B 先提交，避免 merge 冲突。Phase D 在 Phase B 之后紧跟启动。

### 任务清单

| 任务 | 文件 | FR/AC |
|------|------|-------|
| D-1 `notify_task_state_change` 新增 channels 参数 | `notification.py:468` | FR-B8 / SD-6 |
| D-2 `_write_notification_audit_event` 补充 channels 字段 | `notification.py`（NOTIFICATION_DISPATCHED payload）| AC-F1 / AC-D3 |
| D-3 补 `channel_name` 断言注释 | `notification.py` 内部 for loop | — |

**D-1 实现细节**：

```python
async def notify_task_state_change(
    self,
    *,
    task_id: str,
    event_type: str,
    payload: dict[str, Any],
    priority: NotificationPriority = NotificationPriority.LOW,
    active_hours: str | None = None,
    state_transition_event_id: str = "",
    session_id: str | None = None,
    channels: frozenset[str] | None = None,   # 新增，None = 全推（向后兼容）
) -> None:
    ...
    # 内部 for channel in self._channels 循环加过滤：
    # if channels is not None and channel.channel_name not in channels:
    #     continue
```

**D-2 NOTIFICATION_DISPATCHED payload 扩展**：在现有 payload 中新增 `channels: list[str] | None`，`None` 表示全渠道推送，非 None 表示过滤后的目标渠道集。

### 向后兼容验证

F101 所有已有调用方（`task_runner.py` / `approval_manager.py` / `ask_back_tools.py`）不传 `channels`，行为不变。F102 是唯一传 `channels` 的 caller。

### 对应测试

- `tests/services/test_notification_channels.py`：AC-D3 / FR-B8（channels=None 全推 / channels={"telegram"} 只推 Telegram / channels={"web_sse"} 只推 Web）

### 完成条件

- `notify_task_state_change` 新签名向后兼容，所有 F101 现有单测仍通过
- `test_notification_channels.py` AC-D3 全绿
- 全量回归 >= 3571 passed，0 regression

### Codex Review 节点

Phase D 完成后触发 per-Phase review：聚焦向后兼容性（现有调用方不传 channels 时行为不变）；`NOTIFICATION_DISPATCHED` payload 扩展是否影响现有 F101 测试。

---

## 4. Phase C — 核心 DailyRoutineService

**依赖**：Phase B（4 EventType + daily_routine_config.py + task_store 新 API）+ Phase D（channels 参数就绪）

### 目标

实现 `DailyRoutineService` 主类，完成完整执行路径（FR-B1/B2/B5/B6/B7 + FR-DI1 + cron 注册 + bootstrap 集成）。

### 新建文件

**`apps/gateway/src/octoagent/gateway/services/daily_routine.py`**（预估 250-320 行，< 350 行约束）：

```python
class DailyRoutineService:
    _DAILY_ROUTINE_AUDIT_TASK_ID = "_daily_routine_audit"

    def __init__(
        self,
        scheduler: AutomationSchedulerService,
        task_store: TaskStore,
        event_store: SqliteEventStore,
        notification_service: NotificationService,
        snapshot_store: SnapshotStore,
        provider_router: ProviderRouter,
    ) -> None: ...

    async def startup(self) -> None:
        """1. ensure_system_audit_task; 2. 读 USER.md 配置; 3. 注册 cron job"""

    async def shutdown(self) -> None:
        """remove cron job from scheduler"""

    async def _run_daily_summary(self) -> None:
        """主执行路径（FR-B2 全部 9 步）"""

    async def _collect_yesterday_data(
        self, tz: ZoneInfo
    ) -> tuple[list[Task], dict[str, list[Event]]]:
        """查 task_store + event_store，返回昨日 task 列表及各自事件"""

    async def _generate_summary_llm(self, data: DailyData) -> str:
        """cheap alias LLM 路径；超出 token budget 时截断（优先保留 failed + attention）"""

    def _generate_summary_fallback(self, data: DailyData) -> str:
        """deterministic 模板渲染，1s 内完成（NFR-2）"""

    def _read_config(self) -> DailyRoutineConfig:
        """从 snapshot_store.get_live_state("USER.md") 读取并解析"""

    def _compute_yesterday_range_utc(
        self, now_local: datetime, tz: ZoneInfo
    ) -> tuple[datetime, datetime]:
        """按用户本地时区定义"昨日"[yesterday_00:00, today_00:00)，转 UTC datetime"""
```

### 执行顺序（FR-B2 完整 9 步）

1. 写 `ROUTINE_TRIGGERED` 事件
2. 读 USER.md 配置（routine_active / summary_channels）
3. `routine_active=False` → 写 `ROUTINE_SKIPPED(reason="routine_disabled")` → return
4. 计算 yesterday_start / yesterday_end（用户时区 → UTC），调用 `task_store.list_tasks_in_time_range`
5. `len(tasks) == 0` → 写 `ROUTINE_COMPLETED(worker_count=0, ...)` → return（SD-8：不推送）
6. 对每个 task 查 events（STATE_TRANSITION / WORKER_DISPATCHED / WORKER_RETURNED / APPROVAL_REQUESTED / APPROVAL_EXPIRED）
7. 汇总 worker_count / failed_count / attention_count（SD-7：task.status ∈ attention_statuses）
8. LLM 摘要（cheap alias，max_tokens=512，输入 ≤ 3000 tokens）；失败 → fallback（FR-B3）
9. `notification_service.notify_task_state_change(..., channels=summary_channels)`（FR-B7）
10. 写 `ROUTINE_COMPLETED` 事件（含全量 metrics）

### CancelledError 处理（FR-B6）

```python
try:
    await self._run_daily_summary()
except asyncio.CancelledError:
    raise  # 显式 re-raise，不吞掉
except Exception as exc:
    # 写 ROUTINE_FAILED + ERROR log，loop 继续
    await self._write_routine_failed(exc)
```

### bootstrap 集成（octo_harness._bootstrap_optional_routines）

在 `automation_scheduler.startup()` 之后（第 1197 行后）构造并调用：

```python
from ..services.daily_routine import DailyRoutineService

_daily_routine_service = DailyRoutineService(
    scheduler=app.state.automation_scheduler,
    task_store=store_group.task_store,
    event_store=store_group.event_store,
    notification_service=app.state.notification_service,
    snapshot_store=snapshot_store,
    provider_router=provider_router,
)
try:
    await _daily_routine_service.startup()
    app.state.daily_routine_service = _daily_routine_service
except Exception as _exc:
    _log.warning("daily_routine_service_init_skipped", error=str(_exc))
    app.state.daily_routine_service = None
```

shutdown 段（`shutdown()` 方法内）：

```python
if hasattr(app.state, "daily_routine_service") and app.state.daily_routine_service:
    await app.state.daily_routine_service.shutdown()
```

### LLM prompt 模板（SD-9 token budget 实现）

```
你是一个任务助手，请用简洁中文（不超过 200 字）总结以下昨日 Worker 任务情况：

日期：{date}
完成：{completed_count} 个
失败：{failed_count} 个
待关注：{attention_count} 个

任务详情：
{task_details}   # 优先包含 failed + attention task 的 events；其余 task 仅保留 title + final_status
                  # 总输入 ≤ 3000 tokens，超限时截断"其余任务"部分
```

输入截断策略：先加入 failed + attention task（全量 events），再按 created_at DESC 补其余 task（仅 title + status），直到 token 预估 < 3000 为止（按 `len(text) / 4` 粗估）。

### 对应测试

- `tests/services/test_daily_routine_summary.py`：AC-B3 / AC-B5 / **AC-E3**（CancelledError re-raise）/ **AC-E4**（attention_count=3 with 5-task fixture）
- `tests/services/test_daily_routine_priority.py`：AC-B4（quiet hours，真实 NotificationService + mock SnapshotStore）/ AC-B7（priority MEDIUM when attention_count > 0）
- `tests/services/test_daily_routine_startup.py`：AC-B6（cron job_id + replace_existing + cron 注册失败兜底）
- `tests/services/test_daily_routine_integration.py`：AC-B1 / **AC-B2**（routine_active=false）/ AC-E1 / AC-E2 / AC-F1

**AC-B4 测试方法**（CHK-1.2 BLOCKER 决议）：

```python
# 真实 NotificationService + mock SnapshotStore 返回含 quiet hours 的 USER.md
# active_hours: "09:00-23:00"，daily_summary_time 设为 02:00
# 断言：event_store 中含 NOTIFICATION_DISPATCHED(filtered=True)
#       channel.notify 未被调用（mock assert not called）
```

### 完成条件

- DailyRoutineService 全量实现，cron 注册到 AutomationSchedulerService
- 所有集成测试文件全绿（AC-B1~B7 / AC-E1~E4 / AC-F1 / AC-T1 / AC-D1~D4 全覆盖）
- 全量回归 >= 3571 passed，0 regression

### Codex Review 节点

Phase C 完成后触发 per-Phase review：聚焦 CancelledError re-raise 正确性；bootstrap 顺序是否有 race（notification_service 是否已 bind）；LLM token budget 截断逻辑是否正确；AC-B4 quiet hours 测试是否真实验证 filtered=True。

---

## 5. Phase E — LLM 摘要路径单独验证

**依赖**：Phase C（DailyRoutineService 主体完成）

### 目标

验证 LLM 路径（cheap alias 真实调用）、fallback 路径（任何异常自动切换）、`ROUTINE_COMPLETED.fallback` 字段正确写入。本 Phase 主要是补充 Phase C 可能遗漏的 edge case 和 LLM 特定测试。

### 任务清单

| 任务 | 说明 |
|------|------|
| E-1 LLM 路径单测补全 | mock provider_router 返回合法摘要，断言 ROUTINE_COMPLETED.fallback=False |
| E-2 fallback 路径单测 | mock provider_router raise Exception，断言 ROUTINE_COMPLETED.fallback=True |
| E-3 fallback 模板格式验证 | deterministic 模板 3 种情况（全完成/有失败/有 attention）输出符合 FR-B3 格式 |
| E-4 token budget 截断验证 | 构造 > 3000 token 的 task 列表，确认 failed + attention task 在截断后仍保留 |
| E-5 priority 决策验证 | attention_count=0 → LOW；attention_count=1 → MEDIUM（AC-B7） |
| E-6 e2e_smoke 回归 | `pytest -m e2e_smoke` 全过，确保 F101 已有 5 个 smoke 域 0 regression |

### 完成条件

- AC-B3 / AC-E2 测试路径均有对应断言
- `pytest -m e2e_smoke` 全过
- 全量回归 >= 3571 + 新增测试数

### Codex Review 节点

Phase E 完成后触发 per-Phase review：聚焦 fallback 触发条件完整性（空字符串/仅空白/None 响应是否覆盖）；token budget 粗估是否有明显偏差风险。

---

## 6. Phase F — Final 验证与 Codex cross-Phase review

**依赖**：Phase E 完成（所有 production 代码 + 测试就绪）

### 任务清单

| 任务 | 说明 |
|------|------|
| F-1 17 AC 全覆盖矩阵检查 | 逐 AC 对照测试文件，确认每个 AC 至少有一个 PASS 断言 |
| F-2 全量回归 | `uv run pytest -x -q --tb=short -p no:cacheprovider`，>= 3571 + 新增 |
| F-3 e2e_smoke 回归 | `pytest -m e2e_smoke` 全过 |
| F-4 completion-report.md | 生成 `.specify/features/102-proactive-followup/completion-report.md` |
| F-5 handoff.md | 生成 handoff 给 F103/F107 |
| F-6 **Final cross-Phase Codex review** | 输入：全部 Phase A~E diff + spec.md + plan.md；聚焦整体 audit chain / bootstrap 顺序 / F101 边界 |

### 完成条件

- 17 AC 全部 PASS（无跳过、无标注"TODO"）
- 全量回归 >= 3571，0 regression
- Codex Final review 0 HIGH 残留
- completion-report.md + handoff.md 产出
- 不主动 push origin/master，等用户拍板

---

## 7. Phase 依赖 DAG

```
Phase A（侦察，1天）
    │
    ├──→ Phase B（基础设施：enums + config + task_store + USER.md）
    │         │
    │         │（Phase B 和 Phase D 可在同一轮开发中按序完成，
    │         │  Phase D 不依赖 Phase B 代码，依赖 Phase A 校正结论）
    ├──→ Phase D（F101 接口扩展：channels 参数）
    │         │
    └─────────┴──→ Phase C（核心 DailyRoutineService，依赖 B + D 全部完成）
                       │
                       ↓
                   Phase E（LLM 路径验证 + e2e_smoke）
                       │
                       ↓
                   Phase F（Final 验证 + Codex cross-Phase review）
```

**Phase B/D 并行决策**：Phase B 和 Phase D 修改不同文件（B 改 enums/task_store/config；D 改 notification.py），无代码冲突，可并行开发，但建议顺序提交（B 先 D 后）减少 rebase 风险。

---

## 8. 风险与 Mitigation 表

| 风险 | 严重度 | 实测结论 | Mitigation |
|------|--------|---------|------------|
| **cheap alias 不可用** | MED | OQ-2 已确认 cheap 已配置（gpt-5.4 + low thinking）。运行时 LLM provider 故障时 fallback | FR-B3 deterministic fallback 路径，ROUTINE_COMPLETED.fallback=true 可审计 |
| **channel_name vs name 属性名** | MED | 实测确认属性名为 `channel_name`；spec FR-B8 描述有误 | Phase A 已校正，实现时使用 `channel.channel_name`；"web" → "web_sse" 映射在 extract_summary_channels_from_user_md 内完成 |
| **bootstrap 顺序 race** | MED | CQ-5 已确认 NotificationService 在 _bootstrap_executors 完成（早于 _bootstrap_optional_routines）| DailyRoutineService 在 automation_scheduler.startup() 之后构造，notification_service 已完全就绪 |
| **N+1 查询性能** | MED | 昨日 task 量 ≤ 50 时 P50 < 5s（NFR-1）；task 量 > 50 时累计 event_store 查询可能 > 5s | `list_tasks_in_time_range` 已有 idx_tasks_created_at 索引；超 50 task 时 ROUTINE_COMPLETED.elapsed_ms 可观测；batch_get_events 推 F107 |
| **quiet hours 多日丢失** | LOW | D5 决策：不补发不延迟 | 用户可调整 daily_summary_time 到 active_hours 内；handoff 注明 |
| **cron timezone 边界** | LOW | NFR-3：CronTrigger.from_crontab(timezone=user_tz) | timezone 解析非法时 WARNING log + fallback UTC |
| **tasks.created_at 索引** | 已消除 | OQ-1 确认 idx_tasks_created_at 已存在 | 无需新建索引 |
| **cron 注册失败** | LOW | CHK-4.2 WARNING | startup() catch + ERROR log + ROUTINE_FAILED event，不阻塞 gateway |
| **attention_count 语义** | LOW | SD-7 已明确：task.status ∈ 5 个状态集，不查 STATE_TRANSITION 事件 | 5-task fixture（AC-E4）验证 attention_count=3 |

---

## 9. 测试策略对照表（17 AC → Phase → 测试文件）

| AC | 描述（简）| Phase | 测试文件 |
|----|----------|-------|---------|
| AC-B1 | 完整流程 + P50 < 5s | C | `test_daily_routine_integration.py` |
| AC-B2 | routine_active=false 跳过 | C | `test_daily_routine_integration.py`（显式覆盖）|
| AC-B3 | LLM 失败 → fallback + fallback=true | C/E | `test_daily_routine_summary.py` |
| AC-B4 | quiet hours 过滤 + filtered=True | C | `test_daily_routine_priority.py`（真实 NotificationService + mock SnapshotStore）|
| AC-B5 | 空数据不推送 + ROUTINE_COMPLETED | C | `test_daily_routine_integration.py` |
| AC-B6 | cron 注册 job_id + replace_existing | C | `test_daily_routine_startup.py` |
| AC-B7 | attention_count>0 → MEDIUM priority | C/E | `test_daily_routine_priority.py` |
| AC-D1 | daily_summary_time 解析 | B | `test_daily_routine_config.py` |
| AC-D2 | routine_active 解析 | B | `test_daily_routine_config.py` |
| AC-D3 | summary_channels 过滤（channels 参数路由）| D | `test_notification_channels.py` |
| AC-D4 | 字段缺失时默认值 | B | `test_daily_routine_config.py` |
| AC-E1 | ROUTINE_TRIGGERED + ROUTINE_COMPLETED audit chain | C | `test_daily_routine_integration.py` |
| AC-E2 | ROUTINE_COMPLETED.fallback=true | C/E | `test_daily_routine_summary.py` |
| AC-E3 | ROUTINE_FAILED + CancelledError re-raise | C | `test_daily_routine_summary.py`（CHK-5.1 补全）|
| AC-E4 | attention_count=3（5-task fixture）| C | `test_daily_routine_summary.py` |
| AC-F1 | NOTIFICATION_DISPATCHED（filtered=True/False）| C | `test_daily_routine_integration.py` |
| AC-T1 | list_tasks_in_time_range SQL + 边界 | B | `test_task_store_time_range.py` |

**测试覆盖完整性**：17 AC 全部有 Phase 归属和对应测试文件，无遗漏。

---

## 10. 回归基线与验证命令

**基线**：F101 commit `74c9ab3`，3571 passed，0 regression

**每 Phase 后运行**：

```bash
cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F102-proactive-followup/octoagent
uv run pytest -x -q --tb=short -p no:cacheprovider
```

**e2e_smoke 回归**（Phase E + Phase F 必走）：

```bash
uv run pytest -m e2e_smoke -x -q --tb=short
```

**目标**：每 Phase 提交时 passed >= 3571，0 regression；Final F Phase 提交时 passed = 3571 + 新增测试数（预估 +25~40）。

---

## 11. 提交策略

- 每 Phase 单独 commit，commit message 格式：`feat(F102-Phase-X): <描述> + Codex review <N>H/<M>M 闭环`
- Phase A：`docs/recon(F102-Phase-A): 侦察结论 + spec 校正 (channel_name / misfire_grace_time)`
- Phase B：`feat(F102-Phase-B): enums +4 EventType + daily_routine_config + task_store.list_tasks_in_time_range + USER.md`
- Phase D：`feat(F102-Phase-D): NotificationService.notify_task_state_change channels 参数（向后兼容）`
- Phase C：`feat(F102-Phase-C): DailyRoutineService 主体 + cron 注册 + bootstrap 集成`
- Phase E：`feat(F102-Phase-E): LLM 摘要路径验证 + fallback + e2e_smoke 回归`
- Phase F：`docs(F102-Final): cross-Phase review 闭环 + completion-report + handoff`
- **不主动 push origin/master**，等用户拍板（CLAUDE.local.md §Spawned Task 处理流程）

---

## 12. Codex Review 触发节点

| 节点 | 时机 | 模式 | 聚焦范围 |
|------|------|------|---------|
| **pre-impl（Phase A 后）** | Phase A commit 后，Phase B 开始前 | foreground | spec + plan 设计一致性；channel_name 校正影响；bootstrap 顺序 |
| **Phase B 后** | Phase B commit 后 | foreground | extract_summary_channels "web"→"web_sse" 映射；list_tasks_in_time_range NaiveDatetime |
| **Phase D 后** | Phase D commit 后 | foreground | channels 参数向后兼容；NOTIFICATION_DISPATCHED payload 扩展 |
| **Phase C 后** | Phase C commit 后 | background | CancelledError re-raise；bootstrap race；AC-B4 quiet hours 真实验证 |
| **Phase E 后** | Phase E commit 后 | foreground | fallback 触发完整性；token budget 截断策略 |
| **Final cross-Phase（Phase F）** | 所有 Phase 完成后 | background | 整体 audit chain；F101 边界；17 AC 全覆盖确认；handoff 完整性 |

---

## 13. 完成 Checklist

- [ ] Phase A：phase-a-recon.md 产出，8 项侦察结论，spec.md 3 处校正
- [ ] Phase B：4 EventType + daily_routine_config.py（3 解析函数 + DailyRoutineConfig）+ task_store.list_tasks_in_time_range + USER.md 模板
- [ ] Phase D：notify_task_state_change channels 参数 + NOTIFICATION_DISPATCHED payload 扩展
- [ ] Phase C：DailyRoutineService 完整主体 + cron 注册 + bootstrap 集成 + shutdown 段
- [ ] Phase E：LLM/fallback 路径完整单测 + e2e_smoke 回归全过
- [ ] Phase F：17 AC 全部 PASS，全量回归 0 regression，Codex Final review 0 HIGH，completion-report + handoff
- [ ] 不主动 push，等用户拍板

---

## 附录 A：关键文件路径索引

| 文件 | 路径 | 操作 |
|------|------|------|
| `daily_routine.py` | `octoagent/apps/gateway/src/octoagent/gateway/services/daily_routine.py` | 新建 |
| `daily_routine_config.py` | `octoagent/apps/gateway/src/octoagent/gateway/services/daily_routine_config.py` | 新建 |
| `notification.py` | `octoagent/apps/gateway/src/octoagent/gateway/services/notification.py` | 修改（+channels 参数）|
| `task_store.py` | `octoagent/packages/core/src/octoagent/core/store/task_store.py` | 修改（+list_tasks_in_time_range）|
| `enums.py` | `octoagent/packages/core/src/octoagent/core/models/enums.py` | 修改（+4 EventType）|
| `octo_harness.py` | `octoagent/apps/gateway/src/octoagent/gateway/harness/octo_harness.py` | 修改（_bootstrap_optional_routines + shutdown）|
| `USER.md` | `octoagent/packages/core/src/octoagent/core/behavior_templates/USER.md` | 修改（+3 字段）|
| `sqlite_init.py` | `octoagent/packages/core/src/octoagent/core/store/sqlite_init.py` | 无改动（idx_tasks_created_at 已存在）|
