# 技术调研报告: F097 Subagent Mode Cleanup

**特性分支**: `feature/097-subagent-mode-cleanup`
**调研日期**: 2026-05-10
**调研模式**: 在线 + 本地 codebase-scan
**产品调研基础**: [独立模式] 本次技术调研未参考产品调研结论；基于 F097 需求描述 + F092/F094/F095/F096 完成记录

---

## 1. 调研目标

**核心问题**:
1. 当前 Subagent spawn 走哪条路径？是否已统一到 `plane.spawn_child`？
2. Subagent context 共享（Project / Memory namespace / RuntimeHintBundle）当前真实状态？
3. F096 audit chain 在 Subagent 路径的实际覆盖情况？
4. SubagentSession（实为 `SUBAGENT_INTERNAL` 类型的 AgentSession）完成后是否有真清理？zombie 风险？

**F097 需求范围**:
- 显式建模 `SubagentDelegation` 数据类型
- 扩展 `agent_kind=subagent` enum（`AgentProfileKind`）
- Subagent 共享调用方 Project / Memory / Context（spawn-and-die 模式）
- 完成后真清理 Subagent session

---

## 2. 当前路径对照表（块 A1 实测）

### 2.1 Spawn 路径

| 维度 | 实测结论 |
|------|---------|
| 统一入口 | **已走 `plane.spawn_child`**（F092 已完成）|
| `delegate_task` 工具 | `delegation_tools.py:150` → `deps.delegation_plane.spawn_child(target_kind="subagent", emit_audit_event=True)` |
| `subagents.spawn` 工具 | `delegation_tools.py:150` → `deps.delegation_plane.spawn_child(target_kind=target_kind, emit_audit_event=False)` |
| DelegationManager gate | `spawn_child` 内第 2 步调用 `DelegationManager.delegate()`，gate（depth/concurrent/blacklist）必走 |
| 3 条豁免路径 | `apply_worker_plan` / `work.split` / `spawn_from_profile`（F092 已归档，不在此覆盖）|

关键差异（两个工具）：
- `delegate_task`（`delegate_task_tool.py:155`）：`emit_audit_event=True` → 写 SUBAGENT_SPAWNED
- `subagents.spawn`（`delegation_tools.py:159`）：`emit_audit_event=False` → **不写** SUBAGENT_SPAWNED（F092 保守决策，历史行为等价）

**参考文件**:
- `octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py:953-1142`（`spawn_child` 完整实现）
- `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/delegate_task_tool.py:146-171`
- `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/delegation_tools.py:120-162`

### 2.2 Subagent 底层执行路径

`spawn_child` → `capability_pack._launch_child_task`（`capability_pack.py:1229`）：

- **enforce_child_target_kind_policy**（`capability_pack.py:1282`）：当前运行时是 Worker 时禁止 `target_kind=WORKER`，但 `target_kind=subagent` 不触发禁止 → subagent 可从 worker 内 spawn
- 创建 `NormalizedMessage`，继承 `parent_task.scope_id`（`capability_pack.py:1250`）→ 子任务与父任务共享同一 scope → **project 绑定通过 scope_id 传递**
- 调用 `task_runner.launch_child_task(child_message)` 启动子任务

### 2.3 SubagentSession 概念：实际是 AgentSession (kind=SUBAGENT_INTERNAL)

**当前没有独立的 `SubagentSession` 模型**。其等价物是：
- `AgentSession.kind = AgentSessionKind.SUBAGENT_INTERNAL`（`agent_context.py:133`）
- `AgentSession.parent_worker_runtime_id`（`agent_context.py:326`）：记录所属 Worker 的 AgentRuntime ID

schema 层完整，但 `_ensure_agent_session`（`agent_context.py:2318`）当前**不含** SUBAGENT_INTERNAL 路径：

```python
kind = (
    AgentSessionKind.DIRECT_WORKER       # worker 且无 parent_session + 无 work_id
    if is_direct_worker_session
    else (
        AgentSessionKind.WORKER_INTERNAL  # worker 有 parent / work_id
        if agent_runtime.role is AgentRuntimeRole.WORKER
        else AgentSessionKind.MAIN_BOOTSTRAP  # 主 Agent
    )
)
```

`SUBAGENT_INTERNAL` 只在以下地方使用：
- `agent_context_store.py:673`：`list_subagent_sessions` store 查询方法（有实现）
- `session_service.py:597`：list sessions 时跳过 `SUBAGENT_INTERNAL`（不暴露给侧栏）
- `session_service.py:694`：`_default_turn_executor_kind_for_runtime` 返回 `TurnExecutorKind.SUBAGENT`
- `agent_context.py:2285`：注释说明 SUBAGENT_INTERNAL 不做 session lookup 复用

**关键 Gap**：当前 `_ensure_agent_session` 无法创建 `kind=SUBAGENT_INTERNAL` 的 session，subagent 运行时实际会走 `WORKER_INTERNAL` 路径（`agent_runtime.role=WORKER` 的 fallback）。

---

## 3. Context 共享真实状态（块 A2 实测）

### 3.1 Project 共享

**结论**：Subagent 继承调用方 project，通过 `scope_id` 传递（`capability_pack.py:1250`）：

```python
child_message = NormalizedMessage(
    scope_id=parent_task.scope_id,  # 与父任务完全相同的 scope
    ...
)
```

子任务的 `scope_id` 与父任务一致 → `delegation_plane.prepare_dispatch` 内 `_resolve_project_context` 会解析到同一个 Project → Subagent 写文件落在调用方同一 project 目录。**已符合 F097 H3-A 共享 Project 的设计目标，baseline 已通**。

### 3.2 Memory namespace 归属

**结论**：当前 Subagent 走 `AgentRuntime.role=WORKER` 路径，`_ensure_memory_namespaces`（`agent_context.py:2463`）按 F094 B1 逻辑创建 `AGENT_PRIVATE` namespace（`agent_context.py:2526`）——**独立的 AGENT_PRIVATE，而不是共享调用方 namespace**。

F094 决策：Worker → AGENT_PRIVATE（独立）；main direct → PROJECT_SHARED。Subagent 与 Worker 共享相同路径。

**F097 需要明确**：H3-A 共享调用方 Memory namespace 是否意味着 Subagent 应用 caller 的 AGENT_PRIVATE（或 PROJECT_SHARED），而非创建新的 AGENT_PRIVATE？这是 **F097 核心 Gap**，需要 spec 决策。

### 3.3 RuntimeHintBundle 拷贝

**结论**：`RuntimeHintBundle`（`behavior.py:206`）当前**没有**从调用方拷贝到 Subagent 的机制。

`_launch_child_task` 创建的 `child_message.control_metadata` 不含 RuntimeHintBundle 字段：

```python
control_metadata={
    "parent_task_id": parent_task.task_id,
    "parent_work_id": parent_work.work_id,
    "requested_worker_type": worker_type,
    "target_kind": target_kind,
    "tool_profile": tool_profile,
    "spawned_by": spawned_by,
    "child_title": title,
    "worker_plan_id": plan_id,
}
```

Subagent 从零构建自己的 RuntimeHintBundle（`agent_decision.py` 中 make_decision 流程）。**这是 F097 需要新增的能力**：拷贝 caller 的 `surface` / `tool_universe` / 最近失败限制 / RecentConversation 摘要等 hints 到 child。

### 3.4 agent_kind enum 实测

`AgentProfileKind`（`agent_context.py:16`）当前定义：

```python
AgentProfileKind = Literal["main", "worker", "subagent"]
```

**已含 `subagent` 值**（F090 D2 引入，注释明确"临时 Subagent（F097 启用）"）。`AgentProfile.kind` 字段（`agent_context.py:179`）默认 `"main"`，已可设为 `"subagent"`。

`DelegationMode`（`orchestrator.py:39`）：

```python
DelegationMode = Literal["unspecified", "main_inline", "main_delegate", "worker_inline", "subagent"]
```

**已含 `"subagent"` 值**（F090 D1 引入）。`delegation_mode_for_target_kind(SUBAGENT)` 返回 `"subagent"`（`delegation_plane.py:949`）。

**Baseline-Already-Passed 发现 #1**：`AgentProfileKind` 和 `DelegationMode` 的 `subagent` enum 值已存在，F097 无需新增枚举值，只需填充实际使用路径。

---

## 4. F096 audit chain 在 Subagent 覆盖矩阵（块 A3 实测）

### 4.1 BEHAVIOR_PACK_LOADED 对 Subagent 的 emit

**结论**：`BEHAVIOR_PACK_LOADED` 的 emit 逻辑在 `agent_context.py:976-1007`：

```python
if loaded_pack is not None:
    if loaded_pack.metadata.get("cache_state") == "miss":
        loaded_payload = make_behavior_pack_loaded_payload(
            loaded_pack, agent_profile=agent_profile, load_profile=load_profile_emit,
        )
        # ... append_event_committed(loaded_event)
```

`make_behavior_pack_loaded_payload`（`agent_decision.py:321`）读取 `agent_profile.kind`，即 `str(agent_profile.kind)`。

**当前 subagent 触发 `build_task_context` 时会 emit BEHAVIOR_PACK_LOADED**（只要走了 `build_task_context` 主路径）。但 `agent_kind` 字段当前值取决于 subagent 的 `AgentProfile.kind`——由于 F097 尚未实现创建 `kind=subagent` 的 `AgentProfile`，**当前 subagent 路径实际 emit 的 `agent_kind="main"` 或 `"worker"`**（取决于 `agent_profile` 解析结果），**不是 `"subagent"`**。

**参考**：`agent_decision.py:377` 注释明确："F096 仅 emit `main` / `worker` 两个值（不预占 `subagent`，由 F097 引入）"。

### 4.2 BEHAVIOR_PACK_LOADED payload `agent_kind` 字段当前值

如上，当前 subagent 走的 session 是 `WORKER_INTERNAL` 路径，`agent_profile` 是 worker profile 镜像（kind=`"worker"`），故 emit 的 `agent_kind="worker"`。F097 引入 `kind="subagent"` 的 `AgentProfile` 后，此字段才会正确区分。

### 4.3 RecallFrame.agent_runtime_id 在 Subagent 上的填充

**结论**：RecallFrame 由 `build_task_context` → `_resolve_context_bundle` 路径写入（`agent_context.py:952`），填充的 `agent_runtime_id` 来自 `agent_runtime.agent_runtime_id`。

当前 subagent 路径使用 Worker AgentRuntime（`role=WORKER`），RecallFrame 填充的是 subagent 自己的 `agent_runtime_id`（不是 caller 的）——**已满足 F096 要求（每个 runtime 有独立 RecallFrame 记录）**。

**Baseline-Already-Passed 发现 #2**：RecallFrame 按 subagent 自己的 agent_runtime_id 填充，与 F096 audit chain 一致。

### 4.4 `list_recall_frames` endpoint 对 Subagent 维度的过滤

**结论**：F096 已完成的 `list_recall_frames` endpoint（`control_plane.py:167-202`）支持按 `agent_runtime_id` / `agent_session_id` 过滤。**当前没有** `agent_kind` 过滤参数。

```python
async def list_recall_frames(
    agent_runtime_id: str | None = Query(default=None),
    agent_session_id: str | None = Query(default=None),
    context_frame_id: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    queried_namespace_kind: str | None = Query(default=None),
    hit_namespace_kind: str | None = Query(default=None),
    ...
```

F097 可以扩展 `agent_kind` 过滤参数，但核心 audit 能力（按 runtime_id 查）已稳定，**不阻塞 F097 主路径**。

---

## 5. SubagentSession zombie 风险评估（块 A4 实测）

### 5.1 完成路径清理状态

**关键发现**：当前 Subagent session（`kind=SUBAGENT_INTERNAL`）**没有专门的完成清理路径**。

分析现有清理机制：
- `close_active_sessions_for_project`（`agent_context.py:2434`）：在创建新 session 时关闭同 project 的旧活跃 session，但此逻辑仅针对 `MAIN_BOOTSTRAP` / `DIRECT_WORKER`（partial unique index 约束）
- `session_service.py:1569`：只有 `session_reset` 动作才 mark session `CLOSED`，且只操作 `related_sessions`（不含 SUBAGENT_INTERNAL 类型）
- Worker runtime 完成（`worker_runtime.py:573`）：返回 `WorkerResult`，**不调用任何 session 清理逻辑**

**Zombie 风险等级：高**。每次 subagent 执行完毕，相关的 `AgentSession`（目前实际走 `WORKER_INTERNAL` 类型，future `SUBAGENT_INTERNAL` 类型）保持 `ACTIVE` 状态，永不关闭。

### 5.2 资源清理情况

| 资源类型 | 当前状态 |
|---------|---------|
| AgentSession 状态转换 | **无**：完成后 session 仍 `ACTIVE` |
| AGENT_PRIVATE namespace 引用 | **不解除**：namespace 按 agent_runtime_id 绑定，无 GC |
| 子 task 附着 | 完成后 task 状态到 `SUCCEEDED`，task store 正常更新，不受 session 影响 |
| parent_worker_runtime_id | 有 index（`sqlite_init.py:832`）可用于 GC，但当前**无 GC 实现** |

### 5.3 zombie 定量评估

`list_subagent_sessions`（`agent_context_store.py:673`）store 方法存在，通过 `parent_worker_runtime_id` 查子 session。但**当前代码库中没有任何 caller 调用此方法**（仅在 store 层定义）。

现有 GC / cleanup 路径：
- `cleanup_orphaned_*`：无相关实现
- TTL 机制：无
- `session_reset` action：不含 SUBAGENT_INTERNAL

**结论**：F097 必须实现 subagent session 完成后的显式关闭（`AgentSessionStatus.CLOSED`）。可选方案：
1. Worker runtime `run()` 完成后，检查所有 `SUBAGENT_INTERNAL` sessions 并 mark CLOSED（需要 task_id 关联）
2. Task runner 在子 task 进入终态时触发 session 清理

---

## 6. Baseline-Already-Passed 汇总

与 F093/F094/F095/F096 的 "baseline 部分已通" pattern 相同，F097 实测发现以下项目在 baseline 已实施：

| # | 预期需要的工作 | 实测结论 |
|---|--------------|---------|
| BAP-1 | 新增 `AgentProfileKind` 中的 `"subagent"` 值 | **已存在**（`agent_context.py:16`，F090 D2 引入） |
| BAP-2 | 新增 `DelegationMode = "subagent"` 值 | **已存在**（`orchestrator.py:39`，F090 D1 引入）|
| BAP-3 | Subagent spawn 走 `plane.spawn_child` 统一入口 | **已完成**（F092，`delegation_plane.py:953`）|
| BAP-4 | `DelegationTargetKind.SUBAGENT` enum 值 | **已存在**（`delegation.py:252`）|
| BAP-5 | `AgentSessionKind.SUBAGENT_INTERNAL` enum 值 | **已存在**（`agent_context.py:133`）|
| BAP-6 | `AgentSession.parent_worker_runtime_id` 字段 | **已存在**（`agent_context.py:326` + SQLite `sqlite_init.py:452`）|
| BAP-7 | `list_subagent_sessions` store 方法 | **已存在**（`agent_context_store.py:673`，但无 caller）|
| BAP-8 | Subagent scope_id 继承父 task（project 共享） | **已完成**（`capability_pack.py:1250`）|
| BAP-9 | `TurnExecutorKind.SUBAGENT` 返回逻辑 | **已存在**（`session_service.py:694`）|

**Baseline-Already-Passed 项目总计：9 项**

---

## 7. Gap 列表（按 F097 Phase 编号）

### Gap-A：SubagentDelegation 显式建模（Phase A）

**位置**：`packages/core/src/octoagent/core/models/` 新建文件或扩展 `delegation.py`

**描述**：当前无 `SubagentDelegation` 数据类型，subagent 属性完全依赖 `AgentSession.metadata` 字典。需要建立：

```python
class SubagentDelegation(BaseModel):
    delegation_id: str
    parent_task_id: str
    parent_work_id: str
    child_task_id: str
    caller_agent_runtime_id: str       # 调用方 runtime（用于 context 共享）
    caller_project_id: str
    caller_memory_namespace_ids: list[str]  # 共享的 namespace 引用
    spawned_by: str
    target_kind: DelegationTargetKind = DelegationTargetKind.SUBAGENT
    created_at: datetime
    closed_at: datetime | None = None
```

**改动估算**：新增约 50 行 Pydantic model；风险低。

### Gap-B：`_ensure_agent_session` 增加 SUBAGENT_INTERNAL 路径（Phase B）

**位置**：`services/agent_context.py:2337-2345`

**描述**：当前 `_ensure_agent_session` 无法创建 `kind=SUBAGENT_INTERNAL` session。当 `target_kind="subagent"` 且有 `parent_agent_session_id` 时应创建 SUBAGENT_INTERNAL。

```python
# 当前逻辑（仅 3 路）
kind = DIRECT_WORKER | WORKER_INTERNAL | MAIN_BOOTSTRAP

# F097 需增加第 4 路：
# 当 target_kind=subagent 且有 parent_session 时 → SUBAGENT_INTERNAL
```

**改动估算**：`agent_context.py` 约 20 行；风险中（session 创建路径是核心逻辑）。

### Gap-C：AgentProfile 创建 `kind="subagent"` 实例（Phase C）

**位置**：`services/agent_context.py`，`_resolve_or_create_agent_profile` 路径

**描述**：当前 subagent 复用 Worker 或 main 的 AgentProfile（kind 值错误）。F097 需要在 spawn 时创建 `kind="subagent"` 的 ephemeral AgentProfile，或从 caller 的 profile 派生。

**设计选项**：
- 方案 A（推荐）：spawn_child 时生成 ephemeral AgentProfile（kind=subagent，scope=PROJECT，生命周期与 SubagentDelegation 绑定）
- 方案 B：复用 caller 的 AgentProfile，但修改 kind 标记

**改动估算**：约 30-50 行；风险中。

### Gap-D：RuntimeHintBundle 从 caller 拷贝到 child（Phase D）

**位置**：`services/capability_pack.py:1254`（`_launch_child_task`）

**描述**：当前 `child_message.control_metadata` 不含 RuntimeHintBundle 字段。F097 需要扩展拷贝以下字段：
- `surface`（已在 control_metadata 中间接传递，通过 scope_id）
- `tool_universe` hints
- `recent_worker_lane_*` 字段
- 最近失败限制（如有）

**改动估算**：`_launch_child_task` 约 15 行扩展；风险低（新增字段不影响现有路径）。

### Gap-E：SubagentSession 完成后真清理（Phase E）

**位置**：`services/worker_runtime.py:573-593`（`run()` 完成分支）和/或 task runner 终态回调

**描述**：当前 subagent 完成后 AgentSession 保持 `ACTIVE`（zombie）。F097 需要在子任务进入终态（succeeded/failed/cancelled）时：
1. 查找绑定到该 task_id 的 `SUBAGENT_INTERNAL` sessions
2. 调用 `agent_context_store.save_agent_session` 将 status 设为 `CLOSED`，填充 `closed_at`

利用已有方法：`agent_context_store.list_subagent_sessions(parent_worker_runtime_id)` 已实现，但需要知道 parent_worker_runtime_id → 通过 `parent_task_id` 反查 AgentRuntime。

**改动估算**：约 30-40 行（cleanup hook + store 查询）；风险中（需要准确的终态判断时机）。

### Gap-F：Memory namespace 共享语义明确化（Phase F，需 spec 决策）

**位置**：`services/agent_context.py:2517-2545`（`_ensure_memory_namespaces`）

**描述**：H3-A 设计目标"共享调用方 Memory"的具体语义需要 spec 明确：
- **选项 α（共享引用）**：Subagent 直接使用 caller 的 `AGENT_PRIVATE` namespace ID（不创建新的）
- **选项 β（拷贝 scope_ids）**：Subagent 创建自己的 `AGENT_PRIVATE` namespace，但 `memory_scope_ids` 拷贝 caller 的
- **选项 γ（读共享写隔离）**：Subagent 读 caller namespace，写到独立临时 namespace（cleanup 时删除）

当前实现走选项 β 的变体（但 scope_id 不拷贝 caller）。**F097 spec 阶段必须做明确决策**。

**改动估算**：取决于选项，约 20-60 行；风险中。

### Gap-G：BEHAVIOR_PACK_LOADED `agent_kind` 正确标记（Phase G）

**位置**：`services/agent_decision.py:352`（`make_behavior_pack_loaded_payload`）

**描述**：当 Gap-C 实施后（subagent 有 kind=subagent 的 AgentProfile），BEHAVIOR_PACK_LOADED 的 `agent_kind` 字段将自动正确为 `"subagent"`（`str(agent_profile.kind)` 直接返回）。**此 gap 是 Gap-C 的自动副产品**，无额外实施。

---

## 8. F098/F099/F100 边界（F097 不动范围）

| 主题 | 所属 Feature | F097 处理 |
|------|-------------|---------|
| Worker→Worker 通信解禁（`_enforce_child_target_kind_policy` 移除） | F098 | 不动 |
| A2A receiver 在自己 context 工作 | F098 | 不动 |
| `dispatch_service` 拆分（D7） | F098 | 不动 |
| Ask-back channel (`worker.ask_back`) | F099 | 不动 |
| A2A source_type 泛化 | F099 | 不动 |
| Decision Loop Alignment / recall_planner_mode auto | F100 | 不动 |
| AC-F1 worker_capability 路径 audit chain（F096 H2 推迟） | F098 | 不动（等 delegate_task fixture 完备）|

---

## 9. 架构方案对比

F097 的核心架构决策点是 **SubagentDelegation 生命周期管理方式**：

| 维度 | 方案 A：轻量 ephemeral（推荐）| 方案 B：重量级状态机 |
|------|---------------------------|-------------------|
| 概述 | SubagentDelegation 作为纯数据对象，生命周期跟随 Task | 独立的 SubagentSession 状态机（类似 WorkerDispatchState）|
| 复杂度 | 低：利用已有 AgentSession + 新增 cleanup hook | 高：需要新状态机 + 独立存储 |
| 与现有架构契合 | ✅ 高：复用 AgentSession / AgentRuntime 体系 | ⚠️ 中：引入额外层次 |
| Memory 共享 | ✅ 易实现：在 spawn 时传递 namespace_ids | ⚠️ 中：需要状态机维护引用 |
| Cleanup | ✅ 通过 Task 终态触发 session.close | 需要额外状态转换 |
| F098 兼容性 | ✅ 高：spawn-and-die 语义清晰，F098 独立演化 | ⚠️ 可能与 F098 A2A 状态机冲突 |
| 改动规模 | 约 200-300 行新增 | 约 500-800 行 |

**推荐方案 A（轻量 ephemeral）**：与 F092 `SpawnChildResult` / F096 audit chain 设计哲学一致，最小侵入，F098 后续可独立演化。

---

## 10. 技术风险清单

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---------|------|------|---------|
| R1 | Memory 共享语义 spec 不清晰导致 F097 实施错方向 | 高 | 高 | spec 阶段必须明确选项 α/β/γ，Phase F 前锁定决策 |
| R2 | `_ensure_agent_session` 改动破坏现有 Worker session 创建逻辑 | 中 | 高 | 保守路径：仅在有明确 `target_kind=subagent` 时走新路径；全量回归验证 |
| R3 | Subagent cleanup hook 时机错误（过早 close 活跃 session） | 中 | 中 | 严格按 Task 终态（succeeded/failed/cancelled）触发；加幂等保护 |
| R4 | AgentProfile (kind=subagent) ephemeral 引入 profile_id 冲突 | 低 | 中 | 使用 ULID 生成，不依赖 worker_profile 表；不写入持久化 profile store |
| R5 | `list_subagent_sessions` 通过 parent_worker_runtime_id 查询，但 subagent 的 parent_worker_runtime 无法准确确定 | 中 | 低 | cleanup 改用 task_id 维度查询（RecallFrame/AgentSession 已有 task_id 字段）|

---

## 11. 需求-技术对齐度评估

| F097 需求 | 技术方案覆盖 | 说明 |
|-----------|-------------|------|
| 显式建模 SubagentDelegation | ⚠️ 部分覆盖 | enum 已有；model 需要新建（Gap-A）|
| agent_kind=subagent enum 扩展 | ✅ 已覆盖 | `AgentProfileKind` 已含 subagent（BAP-1）|
| 共享调用方 Project | ✅ 已覆盖 | scope_id 继承（BAP-8）|
| 共享调用方 Memory | ❌ 未覆盖 | 需 spec 决策 + 实施（Gap-F）|
| 共享 RuntimeHintBundle | ❌ 未覆盖 | 需实施 caller→child 拷贝（Gap-D）|
| 完成后真清理 session | ❌ 未覆盖 | zombie 风险，需实施（Gap-E）|

### Constitution 约束检查

| 约束 | 兼容性 | 说明 |
|------|--------|------|
| C1 Durability First | ✅ 兼容 | SubagentDelegation 持久化；Task 持久化 |
| C2 Everything is an Event | ⚠️ 需注意 | SUBAGENT_SPAWNED 仅 delegate_task 路径写，subagents.spawn 不写；F097 可评估是否统一 |
| C9 Agent Autonomy | ✅ 兼容 | spawn 时机由 LLM 决策 |
| C7 User-in-Control | ✅ 兼容 | `subagents.kill` 工具已存在，可取消 |

---

## 12. 结论与建议

### 核心发现摘要

1. **baseline 已完成 9 项预期工作**，F097 实际增量约 6 个 Gap（A-F），改动规模约 200-300 行
2. **Spawn 路径已统一**（F092），F097 不需要重新设计 spawn 机制
3. **enum 层已预留**（F090），`AgentProfileKind=subagent` / `DelegationMode=subagent` 无需新增
4. **最大未知量**：Memory namespace 共享语义（选项 α/β/γ），需 spec 阶段明确
5. **zombie 风险是真实的**：每次 subagent 执行后 session 永不关闭，随着使用积累

### 实施建议

- **Phase A**：先建 SubagentDelegation model，作为后续 Phase 的数据基础
- **Phase B**：`_ensure_agent_session` 增加 SUBAGENT_INTERNAL 路径（最小改动，只在明确 subagent 路径触发）
- **Phase C**：ephemeral AgentProfile (kind=subagent) 创建（独立，不影响现有 AgentProfile store）
- **Phase D**：RuntimeHintBundle 拷贝（最安全，新增字段不破坏现有）
- **Phase E**：Session cleanup hook（推荐在 task runner 终态回调，而不是 worker_runtime.run()）
- **Phase F**：Memory 共享——**必须先做 spec 决策**，不建议在不确定方向的情况下实施

### 对后续 spec 的建议

- Memory namespace 共享语义需要单独一节明确（选项 α/β/γ 各有 trade-off）
- F098（A2A 模式）的 SubagentDelegation 可能需要演化，F097 设计应保留向后兼容扩展点
- `subagents.spawn` 不写 SUBAGENT_SPAWNED 的历史决策是否在 F097 中统一，建议 spec 明确取舍

---

## 关键参考引用

| 文件 | 行号 | 内容 |
|------|------|------|
| `octoagent/packages/core/src/octoagent/core/models/agent_context.py` | L16 | `AgentProfileKind = Literal["main", "worker", "subagent"]` |
| `octoagent/packages/core/src/octoagent/core/models/agent_context.py` | L133 | `SUBAGENT_INTERNAL = "subagent_internal"` |
| `octoagent/packages/core/src/octoagent/core/models/agent_context.py` | L326 | `parent_worker_runtime_id` 字段定义 |
| `octoagent/packages/core/src/octoagent/core/models/orchestrator.py` | L39-45 | `DelegationMode` Literal 含 subagent |
| `octoagent/packages/core/src/octoagent/core/models/delegation.py` | L248-255 | `DelegationTargetKind` enum |
| `octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py` | L953 | `spawn_child` 统一入口 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py` | L949 | `delegation_mode_for_target_kind(SUBAGENT) = "subagent"` |
| `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` | L1229-1279 | `_launch_child_task` 实现 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` | L1250 | `scope_id=parent_task.scope_id`（project 共享） |
| `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/delegate_task_tool.py` | L146-171 | `delegate_task` spawn 路径 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/delegation_tools.py` | L120-162 | `subagents.spawn` spawn 路径 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` | L2337-2345 | `_ensure_agent_session` 不含 SUBAGENT_INTERNAL 路径 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` | L2526 | `_ensure_memory_namespaces` AGENT_PRIVATE 路径 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` | L976-1007 | `BEHAVIOR_PACK_LOADED` emit 逻辑 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/agent_decision.py` | L352,389 | `make_behavior_pack_loaded_payload`，`agent_kind=str(agent_profile.kind)` |
| `octoagent/packages/core/src/octoagent/core/store/agent_context_store.py` | L673 | `list_subagent_sessions`（有实现，无 caller）|
| `octoagent/packages/core/src/octoagent/core/store/sqlite_init.py` | L452,832 | `parent_worker_runtime_id` 字段 + index |
| `octoagent/apps/gateway/src/octoagent/gateway/routes/control_plane.py` | L167-202 | `list_recall_frames` endpoint（无 agent_kind 过滤）|
| `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane/session_service.py` | L694 | `SUBAGENT_INTERNAL` → `TurnExecutorKind.SUBAGENT` |
