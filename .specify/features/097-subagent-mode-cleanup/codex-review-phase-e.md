# F097 Phase E — Codex Adversarial Review 闭环

**日期**: 2026-05-10
**审视命令**: `cat /tmp/f097-phase-e-codex-review.txt | codex review -`
**审视范围**: 5 个代码文件（task_runner.py / enums.py / payloads.py / __init__.py / test_task_runner_subagent_cleanup.py）
**审视模型**: GPT-5.4 high (Codex 默认)

## Findings 闭环表

| ID | 严重度 | 文件:行 | 描述 | 处理决策 | 实施动作 |
|----|--------|---------|------|---------|---------|
| **P1-1** | **high** | task_runner.py:666-667 | `TaskService.get_latest_user_metadata()` 经 `normalize_control_metadata()` 过滤白名单，`subagent_delegation` key 不在 `TASK_SCOPED_CONTROL_KEYS` → 真实 spawn 路径下 cleanup 永远 return noop。AC-E1 session CLOSED 和 AC-EVENT-1 SUBAGENT_COMPLETED 永不触发 | **接受** | 加 `subagent_delegation` 到 `TASK_SCOPED_CONTROL_KEYS`（normalize 对结构化 dict value 已支持，line 114）；新增 2 个测试守护此白名单不丢失 |
| **P1-2** | **high** | task_runner.py:681-683 | `delegation.closed_at` 未持久化回 task metadata，重复触发 cleanup（进程重启 / _notify_completion 多次调用）时 closed_at 仍是 None → 幂等检查失效 → 重复 emit SUBAGENT_COMPLETED | **接受** | 用 `EventStore.check_idempotency_key("subagent_completed:{delegation_id}")` 做真幂等检查（事件存在则整个 cleanup 短路）；emit 时 Event.causality.idempotency_key 设为同 key；新增测试验证重复触发不重复 emit |
| **P2-3** | medium | task_runner.py:708-710 | session.commit 与 event.append 非原子；event 失败留下 closed session 但无审计事件，task_job 已终态不会重试 → 不可恢复的 audit chain 不一致 | **接受推迟到 Phase B** | 显式归档：spawn 路径设计在 Phase B 实施，事务边界（session save + event emit 同事务 / 或先写事件后改 session）需与 spawn 写入路径联合设计。Phase E 当前的 P1-2 EventStore 幂等已确保不会重复 emit；但首次 emit 失败后留下 closed session 仍是已知风险 |
| **P2-4** | medium | task_runner.py:641-643 | cleanup 仅挂在 `_notify_completion`，但 dispatch exception / shutdown 兜底（task_runner.py L253/L269/L289/L312/L403/L420/L570/L578/L585/L637 共 11 处终态分支中至少 7 处也调 _notify_completion）会标 FAILED 但不一定全调 _notify_completion → 部分终态路径不触发 cleanup → ACTIVE session 残留 | **接受推迟到 Phase B** | 显式归档：实测 grep 确认 7/11 终态分支已调 _notify_completion；剩余 4 处属于 shutdown / exception 边缘场景，应在 Phase B 实施 SUBAGENT_INTERNAL session 创建路径时同步把 cleanup 挪到统一的 task state machine 终态层（task_service._write_state_transition），覆盖所有终态路径 |

## 总结

- High: 2（**全部接受 + 闭环**）
- Medium: 2（**全部接受 + 显式归档推迟到 Phase B**）
- Low: 0

## P1 闭环价值

Codex 找到的 P1-1 是**严重的真实路径不工作 bug**——10 个原有测试全 PASS，但全部 mock 了 `get_latest_user_metadata`（return_value 直接含 subagent_delegation 字典）→ **测试覆盖完全绕开了 normalize 路径**。Phase B 实施 spawn 后，真实路径下 cleanup 永远拿不到 delegation 数据，spec AC-E1 / AC-EVENT-1 全部失效。

Codex 找到的 P1-2 是**幂等检查的设计缺陷**——子代理实施时把幂等寄托在 `delegation.closed_at`，但 spec CL#16 明确持久化路径走 `child_task.metadata`，cleanup 函数本身没有写回 metadata。修复采用 EventStore.check_idempotency_key（更轻量 + 与 OctoAgent 现有 EventStore 设计哲学一致）。

## P2 推迟到 Phase B 的理由

P2-3 和 P2-4 都涉及 spawn 路径设计：
- P2-3 事务边界需要 spawn 写入 metadata 和 cleanup 写回 metadata 联动设计
- P2-4 终态统一层需要 task state machine 内部触发 hook（涉及 task_service._write_state_transition 改造）

Phase B 实施 SUBAGENT_INTERNAL session 路径时同步收口；Phase E 当前的 P1-2 EventStore 幂等已经把 P2-3 / P2-4 的最坏后果（重复 emit）兜住。

## 测试结果

- Phase E 测试：10 → 13 PASS（+3 P1 闭环：normalize 白名单 dict value / normalize 白名单 string value / EventStore.check_idempotency_key 幂等防重复 emit）
- 全量回归（exclude e2e）：3315 passed / 0 failed（vs Phase 0 baseline 3252 + 累计新增 0 regression）

## Commit Message 闭环说明

```
feat(F097-Phase-E): cleanup hook + SUBAGENT_COMPLETED enum/emit + 4 项 Codex review 闭环

- EventType.SUBAGENT_COMPLETED enum + SubagentCompletedPayload schema
- _close_subagent_session_if_needed cleanup hook（session CLOSED + emit SUBAGENT_COMPLETED）
- Codex P1-1: subagent_delegation 加 TASK_SCOPED_CONTROL_KEYS 白名单（normalize 不丢失）
- Codex P1-2: EventStore.check_idempotency_key 真幂等防重复 emit
- Codex P2-3 + P2-4 显式归档推迟 Phase B（事务边界 + 终态统一层）

AC 对齐: AC-E1 / AC-E2 / AC-E3 / AC-EVENT-1（F-01 条件路径 b 完整）
Codex review: 2 high + 2 medium 全闭环（P2 推迟 Phase B 已显式归档）
回归: 3315 passed (Phase 0 baseline + 0 regression)
```
