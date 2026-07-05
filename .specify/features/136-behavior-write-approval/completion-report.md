# F136 completion-report — behavior.write_file 服务端审批绑定

> 安全 fix Feature（L）。base master `662df4a7`（含 F135）。**未 push origin，等用户拍板。**
> 关闭 F135 Codex P1 HIGH：`behavior.write_file` 的 `confirmed` 是 LLM 自填参数，一轮自确认绕过人审。

## 1. 缺陷与修法（一句话）

REVIEW_REQUIRED behavior 文件的"用户确认"从 **LLM 自证布尔参数** 升级为 **服务端 ApprovalGate
证据**：`confirmed=true` 不再直接落盘，而是发起服务端审批，用户在 Web/Telegram 审批卡片
真实批准后才写入。聊天里的"好的你写吧"仍是 LLM 发起写入的信号，但**落盘依据是服务端持有的
APPROVAL_DECIDED，不再是 confirmed 参数**。

## 2. GATE_DESIGN 决策（自治会话按最强理由选定，替代项供用户拍板）

| DP | 决策 | 关键理由 / 可推翻项 |
|----|------|--------------------|
| DP-1 | 方向①接 ApprovalGate（**否决**②proposal token）| token 经 tool result 流回 LLM，只能证明"proposal 发生过"，**证明不了"用户确认过"**——被注入的 LLM proposal→回传两连击仍零人审落盘。①复用 F099/F101 生产验证的全套机械，新增状态为零。**可推翻**：若坚持不阻塞工具调用，需接受安全性打折 |
| DP-2 | 保留两段调用；proposal 步字节级不变，confirmed=true 从自证降级为发起审批 | 引导流 UX 不破坏、工具 schema 不变（prefix-cache 不动） |
| DP-3 | 每次写独立审批，**不入 session allowlist / allow-always** | allowlist/白名单以 tool_name 为粒度，无法区分内容——一次批准=后续任意内容静默改写，缝重开 |
| DP-4 | 显式拒绝**恢复 RUNNING**（与 escalate 刻意差异）；超时不恢复 | 写入被否决≠任务失败，对话须继续；超时归 task_runner 终态 owner（F101 HIGH-02 v3）|
| DP-5 | gate 缺失 fail-closed 拒写；manager/notification 缺失仅降级对应通道 | Constitution #6：降级=功能不可用，不是安全绕过 |
| DP-6 | 审批卡片带 unified diff（批的是具体修改）；版本 baseline 批准后重读 | 阻塞模型下内容在服务端闭包持有，批准后 LLM 无法换内容（无 TOCTOU）|

## 3. 改动清单

| 文件 | 改动 | commit |
|------|------|--------|
| `builtin_tools/write_approval.py`（新）| 服务端审批门 helper（镜像 escalate_permission 范式）| b80ae567 |
| `builtin_tools/misc_tools.py` | REVIEW_REQUIRED+confirmed=true 接 gate_behavior_write | b80ae567 |
| `core/models/tool_results.py` | BehaviorWriteFileResult 加 approval_id | b80ae567 |
| `services/agent_decision.py` | 教学文本：confirmed=true 触发审批卡片 | b80ae567 |
| `policy/models.py` | ApprovalRequest 加 allow_always_eligible（默认 True）| 26901eab |
| `policy/approval_manager.py` | register/resolve 尊重 allow_always_eligible | 26901eab |
| `apps/gateway/tests/test_f136_write_approval.py`（新）| 11 case | b80ae567 + 26901eab |
| `test_f135 / test_behavior_write_golden` | auto-approve 替身适配 | b80ae567 |
| `docs/blueprint/milestones.md` / `harness-and-context.md` | living-docs 漂移闸 | b80ae567 |

生产净改动集中：1 新 helper + handler 一处接线 + policy 3 行条件 + 1 向后兼容字段 + 文案。
control_plane 路径 / user_profile.* / NONE 直写 / proposal 步字节级行为**未动**。

## 4. AC ↔ test 绑定（SDD 强化，全部 PASS）

| AC | test | 验证 |
|----|------|------|
| AC-1 | test_first_call_confirmed_true_gated_until_approval / test_reject_leaves_file_untouched | 首调 confirmed=true 不落盘直到 approve；reject 文件不存在 |
| AC-2 | test_approved_write_lands_with_version_and_events | approve→written+approval_id+F107 版本+APPROVAL_* 事件+CRITICAL 通知+risk_explanation 含 diff |
| AC-3 | test_reject_restores_running | 显式拒绝恢复 RUNNING（对话继续）|
| AC-4 | test_timeout_rejects_without_running_restore | 超时→APPROVAL_TIMEOUT 不落盘、不恢复 RUNNING |
| AC-5 | test_gate_unavailable_fail_closed | gate=None fail-closed 拒写 |
| AC-6 | test_review_mode_none_writes_without_gate | NONE 直写不弹审批 |
| AC-7 | test_each_confirmed_write_requires_fresh_approval | 两次 confirmed 写=两次独立审批 |
| AC-8 | test_proposal_step_does_not_consult_gate + golden 不改断言绿 | proposal 步不消费 gate、不触盘 |
| AC-9 | test_approval_manager_dual_registration | 双注册 approval_id=handle_id + allow_always_eligible=False |
| AC-11 | test_allow_always_does_not_shortcircuit_next_write | **Codex P2**：真 ApprovalManager，allow-always 后第二次仍独立审批（不 404 短路）|

## 5. 回归 + 双评审闭环

**回归**（PYTHONPATH 锁 worktree + `uv run --no-sync python -m pytest`，禁 uv sync）：
- 全量 `-m "not e2e_live"`：**4669 passed** / 11 skipped / 1 xfailed / 1 xpassed。
- **唯一 failed = `test_plugin_watcher::test_start_degrades_without_watchdog`**：pre-existing
  环境假失败（worktree `.venv` symlink 主仓已装 watchdog → 断言"未装→start()=False"必挂），
  `git diff master` 该文件为空（未触碰），与 F135 §5 同源，F106 测试 hygiene（缺
  `skipif(watchdog installed)`）。**F136 归因回归=0。**
- pre-commit e2e_smoke 每次 commit 8/8 PASS（3 次 commit 均绿）。

**双评审 panel**：
- **Opus 席**（spec 对齐 + 对抗）：**0 HIGH**。4 low：F-1 operator 保留值契约（已采纳，
  write_approval.py 加注释）/ F-2 decision 四态 fail-closed（设计如此）/ F-3 显式拒绝恢复
  RUNNING 竞态（经 CAS status-guard 证安全，与 F101 一致）/ F-4 文档删除线（审计痕迹保留）。
  变异测试验证：删 gate 调用 → 4 安全测试全红（测试非 vacuous）。
- **Codex 席**（`codex review --base master`）：2 finding **全闭环（commit 26901eab）**：
  - **P1（HIGH 级）审批卡片不展示 diff**：diff 只进 ApprovalGate 的 diff_content 字段，但前端
    渲染（approval.ts）+ OperatorInbox + Telegram 全读 `risk_explanation`（实测 approval.ts:176
    `risk_explanation||summary`、:87 `approval.risk_explanation`），不读 diff_content → 内容级
    审批看不到实际变更。**修**：diff（截断 1500，留 Telegram 4096 余量）拼进 risk_explanation。
  - **P2（MED 级）allow-always 破坏后续独立审批**：前端每卡片硬编码"总是批准"（approval.ts:96）
    + Telegram APPROVE_ALWAYS（operator_actions.py:236）→ 用户点 → ApprovalManager 记全局白名单
    → 下次 register 短路返回 APPROVED 但不入 pending → Web/Telegram resolve 404 → 写入超时。
    **修**：ApprovalRequest 加 allow_always_eligible（behavior.write_file 传 False），register
    不短路、resolve 时 allow-always 降级一次性批准。

**re-review**（P1/P2 修复 commit 后二次 Codex，符合 CLAUDE.local.md 强制）：见 §6。

## 6. re-review 结果

`codex review --base master`（含 P1/P2 修复 commit 26901eab）：**0 新 finding**——
"未发现需要作者修复的新增正确性问题，相关 F136 / F135 / behavior write golden 测试已通过"。
双评审收敛：Opus 0 HIGH + Codex 2 finding 全闭环 + re-review 0 新增 = **0 HIGH 残留**。

## 7. 已知 limitations（living-docs 漂移闸）

- **escalate_permission 同款 allow-always 404 隐患**：~~pre-existing 未修~~ **用户拍板并入
  本次已修**（escalate register 传 allow_always_eligible=False + 真 ApprovalManager 短路回归
  测试 `test_escalate_permission_allow_always_does_not_shortcircuit`）。语义变化：用户对
  escalate 点"总是批准"降级为本次批准（gate 侧本就无 allow-always 语义，此前点了反而导致
  后续全部假超时——严格变好）。若未来要真 auto-approve 语义，需给 ApprovalGate 实现
  allow-always（独立设计决策，Constitution #7 需权衡）。
- **user_profile.update add 路径无人审（F084 既定设计）**：ThreatScanner 门控直写，增量画像
  观察不弹卡片；replace/remove 自 F084 起 fail-closed。若未来要统一 behavior 与 profile 的
  写治理，是独立权限模型决策。
- **control_plane behavior.write_file / restore_version action**：actor 是用户本人（Web 行为
  工作台），confirmed 是 UI 流程标记非信任边界，未纳入（spec §2.2）。
- **ApprovalGate CancelledError pending 泄漏 / allow-always 对 gate 二态无真实语义**：F101 层
  既有 limitation，escalate 同样暴露，未在 F136 修。
- **diff 在 Web 审批面板可能偏长**：risk_explanation 含 ≤1500 字符 diff，Web 卡片可滚动、
  Telegram 在 4096 内；若未来要结构化渲染，diff_content 字段已备（前端接入即可）。

## 8. 用户上手：修复合入后真机验证

**前置**：合入 master 后 `octo update` 重启托管实例（让新审批门生效）。

**验证绕过关闭（安全）**：让 Agent"把时区 Asia/Shanghai 写进 USER.md"。预期：Agent 先出
proposal（聊天摘要）→ 你说同意 → **Web/Telegram 弹审批卡片（含内容 diff）** → 你点批准 →
落盘 + F107 版本记录。**关键**：不点批准（或点拒绝）→ 文件不变、对话继续。即便 Agent 直接
`confirmed=true`（跳过 proposal），也照样弹卡片，不会静默写。

**验证 allow-always 不破坏**：对一次 USER.md 写点"总是批准" → 这次写成功 → 下次再改 USER.md
仍会弹卡片（不会因"总是批准"变成静默写，也不会 404 超时）。
