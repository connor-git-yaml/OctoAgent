# F096 → F097 / F098 / F107 / Phase E Handoff

## 给 F097 (Subagent Mode Cleanup) 的接口点

### 1. BEHAVIOR_PACK_LOADED / USED 事件 schema 扩展

F096 当前 `BehaviorPackLoadedPayload.agent_kind` / `BehaviorPackUsedPayload.agent_kind` 仅 emit `main` / `worker`（不预占 `subagent`）。

F097 引入 Subagent Mode 时：
- `AgentProfile.kind` 新增 `subagent` enum 值
- helper `make_behavior_pack_loaded_payload` / `make_behavior_pack_used_payload` 自动 emit `subagent`（无需改 helper）
- F107 schema 演化时如改 schema_version 字段，参照 F096 spec §4.3 归档约定

### 2. SubagentDelegation 复用 F096 audit chain

F097 SubagentDelegation 共享调用方 RuntimeHintBundle/Project/Memory，但有独立 AgentRuntime 行：
- F096 audit chain: AgentProfile.profile_id → AgentRuntime.profile_id → RecallFrame.agent_runtime_id 路径完整覆盖 Subagent
- F096 list_recall_frames endpoint 可按 agent_runtime_id 过滤 Subagent 召回 frame
- F096 BEHAVIOR_PACK_LOADED.agent_id 与 Subagent AgentRuntime.profile_id 自动对齐

### 3. 阶段 2 启动条件

F096 完成后阶段 2 启动条件（CLAUDE.local.md §M5/M6 战略规划 §依赖波次）：
- ✅ F092 / F093 / F094 / F095 / F096 全部 acceptance gate 主体闭环
- ⏳ F097 启动顺序：阶段 2 第一个 Feature

## 给 F098 (A2A Mode + Worker↔Worker) 的接口点

### 1. AC-F1 worker_capability 路径推迟到 F098

F096 Phase F audit chain test 仅 cover main agent dispatch 路径。F098 实施 delegate_task tool / Worker↔Worker 解禁时一并完成 worker 路径 audit chain test：

**F098 必做**：
```python
async def test_f096_audit_chain_worker_dispatch():
    """F096 Phase F AC-F1 worker_capability 路径补全（F098 范围）。"""
    # arrange: 创建 WorkerProfile + dispatch_metadata.worker_capability
    # act: 调用 delegate_task tool 触发 worker AgentRuntime 创建
    # assert:
    #   - BEHAVIOR_PACK_LOADED.agent_kind == "worker"
    #   - AgentRuntime.kind == WORKER
    #   - audit chain 四层身份对齐（同 F096 audit chain test）
```

复用：F096 audit chain test 的 Layer 1-4 验证结构。

### 2. A2A receiver context audit

F096 audit chain（profile_id ↔ runtime_id ↔ recall_frame.agent_runtime_id）兼容 A2A receiver 在自己 context 工作的语义——A2A receiver 创建独立 AgentRuntime + 自己的 build_task_context 调用自动触发 LOADED + USED + RecallFrame 持久化。

### 3. Worker → Worker 解禁

F092 `_enforce_child_target_kind_policy` 在 F098 删除后，Worker → Worker 路径产生的 RecallFrame 自动包含 child Worker 的 agent_runtime_id；F096 list_recall_frames endpoint 可通过 `agent_runtime_id` 过滤"哪个 Worker 召回了什么"。

## 给 F107 (Capability Layer Refactor) 的接口点

### 1. BehaviorPack.pack_id / cache_state metadata API 保留

F096 实施依赖：
- `BehaviorPack.pack_id`（F095 hash 化）
- `pack.metadata["cache_state"]`（"miss" or absent）
- `pack.metadata["pack_source"]`（filesystem / default / metadata_raw_pack）
- `_behavior_pack_cache` module-level dict（cross-test 污染风险已用 fixture 解决）

F107 重构 capability_pack/tooling/harness 三层时**必须保留**：
- pack_id 字段 + hash 算法（F095/F096 集成测对齐依赖）
- cache_state metadata 标记（LOADED emit 触发条件）
- pack_source metadata 标记（LOADED payload.pack_source 字段）

### 2. agent_decision.py helper 重构兼容

F107 重构 agent_decision.py 时保留 API 兼容：
- `resolve_behavior_pack(...)` 函数签名 + cache 行为
- `make_behavior_pack_loaded_payload(...)` helper API
- `make_behavior_pack_used_payload(...)` helper API（F096 新增）
- `render_behavior_system_block(...)` 渲染入口

### 3. Phase B M1 timelines N+1 性能优化

F096 Phase B `MemoryDomainService.list_recall_frames` group_by 路径对每个 agent_runtime_id 各调一次 `get_agent_runtime` + `get_agent_profile`（M agents groups → 2M store query）。

F107 重构时如发现真实负载性能瓶颈（实测 ~10 agents 量级 ~20ms 可接受），可改批量 fetch（一次 query 多个 runtime_id）。

### 4. share_with_workers 字段彻底删除（F107 完成）

F095 保留 share_with_workers UI 字段；F096 不动；F107 capability layer refactor 时彻底删除（spec §5 已归档）。

## 给 Phase E（推迟到后续独立 session）的接口点

### 1. Backend 契约已稳定

F096 Phase B 已交付的稳定契约：
- **endpoint**：`GET /api/control/resources/recall-frames`
- **query params**：12 个（7 过滤维度 + 时间窗 + 分页 + group_by）
- **response model**：`RecallFrameListDocument`（`packages/core/src/octoagent/core/models/control_plane/session.py`）
  - `frames: list[RecallFrameItem]`（16 字段对齐 RecallFrame model）
  - `total: int`
  - `limit: int / offset: int`
  - `scope_hit_distribution: dict[str, int]`
  - `agent_recall_timelines: list[AgentRecallTimeline] | None`（仅 group_by="agent_runtime_id" 时填）

### 2. Frontend 实施清单（Phase E 完整 5 AC）

按 plan §6 实施，路径修正为 `frontend/src/domains/memory/`：

| AC | 文件 | 改动 |
|----|------|------|
| E-1 | `frontend/src/platform/contracts/controlPlane.ts` | RecallFrameQuery / RecallFrameListDocument / AgentRecallTimeline 接口 |
| E-1 | `frontend/src/types/index.ts` | re-export 上述 types |
| E-1 | `frontend/src/api/client.ts` | `fetchRecallFrames(params: RecallFrameQuery)` |
| E-2/3 | `frontend/src/domains/memory/MemoryFiltersSection.tsx` | agent_filter dropdown |
| E-2/3 | `frontend/src/domains/memory/MemoryResultsSection.tsx` | 条件渲染 RecallFrameTimeline |
| E-3 | `frontend/src/domains/memory/RecallFrameTimeline.tsx` | 新建（Agent 分组渲染）|
| E-3 | `frontend/src/domains/memory/shared.tsx` | `buildAgentRecallTimelines()` helper |
| E-4 | `frontend/src/domains/memory/MemoryPage.tsx` | "Recall Audit" view 切换 |
| E-5 | `RecallFrameTimeline.test.tsx` + `client.test.ts` + `MemoryPage.test.tsx` | vitest 测试（≥ 3）|

### 3. Phase E review #1 推迟项

- **M6 useSearchParams race**：useState + useSearchParams 双向同步 race；评估抽离 `useViewQueryState` hook helper
- **Phase B M1 timelines N+1**：实施时如发现性能瓶颈，改批量 fetch
- **Phase C M1 SSE broadcast 缺失**：sync 路径 emit 不通过 SSE，UI 实时刷新需评估

### 4. Phase E 不阻 F097 启动

F097（Subagent Mode Cleanup）启动条件不依赖 Phase E（frontend）；F097 作为 backend Feature 可独立推进。Phase E 作为 frontend Feature 或 F107 顺手清都可。

## 不在 F096 / F097 / F098 / F107 范围（长期跟踪）

- pack_id 长度（16 hex）跨用户场景如不足可扩 32/64 hex
- BEHAVIOR_PACK_LOADED / USED 事件 retention / cleanup 策略（EventStore 通用）
- conftest.py 全局 `_behavior_pack_cache` 清理 fixture（F096 用单文件 scope fixture 实测足够）
- ISO8601 字符串 validation hardening（frontend 标准化 + endpoint 不强 validate）

## docs/codebase-architecture/* 同步建议

F096 改动涉及：
- `apps/gateway/src/octoagent/gateway/services/agent_context.py:build_task_context` 加 prime resolve + 3 段 emit（LOADED + USED + MEMORY_RECALL_COMPLETED）
- `apps/gateway/src/octoagent/gateway/services/task_service.py:_materialize_delayed_recall_once` 加 RecallFrame 持久化
- `packages/core/.../store/agent_context_store.py` `list_recall_frames` + `count_recall_frames` 扩展
- `apps/gateway/.../control_plane/memory_service.py:list_recall_frames` 新增 audit endpoint domain service
- `RecallFrameItem` 16 字段（M7 闭环）

如 docs 有 "Recall path" / "Behavior pack 加载" / "Audit endpoint" 章节，需更新；如无，本 handoff 已留接口。F107 capability layer refactor 时统一同步更稳。
