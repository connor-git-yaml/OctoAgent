# F101 Per-Phase B Codex Re-Review (v2)

> Reviewer: Codex GPT-5.4 high
> Date: 2026-05-17
> Phase: B v2（4 HIGH 修复后）
> Baseline: 3eba8a7 (Phase A)
> Verification (Phase B v2): 22 Phase B tests PASS + e2e_smoke 8/8 + 3488 regression
> **总体评估**：**FIX_HIGH_FIRST**（不允许 commit；3 HIGH 仍 PARTIAL + 2 新 MED）

## Summary

- HIGH: 3（HIGH-01/02/04 PARTIAL，仍需修）
- MEDIUM: 2 新（N-M-01/02）
- LOW: 0
- 整体：**NEEDS_V3_FIX**

## 4 HIGH 修复闭环验证

### HIGH-01 ApprovalGate 生产 resolve 路径 — ❌ **PARTIAL**

**v2 修复方向错误**：

`worker.escalate_permission` 只创建 ApprovalGate handle（`ask_back_tools.py`），**未注册到 ApprovalManager**。Web (`routes/approvals.py:97`) 和 Telegram (`telegram.py:707-723` → `operator_actions.py`) 双 resolve 都要求**先** `approval_manager.resolve` 成功，**才**调 `approval_gate.resolve_approval`。

Gate-only approval（escalate_permission 发起的）→ ApprovalManager 根本不知道这个 approval_id → 返回 404/409 → approval_gate.resolve 永不执行 → worker 永远等 timeout。

**Telegram 路径确认**：sub-agent 说"部分 Telegram 通过同一 OperatorActionService 路径"——已经验证：Telegram 通过 OperatorActionService，但仍受"先 ApprovalManager.resolve 才 ApprovalGate.resolve"约束。

**v3 修复方向**（关键决策）：

- **路径 A（最简）**：escalate_permission 在创建 ApprovalGate handle 时**也**调用 `approval_manager.create()`/注册，让 Web/Telegram 双 resolve 路径都能找到此 approval_id
- **路径 B（解耦）**：Web/Telegram 路径**先尝试** approval_gate.resolve（不依赖 ApprovalManager），再尝试 approval_manager.resolve（policy 层 audit）；任一成功即返回 success
- **推荐路径 A**：保持 ApprovalManager 作为 production 唯一 SoT，让 ApprovalGate 通过 ApprovalManager 注册

### HIGH-02 finally 块 vs monitor 竞态 — ❌ **PARTIAL**

`wait_for_decision` 300s timeout 先返回时，finally 块**仍可**把当前是 WAITING_APPROVAL 的任务恢复 RUNNING。violatesspec.md:194-196。

race window 缩小但未消除：
- v1：无条件 mark_running_from_waiting_approval → race window 大
- v2：先查状态再决定 → race window 缩小（仅 wait_for_decision timeout 返回 + monitor 还未推 FAILED 之间窗口）
- v3 修复：finally 块**完全不**调 mark_running_from_waiting_approval，让 task_runner monitor 唯一负责终态转移；或者更细致的语义——通过 wait_for_decision 返回值区分 timeout（不恢复 RUNNING）vs 用户决议（恢复 RUNNING 让 worker 继续）

### HIGH-03 monitor CAS 失败 abort side effects — ✅ **CLOSED**

`task_runner.py` CAS 失败时不继续 emit side effects（mark_failed + 通知），transaction.py 在 expected_status 不匹配时 raise + rollback。修复正确。

### HIGH-04 startup_recovery WAITING_APPROVAL — ❌ **PARTIAL**

两个问题：

1. **直接推 FAILED 错误**：startup_recovery 把"尚未到 approval timeout"的 WAITING_APPROVAL 也直接推 FAILED（reason: `user_inaction_restart_Ns`），而非恢复 monitor。如果用户重启 gateway 时审批刚发起 30s，新流程不应该 5 分钟后超时——应该重启 monitor 继续等剩余 270s。
2. **reason 格式不符 spec**：v2 写的是 `user_inaction_restart_Ns` / `gateway_restart_approval_lost`，但 spec FR-C3b 实际格式（与 approval_gate timeout 一致）应该是 `timeout_after_<sec>s`。

**v3 修复**：
- startup_recovery 扫 WAITING_APPROVAL 时，**重启 monitor**（按 USER.md approval_timeout_seconds + 剩余时间计算）
- 若实测发现 timeout 已过（重启时间 > approval_timeout）→ 推 FAILED + reason `timeout_after_<sec>s`（统一格式）

## 新 Finding（v2 引入）

### [N-M-01] MEDIUM — 双 resolve 调 ApprovalGate 未传 session_id / operation_type

- **位置**：`approval_gate.py:367-368` + `routes/approvals.py:139-141` + `operator_actions.py:283-285`
- **描述**：v2 双 resolve 调 ApprovalGate.resolve_approval 时未传 `session_id` / `operation_type`，但 ApprovalGate 内部有条件 `if decision == "approved" and session_id and operation_type:` 才更新 session allowlist。调用方传空字符串 → allowlist 永远不会更新 → 后续同 session/同 operation 重新触发 escalate_permission 时无法享受"已批准放行"优化。
- **v3 修复**：双 resolve 调 approval_gate.resolve_approval 时从 approval_request（或 ApprovalManager 的 entry）读取原 session_id / operation_type 并传入

### [N-M-02] MEDIUM — approval timeout CAS 成功后未取消 worker task

- **位置**：`task_runner.py:_monitor_loop_step` + `_run_job`
- **描述**：approval timeout CAS 成功后只推 FAILED state transition，但 worker task（`_run_job` 协程）仍在等 ApprovalGate.wait_for_decision。timeout 后 _run_job 看到 task 已终态会再次调 `mark_failed` + 通知 → double-notify 风险。
- **v3 修复**：approval timeout CAS 成功后 cancel _run_job task（或通过 ApprovalGate.resolve_with_timeout 让 wait_for_decision 提前返回，让 _run_job 走 timeout 路径但加 "终态已写入" check 跳过重复 mark_failed）

## v2 是否引入新 bug

- 修复 HIGH-01 时新增的 `get_approval_gate` dependency / OperatorActionService.approval_gate 参数 — 不破坏现有测试（mock 链路保持兼容）
- HIGH-04 startup_recovery 处理 WAITING_APPROVAL 时——可能与 HIGH-01 修复后的 ApprovalManager 注册产生 race，需 v3 验证

## 整体结论

**不可 commit 进入 Phase C**。

修复优先级：
1. **HIGH-01（最高）**：v3 修复方向 A——escalate_permission 创建 ApprovalGate handle 时同步注册到 ApprovalManager。
2. **HIGH-02**：v3 修复——finally 块根本不调 mark_running_from_waiting_approval；让 task_runner monitor 唯一负责终态。
3. **HIGH-04**：v3 修复——startup_recovery 重启 monitor 而非直接 FAILED；reason 格式统一 `timeout_after_<sec>s`。
4. **N-M-01**：双 resolve 传 session_id / operation_type。
5. **N-M-02**：approval timeout 后取消 worker task 或加去重 check。

修复后跑第三轮 re-review。CLAUDE.local.md 实证：重大状态机改造需要 ≥ 3 轮 review 才收敛。
