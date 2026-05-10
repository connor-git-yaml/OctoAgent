# F098 Phase 0 — 实测侦察报告

**日期**: 2026-05-10
**Worktree**: `feature/098-a2a-mode-worker-to-worker`（HEAD = 4441a5a F097 baseline）
**侦察对象**: A2A 当前路径 / `_enforce_child_target_kind_policy` / dispatch 组织 / F097 5 项推迟项 baseline
**Pattern**: 沿用 F093/F094/F095/F096/F097 5 连侦察实证有效

---

## 1. A2A 当前实施路径（块 A 关键）

### 1.1 模型层（已完整）

`packages/core/src/octoagent/core/models/a2a_runtime.py`：

- **A2AConversation**（line 29）：含完整双向 carrier 字段
  - `source_agent_runtime_id` / `source_agent_session_id`
  - `target_agent_runtime_id` / `target_agent_session_id`
  - `source_agent` / `target_agent`（agent URI）
  - `task_id` / `work_id` / `project_id` / `workspace_id`
  - `status` / `message_count` / `trace_id` / `metadata`
- **A2AMessageRecord**（line 56）：持久化 message 审计记录
- **A2AConversationStatus**：ACTIVE / WAITING_INPUT / COMPLETED / FAILED / CANCELLED
- **A2AMessageDirection**：OUTBOUND / INBOUND

### 1.2 dispatch 路径（orchestrator.py）

`apps/gateway/src/octoagent/gateway/services/orchestrator.py`（**3432 行 — D7 拆分对象**）：

`_prepare_a2a_dispatch`（line 2259）当前实施：

| 维度 | 当前实施 | F098 改造方向 |
|------|---------|--------------|
| source role | 硬编码 `AgentRuntimeRole.MAIN` | 从 envelope/runtime_context 解析（支持 Worker→Worker）|
| target role | 硬编码 `AgentRuntimeRole.WORKER` | 不变（A2A receiver 是 Worker）|
| source session.kind | 硬编码 `MAIN_BOOTSTRAP` | 从 source role 派生（main → MAIN_BOOTSTRAP / worker → WORKER_INTERNAL）|
| target session.kind | 硬编码 `WORKER_INTERNAL` | 不变 OR 引入新 `A2A_RECEIVER` kind（spec 阶段决策）|
| target agent_profile_id | **复用 source_agent_profile_id**（line 2299，**当前 bug**）| target Worker 独立 profile |
| source / target runtime | 各自独立（`_ensure_a2a_agent_runtime` 不同入参）| 不变 |
| Memory namespace | receiver 走 _ensure_memory_namespaces 自己 namespace | 不变（receiver 真独立）|
| Behavior 4 层 | receiver 走 build_task_context 自己加载 BehaviorPack | 不变 |

**关键判断**：A2A receiver runtime + session 已"独立"（不复用 caller 的 active runtime），但 **target_agent_profile_id 复用 source profile** 是当前唯一阻断"receiver 在自己 context 工作"的 bug。
F098 块 B 的核心改造：让 target Worker 加载自己的 AgentProfile（按 requested_worker_profile_id 或 worker_capability 派生）。

### 1.3 调用点

orchestrator.py 内：
- `dispatch` (line 524) → 路由到 `_prepare_a2a_dispatch`（A2A 模式）或 `_dispatch_inline_decision` 等其他模式
- `_prepare_a2a_dispatch` (line 2259) → A2A 主路径
- `_persist_a2a_terminal_message` (line 2119, 2194) → 终态消息持久化
- `_save_a2a_message` (line 2467) → 消息审计

### 1.4 e2e 测试存在

`apps/gateway/tests/e2e_live/test_e2e_delegation_a2a.py`（域 #10）：
- 验证 DispatchEnvelope / a2a_messages / parent_task_id 4 子断言
- pytestmark = e2e_full + e2e_live（不在 e2e_smoke 5x 默认）

---

## 2. `_enforce_child_target_kind_policy` 调用点（块 C 解禁起点）

### 2.1 函数定义

`apps/gateway/src/octoagent/gateway/services/capability_pack.py:1355`（F092 提为 public）：

```python
@staticmethod
def enforce_child_target_kind_policy(target_kind: str) -> None:
    """Worker→Worker 委托禁止策略（F084 引入；F092 Phase B 提为 public）。
    F098 H3-B 解绑会移除此约束（Worker→Worker 解绑），F092 不动语义。
    """
    normalized_target_kind = str(target_kind).strip().lower()
    if normalized_target_kind != DelegationTargetKind.WORKER.value:
        return
    try:
        execution_context = get_current_execution_context()
    except RuntimeError:
        return
    runtime_kind = str(execution_context.runtime_kind or "").strip().lower()
    runtime_turn_kind = ""
    if execution_context.runtime_context is not None:
        runtime_turn_kind = str(
            execution_context.runtime_context.turn_executor_kind.value
        ).strip().lower()
    if runtime_kind == RuntimeKind.WORKER.value or runtime_turn_kind == TurnExecutorKind.WORKER.value:
        raise RuntimeError(
            "worker runtime cannot delegate to another worker; use a subagent target instead"
        )
```

### 2.2 调用点（生产代码 1 处 + 注释引用 3 处 + 测试 mock 2 处）

| 位置 | 类型 | F098 处理 |
|------|------|----------|
| `capability_pack.py:1252` | 生产 — `_launch_child_task` 主调用 | **删除** + 函数本身删除 |
| `capability_pack.py:1355` | 生产 — 函数定义 | **删除整个函数** |
| `delegation_plane.py:81` | 注释引用 | 更新文字（移除 enforce 提及）|
| `delegation_plane.py:976` | 注释引用 | 更新文字 |
| `delegation_plane.py:1082` | 注释引用 | 更新文字 |
| `test_capability_pack_phase_d.py:71` | 测试 mock | **删除 mock + 改 worker→worker 正面测试** |
| `test_capability_pack_phase_d.py:261` | 测试断言 | **删除断言 + 改正面测试** |

### 2.3 解禁影响

删除后：
- Worker A 调用 `delegate_task(target_kind="worker", ...)` 不再 raise
- 走 `plane.spawn_child` 创建 child Worker task
- child Worker 在自己 runtime + session 工作（已是 A2A receiver 现成机制）
- audit chain 自动追踪（child Worker 的 runtime_id / session_id 独立）

但 **F098 范围限制**：Worker → Worker 是"通信权"解禁，**不是 spawn 新 Worker**。当前 baseline `delegate_task` 工具实际走 `plane.spawn_child` → 创建 child Worker task → enqueue。如果保持当前语义（Worker 可以"创建" child Worker task），是 spawn 权解禁；如果限制"已存在 Worker"（A2A 消息发送），则需引入新 worker.send_message 工具。

**spec 阶段决策**：F098 范围按"通信权"理解，主路径是 Worker A 通过 worker.delegate_task 创建 child Worker task（target_kind=worker），child Worker 独立运行（与 main 创建 Worker 等价）。**不引入新 spawn 工具，仅删除 enforce 限制**。

---

## 3. dispatch 路径组织（块 D D7 拆分起点）

### 3.1 orchestrator.py 函数清单（前 40 个）

3432 行（巨型文件）。主要职责区：

| 函数 | 行 | 职责 |
|------|----|------|
| `OrchestratorRouter.evaluate` / `route` | 233 / 307 | 路由策略 |
| `dispatch` | 524 | 主入口（路由 + 编排）|
| `dispatch_prepared` | 493 | 已准备 envelope 派发 |
| `_dispatch_inline_decision` | 1072 | inline 决策派发 |
| `_dispatch_direct_execution` | 1133 | 直接执行派发 |
| `_dispatch_owner_self_worker_execution` | 1243 | owner self worker 派发 |
| `_prepare_single_loop_request` | 748 | single loop 请求准备 |
| `_prepare_a2a_dispatch` | 2259 | **A2A 主路径** |
| `_persist_a2a_terminal_message` | 2119/2194 | A2A 终态消息持久化 |
| `_save_a2a_message` | 2467 | A2A 消息审计 |
| `_resolve_routing_decision` | 1035 | 路由决策 |
| `_normalize_requested_worker_lens` | 688 | requested worker normalize |
| `_resolve_single_loop_*` | 918 / 941 / 991 | single loop 解析 |
| `_register_owner_self_execution_session` | 1341 | session 注册 |
| `_mark_owner_self_execution_terminal` | 1383 | 终态标记 |
| `_build_request_runtime_hints` | 1473 | runtime hints 构造 |
| `_build_decision_trace_metadata` | 1562 | trace metadata |

### 3.2 块 D 拆分提案（spec 阶段细化）

`orchestrator.py` 拆出 `dispatch_service.py`：

- **保留 orchestrator.py 中（编排层）**：dispatch / dispatch_prepared / route / 决策、policy、approval、worker handler 注册
- **挪入 dispatch_service.py（路由 + target resolution）**：
  - `_prepare_a2a_dispatch`
  - `_persist_a2a_terminal_message`
  - `_save_a2a_message`
  - `_write_a2a_message_event`
  - `_dispatch_inline_decision`
  - `_dispatch_direct_execution`
  - `_dispatch_owner_self_worker_execution`
  - 相关 `_ensure_a2a_*` helper（如有）
- **职责边界**：orchestrator 编排（决策 + 编排），dispatch_service 路由 + target resolution（构建 envelope + ensure runtime/session + 写 A2A conversation/message）

预估拆分后：orchestrator.py ≤ 2000 行，dispatch_service.py ≈ 1500 行（**spec 阶段精确化**）。

---

## 4. F097 5 项推迟项 baseline 行为

### 4.1 P1-1 USER_MESSAGE 复用污染（块 E，high）

**写入点 1**：`task_runner.py:287` `_emit_subagent_delegation_init_if_needed` (line 203)

```python
type=EventType.USER_MESSAGE,  # 复用 USER_MESSAGE 承载 control_metadata
text_preview="[subagent delegation metadata]",
text_length=0,
text="",
control_metadata={...subagent_delegation init...},
```

**写入点 2**：`agent_context.py:2628` B-3 backfill（line 2589-2647）

```python
type=EventType.USER_MESSAGE,
text_preview="[subagent delegation session backfill]",
control_metadata={...preserved + subagent_delegation w/ child_agent_session_id...}
```

**受影响 consumer**：

| Consumer | 文件:行 | 行为 |
|----------|---------|------|
| `_load_conversation_turns` | `context_compaction.py:807` | 当 USER_MESSAGE 处理 → text_preview 加入 ConversationTurn → 污染对话历史 |
| `merge_control_metadata` | `connection_metadata.py:141` | 过滤 `event.type == USER_MESSAGE` → 取 control_metadata（这是预期行为，非污染）|
| `get_latest_input_metadata` | `task_service.py:2632` | 取最近 USER_MESSAGE → input_metadata |
| `chat.py:208` / `telegram.py:851` / `operator_actions.py:803/844` | 多处 | UI 路径过滤 USER_MESSAGE 用作渲染 |

**修复方向**（spec 阶段 3 选 1）：
- **A（推荐）**：引入 `EventType.CONTROL_METADATA_UPDATED`（only carries control_metadata）
  - 写入点 task_runner.py:287 + agent_context.py:2628 → 改 CONTROL_METADATA_UPDATED
  - `merge_control_metadata` 改：合并 USER_MESSAGE + CONTROL_METADATA_UPDATED（保持 latest 时序）
  - `_load_conversation_turns` 不变（USER_MESSAGE 过滤天然不取 CONTROL_METADATA_UPDATED）
  - 其他 USER_MESSAGE consumer 自然不受影响（不再写 marker text USER_MESSAGE）
- B：USER_MESSAGE 加 `is_synthetic_marker` 标记，所有 consumer 跳过 → 多处改动
- C：SubagentDelegation 持久化走 task store metadata（CL#16 原始决策）→ 大改

**spec 阶段决策**：选 A，工作量 ~3-5h。

### 4.2 P1-2 ephemeral subagent runtime 复用 caller worker runtime（块 F，high）

**根因**：`agent_context.py:2237` `_ensure_agent_runtime`（前面读完整）

```python
worker_profile_id = str(
    resolve_delegation_target_profile_id(request.delegation_metadata)
).strip()
if not worker_profile_id and role is AgentRuntimeRole.WORKER:
    worker_profile_id = str(
        agent_profile.metadata.get("source_worker_profile_id", "")
    ).strip()
# ... 若仍空，find_active_runtime(project, role=WORKER, profile_id="") → 复用 caller worker runtime
existing = await self._stores.agent_context_store.find_active_runtime(
    project_id=project_id,
    role=role,
    worker_profile_id=worker_profile_id,
    agent_profile_id=agent_profile.profile_id,
)
```

**问题**：subagent ephemeral profile 没有 `source_worker_profile_id` → worker_profile_id 为 `""` → find_active_runtime 用 (project, WORKER, "") 查找 → 复用 caller worker 的 active runtime。

**修复方向**（spec 阶段 2 选 1）：
- **A（推荐）**：subagent runtime 用独立 query key，跳过 find_active_runtime 复用
  - 在 `_ensure_agent_runtime` 检测 `_is_subagent_session` 信号（已有 target_kind == subagent）
  - subagent 路径：跳过 find_active_runtime → 每次新建 runtime（runtime_id = `runtime-{ULID()}`）
  - 关键：runtime_metadata 加 `subagent_delegation_id` 字段做 audit 关联
- B：用 SubagentDelegation.delegation_id 派生独立 query key

**与 F098 H3-B 协同**：F098 块 B 的 A2A receiver runtime 已独立（`_ensure_a2a_agent_runtime` 不走 `_ensure_agent_runtime` 路径），**问题仅限 subagent 路径**。F098 修复只需在 `_ensure_agent_runtime` 检测 subagent 路径并跳过 find_active_runtime 复用。

**spec 阶段决策**：选 A，工作量 ~3-5h。

### 4.3 P2-3 事务边界（块 G，medium）

**当前 baseline**（task_runner.py:776 `_close_subagent_session_if_needed`，F097 Phase B-4 已修）：

- 顺序：先 emit SUBAGENT_COMPLETED event（idempotency_key 守护）→ 后 save AgentSession CLOSED
- 缓解：失败模式从"session closed 但无事件"→"事件存在但 session 未 CLOSED"（audit chain 优先）
- **仍是两次 commit**：line 881 `append_event_committed` 一次 + line 899 `await commit()` 一次

**修复方向**：
- **A（推荐）**：单一事务包裹 event append + session save
  - 改用 `append_event_only`（不 commit）+ `save_agent_session`（不 commit）+ 最后一次 commit
  - 如果失败则全部 rollback（atomic）
  - 需要在 EventStore 引入 `append_event_pending`（不立即 commit）API
- B：保持当前两步顺序，加上 first-cleanup 失败重试机制

**spec 阶段决策**：选 A（atomic 是最干净的），但工作量较大（涉及 EventStore API 演化）。**block H 完成后做**（cleanup hook 已挪到 task state machine 终态层后再统一收口事务）。

### 4.4 P2-4 终态统一层（块 H，medium，大）

**当前 baseline**（F097 Phase B-4 已在 task_runner 多处补 cleanup）：

cleanup 触发路径：
- `_notify_completion` (task_runner.py:761) → 通知前 cleanup
- dispatch exception (task_runner.py:679) → 异常时 cleanup
- mark_failed 非终态分支 (task_runner.py:711) → 非终态时 cleanup
- 其他终态路径（mark_running_task_failed_for_recovery / mark_running_task_cancelled_for_runtime 等）**当前未触发 cleanup**

**问题**：分散 + 未覆盖所有终态路径。

**修复方向**：
- **A（推荐）**：cleanup hook 挪到 `task_service._write_state_transition` 的 cleanup_lock 后（line 2761）
  - 在 task service 注入 `subagent_session_close_callback`（task_runner 提供，task_service 调用）
  - 移除 task_runner 各处手动 `_close_subagent_session_if_needed` 调用
  - 所有终态路径统一触发（task state machine 是终态权威）
- B：保持当前分散 + 补全所有终态路径

**关键风险**：task_service 调用 task_runner 是反向依赖。需要 callback 注入或 weak ref 设计。

**spec 阶段决策**：选 A，但 task state machine 改造影响面大——必走 Codex review。

### 4.5 AC-F1 worker_capability audit chain（块 I，medium）

F096 Phase F audit chain 测试仅 cover main agent dispatch 路径。F098 实施 worker→worker A2A 解禁（块 C）后，复用 F096 audit chain 测试结构补 worker_capability 路径：

```python
async def test_f096_audit_chain_worker_dispatch():
    # arrange: 创建 WorkerProfile + dispatch_metadata.worker_capability
    # act: 调用 delegate_task tool 触发 worker AgentRuntime 创建
    # assert:
    #   - BEHAVIOR_PACK_LOADED.agent_kind == "worker"
    #   - AgentRuntime.kind == WORKER
    #   - audit chain 四层身份对齐（同 F096 audit chain test）
```

复用：F096 audit chain test 的 Layer 1-4 验证结构（已稳定 in F096 Phase F）。

---

## 5. 架构设计点（spec 阶段必决）

### 5.1 BaseDelegation 公共抽象

F097 SubagentDelegation 是独立 model（packages/core/src/octoagent/core/models/）。F098 引入 A2A 长生命周期 WorkerDelegation 时考虑提取父类：

| 字段 | SubagentDelegation | WorkerA2ADelegation |
|------|-------------------|---------------------|
| delegation_id | ✓ | ✓ |
| parent_task_id | ✓ | ✓ |
| parent_work_id | ✓ | ✓ |
| child_task_id | ✓ | ✓ |
| spawned_by | ✓ | ✓ |
| created_at | ✓ | ✓ |
| closed_at | ✓ | ✓ |
| caller_agent_runtime_id | ✓ | ✓ |
| caller_project_id | ✓ | receiver 自己 project（不同语义）|
| child_agent_session_id | ✓ | ✓ |

**spec 阶段决策**：
- 选项 A：提取 `BaseDelegation` 父类，子类 SubagentDelegation / WorkerA2ADelegation
- 选项 B：保持各自独立，重复字段（更简单但 DRY 差）

**推荐 A**：抽象足够清晰（共享 7+ 字段），子类区分语义（lifecycle / project / context 等）。

### 5.2 agent_kind enum 演化

F097 当前：`Literal["main", "worker", "subagent"]`（BehaviorPackLoadedPayload.agent_kind 已是 str，不是 Literal）。

F098 选项：
- A：新增 `worker_a2a` enum 值（明确区分 main_delegate worker vs A2A receiver worker）
- B：保持 `worker`，通过 delegation_mode 字段区分（"main_delegate" vs "subagent" vs "a2a"）

**推荐 B**：保持 agent_kind 简单（worker 就是 worker），用 delegation_mode 区分 dispatch 来源更合理。BEHAVIOR_PACK_LOADED.agent_kind 不动，audit 通过 delegation_mode 字段做更细粒度分析。

---

## 6. 测试 baseline 数

- `find octoagent -name "test_*.py" -not -path "*/e2e_live/*"`：309 个测试文件
- F097 完成累计 +103 测试 → **3355 passed**（F097 baseline）
- F098 baseline = F097 baseline = origin/master 4441a5a

**验收**：F098 完成后全量回归 ≥ 3355 + 0 regression。

---

## 7. F098 Phase 顺序建议（基于实测）

按用户给的 prompt：A 实测 → E P1-1 → F P1-2 → B → C → I → G → H → D。

**实测验证后调整**：

| Phase | 内容 | 依赖 | 关键风险 |
|-------|------|------|----------|
| Phase 0 | 实测侦察（本文件）| — | 已完成 ✓ |
| Phase E | P1-1 USER_MESSAGE 修复（CONTROL_METADATA_UPDATED）| 0 | merge_control_metadata 演化兼容 |
| Phase F | P1-2 ephemeral runtime 修复 | 0 | _ensure_agent_runtime subagent 信号 |
| Phase B | A2A receiver target profile 独立加载 | F | target_agent_profile_id 资源解析 |
| Phase C | Worker→Worker A2A 解禁 | B | enforce 删除 + 测试调整 |
| Phase I | AC-F1 worker audit chain test | C | F096 audit chain 复用 |
| Phase G | 事务边界（先实现 cleanup hook 挪迁）| H 前 | EventStore atomic API |
| Phase H | 终态统一 cleanup hook（task state machine）| 无强依赖（与 G 独立）| task_service ↔ task_runner 反向依赖 callback |
| Phase D | D7 拆分 orchestrator → dispatch_service | 所有上面 | 大量文件改动 + import 更新 |

**Phase G/H 顺序**：H 先做（cleanup hook 挪迁，结构改造），G 后做（事务边界，G 受益于 H 已统一的 hook）。

**Phase D 最后**：所有功能改动稳定后再做大规模文件拆分（避免 rebase 冲突）。

---

## 8. 关键文件列表（实施时改动范围）

### 8.1 新建

- `packages/core/src/octoagent/core/models/delegation_base.py`（如果选 BaseDelegation 路径）
- `packages/core/src/octoagent/core/models/worker_a2a_delegation.py`（如果引入 WorkerA2ADelegation）
- `apps/gateway/src/octoagent/gateway/services/dispatch_service.py`（Phase D 拆分目标）
- `apps/gateway/tests/services/test_*_phase_*.py`（每 Phase 测试）

### 8.2 修改

| 文件 | Phase | 改动 |
|------|-------|------|
| `packages/core/src/octoagent/core/models/enums.py` | E | 新增 `EventType.CONTROL_METADATA_UPDATED` |
| `packages/core/src/octoagent/core/models/payloads.py` | E | 新增 `ControlMetadataUpdatedPayload` |
| `apps/gateway/src/octoagent/gateway/services/connection_metadata.py` | E | `merge_control_metadata` 合并两类事件 |
| `apps/gateway/src/octoagent/gateway/services/task_runner.py` | E + H | `_emit_subagent_delegation_init_if_needed` 改 event type；移除手动 cleanup 调用 |
| `apps/gateway/src/octoagent/gateway/services/agent_context.py` | E + F | B-3 backfill 改 event type；`_ensure_agent_runtime` subagent 路径独立 |
| `apps/gateway/src/octoagent/gateway/services/orchestrator.py` | B + D | A2A target profile 独立；D7 拆分挪迁 |
| `apps/gateway/src/octoagent/gateway/services/capability_pack.py` | C | 删除 `enforce_child_target_kind_policy` + 调用点 |
| `apps/gateway/src/octoagent/gateway/services/delegation_plane.py` | C | 注释更新 |
| `apps/gateway/src/octoagent/gateway/services/task_service.py` | G + H | `_write_state_transition` 加 cleanup hook + 事务边界 atomic |
| `packages/core/src/octoagent/core/store/event_store.py` | G | `append_event_pending` API（如选事务原子化路径）|

### 8.3 删除

- F092 残留 `enforce_child_target_kind_policy` 函数 + 调用点（块 C）
- task_runner.py 各处 `_close_subagent_session_if_needed` 手动调用（块 H 后）

---

## 9. e2e_smoke baseline

F097 验收：8/8 PASS × 5 次循环 = 40/40。F098 各 Phase 后必须保持。
当前 worktree HEAD = 4441a5a 等同 origin/master。

---

## 10. 实测结论（spec 阶段输入）

| 块 | 实测发现 | spec 阶段决策 |
|----|---------|--------------|
| A | A2A 模型 + dispatch 已存在；target_agent_profile_id 复用 source 是当前唯一阻断 | 块 B 仅需修复 target profile 独立加载 |
| _enforce_child_target_kind_policy | 1 处生产调用 + 3 处注释 + 2 处测试 mock | 块 C 删除 + 测试改正面 |
| dispatch | orchestrator.py 3432 行；A2A 主路径 (2259) 在巨型文件中 | 块 D 拆 dispatch_service.py（≈ 1500 行）|
| P1-1 | USER_MESSAGE 复用 — 引入 CONTROL_METADATA_UPDATED 路径最干净 | 块 E 选项 A |
| P1-2 | ephemeral runtime 复用 — 检测 subagent 信号跳过 find_active_runtime 即可 | 块 F 选项 A |
| P2-3 | 当前 2 次 commit；F097 已颠倒顺序缓解 | 块 G 选项 A（atomic）|
| P2-4 | task_runner 多处手动 cleanup；未覆盖所有终态路径 | 块 H 选项 A（task state machine 触发）|
| AC-F1 | F096 audit chain 已稳定 | 块 I 复用结构 |
| BaseDelegation | F097 SubagentDelegation 独立 | 选项 A：提取父类（spec 阶段最终决定）|
| agent_kind enum | F097 已是 str | 不动；用 delegation_mode 区分 |

---

**Phase 0 实测侦察完成。下一步：编写 spec.md（specify agent）。**
