# F132 cron 自助工具 — Completion Report

> M8 P1，规模 M。分支 `feature/132-cron-self-serve`（off master 1e64ecd3）。**未 push**，等用户拍板。

## 1. 交付摘要

让用户/主 Agent 从手机（Telegram/Web）用自然语言自助建/改/删/列定时任务。

**核心发现（诊断）**：后端 CRUD（F026 `automation_store` + `automation_service` + `automation_scheduler`）
**完整**，但只能跑 control-plane **管理动作**（memory.consolidate / backup.create 等），
**没有任何动作能把提醒交付给用户**——所以任务示例「每周一提醒我 X」此前后端跑不通。

**本 Feature 补三处**：①agent 写工具；②`reminder.notify` 交付动作（补齐 cron→用户缺口）；③Web UI。

## 2. 改动清单（相对 master）

### 新增
- `apps/gateway/.../builtin_tools/cron_tools.py` — `cron.create/update/delete` 工具主体
- `frontend/src/pages/AutomationCenter.tsx` — 定时任务列表页
- 测试：`test_cron_tools.py`(21) / `test_reminder_action.py`(6) / `test_automation_resource_route.py`(2) / `AutomationCenter.test.tsx`(9)

### 修改（backend）
- `core/models/tool_results.py` + `__init__.py` — `CronMutationResult(WriteResult)` 回显契约
- `core/models/enums.py` — `AUTOMATION_JOB_MUTATED` EventType
- `tooling/models.py` — `CoreToolSet.default()` 加 `cron.list/create/update/delete`（12→16）
- `control_plane/automation_service.py` — `reminder.notify` action + handler
- `control_plane/action_registry.py` — 注册 `reminder.notify`
- `control_plane/_base.py` + `_coordinator.py` — `ControlPlaneContext.notification_service` 绑定
- `builtin_tools/write_approval.py` — `gate_destructive_action`（通用 Two-Phase 审批门）
- `builtin_tools/_deps.py` — `ToolDeps._automation_scheduler` late-bind 字段
- `builtin_tools/__init__.py` — 注册 cron_tools
- `routes/control_plane.py` — `GET /api/control/resources/automation`
- `harness/octo_harness.py` — notification_service 传入 control_plane + scheduler late-bind 到 ToolDeps

### 修改（frontend）
- `api/client.ts` — `fetchAutomationDocument`
- `App.tsx` — `/automation` 路由
- `components/shell/WorkbenchLayout.tsx` — 导航「定时任务」项

### 修改（测试护栏）
- `test_models.py` / `test_deferred_tools_e2e.py` / `test_061_unified_tool_permission.py` — Core size 上限随 cron 工具入 Core 调整（F135 同款先例，非 gaming）

## 3. 关键设计决策

| # | 决策 | 理由 |
|---|------|------|
| DP-1 | 命名工具 `cron.create/update/delete`（非 OpenClaw action 枚举）| 与 `cron.list`/`user_profile.*` 一致；per-tool schema 对弱 model 友好 |
| DP-2 | 新增 `reminder.notify` action | **补齐 cron→交付用户缺口**；H1 守界=通知非对话轮次（同 F102 daily summary）|
| DP-3 | NL↔cron 由 LLM 自译，工具只校验 | Constitution #9（不写规则引擎）|
| DP-4 | 时区接 F115 降级链 | 复用 `extract_user_timezone_from_user_md` |
| DP-5 | 破坏性操作走 `gate_destructive_action` | Constitution #4/#7（Two-Phase）|
| DP-6 | 工具直调 store + scheduler.sync | 落盘后立即生效（crash gap 由 scheduler.startup 自愈）|
| DP-7 | 进 CoreToolSet | F135 强制先例（不进 → deferred → 弱 model 两跳不可靠）|
| DP-8 | Web 只读+toggle，删除走对话 | 破坏性删除无 exec_ctx 挂 ApprovalGate → 走对话治理（Codex P1-3）|

## 4. 双评审闭环

### Codex Review
- **Spec review（成功）**：3 P1 finding，**全接受闭环**：
  - **P1-1**（真陷阱）：APScheduler `from_crontab` 星期 Monday=0（非 Unix Monday=1），`0 9 * * 1` 实证触发**周二**。LLM 按 Unix 约定产出数字 DOW → 每周提醒错一天。**修复**：docstring 强制命名星期 + 工具校验层拒绝纯数字 DOW。
  - **P1-2**：`action_id` 创建路径可排任意 action（含 `update.apply`）绕过审批。**修复**：`_CRON_AGENT_ACTION_ALLOWLIST` 安全白名单。
  - **P1-3**：Web `automation.delete` 绕过 `cron.delete` 的 ApprovalGate。**修复**：Web 去删除按钮，删除走对话。
- **全量 diff review（成功）**：3 finding（2 P1 + 1 P2），**全接受闭环**：
  - **P1-1（核心交付 bug，Codex 独有发现）**：`reminder.notify` 的 `message` 在 `notify_task_state_change` payload，但渠道模板（Telegram/Slack/Discord）读 `task_title`/`to_status`/`summary` → 用户收到「未命名任务/状态空」、**提醒正文丢失**。这是本功能核心交付路径的真 bug。**修复**：payload 加 `notify_kind="reminder"` + notification.py `_reminder_text_or_none` 专用渲染分支。
  - **P1-2（时区 bug）**：`once` 的 naive ISO 原样存 → 调度器 DateTrigger 补 UTC 忽略 job.timezone → 非 UTC 用户一次性提醒错开。**修复**：`_validate_schedule` 对 once naive ISO 按 timezone 归一化为 aware。
  - **P2-1（前端假成功）**：`executeControlAction` 在 404/409 仍返回 result → toggle 不查 `result.status` 显示假「已暂停」。**修复**：查 `result.status==="rejected"` 走错误提示。

### Opus 对抗自审
- **抓 1 真 bug**：`_emit_cron_event` 的 `Event.ts` 用 `datetime.now()`（naive local）→ 改 `datetime.now(timezone.utc)`（全库一致；naive 时间戳在 event store 排序/比较出错，F129 已有同类坑）。**已修**。
- **验证通过**：DOW 校验边界（`mon-fri`/`mon,wed,fri` 放行；数字范围/步长/`#` 拒绝）；scheduler.startup crash-gap 自愈（`list_jobs()` → `sync_job` 逐个重挂）；`reminder.notify` 经 coordinator `execute_action` 路由正确。

## 5. 已知 limitations / 设计权衡

- **quiet hours 与 reminder**：`reminder.notify` 用 `NotificationPriority.MEDIUM`，若用户显式配置了 quiet hours 且提醒时刻落在其内，会被静默过滤（与 F102 daily summary 同路径）。**默认无 quiet hours → 提醒正常触发**。是否让用户自定义提醒豁免自己的 quiet hours 是产品判断，本期未做（spec 未要求；避免范围外扩）。
- **ONCE 时区**：`schedule_kind="once"` 的 `timezone` 参数存储但 DateTrigger 用绝对时刻（naive ISO 按 UTC，同 OpenClaw 语义），docstring 已说明。
- **cron.update 改 name 也需审批**：保守设计（任何非 enabled 改动走审批）。renaming 低风险但仍走审批，可后续放宽。
- **Web 无创建表单**：创建走对话（H1 mediated），非本期 UI 范围。

## 6. 验证

- **F132 新测试**：41 passed（cron 22 + reminder 7 + resource 2 + 前端 10）。
- **回归**：core+tooling 772 passed；gateway 全量单测（排除 e2e_live）**2341 passed 0 fail**（Codex 修复后 +2；仅 Core-size 护栏测试按预期调整）。0 regression vs master 1e64ecd3。
- **前端**：vitest 10 passed；tsc 新文件 0 错误（4 个 marked/dompurify/diff 缺类型是 master 既有）。
- **e2e_smoke**：每次 commit pre-commit hook 8/8 passed。
- **preview**：worktree 共享 node_modules 缺 diff/marked/dompurify 依赖（master 同样缺）→ vite build 不可跑，UI 经 vitest 单测覆盖验证。
- **环境插曲（非代码问题）**：会话中途 shared `.venv` 被 `ulid-py`==1.1.0 污染（其 `ULID` 需 buffer 参数，与代码全库 `str(ULID())` 无参调用不兼容 → `MemoryView.__init__` TypeError），master 同样受影响。`uv pip uninstall ulid-py` + `uv pip install --reinstall python-ulid>=3.1`（surgical，不动 lockfile）修复。此后全量绿。

## 7. 用户上手（真机怎么让 agent 帮建定时任务）

1. **对话建**（Telegram/Web）：「每周一早上9点提醒我交周报」→ 主 Agent 译成 `cron.create(schedule_kind="cron", schedule_expr="0 9 * * mon", reminder_text="交周报")` → 回复已建。到点通知系统推送「交周报」到用户 channel。
2. **对话改/删**：「把喝水提醒暂停」→ `cron.update(enabled=false)`（直改）；「删掉周报提醒」→ `cron.delete`（弹审批卡片，批准后删）。
3. **Web 查看/暂停**：`/automation` 页看所有定时任务（人读化时间 + 状态），行内 toggle 暂停/恢复。删除走对话。
