# F132 cron 自助工具（OC-5）— Spec

> M8 P1，规模 M。让用户/主 Agent 从手机（Telegram/Web）用自然语言自助建/改/删/列定时任务。
> 后端 CRUD 已在（F026），本 Feature 补 **agent 工具暴露 + 缺失的 reminder 交付动作 + 用户面 UI**。

## 0. 现状诊断（file:line 证据）

### 0.1 后端 automation CRUD —— 完整（生产可用）

| 层 | 文件 | 结论 |
|----|------|------|
| 模型 | `packages/core/src/octoagent/core/models/control_plane/automation.py:23-50` | `AutomationScheduleKind{INTERVAL,CRON,ONCE}` + `AutomationJob{job_id,name,action_id,params,project_id,schedule_kind,schedule_expr,timezone,enabled,...}`。**已含 `timezone` 字段**。 |
| 持久化 | `.../control_plane/automation_store.py:22-100` | JSON 文件 `{project_root}/data/control-plane/automation-jobs.json` + FileLock 原子写。CRUD 全：`list_jobs/get_job/save_job/delete_job/list_runs/save_run/get_run`。 |
| 领域服务 | `.../control_plane/automation_service.py:73-80` | action 路由 `automation.{create,run,pause,resume,delete}` + `get_automation_document`。 |
| 调度 | `.../services/automation_scheduler.py:181-206` | **APScheduler**：`CronTrigger.from_crontab(expr, timezone=job.timezone)` / `IntervalTrigger(seconds, timezone)` / `DateTrigger`。时区在 trigger 级已正确落地。 |
| 触发执行 | `.../automation_scheduler.py:136-178` | 触发 → `control_plane_service.execute_action(ActionRequestEnvelope{action_id, params})`。 |

**结论：CRUD 95% 完整**。缺三处（本 Feature 补）：agent 写工具、reminder 交付动作、Web UI。

### 0.2 已有 agent 工具 —— 只有只读 `cron.list`

`builtin_tools/runtime_tools.py:167-184` 已注册 `cron.list`（`AutomationStore(deps.project_root).list_jobs()` 直读，`SideEffectLevel.NONE`，toolset=`ops_tools`，entrypoints=`{agent_runtime, web}`）。
**无** `cron.create/update/delete` agent 工具。`automation.*` 是 control-plane action（REST），主 Agent 无法直接调用。

### 0.3 关键架构缺口 —— automation 只能跑"管理动作"，无法给用户发提醒

`automation.create` 的 `action_id` 必须是已注册 control-plane action（`automation_service.py:288-308` 校验存在性 + 禁 `automation.*` 递归）。现有 action 全是**管理操作**（`action_registry.py:250-340`：memory.consolidate / backup.create / session.* / operator.* 等）。
**没有任何 action 会"把一段文字/提醒推送给用户"**。因此任务示例里的「每周一提醒我 X」**当前后端跑不通**——job 能建、能触发，但触发后无处交付。

对照物：F102 `daily_routine.py:284` 用 `NotificationService.notify_task_state_change(channels=...)` 把每日总结推给用户——这是"cron→交付给用户"的唯一现存范式，但 DailyRoutine 是**单一用途**硬编码（只发昨日 worker 摘要），不接受用户自定义提醒。

### 0.4 时区 —— scheduler 级已用 job.timezone，但 create 默认 UTC 未接 F115

`automation_service.py:343` create 时 `timezone = params.get("timezone","UTC")`，**未走 F115 的 USER.md > env > UTC 降级链**。F115 的降级链在 `daily_routine.py` 内。

### 0.5 Web UI —— 零

`frontend/src/types/index.ts` 有 `AutomationJob*` TS 类型，但 `components/` `pages/` **无** automation/scheduler/cron 组件。`GET /api/control/resources/automation` 后端 route **不存在**（`get_automation_document` 只在 `document_routes()` 注册，未接 REST resource endpoint，前端 `platform/contracts/controlPlane.ts` 也无 automation 条目）。

---

## 1. 设计决策

### DP-1 agent 工具形态：3 个命名工具 `cron.create/update/delete`（+ 复用现存 `cron.list`）

任务明确要求 `cron.create/update/delete/list`。Octo 惯例是命名工具（`user_profile.update/read/observe`、`work.split/merge`），**不采用** OpenClaw 的单 `cron` + action 枚举——命名工具的 per-tool schema 对弱 model 更友好（schema 直接告诉参数），且与 `cron.list` 已有命名一致。

- `cron.list` —— 已存在，不改（只读）。
- `cron.create(name, schedule_kind, schedule_expr, reminder_text | action_id, action_params?, timezone?, enabled?)` —— 新建。
- `cron.update(job_id, patch...)` —— 改（改 schedule/enabled/name/reminder_text）。
- `cron.delete(job_id)` —— 删。

### DP-2 【核心】新增 `reminder.notify` control-plane action —— 补齐"cron→交付用户"缺口

无此动作，用户自定义提醒无法交付（§0.3）。新增最小 action：触发时把 `params.message` 经
`NotificationService.notify_task_state_change` 推到用户 channel（复用 F102 §0.3 唯一范式）。

- `reminder.notify` 注册进 action_registry + 一个新 domain service（`ReminderDomainService`）或挂到 automation_service（择简）。
- **H1 守界**：交付是 **notification**（系统提醒卡片），不是主 Agent「假装说话」。主 Agent 是提议+建 job 的人；到点由通知系统提醒用户。用户看到提醒后可再对话让 Agent 做事。这与 F102 daily summary 同构，不违 H1（主 Agent 仍是唯一 user-facing speaker——通知不是对话轮次）。
- **范围排除**：不做「到点自动跑一个 LLM agent turn 生成内容」（如现算「总结昨天」）。理由：①「总结昨天」F102 DailyRoutine 已是专用机制；②自动 agent turn 触及主会话注入 + LLM 成本 + H1 边界，属 XL，超 F132（M）。`cron.create` 支持 `action_id` 直连已注册管理动作（如 `memory.consolidate`）给高级用户，但**默认路径是 reminder_text→reminder.notify**。

`cron.create` 参数二选一：
- 传 `reminder_text` → 自动设 `action_id="reminder.notify"`, `params={"message": reminder_text, ...}`（用户面默认路径）。
- 传 `action_id`（+ `action_params`）→ 直连已注册动作（高级）。**【Codex P1-2】安全白名单**：agent 工具的
  action_id 路径**仅允许**安全只读/低风险管理动作白名单 `_CRON_AGENT_ACTION_ALLOWLIST`
  （初始 `{"reminder.notify", "memory.consolidate", "memory.profile_generate"}`）。传入白名单外
  action_id（如 `update.apply` / `runtime.restart` / `operator.*`）→ `status="rejected",
  reason="action_not_allowed"`，不落盘。理由：automation scheduler 触发时按 SYSTEM surface
  直接 `execute_action`，coordinator **不会**按 action_registry 的 `approval_hint` 自动拦截——
  若放任 agent 排任意 action，等于让 Agent 免审批安排高风险操作（绕过 Constitution #4/#7）。
  高风险动作的定时化留 Web/CLI 显式操作（非本 agent 工具范围）。

### DP-3 NL↔cron 转换：LLM 自己译，工具只收结构化（Constitution #9）

**不写正则/规则引擎**把「每天早8点」解析成 cron。主 Agent（LLM）负责把自然语言译成
`schedule_kind` + `schedule_expr`（cron 表达式 / 秒数 / ISO datetime），工具只接收结构化入参并**校验合法性**（cron 表达式能否被 `CronTrigger.from_crontab` 解析；秒数>0；ISO 可 parse）。工具 docstring 给足 few-shot 示例（「每天早8点」→ kind=cron, expr=`0 8 * * *`；「每周一9点」→ `0 9 * * mon`；「30分钟后一次」→ kind=once + 算好的 ISO），引导 LLM 正确产出——这是"提供上下文让 LLM 决策"，非硬编码替代决策。

**【Codex P1-1 关键陷阱】星期约定**：调度器用 APScheduler `CronTrigger.from_crontab`，其数字
星期是 **Monday=0 / Sunday=6**（与标准 Unix cron 的 Sunday=0 / Monday=1 **不同**）。实证：
`0 9 * * 1` 在 APScheduler 下触发**周二**（不是周一）。LLM 多按 Unix 约定产出数字 DOW，会导致
**每个周提醒错一天**。缓解（两层）：
1. docstring 强制 few-shot **只用命名星期**（`mon/tue/wed/thu/fri/sat/sun`），APScheduler 对
   命名 DOW 无歧义（`0 9 * * mon` 实证触发周一）。
2. 工具校验层：`schedule_expr` 的第 5 字段若为**纯数字 DOW**（含范围/列表），`status="rejected",
   reason` 提示改用命名星期——把 off-by-one 的沉默错误变成显式拒绝 + 引导。`*` 与命名星期放行。

### DP-4 时区：工具层接 F115 降级链（USER.md > env > UTC）

`cron.create` 的 `timezone` 参数缺省时，工具解析 USER.md `user_timezone`（F115 已有 helper）→ env `OCTOAGENT_USER_TIMEZONE` → UTC。抽 F115 现有解析逻辑为共享 helper 复用，不重写。LLM 也可显式传 timezone 覆盖。

### DP-5 破坏性操作走 Two-Phase 治理（Constitution #4/#7）

- `cron.create` —— **非破坏**（新增可逆，随时可删/禁）。直接执行，`SideEffectLevel.REVERSIBLE`。
- `cron.delete` —— **破坏**（删除已有 job 不可逆恢复其 run 历史关联）。`SideEffectLevel.IRREVERSIBLE` → 服务端 ApprovalGate（镜像 F136 `write_approval` 序列：request_approval→双注册→notify(CRITICAL)→wait_for_decision(300s)）。审批卡片摘要含 job 名/schedule。
- `cron.update` —— **改已有 job**：改 `enabled`（暂停/恢复）视为**可逆**低风险直接执行；改 `schedule_expr`/`reminder_text`/`name` 改变既定行为 → 与 delete 同级走审批。判定：patch 只含 `{enabled}` → 直接；含其他字段 → 审批。
- **降级分层**（Constitution #6）：approval_gate 缺失 → 破坏操作 fail-closed 拒绝（不静默执行）；notification/manager 缺失 → 仅降级对应通道，审批主路径继续（同 write_approval DP-5）。

### DP-6 工具直调 store+scheduler，不走自己的 REST

`cron.list` 先例是直读 `AutomationStore`。写工具同样直调 `AutomationStore.save_job/delete_job`，**并**调 `AutomationSchedulerService.sync_job/remove_job`（否则新 job 要等重启才生效）。scheduler 经 ToolDeps late-bind 注入（新增 `ToolDeps._automation_scheduler`，在 `_bootstrap_control_plane` 后补绑，同 `_snapshot_store`/`_notification_service` 模式）。scheduler 不可用（None）→ job 落盘但标记 degraded 提示需重启（不静默丢）。

### DP-7 进 CoreToolSet.default()（F135 强制先例）

`cron.create/update/delete` 必须进 `CoreToolSet.default()`（`packages/tooling/src/octoagent/tooling/models.py:396`）。否则落 Deferred 桶 → 主 Agent 只在 system prompt 见名字无 schema → 须 tool_search 两跳 → 弱 model / 手机单轮场景不可靠（与 `delegate_task`/`behavior.write_file` 同款教训，models.py:407-416 已注释此坑）。`cron.list` 已在 ops_tools 但**未在 Core**——一并补进 Core（用户问「我有哪些定时任务」是高频）。

### DP-8 Web UI 范围：只读列表 + enable/disable toggle（可逆自助面，删除走对话治理）

新增 `GET /api/control/resources/automation` REST resource endpoint（接现存 `get_automation_document`）+ 前端 `AutomationCenter` 页（列表：名称/schedule 人读化/下次运行/状态；行内 toggle 调 `automation.pause|resume`）。

**【Codex P1-3】Web 不放直接删除按钮**。理由：`automation.delete` control-plane action（`automation_service.py:413`）**立即删除**、无审批；从 SYSTEM/WEB surface 触发时没有 task/session 上下文可挂 ApprovalGate（ApprovalGate 依赖 exec_ctx 的 WAITING_APPROVAL 转移，见 write_approval）。若 Web 放直接删除按钮会绕过 DP-5 的 Two-Phase 治理。故：
- Web 只做**可逆** enable/disable（toggle）——禁用等于停止触发，用户随时可恢复，无需审批。
- **删除走对话**：用户在聊天里说「删掉那个提醒」→ 主 Agent 调 `cron.delete`（走 ApprovalGate Two-Phase）。AutomationCenter 页对每行给一句提示「如需删除，在对话中让助手删除」而非删除按钮。

**不做**创建表单（创建走对话让 Agent 建，符合 H1 mediated + 避免 UI 重造 NL 理解）。面向普通用户：schedule 表达式人读化展示（cron→「每天 08:00」近似），技术字段（action_id/job_id）收折叠。

### DP-9 Telegram 命令：本期不加专用命令

任务列「可选 Telegram 命令」。Telegram 侧用户直接**对话**让 Agent 建/删/列（agent 工具已支持，Telegram 是 chat 入口）即可覆盖，不需专用 `/cron` 命令。列表查看用 `cron.list` 工具的对话回复。降范围，避免 Telegram 命令解析面外扩。

---

## 2. 用户故事 + 验收标准（AC↔test 绑定）

### US-1（P1）用户从手机对话让 Agent 建定时提醒
> Connor 在 Telegram 说「每周一早上9点提醒我交周报」。主 Agent 译成 cron 表达式，调 `cron.create(name="交周报提醒", schedule_kind="cron", schedule_expr="0 9 * * 1", reminder_text="交周报")`，回复「已建，每周一 09:00（Asia/Shanghai）提醒你」。

- **AC-1.1** `cron.create` 传 `reminder_text` → 落 `AutomationJob{action_id="reminder.notify", params.message=reminder_text}` + scheduler `sync_job` 被调 + 返回 `CronMutationResult{status="written", job_id}`。`[@test: apps/gateway/tests/tools/test_cron_tools.py::test_create_reminder_job]`
- **AC-1.2** `schedule_expr` 非法 cron（如 `"每天"`）→ `status="rejected", reason` 含解析失败，不落盘。`[@test: ::test_create_invalid_cron_rejected]`
- **AC-1.2b** `schedule_expr` 用纯数字 DOW（如 `0 9 * * 1`）→ `status="rejected", reason` 提示改命名星期（Codex P1-1 off-by-one 防护）。`[@test: ::test_create_numeric_dow_rejected]`
- **AC-1.2c** `cron.create` 传白名单外 `action_id`（如 `update.apply`）→ `status="rejected", reason="action_not_allowed"`，不落盘（Codex P1-2）。`[@test: ::test_create_action_id_not_allowed]`
- **AC-1.3** `timezone` 缺省 → 走 F115 链解析（USER.md `user_timezone` 优先）。`[@test: ::test_create_timezone_fallback_user_md]`
- **AC-1.4** 到点触发 `reminder.notify` → `NotificationService.notify_task_state_change` 被调、message 透传。`[@test: apps/gateway/tests/test_reminder_action.py::test_reminder_notify_delivers]`

### US-2（P1）用户改/暂停定时任务
- **AC-2.1** `cron.update(job_id, enabled=false)` 只含 enabled → 直接执行（无审批）+ scheduler `sync_job`（禁用后 remove）+ `status="written"`。`[@test: ::test_update_toggle_enabled_no_approval]`
- **AC-2.2** `cron.update(job_id, schedule_expr=...)` 含非 enabled 字段 → 走 ApprovalGate；approved 才落盘。`[@test: ::test_update_schedule_requires_approval]`
- **AC-2.3** `cron.update` 目标 job 不存在 → `status="rejected", reason="job_not_found"`。`[@test: ::test_update_missing_job]`

### US-3（P1）用户删定时任务（治理确认）
- **AC-3.1** `cron.delete(job_id)` → 服务端 ApprovalGate 审批；approved → `delete_job` + scheduler `remove_job` + `status="written"`。`[@test: ::test_delete_requires_approval_then_deletes]`
- **AC-3.2** 审批 rejected → job 保留，`status="rejected", reason` 含 user_rejected，对话可继续。`[@test: ::test_delete_rejected_keeps_job]`
- **AC-3.3** approval_gate 缺失 → fail-closed（`status="rejected"`, decision=unavailable），不删。`[@test: ::test_delete_fail_closed_no_gate]`

### US-4（P1）工具进 Core + 可发现
- **AC-4.1** `CoreToolSet.default().tool_names` 含 `cron.create/update/delete/list`。`[@test: packages/tooling/tests/test_models.py::test_cron_tools_in_core]`
- **AC-4.2** `cron.create/update/delete` 经 `register_all` 注册进 broker + ToolRegistry（entrypoints 含 agent_runtime）。`[@test: apps/gateway/tests/tools/test_cron_tools.py::test_cron_tools_registered]`

### US-5（P1）Web 定时任务列表 + 开关 + 删除
- **AC-5.1** `GET /api/control/resources/automation` 返回 `AutomationJobDocument`（含 jobs + next_run_at + status）。`[@test: apps/gateway/tests/test_automation_resource_route.py::test_get_automation_resource]`
- **AC-5.2** 前端 `AutomationCenter` 渲染 job 列表、toggle 调 pause/resume action。**无直接删除按钮**（删除走对话，Codex P1-3）。`[@test: frontend/src/pages/AutomationCenter.test.tsx]`

### US-6（非功能）零回归 + H1
- **AC-6.1** 全量回归 0 regression vs master 1e64ecd3（动到的包）。
- **AC-6.2** 到点提醒经通知系统交付，非主 Agent 对话轮次注入（H1）。`reminder.notify` 不创建 user-facing task、不调 LLM。

---

## 3. FR 清单

- FR-1 `cron.create` 工具：reminder_text 路径 + action_id 路径（白名单 `_CRON_AGENT_ACTION_ALLOWLIST`）二选一；cron/interval/once 校验 + 纯数字 DOW 拒绝（命名星期防 off-by-one）；F115 时区；REVERSIBLE；进 Core。
- FR-2 `cron.update` 工具：enabled-only 直改 / 其他字段走审批；scheduler sync；进 Core。
- FR-3 `cron.delete` 工具：ApprovalGate Two-Phase；fail-closed；scheduler remove；进 Core。
- FR-4 `reminder.notify` action：NotificationService 交付 message；注册进 action_registry + 校验白名单；不违 H1。
- FR-5 `ToolDeps._automation_scheduler` late-bind + `_bootstrap_control_plane` 后补绑。
- FR-6 F115 时区解析抽共享 helper（USER.md > env > UTC），cron 工具与 daily_routine 共用。
- FR-7 `GET /api/control/resources/automation` REST resource route + 前端契约条目。
- FR-8 前端 `AutomationCenter` 页（列表 + toggle + schedule 人读化，**无删除按钮**——删除走对话治理）+ 路由挂载。
- FR-9 `CronMutationResult(WriteResult)` 回显契约（status/job_id/reason/approval_requested）。
- FR-10 审计事件：cron job 建/改/删 emit 事件（复用 MEMORY_ENTRY_ADDED 模式或新 `AUTOMATION_JOB_MUTATED`）。

## 4. 非目标（明确排除）

- ❌ 到点自动跑 LLM agent turn 生成动态内容（「现算总结昨天」）——F102 DailyRoutine 已覆盖固定日报；动态属 XL 留后续。
- ❌ Web 创建表单（创建走对话 mediated）。
- ❌ Telegram 专用 `/cron` 命令（对话即可）。
- ❌ OpenClaw 的 on-exit / webhook delivery / 复杂 delivery 路由 / creatorToolAllowlist capping。
- ❌ NL 解析规则引擎（LLM 自译，Constitution #9）。

## 5. Codex/Opus 双评审重点
- H1：reminder.notify 是否真的不构成"主 Agent 偷偷说话"？（通知 vs 对话轮次边界）
- 治理：cron.delete/update 审批是否真 fail-closed？enabled-only 直改是否被滥用绕审批（如同一 update 混 enabled+schedule）？
- scheduler sync 时序：工具落盘与 scheduler.sync_job 之间崩溃 → job 落盘但没调度（重启自愈？degraded 标记？）。
- Constitution #9：schedule 校验是否退化成规则引擎？
