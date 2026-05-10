# F098 A2A Mode + Worker↔Worker — Spec（v0.2 Pre-Impl Codex Review 闭环）

**Feature Branch**: `feature/098-a2a-mode-worker-to-worker`
**Created**: 2026-05-10
**Status**: Draft
**M5 Stage**: 阶段 2 第 2 个 Feature（继 F097 H3-A 后）
**Upstream**: F097（已合入 master 4441a5a）/ F096（audit chain 已稳定）/ F092（plane.spawn_child 主路径已统一）
**Downstream**: F099 Ask-Back Channel + Source Generalization
**Input Prompt**: F098 A2A Mode + Worker↔Worker（H3-B + 承接 F096/F097 共 5 项推迟，M5 阶段 2 最复杂 Feature）

---

## 0. GATE_DESIGN 候选决策（待 plan 阶段拍板）

下列决策点在 spec → clarify → plan 路径上需要 GATE_DESIGN 锁定，**plan 阶段不得偏离**：

| 决策点 | 候选 | 推荐 | 备注 |
|--------|------|------|------|
| **OD-1 P1-1 修复路径** | A=新 EventType `CONTROL_METADATA_UPDATED` / B=USER_MESSAGE 加 `is_synthetic_marker` / C=持久化走 task store metadata | **A** | A 最干净，consumer 影响小（仅 `merge_control_metadata` 演化）|
| **OD-2 P1-2 修复路径** | A=subagent 路径跳过 `find_active_runtime` / B=用 delegation_id 派生独立 query key | **A** | A 改动最小（仅在 `_ensure_agent_runtime` 增加 subagent 信号检测）|
| **OD-3 P2-3 事务边界** | A=单一 atomic 事务（EventStore 引入 `append_event_pending`）/ B=保持当前两步 + 重试 | **A** | A 最干净，但需 EventStore API 演化；B 兼容性更高 |
| **OD-4 P2-4 终态触发层** | A=cleanup hook 挪到 `task_service._write_state_transition` / B=保持 task_runner 多处手动 | **A** | A 是真正的终态权威；B 是当前 baseline |
| **OD-5 BaseDelegation 公共抽象** | A=提取父类 / B=保持各自独立 | **A** | A 共享 7+ 字段语义清晰 |
| **OD-6 agent_kind enum 演化** | A=新增 `worker_a2a` / B=保持 `worker` 通过 delegation_mode 区分 | **B** | B 更简单，audit 通过 delegation_mode 字段做更细粒度 |
| **OD-7 Worker→Worker 解禁语义** | A=完整解禁（spawn + 通信权）/ B=仅通信权（已有 Worker A2A）/ C=仅 spawn 权 | **A** | F098 当前 baseline `delegate_task(target_kind=worker)` 已是 spawn 路径；解禁=允许 Worker 调用此工具 |
| **OD-8 Phase G/H 顺序** | A=H 先 G 后（结构改造先）/ B=G 先 H 后（事务先）/ C=合并到一个 Phase | **A** | H 把 cleanup 挪到 task state machine 终态层；G 受益于 H 已统一的 hook（事务包装单一入口）|
| **OD-9 A2A target Worker profile 加载** | A=按 requested_worker_profile_id 独立加载 / B=按 worker_capability 派生 / C=保持 source 复用（baseline）| **A** | 是 H3-B 核心：receiver 在自己 context 工作的根本前提 |

**决策建议**：用户在 GATE_DESIGN 阶段对以上 9 项做 batch 拍板（推荐全部接受 spec 推荐）。

---

## 1. 目标（Why）

F098 是 M5 阶段 2 第 2 个 Feature，**主责 H3-B（A2A 真 P2P）+ H2 完整对等性的 Worker→Worker 解禁**，同时承接 F097 / F096 共 5 项推迟项。

**核心问题**（来自 CLAUDE.local.md §M5 战略规划 §三条核心设计哲学）：

1. **H3-B（A2A receiver 在自己 Context 工作）**：当前 A2A 实施 receiver runtime + session 已独立，但 `target_agent_profile_id` 复用 source profile（orchestrator.py:2299）是当前唯一阻断"receiver 在自己 context 工作"的 bug
2. **H2（Worker→Worker 完整对等性）**：`capability_pack.enforce_child_target_kind_policy` 硬禁止 Worker→Worker 委托（F084 引入；F092 提为 public 待 F098 解绑），违反"Worker = 主 Agent − {hire/fire/reassign Worker} − {user-facing 表面}"对等性
3. **F097 5 项推迟项**：P1-1（USER_MESSAGE 复用污染）/ P1-2（ephemeral runtime 复用）/ P2-3（事务边界）/ P2-4（终态统一层）/ AC-F1（worker audit chain 集成测）—— 详见 F097 codex-review-final.md
4. **D7 架构债**：orchestrator.py 3432 行（最大单文件），dispatch 路径与编排路径职责混杂

**预期收益**：
- A2A receiver 真"在自己 context 工作"——Worker A 给 Worker B 发消息后 B 在自己的 Project / Memory / Behavior 4 层下处理
- Worker A 可委托给 Worker B（解禁后）—— H2 完整对等性达成
- USER_MESSAGE 不再被复用承载 control_metadata —— event 模型概念清晰
- ephemeral subagent runtime 独立 —— audit chain 不混叠到 caller worker runtime
- task state machine 终态触发 cleanup —— 所有终态路径自动 cleanup，无遗漏
- session.save + event.append 同事务 —— 失败模式从"两边不一致"→"两边同步 rollback"
- orchestrator.py 拆分 —— 编排 vs 路由职责边界清晰

---

## 2. 已通项（Baseline-Already-Passed）

实测发现 F097 baseline 已实现的部分（沿用 F093/F094/F095/F096/F097 5 连"baseline 已部分通"pattern）：

| 已通项 | 实测点 | 影响 F098 |
|--------|--------|-----------|
| **A2A 模型层完整** | `a2a_runtime.py`：A2AConversation 含 source/target runtime+session 字段 | F098 不动 model 层 |
| **A2A receiver runtime 已独立** | `_ensure_a2a_agent_runtime` 不走 `_ensure_agent_runtime`，target_runtime 用独立 ULID | F098 不需修复 receiver runtime（仅修 ephemeral subagent，**P1-2 范围**）|
| **A2A receiver session 已独立** | target_session.kind = WORKER_INTERNAL，agent_session_id 独立 | 不动 session kind |
| **A2A Memory namespace 独立** | receiver 走自己的 build_task_context → _ensure_memory_namespaces → 自己的 namespace | 不动 namespace 路径 |
| **A2A Behavior 4 层独立** | receiver build_task_context 自己 resolve_behavior_pack | 不动 behavior 加载 |
| **F097 SubagentDelegation 模型已稳定** | spawn-and-die / shared context 7 维度概念已分离 | F098 BaseDelegation 提取时不动 SubagentDelegation 子类语义 |
| **F092 plane.spawn_child 已统一** | `delegation_plane.py:1058` 唯一生产入口 | F098 不改 spawn 编排，仅改 enforce 策略（块 C）|
| **F097 SUBAGENT_COMPLETED event 已 emit** | task_runner._close_subagent_session_if_needed 已写 SUBAGENT_COMPLETED | F098 块 H 仅挪迁触发点，不改 emit |
| **F096 audit chain 4 层对齐已稳定** | profile_id ↔ runtime_id ↔ LOADED.agent_id ↔ RecallFrame.agent_runtime_id | F098 块 I 复用 F096 测试结构 |
| **F097 P2-1 已修** | B-3 backfill USER_MESSAGE preserve normalize control_metadata | F098 块 E 改 event type 后此修复延续语义 |

**结论**：F098 实际改动比 prompt 列举的 9 块工作量小（baseline 已通 6 项）。主要工作集中在：
- 块 B：A2A target profile 独立加载（一处 bug fix）
- 块 C：删除 enforce + 调用点
- 块 E：CONTROL_METADATA_UPDATED event type 引入 + 写入点改造
- 块 F：subagent 路径独立 runtime（小改动）
- 块 G + H：task state machine 改造（影响面较大）
- 块 D：orchestrator.py 拆分（最大文件改动，最后做）
- 块 I：audit chain test 补全（已通结构复用）

---

## 3. 范围（What）

按依赖关系组织 9 块（块 A 已在 phase-0-recon.md 完成）。每块对应一个 implementation Phase。

### 块 B：A2A Source/Target 双向独立加载（H3-B 核心，Codex review P1 闭环）

**当前 baseline**（orchestrator.py:2280-2299）：
```python
source_runtime = await self._ensure_a2a_agent_runtime(
    role=AgentRuntimeRole.MAIN,  # 硬编码：source 永远是 main
    ...
)
source_session = await self._ensure_a2a_agent_session(
    kind=AgentSessionKind.MAIN_BOOTSTRAP,  # 硬编码
    ...
)
source_agent_uri = self._agent_uri("main.agent")  # 硬编码
target_agent_profile_id = source_agent_profile_id  # bug: receiver 复用 caller profile
```

**两个互相依赖的 bug 必须一起修**（**Codex review P1 闭环：仅修 target 不够**）：

#### B-1: Source 端从 runtime_context/envelope 派生（**新增**）

**目标**：source role / session kind / source agent URI 不再硬编码：
- source role：从 `runtime_context.runtime_kind` / `runtime_metadata.role_hint` 派生
  - 主 Agent → MAIN / MAIN_BOOTSTRAP / `main.agent`
  - Worker A 委托给 Worker B → WORKER / WORKER_INTERNAL / `worker.<source_capability>`
- source session kind：与 source role 对齐（main → MAIN_BOOTSTRAP；worker → WORKER_INTERNAL）
- source agent URI：与 source role + worker_capability 对齐
- A2AConversation.source_agent / source_agent_runtime_id / source_agent_session_id 反映真实 source

**为什么必修**：worker→worker 场景下若 source 端仍是 main，则：
- A2AConversation 记录"main → worker"，audit chain 错误（AC-C3 / AC-I3 失败）
- BEHAVIOR_PACK_LOADED 缺少 source Worker 标识
- F099 ask-back 路径找不到正确的 source receiver

#### B-2: Target 端从 requested_worker_profile_id / worker_capability 解析（**原 spec 块 B**）

**目标**：A2A receiver 加载自己的 AgentProfile：
- 路径 1：按 `requested_worker_profile_id` 直接 lookup（envelope.metadata 已含此字段）
- 路径 2：按 `worker_capability` 派生 default profile（**通过 `_delegation_plane.capability_pack` 路径**——orchestrator 不直接持 capability_pack 引用，**Codex review P2 闭环**）
- 路径 3：fallback 到 source profile（兼容性，warning log + 测试 fail-loud——避免 except 吞 error 后静默使用 source profile）

**关键约束**：
- target Worker 在自己 Project / Memory / Behavior 4 层下工作（已通，仅 profile 修复）
- 与 F097 SubagentDelegation 概念严格分离（A2A 是长生命周期，receiver 在自己 project；subagent 是 spawn-and-die，shared context）
- receiver runtime 独立路径已存在（`_ensure_a2a_agent_runtime`）
- **B-1 与 B-2 一起 commit**（不可分离，否则 audit chain 不一致）

### 块 C：Worker → Worker A2A 解禁（H2 完整对等性）

**目标**：删除 `enforce_child_target_kind_policy` 硬禁止：

- 删除 `capability_pack.py:1252` 调用
- 删除 `capability_pack.py:1355-1381` 函数定义
- 更新 `delegation_plane.py:81/976/1082` 注释引用（移除 "Worker→Worker 禁止"提及）
- 删除 `test_capability_pack_phase_d.py:71/261` mock + 改正面 worker→worker 测试

**解禁后行为**：
- Worker A 调用 `delegate_task(target_kind="worker", ...)` 不再 raise
- 走 `plane.spawn_child` 创建 child Worker task（已是 baseline 路径）
- child Worker 在自己 runtime + session 工作（A2A receiver 现成机制）
- audit chain 自动追踪（child Worker 独立 runtime_id / session_id）—— **依赖块 B-1 source 端改造**（否则 source 仍记录为 main，audit chain 错误）

**spec 阶段决策（OD-7）**：F098 Worker→Worker 解禁 = 允许 Worker 走 baseline `delegate_task(target_kind=worker)` 路径（**spawn + 通信合一**），不引入新 worker.send_message 工具。

**强制依赖**（Codex review P1 闭环）：块 C 必须与块 B-1 协同——单独删除 enforce 不充分，因为 source 端仍硬编码 MAIN 会导致 worker→worker A2A 被错误记录为 main→worker，违反 AC-C3 / AC-I3。

### 块 D：D7 顺手清——orchestrator.py 拆 dispatch_service.py

**目标**：orchestrator.py 3432 行 → 拆分为：
- `orchestrator.py`（保留）≤ 2000 行：编排层（dispatch 入口、决策、policy、approval、worker handler 注册）
- `dispatch_service.py`（新建）≈ 1500 行：路由 + target resolution（`_prepare_a2a_dispatch` / `_persist_a2a_terminal_message` / `_save_a2a_message` / `_dispatch_*_*` / 相关 helper）

**关键约束**：
- 行为零变更（纯 refactor）
- 所有 import 链路保持兼容（外部调用方不感知）
- 测试无 regression（3355 → ≥ 3355 + 新增）

### 块 E：P1-1 USER_MESSAGE 复用污染修复（CONTROL_METADATA_UPDATED event）

**当前 baseline**（task_runner.py:287 / agent_context.py:2628）：
```python
type=EventType.USER_MESSAGE,
text_preview="[subagent delegation metadata]",  # 污染对话历史
control_metadata={...subagent_delegation init...},
```

**目标（OD-1 选 A）**：引入 `EventType.CONTROL_METADATA_UPDATED`：

1. `enums.py`：新增 `CONTROL_METADATA_UPDATED = "control_metadata_updated"`
2. `payloads.py`：新增 `ControlMetadataUpdatedPayload`（含 control_metadata 字段，无 text/text_preview）
3. `task_runner.py:287`：改 event type → `CONTROL_METADATA_UPDATED`
4. `agent_context.py:2628`：改 event type → `CONTROL_METADATA_UPDATED`
5. `connection_metadata.py:141` `merge_control_metadata`：合并 USER_MESSAGE + CONTROL_METADATA_UPDATED 两类 events 的 control_metadata（按 ts/seq 时序保持 latest）
6. 其他 USER_MESSAGE consumer（context_compaction._load_conversation_turns / chat / telegram / operator_actions）**自然不受影响**（不再写 marker text USER_MESSAGE）

**关键约束**：
- 向后兼容：已存在历史 USER_MESSAGE 含 subagent_delegation control_metadata 的 task **保持可读**（`merge_control_metadata` 合并两类 events）
- Constitution C2: Everything is an Event：CONTROL_METADATA_UPDATED 是 first-class event type，进 EventStore + 走 SSE broadcast（如适用）

### 块 F：P1-2 ephemeral runtime 复用修复

**当前 baseline**（agent_context.py:2237 `_ensure_agent_runtime`）：

```python
worker_profile_id = "" if subagent  # ephemeral profile 无 source_worker_profile_id
existing = find_active_runtime(project, role=WORKER, profile_id="")  # 复用 caller worker runtime
```

**目标（OD-2 选 A）**：subagent 路径跳过 find_active_runtime 复用：

1. 在 `_ensure_agent_runtime` 入口检测 subagent 信号（`request.delegation_metadata["target_kind"] == "subagent"` OR `agent_profile.kind == "subagent"`）
2. subagent 路径：跳过 find_active_runtime → 直接走新建 ULID 路径
3. AgentRuntime.metadata 加 `subagent_delegation_id` 字段（关联 SubagentDelegation.delegation_id）做 audit
4. 保留 main / worker 路径行为不变（regression 防护）

**关键约束**：
- 与 F098 H3-B 协同：F098 块 B 修复 A2A target profile，但**不改 receiver runtime 路径**（receiver 已用独立 `_ensure_a2a_agent_runtime`）
- ephemeral runtime 数量增长可控：每个 subagent task 一个独立 runtime（与 task 同生命周期）

### 块 G：P2-3 事务边界（OD-3 选 A，atomic）

**当前 baseline**（task_runner.py:881-899 `_close_subagent_session_if_needed`，F097 Phase B-4 已颠倒顺序）：

```python
await event_store.append_event_committed(...)  # commit 1
await session_store.save_agent_session(...)
await conn.commit()                            # commit 2
```

**目标（OD-3 选 A）**：单一 atomic 事务包裹 event append + session save：

1. `event_store.py` 新增 `append_event_pending(event, update_task_pointer)` API（写入但不 commit）
2. cleanup 流程改为：
   ```python
   await event_store.append_event_pending(...)
   session_updated = session.model_copy(update={...CLOSED...})
   await session_store.save_agent_session_pending(session_updated)
   await conn.commit()  # atomic commit
   ```
3. 任一步失败 → conn.rollback() → 全部回滚

**关键约束**：
- atomic 失败可恢复：rollback 后 idempotency_key 守护重试不重复
- 与块 H 协同：cleanup hook 挪到 task state machine 后，事务原子化在 hook 内（统一入口）

### 块 H：P2-4 终态统一层（OD-4 选 A，Codex review P2 闭环）

**当前 baseline**（task_runner.py 多处分散调用 `_close_subagent_session_if_needed`）。

**目标（OD-4 选 A）**：cleanup hook 挪到 task state machine 终态层：

1. `task_service.py` 新增 **实例级** `_terminal_state_callbacks` 注册机制（**非 class-level**——Codex review P2 闭环避免泄漏旧 TaskRunner）
2. `task_runner.py` 注册 `_close_subagent_session_if_needed` 为 callback（每实例只注册一次，shutdown 时注销）
3. `task_service._write_state_transition` 在 `cleanup_lock = TaskStatus(new_status) in TERMINAL_STATES` 后调用所有 callback（line 2761 后）
4. 移除 `task_runner.py:679/711/764` 手动调用

**关键约束**：
- 反向依赖：task_runner 已 import task_service（正向）；task_service 调用 task_runner 通过 callback 注入（无 import 循环）
- callback 异常隔离：cleanup callback 失败 log warn 不影响 state transition
- 所有终态路径自动覆盖：mark_running_task_failed_for_recovery / mark_running_task_cancelled_for_runtime / dispatch exception / shutdown 等都走 _write_state_transition → 自动触发 callback
- **callback 生命周期管理**（Codex review P2 闭环）：
  - **实例级注册**（`task_service._terminal_state_callbacks` instance attr，非 class attr）：每个 TaskService 实例独立 callback list
  - **幂等注册**：register_terminal_state_callback 内部检查重复 callback（按 callback identity）
  - **shutdown 注销**：`unregister_terminal_state_callback` API + TaskRunner.shutdown / TaskRunner.__aexit__ 显式注销
  - **测试保护**：test fixture 创建新 TaskRunner 时不应导致旧实例 callback 残留

### 块 I：AC-F1 worker_capability audit chain 集成测

**当前 baseline**：F096 Phase F audit chain 测试仅 cover main agent dispatch 路径（F096 H2 推迟项）。

**目标**：补 worker_capability 路径完整 audit chain 集成测：

```python
async def test_f098_audit_chain_worker_dispatch():
    # arrange: 创建 WorkerProfile + dispatch_metadata.worker_capability
    # act: 调用 delegate_task tool 触发 worker AgentRuntime 创建
    # assert:
    #   - BEHAVIOR_PACK_LOADED.agent_kind == "worker"
    #   - AgentRuntime.kind == WORKER
    #   - audit chain 四层身份对齐（同 F096 audit chain test）
    #   - delegation_mode == "main_delegate" OR "a2a"（按 F098 OD-6 决策）
```

复用：F096 audit chain test 的 Layer 1-4 验证结构。

### 块 J：BaseDelegation 公共抽象（OD-5 选 A）

**目标**：提取 `BaseDelegation` 父类：

1. `packages/core/src/octoagent/core/models/delegation_base.py`（新建）：BaseDelegation
2. F097 SubagentDelegation 继承 BaseDelegation（不动 spawn-and-die / shared context 子类语义）
3. 新建 `WorkerA2ADelegation`（如 F098 引入 A2A 长生命周期 delegation 持久化需求）—— 但 F098 范围**不持久化 A2A delegation**（与 SubagentDelegation 不同，A2A 通过 A2AConversation + A2AMessageRecord 持久化，已存在）

**spec 阶段决策**：
- F098 范围：**仅提取 BaseDelegation，不引入 WorkerA2ADelegation 持久化** —— A2AConversation 已是 A2A 长生命周期载体
- BaseDelegation 设计为 `Generic[T]`（T 为子类专属字段类型）OR 简单抽象基类（spec 阶段 plan 阶段细化）

---

## 4. 不在范围（Out of Scope）

| 项目 | 推迟到 | 原因 |
|------|--------|------|
| Ask-back / `worker.ask_back` 工具 | F099 | 独立 Feature 范围 |
| source_type 泛化（butler/user/worker/automation）| F099 | 独立 Feature 范围 |
| Decision Loop Alignment（recall planner 内部自适应）| F100 | 独立 Feature 范围 |
| main direct 路径走 AGENT_PRIVATE | F107 | 完整对等留 F107 |
| WorkerProfile / AgentProfile 完全合并 | F107 | F090 部分完成；完全合并涉及独立 SQL 表数据迁移 |
| F096 Phase E frontend agent 视角 UI | 独立 Feature | backend 契约已稳定，独立 frontend 工作 |
| F097 已稳定的 SubagentDelegation 主体 | F098 不改 | 仅 BaseDelegation 抽象提取（不动子类语义）|
| 新增 worker.send_message / worker.notify 等 A2A 工具 | F099+ | F098 仅解禁现有路径（delegate_task）|
| 多用户 / 团队 / 家庭 A2A 隔离 | M7+ | Blueprint §0 已锁单用户深度 |
| BehaviorPack F095 share_with_workers 字段彻底删除 | F107 | UI 字段保留 |

---

## 5. 验收标准（Acceptance Criteria）

### 块 B：A2A Source/Target 双向独立加载（Codex review P1 闭环新增 B-1）

#### B-1: Source 端从 runtime_context/envelope 派生（**新增**）

- **AC-B1-S1**：worker→worker A2A 场景下，source role / session_kind / agent URI 反映真实 source（worker / WORKER_INTERNAL / `worker.<source_capability>`）—— 单测验证
- **AC-B1-S2**：main→worker 场景下，source 仍是 main / MAIN_BOOTSTRAP / `main.agent`（regression 防护）—— 单测验证
- **AC-B1-S3**：A2AConversation.source_agent / source_agent_runtime_id / source_agent_session_id 反映真实 source —— 单测验证
- **AC-B1-S4**：source 派生 fallback：runtime_context/envelope 元数据缺失时优雅降级（不 raise，warning log）—— 单测验证

#### B-2: Target 端独立加载（原 AC-B1/B2/B3）

- **AC-B2-T1**：A2A target Worker 加载自己的 AgentProfile（按 `requested_worker_profile_id`）—— 通过 `_prepare_a2a_dispatch` 单测验证 target_profile.profile_id != source_profile.profile_id
- **AC-B2-T2**：fallback 路径可工作（无 requested_worker_profile_id 时按 worker_capability 派生 default profile via `_delegation_plane.capability_pack`）—— 单测验证；fallback 中**不**吞 except 静默用 source profile（**Codex review P2 闭环：fail-loud**）
- **AC-B2-T3**：A2A receiver context 4 层身份独立（profile / runtime / session / Memory namespace）—— 集成测验证

### 块 C：Worker→Worker 解禁

- **AC-C1**：Worker A 调用 `delegate_task(target_kind="worker", ...)` 不再 raise —— 单测验证 enforce 函数已删除
- **AC-C2**：child Worker 任务正常 spawn + 走自己 runtime + session —— 集成测 worker→worker 端到端
- **AC-C3**：audit chain 完整：parent_worker_runtime_id 字段填充正确，可追溯 caller worker 链 —— 单测验证 AgentSession.parent_worker_runtime_id

### 块 D：orchestrator.py 拆分

- **AC-D1**：orchestrator.py 行数 ≤ 2000；dispatch_service.py 新建（≈ 1500 行）
- **AC-D2**：所有 import 链路兼容（外部 import orchestrator 模块的功能保持）—— grep 验证
- **AC-D3**：行为零变更 —— 全量回归 ≥ 3355 + 0 regression

### 块 E：CONTROL_METADATA_UPDATED 引入

- **AC-E1**：`EventType.CONTROL_METADATA_UPDATED` 已加入 enum + ControlMetadataUpdatedPayload 定义完整
- **AC-E2**：`task_runner._emit_subagent_delegation_init_if_needed` 改用 CONTROL_METADATA_UPDATED —— 历史 USER_MESSAGE 路径已清理
- **AC-E3**：`agent_context._ensure_agent_session` B-3 backfill 改用 CONTROL_METADATA_UPDATED
- **AC-E4**：`merge_control_metadata` 合并 USER_MESSAGE + CONTROL_METADATA_UPDATED 两类 events —— 单测覆盖时序合并
- **AC-E5**：`context_compaction._load_conversation_turns` 不再被污染（subagent task 首轮 latest_user_text 不含 marker text）—— 集成测验证
- **AC-E6**：向后兼容：历史 USER_MESSAGE 含 subagent_delegation 的 task 仍可读取（`merge_control_metadata` 合并兼容）—— migration test

### 块 F：ephemeral runtime 独立

- **AC-F1**：`_ensure_agent_runtime` 检测 subagent 路径 → 跳过 `find_active_runtime` 复用 → 每次新建 runtime —— 单测验证 subagent runtime ≠ caller worker runtime
- **AC-F2**：subagent AgentRuntime.metadata 含 `subagent_delegation_id` 字段 —— 单测验证
- **AC-F3**：main / worker 路径行为零变更（regression 防护）—— 单测验证 find_active_runtime 仍走

### 块 G：事务边界 atomic

- **AC-G1**：EventStore `append_event_pending` API 实现并测试覆盖（pending → commit / rollback 两条路径）
- **AC-G2**：`_close_subagent_session_if_needed` 改用 atomic 事务（event + session 同事务）—— 单测注入 fault 验证 rollback 行为
- **AC-G3**：失败恢复：rollback 后 idempotency_key 守护重试不重复 —— 单测验证

### 块 H：终态统一层（Codex review P2 闭环 + AC-H6/H7 新增）

- **AC-H1**：`task_service._write_state_transition` 加 **实例级** `_terminal_state_callbacks` 注册机制（**非 class-level**）—— 单测验证 callback 触发
- **AC-H2**：`_close_subagent_session_if_needed` 注册为 callback —— 单测验证终态触发
- **AC-H3**：`task_runner.py` 多处手动调用已移除 —— grep 验证 0 处手动 `_close_subagent_session_if_needed` 调用
- **AC-H4**：所有终态路径自动覆盖：mark_failed / mark_cancelled / dispatch exception / shutdown —— 集成测 4 路径
- **AC-H5**：callback 异常隔离：cleanup 失败不影响 state transition —— 单测注入异常验证
- **AC-H6**：**callback 注册幂等**（**Codex review P2 闭环新增**）—— 同一 callback 重复 register 仅生效一次（按 callback identity 检测）；单测验证多次注册触发次数仍为 1
- **AC-H7**：**callback 生命周期**（**Codex review P2 闭环新增**）—— TaskRunner.shutdown / __aexit__ 必须 unregister 自身 callback；旧 TaskRunner 不应被 callback 持有引用；测试用 fixture 创建多个 TaskRunner 时旧实例不残留 callback —— 单测验证 GC + reference count

### 块 I：worker audit chain 集成测

- **AC-I1**：worker_capability 路径完整 audit chain 集成测 PASS（F096 H2 推迟项归位）
- **AC-I2**：BEHAVIOR_PACK_LOADED.agent_kind == "worker" + AgentRuntime.kind == WORKER + 4 层身份对齐
- **AC-I3**：worker → worker A2A 路径 audit chain 四层对齐（块 C 完成后跑通）

### 块 J：BaseDelegation 抽象

- **AC-J1**：`BaseDelegation` 父类定义完整（共享 7+ 字段）+ `SubagentDelegation` 继承
- **AC-J2**：F097 已有 SubagentDelegation 测试不 regression（继承不破坏子类语义）
- **AC-J3**：BaseDelegation 不引入 WorkerA2ADelegation 子类持久化（A2AConversation 已是载体）

### 审计链完整性

- **AC-AUDIT-1**：F098 完成后 audit chain 5 维度对齐：
  1. AgentProfile.profile_id ↔ AgentRuntime.profile_id（F096 已稳定）
  2. AgentRuntime.profile_id ↔ BEHAVIOR_PACK_LOADED.agent_id（F096 已稳定）
  3. BEHAVIOR_PACK_LOADED.agent_id ↔ RecallFrame.agent_runtime_id（F096 已稳定）
  4. SubagentDelegation.caller_agent_runtime_id ↔ caller AgentRuntime.agent_runtime_id（F097 已稳定）
  5. **F098 新增**：A2A target Worker AgentProfile.profile_id ↔ requested_worker_profile_id（块 B）

### 向后兼容性

- **AC-COMPAT-1**：F098 完成后所有 main / worker / subagent 已存在路径行为零变更（regression 防护）
- **AC-COMPAT-2**：历史 USER_MESSAGE 含 control_metadata 的 task 在 F098 完成后仍可正常读取（向后兼容 + migration 数据 0 条）

### 事件可观测

- **AC-EVENT-1**：CONTROL_METADATA_UPDATED 事件正确 emit + EventStore.append_event_committed 持久化
- **AC-EVENT-2**：SUBAGENT_COMPLETED 事件 emit 路径不变（块 H 仅挪迁触发点，不改 emit）
- **AC-EVENT-3**：A2A_MESSAGE_SENT / A2A_MESSAGE_RECEIVED 事件正确 emit（A2A 路径不变）

### 范围边界

- **AC-SCOPE-1**：F099 / F100 / F107 范围未触动（grep 验证 ask_back / source_type / WorkerProfile 合并 / D9 不动）

### 全局回归与流程

- **AC-GLOBAL-1**：每 Phase 后回归 0 regression vs F097 baseline (4441a5a) —— 累计 ≥ 3355 + 新增
- **AC-GLOBAL-2**：e2e_smoke 5x 循环 PASS（参考 F097 sigma 8/8 × 5）
- **AC-GLOBAL-3**：每 Phase 前 Codex review + Final cross-Phase Codex review，0 high 残留
- **AC-GLOBAL-4**：completion-report.md + handoff.md（给 F099）已产出
- **AC-GLOBAL-5**：Phase 跳过 / 偏离显式归档

---

## 6. User Stories

### User Story 1 — Worker 之间可以互相委托工作（H2 完整对等性，Priority: P1）

**Why this priority**：H2 是 OctoAgent 三大设计哲学之一（CLAUDE.local.md §三条核心设计哲学）。当前 Worker 不能委托给 Worker，违反"Worker = 主 Agent − {hire/fire/reassign Worker} − {user-facing 表面}"对等性，是阻塞 M5 的核心架构债。

**Independent Test**：Worker A（research）调用 `delegate_task(target_kind="worker", worker_capability="code", objective="...")` 创建 child Worker B（code），B 在自己 runtime + session 工作完成后回报 A，A 继续。所有 audit trail 完整保留。

**Acceptance Scenarios**：

1. **Given** Worker A 在执行任务时 LLM 决定委托给 Worker B（code），**When** Worker A 调用 `delegate_task(target_kind="worker", worker_capability="code")`，**Then** child task 创建成功，Worker B 在自己 runtime / session 工作，Worker A 收到 B 的结果继续执行
2. **Given** baseline Worker→Worker 报错 "worker runtime cannot delegate to another worker"，**When** F098 完成后再次调用，**Then** 不再 raise，正常 spawn child Worker
3. **Given** child Worker B 完成任务，**When** 查询 audit chain，**Then** Worker B AgentSession.parent_worker_runtime_id == Worker A AgentRuntime.agent_runtime_id（完整 4 层对齐）

---

### User Story 2 — A2A receiver 在自己 Project / Memory / Behavior 下工作（H3-B，Priority: P1）

**Why this priority**：H3-B 是 OctoAgent 第三个核心设计哲学（A2A 真 P2P）。当前 A2A target Worker 复用 source profile，违反"receiver 在自己 context 工作"。是 F098 主责。

**Independent Test**：主 Agent 通过 A2A 委托给 Worker B，B 加载自己的 AgentProfile（research worker），B 的 BehaviorPack 显示 worker variants（IDENTITY.worker.md / SOUL.worker.md / 等），B 的 Memory 写入 B 自己的 namespace（不污染主 Agent AGENT_PRIVATE）。

**Acceptance Scenarios**：

1. **Given** A2A dispatch 完成，**When** 检查 target_session.agent_runtime_id 对应的 AgentProfile，**Then** profile_id != source_profile_id（target Worker 加载自己 profile）
2. **Given** A2A receiver 工作中，**When** 检查 BEHAVIOR_PACK_LOADED 事件，**Then** agent_kind == "worker" + agent_id == target Worker AgentProfile.profile_id
3. **Given** A2A receiver 写 memory，**When** 查询 Memory namespace，**Then** namespace 是 A2A receiver 自己 AgentRuntime 的 AGENT_PRIVATE（不混入 caller namespace）

---

### User Story 3 — Subagent 委托不再污染对话历史（P1-1 修复，Priority: P1）

**Why this priority**：F097 known issue，影响 ContextCompactionService 等多 consumer。需在 F098 完整修复，不允许再次推迟。

**Independent Test**：spawn 一个 subagent task，检查 ContextCompactionService._load_conversation_turns 返回的 turns 列表，**不含** marker text "[subagent delegation metadata]"（不污染 latest_user_text）。

**Acceptance Scenarios**：

1. **Given** 一个 subagent task 已完成，**When** 调用 `_load_conversation_turns(task_id)`，**Then** 返回的 turns 不含 marker text USER_MESSAGE
2. **Given** 引入 CONTROL_METADATA_UPDATED 事件类型后，**When** spawn subagent，**Then** subagent_delegation control_metadata 写入 CONTROL_METADATA_UPDATED 事件（不写 USER_MESSAGE）
3. **Given** 历史 task 含 subagent_delegation 的 USER_MESSAGE 事件（F097 baseline 数据），**When** F098 完成后查询，**Then** `merge_control_metadata` 仍能正确合并（向后兼容）

---

### User Story 4 — Subagent 完成后 audit chain 不混叠到 caller worker runtime（P1-2 修复，Priority: P1）

**Why this priority**：F097 known issue，subagent ephemeral profile 复用 caller worker runtime 导致 audit 混叠。需在 F098 完整修复。

**Independent Test**：spawn 一个 subagent，查询 AgentRuntime 数据库，subagent 任务的 agent_runtime_id != caller worker 的 agent_runtime_id，且 metadata 含 `subagent_delegation_id`。

**Acceptance Scenarios**：

1. **Given** caller Worker 已有 active AgentRuntime，**When** Worker spawn subagent，**Then** subagent task 创建独立 AgentRuntime（不复用 caller worker active runtime）
2. **Given** subagent AgentRuntime 创建，**When** 检查 metadata，**Then** 含 `subagent_delegation_id` 字段关联 SubagentDelegation
3. **Given** main / worker 路径不动，**When** main 创建 worker 或 worker 创建 worker，**Then** find_active_runtime 复用行为保持（regression 防护）

---

### User Story 5 — Subagent session cleanup 在所有终态路径自动触发（P2-4 修复，Priority: P2）

**Why this priority**：F097 已在多处补 cleanup 但未覆盖所有终态路径。task state machine 终态层是终态权威，挪迁后所有终态路径自动覆盖。

**Independent Test**：通过 mark_running_task_failed_for_recovery / mark_running_task_cancelled_for_runtime / shutdown 各种终态路径触发 task 终态，subagent session 自动 CLOSED + SUBAGENT_COMPLETED 事件 emit。

**Acceptance Scenarios**：

1. **Given** subagent task 在 RUNNING 状态，**When** mark_running_task_failed_for_recovery 触发终态，**Then** subagent session 自动 CLOSED + SUBAGENT_COMPLETED 事件 emit
2. **Given** task_runner 各处手动 cleanup 调用已移除，**When** grep `_close_subagent_session_if_needed`，**Then** 仅出现在 callback 注册处和定义处
3. **Given** cleanup callback 失败，**When** 触发终态，**Then** state transition 仍成功（callback 异常隔离）

---

### User Story 6 — orchestrator.py 拆分后职责边界清晰（D7 架构债，Priority: P2）

**Why this priority**：orchestrator.py 3432 行（最大单文件），dispatch 路径与编排路径职责混杂。拆分提升可读性 + 后续扩展性。

**Independent Test**：拆分后 orchestrator.py ≤ 2000 行 + dispatch_service.py 新建（≈ 1500 行），所有 import 链路兼容，全量回归 0 regression。

**Acceptance Scenarios**：

1. **Given** orchestrator.py 拆分完成，**When** wc -l orchestrator.py，**Then** ≤ 2000 行
2. **Given** 拆分完成，**When** 全量回归，**Then** ≥ 3355 + 0 regression
3. **Given** 拆分完成，**When** grep 外部 import orchestrator 的功能，**Then** 全部兼容（dispatch / route / approval 等）

---

### User Story 7 — Worker / Subagent 的 audit trail 完整（块 I 集成测，Priority: P2）

**Why this priority**：F096 H2 推迟项，audit chain 在 worker_capability 路径完整覆盖是 F096 验收的最后 gap。

**Independent Test**：worker_capability 路径触发的 BEHAVIOR_PACK_LOADED 事件 + AgentRuntime + RecallFrame 4 层身份对齐验证。

**Acceptance Scenarios**：

1. **Given** main agent 调用 delegate_task 创建 worker，**When** worker BehaviorPack 加载，**Then** BEHAVIOR_PACK_LOADED.agent_kind == "worker" + audit chain 4 层对齐
2. **Given** worker A 调用 delegate_task 创建 worker B（块 C 解禁后），**When** worker B 工作，**Then** audit chain 链式追溯到 worker A 再到 main agent

---

## 7. 关键实体

### EventType.CONTROL_METADATA_UPDATED（新增）

```python
class EventType(StrEnum):
    # ...existing values...
    CONTROL_METADATA_UPDATED = "control_metadata_updated"  # F098 块 E

class ControlMetadataUpdatedPayload(BaseModel):
    control_metadata: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(default="")  # 描述 emit 来源（subagent_delegation_init / subagent_delegation_session_backfill）
```

### BaseDelegation（新增父类）

```python
class BaseDelegation(BaseModel):
    """F097 SubagentDelegation + F098 A2A delegation 公共抽象基类。"""
    delegation_id: str = Field(min_length=1)
    parent_task_id: str = Field(min_length=1)
    parent_work_id: str = Field(default="")
    child_task_id: str | None = None
    spawned_by: str = Field(default="")
    created_at: datetime = Field(default_factory=_utc_now)
    closed_at: datetime | None = None
    caller_agent_runtime_id: str = Field(default="")
```

### SubagentDelegation（继承 BaseDelegation）

```python
class SubagentDelegation(BaseDelegation):
    """F097：spawn-and-die / shared context（α 共享 caller AGENT_PRIVATE）。"""
    caller_project_id: str = Field(default="")
    child_agent_session_id: str | None = None
    # ...其他 F097 已有字段...
```

### AgentRuntime.metadata 扩展（块 F）

```python
# 现有 AgentRuntime.metadata 字段保持
# F098 新增：subagent_delegation_id 字段（仅 subagent 路径填充）
metadata={
    # ...existing keys...
    "subagent_delegation_id": "<delegation_id>",  # F098 块 F
}
```

### orchestrator.py target_agent_profile_id 解析（块 B）

```python
# 当前 baseline (orchestrator.py:2299):
# source_agent_profile_id = ... (复用)
# target_agent_profile_id = source_agent_profile_id  # bug

# F098 块 B：
async def _resolve_target_agent_profile(
    self,
    *,
    requested_worker_profile_id: str,
    worker_capability: str,
    fallback_source_profile_id: str,
) -> str:
    """A2A target Worker 加载自己的 AgentProfile。"""
    # 路径 1: 按 requested_worker_profile_id 直接 lookup
    if requested_worker_profile_id:
        profile = await self._stores.agent_context_store.get_agent_profile(
            requested_worker_profile_id,
        )
        if profile is not None:
            return profile.profile_id
    # 路径 2: 按 worker_capability 派生 default profile
    if worker_capability:
        default_profile = await self._resolve_default_worker_profile(worker_capability)
        if default_profile is not None:
            return default_profile.profile_id
    # 路径 3: fallback (warning log)
    log.warning(
        "a2a_target_profile_fallback_to_source",
        requested_worker_profile_id=requested_worker_profile_id,
        worker_capability=worker_capability,
    )
    return fallback_source_profile_id
```

---

## 8. Open Decisions（GATE_DESIGN 阶段决策点）

总共 9 项决策点（OD-1 ~ OD-9，已在 §0 列出）。**plan 阶段必须全部锁定**。

**用户拍板路径**（推荐 batch 接受）：

| OD | 推荐 | 理由 |
|----|------|------|
| OD-1 | A | CONTROL_METADATA_UPDATED 最干净 |
| OD-2 | A | subagent 信号检测 + 跳过复用最简单 |
| OD-3 | A | atomic 事务最干净 |
| OD-4 | A | task state machine 是终态权威 |
| OD-5 | A | BaseDelegation 抽象语义清晰 |
| OD-6 | B | 不动 agent_kind enum，用 delegation_mode 区分 |
| OD-7 | A | 复用 baseline delegate_task 路径 |
| OD-8 | A | H 先 G 后（结构改造先）|
| OD-9 | A | A2A target profile 独立加载 |

---

## 9. Success Criteria（可测量成果）

| 维度 | 指标 | 目标值 |
|------|------|--------|
| **单测覆盖** | 各 Phase 新增单测 | ≥ 50（参考 F097 + 71）|
| **回归** | passed vs F097 baseline | ≥ 3355 + 净增 |
| **e2e_smoke** | 5x 循环 PASS | 8/8 × 5 = 40/40 |
| **代码改动** | 实施代码净增减 | 估计 +800 / -200（含拆分块 D 的 ≈ 1500 行挪迁）|
| **新建文件** | spec-driver 制品 + 实施代码 + 测试 | ≈ 30 文件 |
| **commits** | per-Phase 实施 + Verify | 估计 8-10（块 B/C/D/E/F/G/H/I/J + Verify）|
| **Codex review** | per-Phase + Final cross-Phase | ≥ 9 次 review，high 全闭环 |
| **新事件类型** | EventType 枚举增长 | +1（CONTROL_METADATA_UPDATED）|

---

## 10. Edge Cases（异常场景）

### 10.1 块 E：CONTROL_METADATA_UPDATED 引入向后兼容

**场景**：F097 baseline 已有的 task 含 USER_MESSAGE w/ subagent_delegation control_metadata。

**预期行为**：
- F098 完成后，`merge_control_metadata` 合并 USER_MESSAGE + CONTROL_METADATA_UPDATED 两类 events
- 历史数据 USER_MESSAGE 路径仍可读 control_metadata（向后兼容）
- F098 完成后新创建的 task 仅写 CONTROL_METADATA_UPDATED（不再写 USER_MESSAGE marker）

**测试**：migration test 模拟历史 task + F098 后查询，verify 兼容。

### 10.2 块 F：subagent runtime 数量增长

**场景**：每个 subagent task 都创建独立 AgentRuntime，长期运行可能数据增长。

**预期行为**：
- subagent runtime 与 task 同生命周期（task closed → runtime status 转为 closed/inactive）
- AgentRuntime 表已有 status 字段（inactive runtime 不被 find_active_runtime 复用）
- 长期清理由 Memory 维护任务负责（与 closed Task 同策略）

### 10.3 块 G：atomic 事务失败恢复

**场景**：cleanup 流程中 event append 成功但 session save 失败 → atomic rollback 全部撤销。

**预期行为**：
- conn.rollback() 后 idempotency_key 守护重试不重复 emit event
- 重试时再次 atomic 事务尝试 commit（idempotent）
- 如 session save 持续失败 → log error + 触发降级（不阻塞主流程）

### 10.4 块 H：cleanup callback 异常隔离

**场景**：subagent_session_close callback 在某 task 终态路径触发时抛异常。

**预期行为**：
- callback 内部 try-except 隔离，异常 log warn 不影响 state transition
- state transition 写入成功（task projection 更新）
- subagent session 可能未 CLOSED（log warn 提示），但下次 cleanup 重试时幂等

### 10.5 块 C：worker→worker 死循环防护

**场景**：Worker A 委托给 Worker B，B 又委托给 Worker C，C 再委托给 Worker A，可能形成死循环。

**预期行为**：
- DelegationManager 已有 `max_depth=2`（F084 引入）—— 第 3 层 spawn 直接拒绝
- F098 块 C 不改 max_depth，仅删除 enforce target_kind == worker 限制
- 死循环防护由 max_depth 兜底

---

## 11. Constitution 兼容性

| 原则 | F098 影响 | 验证 |
|------|----------|------|
| **C1 Durability First** | CONTROL_METADATA_UPDATED 事件持久化（Constitution C1 C2 双合规）；BaseDelegation 持久化继承 SubagentDelegation 现有路径 | AC-EVENT-1 / AC-J1 |
| **C2 Everything is an Event** | CONTROL_METADATA_UPDATED 是 first-class event；SUBAGENT_COMPLETED 路径不变；A2A_MESSAGE_* 不变 | AC-EVENT-1/2/3 |
| **C3 Tools are Contracts** | F098 不改任何工具 schema；delegate_task 工具语义保持 | AC-COMPAT-1 |
| **C4 Side-effect Two-Phase** | F098 不涉及不可逆操作 | — |
| **C5 Least Privilege** | A2A receiver 在自己 secret scope（与 F095 SOUL.worker.md 协同）；不动 secrets | — |
| **C6 Degrade Gracefully** | atomic 事务失败 rollback；callback 异常隔离；A2A target profile fallback 路径 | AC-G3 / AC-H5 / 块 B fallback |
| **C7 User-in-Control** | F098 不改取消 / 审批路径 | — |
| **C8 Observability** | audit chain 5 维度对齐；CONTROL_METADATA_UPDATED 事件可观测 | AC-AUDIT-1 / AC-EVENT-1 |
| **C9 Agent Autonomy** | LLM 决策 Worker→Worker 委托时机；F098 不引入硬编码规则 | — |
| **C10 Policy-Driven Access** | F098 不改权限决策；删除 enforce_child_target_kind_policy 是**架构决策**（H2 对等性），不是权限决策 | — |

---

## 12. 依赖关系（实施 Phase 顺序）

```
Phase 0 实测 (已完成 ✓)
   ↓
Phase E (P1-1 CONTROL_METADATA_UPDATED) ─┐
                                         │ → Phase B (A2A target profile 独立)
Phase F (P1-2 ephemeral runtime 独立) ───┘
                                         ↓
                                Phase C (Worker→Worker 解禁)
                                         ↓
                                Phase I (worker audit chain test)
                                         ↓
                                Phase H (终态统一 — 结构改造先)
                                         ↓
                                Phase G (事务边界 atomic)
                                         ↓
                                Phase J (BaseDelegation 抽象 — 可与 H/G 并行)
                                         ↓
                                Phase D (orchestrator.py 拆分 — 最后做)
                                         ↓
                                Verify + Final Codex review
```

**关键点**：
- Phase E + F **并行可能**（互不依赖），但顺序 E → F 更安全（E 是 event 模型层改动，F 是 runtime 路径改动）
- Phase B 依赖 F（receiver runtime 路径稳定后再做 profile 独立加载）
- Phase C 依赖 B（worker→worker A2A 路径需 receiver 真独立才有意义）
- Phase I 依赖 C（解禁后才能测 worker→worker audit chain）
- Phase H 先于 G（结构改造先；G 受益于 H 已统一的 hook）
- Phase J 可与 H/G 并行（BaseDelegation 抽象不依赖 task state machine）
- Phase D 最后（最大文件改动，避免 rebase 冲突）

---

## 13. 关键引用

- **Phase 0 实测侦察**：[phase-0-recon.md](phase-0-recon.md)
- **F097 handoff**：`.specify/features/097-subagent-mode-cleanup/handoff.md`
- **F097 codex-review-final**：`.specify/features/097-subagent-mode-cleanup/codex-review-final.md`
- **F096 handoff**：`.specify/features/096-worker-recall-audit/handoff.md`
- **F092 completion-report**：`.specify/features/092-delegation-plane-unification/completion-report.md`
- **CLAUDE.local.md M5 战略规划 § F098 范围**

---

**Spec v0.1 Draft 完成。下一步：clarify + checklist（GATE_DESIGN 硬门禁）。**
