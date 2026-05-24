# F102 → F103 Handoff

**Source**: F102 Proactive Followup（DailyRoutine v0.1）
**Target**: F103 Blueprint 修订（M5 阶段 3 第 3 个 Feature，独立可并行）
**F102 终态**: feature/102-proactive-followup 分支，7 commits + 148 联合回归 0 regression
**Status**: READY_TO_MERGE（待用户拍板 Final review + push）

---

## 1. F102 关键架构产出（F103 Blueprint 修订必引用）

### 1.1 新组件

| 文件 | 行数 | 职责 |
|------|------|------|
| `apps/gateway/src/octoagent/gateway/services/daily_routine.py` | ~485 | DailyRoutineService 主类 + cron 注册 + 9 步执行 + LLM/fallback |
| `apps/gateway/src/octoagent/gateway/services/daily_routine_config.py` | ~285 | USER.md 3 字段解析 + DailyRoutineConfig + 4 payload schemas |
| `apps/gateway/tests/test_f102_daily_routine_config.py` | ~370 | 38 tests 解析 + payload schema |
| `apps/gateway/tests/test_f102_notification_channels.py` | ~250 | 8 tests channels 路由 + audit payload |
| `apps/gateway/tests/test_f102_daily_routine_service.py` | ~520 | 15 tests 主体 + LLM token budget |
| `packages/core/tests/test_task_store_time_range.py` | ~225 | 13 tests 时间窗 + 性能 + SD-10 时区 |

### 1.2 修改文件

| 文件 | 修改要点 |
|------|---------|
| `packages/core/src/octoagent/core/models/enums.py` | +4 EventType（ROUTINE_TRIGGERED/COMPLETED/FAILED/SKIPPED）|
| `packages/core/src/octoagent/core/store/task_store.py` | +list_tasks_in_time_range 方法（UTC 归一化，SD-10）|
| `packages/core/src/octoagent/core/behavior_templates/USER.md` | +3 字段（daily_summary_time / routine_active / summary_channels）|
| `apps/gateway/src/octoagent/gateway/services/notification.py` | notify_task_state_change +channels 可选参数 + audit payload 扩展 |
| `apps/gateway/src/octoagent/gateway/harness/octo_harness.py` | bootstrap _bootstrap_optional_routines 末尾构造 DailyRoutineService + shutdown 段加 routine.shutdown() |

### 1.3 新 EventType

```python
# enums.py:240-247
ROUTINE_TRIGGERED = "ROUTINE_TRIGGERED"    # cron 触发时刻
ROUTINE_COMPLETED = "ROUTINE_COMPLETED"    # 含 elapsed_ms / fallback / worker/failed/attention counts
ROUTINE_FAILED = "ROUTINE_FAILED"          # error_type + error_msg（无 traceback）
ROUTINE_SKIPPED = "ROUTINE_SKIPPED"        # reason (routine_disabled / no_user_timezone)
```

### 1.4 新 audit task

`_daily_routine_audit`（task_id 占位，FK 引用）—— F102 所有 ROUTINE_* 事件挂在此 task 下；event_store 查询 `task_id="_daily_routine_audit"` 可见完整 routine 历史。

---

## 2. F103 Blueprint 修订要点

### 2.1 应新增章节

#### §"Agent 协作三条设计哲学"补充 H1 实施

F102 是 H1 管家 mediated 模式的实施代表：**主 Agent 通过 NotificationService 推送 daily summary，用户被动接收"昨日做了什么"**——这是 H1"主动告知"的具体落地。

#### §"Proactive Followup"新章节

```markdown
## Proactive Followup（F102 Daily Routine v0.1）

### 触发模型
- APScheduler CronTrigger（cron 表达式由 USER.md daily_summary_time 字段生成）
- 默认每日 08:30（用户本地时区，从 USER.md active_hours 同源解析）
- 注册路径：AutomationSchedulerService.add_job（与现有 cron job 同一调度框架）

### 数据收集
- `task_store.list_tasks_in_time_range(yesterday_start_utc, yesterday_end_utc)`
- 时区语义：用户本地"昨日"边界 → UTC 归一化 → SQLite created_at 比较

### 摘要生成
- LLM 路径（cheap alias）：max_input ≤ 2000 中文字符 + max_output ≤ 512 token
- token 超限策略：attention task 详情优先 → succeeded task title-only → "... 以及 N 个其他完成任务"
- Fallback 路径（deterministic 模板）：LLM 不可用时自动启用，1s 内完成

### 推送
- F101 NotificationService.notify_task_state_change(channels=summary_channels)
- priority: attention_count > 0 时 MEDIUM，否则 LOW
- quiet hours 内 discard + filtered=True 审计（与 F101 一致）
- 空数据（worker_count=0）不推送通知，仍写 ROUTINE_COMPLETED event

### 配置（USER.md SoT）
- daily_summary_time: "HH:MM" 默认 "08:30"
- routine_active: "true"/"false" 默认 true
- summary_channels: "telegram,web" 用户友好写法（内部映射 "web" → "web_sse"）

### Audit
- 4 EventType: ROUTINE_TRIGGERED / COMPLETED / FAILED / SKIPPED
- 全部挂在 task_id="_daily_routine_audit" 下
- ROUTINE_COMPLETED 含 elapsed_ms / llm_elapsed_ms / fallback / channels 等完整字段
```

### 2.2 应更新的章节

#### §"系统服务清单"

| 服务 | 触发 | 用途 |
|------|------|------|
| ... |  |  |
| **DailyRoutineService**（F102 新增）| 每日 08:30 cron | 推送昨日 Worker 摘要 |

#### §"NotificationService 接口"

`notify_task_state_change` 签名追加 `channels: frozenset[str] | None = None` 参数：
- None（默认）：对所有已注册 channel push
- frozenset：仅对 `channel.channel_name ∈ channels` 的 channel push
- F102 daily routine 是当前唯一传 channels 的 caller（v0.1）

#### §"EventType 清单"

新增 4 个 ROUTINE_* EventType，描述参考本文件 §1.3。

#### §"USER.md 机器可读字段"

| 字段 | 引入 Feature | 格式 | 默认 | 解析路径 |
|------|-------------|------|------|---------|
| active_hours | F101 | "HH:MM-HH:MM" | None | extract_active_hours_from_user_md |
| daily_summary_time | F102 | "HH:MM" | "08:30" | extract_daily_summary_time_from_user_md |
| routine_active | F102 | "true"/"false" | true | extract_routine_active_from_user_md |
| summary_channels | F102 | "telegram,web" | "telegram,web" | extract_summary_channels_from_user_md（含 "web"→"web_sse" 映射）|

---

## 3. F102 中确定的接口契约（F103 同步）

### 3.1 NotificationService channels 参数（向后兼容）

F102 唯一对 F101 的 production 改动。F101 现有 caller（task_runner / approval_manager / ask_back_tools 等）不传 channels → 行为 0 变更。**Blueprint 应明示**：
- channels 参数仅供 F102+ routine 类 caller 使用
- channels=None 是 default，对所有 channel push
- channels=frozenset() 空集是边界（不推），实际不应被业务 caller 触发

### 3.2 daily_routine_config.py 复用

后续 WeeklyRoutine / Cookie Routine / 其他 routine 实施时可复用：
- 解析函数 pattern（regex + fallback + WARNING log）
- DailyRoutineConfig 风格的 frozen dataclass
- RoutineCompletedPayload 字段 schema（routine_type Literal 可扩展）

### 3.3 _ensure_audit_task pattern

F102 实施了"系统 routine audit task 占位"pattern（参照 ObservationRoutine）：
- 启动时 `await task_store.get_task(audit_task_id)`，不存在则 create_task + commit
- task.status = SUCCEEDED（避免被业务逻辑捡起）
- 所有 routine 事件 task_id 引用此占位

WeeklyRoutine / 其他系统 routine 应复用此 pattern。

---

## 4. F103 范围建议

F103 Blueprint 修订属于纯文档工作，不涉及代码改动。建议范围：

### 4.1 必做

1. 在 `docs/blueprint.md` 索引中加 F102 章节链接
2. 在 `docs/blueprint/` 子目录新增 `proactive-followup.md`（按上文 §2.1 模板）
3. 更新 §"系统服务清单" / §"NotificationService 接口" / §"EventType 清单" / §"USER.md 机器可读字段" 4 个章节
4. 在"M5 实施记录"章节追加 F102 完成节点

### 4.2 可选（视 F103 范围）

- 同步 F084-F102 整体演进路线（F084 USER.md SoT → F101 NotificationService → F102 DailyRoutine 链路）
- 更新 Constitution check 表格（F102 C6 graceful degrade 实施样本）
- 添加 D 列入"已解决架构债"标记

### 4.3 不在 F103 范围（明确排除）

- 任何代码改动（F103 纯文档）
- 触发 D8 / D11 / D12 等架构债重构（推迟 F107）
- WeeklyRoutine spec（独立 Feature）

---

## 5. F102 已知 limitations（F103 文档化）

| Limitation | 推迟到 | F103 是否文档化 |
|-----------|--------|----------------|
| dismiss 跨重启持久化 | F107 | 否（F101 已归档） |
| 运行期 daily_summary_time 修改需重启生效 | YAGNI / 未来 Feature | **是**（用户文档需提示）|
| quiet hours 多日丢失（持续 daily_summary_time 在 quiet 内）| 用户调整 daily_summary_time | **是**（建议加在 USER.md 字段注释中）|
| N+1 query 性能（task > 50 时 P50 > 5s）| F107 batch_get_events | 否（运行期数据 audit 后再评估）|
| WeeklyRoutine 未实施 | 独立 Feature | **是**（明示）|

---

## 6. F102 实施过程沉淀（对 F103+ 有参考价值）

### 6.1 Spec 阶段实测侦察的价值（7 连 pattern 第 8 次实证）

F102 plan Phase A 预实测发现 spec 4 处冲突：
- Hermes Agent 源码不存在（设计 fallback 到 ObservationRoutine pattern）
- USER.md 当前只有 active_hours 字段
- approval_timeout_seconds 不在 USER.md（在 policy/models.py）
- 无 TASK_COMPLETED EventType（走 STATE_TRANSITION.payload.to_status）

**结论**：spec-driver Phase A 实测侦察是必做项，不能跳。

### 6.2 Codex review per-Phase 风险

- Phase B review 抓到 1 BLOCKER + 1 HIGH（H1 summary_channels regex / H2 时区比较）—— review 价值显著
- Phase D review 输出不完整（codex 后台任务流产）—— per-Phase review 不可靠时需 Final cross-Phase 兜底
- 建议未来 Feature：per-Phase review 与 Final review 双保险

### 6.3 实施时 spec 校正属正常工作流

F102 实施过程中发现 spec 4 处需校正：
- FR-B8 channel.name → channel.channel_name（plan A 实测）
- summary_channels "web" → "web_sse" 映射（plan A 实测）
- misfire_grace_time 300 → 30（与现有约定对齐）
- SD-7 attention_statuses 去掉 escalated（4 个 TaskStatus 实际值）

校正以 trace + spec 文字更新 + commit message 显式归档，符合 CLAUDE.local.md §工作流改进。

---

## 7. F103 启动前置 checklist

F102 完成后启动 F103 之前确认：

- [ ] F102 Final cross-Phase Codex review 完成（Phase C + D + E）
- [ ] F102 push 到 origin/master（用户拍板）
- [ ] F102 远端分支 feature/102-proactive-followup 删除（按 CLAUDE.local.md §远端分支精简）
- [ ] F102 worktree 不再需要时清理（git worktree remove）

**F103 准备就绪信号**：本 handoff.md + completion-report.md push 到 master，F102 PR / squash merge 完成。

---

**生成时间**: 2026-05-25
**F102 commits 索引**: 见 completion-report.md §4
**F102 联合回归**: 148 passed in 5.62s，0 regression vs F101 baseline 74c9ab3
