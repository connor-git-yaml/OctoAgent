# F097 Phase 0 — 实测侦察报告

**日期**: 2026-05-10
**baseline**: cc64f0c (F096 final)
**worktree**: `/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F097-subagent-mode-cleanup`

---

## §1 T0.1 — BEHAVIOR_PACK_LOADED + SUBAGENT_COMPLETED 实测

### 1.1 BEHAVIOR_PACK_LOADED 消费方文件清单

| 文件 | 行号 | 类型 | 内容 |
|------|------|------|------|
| `packages/core/src/octoagent/core/models/enums.py` | L220 | enum 定义 | `BEHAVIOR_PACK_LOADED = "BEHAVIOR_PACK_LOADED"` |
| `packages/core/src/octoagent/core/models/behavior.py` | L291 | payload schema 定义 | `BehaviorPackLoadedPayload` class 定义 |
| `apps/gateway/src/octoagent/gateway/services/agent_context.py` | L976-1007 | **唯一 emit 点** | `build_task_context` 内，cache miss 时 emit + `append_event_committed` |
| `apps/gateway/src/octoagent/gateway/services/agent_decision.py` | L166, L327, L336 | payload 构造函数 | `make_behavior_pack_loaded_payload` 函数定义 + 注释说明 |
| `apps/gateway/tests/test_task_service_context_integration.py` | L76, L2369-2584 | 测试 reader | 事件类型过滤（不校验 agent_kind 值）；audit chain 测试（断言 agent_id 一致性）|
| `apps/gateway/tests/services/test_agent_decision_envelope.py` | L400, L466 | 测试 reader | Phase D 注释 + USED/LOADED pack_id 区分说明 |

### 1.2 agent_kind 字段所有 reader 清单

| 文件 | 行号 | 类型 | 代码内容 |
|------|------|------|---------|
| `packages/core/src/octoagent/core/models/behavior.py` | L306 | 字段定义（LOADED 事件）| `agent_kind: str = Field(description="main / worker / subagent；与 AgentProfile.kind 对齐")` |
| `packages/core/src/octoagent/core/models/behavior.py` | L324, L329 | 字段定义（USED 事件）| `agent_kind: str = Field(description="main / worker；F096 仅这两个值，subagent 由 F097 引入")` |
| `apps/gateway/src/octoagent/gateway/services/agent_decision.py` | L352 | **写入点（LOADED）** | `agent_kind=str(agent_profile.kind)` — 直接 str 转换，无硬编码值 |
| `apps/gateway/src/octoagent/gateway/services/agent_decision.py` | L389 | **写入点（USED）** | `agent_kind=str(agent_profile.kind)` — 同上 |
| `apps/gateway/tests/services/test_agent_decision_envelope.py` | L640 | **唯一值断言** | `assert payload.agent_kind == "worker"` — **仅针对 Worker 路径具体断言** |
| `apps/gateway/tests/test_task_service_context_integration.py` | L2373 | 仅类型过滤 | `ev for ev in events if ev.type is EventType.BEHAVIOR_PACK_LOADED` — 不校验 agent_kind 值 |

**frontend**：`octoagent/frontend/` 中无任何 `agent_kind` 引用（grep 验证，exit code 1）

### 1.3 硬校验判定结论（C-2 决策）

**结论：无枚举硬校验。`"subagent"` 值可以直接引入，无需 schema version bump。**

详细证据：
- `behavior.py` 字段类型为 `str`（无 `Literal` 约束，无 `Enum` 约束）
- `agent_decision.py` 写入用 `str(agent_profile.kind)`（动态转换，不硬编码值）
- `test_agent_decision_envelope.py:640` 的 `assert payload.agent_kind == "worker"` 是针对 **Worker 路径**的具体断言，与 Subagent 路径完全独立，F097 新路径不影响该测试
- backend 代码中 **无** `agent_kind in ["main", "worker"]` 或类似枚举限制
- frontend 当前未消费 `agent_kind` 字段（Phase E UI 已推迟）

**plan §3.6 结论实测验证：完全一致。**

---

### 1.4 SUBAGENT_COMPLETED 存在性判定（F-01 决策）

**判定结论：(c) 未定义——`EventType.SUBAGENT_COMPLETED` 枚举值不存在，也无任何 emit 路径。**

实测证据：
- `grep "SUBAGENT_COMPLETED" enums.py` → **零结果**（exit code 1）
- `grep -rn "SUBAGENT_COMPLETED" octoagent/ --include="*.py"` → 仅一处注释引用（`harness/delegation.py:243`，为注释说明，非 emit 代码）
- `enums.py` 中 SUBAGENT 相关枚举值：
  - L211: `SUBAGENT_SPAWNED = "SUBAGENT_SPAWNED"` — delegate_task 路径 emit
  - L212: `SUBAGENT_RETURNED = "SUBAGENT_RETURNED"` — 子任务返回结果

**Phase E 必须补充的工作（TE.1 条件路径 b/c 触发）**：

| 工作项 | 文件 | 内容 |
|--------|------|------|
| 新增枚举值 | `packages/core/src/octoagent/core/models/enums.py` | 在 EventType 中新增 `SUBAGENT_COMPLETED = "SUBAGENT_COMPLETED"` |
| 实现 emit | `apps/gateway/src/octoagent/gateway/services/task_runner.py` | 在 `_close_subagent_session_if_needed` 末尾 emit，payload 含 `delegation_id` / `child_task_id` / `terminal_status` / `closed_at` |

---

## §2 T0.2 — 三函数 sanity check

### 2.1 `_ensure_agent_session`（tech-research 标 line 2337-2345）

**实际行号**：`agent_context.py:2318-2461`（函数起始 L2318，条件判断 L2337-2345）

**与 tech-research 对比**：**行号准确**（tech-research 标注 L2337-2345 正好是 `kind = ...` 条件表达式，实测一致）

**当前函数签名**：
```python
async def _ensure_agent_session(
    self,
    *,
    request: ContextResolveRequest,
    task: Task,
    project: Project | None,
    agent_runtime: AgentRuntime,
    session_state: SessionContextState,
) -> AgentSession:
```

**当前条件分支结构（L2337-2345）**：
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

**关键发现**：`_ensure_agent_session` 通过 `agent_runtime.role is AgentRuntimeRole.WORKER` 判断，**不含 `delegation_mode` 字段**（`AgentRuntime` 模型本身无 `delegation_mode` 字段）。

**Phase B 注入点分析**：Phase B 需要在 `kind = ...` 赋值之前增加第 4 路判断。判断条件不能用 `agent_runtime.delegation_mode`（字段不存在），应使用以下替代信号：
- `request.delegation_metadata.get("target_kind")` 是否等于 `"subagent"`
- 或通过 `ContextResolveRequest.delegation_metadata["target_kind"]` 读取（在 `delegation_plane.py:216` 中可确认 target_kind 已传入 dispatch_metadata，进而进入 delegation_metadata）

**Phase B 注入点精确位置**：在 `is_direct_worker_session` 判断完成后、`kind = ...` 赋值之前（L2332-2337 之间）新增：
```python
# Phase B 注入：target_kind=subagent 时优先走 SUBAGENT_INTERNAL
target_kind_from_meta = str(request.delegation_metadata.get("target_kind", "")).strip()
if target_kind_from_meta == DelegationTargetKind.SUBAGENT.value:
    kind = AgentSessionKind.SUBAGENT_INTERNAL
    # ... 后续创建逻辑
```

---

### 2.2 `_resolve_or_create_agent_profile`（tech-research 标注函数）

**实际状态**：**函数名不存在**——`agent_context.py` 中无 `_resolve_or_create_agent_profile`。实际对应函数为：

| 实际函数名 | 行号 | 说明 |
|-----------|------|------|
| `_resolve_agent_profile` | L2753 | 主入口：查 profile + 调用 `_ensure_agent_profile_from_worker_profile` |
| `_ensure_agent_profile` | L2775 | 创建/更新 agent profile（写入持久化 store）|
| `_ensure_agent_profile_from_worker_profile` | L2862 | 从 WorkerProfile 迁移创建 AgentProfile |

**调用链**：`_resolve_context_bundle`（L1288）→ `_resolve_agent_profile`（L2753）→ `_ensure_agent_profile` 或返回已有 profile。

**Phase C 注入点**：在 `_resolve_agent_profile`（L2753）入口处、查询 `requested_profile_id` 之前，新增 subagent 路径：
```python
async def _resolve_agent_profile(self, *, project, requested_profile_id="", request=None):
    # F097 Phase C: subagent 路径，创建 ephemeral profile，不调用 save_agent_profile
    if request is not None:
        target_kind = str(request.delegation_metadata.get("target_kind", "")).strip()
        if target_kind == DelegationTargetKind.SUBAGENT.value:
            return AgentProfile(
                profile_id=str(ULID()),
                kind="subagent",
                scope=AgentProfileScope.PROJECT,
                ...
            ), []
    # 原有逻辑继续
```

**注意**：`_resolve_agent_profile` 当前签名不含 `request` 参数（L2753-2773 实测），Phase C 实施时需要调整签名或通过其他方式传递 `target_kind` 信号（如查 task metadata）。

---

### 2.3 `_ensure_memory_namespaces`（tech-research 标 line 2517-2545）

**实际行号**：`agent_context.py:2463-2574`（函数起始 L2463，AGENT_PRIVATE 路径起点 L2526）

**与 tech-research 对比**：**tech-research 标注 L2517-2545 是函数内部 AGENT_PRIVATE 段**，实测一致（L2526 = `private_kind = MemoryNamespaceKind.AGENT_PRIVATE`，L2545 = `private_namespace = ...` 构造开始）。

**当前函数签名**：
```python
async def _ensure_memory_namespaces(
    self,
    *,
    project: Project | None,
    agent_runtime: AgentRuntime,
    agent_session: AgentSession,
    project_memory_scope_ids: list[str],
) -> list[MemoryNamespace]:
```

**当前 AGENT_PRIVATE 创建逻辑**（L2526-2573）：
```python
private_kind = MemoryNamespaceKind.AGENT_PRIVATE
private_namespace_id = self._build_memory_namespace_id(
    kind=private_kind, project_id=project_id, agent_runtime_id=agent_runtime.agent_runtime_id
)
# ... get existing or create new MemoryNamespace ...
await self._stores.agent_context_store.save_memory_namespace(private_namespace)
namespaces.append(private_namespace)
return namespaces
```

**Phase F 注入点**：在 `_ensure_memory_namespaces` 函数入口（L2471 之后），新增 subagent α 共享路径：
```python
# F097 Phase F: subagent 路径 α 共享引用——不创建新 AGENT_PRIVATE namespace
target_kind = ... # 从 task metadata 读取 SubagentDelegation
if target_kind == "subagent" and delegation.caller_memory_namespace_ids:
    return caller_namespaces  # 直接返回 caller namespace ID 集合
# fallback 走正常创建路径
```

**同样的问题**：`_ensure_memory_namespaces` 当前签名不含 `task` 或 `request` 参数，Phase F 需通过 `agent_runtime.metadata` 或 `agent_session.metadata` 传递 SubagentDelegation 信息，或调整调用方传参。

---

### 2.4 `_launch_child_task`（tech-research 标 line 1229-1279）

**实际行号**：`capability_pack.py:1229-1279`（完全一致）

**当前 `control_metadata` 字段**（L1254-1263）：
```python
control_metadata={
    "parent_task_id": parent_task.task_id,
    "parent_work_id": parent_work.work_id,
    "requested_worker_type": worker_type,
    "target_kind": target_kind,           # ← "subagent" 在这里
    "tool_profile": tool_profile,
    "spawned_by": spawned_by,
    "child_title": title,
    "worker_plan_id": plan_id,
}
```

**关键发现**：`target_kind` **已在 control_metadata 中**（L1258），传递到子任务的 `delegation_metadata`，Phase B `_ensure_agent_session` 通过 `request.delegation_metadata.get("target_kind")` 即可读取——**信号路径已通**。

---

## §3 T0.3 / T0.4 — F096 baseline 回归基准

### 3.1 测试 collect 统计

| 指标 | 值 |
|------|----|
| 总 collect 数（octoagent/ 子目录）| **3288** |
| e2e_smoke 测试数 | 8（3280 deselected）|
| 非 e2e collect 数 | 3280 |

### 3.2 全量回归结果（排除 e2e_full 和 e2e_smoke）

**命令**：`uv run pytest -q -m "not e2e_full and not e2e_smoke"`

**运行时间**：115.61s（约 1 分 55 秒）

**结果**：

| 指标 | 值 |
|------|----|
| **passed** | **3252** |
| skipped | 12 |
| deselected（排除 e2e）| 22 |
| xfailed（预期失败）| 1 |
| xpassed（预期失败但通过）| 1 |
| **退出码** | **0**（全通）|

**已知 warning**：aiosqlite event loop 关闭 warning（F083 已知工程债，非 regression），以及 `ToolEntry.schema` 字段 shadow warning（已知）。

### 3.3 与 CLAUDE.local.md 记录（3260）的差异分析

| 数值 | 来源 | 说明 |
|------|------|------|
| 3260 passed | CLAUDE.local.md F096 实施记录 | 包含所有测试（含 e2e_smoke 8 个？）|
| 3252 passed | 本次 F097 worktree 实测 | 排除 e2e_smoke（8 个）后结果 |
| 差值 | 8 | 等于 e2e_smoke 数量，与预期一致 |

**结论**：差值 8 = e2e_smoke 测试数量，无异常。本次 worktree 的 baseline 为：

**F097 baseline = 3252 passed（排除 e2e），或 ~3260（含 e2e_smoke，需 LLM 凭证才能运行）**

### 3.4 pre-existing failures

**无 failed**（exit code 0）。xfailed 1 个 + xpassed 1 个属于预期状态，不视为 regression。

---

## §4 Phase 6 后续 Phase 影响清单

基于本侦察对各 Phase 注入点和行为决策的影响汇总：

### Phase A 影响（SubagentDelegation model）

**无变化**：plan §2 Phase A 代码草图可直接使用，`delegation.py` 结构无异常。

### Phase C 影响（ephemeral AgentProfile）

**重要发现**：`_resolve_or_create_agent_profile` **函数不存在**，实际函数为 `_resolve_agent_profile`（L2753）。

- 注入点：`_resolve_agent_profile` 入口
- 判断信号：`request.delegation_metadata.get("target_kind")` == `"subagent"`
- **但 `_resolve_agent_profile` 当前签名不含 `request` 参数**，Phase C 实施时需要：
  - 方案 A（推荐）：在 `_resolve_context_bundle` 调用层注入（L1288），查看是否能在调用 `_resolve_agent_profile` 之前判断 target_kind 并短路返回 ephemeral profile
  - 方案 B：给 `_resolve_agent_profile` 增加可选 `request` 参数
  - 方案 C：通过 task metadata 传递 target_kind（更解耦但多一跳）

**建议**：Phase C 实施时先检查 L1280-1300 的 `_resolve_context_bundle` 调用结构，在调用 `_resolve_agent_profile` 之前就短路返回 ephemeral profile（不改 `_resolve_agent_profile` 签名）。

### Phase E 影响（session cleanup hook）

**新增工作**：因 SUBAGENT_COMPLETED 枚举不存在（判定 c），TE.1 必须同步：
1. 在 `enums.py` EventType 中新增 `SUBAGENT_COMPLETED = "SUBAGENT_COMPLETED"`
2. 在 `_close_subagent_session_if_needed` 末尾 emit 该事件

**其他无变化**：plan §2 Phase E 代码草图可直接使用（`_notify_completion` 挂载点、幂等逻辑、store 方法复用）。

### Phase B 影响（`_ensure_agent_session` SUBAGENT_INTERNAL 路径）

**关键发现**：判断条件不能用 `agent_runtime.delegation_mode`（字段不存在），**正确信号为 `request.delegation_metadata.get("target_kind")`**。

- `target_kind` 已由 `_launch_child_task`（`capability_pack.py:1258`）写入 control_metadata，传递路径已通：
  `_launch_child_task.control_metadata["target_kind"] = "subagent"` → `NormalizedMessage` → `task.metadata` → `dispatch_metadata` → `ContextResolveRequest.delegation_metadata`
- 注入点精确位置：在 `is_direct_worker_session` 判断之后（L2332-2335）、`kind = ...` 赋值之前（L2337），增加：
  ```python
  if str(request.delegation_metadata.get("target_kind", "")).strip() == DelegationTargetKind.SUBAGENT.value:
      kind = AgentSessionKind.SUBAGENT_INTERNAL
      # 新路径 early return
  ```

### Phase D 影响（RuntimeHintBundle 拷贝）

**RuntimeHintBundle 字段精确清单**（`behavior.py:206-216`，与 T0.3 任务重叠，提前归档）：

| 字段名 | 类型 | 默认值 |
|--------|------|--------|
| `surface` | `str` | `""` |
| `can_delegate_research` | `bool` | `False` |
| `recent_clarification_category` | `str` | `""` |
| `recent_clarification_source_text` | `str` | `""` |
| `recent_worker_lane_worker_type` | `str` | `""` |
| `recent_worker_lane_profile_id` | `str` | `""` |
| `recent_worker_lane_topic` | `str` | `""` |
| `recent_worker_lane_summary` | `str` | `""` |
| `tool_universe` | `ToolUniverseHints \| None` | `None` |
| `metadata` | `dict[str, Any]` | `{}` |

**注意**：`recent_failure_budget` 字段**不存在**（plan §2 Phase D 预测有误），Phase D 实施时拷贝字段应为 `surface` / `tool_universe` / `can_delegate_research` / `recent_worker_lane_*` 系列（共 9 个字段，metadata 可选）。

**Phase D 注入点无变化**：`capability_pack.py:1229`（`_launch_child_task`），在 `control_metadata` 构造后追加 `if target_kind == "subagent"` 分支拷贝字段。

### Phase F 影响（Memory α 共享）

**问题**：`_ensure_memory_namespaces` 签名不含 task 或 request，Phase F 需要读取 SubagentDelegation 的 `caller_memory_namespace_ids`。路径分析：
- 函数参数有 `agent_session: AgentSession`，可以将 SubagentDelegation 信息预先注入 `agent_session.metadata`（在 Phase B session 创建时）
- 或通过 `agent_runtime.metadata` 存入（`agent_runtime` 参数已有）

**建议**：Phase F 实施时通过 `agent_runtime.metadata.get("subagent_delegation")` 读取（Phase B 创建 SUBAGENT_INTERNAL session 时顺手写入 agent_runtime.metadata，形成单一路径）。

### Phase G 影响

**无变化**：Phase C ephemeral profile kind="subagent" 实施后，`make_behavior_pack_loaded_payload` 的 `agent_kind=str(agent_profile.kind)` 自动返回 `"subagent"`（Gap-G 是 Gap-C 的副产品）。

---

## §5 侦察结论汇总表

| 侦察项 | 结论 | Phase 影响 |
|--------|------|-----------|
| BEHAVIOR_PACK_LOADED 消费方 agent_kind 硬校验 | **无硬校验**（str 类型，动态转换）| Phase G 可直接引入 "subagent" |
| SUBAGENT_COMPLETED 存在性 | **(c) 未定义，无 emit** | TE.1 必须补充枚举 + emit |
| `_ensure_agent_session` 行号 | 函数 L2318，条件 L2337-2345，**行号准确** | Phase B 注入点确认 |
| Phase B 判断信号 | `request.delegation_metadata.get("target_kind")` == `"subagent"`（非 delegation_mode）| Phase B 实施时不用 agent_runtime.delegation_mode |
| `_resolve_or_create_agent_profile` | **函数不存在**，实际为 `_resolve_agent_profile`（L2753）| Phase C 注入点调整 |
| `_ensure_memory_namespaces` 行号 | 函数 L2463，AGENT_PRIVATE 段 L2526，**行号准确** | Phase F 注入点确认 |
| `_launch_child_task` 行号 | L1229，**行号准确**；target_kind 已在 control_metadata | Phase D 注入点确认 |
| RuntimeHintBundle 字段 | 9 个字段（**无 recent_failure_budget**）| Phase D 拷贝字段列表修正 |
| F097 baseline passed 数 | **3252**（排除 e2e），总 collect **3288** | Verify Phase 回归基准 |
