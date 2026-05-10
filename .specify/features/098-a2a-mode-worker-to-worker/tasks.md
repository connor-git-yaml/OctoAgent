# F098 Tasks — 细化任务清单

**关联**: spec.md v0.1 + clarification.md（GATE_DESIGN 已锁）+ plan.md
**Phase 顺序**: E → F → B → C → I → H → G → J → D → Verify
**总任务数**: 60+
**估计实施时间**: 60-80h（per-Phase 见 plan.md §13）

---

## Phase 0 — 实测侦察 ✓ 已完成

- [x] T-0-1: A2A 当前路径侦察（产出 phase-0-recon.md §1）
- [x] T-0-2: `_enforce_child_target_kind_policy` 调用点清单（phase-0-recon.md §2）
- [x] T-0-3: dispatch 路径组织（phase-0-recon.md §3）
- [x] T-0-4: F097 5 项推迟项 baseline 实测（phase-0-recon.md §4）
- [x] T-0-5: 架构设计点候选评估（phase-0-recon.md §5）

---

## Phase E — CONTROL_METADATA_UPDATED 引入

### Spec & Design

- [ ] T-E-1: 在 `enums.py` 添加 `EventType.CONTROL_METADATA_UPDATED`
- [ ] T-E-2: 在 `payloads.py` 添加 `ControlMetadataUpdatedPayload` model
- [ ] T-E-3: 单测验证 ControlMetadataUpdatedPayload round-trip（model_dump / model_validate）

### Implementation

- [ ] T-E-4: `connection_metadata.merge_control_metadata` 改造合并 USER_MESSAGE + CONTROL_METADATA_UPDATED
- [ ] T-E-5: `task_runner._emit_subagent_delegation_init_if_needed` 改用 CONTROL_METADATA_UPDATED
- [ ] T-E-6: `agent_context._ensure_agent_session` B-3 backfill 改用 CONTROL_METADATA_UPDATED

### Testing

- [ ] T-E-7: 新增 `test_phase_e_control_metadata_updated.py`
  - merge_control_metadata 合并两类 events 时序
  - subagent_delegation_init 事件正确 emit + payload 字段
  - B-3 backfill 事件正确 emit + 含 child_agent_session_id
- [ ] T-E-8: 新增 `test_phase_e_backward_compat.py`
  - 历史 USER_MESSAGE 含 subagent_delegation 仍可读
  - 混合事件流（USER_MESSAGE + CONTROL_METADATA_UPDATED）merge 正确
- [ ] T-E-9: 集成测：subagent task 首轮 `latest_user_text` 不含 marker text "[subagent delegation metadata]"

### Codex Review

- [ ] T-E-10: Phase E pre-review（spec/design 大改）
- [ ] T-E-11: Phase E post-review

### Validation

- [ ] T-E-12: 全量回归 ≥ 3355 vs F097 baseline (4441a5a)
- [ ] T-E-13: e2e_smoke PASS（pre-commit hook）

---

## Phase F — Ephemeral Runtime 独立路径

### Implementation

- [ ] T-F-1: `agent_context._ensure_agent_runtime` 加 subagent 路径检测
  - 信号 1: `request.delegation_metadata["target_kind"] == "subagent"`
  - 信号 2: `agent_profile.kind == "subagent"`（fallback）
- [ ] T-F-2: subagent 路径跳过 `find_active_runtime` 复用
- [ ] T-F-3: AgentRuntime.metadata 添加 `subagent_delegation_id` 字段（仅 subagent 路径填充）

### Testing

- [ ] T-F-4: 新增 `test_phase_f_ephemeral_runtime.py`
  - subagent → 不复用 caller worker runtime
  - subagent AgentRuntime.metadata 含 subagent_delegation_id
  - main / worker 路径 find_active_runtime 仍走（regression）

### Codex Review

- [ ] T-F-5: Phase F post-review

### Validation

- [ ] T-F-6: 全量回归 0 regression vs Phase E baseline
- [ ] T-F-7: e2e_smoke PASS

---

## Phase B — A2A Target Profile 独立加载

### Implementation

- [ ] T-B-1: `orchestrator._resolve_target_agent_profile` 新增（3 路径：requested_worker_profile_id / worker_capability / fallback）
- [ ] T-B-2: `orchestrator._prepare_a2a_dispatch` 用 _resolve_target_agent_profile 替代 source_agent_profile_id 复用
- [ ] T-B-3: capability_pack 加 `resolve_worker_agent_profile(worker_capability)` API（如不存在）

### Testing

- [ ] T-B-4: 新增 `test_phase_b_a2a_target_profile.py`
  - 路径 1: requested_worker_profile_id 直接 lookup
  - 路径 2: worker_capability 派生 default profile
  - 路径 3: fallback (warning log)
  - target_profile_id != source_profile_id（独立加载验证）
- [ ] T-B-5: 集成测：A2A receiver context 4 层身份独立

### Codex Review

- [ ] T-B-6: Phase B post-review（H3-B 核心）

### Validation

- [ ] T-B-7: 全量回归 0 regression vs Phase F baseline
- [ ] T-B-8: e2e_smoke PASS（A2A 路径不破）

---

## Phase C — Worker→Worker A2A 解禁

### Implementation

- [ ] T-C-1: `capability_pack._launch_child_task` 删除 `self.enforce_child_target_kind_policy(target_kind)` 调用 (line 1252)
- [ ] T-C-2: `capability_pack` 删除 `enforce_child_target_kind_policy` 函数定义 (line 1355-1381)
- [ ] T-C-3: `delegation_plane.py` 注释更新（3 处：line 81 / 976 / 1082）
- [ ] T-C-4: 删除 `test_capability_pack_phase_d.py:71` 的 mock
- [ ] T-C-5: 调整 `test_capability_pack_phase_d.py:261` 测试为正面验证 worker→worker 通过

### Testing

- [ ] T-C-6: 新增 `test_phase_c_worker_to_worker.py`
  - Worker A 调用 delegate_task(target_kind=worker) 不 raise
  - child Worker B 任务正常 spawn
  - audit chain: B AgentSession.parent_worker_runtime_id == A AgentRuntime.agent_runtime_id
  - max_depth=2 死循环防护仍生效

### Codex Review

- [ ] T-C-7: Phase C post-review（解禁影响面）

### Validation

- [ ] T-C-8: 全量回归 0 regression
- [ ] T-C-9: e2e_smoke PASS

---

## Phase I — Worker Audit Chain 集成测

### Implementation

- [ ] T-I-1: 新增 `test_phase_i_worker_audit_chain.py`
  - `test_f098_audit_chain_worker_dispatch`: main → worker dispatch audit chain 4 层对齐（F096 H2 推迟项归位）
  - `test_f098_audit_chain_worker_to_worker_dispatch`: worker → worker A2A audit chain 链式追溯（依赖 Phase C）

### Codex Review

- [ ] T-I-2: Phase I post-review

### Validation

- [ ] T-I-3: 集成测 PASS
- [ ] T-I-4: 全量回归 0 regression

---

## Phase H — 终态统一 Cleanup Hook（task state machine 改造）

### Spec & Design

- [ ] T-H-1: TaskService callback 注册机制设计（class-level vs instance-level；`_terminal_state_callbacks`）
- [ ] T-H-2: TaskRunner 注册时机（构造函数 vs 启动 hook）

### Implementation

- [ ] T-H-3: `TaskService` 加 `_terminal_state_callbacks` class 字段 + register_terminal_state_callback classmethod
- [ ] T-H-4: `TaskService._invoke_terminal_state_callbacks` 异常隔离实现
- [ ] T-H-5: `TaskService._write_state_transition` 在 `cleanup_lock` 后调用 callbacks
- [ ] T-H-6: `TaskRunner.__init__` 注册 `_close_subagent_session_if_needed` 为 callback
- [ ] T-H-7: 移除 `task_runner.py:679` dispatch exception 路径手动调用
- [ ] T-H-8: 移除 `task_runner.py:711` mark_failed 非终态分支手动调用
- [ ] T-H-9: 移除 `task_runner.py:764` _notify_completion 内调用

### Testing

- [ ] T-H-10: 新增 `test_phase_h_terminal_callback.py`
  - _write_state_transition 终态触发 callback
  - cleanup callback 注册 + 终态触发
  - mark_failed_for_recovery / mark_cancelled_for_runtime / dispatch exception / shutdown 4 路径自动 cleanup
  - callback 异常 → state transition 仍成功（异常隔离）
- [ ] T-H-11: grep 验证 task_runner.py 不再有手动 `_close_subagent_session_if_needed` 调用（仅定义和注册处）

### Codex Review

- [ ] T-H-12: Phase H pre-review（强制 — task state machine 改造）
- [ ] T-H-13: Phase H post-review（强制）

### Validation

- [ ] T-H-14: 全量回归 0 regression
- [ ] T-H-15: e2e_smoke PASS

---

## Phase G — Atomic 事务边界

### Implementation

- [ ] T-G-1: `EventStore.append_event_pending` API 实现（写 event 但不 commit）
- [ ] T-G-2: `AgentContextStore.save_agent_session_pending` API 实现（写 session 但不 commit）
- [ ] T-G-3: `task_runner._close_subagent_session_if_needed` 改 atomic：append_event_pending → save_session_pending → conn.commit()
- [ ] T-G-4: 失败 rollback 路径实现（log + idempotency_key 守护重试）

### Testing

- [ ] T-G-5: 新增 `test_phase_g_atomic_event_session.py`
  - append_event_pending: pending → commit / rollback 两条路径
  - atomic 事务：fault 注入 (event 后 / session 前) → rollback → idempotency 重试
  - 重试时 idempotency_key 守护不重复 emit

### Codex Review

- [ ] T-G-6: Phase G post-review（atomic 事务边界）

### Validation

- [ ] T-G-7: 全量回归 0 regression
- [ ] T-G-8: e2e_smoke PASS

---

## Phase J — BaseDelegation 公共抽象

### Implementation

- [ ] T-J-1: 新建 `packages/core/src/octoagent/core/models/delegation_base.py`，定义 `BaseDelegation`
- [ ] T-J-2: 修改 `SubagentDelegation` 继承 `BaseDelegation`（保留子类专属字段）
- [ ] T-J-3: `packages/core/src/octoagent/core/models/__init__.py` re-export

### Testing

- [ ] T-J-4: 新增 `test_phase_j_base_delegation.py`
  - BaseDelegation 字段完整性
  - SubagentDelegation 继承不破坏子类语义
  - SubagentDelegation round-trip（json / dict）
- [ ] T-J-5: F097 SubagentDelegation 已有测试 0 regression（grep + 跑）

### Codex Review

- [ ] T-J-6: Phase J post-review

### Validation

- [ ] T-J-7: 全量回归 0 regression
- [ ] T-J-8: e2e_smoke PASS

---

## Phase D — orchestrator.py 拆分（D7 架构债）

### Spec & Design

- [ ] T-D-1: 拆分边界设计：保留 vs 挪入清单（plan.md §9.2）
- [ ] T-D-2: orchestrator.py / dispatch_service.py 共享 helper 处理（移到 _shared.py 或保留 orchestrator）

### Implementation

- [ ] T-D-3: 新建 `apps/gateway/src/octoagent/gateway/services/dispatch_service.py`
- [ ] T-D-4: 移动 A2A 路径函数（`_prepare_a2a_dispatch` / `_persist_a2a_terminal_message` / `_save_a2a_message` / `_write_a2a_message_event`）
- [ ] T-D-5: 移动 dispatch 路径函数（`_dispatch_inline_decision` / `_dispatch_direct_execution` / `_dispatch_owner_self_worker_execution`）
- [ ] T-D-6: 移动 owner self execution（`_register_owner_self_execution_session` / `_mark_owner_self_execution_terminal`）
- [ ] T-D-7: 移动 A2A helper（`_ensure_a2a_agent_runtime` / `_ensure_a2a_agent_session` / `_agent_uri`）
- [ ] T-D-8: 处理 import 链路：`orchestrator.py` re-export `dispatch_service` 关键 API（兼容外部 import）
- [ ] T-D-9: 更新所有内部 import 链路

### Testing

- [ ] T-D-10: 全量回归 ≥ 3355 + 新增（核心：行为零变更）
- [ ] T-D-11: grep 验证 orchestrator.py 行数 ≤ 2000；dispatch_service.py 行数 ≈ 1500
- [ ] T-D-12: grep 验证外部 import orchestrator 模块的功能保持兼容

### Codex Review

- [ ] T-D-13: Phase D pre-review（强制 — 最大文件拆分）
- [ ] T-D-14: Phase D post-review（强制）

### Validation

- [ ] T-D-15: 全量回归 0 regression
- [ ] T-D-16: e2e_smoke PASS

---

## Verify — Final Cross-Phase Codex + Reports

### Final Cross-Phase Codex Review

- [ ] T-V-1: 跑 `/codex:adversarial-review`（foreground，base = origin/master 4441a5a）
- [ ] T-V-2: 处理 high finding（必须 0 high 残留）
- [ ] T-V-3: 处理 medium finding（接受 → 修 / 拒绝 → 显式归档）
- [ ] T-V-4: 处理 low finding（可 ignored，commit message 注明）

### Reports

- [ ] T-V-5: 产出 `codex-review-final.md`（含 finding 闭环表）
- [ ] T-V-6: 产出 `completion-report.md`（含计划 vs 实际对照 + Codex 闭环 + AC 覆盖）
- [ ] T-V-7: 产出 `handoff.md`（给 F099 Ask-Back Channel）
- [ ] T-V-8: 更新 `trace.md`（编排时间线）
- [ ] T-V-9: 更新 CLAUDE.local.md §F098 实施记录

### Final Validation

- [ ] T-V-10: 全量回归 ≥ 3355 + 净增（估计 +300+ 单测）
- [ ] T-V-11: e2e_smoke 5x 循环 PASS（8/8 × 5 = 40/40）
- [ ] T-V-12: 每 Phase Codex review 闭环（0 high 残留）
- [ ] T-V-13: 归总报告呈给用户等拍板（Spawned Task 处理流程）

---

## 任务统计

| Phase | 任务数 | 重点 |
|-------|--------|------|
| Phase 0 | 5 ✓ | 实测侦察 |
| Phase E | 13 | CONTROL_METADATA_UPDATED 引入 |
| Phase F | 7 | ephemeral runtime 独立 |
| Phase B | 8 | A2A target profile 独立 |
| Phase C | 9 | Worker→Worker 解禁 |
| Phase I | 4 | worker audit chain test |
| Phase H | 15 | 终态统一（Codex 强制 pre + post）|
| Phase G | 8 | atomic 事务（EventStore API 演化）|
| Phase J | 8 | BaseDelegation 抽象 |
| Phase D | 16 | orchestrator.py 拆分（Codex 强制 pre + post）|
| Verify | 13 | Final Codex + reports |
| **总计** | **106** | F098 完整流程 |

---

**Tasks 完成。下一步：Pre-Implementation Codex adversarial review，然后 Phase E 实施。**
