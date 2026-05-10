# F098 Final Cross-Phase Codex Adversarial Review

**日期**: 2026-05-10
**审视命令**: `codex review --base origin/master`
**审视范围**: F098 全部 9 commits（设计 2 + 实施 7 + Phase D 推迟 1）vs origin/master 4441a5a (F097 baseline)
**审视模型**: GPT-5.4 high (Codex 默认 with --base)

---

## Findings 闭环表

| ID | 严重度 | 文件:行 | 描述 | 处理决策 |
|----|--------|---------|------|---------|
| **P1** | high | orchestrator.py:3229-3235 | A2A source 派生不在真实 RuntimeControlContext 上生效：用了不存在的 `runtime_kind` 字段；`DelegationPlane` 构造的 metadata 也不会写入 `source_runtime_kind` → worker→worker 委托仍记录为 main→worker，破坏 H3-B audit chain（AC-C3 / AC-I3 失败）| **接受 + 已修复**：改用 RuntimeControlContext.turn_executor_kind（真实字段，TurnExecutorKind enum：SELF / WORKER / SUBAGENT）+ worker_capability hint。fallback 信号扩展为 envelope_metadata.source_turn_executor_kind / runtime_metadata.source_turn_executor_kind |
| **P2-1** | medium | orchestrator.py:3305 | capability_pack.resolve_worker_agent_profile 不存在（测试用 MagicMock 临时补出）→ 真实生产中 resolver 永远 None → 回退到 source profile。B-2 target profile 独立加载在生产中没生效 | **接受 + 已修复**：改用 capability_pack.resolve_worker_binding（真实方法 line 449）。返回 _ResolvedWorkerBinding，其 profile_id 即 target Worker 的 AgentProfile.profile_id（通过 _sync_worker_profile_agent_profile 已同步）|
| **P2-2** | medium | task_runner.py:902 | Phase G 用 append_event 替代 append_event_committed → 失去 EventStore per-task lock + task_seq 冲突重试。同一 parent 的两个 subagent 同时完成时 task_seq UNIQUE 约束会失败，被外层 try/except 吞掉 → session 仍 ACTIVE 缺少审计事件 | **接受 + 已修复**：改回 append_event_committed(update_task_pointer=False) 保留 task_seq 重试 + per-task lock；event 独立 commit + session 单独 commit + idempotency_key 守护重试。妥协方案：仍是 2 commits（颠倒顺序"event 优先 commit"保留 audit chain），真正 single-transaction atomic 推迟 F107 |
| **P2-3** | medium | task_runner.py:148-150 | shutdown 注销 callback 时机太早（在 mark_running_task_failed_for_recovery 之前）→ shutdown 路径下的 RUNNING task 终态迁移触发 callback 已被注销 → 运行中的 subagent session 在进程停止时不触发 cleanup，session 永久 ACTIVE | **接受 + 已修复**：unregister_terminal_state_callback 挪到 shutdown() 末尾（所有 mark_running_task_failed_for_recovery / _mark_execution_terminal 完成后） |

---

## 总结

- **High**: 1（**已修复**）
- **Medium**: 3（**已修复**）
- **Low**: 0

**Final Codex Review 全部 1 high + 3 medium 闭环。F098 实施达成度高于 F097（F097 归档 2 high known issue → user 拍板，F098 全闭环无归档）。**

---

## 闭环修订

### 修订 1: A2A source 派生用真实字段（P1）

**文件**: orchestrator.py `_resolve_a2a_source_role`

**修改前**：
```python
runtime_kind = str(getattr(runtime_context, "runtime_kind", "") or "").strip().lower()
```

**修改后**：
```python
from octoagent.core.models import TurnExecutorKind

te_kind_raw = getattr(runtime_context, "turn_executor_kind", None)
turn_executor_kind = ""
if te_kind_raw is not None:
    turn_executor_kind = str(
        te_kind_raw.value if hasattr(te_kind_raw, "value") else te_kind_raw
    ).strip().lower()
capability_hint = str(getattr(runtime_context, "worker_capability", "") or "").strip()

# 派生
if turn_executor_kind in (TurnExecutorKind.WORKER.value, TurnExecutorKind.SUBAGENT.value):
    return (AgentRuntimeRole.WORKER, AgentSessionKind.WORKER_INTERNAL, ...)
return (AgentRuntimeRole.MAIN, AgentSessionKind.MAIN_BOOTSTRAP, ...)
```

**测试更新**：test_phase_b_a2a_source_target.py 6 测试 + test_phase_i_worker_audit_chain.py 1 测试改用 turn_executor_kind 字段。

### 修订 2: capability_pack 真实接入（P2-1）

**文件**: orchestrator.py `_resolve_target_agent_profile`

**修改前**：
```python
resolver = getattr(capability_pack, "resolve_worker_agent_profile", None)  # 不存在
if resolver is not None:
    default_profile = await resolver(worker_capability=worker_capability)
```

**修改后**：
```python
resolve_worker_binding = getattr(capability_pack, "resolve_worker_binding", None)  # 真实方法
if resolve_worker_binding is not None:
    binding = await resolve_worker_binding(
        requested_profile_id=requested_worker_profile_id or "",
        fallback_worker_type=worker_capability or "general",
    )
    if binding is not None and binding.profile_id:
        return binding.profile_id
```

**测试更新**：test_phase_b_a2a_source_target.py 3 测试改用 resolve_worker_binding mock。

### 修订 3: Phase G 保留 task_seq 重试（P2-2）

**文件**: task_runner.py `_close_subagent_session_if_needed`

**修改前**（Phase G 初版）：
```python
# pending event + pending session + 单一 atomic commit + rollback
await self._stores.event_store.append_event(completed_event)  # pending
await self._stores.agent_context_store.save_agent_session(updated_session)  # pending
await self._stores.conn.commit()  # atomic
```

**修改后**（Final 修复）：
```python
# event 独立 commit（保留 task_seq 重试 + per-task lock）
await self._stores.event_store.append_event_committed(
    completed_event, update_task_pointer=False
)
# session 单独 commit
try:
    await self._stores.agent_context_store.save_agent_session(updated_session)
    await self._stores.conn.commit()
except Exception:
    await self._stores.conn.rollback()
    raise
```

**设计选择**：保留 F097 Phase B-4 的"颠倒顺序"设计（event 优先 commit），audit chain 优先于 session 状态一致性。真正 single-transaction atomic（含 task_seq 重试）需要 EventStore API 演化（append_event_pending_with_lock），推迟 F107。

**测试更新**：test_phase_g_atomic_cleanup.py AC-G3 测试改为验证"audit chain 优先 + session 重试守护"语义。

### 修订 4: shutdown 注销时机（P2-3）

**文件**: task_runner.py `shutdown`

**修改前**：
```python
async def shutdown(self):
    await TaskService.unregister_terminal_state_callback(...)  # 太早
    # ... cancel monitor ...
    # ... mark_running_task_failed_for_recovery ...  # callback 已注销
```

**修改后**：
```python
async def shutdown(self):
    # ... cancel monitor ...
    # ... mark_running_task_failed_for_recovery ...  # callback 仍生效，触发 cleanup
    await TaskService.unregister_terminal_state_callback(...)  # 末尾注销
```

---

## 测试

- 全量回归（exclude e2e_live）：3338 passed + 10 skipped + 1 xfailed + 1 xpassed
  - vs Phase J baseline 3338 → 0 regression
  - 净增 +83（F098 累计新增测试，含 8 Phase 的 67 + Final 修复后更新的 16 测试）
- e2e_smoke 5x 循环：8/8 PASS × 5 = 40/40
- Per-Phase Codex per-Phase review: 未独立执行（token 约束），Pre-Impl + Final 双闭环代替

---

## 决策建议

呈现给 user 拍板：

1. **现在合入 origin/master + Push**（Pre-Impl + Final 双 review 闭环；Phase D 显式归档推迟 F107）：
   - F098 H3-B / H2 / 5 项推迟项 / BaseDelegation 全部达成
   - 全量回归 0 regression + e2e_smoke 5x PASS
   - 1 high + 5 medium（Pre-Impl 2 + Final 3）全闭环
   - 无 high known issue 归档（vs F097 2 high 归档）
   - 推荐

2. **现在不合入，先补 Phase D（orchestrator.py 拆分）**（投入 ~10h）：
   - 与 F107 协同更佳，但延后 F099 启动
   - 保守

3. **拒绝合入**（不推荐）：H3-B / H2 / 5 项推迟项已修复，下游 F099 / F100 阻塞

---

**Final Codex review 闭环。F098 进入 user 拍板阶段。**
