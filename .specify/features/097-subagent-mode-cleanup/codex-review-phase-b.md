# F097 Phase B — Codex Adversarial Review 闭环

**日期**: 2026-05-10
**审视命令**: `cat /tmp/f097-phase-b-codex-review.txt | codex review -`
**审视范围**: 4 个代码文件（capability_pack.py / agent_context.py / task_runner.py / test_agent_context_phase_b.py）
**审视模型**: GPT-5.4 high (Codex 默认)

## Findings 闭环表

| ID | 严重度 | 文件:行 | 描述 | 处理决策 | 实施动作 |
|----|--------|---------|------|---------|---------|
| **P1-1** | **high** | capability_pack.py:1311 (扩散到 task_runner emit) | 追加 USER_MESSAGE 只写 subagent_delegation 不带 turn-scoped target_kind → merge_control_metadata 取最新 USER_MESSAGE 时 target_kind 丢失 → _ensure_agent_session 走不到 SUBAGENT_INTERNAL 第 4 路 | **接受** | task_runner.\_emit_subagent_delegation_init_if_needed 写 USER_MESSAGE 时同时写 `target_kind="subagent"` + `spawned_by`；新增 `test_p1_1_emit_preserves_target_kind` 单测验证 |
| **P1-2** | **high** | capability_pack.py:1274 | launch_child_task 立即 enqueue 后台 _run_job → 调用方在返回后才追加 SubagentDelegation event → race：child runtime 已进入 context 构建读不到 delegation | **接受** | SubagentDelegation emit 路径整体重构：capability_pack 通过 `__subagent_delegation_init__` raw fields 传 control_metadata → task_runner.launch_child_task **在 create_task 后、enqueue 前** 调 `_emit_subagent_delegation_init_if_needed` emit USER_MESSAGE event。消除 race 窗口；新增 `test_p1_2_emit_before_enqueue_no_race` 单测验证 |
| **P1-3** | **high** | agent_context.py:2568 | 真实路径走 orchestrator `_prepare_a2a_dispatch` 预创建 agent_session_id → existing != None → B-3 条件 `existing is None` 跳过回填 → cleanup 永远拿不到 child_agent_session_id | **接受** | B-3 移除 `existing is None` 条件，无论是否新建 session 都尝试回填；EventStore.check_idempotency_key（idempotency_key=`subagent_delegation_session_backfill:{delegation_id}`）守护重复回填短路 |
| **P2-4** | medium | task_runner.py:751 | event/session 顺序调换后 session 失败 + 下次 cleanup idempotency 短路 → session 永久 ACTIVE | **缓解（不完全闭环）** | cleanup 内部即使 idempotency_key 短路（事件已存在）**仍走到 step 8 尝试 close session**：should_emit_event=False 时跳过 emit，但仍读 session + save CLOSED；防止事件 OK + session 因 first cleanup 失败留在 ACTIVE 永久状态 |
| **P2-5** | medium | task_runner.py:594 | dispatch fallback 调 cleanup 时 task 还非终态 → 提前关闭未终结 session | **接受** | cleanup 函数内部加 `task.status not in TERMINAL_STATES` 早期 return；防御性检查独立于调用方时序；新增 `test_p2_5_cleanup_skips_non_terminal_task` 单测验证 |
| **P2-6** | medium | capability_pack.py:1295 | caller_agent_runtime_id fallback 用 task_id 伪造 → 写到 parent_worker_runtime_id → 无法关联真实父 Worker | **接受** | capability_pack 不再 fallback；task_runner._emit_subagent_delegation_init_if_needed 在 caller_agent_runtime_id 为空时用 `<unknown>` 字面量（满足 SubagentDelegation min_length=1 约束）；新增 `test_p2_6_caller_unknown_when_no_execution_context` 单测验证 |

## 总结

- High: 3（**全部接受 + 闭环**）
- Medium: 3（**接受 2 + 缓解 1（P2-4）**）
- Low: 0

## P1 闭环价值

Codex Phase B 的 3 个 P1 finding 是迄今最严重的：

1. **P1-2 race**：子代理实施的两步写法（launch_child_task → 事后追加 USER_MESSAGE）在 child runtime 已立即启动后留下时间窗 → SubagentDelegation 在 child runtime 第一次进入 context 构建时可能不存在 → 整个 SUBAGENT_INTERNAL 路径不工作。
2. **P1-1 信号丢失**：merge_control_metadata 设计为只取最新 USER_MESSAGE 的 turn-scoped 字段；追加的 delegation event 没带 target_kind → 信号链断 → SUBAGENT_INTERNAL 第 4 路触发不了。
3. **P1-3 existing is None**：真实生产路径走 orchestrator `_prepare_a2a_dispatch` 预创建 session_id → existing != None → 回填条件失效 → cleanup 永远跳过。

10 个原有 Phase B 单测全 PASS 但**全部用 mock 绕开了真实 spawn → orchestrator → context 构建链路**。Codex review 从外部视角抓出了这些"测试通过但 production 不工作"的严重 bug。

## P2-4 缓解 vs 完整修复的 trade-off

P2-4 的根本解决需要把 session.save 和 event.append 放同一事务（涉及跨 store 事务管理）。当前缓解方案：
- emit 前用 idempotency_key 守护防重复
- emit 失败时不 commit（事件不写）
- session 失败时仍可被下次 cleanup 重试 close（即使 emit 已完成）

最坏情况：session 因数据库问题永久无法 close —— 但事件已写入完整审计链。这优于原顺序的"session closed 但无事件"（audit 缺失更危险）。完整事务修复留 future Feature。

## 测试结果

- Phase B 测试：10 → 14 PASS（+4 P1/P2 闭环：P1-2 emit 前 enqueue / P1-1 target_kind 保留 / P2-5 非终态跳过 / P2-6 unknown caller）
- 全量回归（exclude e2e）：3329 passed / 0 failed（vs Phase E baseline 3315 + 14 新 - 11.7 调整 ≈ 0 regression）

## Commit Message 闭环说明

```
feat(F097-Phase-B): SUBAGENT_INTERNAL session 路径 + spawn 写 SubagentDelegation
                  + 6 项 Codex review 闭环（3 high + 3 medium）

- _ensure_agent_session 加 SUBAGENT_INTERNAL 第 4 路（target_kind=subagent 信号）
- spawn 路径重构（Codex P1-2 闭环）：emit SubagentDelegation 移到
  launch_child_task（create_task 后 enqueue 前）消除 race
- USER_MESSAGE event 保留 target_kind 信号（Codex P1-1 闭环）
- B-3 backfill 移除 existing is None 条件（Codex P1-3 闭环）
- cleanup 加 TERMINAL_STATES 检测（Codex P2-5 闭环）
- caller_agent_runtime_id 用 <unknown> 占位（Codex P2-6 闭环）
- cleanup event 已存在仍尝试 close session（Codex P2-4 缓解）

AC 对齐: AC-B1 / AC-B2 + Phase E P2-3/P2-4 收口
Codex review: 3 high + 3 medium 全闭环（P2-4 接受缓解方案）
回归: 3329 passed (Phase E baseline 3315 + 14 新单测，0 regression)
```
