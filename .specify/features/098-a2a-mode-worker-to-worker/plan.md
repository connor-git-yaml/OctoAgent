# F098 Plan — A2A Mode + Worker↔Worker 实施计划（v0.2 Pre-Impl Codex Review 闭环）

**关联**: spec.md v0.1（GATE_DESIGN 已锁定）+ clarification.md + phase-0-recon.md
**Phase 数**: 9（B / C / D / E / F / G / H / I / J）+ Phase 0 已完成
**Phase 顺序**: E → F → B → C → I → H → G → J → D → Verify
**baseline**: F097 (origin/master 4441a5a)

---

## 0. Phase 顺序 & 依赖图

```
Phase 0 实测 ✓
   │
   ├─→ Phase E (CONTROL_METADATA_UPDATED) ─┐
   │                                       │
   ├─→ Phase F (subagent runtime 独立) ────┤
   │                                       │
   │                      ┌────────────────┘
   │                      ↓
   │                Phase B (A2A target profile 独立)
   │                      ↓
   │                Phase C (Worker→Worker 解禁)
   │                      ↓
   │                Phase I (worker audit chain test)
   │                      ↓
   │                Phase H (终态统一 cleanup hook)
   │                      ↓
   │                Phase G (atomic 事务边界)
   │                      ↓
   │                Phase J (BaseDelegation 抽象)
   │                      ↓
   │                Phase D (orchestrator → dispatch_service 拆分)
   │                      ↓
   └────────────→ Verify (spec-review + quality-review + Final Codex)
```

**关键依赖**：
- E 与 F 互不依赖（E 是 event 模型，F 是 runtime 路径）。**先 E 后 F** 更安全（event model 改动作为基础设施）
- B 依赖 F（receiver runtime 路径稳定后 profile 独立加载更稳）
- C 依赖 B（worker→worker A2A 路径需 receiver 真独立才有意义）
- I 依赖 C（解禁后才能测 worker→worker audit chain）
- H 先 G 后（结构改造先；G 受益于 H 已统一的 hook）
- J 可与 H/G 并行（不依赖 task state machine 改造）—— 实际放在 G 后简化串行
- D 最后（最大文件改动，避免与其他 Phase 的 import 冲突）

---

## 1. Phase E — CONTROL_METADATA_UPDATED 引入（P1-1 修复）

**目标**：解决 F097 P1-1 USER_MESSAGE 复用污染（spec.md §3 块 E）。

### 1.1 改动文件清单

| 文件 | 改动 | 估计行数 |
|------|------|---------|
| `packages/core/src/octoagent/core/models/enums.py` | 新增 `EventType.CONTROL_METADATA_UPDATED` | +1 |
| `packages/core/src/octoagent/core/models/payloads.py` | 新增 `ControlMetadataUpdatedPayload` | +20 |
| `apps/gateway/src/octoagent/gateway/services/connection_metadata.py` | `merge_control_metadata` 合并两类 events | +30 / -10 |
| `apps/gateway/src/octoagent/gateway/services/task_runner.py` | `_emit_subagent_delegation_init_if_needed` 改 event type | +30 / -25 |
| `apps/gateway/src/octoagent/gateway/services/agent_context.py` | B-3 backfill 改 event type | +30 / -25 |
| `apps/gateway/tests/services/test_phase_e_control_metadata_updated.py` | 新增单测 | +400 |
| `apps/gateway/tests/services/test_phase_e_backward_compat.py` | 新增向后兼容测试 | +200 |

**净增减估计**：+~711 / -~60

### 1.2 关键代码点

**1.2.1 ControlMetadataUpdatedPayload 设计**：

```python
# packages/core/src/octoagent/core/models/payloads.py

class ControlMetadataUpdatedPayload(BaseModel):
    """F098 块 E：承载 control_metadata 更新的 first-class event payload。
    
    与 USER_MESSAGE 不同，CONTROL_METADATA_UPDATED 不含 text/text_preview，
    避免污染对话历史 consumer（context_compaction / chat / telegram 等）。
    """
    control_metadata: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(default="")  # 描述 emit 来源
    # source 候选值（未强制 enum，便于扩展）：
    #   "subagent_delegation_init"      — task_runner._emit_subagent_delegation_init_if_needed
    #   "subagent_delegation_session_backfill"  — agent_context._ensure_agent_session B-3
    #   "<future_source>"               — 后续 Feature 可扩展
```

**1.2.2 merge_control_metadata 演化**：

```python
# apps/gateway/src/octoagent/gateway/services/connection_metadata.py

def merge_control_metadata(events: Iterable[Event]) -> dict[str, Any]:
    """按 turn/task 生命周期合并 USER_MESSAGE + CONTROL_METADATA_UPDATED control metadata。"""
    
    # F098 块 E：合并两类 events，按 task_seq/ts 时序排列
    relevant_events = [
        event for event in events
        if event.type in (EventType.USER_MESSAGE, EventType.CONTROL_METADATA_UPDATED)
    ]
    if not relevant_events:
        return {}
    
    # 取最新一个事件作为 latest（用于 TURN_SCOPED 字段）
    # 注意：CONTROL_METADATA_UPDATED 与 USER_MESSAGE 都可承载 TURN_SCOPED
    latest_payload = relevant_events[-1].payload
    latest_control = control_metadata_from_payload(latest_payload)
    # ...其余合并逻辑保持（TURN_SCOPED + TASK_SCOPED）...
```

**1.2.3 task_runner.py:287 写入点改造**：

```python
# 当前：
event = Event(
    ...
    type=EventType.USER_MESSAGE,
    payload=UserMessagePayload(
        text_preview="[subagent delegation metadata]",
        text_length=0,
        text="",
        control_metadata={...},
    ).model_dump(),
    ...
)

# F098 改造：
event = Event(
    ...
    type=EventType.CONTROL_METADATA_UPDATED,
    payload=ControlMetadataUpdatedPayload(
        control_metadata={...},
        source="subagent_delegation_init",
    ).model_dump(),
    ...
)
```

**1.2.4 向后兼容**：

- 历史 USER_MESSAGE 含 subagent_delegation 的事件：`merge_control_metadata` 仍合并（因 USER_MESSAGE 也在合并列表）
- `context_compaction._load_conversation_turns` 不变（继续过滤 USER_MESSAGE，但新写入不再产生 marker text）
- 不需要 migration 命令（行为合并 + 历史数据 backwards compatible）

### 1.3 测试设计

| 测试文件 | 测试场景 | 验收 AC |
|----------|---------|---------|
| test_phase_e_control_metadata_updated.py | CONTROL_METADATA_UPDATED 事件正确 emit | AC-E1/E2/E3 |
| | merge_control_metadata 合并两类 events | AC-E4 |
| | _load_conversation_turns 不再被污染（subagent task 首轮）| AC-E5 |
| test_phase_e_backward_compat.py | 历史 USER_MESSAGE 含 subagent_delegation 仍可读 | AC-E6 |
| | merge_control_metadata 兼容混合事件流（USER_MESSAGE + CONTROL_METADATA_UPDATED）| AC-E6 |

### 1.4 Codex review 节点

- **Phase E pre-review**：检查 EventType 演化的向后兼容设计
- **Phase E post-review**：检查 merge_control_metadata 合并语义 + 写入点改造完整性

---

## 2. Phase F — Ephemeral Runtime 独立路径（P1-2 修复）

**目标**：解决 F097 P1-2 ephemeral subagent runtime 复用 caller worker runtime（spec.md §3 块 F）。

### 2.1 改动文件清单

| 文件 | 改动 | 估计行数 |
|------|------|---------|
| `apps/gateway/src/octoagent/gateway/services/agent_context.py` | `_ensure_agent_runtime` subagent 路径独立 | +25 / -5 |
| `apps/gateway/tests/services/test_phase_f_ephemeral_runtime.py` | 新增单测 | +250 |

**净增减估计**：+~275 / -~5

### 2.2 关键代码点

```python
# apps/gateway/src/octoagent/gateway/services/agent_context.py:2237

async def _ensure_agent_runtime(
    self,
    *,
    request: ContextResolveRequest,
    project: Project | None,
    agent_profile: AgentProfile,
) -> AgentRuntime:
    role = self._resolve_agent_runtime_role(request)
    project_id = project.project_id if project is not None else ""
    worker_profile_id = str(
        resolve_delegation_target_profile_id(request.delegation_metadata)
    ).strip()
    if not worker_profile_id and role is AgentRuntimeRole.WORKER:
        worker_profile_id = str(
            agent_profile.metadata.get("source_worker_profile_id", "")
        ).strip()
    
    # F098 Phase F: subagent 路径检测 → 跳过 find_active_runtime 复用
    is_subagent_path = (
        str(request.delegation_metadata.get("target_kind", "")).strip()
        == DelegationTargetKind.SUBAGENT.value
        or str(getattr(agent_profile, "kind", "")).strip() == "subagent"
    )
    
    runtime_id = (request.agent_runtime_id or "").strip()
    existing: AgentRuntime | None = None
    if runtime_id:
        existing = await self._stores.agent_context_store.get_agent_runtime(runtime_id)
    
    # F098 Phase F: subagent 路径不走 find_active_runtime 复用
    if existing is None and not is_subagent_path:
        existing = await self._stores.agent_context_store.find_active_runtime(
            project_id=project_id,
            role=role,
            worker_profile_id=worker_profile_id,
            agent_profile_id=agent_profile.profile_id,
        )
        if existing is not None:
            runtime_id = existing.agent_runtime_id
    
    if not runtime_id:
        runtime_id = f"runtime-{ULID()}"
    
    # F098 Phase F: 提取 subagent_delegation_id 到 metadata（audit 关联）
    subagent_delegation_id = ""
    if is_subagent_path:
        raw_delegation = request.delegation_metadata.get("__subagent_delegation_init__", {})
        if isinstance(raw_delegation, dict):
            subagent_delegation_id = str(raw_delegation.get("delegation_id", "")).strip()
    
    # ...其余 worker_profile 解析 + runtime 构造保持...
    
    # 在 metadata 加 subagent_delegation_id（仅 subagent 路径）
    runtime = (
        existing.model_copy(...)
        if existing is not None
        else AgentRuntime(
            agent_runtime_id=runtime_id,
            ...
            metadata={
                "surface": request.surface,
                "request_kind": request.request_kind.value,
                "worker_capability": worker_capability,
                "selected_worker_type": request.delegation_metadata.get(
                    "selected_worker_type", ""
                ),
                **({"subagent_delegation_id": subagent_delegation_id} if subagent_delegation_id else {}),
            },
        )
    )
    # ...保存 runtime 逻辑保持...
```

### 2.3 测试设计

| 测试场景 | 验收 AC |
|---------|---------|
| subagent path → 不复用 caller worker runtime | AC-F1 |
| subagent AgentRuntime.metadata 含 subagent_delegation_id | AC-F2 |
| main / worker 路径行为不变（regression）| AC-F3 |
| caller worker active runtime 与 subagent runtime 独立 | AC-F1 |

### 2.4 Codex review 节点

- **Phase F post-review**：检查 subagent 信号检测覆盖度（target_kind / agent_profile.kind 双信号）

---

## 3. Phase B — A2A Source/Target 双向独立加载（H3-B 核心，Codex review P1 闭环）

**目标**：A2A source + target 双向从 runtime_context/envelope 派生（spec.md §3 块 B + Codex review P1 闭环）。

### 3.1 改动文件清单

| 文件 | 改动 | 估计行数 |
|------|------|---------|
| `apps/gateway/src/octoagent/gateway/services/orchestrator.py` | `_prepare_a2a_dispatch` source/target 双向独立解析（**B-1 新增 source 派生 + B-2 target 解析**）| +120 / -25 |
| `apps/gateway/tests/services/test_phase_b_a2a_source_target.py` | 新增单测（覆盖 B-1 + B-2）| +500 |

**净增减估计**：+~620 / -~25（**Codex review P1 闭环：双向修复一起 commit**）

### 3.2 关键代码点

#### B-1: Source 派生（**Codex review P1 闭环新增**）

```python
# apps/gateway/src/octoagent/gateway/services/orchestrator.py

def _resolve_a2a_source_role(
    self,
    *,
    runtime_context: RuntimeContext | None,
    runtime_metadata: dict[str, Any],
    envelope_metadata: dict[str, Any],
) -> tuple[AgentRuntimeRole, AgentSessionKind, str]:
    """F098 Phase B-1: 从 runtime_context/envelope 派生 A2A source role / session_kind / agent_uri。
    
    Codex review P1 闭环：worker→worker A2A 不能再硬编码 source = MAIN/MAIN_BOOTSTRAP/main.agent。
    """
    # 优先从 runtime_context.runtime_kind 派生
    runtime_kind = ""
    if runtime_context is not None:
        runtime_kind = str(getattr(runtime_context, "runtime_kind", "") or "").strip().lower()
    
    # fallback: envelope metadata (e.g., source_runtime_kind)
    if not runtime_kind:
        runtime_kind = str(envelope_metadata.get("source_runtime_kind", "")).strip().lower()
    
    # 派生 source role
    if runtime_kind == RuntimeKind.WORKER.value:
        source_capability = str(
            runtime_metadata.get("source_worker_capability", "")
            or envelope_metadata.get("source_worker_capability", "")
        ).strip()
        return (
            AgentRuntimeRole.WORKER,
            AgentSessionKind.WORKER_INTERNAL,
            self._agent_uri(f"worker.{source_capability or 'unknown'}"),
        )
    # default: main path（regression 防护）
    return (
        AgentRuntimeRole.MAIN,
        AgentSessionKind.MAIN_BOOTSTRAP,
        self._agent_uri("main.agent"),
    )

# _prepare_a2a_dispatch 改动:
# 当前 line 2280:
# source_runtime = await self._ensure_a2a_agent_runtime(
#     role=AgentRuntimeRole.MAIN,  # 硬编码
#     ...
# )

# F098 Phase B-1:
source_role, source_session_kind, source_agent_uri = self._resolve_a2a_source_role(
    runtime_context=runtime_context,
    runtime_metadata=runtime_metadata,
    envelope_metadata=envelope.metadata,
)
source_runtime = await self._ensure_a2a_agent_runtime(
    agent_runtime_id=source_agent_runtime_id,
    role=source_role,  # 派生
    project_id=project_id,
    agent_profile_id=source_agent_profile_id,
    worker_profile_id=str(runtime_metadata.get("source_worker_profile_id", "")).strip(),
    worker_capability=str(runtime_metadata.get("source_worker_capability", "")).strip(),
)
source_session = await self._ensure_a2a_agent_session(
    ...
    kind=source_session_kind,  # 派生
    ...
)
# source_agent_uri 用派生值代替硬编码 "main.agent"
```

#### B-2: Target 解析（**修订：用 `_delegation_plane.capability_pack` 路径**）

```python
async def _resolve_target_agent_profile(
    self,
    *,
    requested_worker_profile_id: str,
    worker_capability: str,
    fallback_source_profile_id: str,
) -> str:
    """F098 Phase B-2: A2A target Worker 加载自己的 AgentProfile。
    
    Codex review P2 闭环：通过 _delegation_plane.capability_pack 访问 capability_pack（orchestrator 不直接持引用），
    且 fallback fail-loud（不静默吞 except 用 source profile）。
    """
    # 路径 1: 按 requested_worker_profile_id 直接 lookup
    if requested_worker_profile_id:
        profile = await self._stores.agent_context_store.get_agent_profile(
            requested_worker_profile_id,
        )
        if profile is not None:
            return profile.profile_id
        # fail-loud：明确 lookup 失败但不吞 except → 走 capability fallback
        log.warning(
            "a2a_target_profile_explicit_id_not_found",
            requested_worker_profile_id=requested_worker_profile_id,
        )
    
    # 路径 2: 按 worker_capability 派生 default profile via _delegation_plane.capability_pack
    if worker_capability and self._delegation_plane is not None:
        capability_pack = self._delegation_plane.capability_pack  # 现有访问路径
        if capability_pack is not None:
            default_profile = await capability_pack.resolve_worker_agent_profile(
                worker_capability=worker_capability,
            )
            if default_profile is not None:
                return default_profile.profile_id
            log.warning(
                "a2a_target_profile_capability_no_default",
                worker_capability=worker_capability,
            )
    
    # 路径 3: fallback (warning log + 测试 fail-loud)
    log.warning(
        "a2a_target_profile_fallback_to_source",
        requested_worker_profile_id=requested_worker_profile_id,
        worker_capability=worker_capability,
        fallback_source_profile_id=fallback_source_profile_id,
    )
    return fallback_source_profile_id

# _prepare_a2a_dispatch 改动 (line 2299):
# F098 Phase B-2:
target_agent_profile_id = await self._resolve_target_agent_profile(
    requested_worker_profile_id=requested_worker_profile_id,
    worker_capability=worker_capability_hint,
    fallback_source_profile_id=source_agent_profile_id,
)
target_runtime = await self._ensure_a2a_agent_runtime(
    agent_runtime_id=str(envelope.metadata.get("target_agent_runtime_id", "")),
    role=AgentRuntimeRole.WORKER,
    project_id=project_id,
    agent_profile_id=target_agent_profile_id,  # 独立 profile
    worker_profile_id=requested_worker_profile_id,
    worker_capability=worker_capability_hint,
)
```

### 3.3 测试设计

| 测试场景 | 验收 AC |
|---------|---------|
| **B-1 source role 派生**：worker→worker 场景 source 是 worker / WORKER_INTERNAL / `worker.<cap>` | AC-B1-S1 |
| **B-1 source main 防回归**：main→worker 场景 source 仍是 main | AC-B1-S2 |
| **B-1 A2AConversation source 字段**反映真实 source | AC-B1-S3 |
| **B-1 source 派生 fallback**：metadata 缺失优雅降级 | AC-B1-S4 |
| **B-2 路径 1**: requested_worker_profile_id 直接 lookup | AC-B2-T1 |
| **B-2 路径 2**: worker_capability 派生（**通过 _delegation_plane.capability_pack**） | AC-B2-T2 |
| **B-2 路径 3 fail-loud**：lookup/capability resolve 失败时**不**静默吞 except 用 source profile | AC-B2-T2 |
| **B-2 target_profile_id != source_profile_id** | AC-B2-T1 |
| A2A receiver context 4 层身份独立（profile / runtime / session / Memory namespace）| AC-B2-T3 |

### 3.4 Codex review 节点

- **Phase B post-review**：检查 fallback 行为 + worker_capability resolver 完整性

---

## 4. Phase C — Worker→Worker A2A 解禁

**目标**：删除 `enforce_child_target_kind_policy` 硬禁止（spec.md §3 块 C）。

### 4.1 改动文件清单

| 文件 | 改动 | 估计行数 |
|------|------|---------|
| `apps/gateway/src/octoagent/gateway/services/capability_pack.py` | 删除调用 + 函数定义 | -30 |
| `apps/gateway/src/octoagent/gateway/services/delegation_plane.py` | 注释更新（3 处）| +5 / -10 |
| `apps/gateway/tests/services/test_capability_pack_phase_d.py` | 删除 mock + 改正面测试 | +30 / -15 |
| `apps/gateway/tests/services/test_phase_c_worker_to_worker.py` | 新增正面测试 | +250 |

**净增减估计**：+~285 / -~55

### 4.2 关键代码点

```python
# apps/gateway/src/octoagent/gateway/services/capability_pack.py

# 删除 line 1252:
# self.enforce_child_target_kind_policy(target_kind)

# 删除 line 1355-1381:
# @staticmethod
# def enforce_child_target_kind_policy(target_kind: str) -> None:
#     ...完整函数...
```

### 4.3 测试设计

| 测试场景 | 验收 AC |
|---------|---------|
| Worker 调用 delegate_task(target_kind="worker") 不再 raise | AC-C1 |
| child Worker 任务正常 spawn | AC-C2 |
| audit chain: AgentSession.parent_worker_runtime_id 正确填充 | AC-C3 |
| 删除前测试 mock _enforce_child_target_kind_policy 已清理（grep）| AC-C1 |

### 4.4 Codex review 节点

- **Phase C post-review**：检查解禁是否影响 max_depth 死循环防护（应不影响）+ DelegationManager 行为

---

## 5. Phase I — Worker Audit Chain 集成测（F096 H2 推迟项）

**目标**：补 worker_capability 路径完整 audit chain 集成测（spec.md §3 块 I）。

### 5.1 改动文件清单

| 文件 | 改动 | 估计行数 |
|------|------|---------|
| `apps/gateway/tests/integration/test_phase_i_worker_audit_chain.py` | 新增集成测 | +400 |

**净增减估计**：+400 / -0

### 5.2 关键代码点

```python
# apps/gateway/tests/integration/test_phase_i_worker_audit_chain.py

async def test_f098_audit_chain_worker_dispatch():
    """F098 Phase I: worker_capability 路径完整 audit chain 验证（F096 H2 推迟项归位）。"""
    # arrange: 创建 WorkerProfile + dispatch_metadata.worker_capability
    # act: 调用 delegate_task tool 触发 worker AgentRuntime 创建
    # assert:
    #   1. BEHAVIOR_PACK_LOADED.agent_kind == "worker"
    #   2. AgentRuntime.kind == WORKER
    #   3. AgentProfile.profile_id ↔ AgentRuntime.profile_id（Layer 1）
    #   4. AgentRuntime.profile_id ↔ BEHAVIOR_PACK_LOADED.agent_id（Layer 2）
    #   5. BEHAVIOR_PACK_LOADED.agent_id ↔ RecallFrame.agent_runtime_id（Layer 3）
    #   6. delegation_mode 区分（"main_delegate" vs "a2a"）
    pass

async def test_f098_audit_chain_worker_to_worker_dispatch():
    """F098 Phase I + Phase C 协同：worker → worker A2A 路径 audit chain 验证。"""
    # arrange: 主 Agent 派发 Worker A，Worker A 派发 Worker B
    # assert:
    #   1. Worker B AgentSession.parent_worker_runtime_id == Worker A AgentRuntime.agent_runtime_id
    #   2. Worker A AgentSession.parent_agent_session_id == 主 Agent AgentSession.agent_session_id
    #   3. audit chain 4 层链式追溯
    pass
```

### 5.3 Codex review 节点

- **Phase I post-review**：检查测试覆盖维度的完整性

---

## 6. Phase H — 终态统一 Cleanup Hook（task state machine 改造）

**目标**：cleanup hook 挪到 `task_service._write_state_transition` 终态层（spec.md §3 块 H）。

### 6.1 改动文件清单

| 文件 | 改动 | 估计行数 |
|------|------|---------|
| `apps/gateway/src/octoagent/gateway/services/task_service.py` | 加 `_terminal_state_callbacks` 注册机制 | +60 / -5 |
| `apps/gateway/src/octoagent/gateway/services/task_runner.py` | 注册 cleanup callback + 移除手动调用 | +20 / -50 |
| `apps/gateway/tests/services/test_phase_h_terminal_callback.py` | 新增单测 | +400 |

**净增减估计**：+~480 / -~55

### 6.2 关键代码点

**6.2.1 TaskService 加 实例级 callback 注册机制**（**Codex review P2 闭环**：避免 class-level 泄漏）：

```python
# apps/gateway/src/octoagent/gateway/services/task_service.py

class TaskService:
    def __init__(self, ...):
        # ...原有初始化保持...
        # F098 Phase H: 实例级 callback list（不是 class-level，避免泄漏）
        self._terminal_state_callbacks: list[Callable[[str], Awaitable[None]]] = []
        self._terminal_state_callbacks_lock = asyncio.Lock()
    
    async def register_terminal_state_callback(
        self,
        callback: Callable[[str], Awaitable[None]],
    ) -> None:
        """F098 Phase H: 注册 task 终态回调（task_runner 等通过此机制注册 cleanup）。
        
        Codex review P2 闭环：实例级 + 幂等（按 callback identity 检测重复）。
        """
        async with self._terminal_state_callbacks_lock:
            # 幂等：同一 callback 重复 register 仅生效一次
            if callback in self._terminal_state_callbacks:
                log.debug(
                    "terminal_state_callback_already_registered",
                    callback=getattr(callback, "__qualname__", str(callback)),
                )
                return
            self._terminal_state_callbacks.append(callback)
    
    async def unregister_terminal_state_callback(
        self,
        callback: Callable[[str], Awaitable[None]],
    ) -> None:
        """F098 Phase H: 注销 callback（TaskRunner.shutdown 必须调用，避免泄漏）。
        
        Codex review P2 闭环：避免旧 TaskRunner callback 残留。
        """
        async with self._terminal_state_callbacks_lock:
            try:
                self._terminal_state_callbacks.remove(callback)
            except ValueError:
                pass  # 已不在 list（重复 unregister 或 register 未成功）
    
    async def _write_state_transition(...):
        # ...原有 _append_event_and_update_task_with_retry 逻辑保持...
        if cleanup_lock:
            await self._cleanup_task_lock(task_id)
            # F098 Phase H: 终态触发所有 callback
            await self._invoke_terminal_state_callbacks(task_id)
        # ...
    
    async def _invoke_terminal_state_callbacks(self, task_id: str) -> None:
        """F098 Phase H: 调用所有注册的 terminal state callbacks（异常隔离）。"""
        # 在 lock 内拷贝 snapshot，避免并发 register/unregister 导致 RuntimeError
        async with self._terminal_state_callbacks_lock:
            callbacks_snapshot = list(self._terminal_state_callbacks)
        for callback in callbacks_snapshot:
            try:
                await callback(task_id)
            except Exception as exc:
                log.warning(
                    "terminal_state_callback_failed",
                    task_id=task_id,
                    callback=getattr(callback, "__qualname__", str(callback)),
                    error=str(exc),
                )
```

**6.2.2 TaskRunner 注册 cleanup callback + shutdown 注销**（**Codex review P2 闭环**）：

```python
# apps/gateway/src/octoagent/gateway/services/task_runner.py

class TaskRunner:
    def __init__(self, *, task_service: TaskService, ...):
        # ...原有初始化保持...
        # F098 Phase H: 持有 task_service 引用，不再 fly-by-night 创建 TaskService
        self._task_service = task_service
        # 注册时机推迟到 start() / __aenter__ 而非 __init__（确保 event loop 可用）
    
    async def start(self) -> None:
        """启动 TaskRunner：注册 callback 到 task_service。"""
        # F098 Phase H: 实例级注册（task_service 是注入的实例）
        await self._task_service.register_terminal_state_callback(
            self._close_subagent_session_if_needed,
        )
    
    async def shutdown(self) -> None:
        """关闭 TaskRunner：注销 callback，避免泄漏。
        
        Codex review P2 闭环：旧 TaskRunner 不应被 callback 持有引用。
        """
        await self._task_service.unregister_terminal_state_callback(
            self._close_subagent_session_if_needed,
        )
        # ...其他 shutdown 逻辑（取消 jobs / close stores 等）...
    
    async def __aenter__(self):
        await self.start()
        return self
    
    async def __aexit__(self, *exc_info):
        await self.shutdown()
    
    # 移除以下手动调用：
    # task_runner.py:679 - dispatch exception 路径
    # task_runner.py:711 - mark_failed 非终态分支
    # task_runner.py:764 - _notify_completion
```

**6.2.3 测试 fixture 设计**（**Codex review P2 闭环**）：

```python
# apps/gateway/tests/conftest.py 或 test fixture

@pytest.fixture
async def task_runner(task_service):
    """F098 Phase H: TaskRunner fixture 必须 start/shutdown 配对，避免 callback 泄漏。"""
    runner = TaskRunner(task_service=task_service, ...)
    await runner.start()  # 注册 callback
    try:
        yield runner
    finally:
        await runner.shutdown()  # 注销 callback（必须在 yield 之外，避免测试失败时泄漏）
```

### 6.3 测试设计

| 测试场景 | 验收 AC |
|---------|---------|
| _write_state_transition 终态触发 callback | AC-H1 |
| cleanup callback 注册 + 终态触发 | AC-H2 |
| task_runner.py grep `_close_subagent_session_if_needed` 仅在定义和注册处 | AC-H3 |
| mark_failed / mark_cancelled / dispatch exception / shutdown 4 路径自动 cleanup | AC-H4 |
| callback 异常 → state transition 仍成功 | AC-H5 |
| **callback 幂等注册**：重复 register 同一 callback 触发次数仍为 1（Codex review P2 闭环）| AC-H6 |
| **callback 生命周期**：TaskRunner.shutdown 后 callback list 不包含 self；GC 后旧 TaskRunner 可回收（Codex review P2 闭环）| AC-H7 |
| **多 TaskService 实例**：不同 TaskService 的 callback list 不串扰（实例级验证）| AC-H1 |

### 6.4 Codex review 节点

- **Phase H pre-review**（强制）：检查 task state machine 改造的影响面 + callback 设计模式 + **生命周期管理**（Codex P2 闭环）
- **Phase H post-review**（强制）：检查所有终态路径覆盖 + 异常隔离 + **callback 注销正确性**

---

## 7. Phase G — Atomic 事务边界

**目标**：cleanup 流程 event + session 同事务（spec.md §3 块 G）。

### 7.1 改动文件清单

| 文件 | 改动 | 估计行数 |
|------|------|---------|
| `packages/core/src/octoagent/core/store/event_store.py` | 新增 `append_event_pending` API | +30 |
| `packages/core/src/octoagent/core/store/agent_context_store.py` | 新增 `save_agent_session_pending` API | +20 |
| `apps/gateway/src/octoagent/gateway/services/task_runner.py` | `_close_subagent_session_if_needed` 改 atomic | +30 / -40 |
| `apps/gateway/tests/store/test_phase_g_atomic_event_session.py` | 新增单测 | +300 |

**净增减估计**：+~380 / -~40

### 7.2 关键代码点

**7.2.1 EventStore.append_event_pending**：

```python
# packages/core/src/octoagent/core/store/event_store.py

async def append_event_pending(
    self,
    event: Event,
    *,
    update_task_pointer: bool = False,
) -> None:
    """F098 Phase G: 写入 event 但不 commit（caller 负责 commit）。
    
    与 append_event_committed 区别：
    - append_event_committed: 内部 commit
    - append_event_pending: 不 commit，caller 在 atomic 事务边界统一 commit
    
    用途：cleanup 流程需要 event + session 同事务时使用。
    """
    # ...write event 到 sqlite 但不 await commit()...
```

**7.2.2 cleanup 流程 atomic 改造**：

```python
# apps/gateway/src/octoagent/gateway/services/task_runner.py

async def _close_subagent_session_if_needed(self, task_id: str) -> None:
    # ...前置检查保持...
    
    try:
        # F098 Phase G: atomic 事务包装 event + session
        if should_emit_event:
            await self._stores.event_store.append_event_pending(
                completed_event,
                update_task_pointer=False,
            )
        
        if session is not None and session.status != AgentSessionStatus.CLOSED:
            updated_session = session.model_copy(...)
            # F098 Phase G: pending 写入（不 commit）
            await self._stores.agent_context_store.save_agent_session_pending(updated_session)
        
        # F098 Phase G: 单一 atomic commit
        await self._stores.conn.commit()
        
    except Exception as cleanup_exc:
        # F098 Phase G: 失败 rollback（atomic 保护）
        try:
            await self._stores.conn.rollback()
        except Exception as rollback_exc:
            log.error(
                "subagent_cleanup_rollback_failed",
                task_id=task_id,
                error=str(rollback_exc),
            )
        log.warning(...)
```

### 7.3 测试设计

| 测试场景 | 验收 AC |
|---------|---------|
| append_event_pending API（pending → commit/rollback 两条路径）| AC-G1 |
| atomic 事务：fault 注入 → rollback → idempotency 重试 | AC-G2/G3 |
| 重试时 idempotency_key 守护不重复 emit | AC-G3 |

### 7.4 Codex review 节点

- **Phase G post-review**：检查 atomic 事务设计的边界 + idempotency 协同

---

## 8. Phase J — BaseDelegation 公共抽象

**目标**：提取 BaseDelegation 父类（spec.md §3 块 J）。

### 8.1 改动文件清单

| 文件 | 改动 | 估计行数 |
|------|------|---------|
| `packages/core/src/octoagent/core/models/delegation_base.py` | 新增 BaseDelegation | +50 |
| `packages/core/src/octoagent/core/models/subagent_delegation.py`（如有）OR `subagent_delegation_models.py` | 修改 SubagentDelegation 继承 | +5 / -10 |
| `packages/core/src/octoagent/core/models/__init__.py` | re-export | +1 |
| `packages/core/tests/models/test_phase_j_base_delegation.py` | 新增单测 | +200 |

**净增减估计**：+~256 / -~10

### 8.2 关键代码点

```python
# packages/core/src/octoagent/core/models/delegation_base.py（新建）

from datetime import UTC, datetime
from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class BaseDelegation(BaseModel):
    """F098 Phase J: F097 SubagentDelegation + 后续 A2A delegation 公共抽象基类。
    
    共享字段（7+）：delegation_id / parent_task_id / parent_work_id / child_task_id /
    spawned_by / created_at / closed_at / caller_agent_runtime_id。
    
    子类区分：
    - SubagentDelegation（F097）: spawn-and-die / shared context
    - 未来扩展（F099+）: long-lived A2A delegation（可能不持久化此类型，A2AConversation 已是载体）
    """
    delegation_id: str = Field(min_length=1)
    parent_task_id: str = Field(min_length=1)
    parent_work_id: str = Field(default="")
    child_task_id: str | None = None
    spawned_by: str = Field(default="")
    created_at: datetime = Field(default_factory=_utc_now)
    closed_at: datetime | None = None
    caller_agent_runtime_id: str = Field(default="")
```

```python
# packages/core/src/octoagent/core/models/subagent_delegation.py（修改）

from .delegation_base import BaseDelegation


class SubagentDelegation(BaseDelegation):
    """F097: spawn-and-die / shared context（α 共享 caller AGENT_PRIVATE）。
    
    F098 Phase J: 继承 BaseDelegation 提取共享字段（不动子类语义）。
    """
    caller_project_id: str = Field(default="")
    child_agent_session_id: str | None = None
    # ...其他 F097 已有的 SubagentDelegation 专属字段...
```

### 8.3 测试设计

| 测试场景 | 验收 AC |
|---------|---------|
| BaseDelegation 字段完整性 | AC-J1 |
| SubagentDelegation 继承不破坏子类语义 | AC-J2 |
| F097 SubagentDelegation 已有测试 0 regression | AC-J2 |

### 8.4 Codex review 节点

- **Phase J post-review**：检查抽象设计 + 序列化 round-trip 兼容性

---

## 9. Phase D — orchestrator.py 拆分（D7 架构债）

**目标**：orchestrator.py → orchestrator.py + dispatch_service.py（spec.md §3 块 D）。

### 9.1 改动文件清单

| 文件 | 改动 | 估计行数 |
|------|------|---------|
| `apps/gateway/src/octoagent/gateway/services/orchestrator.py` | 移除已挪迁函数 | -1500 |
| `apps/gateway/src/octoagent/gateway/services/dispatch_service.py` | 新建 | +1500 |
| 各处 import 引用 | 更新 import 路径（多文件）| +50 / -50 |

**净增减估计**：约平衡（纯移动）

### 9.2 拆分清单

**保留 orchestrator.py**（编排层，目标 ≤ 2000 行）：
- 类: `MainAgentRouter` / `OrchestratorPolicy` / `OrchestratorRouter` / `OrchestratorService`
- 主要方法：`__init__` / `dispatch` / `dispatch_prepared` / `route` / `evaluate` / approval / worker handler 注册
- 决策：`_resolve_routing_decision` / `_normalize_requested_worker_lens` / `_resolve_single_loop_*`
- runtime hints：`_build_request_runtime_hints` / `_resolve_recent_worker_lane`
- trace metadata：`_build_decision_trace_metadata`

**挪入 dispatch_service.py**（路由 + target resolution，目标 ≈ 1500 行）：
- A2A 路径：`_prepare_a2a_dispatch` / `_persist_a2a_terminal_message` / `_save_a2a_message` / `_write_a2a_message_event`
- A2A helper: `_ensure_a2a_agent_runtime` / `_ensure_a2a_agent_session` / `_agent_uri`
- Dispatch 路径：`_dispatch_inline_decision` / `_dispatch_direct_execution` / `_dispatch_owner_self_worker_execution`
- Owner self execution: `_register_owner_self_execution_session` / `_mark_owner_self_execution_terminal`
- Helper：`_first_non_empty` / `_metadata_flag` 等小工具（按使用频率分配）

### 9.3 测试设计

- AC-D1：行数验证（orchestrator.py ≤ 2000；dispatch_service.py ≈ 1500）
- AC-D2：import 链路兼容（外部 import orchestrator 模块的功能保持）—— grep 验证
- AC-D3：行为零变更（全量回归 ≥ 3355 + 0 regression）

### 9.4 Codex review 节点

- **Phase D pre-review**（强制）：检查拆分边界设计
- **Phase D post-review**（强制）：检查 import 链路兼容 + 行为零变更

---

## 10. Verify — Final Cross-Phase Codex + completion-report

### 10.1 改动文件清单

| 文件 | 改动 | 估计行数 |
|------|------|---------|
| `.specify/features/098-a2a-mode-worker-to-worker/codex-review-final.md` | Final review 闭环表 | +200 |
| `.specify/features/098-a2a-mode-worker-to-worker/completion-report.md` | 完成报告 | +400 |
| `.specify/features/098-a2a-mode-worker-to-worker/handoff.md` | 给 F099 handoff | +250 |
| `.specify/features/098-a2a-mode-worker-to-worker/trace.md` | 编排时间线 | +150 |

### 10.2 验收（spec.md §5 AC-GLOBAL）

- 全量回归 ≥ 3355 + 累计新增（估计 +300+ 单测）
- e2e_smoke 5x 循环 PASS（8/8 × 5 = 40/40）
- 每 Phase Codex review 闭环（0 high 残留）
- Final cross-Phase Codex review 通过
- completion-report.md + handoff.md 已产出

---

## 11. 风险评估（plan 阶段细化）

| 风险 | 严重 | Phase | 缓解 |
|------|------|-------|------|
| **R1** Phase H task state machine 改造 | 中 | H | Pre + post Codex review；callback 异常隔离 |
| **R2** Phase D orchestrator.py 拆分 import | 中 | D | re-export 兼容性；外部 import 链路保持 |
| **R3** Phase E CONTROL_METADATA_UPDATED 向后兼容 | 中 | E | merge_control_metadata 合并两类 events；migration 测试 |
| **R4** Phase G EventStore API 演化 | 低 | G | append_event_pending 是新 API；逐步迁移 |
| **R5** Phase F subagent runtime 数量增长 | 低 | F | runtime 与 task 同生命周期 |
| **R6** worker→worker 死循环 | 低 | C | DelegationManager max_depth=2（F084）|
| **R7** Phase B fallback 路径误用 | 低 | B | warning log + 测试覆盖 |
| **R8** Phase J BaseDelegation 序列化兼容 | 低 | J | round-trip test |

---

## 12. 数据指标（plan 阶段估计）

| 指标 | 估计 |
|------|------|
| 总 commits | 9-11（每 Phase 1-2）|
| 总单测新增 | 300-450（含集成测）|
| 总代码净增减 | +3500 / -250 = +3250 行（含 spec-driver 制品）|
| 实施代码净增减 | +800 / -200 = +600 行 |
| 测试代码净增减 | +2000 |
| 文档制品净增减 | +700 |
| Codex review 次数 | 11+（per-Phase 9 + Final 1 + ad-hoc 1）|
| 估计实施时间 | 65-85h（Codex P1 闭环：Phase B 由 5h → 8h，整体 +3h）|

---

## 13. 实施顺序总结

```
Phase 0 实测 ✓ (已完成)
Phase E (~5h)  → CONTROL_METADATA_UPDATED + 向后兼容 test
Phase F (~3h)  → ephemeral runtime 独立
Phase B (~8h)  → A2A source + target 双向独立加载（Codex P1 闭环：source 端改造扩大）
Phase C (~3h)  → enforce 删除 + 正面 test
Phase I (~5h)  → worker audit chain 集成测
Phase H (~10h) → task state machine 改造（Codex 强制）
Phase G (~8h)  → atomic 事务（EventStore API 演化）
Phase J (~3h)  → BaseDelegation 抽象
Phase D (~10h) → orchestrator.py 拆分（Codex 强制）
Verify (~8h)   → spec-review + quality-review + Final Codex + report
```

---

**Plan v0.1 完成。下一步：tasks.md（细化任务清单）。**
