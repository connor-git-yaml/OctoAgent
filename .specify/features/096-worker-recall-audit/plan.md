# F096 Worker Recall Audit & Provenance — Plan / Refactor Plan（v0.1）

> 上游：[spec.md](spec.md) v0.1 / [research/codebase-scan.md](research/codebase-scan.md)
>
> 本 plan 在 spec 实测基础上 + 5 项深度 trace 完成后撰写，已 verify trace 报告中
> 与 baseline 实际不一致的 2 处假设（详见 §0.5）。

## 0. baseline & 关键路径锁定（plan 阶段实测后）

### 0.1 baseline
- commit：dd70854（origin/master，含 F095 Final review）
- 测试 baseline：3191 passed
- 分支：feature/096-worker-recall-audit

### 0.2 LLM 决策环唯一入口

`AgentContextService.build_task_context` （`apps/gateway/src/octoagent/gateway/services/agent_context.py:591`）：
- async 入口
- 每次 LLM dispatch 调用一次
- 调用方唯一：`task_service.py:1250` `_build_task_context`（其上层为 `task_service.py:552`）
- 内部链路：`_resolve_context_bundle` → `_fit_prompt_budget`（含 behavior pack 装载）→ RecallFrame 创建（line 848）→ `save_recall_frame`（line 914）→ `save_context_frame`（line 915）

**结论**：`build_task_context` 既是 BEHAVIOR_PACK_LOADED 接入点，也是 BEHAVIOR_PACK_USED 接入点（一次 dispatch 一次 emit）。

**review #1 关键判断 #2 verify 闭环**：所有 dispatch 路径（message / chat route / worker_runtime InlineRuntimeBackend / GraphRuntimeBackend / orchestrator agent direct execution / clarification reply / spawn）都收敛到 `task_service._build_task_context:1122` → `agent_context.build_task_context:591`。**唯一例外**：`_InlineReplyLLMService`（orchestrator.py:1112）路径仍走 build_task_context；F096 范围内 USED 频次定义为"build_task_context 调用一次 emit 一次"，inline reply 是合法的 dispatch（虽非用户驱动）。如实施阶段发现频次污染，再调整。

### 0.3 RecallFrame 持久化路径

| 路径 | 文件 | 创建 RecallFrame | save_recall_frame | emit MEMORY_RECALL_COMPLETED |
|------|------|------------------|-------------------|------------------------------|
| **A 同步**（dispatch 主路径）| `apps/gateway/src/octoagent/gateway/services/agent_context.py:848-914` | ✅ | ✅ line 914 | ❌ **F096 Phase C 补** |
| **B 延迟 recall 物化**（async 后台）| `apps/gateway/src/octoagent/gateway/services/task_service.py:1550-1725` | ❌ **F096 Phase C 补** | - | ✅ line 1688 |

### 0.4 Behavior pack 装载链路

```
build_task_context (async, line 591)
  └─ _fit_prompt_budget (sync, line 3649) — 内部多次调用 _build_system_blocks
       └─ _build_system_blocks (sync, line 3296) — 真正构建 system_blocks
            └─ render_behavior_system_block (sync, agent_decision.py:647-700)
                 └─ resolve_behavior_pack (sync, agent_decision.py:179)
                      └─ pack.metadata["cache_state"] = "miss" or absent
```

**结论**：emit 位置 = `build_task_context` 内，`_fit_prompt_budget` 返回**之后**（line 656+ 之前 RecallFrame 创建之前的窗口）。原因：
- `_fit_prompt_budget` 是 sync 函数，内部不能 await
- pack 在 `_fit_prompt_budget` 内部装载并返回（通过 system_blocks）
- emit 必须在 async caller 中做，避免 sync→async 全链路 refactor

### 0.5 Trace 报告纠正（重要）

| trace 假设 | baseline 实测 | 修正 |
|------------|--------------|------|
| 路径 A "未 persist to store" | ❌ 错——`save_recall_frame` 在 line 914 真实持久化 | F096 Phase A 不需要补 persist；路径 A 已完整。Phase A 改为"补 emit MEMORY_RECALL_COMPLETED"|
| LLM 决策环入口分散在 task_service / task_runner | ❌ 错——`build_task_context` 是统一入口 | BEHAVIOR_PACK_LOADED + USED 都在 `build_task_context` emit |
| Worker dispatch 路径"未复用 agent_context.py:848" | ⚠️ 部分错——Worker 也通过 `build_task_context` 路径，复用 RecallFrame 持久化 | Worker dispatch 已有 RecallFrame 持久化（通过 build_task_context）|

### 0.6 list_recall_frames Store API 已 ready

`packages/core/src/octoagent/core/store/agent_context_store.py:1152` 完整支持 7 维过滤。F096 Phase B 仅需暴露 control_plane endpoint + frontend 调用。

### 0.7 控制台 endpoint 注册模板

`apps/gateway/src/octoagent/gateway/routes/control_plane.py:130-161` 现有 `get_control_memory` endpoint 模板：
- URL 前缀：`/api/control/resources/`
- Query params 模式：`Query(default=None, ge=1, le=200)`
- Auth：`Depends(get_control_plane_service)`
- Response：`.model_dump(mode="json", by_alias=True)`

F096 新增 `/api/control/resources/recall-frames` 沿用此模板。

### 0.8 Frontend 文件清单

`frontend/src/domains/memory/`：
- `MemoryPage.tsx` + `MemoryPage.test.tsx`
- `MemoryFiltersSection.tsx`
- `MemoryResultsSection.tsx`
- `MemoryRetrievalLifecycleSection.tsx`
- `MemoryDetailModal.tsx` / `MemoryEditDialog.tsx` / `MemoryHeroSection.tsx` / `MemoryActionsSection.tsx`
- `shared.tsx` / `index.ts`

`frontend/src/api/client.ts`、`frontend/src/platform/contracts/controlPlane.ts`（contract types）。

## 1. Phase 序列（先简后难，**v0.2 review #1 M11 闭环：B 提前到 D 之前**）

| Phase | 主题 | 改动域 | 测试 | per-Phase Codex review |
|-------|------|-------|------|------------------------|
| **A** | 路径 B 延迟 recall 补 RecallFrame（用 `frame.agent_session_id` 强一致派生）| backend memory | 单测 + 集成 | ✅ |
| **C** | 同步 recall 路径补 emit MEMORY_RECALL_COMPLETED（含 idempotency_key）+ Worker dispatch 路径覆盖 | backend memory | 单测 + 集成 | ✅ |
| **B** | list_recall_frames endpoint 完整暴露（store 层补 offset / 时间窗 / count）| backend control_plane | 单测 + endpoint 集成 | ✅ |
| **D** | BEHAVIOR_PACK_LOADED EventStore 接入（**方案 B**：_fit_prompt_budget 返回 loaded_pack 引用）+ BEHAVIOR_PACK_USED 新增 | backend behavior | 单测 + 集成 | ✅ |
| **E** | Web Memory Console agent 视角 UI（含 useSearchParams race 用例）| frontend | vitest + 组件 | ✅ |
| **F** | F095 推迟集成测补全（AC-4 / AC-7b）| 跨域集成 | e2e | ✅ |

**Phase 顺序变化（v0.1 → v0.2）**：A → C → **B**（提前）→ D → E → F。理由：
- B 提前后 E 可直接接入 B 暴露的 endpoint
- D 不依赖 E（独立 backend behavior 改动）
- review M11 finding 闭环（不拆 F096a/F096b——F096 是阶段 1 收尾整合点）

## 2. Phase A：路径 B 延迟 recall 补 RecallFrame

### 2.1 改动文件
- `apps/gateway/src/octoagent/gateway/services/task_service.py`（line 1550-1725 区间，延迟 recall 物化）

### 2.2 实施细节
1. 在 `task_service.py:1688-1725` MEMORY_RECALL_COMPLETED emit **之前**或**之后**，新增 RecallFrame 创建 + save_recall_frame 调用
2. RecallFrame 字段填充策略（**review #1 H2 闭环**：用 `frame.agent_session_id` 强一致来源）：
   - `recall_frame_id`: 新生成 ULID
   - `agent_runtime_id`: 从 `audit_agent_runtime_id`（已有）复用
   - `agent_session_id`: **直接用 `frame.agent_session_id`**——`_materialize_delayed_recall_once:1577` 已 fetch ContextFrame，`ContextFrame.agent_session_id`（packages/core/.../agent_context.py:441）是直接可读的强一致来源；删除 task_metadata 派生 + session_state 反查 + fallback 空字符串链路
   - `context_frame_id`: 直接用 `frame.context_frame_id`（同 frame 来源）
   - `task_id`: 已有
   - `project_id`: 从 task.project_id
   - `query`: recall.query
   - `recent_summary`: ""（延迟 recall 不需要）
   - `memory_namespace_ids`: recall.scope_ids
   - `memory_hits`: 转换 recall.hits 为 payload list
   - `source_refs`: artifact ref 列表
   - `budget`: {memory_recall: ..., max_prompt_tokens: 0}（延迟 recall 不参与 budget）
   - `degraded_reason`: 复用 recall.degraded_reasons 拼接 + "F096_delayed_path"
   - `metadata`: {request_kind: "delayed", surface: "...", source: "delayed_recall"}
   - `queried_namespace_kinds`: audit_queried_kinds（已计算）
   - `hit_namespace_kinds`: audit_hit_kinds（已计算）
   - `created_at`: now()
3. 调用 `await self._stores.agent_context_store.save_recall_frame(recall_frame)`
4. 失败 try-except 隔离：错误 log warn，不阻塞 emit MEMORY_RECALL_COMPLETED 路径

### 2.3 单测
- 新建 `apps/gateway/tests/services/test_task_service_delayed_recall.py`（如已有则扩展）：
  - test_delayed_recall_persists_recall_frame：物化后 store 内 RecallFrame 可查询
  - test_delayed_recall_recall_frame_field_completion：所有字段 audit 派生正确（含 agent_session_id 空值降级）
  - test_delayed_recall_save_recall_frame_failure_does_not_block_emit：persist 失败时 MEMORY_RECALL_COMPLETED 仍 emit

### 2.4 集成测
- 端到端：trigger 一次 delayed recall → 验证 `list_recall_frames(task_id=...)` 返回该 frame + `MEMORY_RECALL_COMPLETED` 事件可查（同一 task_id）

## 3. Phase C：同步 recall + Worker dispatch 路径补 emit

### 3.1 改动文件
- `apps/gateway/src/octoagent/gateway/services/agent_context.py`（line 848-936 区间）

### 3.2 实施细节
1. 在 `agent_context.py` line 936 `await self._stores.conn.commit()` **之后**（事务边界已 commit，event 写入更稳；review #1 L12 闭环），新增 emit MEMORY_RECALL_COMPLETED：
   ```python
   await self._stores.conn.commit()
   # F096 Phase C: emit MEMORY_RECALL_COMPLETED for sync path（idempotency_key 防 retry/resume 重复 emit）
   try:
       event = build_event(
           EventType.MEMORY_RECALL_COMPLETED,
           MemoryRecallCompletedPayload(
               context_frame_id=context_frame_id,
               query=resolve_request.query or task.title,
               scope_ids=memory_scope_ids,
               request_artifact_ref="",
               result_artifact_ref="",
               hit_count=len(memory_hits),
               backend="",  # 同步路径不展开 backend_status
               backend_state="",
               degraded_reasons=degraded_reasons,
               agent_runtime_id=agent_runtime.agent_runtime_id,
               queried_namespace_kinds=[k.value for k in queried_namespace_kinds],
               hit_namespace_kinds=[k.value for k in hit_namespace_kinds],
           ).model_dump(),
           task_id=task.task_id,
           trace_id=f"trace-{task.task_id}",
           causality=EventCausality(
               # review #1 M8 闭环：sync 路径用 recall_frame_id 派生 idempotency_key
               # recall_frame_id 唯一对应一次 build_task_context dispatch；retry/resume 重新 build_task_context 会有新 recall_frame_id
               # 但同一 recall_frame_id 不会被多次 emit
               idempotency_key=f"{recall_frame_id}:event"
           ),
       )
       await self._stores.event_store.append_event_committed(event, update_task_pointer=False)
   except Exception as exc:
       log.warning("memory_recall_completed_emit_failed_sync_path", error=str(exc))
   ```
2. **Worker dispatch 路径覆盖确认**：Worker dispatch 也通过 `build_task_context` → 同 emit 路径生效，无需额外改动（验证由 Phase F AC-F2 覆盖）

### 3.3 单测
- 新建 `apps/gateway/tests/services/test_agent_context_recall_emit.py`：
  - test_sync_path_emits_memory_recall_completed：build_task_context 后 EventStore 内可查 MEMORY_RECALL_COMPLETED
  - test_sync_emit_payload_field_completion：agent_runtime_id / queried / hit 字段 100% 填充（非审计派生）
  - test_sync_emit_failure_does_not_block_dispatch：emit 失败时 build_task_context 仍返回 CompiledTaskContext

### 3.4 集成测
- 复用 `apps/gateway/tests/test_agent_context_*.py` 现有 fixture，新增 1 个 e2e：sync recall → list events filtered by type=MEMORY_RECALL_COMPLETED 返回非空

## 4. Phase D：BEHAVIOR_PACK_LOADED + BEHAVIOR_PACK_USED

### 4.1 改动文件
- `packages/core/src/octoagent/core/models/enums.py`（新增 EventType.BEHAVIOR_PACK_USED）
- `packages/core/src/octoagent/core/models/behavior.py`（新增 BehaviorPackUsedPayload）
- `apps/gateway/src/octoagent/gateway/services/agent_decision.py`（新增 `make_behavior_pack_used_payload` helper）
- `apps/gateway/src/octoagent/gateway/services/agent_context.py`（line 591-936 区间，build_task_context 内 emit）

### 4.2 EventType 新增
```python
# packages/core/src/octoagent/core/models/enums.py
BEHAVIOR_PACK_USED = "BEHAVIOR_PACK_USED"
```

### 4.3 BehaviorPackUsedPayload schema
```python
# packages/core/src/octoagent/core/models/behavior.py
class BehaviorPackUsedPayload(BaseModel):
    """F096: LLM 决策环 emit，每次 dispatch 一次。

    与 BehaviorPackLoadedPayload 通过 pack_id 关联：
    - LOADED：cache miss 时 emit（一次 cache miss 一次）
    - USED：每次 LLM 决策环 emit（一次 dispatch 一次）

    频次约束：USED 频次 ≥ LOADED 频次（cache hit 时仅 emit USED）
    """
    pack_id: str           # = BehaviorPackLoadedPayload.pack_id
    agent_id: str          # = AgentProfile.profile_id
    agent_kind: str        # main / worker / subagent
    agent_runtime_id: str  # 与 F094 RecallFrame 同维度
    task_id: str
    session_id: str | None = None       # A2A / Worker 对话时
    use_phase: str = "context_preparation"
    cache_state: str = "hit"            # hit / miss（与 LOADED 一致时为 miss）
    file_count: int                     # 与 LOADED 同步，便于无 LOADED 历史时仍可推断
    is_advanced_included: bool
    created_at: datetime
```

### 4.4 helper 新增（**review #1 M4 闭环**：删 hasattr fallback；不预占 subagent）
```python
# apps/gateway/src/octoagent/gateway/services/agent_decision.py
def make_behavior_pack_used_payload(
    pack: BehaviorPack,
    *,
    agent_profile: AgentProfile,
    load_profile: BehaviorLoadProfile,
    agent_runtime_id: str,
    task_id: str,
    session_id: str | None = None,
    use_phase: str = "context_preparation",
) -> BehaviorPackUsedPayload:
    # M4 闭环：cache_state 从 pack metadata 取（cache hit strip 后取不到 → "hit"；cache miss 保留 "miss"）
    cache_state = pack.metadata.get("cache_state", "hit")
    return BehaviorPackUsedPayload(
        pack_id=pack.pack_id,
        agent_id=agent_profile.profile_id,
        # M4 闭环：AgentProfile.kind 是 StrEnum 默认 "main"，不需要 hasattr fallback
        # F096 仅 emit "main" / "worker"，不预占 "subagent"（F097 引入）
        agent_kind=str(agent_profile.kind),
        agent_runtime_id=agent_runtime_id,
        task_id=task_id,
        session_id=session_id,
        use_phase=use_phase,
        cache_state=cache_state,
        file_count=len(pack.files),
        is_advanced_included=pack.metadata.get("is_advanced_included", False),
        created_at=datetime.now(tz=UTC),
    )
```

### 4.5 emit 接入（**review #1 H1 + L12 闭环——选方案 B + emit-after-commit**）

**review #1 HIGH 1 verify 实测确认**（agent_decision.py:199-208）：cache hit 路径显式 strip `cache_state` / `pack_source` 标记。`_fit_prompt_budget` 内部第一次 resolve_behavior_pack 装满 cache（agent_decision.py:243），后续在 build_task_context 内重复 resolve 必命中 cache hit 路径，metadata.get("cache_state") == "miss" **永远 False** → LOADED **永远不 emit** → AC-D1/AC-D5 永远不通过。

**方案 A 不可行，必须改方案 B**：让 `_fit_prompt_budget` 返回值附加 `loaded_pack: BehaviorPack | None`（首次 resolve 即 cache miss 的 pack 引用），向上 propagate 到 build_task_context async 层后 emit。

#### 方案 B 实现

##### Step 1：`_fit_prompt_budget` 签名扩展（agent_context.py:3649）

返回 tuple 增 1 元素：
```python
def _fit_prompt_budget(
    self, ...
) -> tuple[
    list[Message],          # system_blocks
    str,                    # recent_summary
    list[MemoryHit],        # memory_hits
    list[str],              # prompt_budget_reasons
    int,                    # system_tokens
    int,                    # delivery_tokens
    BehaviorPack | None,    # F096 Phase D: 首次 resolve 即 cache miss 的 pack（None 表示 cache hit 全程）
]:
    ...
    loaded_pack: BehaviorPack | None = None
    # _build_system_blocks 内部 render_behavior_system_block → resolve_behavior_pack 调用
    # 第一次进入时如 cache miss，agent_decision.py:243 把 pack 装入 cache 但返回的 pack metadata 仍含 cache_state="miss"
    # 后续调用 cache hit 但 strip 标记
    # 我们在 _build_system_blocks 内部 hook 一次："如果当前 resolve 返回 pack.metadata.cache_state == 'miss'，记录"
    # ...
    return (system_blocks, recent_summary, memory_hits, reasons, sys_tokens, delivery_tokens, loaded_pack)
```

##### Step 2：`_build_system_blocks`（line 3296）+ `render_behavior_system_block` 协作

策略：在 `_build_system_blocks` 内部调用 `render_behavior_system_block` 之前先 resolve_behavior_pack（取得带 cache_state metadata 的 pack），再传给 render；同时把这次 resolve 结果上报给 _fit_prompt_budget。

简化方案：增一个 `_resolve_and_track_pack(self, ctx, agent_profile, ...)` helper（在 agent_context 内部）记录到 self._pack_track（本次 build_task_context 调用范围内的 thread-local-ish 状态——但 self 是 service singleton，不能用），改为通过 `ctx` 对象（已经在 _build_system_blocks 输入）传递。

##### Step 3：build_task_context async emit（line 936 `await self._stores.conn.commit()` 之后，**review #1 L12 闭环**）

```python
await self._stores.conn.commit()
# F096 Phase D: emit BEHAVIOR_PACK_LOADED + USED
# loaded_pack 来自 _fit_prompt_budget 返回值（仅 cache miss 时非 None）
try:
    if loaded_pack is not None:  # cache miss 才 emit LOADED
        loaded_payload = make_behavior_pack_loaded_payload(
            loaded_pack, agent_profile=agent_profile,
            load_profile=load_profile_used,
        )
        loaded_event = build_event(
            EventType.BEHAVIOR_PACK_LOADED,
            loaded_payload.model_dump(),
            task_id=task.task_id,
            trace_id=f"trace-{task.task_id}",
        )
        await self._stores.event_store.append_event_committed(loaded_event, update_task_pointer=False)
    # USED 总 emit（pack 拿不到 cache miss 引用时，从 cache 再 lookup 一次仅取 pack_id 等稳定字段）
    pack_for_used = loaded_pack if loaded_pack is not None else _get_cached_pack_for_used(agent_profile, load_profile_used)
    if pack_for_used is not None:
        used_payload = make_behavior_pack_used_payload(
            pack_for_used, agent_profile=agent_profile, load_profile=load_profile_used,
            agent_runtime_id=agent_runtime.agent_runtime_id,
            task_id=task.task_id,
            session_id=agent_session.agent_session_id,
        )
        used_event = build_event(EventType.BEHAVIOR_PACK_USED, used_payload.model_dump(), task_id=task.task_id, trace_id=f"trace-{task.task_id}")
        await self._stores.event_store.append_event_committed(used_event, update_task_pointer=False)
except Exception as exc:
    log.warning("behavior_pack_event_emit_failed", error=str(exc))
```

##### Step 4：`_get_cached_pack_for_used` helper

cache hit 场景下从 `_behavior_pack_cache` 直接取 cache entry（pack_id 等稳定字段不依赖 cache_state metadata）。

#### 性能约束

- 方案 B 不增加 build_task_context 路径上的 resolve_behavior_pack 调用次数（_fit_prompt_budget 内部已 resolve）
- USED emit 时如需取 pack，from cache 直接 lookup（dict O(1)），无文件系统访问
- 整体增 1 次 BehaviorPack model_copy（_fit_prompt_budget 返回时）+ 2 次 EventStore.append_event_committed（commit 后）；忽略级

### 4.6 单测
- 新建 `apps/gateway/tests/services/test_behavior_pack_events.py`：
  - test_loaded_emits_on_cache_miss
  - test_loaded_does_not_emit_on_cache_hit（仅 USED emit）
  - test_used_emits_every_dispatch（连续 2 次 dispatch 同 pack 应 emit 2 次 USED）
  - test_loaded_used_pack_id_matches（同一 pack 的 LOADED 与 USED pack_id 严格相等）
  - test_emit_failure_does_not_block_dispatch

### 4.7 集成测
- 复用 F095 fixture `test_end_to_end_worker_pack_to_envelope_with_worker_variants`：
  - 新增 assertion：dispatch 后 EventStore 内可查 BEHAVIOR_PACK_LOADED + BEHAVIOR_PACK_USED；pack_id 一致

## 5. Phase B：list_recall_frames endpoint

### 5.1 改动文件
- `apps/gateway/src/octoagent/gateway/services/control_plane/memory_service.py`（新增 list_recall_frames domain service 方法）
- `apps/gateway/src/octoagent/gateway/routes/control_plane.py`（新增 endpoint）
- `apps/gateway/src/octoagent/gateway/services/control_plane/_coordinator.py`（DI 注入）
- **review #1 H3 闭环**：`packages/core/src/octoagent/core/store/agent_context_store.py`（store 层扩展 list_recall_frames + count_recall_frames）
- **review #1 M7 闭环**：`packages/core/src/octoagent/core/models/control_plane/session.py:135`（RecallFrameItem 字段补全）

### 5.2 endpoint 实现

```python
# apps/gateway/src/octoagent/gateway/routes/control_plane.py 新增
@router.get("/api/control/resources/recall-frames")
async def get_control_recall_frames(
    agent_runtime_id: str | None = Query(default=None),
    agent_session_id: str | None = Query(default=None),
    context_frame_id: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    queried_namespace_kind: str | None = Query(default=None),
    hit_namespace_kind: str | None = Query(default=None),
    created_after: str | None = Query(default=None),
    created_before: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    group_by: str | None = Query(default=None),
    control_plane=Depends(get_control_plane_service),
):
    return (
        await control_plane.list_recall_frames(
            agent_runtime_id=agent_runtime_id,
            agent_session_id=agent_session_id,
            context_frame_id=context_frame_id,
            task_id=task_id,
            project_id=project_id,
            queried_namespace_kind=queried_namespace_kind,
            hit_namespace_kind=hit_namespace_kind,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            offset=offset,
            group_by=group_by,
        )
    ).model_dump(mode="json", by_alias=True)
```

### 5.3 domain service

```python
# memory_service.py 新增方法
async def list_recall_frames(
    self,
    *,
    agent_runtime_id: str | None = None,
    ...all 7 dims + time window + pagination + group_by,
) -> RecallFrameListDocument:
    rows = await self._ctx._stores.agent_context_store.list_recall_frames(
        # 7 dims to store layer
    )
    # 计算 scope_hit_distribution
    distribution: dict[str, int] = {}
    for row in rows:
        for kind in row.hit_namespace_kinds:
            distribution[kind.value] = distribution.get(kind.value, 0) + 1
    # 分组（如 group_by 指定）
    timelines = (
        self._build_agent_recall_timelines(rows)
        if group_by == "agent_runtime_id"
        else None
    )
    return RecallFrameListDocument(
        frames=[RecallFrameItem.from_recall_frame(r) for r in rows],
        total=len(rows),  # 注意：分页后非全表 total，store 层需补全 count（Phase B 实施时决议）
        scope_hit_distribution=distribution,
        agent_recall_timelines=timelines,
    )
```

### 5.4 response models（contracts）
- `RecallFrameListDocument`（packages/core 或 apps/gateway 的 control_plane 类型层）
- 含 frames / total / scope_hit_distribution / agent_recall_timelines

### 5.4.1 Store 层扩展（**review #1 H3 闭环**——必做）

实测 baseline `agent_context_store.list_recall_frames`（line 1152）仅支持 7 维等值过滤 + ORDER BY created_at + LIMIT。F096 endpoint signature 含 offset / created_after / created_before / total——硬契约失配。

F096 store 层扩展：
- `list_recall_frames` 签名补 `offset: int = 0` 参数 + SQL `LIMIT N OFFSET M`
- 补 `created_after: str | None / created_before: str | None` 字段过滤（SQL `created_at >= ? AND created_at <= ?`）
- 新增 `async def count_recall_frames(self, *, <7 维 filters>, created_after, created_before) -> int`（用于 endpoint total）
- N+1 风险评估：count + list 两次查询；SQLite 性能可接受（recall_frames 单 user 量级 < 100k 行 + indexed query < 10ms）；不引入 N+1（aggregate query 而非 per-row）

`RecallFrameItem` 字段扩展（**review #1 M7 闭环**）：
- 当前 13 字段（缺 metadata / source_refs / budget）；F096 补到 16 字段对齐 RecallFrame model
- frontend `types/index.ts:843` 同步扩展

### 5.5 单测
- 新建 `apps/gateway/tests/control_plane/test_memory_service_recall_frames.py`：
  - 7 个过滤维度各覆盖 1 个测试 = 7 测试
  - test_pagination_limit_offset
  - test_scope_hit_distribution_aggregation
  - test_group_by_agent_runtime_id_returns_timelines
  - test_invalid_namespace_kind_returns_400

### 5.6 endpoint 集成测
- 新建 `apps/gateway/tests/integration/test_recall_frames_endpoint.py`：
  - test_endpoint_returns_200_with_filters
  - test_endpoint_pagination_boundary（offset 超限返回空）
  - test_endpoint_auth_required

## 6. Phase E：Web Memory Console agent 视角 UI

### 6.1 改动文件
- `frontend/src/platform/contracts/controlPlane.ts`（types 扩展）
- `frontend/src/api/client.ts`（新增 fetchRecallFrames）
- `frontend/src/types/index.ts`（RecallFrameQuery / AgentRecallTimeline 类型）
- `frontend/src/domains/memory/MemoryFiltersSection.tsx`（agent dropdown）
- `frontend/src/domains/memory/MemoryResultsSection.tsx`（条件渲染）
- 新建 `frontend/src/domains/memory/RecallFrameTimeline.tsx`
- `frontend/src/domains/memory/MemoryPage.tsx`（新增 "Recall Audit" tab / view 切换）
- `frontend/src/domains/memory/shared.tsx`（buildAgentRecallTimelines helper）

### 6.2 类型扩展（controlPlane.ts）
```typescript
export interface RecallFrameQuery {
  agentRuntimeId?: string;
  agentSessionId?: string;
  contextFrameId?: string;
  taskId?: string;
  projectId?: string;
  queriedNamespaceKind?: string;
  hitNamespaceKind?: string;
  createdAfter?: string;
  createdBefore?: string;
  limit?: number;
  offset?: number;
  groupBy?: "agent_runtime_id" | "agent_session_id";
}

export interface RecallFrameListDocument {
  frames: RecallFrameItem[];
  total: number;
  scopeHitDistribution?: Record<string, number>;
  agentRecallTimelines?: AgentRecallTimeline[];
}

export interface AgentRecallTimeline {
  agentRuntimeId: string;
  agentProfileId: string;
  agentName: string;
  recallFrames: RecallFrameItem[];
  totalHitCount: number;
}
```

### 6.3 client.ts 新增
```typescript
export async function fetchRecallFrames(
  params: RecallFrameQuery = {}
): Promise<RecallFrameListDocument> {
  return apiFetch<RecallFrameListDocument>(
    `/api/control/resources/recall-frames${buildQueryString({...params})}`
  );
}
```

### 6.4 UI 设计

`MemoryPage.tsx`：
- 新增 view 切换（query param `view=recall-audit`）
- 默认 view = "memory"（保持向后兼容）

`MemoryFiltersSection.tsx`：
- view = "recall-audit" 时启用 agent_runtime_id dropdown + namespace_kind dropdown
- view = "memory" 时保留现有过滤维度

**review #1 M6 闭环 useSearchParams race**：
- baseline MemoryPage 当前用 `useState` + workbench snapshot 全局态，**未使用 useSearchParams**
- F096 引入 useSearchParams 必须含 race 用例：params 变化未触发 effect / params 与 useState 双向同步 race
- 评估抽离 `useViewQueryState` hook helper 到 `frontend/src/lib/`（避免后续 page 重复 pattern）；本次评估写到 §6.5 测试，是否真抽离 plan 阶段决议优先 inline；如 Phase E 实施时发现 race 难治再抽离

`RecallFrameTimeline.tsx`（新建）：
- 接 `agentRecallTimelines: AgentRecallTimeline[]` props
- 渲染：每个 agent 一个折叠卡片，内部 frame list 按 created_at 倒序

`MemoryResultsSection.tsx`：
- view = "recall-audit" 时渲染 RecallFrameTimeline（如 group_by 启用）或扁平 frames table

### 6.5 测试
- 新建 `frontend/src/domains/memory/RecallFrameTimeline.test.tsx`（vitest）
  - test_renders_agent_groups
  - test_empty_state
  - test_filters_propagate_to_query
- `frontend/src/api/client.test.ts` 新增 test_fetchRecallFrames_query_string_mapping
- `frontend/src/domains/memory/MemoryPage.test.tsx` 扩展 view 切换 test

## 7. Phase F：F095 推迟集成测补全

### 7.1 改动文件
- 新建 `apps/gateway/tests/integration/test_f096_audit_chain.py`

### 7.2 AC-F1（= F095 AC-4）delegate_task tool 集成测

```python
async def test_delegate_task_emits_behavior_pack_loaded():
    """端到端验证 delegate_task tool 触发 BEHAVIOR_PACK_LOADED emit。"""
    # arrange: worker profile + agent runtime
    # act: 调用 delegate_task tool
    # assert:
    #   - BEHAVIOR_PACK_LOADED event 存在（query EventStore）
    #   - payload.agent_id == AgentProfile.profile_id
    #   - payload.agent_kind == "worker"
    #   - AgentRuntime 表含 (profile_id, runtime_id)
```

### 7.3 AC-F2（= F095 AC-7b）完整 audit 链路集成测

```python
async def test_audit_chain_profile_runtime_recallframe():
    """端到端验证 AgentProfile → AgentRuntime → RecallFrame 完整对齐。"""
    # arrange: worker profile + worker dispatch + memory.write + memory.recall
    # act: 触发 worker dispatch（build_task_context）
    # assert:
    #   - BEHAVIOR_PACK_LOADED 含 agent_id = profile_id
    #   - BEHAVIOR_PACK_USED 含 pack_id = LOADED.pack_id
    #   - RecallFrame.agent_runtime_id == AgentRuntime.runtime_id
    #   - AgentRuntime.profile_id == AgentProfile.profile_id == LOADED.agent_id
    #   - list_recall_frames(agent_runtime_id=runtime_id) 返回该 frame
    #   - MEMORY_RECALL_COMPLETED 事件可查（path A 或 B）
```

### 7.4 fixture 复用
- `test_end_to_end_worker_pack_to_envelope_with_worker_variants`（F095，behavior pack 装载验证）
- `test_worker_profile_e2e_filesystem_with_worker_variants`（F095，worker filesystem 验证）

## 8. 测试策略

### 8.1 每 Phase 后回归
- `pytest -q` 全量回归（baseline 3191 passed）
- 不允许 net regression

### 8.2 e2e_smoke
- 每 Phase commit 前 `pytest -m e2e_smoke` 必过
- pre-commit hook 自动跑

### 8.3 集成测
- Phase F 必须 PASS（AC-F1 / AC-F2）
- Phase B 端到端 endpoint 集成测（auth + pagination）

### 8.4 并发 race 防护
- F083 已知工程债：xdist 并发模式下 task_runner 状态机测试 ~20% 失败率，本 Feature 不主动复现，串行模式即可

## 9. Codex review 节点

| 节点 | 时机 | 范围 |
|------|------|------|
| **pre-spec/plan**（critical）| Phase A 实施前 | spec.md + plan.md 整体 |
| per-Phase A | Phase A 实施完，commit 前 | task_service.py 改动 + 测试 |
| per-Phase C | Phase C 实施完 | agent_context.py emit 改动 + 测试 |
| per-Phase D | Phase D 实施完 | enums + behavior payload + helper + emit + 测试 |
| per-Phase B | Phase B 实施完 | endpoint + service + 测试 |
| per-Phase E | Phase E 实施完 | frontend 改动 + 测试 |
| per-Phase F | Phase F 实施完 | 集成测 |
| **Final cross-Phase**（critical）| Phase 7c verify 前 | 全部 6 Phase commit diff |

每次 review finding 处理流程参照 CLAUDE.local.md §"Codex Adversarial Review 强制规则"。

## 10. 风险 / Open 事项

### 10.1 Phase D pack 二次 resolve 性能
**风险**：build_task_context 在 `_fit_prompt_budget` 之外二次调用 resolve_behavior_pack，理论双重渲染。
**缓解**：cache hit 路径开销可忽略（mtime 检查 + dict lookup）；Phase D 单测 perf assertion < 5ms 重复 resolve。
**降级**：如发现热路径影响，Phase D 可改 `_fit_prompt_budget` 返回 pack 引用（方案 B），但需调整 sync 函数签名（影响范围中）。

### 10.2 Phase A agent_session_id 派生失败
**风险**：延迟 recall 在 task_metadata 内可能找不到 agent_session_id。
**缓解**：派生失败时设空字符串，不阻塞 RecallFrame 持久化；Phase A 单测覆盖空值降级。
**长期**：F107 完整对齐时收口。

### 10.3 Phase B `total` 准确性
**风险**：分页前 total 与分页后 frames count 不一致——store 层 list_recall_frames 是否返回全表 count？
**缓解**：Phase B 实施时检查 store 层；如无 count API，新增 `count_recall_frames(filters)` 方法。

### 10.4 Phase E view 切换的 URL 状态保留
**风险**：query params 变化未触发 view re-render。
**缓解**：用 useSearchParams hook 监听变化（React Router pattern）；Phase E 测试覆盖。

### 10.5 Phase D 与 F107 重叠
**风险**：F107 可能整体重构 capability_pack/tooling/harness 三层职责（D9）。
**缓解**：Phase D emit 改动局部（agent_context.py / agent_decision.py），F107 改动时保留 emit 入口语义。

## 11. 完成标准（与 spec §4 AC 对齐）

每个 Phase 完成时回顾 spec 对应 AC：
- Phase A → AC-A1（已完成 spec 阶段）+ Phase A 实施改动 RecallFrame B 路径完成（spec §4 块 C 部分 AC-C2 ✓）
- Phase C → AC-C1 + AC-C3 + AC-C4
- Phase D → AC-D1 ~ AC-D5
- Phase B → AC-B1 ~ AC-B4
- Phase E → AC-E1 ~ AC-E5
- Phase F → AC-F1 + AC-F2
- 全局 → AC-G1 ~ AC-G6

## 12. 修订记录
- v0.1（2026-05-10）：plan 草案，5 项关键路径 trace 已完成 + 2 项 baseline 假设纠正（路径 A 已 persist / build_task_context 是 LLM 决策环唯一入口）。等待 Codex pre-spec/plan adversarial review。
- v0.2（2026-05-10）：review #1 全闭环（3 high + 7 medium + 3 low）：
  - **H1 闭环**：§4.5 改方案 B（`_fit_prompt_budget` 返回 loaded_pack 引用）；方案 A 因 cache hit 路径 strip metadata 永不 emit，invalidate
  - **H2 闭环**：§2.2 改 `frame.agent_session_id` 强一致派生
  - **H3 闭环**：§5.1 + §5.4.1 store 层补 offset / 时间窗 / count_recall_frames + RecallFrameItem 字段补全（M7）
  - **M4 闭环**：§4.4 helper 删 hasattr fallback；不预占 subagent
  - **M6 闭环**：§6.4 useSearchParams race 用例
  - **M8 闭环**：§3.2 sync 路径 idempotency_key = `f"{recall_frame_id}:event"`
  - **L12 闭环**：§3.2 / §4.5 emit 改在 `await self._stores.conn.commit()` 之后（事务边界更稳）
  - **Phase 顺序变化**：A → C → **B**（提前）→ D → E → F（M11 闭环）
  - **关键判断 #2 verify**：§0.2 加入唯一入口 verify 闭环说明
  - 详细处理表参见 [codex-review-spec-plan.md](codex-review-spec-plan.md)
