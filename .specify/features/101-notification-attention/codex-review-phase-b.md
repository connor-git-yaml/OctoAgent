# F101 Per-Phase B Codex Review

> Reviewer: Codex GPT-5.4 high
> Date: 2026-05-17
> Phase: B 联合（FR-C1+C2+C3+C3b+C6）
> Baseline: 3eba8a7 (Phase A commit)
> Verification (Phase B v1): 11 Phase B tests + 8/8 e2e_smoke + 3531 regression all PASS（但 PASS ≠ 生产链路真工作）

## Summary

- HIGH: 4（全部必修，否则 Phase B 生产不可用）
- MEDIUM: 6（按 Codex 简报，未细列）
- LOW: 1（按 Codex 简报，未细列）
- 总体评估：**FIX_HIGH_FIRST**（不能直接进 Phase C；HIGH-01 是 Phase B 核心功能根本缺陷）

## HIGH Finding 详细列表

### HIGH-01：ApprovalGate 生产 resolve 路径**永远不唤醒** wait_for_decision

- **位置**：`ask_back_tools.py:425-447` + `apps/gateway/src/octoagent/gateway/routes/approvals.py:78-91` + `services/operator_actions.py:189-251`
- **描述**：`escalate_permission_handler` 创建 ApprovalGate handle 后调用 `await wait_for_decision()`，**但 production 路径下用户点击批准走的是 `ApprovalManager.resolve()`，不是 `ApprovalGate.resolve()`**——Web `/api/approve/{approval_id}` 路由 + Telegram callback + operator action 全部走 ApprovalManager 路径。**ApprovalGate._pending_handles 永远不会被生产唤醒**。
- **后果**：生产环境任何 escalate_permission 调用必须等 300s timeout 后返回 "rejected"——**Phase B 的核心 AC-C2 功能在生产路径上根本不工作**。mock 测试 PASS 因为测试直接调用 `ApprovalGate.resolve()` 自己唤醒。
- **Adversarial 立场理由**：sub-agent 没意识到 ApprovalGate（F084 harness 层）和 ApprovalManager（F084 policy 层）是两个不同的对象。pre-impl review 的 H1 警告"mock-only 验收不足以证明生产链路"被部分解决（加了 SSE integration test）但没扩展到 resolve 链路。
- **推荐方向（HIGH 必修）**：在 `routes/approvals.py` POST `/api/approve/...` 端点 + Telegram callback 中**双 resolve**——既调 `approval_manager.resolve()` 又调 `approval_gate.resolve(approval_id, decision)`；或者让 ApprovalGate 通过 ApprovalManager subscriber 模式桥接（ApprovalGate 监听 ApprovalManager 的 decision events）。

### HIGH-02：finally 块 mark_running_from_waiting_approval 与 monitor 推 FAILED 的竞态

- **位置**：`ask_back_tools.py:446-453` + `execution_console.py:408-443` + `task_runner.py:790-859`
- **描述**：`escalate_permission_handler` 在 finally 块**无条件**调用 `mark_running_from_waiting_approval()`，把 WAITING_APPROVAL 写回 RUNNING。但与此同时 task_runner monitor loop 按 300s 超时把 WAITING_APPROVAL 推 FAILED。用户 300s 不响应时——谁先触发决定最终状态，违反 spec §10 第 7 条"task_runner 单 owner + 唯一终态"约束。
- **后果**：competing transitions 可能 race，pre-impl review H2 未完整修复（v1 实施只在 monitor 路径加 CAS guard，没在 finally 块加）。
- **推荐方向**：finally 块在调 mark_running_from_waiting_approval 前**先查询当前状态**——若已是 FAILED（被 monitor 推），跳过；或将状态转移逻辑也走 compare-and-set（仅 WAITING_APPROVAL → RUNNING 时执行）。

### HIGH-03：monitor CAS 失败后仍执行 FAILED side effects → 状态分裂

- **位置**：`task_runner.py:833-859` + `task_job_store.py:173-187`
- **描述**：task_runner monitor 超时路径先调 `task_job_store.mark_failed()` 再调 `_write_state_transition(WAITING_APPROVAL→FAILED)`。若 CAS 失败（task 已转其他终态），代码只 log warning **但仍继续执行 `_mark_execution_terminal()` + `_notify_completion()`**。`task_job_store.mark_failed()` 是无条件更新，导致：task 表 = RUNNING / job 表 = FAILED / 通知已发——三者分裂。
- **后果**：违反 spec 宪法 1（Durability First）+ 第 7 条 task_runner 单 owner 唯一终态保证。监控系统会看到 contradicting 状态。
- **推荐方向**：把 task_job_store.mark_failed() **挪到 CAS 成功后**；CAS 失败时立即 abort 整个 FAILED 路径，不 emit side effects；改 task_job_store 接口让 mark_failed 也带 CAS 语义（only if currently RUNNING/WAITING_APPROVAL）。

### HIGH-04：gateway 重启丢失 WAITING_APPROVAL 状态 → FR-C6 对称性承诺违反

- **位置**：`approval_gate.py:111-112`（`_pending_handles` 内存态）+ `task_runner.py:383-389`（startup_recovery 仅扫 RUNNING）
- **描述**：`ApprovalGate._pending_handles` 是 in-memory dict，gateway 重启全部丢失。task_runner.startup_recovery 只扫 RUNNING job（line 383-389），不扫 WAITING_APPROVAL。重启前正在等审批的 task：①Web 用户点击批准 → ApprovalManager.resolve 没对应 handle → 操作失败 ②task_runner 也不会按 timeout 收口 → 任务永远 hang。
- **后果**：违反 spec FR-C6 对称性承诺（与 attach_input 路径对称恢复）+ 宪法 1（Durability First）。
- **推荐方向**：startup_recovery 扩展扫 WAITING_APPROVAL job，按 timeout policy 推 FAILED（或允许用户 dismiss 重新发起）；或 ApprovalGate._pending_handles 持久化到 event store（如 APPROVAL_REQUESTED event 记录 timeout_at，重启后 task_runner 读取并恢复 monitoring）。

## MEDIUM + LOW（Codex 简报未细列，按数量记录）

- MEDIUM: 6 项（待 re-review 或后续 Phase 修订时展开）
- LOW: 1 项

## 整体结论

**是否可以 commit + 进入 Phase C：不可**

理由：HIGH-01 是 Phase B 核心 AC-C2 的根本缺陷（生产路径下 escalate_permission 永远 timeout），HIGH-02/03/04 是状态一致性/Durability 风险。必须先修 4 HIGH，再跑 re-review 确认闭环（CLAUDE.local.md §"工作流改进"：大改后必须 re-review），再 commit。

## 关键学习点

1. **mock 测试 PASS ≠ 生产可用**：B-9c integration test 用真实 SSEHub + 真实 ApprovalGate 但测试自己调 `ApprovalGate.resolve()`——没穿透 production resolve 路径（routes/approvals.py → ApprovalManager → ApprovalGate）的桥接。
2. **F084 ApprovalGate vs ApprovalManager 是两层**：harness ApprovalGate（F084 SSE 推送 + session allowlist + handle）和 policy ApprovalManager（F084 决策落盘 + event）是不同对象。Phase B 只接通了 ApprovalGate sse_push_fn 但没接通 resolve 通路。
3. **state machine 单 owner 比想象的难**：CAS 在 monitor 路径加了，但 finally 块没加；side effects emit 在 CAS check 之外——这是状态机改造常见的 partial fix。

## 修复优先级

按用户视角 / blast radius：
1. **HIGH-01 必须最先修**：影响生产功能根本性。
2. **HIGH-03 第二**：影响 Durability + 监控可见性。
3. **HIGH-02 第三**：race 在 300s 边界才会触发，blast radius 小但违反单 owner 约束。
4. **HIGH-04 第四**：仅 gateway 重启时触发，但与 FR-C6 对称性承诺直接冲突。

修复后必须 re-review 确认闭环。
