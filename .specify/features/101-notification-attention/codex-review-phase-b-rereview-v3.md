# F101 Per-Phase B Codex Re-Re-Review (v3)

> Reviewer: Codex GPT-5.4 high
> Date: 2026-05-17
> Phase: B v3（5 finding 修复后）
> Baseline: 3eba8a7 (Phase A)

## Summary

- HIGH: 3
- MEDIUM: 2 known + 6 CANNOT_VERIFY legacy
- LOW: 1 CANNOT_VERIFY legacy
- 总体评估：FIX_HIGH_FIRST

## v3 5 finding 闭环验证

### HIGH-01 闭环验证

`ask_back_tools.py:425-431` 创建 `ApprovalGate` handle，`ask_back_tools.py:446-457` 用 `handle.handle_id` 构造 `ApprovalRequest.approval_id` 并调用 `approval_manager.register()`。`ApprovalRequest.approval_id` 在 `policy/models.py:207-210` 只是 `str`，`ApprovalManager` 也用字符串 key 存在 `_pending`（`approval_manager.py:91-130`），所以 ULID handle ID 与 ApprovalManager ID 体系兼容。Web/Telegram resolve 均先走 ApprovalManager 再尝试唤醒 Gate（`routes/approvals.py:97-155`，`operator_actions.py:250-290`）。

状态: ✅ CLOSED（原"ApprovalManager 不知道 approval_id"已闭环；timeout 不一致另列 NEW-HIGH-01）

### HIGH-02 闭环验证

`ApprovalGate.wait_for_decision()` 超时捕获 `asyncio.TimeoutError` 后返回 `"rejected"`，不抛 TimeoutError（`approval_gate.py:281-320`）。`finally` 只在 `decision == "approved"` 时恢复 RUNNING（`ask_back_tools.py:496-530`），所以"rejected/timeout 被恢复 RUNNING"的原缺陷已修掉。

但仍未保证 task_runner 是唯一终态 owner：如果 `wait_for_decision()` 的 300s timeout 先于 monitor tick 返回，`_run_job()` 会看到 task 仍是 `WAITING_APPROVAL`，执行 `mark_deferred()` 后 return（`task_runner.py:935-938`），done callback 会把 task 从 `_running_jobs` 移除（`task_runner.py:704-707`）；monitor 只扫描 `_running_jobs`（`task_runner.py:998-1008`）。该 interleaving 会留下 `WAITING_APPROVAL` job，无后续超时 owner。

状态: ❌ PARTIAL
修复方向：不要让 `wait_for_decision` 自己在 300s 返回，或 timeout 返回时同步 CAS 到终态；另一种做法是 monitor 扫 `task_jobs.WAITING_APPROVAL`，不要依赖 `_running_jobs`。

### HIGH-04 闭环验证

`_get_approval_requested_created_at()` 查询 `EventType.APPROVAL_REQUESTED`，取最新 `task_seq`，并把 naive `ts` 归一到 UTC（`task_runner.py:396-421`）。startup recovery 扫 `WAITING_APPROVAL` job：无事件 fallback 失败，未超时注册 placeholder monitor，已超时推 `timeout_after_<sec>s`（`task_runner.py:441-528`）。这部分符合 v3 摘要。

残留问题是恢复后的 approval 没有 live `ApprovalGate._pending_handles`（`approval_gate.py:111-112`），startup 只塞了 placeholder sleep task（`task_runner.py:503-508`），没有重建 handle 或恢复 worker。ApprovalManager 会从事件恢复 pending approval（`approval_manager.py:493-559`）；用户此时 approve 会让 `approval_manager.resolve()` 成功，但 `approval_gate.resolve_approval()` 找不到 handle 只返回 False，调用方忽略返回值并仍返回成功（`routes/approvals.py:148-170`，`operator_actions.py:283-290`）。最终 task 不能恢复执行，只会被 monitor 推 FAILED。

状态: ❌ PARTIAL
修复方向：重启后要么立即/剩余时间到期前显式 expire 并禁止用户 approve dead approval，要么实现 approval 决议后 resume worker 的持久化恢复桥。

### N-M-01 闭环验证

v3 只传了 `operation_type`，没有传有效 `session_id`。`ApprovalGate.resolve_approval()` 只有在 `decision == "approved" and session_id and operation_type` 时才更新 allowlist（`approval_gate.py:367-368`）。Web 路由把 `_session_id_for_gate` 固定为空字符串（`routes/approvals.py:145-154`），Telegram/operator 路径也传 `session_id=""`（`operator_actions.py:279-290`）。`ApprovalRequest` 模型没有 `session_id` 字段（`policy/models.py:199-248`），`ask_back_tools.py:446-456` 注册时也没有保存 session。

状态: ❌ PARTIAL
修复方向：在 ApprovalManager 记录中持久化 `session_id`，或双 resolve 从 ApprovalGate/request event payload 取 `session_id`。

### N-M-02 闭环验证

`_run_job()` 在 task 已终态时先查 `task_jobs`，若 job 也在 `_TERMINAL_JOB_STATUSES` 则直接 return，跳过重复 `mark_failed + notify`（`task_runner.py:943-971`）。`TERMINAL_STATES` 对 TaskStatus 覆盖 `SUCCEEDED/FAILED/CANCELLED/REJECTED`（`enums.py:63-68`）；`MERGED/ESCALATED/DELETED` 是 WorkStatus，且明确需要上下文、不属于 TaskStatus 安全映射集合（`delegation.py:87-90`, `delegation.py:126-165`）。

状态: ✅ CLOSED

## 新 Finding（v3 引入或前两轮漏掉）

### [NEW-HIGH-01] ApprovalManager timeout 与 ApprovalGate/task_runner timeout 不一致，允许过期审批继续成功

`escalate_permission` 注册 ApprovalManager 时设置 `expires_at = now + 300s`（`ask_back_tools.py:454`），`wait_for_decision()` 是 300s（`ask_back_tools.py:500`），task_runner approval timeout 默认也是 300s（`task_runner.py:88`, `task_runner.py:993-996`）。但 `ApprovalManager` 默认 timeout 是 600s（`approval_manager.py:80`），timer 使用 `self._default_timeout_s` 而不是 `request.expires_at`（`approval_manager.py:408-412`）。`resolve()` 只检查 pending/status，不检查 `request.expires_at`（`approval_manager.py:245-257`）。

后果：task/ApprovalGate 已在 300s 超时后失败或丢 handle，ApprovalManager 仍 pending 到 600s；用户在 300-600s 内 approve 会返回 success，甚至 `allow-always` 会写覆盖（`approval_manager.py:304-327`），但原操作不会执行。

修复方向：ApprovalManager timer 必须按 `request.expires_at - now` rearm，`resolve()` 必须在过期后拒绝并写 EXPIRED；task_runner/ApprovalGate 超时收口时也应同步 expire ApprovalManager pending。

### [NEW-MED-01] startup recovery placeholder 不会在 approval timeout 后清理，可能重复 notify

恢复未超时 WAITING_APPROVAL 时创建 `asyncio.sleep(999_999)` placeholder 并放入 `_running_jobs`（`task_runner.py:503-508`），但没有 done callback。approval timeout CAS 成功后只 `mark_failed/_notify_completion`，不 pop/cancel `_running_jobs`（`task_runner.py:1059-1069`）。之后全局 job timeout 会再次看到该 task，因 FAILED 不在 deferred skip 集合中，走 cancel + `mark_failed` + notify（`task_runner.py:1075-1107`）。

修复方向：approval timeout 成功后从 `_running_jobs` pop 并 cancel placeholder/worker，或给 placeholder 注册与 `_spawn_job()` 一致的 cleanup callback。

## v1/v2 残留 MED/LOW 重审

- v2 N-M-01: ❌ PARTIAL，见上。
- v2 N-M-02: ✅ CLOSED，见上。
- v1 的 6 MED + 1 LOW 在 `codex-review-phase-b.md` 只有数量，没有 finding 明细；按 grounding rule 不能推断 CLOSED，标记为 CANNOT_VERIFY。

## 整体结论

**不可 commit，不可进入 Phase C。**

v3 没有收敛到 0 HIGH：
1. HIGH-02 PARTIAL：timeout owner 丢失 interleaving（`wait_for_decision` 300s 返回后 task 离开 `_running_jobs`，monitor 扫不到，WAITING_APPROVAL 无后续 owner）
2. HIGH-04 PARTIAL：restart 后 approval 是 dead approval（handle 不存在，用户 approve 返回 success 但 task 不能恢复执行）
3. NEW-HIGH-01：ApprovalManager 600s timeout vs Gate/task_runner 300s 不一致，允许过期审批成功写 allow-always

需修复这 3 个 HIGH（HIGH-02/04 PARTIAL + NEW-HIGH-01），再做 v4 收敛确认。
