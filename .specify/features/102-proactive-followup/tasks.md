# F102 Proactive Followup — Tasks

**Spec**: spec.md（17 AC / 16 FR / 10 SD，GATE_DESIGN 通过）
**Plan**: plan.md（5 Phase，MEDIUM 复杂度）
**Baseline**: `74c9ab3`（F101，3571 passed）
**生成日期**: 2026-05-25

---

## 0. 概览

| 指标 | 值 |
|------|-----|
| **总 Task 数** | 42 |
| **总预计工时** | ~22.5 小时 |
| **Phase 分布** | A=4 / B=12 / D=4 / C=12 / E=6 / F=4 |
| **Production 代码行数估计** | ~750 行 |
| **测试代码行数估计** | ~650 行 |
| **HIGH 风险 Task** | 3（T-C3 bootstrap 集成 / T-C4 CancelledError 路径 / T-C5 LLM token budget） |

**Phase 执行顺序**：A → B（+D 可并行）→ C → E → F

---

## 1. Phase A — 实测侦察与 spec 校正

**目标**：验证 8 项 codebase 现状，产出 `phase-a-recon.md`，对 spec 做 3 处最小文字校正（无 AC/FR 编号变化）。无 production 代码改动。

**完成条件**：`phase-a-recon.md` 存在，8 项结论记录，baseline 3571 passed 维持不变。

**Codex Review 节点**：Phase A commit 后触发 **pre-impl Codex review**（foreground），检查 spec + plan 设计一致性。

---

### T-A1: 确认 tasks.created_at 索引（OQ-1）

- **Phase**: A
- **详情**: 读 `packages/core/src/octoagent/core/store/sqlite_init.py:12-35`，确认 `idx_tasks_created_at` 是否存在。预期结论 `IDX_TASKS_CREATED_AT = EXISTS`（plan §0.2 已记录结论：`sqlite_init.py:32` `CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC)`）。将结论写入 `phase-a-recon.md`。
- **依赖**: -
- **覆盖 AC**: -（侦察任务）
- **覆盖 FR**: FR-T1（前提验证）
- **测试**: -
- **行数估计**: 0 代码 / 0 测试（仅文档）
- **风险**: LOW

---

### T-A2: 确认 cheap alias 可用性（OQ-2）

- **Phase**: A
- **详情**: 读 `octoagent.yaml:14-25`，确认 `cheap` alias 已配置（provider=openai-codex, model=gpt-5.4, thinking_level=low）。将结论写入 `phase-a-recon.md`，字段 `CHEAP_ALIAS = CONFIGURED`。
- **依赖**: -
- **覆盖 AC**: -（侦察任务）
- **覆盖 FR**: FR-B3（前提验证）
- **测试**: -
- **行数估计**: 0 代码 / 0 测试
- **风险**: LOW

---

### T-A3: 确认 bootstrap 构造顺序（CQ-5）

- **Phase**: A
- **详情**: 读 `apps/gateway/src/octoagent/gateway/harness/octo_harness.py:860-900`（`_bootstrap_executors`）和 `octo_harness.py:1180-1210`（`_bootstrap_optional_routines`），确认 NotificationService 构造在前（第 869 行），AutomationSchedulerService 在后（第 1183-1197 行）。记录结论 `DAILY_ROUTINE_BOOTSTRAP_STEP = _bootstrap_optional_routines，位置在 automation_scheduler.startup() 之后`。写入 `phase-a-recon.md`。
- **依赖**: -
- **覆盖 AC**: -（AC-B6 前提）
- **覆盖 FR**: FR-DI1（前提验证）
- **测试**: -
- **行数估计**: 0 代码 / 0 测试
- **风险**: LOW

---

### T-A4: 校正 spec 3 处文字错误 + 产出 phase-a-recon.md

- **Phase**: A
- **详情**: 完成以下 3 处 spec 文字校正（不改 AC/FR 编号）：
  1. FR-B8 中 `channel.name` → `channel.channel_name`（plan §0.2 A-4 已确认）
  2. FR-B1 `misfire_grace_time=300` → `misfire_grace_time=30`（与 `automation_scheduler.py:63` 约定对齐；plan §0.2 A-6 已确认）
  3. `extract_summary_channels_from_user_md()` 设计要求补充"`web` → `web_sse` 映射"说明（plan §0.2 A-8 已确认）
  
  同时创建 `.specify/features/102-proactive-followup/phase-a-recon.md`，记录 T-A1～T-A3 的 8 项侦察结论（A-1 到 A-8）。
  
  文件路径：`spec.md`（修改），`.specify/features/102-proactive-followup/phase-a-recon.md`（新建）。
- **依赖**: T-A1, T-A2, T-A3
- **覆盖 AC**: -（文档）
- **覆盖 FR**: -
- **测试**: -
- **行数估计**: 0 代码（spec 文字修改）/ 0 测试
- **风险**: LOW

---

## 2. Phase B — 基础设施

**目标**：完成所有非 notification.py 的基础组件：4 个 EventType 枚举、两个新文件（`daily_routine_config.py`、`daily_routine.py` 空骨架）、`task_store.list_tasks_in_time_range`、USER.md 模板字段、`RoutineCompletedPayload` schema。Phase B 与 Phase D 改不同文件，可并行开发，但建议 B 先提交（减少 rebase 风险）。

**完成条件**：`uv run pytest -x -q tests/services/test_daily_routine_config.py tests/stores/test_task_store_time_range.py` 全绿，全量 >= 3571 passed。

**Codex Review 节点**：Phase B commit 后触发 per-Phase review（foreground），聚焦 `extract_summary_channels` "web"→"web_sse" 映射正确性、NaiveDatetime ValueError 覆盖。

---

### T-B1: 新增 4 个 EventType 枚举值

- **Phase**: B
- **详情**: 在 `packages/core/src/octoagent/core/models/enums.py` 中的 `EventType` 枚举类末尾新增：
  ```python
  ROUTINE_TRIGGERED = "ROUTINE_TRIGGERED"
  ROUTINE_COMPLETED = "ROUTINE_COMPLETED"
  ROUTINE_FAILED = "ROUTINE_FAILED"
  ROUTINE_SKIPPED = "ROUTINE_SKIPPED"
  ```
  确认无冲突（tech-research §任务 7 已实测现有 38 个 EventType 无 `ROUTINE_` 前缀）。预计 +4 行。
- **依赖**: T-A4（spec 校正完成后）
- **覆盖 AC**: AC-E1, AC-E2, AC-E3, AC-B2
- **覆盖 FR**: FR-E1
- **测试**: T-B1.T
- **行数估计**: 4 代码 / 5 测试
- **风险**: LOW

---

### T-B1.T: 单测——新 EventType 枚举值存在性验证

- **Phase**: B
- **详情**: 在 `tests/core/test_enums.py`（若已存在则追加，否则新建）添加断言：
  - `EventType.ROUTINE_TRIGGERED.value == "ROUTINE_TRIGGERED"`
  - `EventType.ROUTINE_COMPLETED.value == "ROUTINE_COMPLETED"`
  - `EventType.ROUTINE_FAILED.value == "ROUTINE_FAILED"`
  - `EventType.ROUTINE_SKIPPED.value == "ROUTINE_SKIPPED"`
  这是最小回归保护，确保枚举未拼错。
- **依赖**: T-B1
- **覆盖 AC**: AC-E1, AC-E2, AC-E3, AC-B2
- **覆盖 FR**: FR-E1
- **测试**: `tests/core/test_enums.py`
- **行数估计**: 0 代码 / 10 测试
- **风险**: LOW

---

### T-B2: 新建 daily_routine_config.py（解析函数 + DailyRoutineConfig dataclass）

- **Phase**: B
- **详情**: 新建 `apps/gateway/src/octoagent/gateway/services/daily_routine_config.py`，实现以下内容（预计 80-120 行）：

  ```python
  @dataclass(frozen=True)
  class DailyRoutineConfig:
      daily_summary_time: str          # "HH:MM"
      routine_active: bool
      summary_channels: frozenset[str] # {"telegram", "web_sse"}（已映射）
      user_timezone: str               # 默认 "UTC"

  def extract_daily_summary_time_from_user_md(content: str) -> str:
      """regex 匹配 `daily_summary_time: "HH:MM"`，非法值返回 "08:30" + WARNING log"""

  def extract_routine_active_from_user_md(content: str) -> bool:
      """regex 匹配 `routine_active: "true"/"false"`，非法值返回 True + WARNING log"""

  def extract_summary_channels_from_user_md(content: str) -> frozenset[str]:
      """regex 匹配 `summary_channels: "telegram,web"`；"web" → "web_sse" 映射；
      非法或空集 → frozenset({"telegram", "web_sse"}) + WARNING log"""

  def build_crontab_from_time(daily_summary_time: str) -> str:
      """"HH:MM" → "MM HH * * *" cron 格式"""
  ```

  regex pattern 参照 `notification.py:73`（`_ACTIVE_HOURS_PATTERN`）风格。三个解析函数非法值 fallback 均写 WARNING log（structlog），不抛出异常（Constitution C6）。
  
  **关键**：`extract_summary_channels_from_user_md` 必须将用户写法 `"web"` 映射为内部值 `"web_sse"`；返回 `frozenset[str]` 而非 `list`。
- **依赖**: T-A4
- **覆盖 AC**: AC-D1, AC-D2, AC-D3, AC-D4
- **覆盖 FR**: FR-D2
- **测试**: T-B2.T
- **行数估计**: 100 代码 / 0 测试（测试在 T-B2.T）
- **风险**: LOW

---

### T-B2.T: 单测——daily_routine_config 三个解析函数

- **Phase**: B
- **详情**: 新建 `tests/services/test_daily_routine_config.py`，覆盖以下场景（预计 100-130 行）：
  - `extract_daily_summary_time_from_user_md`：合法 "09:00" 返回 "09:00"；非法格式返回 "08:30"；字段缺失返回 "08:30"
  - `extract_routine_active_from_user_md`：`"true"` → `True`；`"false"` → `False`；非法值 → `True`；缺失 → `True`
  - `extract_summary_channels_from_user_md`：`"telegram"` → `frozenset({"telegram"})`；`"telegram,web"` → `frozenset({"telegram", "web_sse"})`（关键映射）；`"web"` → `frozenset({"web_sse"})`；空字符串 → 全渠道默认；非法值 → 全渠道默认
  - `build_crontab_from_time`：`"08:30"` → `"30 8 * * *"`；`"00:00"` → `"0 0 * * *"`
  
  覆盖 AC-D1（合法值）/ AC-D2（false 解析）/ AC-D3 解析侧（channels mapping）/ AC-D4（字段缺失默认值）。
- **依赖**: T-B2
- **覆盖 AC**: AC-D1, AC-D2, AC-D3, AC-D4
- **覆盖 FR**: FR-D2
- **测试**: `tests/services/test_daily_routine_config.py`
- **行数估计**: 0 代码 / 120 测试
- **风险**: LOW

---

### T-B3: 新增 task_store.list_tasks_in_time_range 方法

- **Phase**: B
- **详情**: 在 `packages/core/src/octoagent/core/store/task_store.py` 新增异步方法（预计 25-35 行）：

  ```python
  async def list_tasks_in_time_range(
      self,
      start: datetime,      # 必须为 UTC-aware datetime，否则 raise ValueError
      end: datetime,        # 必须为 UTC-aware datetime；范围 [start, end) 半开区间
      statuses: list[TaskStatus] | None = None,
  ) -> list[Task]:
  ```

  SQL：`SELECT * FROM tasks WHERE created_at >= :start AND created_at < :end [AND status IN ...]`
  
  入参校验：`if start.tzinfo is None or end.tzinfo is None: raise ValueError("datetime must be UTC-aware")`
  
  索引 `idx_tasks_created_at`（created_at DESC）已存在（T-A1 确认），无需新建迁移。返回 `list[Task]`（按现有 `Task` Pydantic 模型，与 `list_tasks` 返回类型一致）。
- **依赖**: T-A1（确认索引存在）
- **覆盖 AC**: AC-T1
- **覆盖 FR**: FR-T1
- **测试**: T-B3.T
- **行数估计**: 30 代码 / 0 测试（测试在 T-B3.T）
- **风险**: LOW

---

### T-B3.T: 单测——list_tasks_in_time_range SQL + 边界条件

- **Phase**: B
- **详情**: 新建 `tests/stores/test_task_store_time_range.py`（预计 90-120 行），使用真实 SQLite in-memory DB，覆盖：
  - 正常查询：构造 3 个 task（created_at 分别在范围前/内/后），断言只返回范围内的 1 个
  - 半开区间：`created_at == end` 的 task 不应包含（`< end` 语义）
  - 空结果：时间范围内无 task 时返回空列表
  - `statuses` 过滤：构造 2 个 task（一 completed / 一 failed），`statuses=["failed"]` 只返回 failed 的
  - NaiveDatetime ValueError：`start=datetime.utcnow()` 传入时 raise `ValueError`（确保 tzinfo 校验生效）
  - 查询耗时 < 500ms（构造 100 个 task，NFR-1 验证）
  
  覆盖 AC-T1 全部。
- **依赖**: T-B3
- **覆盖 AC**: AC-T1
- **覆盖 FR**: FR-T1
- **测试**: `tests/stores/test_task_store_time_range.py`
- **行数估计**: 0 代码 / 110 测试
- **风险**: LOW

---

### T-B4: 更新 behavior_templates/USER.md 新增 3 字段

- **Phase**: B
- **详情**: 编辑 `packages/core/src/octoagent/core/behavior_templates/USER.md`，在"工作习惯"节（`active_hours` 字段附近，约第 33 行）新增 3 行：
  ```markdown
  - daily_summary_time: "08:30"
  - routine_active: "true"
  - summary_channels: "telegram,web"
  ```
  格式与现有 `active_hours` 字段风格一致（Markdown 列表格式）。注意 `weekly_summary_day` 字段**不在 F102 范围**，不添加。+3 行，不修改其他内容。
- **依赖**: T-A4
- **覆盖 AC**: AC-D1, AC-D2, AC-D3, AC-D4
- **覆盖 FR**: FR-D1
- **测试**: -（模板文件，通过解析函数测试间接覆盖）
- **行数估计**: 3 代码（模板）/ 0 测试
- **风险**: LOW

---

### T-B5: 新建 RoutineCompletedPayload + RoutineFailedPayload schema

- **Phase**: B
- **详情**: 在 `daily_routine_config.py` 末尾（或独立 `daily_routine_payloads.py`，按实际行数决定）新增 Pydantic 模型（预计 25-35 行）：

  ```python
  class RoutineCompletedPayload(BaseModel):
      routine_type: Literal["daily"] = "daily"
      date: str                    # "YYYY-MM-DD"
      worker_count: int
      failed_count: int
      attention_count: int
      elapsed_ms: int
      llm_elapsed_ms: int = 0
      fallback: bool = False
      summary_length: int

  class RoutineFailedPayload(BaseModel):
      routine_type: Literal["daily"] = "daily"
      error_type: str              # 不含 traceback 原始文本（避免 PII）
      error_msg: str
  ```

  `RoutineFailedPayload` 的 `error_msg` 字段仅保留 `type(exc).__name__ + str(exc)[:200]`，不含 traceback。
- **依赖**: T-B1（依赖 EventType 枚举已就绪，避免循环导入）
- **覆盖 AC**: AC-E1, AC-E2, AC-E3
- **覆盖 FR**: FR-E2, FR-E3
- **测试**: T-B5.T
- **行数估计**: 30 代码 / 0 测试
- **风险**: LOW

---

### T-B5.T: 单测——Payload schema 字段验证

- **Phase**: B
- **详情**: 在 `tests/services/test_daily_routine_config.py` 末尾追加（或新建专门文件，按行数判断），验证：
  - `RoutineCompletedPayload` 必填字段校验（缺 `worker_count` 时 Pydantic 报错）
  - `RoutineCompletedPayload.fallback` 默认值为 `False`
  - `RoutineCompletedPayload.llm_elapsed_ms` 默认值为 `0`
  - `RoutineFailedPayload` 含 `error_type` + `error_msg` 字段
  
  预计 +20 行测试。
- **依赖**: T-B5
- **覆盖 AC**: AC-E1, AC-E2, AC-E3
- **覆盖 FR**: FR-E2, FR-E3
- **测试**: `tests/services/test_daily_routine_config.py`
- **行数估计**: 0 代码 / 20 测试
- **风险**: LOW

---

### T-B6: 新建 daily_routine.py 骨架（类定义 + __init__ + 占位方法）

- **Phase**: B
- **详情**: 新建 `apps/gateway/src/octoagent/gateway/services/daily_routine.py`，只写类骨架（不含具体实现逻辑，Phase C 填充），预计 50-70 行：

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
      ) -> None: ...   # 保存 self._xxx 属性

      async def startup(self) -> None: ...          # raise NotImplementedError（暂占位）
      async def shutdown(self) -> None: ...
      async def _run_daily_summary(self) -> None: ...
      async def _collect_yesterday_data(self, tz: ZoneInfo) -> ...: ...
      async def _generate_summary_llm(self, ...) -> str: ...
      def _generate_summary_fallback(self, ...) -> str: ...
      def _read_config(self) -> DailyRoutineConfig: ...
      def _compute_yesterday_range_utc(self, ...) -> tuple[datetime, datetime]: ...
  ```

  **目的**：提前验证 import 路径正确、DI 参数类型可用，让 Phase C 实现时减少 import 调试时间。骨架本身不需要对应测试。
- **依赖**: T-B2（daily_routine_config.py 已存在）, T-B5（Payload schema 可导入）
- **覆盖 AC**: -（骨架，AC-B6 在 Phase C T-C3 覆盖）
- **覆盖 FR**: FR-DI1（DI 参数签名）
- **测试**: -
- **行数估计**: 60 代码 / 0 测试
- **风险**: LOW

---

## 3. Phase D — F101 接口扩展（channels 参数）

**目标**：为 `NotificationService.notify_task_state_change` 新增 `channels: frozenset[str] | None = None` 可选参数（SD-6 / CHK-3.2 BLOCKER 决议），并扩展 `NOTIFICATION_DISPATCHED` payload 的 `channels` 字段，实现 channel 过滤路由。Phase D 与 Phase B 改不同文件，可并行。

**完成条件**：所有 F101 现有调用方（不传 channels）的单测仍通过，`test_notification_channels.py` AC-D3 全绿，全量 >= 3571 passed。

**Codex Review 节点**：Phase D commit 后触发 per-Phase review（foreground），聚焦向后兼容性和 NOTIFICATION_DISPATCHED payload 扩展。

---

### T-D1: notify_task_state_change 新增 channels 参数

- **Phase**: D
- **详情**: 修改 `apps/gateway/src/octoagent/gateway/services/notification.py`，在 `notify_task_state_change` 方法签名末尾新增 `channels: frozenset[str] | None = None` 参数（向后兼容），并在方法内部 channel 推送循环（约第 561-563 行）加过滤逻辑：

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
      channels: frozenset[str] | None = None,  # 新增，None = 全推（向后兼容）
  ) -> None:
      ...
      for channel in self._channels:
          if channels is not None and channel.channel_name not in channels:
              continue  # SD-6 channel 过滤
          await channel.notify(...)
  ```

  属性名使用 `channel.channel_name`（T-A4 已校正，非 `channel.name`）。`channels=None` 时维持现有行为，所有 F101 已有 caller（task_runner / approval_manager / ask_back_tools）不受影响。预计 +5 行修改。
- **依赖**: T-A4（channel_name 校正结论已确认）
- **覆盖 AC**: AC-D3, AC-F1
- **覆盖 FR**: FR-B8, SD-6
- **测试**: T-D1.T
- **行数估计**: 5 代码 / 0 测试
- **风险**: LOW

---

### T-D2: 扩展 NOTIFICATION_DISPATCHED payload 新增 channels 字段

- **Phase**: D
- **详情**: 修改 `notification.py` 中 `_write_notification_audit_event`（约第 338 行），在写入 `NOTIFICATION_DISPATCHED` 事件的 payload 中新增 `channels: list[str] | None` 字段：
  - `channels=None` 时（全推），payload 中 `channels=None`
  - `channels={"telegram"}` 时，payload 中 `channels=["telegram"]`
  
  其他字段（filtered / notification_id / priority 等）不变。预计 +3 行修改。
- **依赖**: T-D1
- **覆盖 AC**: AC-D3, AC-F1
- **覆盖 FR**: FR-B8
- **测试**: T-D1.T（同一测试文件覆盖）
- **行数估计**: 3 代码 / 0 测试
- **风险**: LOW

---

### T-D1.T: 单测——channels 参数路由验证

- **Phase**: D
- **详情**: 新建 `tests/services/test_notification_channels.py`（预计 80-100 行），使用 mock channel 验证：
  - `channels=None`：两个 channel（telegram / web_sse）均被调用
  - `channels=frozenset({"telegram"})`：只有 telegram channel 被调用，web_sse channel 未被调用
  - `channels=frozenset({"web_sse"})`：只有 web_sse channel 被调用
  - `channels=frozenset({"nonexistent"})`：无 channel 被调用（无崩溃）
  - `NOTIFICATION_DISPATCHED` payload 的 `channels` 字段与传入值对应
  
  同时回归验证：不传 channels 时（F101 现有调用方行为）所有 channel 均被调用（向后兼容）。
- **依赖**: T-D2
- **覆盖 AC**: AC-D3
- **覆盖 FR**: FR-B8
- **测试**: `tests/services/test_notification_channels.py`
- **行数估计**: 0 代码 / 90 测试
- **风险**: LOW

---

### T-D3: 验证 F101 现有调用方不受 channels 参数影响

- **Phase**: D
- **详情**: 运行 F101 现有相关单测，确认无 regression：
  ```bash
  uv run pytest -x -q --tb=short tests/services/test_notification*.py
  ```
  不需要写新测试，只需确认现有测试全绿。若有 baseline 测试失败，在此 task 中 debug 修复（改的只是方法签名追加可选参数，不应有 regression）。将验证结果记录在 task 注释或 commit message 中。
- **依赖**: T-D1, T-D2
- **覆盖 AC**: -（回归验证）
- **覆盖 FR**: FR-B8（向后兼容验证）
- **测试**: 现有 notification 测试文件
- **行数估计**: 0 代码 / 0 测试（回归运行）
- **风险**: LOW

---

## 4. Phase C — 核心 DailyRoutineService

**目标**：实现 `DailyRoutineService` 完整主体，包括 FR-B2 的 9 步执行路径、cron 注册（AC-B6）、bootstrap 集成（FR-DI1）、shutdown 段、所有集成测试和单测。这是 F102 最复杂的 Phase。

**依赖**：Phase B（4 EventType + daily_routine_config.py + task_store API + daily_routine.py 骨架）+ Phase D（channels 参数就绪）全部完成。

**完成条件**：集成测试 `test_daily_routine_integration.py` + `test_daily_routine_startup.py` + `test_daily_routine_summary.py` + `test_daily_routine_priority.py` 全绿，全量回归 >= 3571 passed。

**Codex Review 节点**：Phase C commit 后触发 per-Phase review（background），聚焦 CancelledError re-raise 正确性、bootstrap race、AC-B4 quiet hours 真实验证。

---

### T-C1: 实现 _compute_yesterday_range_utc + _read_config

- **Phase**: C
- **详情**: 填充 `daily_routine.py` 中两个同步辅助方法（预计 30-40 行）：

  **`_read_config()`**：调用 `await self._snapshot_store.get_live_state("USER.md")`（注意此处需要 async，调整为 async 方法或在 caller 中 await），解析三个字段并返回 `DailyRoutineConfig`。

  **`_compute_yesterday_range_utc(now_local: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]`**：
  - `yesterday_local = now_local.date() - timedelta(days=1)`
  - `yesterday_start_local = datetime(yesterday_local.year, yesterday_local.month, yesterday_local.day, 0, 0, 0, tzinfo=tz)`
  - `yesterday_end_local = yesterday_start_local + timedelta(days=1)`
  - 返回两个 UTC-aware datetime（`.astimezone(timezone.utc)`）
  
  两个方法均纯粹（无 IO 副作用，除 snapshot_store 读取外），易于单测。
- **依赖**: T-B2（config module 可导入）, T-B6（骨架存在）
- **覆盖 AC**: AC-D1, AC-D4
- **覆盖 FR**: FR-B2（步骤 2/4）, FR-D2
- **测试**: T-C1.T
- **行数估计**: 35 代码 / 0 测试
- **风险**: LOW

---

### T-C1.T: 单测——yesterday_range 时区计算

- **Phase**: C
- **详情**: 在 `tests/services/test_daily_routine_summary.py` 中添加（或新建专门文件）对 `_compute_yesterday_range_utc` 的单测（预计 20-30 行）：
  - UTC 时区：`now=2026-05-25T08:30:00+00:00` → 返回 `[2026-05-24T00:00:00+00:00, 2026-05-25T00:00:00+00:00)`
  - UTC+8 时区：`now=2026-05-25T08:30:00+08:00` → 返回 `[2026-05-24T16:00:00+00:00, 2026-05-25T16:00:00+00:00)`（本地昨日 0:00 对应 UTC 16:00）
  - 验证返回值均为 UTC-aware datetime（`tzinfo is not None`）
- **依赖**: T-C1
- **覆盖 AC**: AC-T1（时区语义）
- **覆盖 FR**: FR-B2（SD-10 时区语义）
- **测试**: `tests/services/test_daily_routine_summary.py`
- **行数估计**: 0 代码 / 25 测试
- **风险**: LOW

---

### T-C2: 实现 _collect_yesterday_data（task + event 查询）

- **Phase**: C
- **详情**: 实现 `_collect_yesterday_data(self, tz: ZoneInfo) -> tuple[list[Task], dict[str, list[Event]]]`（预计 40-55 行）：

  1. 调用 `_compute_yesterday_range_utc` 得到 `(yesterday_start, yesterday_end)`
  2. 调用 `await self._task_store.list_tasks_in_time_range(yesterday_start, yesterday_end)` 得到 tasks 列表
  3. 对每个 task，调用 `event_store.get_events_by_types_since(task_id, [STATE_TRANSITION, WORKER_DISPATCHED, WORKER_RETURNED, APPROVAL_REQUESTED, APPROVAL_EXPIRED], yesterday_start)` 得到 events
  4. 返回 `(tasks, {task_id: events_list})`

  **attention_count 计算**（SD-7 算法，内联在此方法或独立 helper）：
  ```python
  _ATTENTION_STATUSES = frozenset({"waiting_input", "waiting_approval", "paused", "escalated", "failed"})
  attention_count = sum(1 for t in tasks if t.status.value in _ATTENTION_STATUSES)
  failed_count = sum(1 for t in tasks if t.status.value == "failed")
  ```
  
  注意：`_ATTENTION_STATUSES` 包含 `"failed"`，所以 failed 任务同时计入 attention_count。
- **依赖**: T-B3（list_tasks_in_time_range 可用）, T-C1
- **覆盖 AC**: AC-E4, AC-T1
- **覆盖 FR**: FR-B2（步骤 4/6/7）, FR-T1
- **测试**: T-C2.T
- **行数估计**: 50 代码 / 0 测试
- **风险**: LOW

---

### T-C2.T: 单测——attention_count 计算（5-task fixture）

- **Phase**: C
- **详情**: 在 `tests/services/test_daily_routine_summary.py` 中添加测试（预计 30-40 行），验证 AC-E4：
  - 构造 5 个 mock Task（status 分别为：completed / failed / waiting_input / waiting_approval / running）
  - mock `task_store.list_tasks_in_time_range` 返回这 5 个 task
  - 调用 `_collect_yesterday_data`（mock event_store 返回空 events）
  - 断言：`failed_count == 1`，`attention_count == 3`（failed + waiting_input + waiting_approval，running 和 completed 不计入）
  
  这直接验证 SD-7 算法和 AC-E4 的测试数值预期（"attention_count == 3"）。
- **依赖**: T-C2
- **覆盖 AC**: AC-E4
- **覆盖 FR**: FR-B2（步骤 7）
- **测试**: `tests/services/test_daily_routine_summary.py`
- **行数估计**: 0 代码 / 35 测试
- **风险**: LOW

---

### T-C3: 实现 startup / shutdown + bootstrap 集成（cron 注册）

- **Phase**: C
- **详情**: 实现 `startup()` 和 `shutdown()` 方法，并在 `octo_harness.py` 中集成（预计 `daily_routine.py` +50 行，`octo_harness.py` +20 行）：

  **`startup()`** 步骤（AC-B6 + FR-B1 + FR-B5）：
  1. `await ensure_system_audit_task(self._task_store, self._DAILY_ROUTINE_AUDIT_TASK_ID)`（参照 `observation_promoter.py:40` pattern）
  2. `config = await self._read_config()`（读 USER.md）
  3. cron 表达式 `crontab_expr = build_crontab_from_time(config.daily_summary_time)`
  4. `user_tz = ZoneInfo(config.user_timezone)` （fallback `ZoneInfo("UTC")`）
  5. `self._scheduler.add_job(self._run_daily_summary, trigger=CronTrigger.from_crontab(crontab_expr, timezone=user_tz), id="_daily_routine", replace_existing=True, misfire_grace_time=30)`
  6. 异常兜底：`except Exception as exc: log.error("daily_routine_cron_register_failed", error=str(exc)); await self._write_routine_failed_event("cron_register_failed", exc)`（不向上传播，Constitution C6）

  **`shutdown()`**：`self._scheduler.remove_job("_daily_routine")`（try/except，job 不存在时忽略）

  **octo_harness.py `_bootstrap_optional_routines` 集成**（在 `automation_scheduler.startup()` 之后，约第 1197 行后）：
  ```python
  _daily_routine_service = DailyRoutineService(...)
  try:
      await _daily_routine_service.startup()
      app.state.daily_routine_service = _daily_routine_service
  except Exception as _exc:
      log.warning("daily_routine_service_init_skipped", error=str(_exc))
      app.state.daily_routine_service = None
  ```
  
  **shutdown 段**：在 gateway shutdown 逻辑中追加 `if getattr(app.state, "daily_routine_service", None): await app.state.daily_routine_service.shutdown()`

  **BLOCKER 风险**（HIGH）：bootstrap 集成涉及 `octo_harness.py`（1200+ 行），需精确定位插入点，错误的插入顺序会导致 NotificationService 未就绪时 startup 被调用。
- **依赖**: T-B6（骨架）, T-B2（config）, T-B1（EventType）, Phase D 全部完成
- **覆盖 AC**: AC-B6, AC-E1（ensure_system_audit_task 使 event 可写入）
- **覆盖 FR**: FR-B1, FR-B5, FR-DI1
- **测试**: T-C3.T
- **行数估计**: 70 代码 / 0 测试
- **风险**: **HIGH**（bootstrap 插入顺序 race 风险）

---

### T-C3.T: 集成测试——cron 注册 + replace_existing + 注册失败兜底

- **Phase**: C
- **详情**: 新建 `tests/services/test_daily_routine_startup.py`（预计 70-90 行，使用 mock scheduler 和 mock snapshot_store）：
  - 验证 `startup()` 后 `scheduler.add_job` 被调用一次，`id="_daily_routine"`，`replace_existing=True`，`misfire_grace_time=30`
  - 验证 `startup()` 调用两次时（重启模拟）第二次 `add_job` 仍调用（`replace_existing=True` 语义）
  - 验证 `scheduler.add_job` 抛出异常时，`startup()` 不向上传播（gateway 仍能启动）；验证 ERROR log 被记录；验证 `ROUTINE_FAILED` event 被写入 event_store
  - 验证 `shutdown()` 调用 `scheduler.remove_job("_daily_routine")`
  
  覆盖 AC-B6 全部。
- **依赖**: T-C3
- **覆盖 AC**: AC-B6
- **覆盖 FR**: FR-B1, FR-B5
- **测试**: `tests/services/test_daily_routine_startup.py`
- **行数估计**: 0 代码 / 80 测试
- **风险**: LOW

---

### T-C4: 实现 _run_daily_summary 主路径（FR-B2 全 9 步）+ CancelledError 处理

- **Phase**: C
- **详情**: 实现 `_run_daily_summary()` 主方法和 `_write_routine_*` 辅助方法（预计 80-100 行），按 FR-B2 的 9 步顺序：

  ```python
  async def _run_daily_summary(self) -> None:
      start_ts = time.monotonic()
      routine_event_id = await self._write_routine_triggered()   # 步骤 1
      try:
          config = await self._read_config()                      # 步骤 2
          if not config.routine_active:                           # 步骤 3
              await self._write_routine_skipped("routine_disabled")
              return
          tz = ZoneInfo(config.user_timezone)
          tasks, events_by_task = await self._collect_yesterday_data(tz)  # 步骤 4
          if not tasks:                                           # 步骤 5（SD-8）
              await self._write_routine_completed(worker_count=0, ...)
              return
          # 步骤 6+7 在 _collect_yesterday_data 中完成
          worker_count = len(tasks)
          failed_count = sum(1 for t in tasks if t.status.value == "failed")
          attention_count = sum(1 for t in tasks if t.status.value in _ATTENTION_STATUSES)
          # 步骤 8 LLM or fallback
          summary_text, is_fallback, llm_ms = await self._generate_summary(tasks, events_by_task)
          # 步骤 9 通知推送
          priority = NotificationPriority.MEDIUM if attention_count > 0 else NotificationPriority.LOW
          await self._notification_service.notify_task_state_change(
              task_id=self._DAILY_ROUTINE_AUDIT_TASK_ID,
              event_type="ROUTINE_DAILY_SUMMARY",
              payload={...},
              priority=priority,
              session_id=None,
              state_transition_event_id=routine_event_id,
              channels=config.summary_channels,
          )
          # 步骤 10 写 ROUTINE_COMPLETED
          await self._write_routine_completed(...)
      except asyncio.CancelledError:
          raise                           # FR-B6：显式 re-raise，不吞
      except Exception as exc:
          await self._write_routine_failed(exc)  # FR-B6：其他异常 catch + audit
  ```

  **CancelledError 处理**（HIGH 风险）：必须在 `except Exception` 之前显式 `except asyncio.CancelledError: raise`，不允许宽泛 `except Exception: pass`（FR-B6 / F101 M-1 broad-catch 教训）。
- **依赖**: T-C2（_collect_yesterday_data）, T-C3（startup/bootstrap）, T-D1（channels 参数可用）
- **覆盖 AC**: AC-B1, AC-B2, AC-B3, AC-B5, AC-B7, AC-E1, AC-E3, AC-F1
- **覆盖 FR**: FR-B2, FR-B4, FR-B6, FR-B7
- **测试**: T-C4.T（summary 路径），T-C5.T（集成）
- **行数估计**: 90 代码 / 0 测试
- **风险**: **HIGH**（CancelledError re-raise 是 Constitution C6 核心，遗漏会导致 silent failure）

---

### T-C4.T: 单测——CancelledError re-raise + ROUTINE_FAILED 写入

- **Phase**: C
- **详情**: 在 `tests/services/test_daily_routine_summary.py` 中添加（预计 40-50 行）：
  - 验证 `_collect_yesterday_data` 抛出 `asyncio.CancelledError` 时，`_run_daily_summary` 向上 re-raise，不写 `ROUTINE_FAILED` 事件
  - 验证 `_collect_yesterday_data` 抛出普通 `Exception("some error")` 时，`ROUTINE_FAILED` 事件被写入 event_store，包含 `error_type` + `error_msg` 字段
  - 验证 `_run_daily_summary` 不抛出（普通异常被 catch），即 cron loop 可以继续
  
  覆盖 AC-E3 全部（CHK-5.1 WARNING 要求的必须覆盖）。
- **依赖**: T-C4
- **覆盖 AC**: AC-E3
- **覆盖 FR**: FR-B6
- **测试**: `tests/services/test_daily_routine_summary.py`
- **行数估计**: 0 代码 / 45 测试
- **风险**: LOW

---

### T-C5: 实现 _generate_summary_llm + _generate_summary_fallback（含 token budget 截断）

- **Phase**: C
- **详情**: 实现两个摘要生成方法（预计 50-65 行）：

  **`_generate_summary_fallback(tasks, failed_count, attention_count) -> str`**：deterministic 模板渲染，无 IO，< 1s（NFR-2）：
  ```
  昨日 Worker 摘要（{date}）：
  - 完成任务：{worker_count - failed_count} 个
  - 失败任务：{failed_count} 个
  - 待关注：{attention_count} 个
  {若 failed_count > 0：各失败任务 title（最多 3 条）}
  ```

  **`_generate_summary_llm(tasks, events_by_task, ...) -> tuple[str, int]`**（返回摘要文本 + llm_elapsed_ms）：
  1. 构造 prompt：先加入 failed + attention task 的全量 events（优先级最高），再按 `created_at DESC` 补其余 task（仅 title + final_status），按 `len(text) / 4` 粗估 token 数，超 3000 时停止追加（SD-9 budget）
  2. 调用 `await self._provider_router.complete(model_alias="cheap", messages=[{"role":"user","content":prompt}], max_tokens=512)`（参照 `observation_promoter.py:468` pattern）
  3. 若返回空字符串/仅空白 → raise ValueError（触发 fallback）

  **外层 wrapper `_generate_summary`**：
  ```python
  try:
      text, llm_ms = await self._generate_summary_llm(...)
      return text, False, llm_ms
  except Exception:
      text = self._generate_summary_fallback(...)
      return text, True, 0  # fallback=True, llm_ms=0
  ```

  **风险**（HIGH）：token budget 截断策略依赖 `len(text)/4` 粗估，可能在非 ASCII 内容（中文）时严重低估 token 数（实际中文 token 约为字符数的 1/1.5）。F102 范围内接受此 trade-off（超限时 LLM 自动截断输出，不会崩溃），记录在 ROUTINE_COMPLETED.elapsed_ms 中可观测。
- **依赖**: T-C4（_run_daily_summary 调用者）, T-A2（cheap alias 已确认）
- **覆盖 AC**: AC-B3, AC-E2
- **覆盖 FR**: FR-B3
- **测试**: T-C5.T
- **行数估计**: 60 代码 / 0 测试
- **风险**: **HIGH**（token budget 中文字符估算偏差风险；LLM provider 调用路径 + fallback 触发条件覆盖完整性）

---

### T-C5.T: 集成测试——完整流程 + quiet hours + 空数据 + 事件链

- **Phase**: C
- **详情**: 新建 `tests/services/test_daily_routine_integration.py`（预计 120-150 行），使用真实 SQLite + 真实 event_store + mock LLM + 真实 NotificationService，覆盖：

  **AC-B1（完整流程）**：构造 2 个昨日 task，mock cheap alias 返回合法摘要，断言事件链 `ROUTINE_TRIGGERED → ROUTINE_COMPLETED`，`NOTIFICATION_DISPATCHED(filtered=False)` 存在

  **AC-B2（routine_active=false）**：注入 `routine_active: "false"` 的 USER.md，断言 `ROUTINE_SKIPPED(reason="routine_disabled")` 写入，`NOTIFICATION_DISPATCHED` 不存在

  **AC-B5（空数据不推送）**：task_store 返回空列表，断言 `ROUTINE_COMPLETED(worker_count=0)` 写入，`notify_task_state_change` 未调用

  **AC-E1（audit chain）**：断言两个事件均可通过 `task_id="_daily_routine_audit"` 检索

  **AC-E2（fallback=false when LLM OK）**：mock LLM 返回合法摘要，`ROUTINE_COMPLETED.payload["fallback"] == False`

  **AC-F1（NOTIFICATION_DISPATCHED）**：断言 `NOTIFICATION_DISPATCHED` payload 含 `filtered` 字段

  **AC-B4（quiet hours 过滤）**：使用真实 NotificationService + mock SnapshotStore 注入含 `active_hours: "09:00-23:00"` 的 USER.md（daily_summary_time 设为 02:00），断言 `NOTIFICATION_DISPATCHED(filtered=True)` 写入，mock channel.notify 未被调用（CHK-1.2 BLOCKER 决议）
- **依赖**: T-C4, T-C5, T-D1（channels 参数可用）
- **覆盖 AC**: AC-B1, AC-B2, AC-B4, AC-B5, AC-E1, AC-E2, AC-F1
- **覆盖 FR**: FR-B2, FR-B7
- **测试**: `tests/services/test_daily_routine_integration.py`
- **行数估计**: 0 代码 / 140 测试
- **风险**: MED（真实 SQLite + 真实 NotificationService 组合可能需要 fixture 调试）

---

### T-C6: 单测——priority 提升（attention_count > 0 → MEDIUM）和 fallback 模板

- **Phase**: C
- **详情**: 新建 `tests/services/test_daily_routine_priority.py`（预计 60-80 行）：

  **AC-B7（priority 决策）**：
  - mock 2 个 task（一 failed，一 completed），断言 `notify_task_state_change` 被调用时 `priority=MEDIUM`
  - mock 1 个 task（仅 completed，无 attention），断言 `priority=LOW`

  **AC-B3（fallback 路径单测，mock LLM 层面）**：
  - mock provider_router.complete 抛出 ConnectionError，断言 `_generate_summary` 返回 `(fallback_text, True, 0)`
  - 验证 fallback 文本符合 FR-B3 模板格式（包含"完成任务"/"失败任务"/"待关注"字段）

  **AC-B4（quiet hours，真实 NotificationService + mock SnapshotStore）**（此处与 T-C5.T 集成测试互补，单测聚焦 priority+quiet hours 逻辑）：验证 `attention_count=0` 时 priority 为 LOW，不受 quiet hours 影响（仅测 priority 逻辑，quiet hours 真实验证在集成测试 T-C5.T）
- **依赖**: T-C4, T-C5
- **覆盖 AC**: AC-B3, AC-B4, AC-B7
- **覆盖 FR**: FR-B3, FR-B4
- **测试**: `tests/services/test_daily_routine_priority.py`
- **行数估计**: 0 代码 / 70 测试
- **风险**: LOW

---

## 5. Phase E — LLM 摘要路径单独验证

**目标**：补全 Phase C 可能遗漏的 LLM 路径 edge case（空字符串响应、token budget 截断、fallback 模板 3 种情况），运行 e2e_smoke 回归确保 F101 已有 5 个 smoke 域 0 regression。

**依赖**：Phase C 完成。

**完成条件**：`pytest -m e2e_smoke` 全过，全量 >= 3571 + 新增测试数。

**Codex Review 节点**：Phase E commit 后触发 per-Phase review（foreground），聚焦 fallback 触发完整性、token budget 截断策略。

---

### T-E1: 单测——LLM 路径成功（ROUTINE_COMPLETED.fallback=False）

- **Phase**: E
- **详情**: 在 `tests/services/test_daily_routine_summary.py` 中添加（预计 20-25 行）：
  - mock `provider_router.complete` 返回合法摘要文本 `"昨日完成 2 个任务"`
  - 调用 `_generate_summary_llm`
  - 断言返回值为 `("昨日完成 2 个任务", llm_ms > 0)`（is_fallback=False 由外层 wrapper 决定）
  - 断言 ROUTINE_COMPLETED event 中 `fallback=False`（通过完整流程测试）
  
  对应 AC-E2 的 LLM 成功路径。
- **依赖**: T-C5
- **覆盖 AC**: AC-E2, AC-B3（LLM 成功侧）
- **覆盖 FR**: FR-B3
- **测试**: `tests/services/test_daily_routine_summary.py`
- **行数估计**: 0 代码 / 25 测试
- **风险**: LOW

---

### T-E2: 单测——LLM 空响应触发 fallback

- **Phase**: E
- **详情**: 在 `tests/services/test_daily_routine_summary.py` 中添加（预计 20-25 行）：
  - mock `provider_router.complete` 返回空字符串 `""`
  - 验证 `_generate_summary_llm` 抛出 ValueError（触发 fallback）
  - mock `provider_router.complete` 返回仅空白 `"   "`
  - 验证同样触发 fallback
  - 两种情况下 `is_fallback=True`，`llm_ms=0`
- **依赖**: T-C5, T-E1
- **覆盖 AC**: AC-B3
- **覆盖 FR**: FR-B3（fallback 触发条件完整性）
- **测试**: `tests/services/test_daily_routine_summary.py`
- **行数估计**: 0 代码 / 25 测试
- **风险**: LOW

---

### T-E3: 单测——fallback 模板 3 种情况格式验证

- **Phase**: E
- **详情**: 在 `tests/services/test_daily_routine_priority.py` 中添加（预计 30-40 行）：
  - 场景 A：`worker_count=3, failed_count=0, attention_count=0`，验证输出含"完成任务: 3"，无"失败任务"细节
  - 场景 B：`worker_count=3, failed_count=1, attention_count=1`，验证输出含"失败任务: 1"，含失败 task title
  - 场景 C：`worker_count=0`（空数据），此路径不调用 fallback（SD-8 决议），验证不产出摘要文本
  
  验证 fallback 模板格式符合 FR-B3 格式要求。
- **依赖**: T-C5
- **覆盖 AC**: AC-B3
- **覆盖 FR**: FR-B3
- **测试**: `tests/services/test_daily_routine_priority.py`
- **行数估计**: 0 代码 / 35 测试
- **风险**: LOW

---

### T-E4: 单测——token budget 截断策略验证

- **Phase**: E
- **详情**: 在 `tests/services/test_daily_routine_summary.py` 中添加（预计 25-35 行）：
  - 构造超过 3000 token 预算的 task 列表（10 个 task，每个 event 内容约 500 字符，总约 40k 字符 ≈ 10k token）
  - 其中 2 个 task 为 failed/attention 状态
  - 调用 prompt 构建逻辑（可提取为独立方法 `_build_llm_prompt` 以便测试）
  - 断言：failed + attention task 的 events 在 prompt 中完整存在
  - 断言：其余 task 仅以 "title + status" 形式出现（非全量 events）
  - 断言：最终 prompt 长度 * 4（粗估 token） < 12000（大约 3000 token 的 4x 容忍）
  
  注意：如果 `_build_llm_prompt` 内联在 `_generate_summary_llm` 中，此测试可能需要 mock 部分内部逻辑。若内联难以测试，建议将其提取为独立 `_build_llm_prompt(tasks, events_by_task) -> str` 方法。
- **依赖**: T-C5
- **覆盖 AC**: AC-B3（token budget 截断）
- **覆盖 FR**: FR-B3（SD-9 截断策略）
- **测试**: `tests/services/test_daily_routine_summary.py`
- **行数估计**: 0 代码 / 30 测试
- **风险**: MED（token budget 粗估与中文内容的偏差，可能需要调整测试断言容忍度）

---

### T-E5: [P] 单测——priority 决策最终验证（attention_count 边界）

- **Phase**: E
- **详情**: 在 `tests/services/test_daily_routine_priority.py` 中添加（预计 15-20 行），验证 AC-B7 的边界：
  - `attention_count=0` → `priority=LOW`
  - `attention_count=1` → `priority=MEDIUM`
  - `attention_count=100` → `priority=MEDIUM`（不会超过 MEDIUM）
  
  这是对 Phase C T-C6 中 priority 逻辑的补充边界测试。标注 [P]：与 T-E2/T-E3 无依赖关系，可并行写。
- **依赖**: T-C4
- **覆盖 AC**: AC-B7
- **覆盖 FR**: FR-B4
- **测试**: `tests/services/test_daily_routine_priority.py`
- **行数估计**: 0 代码 / 20 测试
- **风险**: LOW

---

### T-E6: e2e_smoke 回归验证

- **Phase**: E
- **详情**: 运行 F101 已有的 5 个 smoke 域，确保 0 regression：
  ```bash
  cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F102-proactive-followup/octoagent
  uv run pytest -m e2e_smoke -x -q --tb=short
  ```
  F102 不新增独立 e2e_smoke 域（避免 cron 时间依赖使测试不稳定，spec §11 决议）。若 smoke 失败，在此 task 中 debug 修复（F102 修改不应影响 F101 smoke 路径）。将结果记录在 Phase E commit message 中。
- **依赖**: T-E1, T-E2, T-E3, T-E4, T-E5（所有 Phase E 测试完成后）
- **覆盖 AC**: -（回归验证）
- **覆盖 FR**: NFR-4
- **测试**: `pytest -m e2e_smoke`
- **行数估计**: 0 代码 / 0 测试（回归运行）
- **风险**: LOW

---

## 6. Phase F — Final 验证与 Codex cross-Phase review

**目标**：确认 17 AC 全部 PASS，全量回归 >= 3571 + 新增，产出 completion-report.md + handoff.md，触发 Final Codex cross-Phase review（background）。

**依赖**：Phase E 完成（所有 production 代码 + 测试就绪）。

**Codex Review 节点**：Phase F 开始时触发 **Final cross-Phase Codex review**（background），输入全部 Phase A~E diff + spec.md + plan.md。

---

### T-F1: 17 AC 全覆盖矩阵检查

- **Phase**: F
- **详情**: 逐 AC 对照所有测试文件，确认每个 AC 至少有一个明确的 PASS 断言（不允许 TODO / skip）。填写本 tasks.md §8 AC↔Task 映射表中的"实际 task"列（与计划对齐）。检查项：
  - AC-B1 ~ AC-B7：7 个
  - AC-D1 ~ AC-D4：4 个
  - AC-E1 ~ AC-E4：4 个
  - AC-F1：1 个
  - AC-T1：1 个
  
  共 17 个 AC，全部确认有测试覆盖后记录结论。若发现漏洞，在此 task 中补充对应测试。
- **依赖**: 所有 Phase A~E task 完成
- **覆盖 AC**: 全部 17 AC
- **覆盖 FR**: 全部 16 FR
- **测试**: -（检查任务）
- **行数估计**: 0 代码（可能补 ≤30 行测试）
- **风险**: LOW

---

### T-F2: 全量回归 + 数量确认

- **Phase**: F
- **详情**: 运行全量测试：
  ```bash
  uv run pytest -x -q --tb=short -p no:cacheprovider
  ```
  目标：`passed >= 3571 + 新增测试数`（预估 +25~40），0 regression。若有 regression，在此 task 中 debug 修复（注意不要扩大修复范围超出 F102 改动的文件）。结果记录在 completion-report.md 中。
- **依赖**: T-F1
- **覆盖 AC**: -（全量回归）
- **覆盖 FR**: NFR-4
- **测试**: 全量
- **行数估计**: 0 代码 / 0 测试
- **风险**: LOW

---

### T-F3: 产出 completion-report.md + handoff.md

- **Phase**: F
- **详情**: 创建以下文档（纯文档，不含代码）：

  **`.specify/features/102-proactive-followup/completion-report.md`**：
  - Phase A~E 实际执行情况 vs 计划（是否偏离、已跳过哪些）
  - 17 AC 全部 PASS 确认
  - Codex review 闭环表（N high / M medium / K low）
  - 实际新增文件和修改文件清单（行数）
  - 已知 limitation（token budget 中文估算偏差 / quiet hours 多日丢失 / 重启才生效语义）

  **`.specify/features/102-proactive-followup/handoff.md`**（给 F103 / F107 / WeeklyRoutine）：
  - DailyRoutineService DI 接口（6 个参数）已稳定
  - `daily_routine_config.py` 解析函数可直接复用于 WeeklyRoutine
  - `ROUTINE_*` EventType 已建立，WeeklyRoutine 只需追加 4 个新值
  - F107 注意事项：D8 control_plane DI 不在 F102 范围（Phase E SKIP 归档）
  - quiet hours 多日丢失是 known limitation（D5 决策：不补发不延迟）
  - `daily_summary_time` 修改需重启生效（CQ-3 决议：YAGNI）
- **依赖**: T-F2
- **覆盖 AC**: -（文档）
- **覆盖 FR**: -
- **测试**: -
- **行数估计**: 0 代码 / 0 测试（文档）
- **风险**: LOW

---

### T-F4: Final cross-Phase Codex review 触发 + 0 HIGH 残留确认

- **Phase**: F
- **详情**: 触发 Final Codex adversarial review（background）：
  - 输入：Phase A~E 全部 diff + spec.md + plan.md
  - 聚焦：整体 audit chain（ROUTINE_TRIGGERED → ROUTINE_COMPLETED → NOTIFICATION_DISPATCHED）/ bootstrap 顺序 race / F101 边界（向后兼容）/ 17 AC 全覆盖确认 / handoff 完整性
  - 处理所有 high / medium finding（接受改动 或 拒绝并说明理由）
  - 确认 0 HIGH 残留后可 commit
  
  commit message 格式：`docs(F102-Final): cross-Phase Codex review 闭环 + completion-report + handoff（给 F103/F107）/ Codex: N high / M medium 全闭环 / K low ignored`
  
  **不主动 push origin/master，等用户拍板**（CLAUDE.local.md §Spawned Task 处理流程）。
- **依赖**: T-F3
- **覆盖 AC**: -（review）
- **覆盖 FR**: -
- **测试**: -
- **行数估计**: 0 代码 / 0 测试（review + possible fix）
- **风险**: MED（Codex review 可能发现新 HIGH，需要返工）

---

## 7. Phase 依赖图（DAG）

```
Phase A（侦察 + spec 校正）
    │
    ├──→ Phase B（enums + config + task_store + USER.md + 骨架）
    │         │
    ├──→ Phase D（notification.py channels 参数，与 B 可并行）
    │         │
    └─────────┴──→ Phase C（DailyRoutineService 完整主体，依赖 B + D 全部）
                       │
                       ↓
                   Phase E（LLM/fallback 路径验证 + e2e_smoke）
                       │
                       ↓
                   Phase F（Final 验证 + Codex cross-Phase review）
```

**Phase B/D 并行规则**：两者改不同文件（B 改 enums/task_store/config；D 改 notification.py），无代码冲突，可并行开发。建议 B 先提交，D 后提交（减少 rebase 风险）。Phase C 必须在 B 和 D **都完成后**启动。

---

## 8. AC ↔ Task 映射表

| AC | 描述（简）| 对应 Task IDs |
|----|----------|-------------|
| **AC-B1** | 完整流程 + P50 < 5s | T-C4, T-C5.T（集成测试） |
| **AC-B2** | routine_active=false 跳过 | T-C4, T-C5.T（集成测试 AC-B2 场景） |
| **AC-B3** | LLM 失败 → fallback + fallback=true | T-C5, T-C6, T-E1, T-E2, T-E3 |
| **AC-B4** | quiet hours 过滤 + filtered=True | T-C5.T（真实 NotificationService 集成）, T-C6 |
| **AC-B5** | 空数据不推送 + ROUTINE_COMPLETED | T-C4, T-C5.T（空数据场景） |
| **AC-B6** | cron 注册 job_id + replace_existing | T-C3, T-C3.T |
| **AC-B7** | attention_count>0 → MEDIUM | T-C4, T-C6, T-E5 |
| **AC-D1** | daily_summary_time 解析 | T-B2, T-B2.T, T-C1 |
| **AC-D2** | routine_active 解析 | T-B2, T-B2.T |
| **AC-D3** | summary_channels 过滤路由 | T-B2, T-B2.T（解析侧）, T-D1, T-D1.T（路由侧） |
| **AC-D4** | 字段缺失时默认值 | T-B2, T-B2.T |
| **AC-E1** | ROUTINE_TRIGGERED + ROUTINE_COMPLETED | T-B1, T-C3, T-C4, T-C5.T |
| **AC-E2** | ROUTINE_COMPLETED.fallback=true | T-C5, T-C5.T, T-E1 |
| **AC-E3** | ROUTINE_FAILED + CancelledError re-raise | T-B1, T-C4, T-C4.T |
| **AC-E4** | attention_count=3（5-task fixture）| T-C2, T-C2.T |
| **AC-F1** | NOTIFICATION_DISPATCHED（filtered）| T-D2, T-C4, T-C5.T |
| **AC-T1** | list_tasks_in_time_range SQL + 边界 | T-B3, T-B3.T, T-C1.T |

**覆盖率**：17 AC 全部有对应 Task，覆盖率 100%。

---

## 9. FR ↔ Task 映射表

| FR | 描述（简）| 对应 Task IDs |
|----|----------|-------------|
| **FR-B1** | startup cron 注册 + 异常兜底 | T-C3, T-C3.T |
| **FR-B2** | _run_daily_summary 9 步执行顺序 | T-C1, T-C2, T-C4, T-C5 |
| **FR-B3** | LLM 路径 + fallback + token budget | T-C5, T-C6, T-E1, T-E2, T-E3, T-E4 |
| **FR-B4** | attention_count→priority 映射 | T-C4, T-C6, T-E5 |
| **FR-B5** | ensure_system_audit_task 审计占位 | T-C3, T-C3.T |
| **FR-B6** | CancelledError re-raise + 异常 catch | T-C4, T-C4.T |
| **FR-B7** | notify_task_state_change 调用样板 | T-C4, T-D1 |
| **FR-B8** | channels 参数 + channel_name 校正 | T-D1, T-D2, T-D1.T |
| **FR-D1** | USER.md 模板新增 3 字段 | T-B4 |
| **FR-D2** | 3 个解析函数 + DailyRoutineConfig | T-B2, T-B2.T |
| **FR-E1** | 4 个 EventType 枚举值 | T-B1, T-B1.T |
| **FR-E2** | RoutineCompletedPayload schema | T-B5, T-B5.T |
| **FR-E3** | RoutineFailedPayload schema | T-B5, T-B5.T, T-C4.T |
| **FR-T1** | list_tasks_in_time_range + 索引确认 | T-A1, T-B3, T-B3.T |
| **FR-DI1** | DailyRoutineService DI __init__ | T-B6, T-C3 |
| **SD-6**（FR-B8 前置）| channels 参数向后兼容扩展 | T-D1, T-D2, T-D1.T, T-D3 |

**覆盖率**：16 FR 全部有对应 Task，覆盖率 100%。

---

## 10. Codex Review 触发节点

| 节点 | 触发时机 | 模式 | 聚焦范围 |
|------|---------|------|---------|
| **pre-impl（Phase A 后）** | T-A4 commit 后，Phase B 开始前 | foreground | spec + plan 设计一致性；channel_name 校正影响 AC-D3；bootstrap 顺序合理性 |
| **Phase B 后** | T-B6 commit 后 | foreground | `extract_summary_channels` "web"→"web_sse" 映射正确性；NaiveDatetime ValueError 是否覆盖；DI 骨架 import 路径 |
| **Phase D 后** | T-D3 commit 后 | foreground | channels 参数向后兼容；NOTIFICATION_DISPATCHED payload channels 字段；F101 现有调用方不受影响 |
| **Phase C 后** | T-C5.T commit 后 | **background** | CancelledError re-raise 正确性；bootstrap race（NotificationService 就绪顺序）；AC-B4 quiet hours 真实验证；token budget 截断逻辑 |
| **Phase E 后** | T-E6 commit 后 | foreground | fallback 触发条件完整性（空字符串/仅空白/None 响应）；token budget 中文估算偏差风险；e2e_smoke 结果 |
| **Final cross-Phase（Phase F）** | T-F2 完成后，T-F4 执行 | **background** | 整体 audit chain；F101 边界（向后兼容 17 个现有 caller）；17 AC 全覆盖确认；handoff 完整性；constitution C6 CancelledError 保证 |

---

## 11. 文件变更清单

| 文件 | 操作 | 预估行数变化 |
|------|------|------------|
| `packages/core/src/octoagent/core/models/enums.py` | 修改（+4 EventType）| +4 |
| `packages/core/src/octoagent/core/store/task_store.py` | 修改（+list_tasks_in_time_range）| +30 |
| `packages/core/src/octoagent/core/behavior_templates/USER.md` | 修改（+3 字段）| +3 |
| `apps/gateway/src/octoagent/gateway/services/notification.py` | 修改（+channels 参数 + payload 扩展）| +10 |
| `apps/gateway/src/octoagent/gateway/harness/octo_harness.py` | 修改（+bootstrap 集成 + shutdown 段）| +25 |
| `apps/gateway/src/octoagent/gateway/services/daily_routine_config.py` | **新建** | ~130（含 schema） |
| `apps/gateway/src/octoagent/gateway/services/daily_routine.py` | **新建** | ~320 |
| `tests/core/test_enums.py` | 修改或新建（+4 AC）| +15 |
| `tests/stores/test_task_store_time_range.py` | **新建** | ~110 |
| `tests/services/test_daily_routine_config.py` | **新建** | ~140 |
| `tests/services/test_notification_channels.py` | **新建** | ~90 |
| `tests/services/test_daily_routine_startup.py` | **新建** | ~80 |
| `tests/services/test_daily_routine_summary.py` | **新建** | ~175 |
| `tests/services/test_daily_routine_priority.py` | **新建** | ~105 |
| `tests/services/test_daily_routine_integration.py` | **新建** | ~140 |
| `.specify/features/102-proactive-followup/phase-a-recon.md` | 新建（文档）| — |
| `.specify/features/102-proactive-followup/completion-report.md` | 新建（文档）| — |
| `.specify/features/102-proactive-followup/handoff.md` | 新建（文档）| — |

**Production 代码总计**：~522 行（新增）+ ~72 行（修改）= ~594 行
**测试代码总计**：~855 行（新增）

---

## 12. 已知风险与阻塞

### HIGH 风险 Task（需特别关注）

| Task | 风险描述 | 缓解措施 |
|------|---------|---------|
| **T-C3** | bootstrap 集成 `octo_harness.py` 插入顺序错误可能导致 race（NotificationService 未就绪时 startup 被调用）| plan §0.2 CQ-5 已确认 NotificationService 在 _bootstrap_executors（第 869 行）完成，早于 _bootstrap_optional_routines（第 1183 行）；T-A3 已确认顺序 |
| **T-C4** | CancelledError re-raise 遗漏会导致 cron loop 被 silently cancelled（Constitution C6 违规）| T-C4.T 单测强制覆盖此场景；Codex Phase C review 聚焦此点 |
| **T-C5** | token budget 中文字符粗估（`len(text)/4`）误差大（中文实际 ~1.5 char/token）；LLM 响应非法格式触发 fallback 的边界条件 | T-E2/T-E4 覆盖 edge case；接受 trade-off（LLM 超限时自动截断，不崩溃） |

### 无需用户立即决策的已知 limitation

- `daily_summary_time` 修改需重启才生效（CQ-3 决议：YAGNI，handoff 中注明）
- quiet hours 内的 daily summary 不补发（D5 决策，audit 链有 filtered=True 记录）
- token budget 中文估算偏差（F107 可评估 tiktoken 集成）
- Phase D 和 Phase B 建议按序（B 先 D 后）提交，避免 rebase 风险（非阻塞）
