# Feature Specification: F136 behavior.write_file 服务端审批绑定（关闭 LLM 一轮自确认绕过）

> 安全 fix Feature（L）。base master `662df4a7`（含 F135）。
> 触发：F135 Codex review P1 HIGH + 经验复现双重确认（`scratchpad/repro_p1.py`：首调
> `behavior.write_file(confirmed=true)` 无任何前置 proposal 直接写入 REVIEW_REQUIRED 的 USER.md）。
> M6 `CLAUDE.local.md`"待核查安全项"既定策略：确认即立独立 fix Feature。
> Constitution #4（Two-Phase）/ #7（User-in-Control）/ #10（Policy-Driven Access）核心。

## 0. 设计基础说明（实测核实，master HEAD 662df4a7）

- **缺陷现场**：`misc_tools.behavior_write_file`（`apps/gateway/.../builtin_tools/misc_tools.py:191-346`）。
  `confirmed: bool = False` 是 **LLM 自填执行参数**；`review_mode==REVIEW_REQUIRED and not confirmed`
  仅做一次分支返回 proposal，服务端**不校验**"是否真有过 proposal + 用户确认"。LLM（或被
  prompt injection 的 LLM）下一轮自置 `confirmed=true` 即绕过人审。
- **pre-existing**：handler 自 F084/F107 起即如此。F135 把工具从 Deferred 提进 CoreToolSet
  只改发现层（两跳→一跳），**放大暴露面但未创造该缝**——两跳链路从来不是安全边界。
- **受影响文件面**：`get_behavior_file_review_modes(include_advanced=True)` 全部 9 个 behavior 文件
  （AGENTS/USER/PROJECT/KNOWLEDGE/TOOLS/BOOTSTRAP + SOUL/IDENTITY/HEARTBEAT）都是
  REVIEW_REQUIRED（`template.py:36-141`）；未知 file_id 在 `prepare_behavior_file_write` 即
  ValueError 拒绝，到不了审批层。`BehaviorReviewMode` 仅 {NONE, REVIEW_REQUIRED}，NONE
  （auto-apply）当前无文件使用但代码路径保留。
- **现成基础设施（全部生产可用，F084/F099/F101 已建）**：
  - `ApprovalGate`（`harness/approval_gate.py`）：request_approval（写 APPROVAL_REQUESTED 事件 +
    SSE 推审批卡片，支持 `diff_content`）/ wait_for_decision（300s 超时→"rejected"）/
    resolve_approval（写 APPROVAL_DECIDED）。生产实例 `octo_harness.py:1032`，经
    `capability_pack.bind_approval_gate` 注入 `ToolDeps._approval_gate`。
  - **双 resolve 通道**：Web `POST /api/approve/{approval_id}`（`routes/approvals.py:78`，先
    `ApprovalManager.resolve` 后 `ApprovalGate.resolve_approval`——**必须双注册 ApprovalManager，
    否则 Web resolve 404**）+ Telegram（`operator_actions.py` 持 gate 直接双 resolve）。
  - **工具内阻塞审批唯一先例**：`worker.escalate_permission`（`ask_back_tools.py:388-648`）：
    request_approval → ApprovalManager.register → mark_waiting_approval → notify_approval_request
    (CRITICAL，豁免 quiet hours) → wait_for_decision(300s) → 按 decision 恢复 RUNNING。
  - **WAITING_APPROVAL 状态机**（F101）：`execution_console.mark_waiting_approval /
    mark_running_from_waiting_approval` 均 status-guarded 幂等；task_runner 是终态唯一 owner
    （approval_timeout 300s 后推 FAILED + startup recovery HIGH-04）。
- **相邻但范围外的 USER.md 写路径**（诚实划界）：`user_profile.update`（F084）add 路径 =
  ThreatScanner 门控直写（无人审，F084 既定设计——增量画像观察不弹卡片）；replace/remove
  自 F084 起 fail-closed 返回 `approval_pending`（Phase 3 从未接线）。本 Feature 不动
  user_profile.*；见 §2.2。
- **control_plane 侧 `behavior.write_file` action**（`worker_service.py:563`）调用方是 Web UI
  行为工作台（**用户本人**是 actor），confirmed 语义在该路径是 UI 流程标记而非信任边界，不在
  本 Feature 范围。

## 1. 目标（Why）

把 REVIEW_REQUIRED behavior 文件写入的"用户确认"从 **LLM 自证参数** 升级为 **服务端证据**：
只有用户在 Web 审批卡片 / Telegram 真实批准后，服务端才允许落盘。聊天里的"好的你写吧"仍是
LLM 发起写入的对话信号，但**不再是落盘依据**——落盘依据是服务端持有的 APPROVAL_DECIDED。

## 2. 范围声明

### 2.1 In Scope

- `misc_tools.behavior_write_file`（LLM 工具入口）REVIEW_REQUIRED 分支接 ApprovalGate。
- 新 helper 模块 `builtin_tools/write_approval.py`（gate 交互全封装，供未来
  `user_profile.replace/remove` 接线复用——见 handoff）。
- `BehaviorWriteFileResult` 增 `approval_id` 字段（审计关联，默认 ""）。
- 教学文本同步：`agent_decision.py:801-804` + handler proposal 文案 + 工具 docstring。
- 测试：新 `test_f136_write_approval.py` + 适配既有 golden / F135 测试。
- living-docs：`harness-and-context.md`（ApprovalGate 章节 + behavior 治理描述）。

### 2.2 Out of Scope（显式排除，带理由）

- **`user_profile.update` add 路径**：ThreatScanner 门控直写是 F084 既定分层（增量观察 vs 全文
  改写风险不同级）；replace/remove 已 fail-closed。改它是另一个权限模型决策，独立评估。
- **control_plane `behavior.write_file` / `behavior_restore_version` action**：actor 是用户本人
  （Web UI），无 LLM 自证问题。
- **ApprovalGate 自身缺陷**（wait_for_decision 被 asyncio.CancelledError 打断时 pending handle
  泄漏；审批卡片 allow-always 对 gate 类审批实际等效 allow-once）：escalate_permission 同样暴露，
  属 F101 层既有 limitation，记 handoff 不在本 Feature 修。
- **e2e_live 新域**：本 Feature 交付 service 层集成测试（真 ApprovalGate + resolver 协程）；
  e2e_live 域按项目惯例归 F119 式 backfill。

## 3. 关键决策点（GATE_DESIGN 决策记录）

> 会话为自治模式（用户不在线）：以下决策按最强理由选定并**全部实施**；归总报告列出替代项
> 供用户拍板推翻。改判 D1 需要重做实施；改判 D2-D4 是小 delta。

### DP-1 ★ 方向 = ①接 ApprovalGate（选定）；②proposal 令牌（否决）

②的致命缺陷：令牌必须经 tool result 返回给 LLM 才可能被回传 → 令牌只能证明"proposal 步骤
发生过"，**不能证明"用户确认过"**。被注入的 LLM 两连调（proposal 拿 token → 立即回传）仍然
零人审落盘——换汤不换药，不满足"confirmed 绑定服务端证据"的核心要求。令牌若不给 LLM（外带
展示给用户、要求用户转达）= 体验更差的 ①。②唯一优势（不阻塞工具调用）不足以抵消根本缺陷。
①复用 F099/F101 已生产验证的全套机械（卡片/SSE/Telegram/状态机/startup recovery），新增
状态为零。

### DP-2 调用形态 = 保留两段调用；confirmed 从"自证"降级为"发起服务端审批"

- `confirmed=false` → proposal（**行为不变**：快速返回、不触盘，LLM 在聊天里叙述提议——
  引导流 UX 不破坏，工具 schema 不变，prefix-cache 不动）。
- `confirmed=true` + REVIEW_REQUIRED → 服务端 `request_approval`（含 unified diff）→
  task 转 WAITING_APPROVAL → 用户在 Web 卡片 / Telegram 批准 → 落盘；拒绝/超时 → 不落盘。
- `review_mode==NONE` → 直写（不变）。
- **不要求"必须先调过 proposal"的时序状态**：审批卡片本身展示最终内容 diff，时序绑定不增加
  安全性、只增加服务端状态与失败模式。

### DP-3 审批作用域 = 每次写独立审批，绝不参与 session allowlist

handler **不得**调 `check_allowlist`；每个 confirmed 写各自 request_approval。理由：allowlist
以 operation_type 为粒度，无法区分内容——一次批准 = 本 session 后续任意内容静默改写，缝重开。
（现状护栏：Web resolve 路径 `_session_id_for_gate=""` 使 gate 侧 allowlist 本就不会被填充；
本决策把"不查"固化为 handler 契约。）

### DP-4 决策语义（与 escalate_permission 的一处刻意差异）

| decision | task 状态 | 工具返回 |
|---|---|---|
| approved | 恢复 RUNNING → 落盘 | `written`（含 approval_id）|
| 用户显式拒绝 | **恢复 RUNNING**（差异点）| `rejected`（APPROVAL_REJECTED），对话继续 |
| 超时（operator=system_timeout）| **不恢复**（镜像 escalate/F101 HIGH-02 v3，task_runner 是终态唯一 owner）| `rejected`（APPROVAL_TIMEOUT）|
| gate 不可用 | 不涉及 | `rejected`（APPROVAL_UNAVAILABLE，fail-closed）|

差异点理由：escalate 的"rejected"= 任务核心动作被禁止 → 走向 FAILED 合理；behavior 写入的
"拒绝"= 用户否决**一次写入**，对话必须能继续（"好的，不改了"）。恢复转移是 status-guarded
幂等（`execution_console.py:408-418`），显式拒绝通常远早于 300s monitor 阈值，无竞态扩大。

### DP-5 降级分层（Constitution #6：降级 = 功能不可用，不是安全绕过）

- `_approval_gate` 缺失 → **fail-closed** rejected（不写）。
- `_approval_manager` 缺失 → 仅失去 Web resolve/列表通道，gate 路径仍可用（log warning，
  与 escalate 同）。
- `_notification_service` 缺失 / 通知失败 → 仅失去推送，卡片仍在（log debug）。
- `mark_waiting_approval` 失败 → best-effort 继续等审批（与 escalate 同）。

### DP-6 审批证据 = 具体内容 diff，非抽象权限

`request_approval(diff_content=...)` 传 `difflib.unified_diff(旧盘内容, 新内容)`（截断
`_DIFF_MAX_CHARS=4000`）。用户批准的是"这份具体修改"。阻塞模型下内容在服务端闭包里持有，
**批准后 LLM 无法换内容**（无 token/TOCTOU 面）。diff 用写前快照；版本记录 baseline 在批准后
**重读磁盘**（审批等待期间若有并发写，F107 版本 baseline 不失真）。

## 4. User Scenarios（P1）

### US-1 绕过关闭（安全主场景）

被注入/失准的 LLM 首调 `behavior.write_file(file_id="USER.md", content=恶意内容,
confirmed=true)`（无任何前置 proposal）。**预期**：不落盘；审批卡片出现（含 diff）；用户拒绝
→ 工具返回 rejected、文件保持原状、APPROVAL_REQUESTED/DECIDED 事件链可查；对话不中断。

### US-2 正常引导流（UX 主场景）

用户："把我时区 Asia/Shanghai 写进 USER.md"。Agent 调 proposal（confirmed=false）→ 聊天里
展示摘要 → 用户口头同意 → Agent 调 confirmed=true → **Web/Telegram 弹审批卡片（含 diff）**
→ 用户一键批准 → 落盘 + F107 版本记录 → Agent 回复完成。与 F135 前唯一 UX 差异：确认动作从
"聊天里说好"变为"卡片上点批准"（聊天同意仍自然发生，但落盘依据是卡片点击）。

### US-3 用户不在场

Agent 在用户离开时尝试 confirmed 写 → CRITICAL 通知推送（豁免 quiet hours）→ 300s 无人批
→ 超时 rejected 不落盘；task 交由 task_runner 终态治理（F101 既定语义）。

## 5. FR（功能需求）

- **FR-1** REVIEW_REQUIRED + confirmed=true 必须经服务端 ApprovalGate approved 才落盘；
  首调 confirmed=true（无前置 proposal）同样被卡。
- **FR-2** proposal 步（confirmed=false）行为字节级不变（status=skipped/proposal=True/不触盘），
  仅 preview 文案更新为新语义。
- **FR-3** review_mode NONE 直写不变；未知 file_id / 预算超限仍在审批之前拒绝（不为无效内容
  弹卡片）。
- **FR-4** 决策语义按 DP-4；rejected/timeout 一律不写盘、返回 `BehaviorWriteFileResult`
  status="rejected"（reason 前缀 APPROVAL_REJECTED / APPROVAL_TIMEOUT / APPROVAL_UNAVAILABLE）。
- **FR-5** 双注册 ApprovalManager（approval_id=handle_id，expires_at=+300s，
  side_effect_level=REVERSIBLE）+ notify_approval_request(CRITICAL) + mark_waiting_approval /
  按 DP-4 恢复——镜像 escalate 生产范式，各依赖缺失按 DP-5 分层降级。
- **FR-6** 审批请求含 operation_summary（file_id/字符数/预算/目标路径）+ unified diff
  （≤4000 chars）；APPROVAL_REQUESTED/APPROVAL_DECIDED 事件由 gate 既有路径写入，
  `behavior_file_written` structlog 增 approval_id 字段；结果模型增 `approval_id`。
- **FR-7** 每次 confirmed 写独立审批：handler 不查/不填 session allowlist（DP-3）。
- **FR-8** 教学文本一致性：`agent_decision.py` 工具指引、handler docstring、proposal preview
  三处更新为"confirmed=true 触发服务端审批卡片，用户批准后才写入"。
- **FR-9** F107 版本记录照常：approved 落盘后 record_behavior_version，old_content 于批准后
  重读（DP-6）；BOOTSTRAP marker / pack cache invalidate 等既有副作用顺序不变。

## 6. AC ↔ test 显式绑定（SDD 强化）

新测试文件 `apps/gateway/tests/test_f136_write_approval.py`：

| AC | 内容 | test |
|----|------|------|
| AC-1 | US-1 绕过关闭：首调 confirmed=true 不落盘，直到 approve 才写；reject 则文件不存在 | `::test_first_call_confirmed_true_gated_until_approval` + `::test_reject_leaves_file_untouched` |
| AC-2 | US-2 全流程：真 ApprovalGate + resolver 协程 approve → written + approval_id + F107 版本记录 + APPROVAL_REQUESTED/DECIDED 事件落 event_store | `::test_approved_write_lands_with_version_and_events` |
| AC-3 | 显式拒绝恢复 RUNNING（对话可继续），返回 APPROVAL_REJECTED | `::test_reject_restores_running` |
| AC-4 | 超时→APPROVAL_TIMEOUT 不落盘、不恢复 RUNNING（terminal ownership 归 task_runner） | `::test_timeout_rejects_without_running_restore` |
| AC-5 | gate=None fail-closed：REVIEW_REQUIRED+confirmed=true → APPROVAL_UNAVAILABLE 不写 | `::test_gate_unavailable_fail_closed` |
| AC-6 | NONE 直写不弹审批（monkeypatch review_modes） | `::test_review_mode_none_writes_without_gate` |
| AC-7 | 两次 confirmed 写 = 两次独立 request_approval（无 allowlist 短路） | `::test_each_confirmed_write_requires_fresh_approval` |
| AC-8 | proposal 步不变（不触盘/skipped/proposal=True） | 既有 `test_f135::test_behavior_write_review_required_still_two_phase` + golden `test_tool_write_proposal_gate_golden` 不改断言仍绿 |
| AC-9 | ApprovalManager 双注册发生（approval_id=handle_id）；manager 缺失仅降级不阻塞 | `::test_approval_manager_dual_registration` |
| AC-10 | 既有 confirmed=true 测试适配 auto-approve gate 后全绿；全量回归 0 regression vs 662df4a7 | golden 5 处 + F135 AC-1.4 适配；全量 pytest |

## 7. Edge cases（已推演）

- **并发两笔 confirmed 写同文件**：各自独立卡片、独立批；后批覆盖先批（两版都进版本历史）——
  与 baseline 无锁语义一致，不新增风险。
- **审批等待期间磁盘被并发改**：卡片 diff 用请求时快照；版本 baseline 批准后重读（DP-6）。
- **进程重启 mid-wait**：F101 HIGH-04 startup recovery expire ApprovalManager entry；写未发生
  → 安全侧正确（fail = 不写）。
- **卡片点 allow-always**：对本 gate 等效 allow-once（handler 不查 override/allowlist），与
  escalate 现状一致，记 handoff limitation。
- **worker/subagent 调用**：路径按 agent_slug/project_slug 解析到各自 scope 文件，REVIEW_REQUIRED
  同样弹卡片——行为文件治理不因调用方身份放松（H2 对等性）。
- **execution context 缺失**：baseline 已在 handler 早段 `get_current_execution_context()`
  raise（agent_runtime 入口必有 ctx），不变。

## 8. 全局约束

- PYTHONPATH 锁 worktree + `uv run --no-sync python -m pytest`，禁 `uv sync`。
- 全量回归 0 regression vs master `662df4a7`；e2e_smoke 8/8（pre-commit hook）。
- Codex（CLI 同步 `codex review --base` scoped diff）+ Opus 双评审，high/medium 全闭环，
  分歧列人裁清单。
- 不主动 push；completion-report + handoff + living-docs 漂移闸。
