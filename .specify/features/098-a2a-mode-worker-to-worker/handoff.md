# F098 → F099 Handoff

**Source**: F098 A2A Mode + Worker↔Worker（feature/098-a2a-mode-worker-to-worker，9 commits）
**Target**: F099 Ask-Back Channel + Source Generalization（M5 阶段 2 第 3 个 Feature）

---

## F098 完成 / 推迟项

### F098 已完成

- ✅ H3-B 主责（Phase B-1/B-2 source+target 双向独立加载）
- ✅ H2 完整对等性（Phase C Worker→Worker 解禁 + enforce 删除）
- ✅ F097 5 项推迟项全部接管（P1-1 块 E / P1-2 块 F / P2-3 块 G / P2-4 块 H / AC-F1 块 I）
- ✅ BaseDelegation 公共抽象（块 J）
- ⏳ Phase D（orchestrator.py 拆 dispatch_service.py）显式归档推迟到 F107

### F098 留给 F099 的接入点

#### 1. CONTROL_METADATA_UPDATED 事件类型已稳定

F099 ask-back / source 泛化时如需承载 control_metadata 更新（如 worker.ask_back 工具状态切换），可复用 EventType.CONTROL_METADATA_UPDATED + ControlMetadataUpdatedPayload。

定义位置：
- `packages/core/src/octoagent/core/models/enums.py` (EventType.CONTROL_METADATA_UPDATED)
- `packages/core/src/octoagent/core/models/payloads.py` (ControlMetadataUpdatedPayload)
- `merge_control_metadata` 已支持合并 USER_MESSAGE + CONTROL_METADATA_UPDATED 两类事件（connection_metadata.py:141）

**source 字段约定**（candidates，非强制 enum）：
- `subagent_delegation_init`（task_runner._emit_subagent_delegation_init_if_needed）
- `subagent_delegation_session_backfill`（agent_context._ensure_agent_session B-3）
- F099 可扩展：`worker_ask_back` / `source_type_changed` 等

#### 2. A2A source 派生已稳定（B-1）

F099 source_type 泛化（butler / user / worker / automation）可在 `_resolve_a2a_source_role`（orchestrator.py）基础上扩展：

当前实现（F098 Final Codex review P1 闭环）：
```python
# 输入：runtime_context (RuntimeControlContext)
# 信号：runtime_context.turn_executor_kind (TurnExecutorKind enum)
# 派生：(role, session_kind, agent_uri)
```

F099 扩展点：
- 引入 `source_type` 字段到 RuntimeControlContext / envelope_metadata
- `_resolve_a2a_source_role` 加新判断：
  - `source_type == "automation"` → AUTOMATION runtime（如未来引入）
  - `source_type == "butler"` → BUTLER runtime（如 Hermes Agent 模式落地）
- 当前 turn_executor_kind 派生覆盖 main / worker / subagent 三种来源（保持向后兼容）

#### 3. A2A target Worker profile 解析已稳定（B-2）

`_resolve_target_agent_profile`（orchestrator.py）通过 `_delegation_plane.capability_pack.resolve_worker_binding` 接入。F099 worker.ask_back 工具如需指定 target Worker，可复用此路径。

#### 4. Worker→Worker 通信权已解禁（C）

F099 引入 `worker.ask_back` / `worker.send_message` 等新工具时，可直接走 baseline `delegate_task(target_kind=worker)` 路径（已解禁）。**无需引入新 spawn 工具**——F098 OD-7 决定保持"spawn + 通信合一"。

#### 5. 终态统一 callback 机制（H）

F099 如需在 task 终态触发其他 cleanup（如 ask-back conversation 关闭），可注册到 `TaskService._terminal_state_callbacks`：

```python
await TaskService.register_terminal_state_callback(your_cleanup_callback)
# ...
# shutdown 时
await TaskService.unregister_terminal_state_callback(your_cleanup_callback)
```

设计要点：
- class-level callback list（多 TaskService 实例共享）
- 幂等注册（按 callback identity 检测重复）
- 异常隔离（cleanup 失败不影响 state transition）
- shutdown 必须 unregister 防泄漏

#### 6. BaseDelegation 公共抽象（J）

F099 如需引入新的 delegation model（如 AskBackRequest / AssignmentDelegation），继承 BaseDelegation 父类即可：

```python
from octoagent.core.models.delegation import BaseDelegation, DelegationTargetKind
from typing import Literal

class AskBackDelegation(BaseDelegation):
    """F099 worker.ask_back 委托（spawn-and-die，回调原 caller）。"""
    callback_target_runtime_id: str = ""  # 子类专属
    callback_question: str = ""  # 子类专属
    target_kind: Literal[...] = ...  # 子类 Literal 收紧
```

共享 7+ 字段：delegation_id / parent_task_id / parent_work_id / child_task_id /
caller_agent_runtime_id / spawned_by / created_at / closed_at。

---

## F098 设计阶段决策（F099 必读）

### OD-1 ~ OD-9 GATE_DESIGN 锁定（spec.md §0）

F099 实施时 **不得偏离** F098 已锁的 9 项 OD：

- OD-1: P1-1 修复 = CONTROL_METADATA_UPDATED 路径（不再用 USER_MESSAGE 复用）
- OD-2: P1-2 修复 = subagent 路径跳过 find_active_runtime
- OD-3: P2-3 = atomic 事务（F098 复用 append_event_committed task_seq 重试 + idempotency 守护，真正 single-transaction 留 F107）
- OD-4: P2-4 = task_service._write_state_transition 终态 callback
- OD-5: BaseDelegation 公共抽象提取 ✅
- OD-6: agent_kind enum 不动，用 delegation_mode 区分 ✅
- OD-7: Worker→Worker 解禁 = spawn+通信合一（不引入新工具）
- OD-8: Phase H 先 G 后（结构改造先）
- OD-9: A2A target Worker profile 通过 capability_pack.resolve_worker_binding

### 不在 F098 / F099 / F107 范围（长期跟踪）

- main direct 路径走 AGENT_PRIVATE → F107
- WorkerProfile 完全合并 → F107
- BehaviorPack share_with_workers 字段彻底删除 → F107
- 多用户 / 团队 / 家庭 A2A 隔离 → M7+

---

## F098 已知遗留项（F099 / F107 评估）

### 已知 LOW（不阻 F099 启动）

1. **Phase G 仍是 2 commits**（event commit + session commit）：
   - F097 已颠倒顺序 + idempotency 守护重试缓解
   - F098 Final Codex P2 修复保留 task_seq 重试，atomic 妥协推迟 F107
   - 真正 single-transaction 需 EventStore.append_event_pending API 演化

2. **Phase H AC-H3 未完全达成**（task_runner 仍有手动 cleanup 调用）：
   - 当前手动调用作为 fallback（cleanup 内部已幂等 + 非终态检测）
   - 影响：grep 结果不"干净"，但功能等价
   - F099 / F107 顺手清

3. **Phase B-1 source 派生信号源**：
   - 当前优先 RuntimeControlContext.turn_executor_kind
   - fallback 到 envelope_metadata.source_runtime_kind / source_turn_executor_kind
   - F099 source 泛化时可能需要扩展 RuntimeControlContext 加 source_type 字段

### Final Codex review 闭环情况

| 严重 | 处理 |
|------|------|
| 1 high (P1-1 source 派生不生效) | ✅ 已修复（用 turn_executor_kind） |
| 3 medium (P2-1/2/3) | ✅ 全闭环（capability_pack 真实接入 / task_seq 重试保留 / shutdown 注销时机） |

无 high known issue 归档到 F099。F098 实施达成度高于 F097（F097 归档 2 high known issue）。

---

## 测试基础设施可借鉴

F098 测试可借鉴的模式：

- `test_phase_e_control_metadata_updated.py` (12 单测): merge_control_metadata 合并两类 events + 向后兼容
- `test_phase_f_ephemeral_runtime.py` (6 单测): subagent 路径跳过 find_active_runtime
- `test_phase_b_a2a_source_target.py` (12 单测): A2A source 派生 + target profile 解析
- `test_phase_h_terminal_callback.py` (8 单测): class-level callback + 幂等 + 生命周期
- `test_phase_g_atomic_cleanup.py` (3 单测): cleanup 端到端集成测
- `test_phase_j_base_delegation.py` (8 单测): BaseDelegation 抽象 + 子类继承

---

## 关键引用

- 完整 spec：[spec.md](spec.md) v0.2 GATE_DESIGN + Codex 闭环
- 实施计划：[plan.md](plan.md) v0.2 9 Phase
- 任务清单：[tasks.md](tasks.md) (106 任务)
- 完成报告：[completion-report.md](completion-report.md)
- Pre-Impl Codex review：[codex-review-spec-plan.md](codex-review-spec-plan.md)
- Final Codex review：[codex-review-final.md](codex-review-final.md)
- Phase D 推迟说明：[phase-d-deferral.md](phase-d-deferral.md)
