# F136 handoff — 给后续 Feature

> base master `662df4a7`。F136 把 `behavior.write_file` 的 confirmed 绑定服务端 ApprovalGate。

## 可复用件（下一个碰"工具内阻塞审批"的 Feature 直接拿）

1. **`builtin_tools/write_approval.gate_behavior_write`（服务端审批门通用范式）**
   - 封装了完整 escalate 生产范式：request_approval → ApprovalManager 双注册 →
     mark_waiting_approval → notify CRITICAL → wait_for_decision → 按 decision 条件恢复 RUNNING。
   - **user_profile.replace / remove 接线直接改用它**：F084 起这两个 op fail-closed 返回
     `approval_pending`（Phase 3 从未接线），正好用本 helper 补上真审批。传入 file=USER.md、
     old_content=当前 USER.md、new_content=替换后全文即可，其余机械一致。
   - 返回 `WriteApprovalOutcome{decision, approval_id, reason}`，调用方仅在 `decision=="approved"`
     时落盘，其余一律 fail-closed 不写。

2. **`ApprovalRequest.allow_always_eligible`（per-request 禁用 allow-always）**
   - policy/models.py 新增字段，默认 True（现有工具零影响）。传 False → 该工具每次调用独立审批，
     用户点"总是批准"降级为一次性批准、不写全局白名单/override。
   - register（approval_manager.py:132）+ resolve（:305）两处已尊重它。任何"每次内容不同、
     必须每次审批"的写工具都应传 False。

3. **测试范式**（test_f136_write_approval.py）：真 ApprovalGate + resolver 协程（轮询
   `_pending_handles` 出现后 resolve）+ Fake console 记录 WAITING_APPROVAL 转移 + 真
   ApprovalManager 验证 allow-always 语义。gather(handler_call, resolver) 并发驱动阻塞审批。

## 坑（按命中率排序）

1. **审批渲染渠道读 `risk_explanation`，不读 `diff_content`（P1 教训，最坑）**：
   前端 approval.ts（`readLatestApprovalContext`:176 / `buildSyntheticApprovalItem`:87）+
   OperatorInbox + Telegram 全从 `risk_explanation` 取展示文本。ApprovalGate 的
   `diff_content` 结构化字段**当前无前端消费者**。所以要让用户看到内容，必须把内容拼进
   risk_explanation。别只传 diff_content 就以为用户看得到（F136 首版就踩了这个，Codex P1）。

2. **allow-always 全局白名单短路（P2 教训）**：ApprovalManager.register 命中 allow-always 会
   **返回 APPROVED record 但不入 `_pending`、不写 APPROVAL_REQUESTED 事件、不推 SSE**。若你的
   工具依赖"注册后 Web resolve"（routes/approvals.py 先查 ApprovalManager），短路会让 resolve
   404 → wait 满超时。前端**每个审批卡片硬编码"总是批准"按钮**（approval.ts:96），Telegram
   有 APPROVE_ALWAYS（operator_actions.py:236）——用户一定点得到。要么传
   `allow_always_eligible=False`，要么实现 gate 侧真 allow-always 语义。

3. **escalate_permission 有同款 404 隐患（未修）**：escalate 也 register ApprovalManager 不传
   `allow_always_eligible=False` → 用户对 escalate 选"总是批准"同样短路 404 超时。F136 范围内
   未动（escalate 语义上 allow-always 可辩护）。**若要修**：escalate register 处传
   `allow_always_eligible=False`（一行），或给 ApprovalGate 实现 allow-always 语义。

4. **ApprovalGate 二态 vs ApprovalManager 三态**：ApprovalGate.resolve_approval 只有
   approved/rejected；allow-always 是 ApprovalManager 概念，由 Web/Telegram 路由把
   body.decision=allow-always 映射成 gate "approved"。桥接两套系统时注意 decision 语义转换
   （routes/approvals.py:134、operator_actions.py:276）。

5. **worktree `.venv` symlink 主仓 → plugin_watcher 假失败**：`test_start_degrades_without_watchdog`
   在 worktree 跑必挂（.venv 已装 watchdog，测试缺 skipif 守卫）。这是 F106 测试 hygiene，
   与你的改动无关——`git diff master` 该文件为空即可确认非回归。

## 决策语义速查（DP-4，与 escalate 的差异）

| decision | task 状态 | 工具返回 |
|---|---|---|
| approved | 恢复 RUNNING → 落盘 | written（含 approval_id）|
| 显式拒绝 | **恢复 RUNNING**（写工具差异：否决一次≠任务失败，对话继续）| rejected(APPROVAL_REJECTED) |
| 超时 | 不恢复（task_runner 终态 owner，F101 HIGH-02 v3）| rejected(APPROVAL_TIMEOUT) |
| gate 不可用 | — | rejected(APPROVAL_UNAVAILABLE，fail-closed) |

超时判定：`handle.operator == "system_timeout"`（ApprovalGate 超时分支唯一写入源，resolve 端
传 user:web/actor_id 绝不撞值）。
