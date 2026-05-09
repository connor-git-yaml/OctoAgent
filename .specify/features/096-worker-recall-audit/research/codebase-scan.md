# F096 Spec 阶段块 A 实测侦察报告（codebase-scan）

> **目的**：沿用 F093/F094/F095 三连"baseline 已部分通"pattern，spec 起草前实测当前
> RecallFrame / list_recall_frames endpoint / MEMORY_RECALL_COMPLETED / BEHAVIOR_PACK_LOADED /
> Web Memory Console 现状，避免 spec 假设 baseline 缺失项实际已 ready，或反之。
>
> **路径前缀注记**：F096 worktree 内项目根是 `octoagent/` 子目录。所有相对路径以此为根。

## 1. 后端 Memory 域

### 1.1 RecallFrame 模型（packages/core/src/octoagent/core/models/agent_context.py:460-486）

字段 16 个（含 F094 双字段）：
- 基础 7：recall_frame_id / agent_runtime_id / agent_session_id /
  context_frame_id / task_id / project_id / workspace_id（可选）
- 召回 4：query / recent_summary / memory_namespace_ids / memory_hit_count
- F094 双字段：queried_namespace_kinds / hit_namespace_kinds（list[MemoryNamespaceKind]）
- 异常：degraded_reason
- 时间：created_at

### 1.2 RecallFrame 写入路径

| 路径 | 位置 | agent_runtime_id | agent_session_id | queried_kinds | hit_kinds | 创建 RecallFrame? |
|------|------|------------------|------------------|---------------|-----------|-------------------|
| **A 同步 recall** | `apps/gateway/src/octoagent/gateway/services/agent_context.py:848-875` | ✅ 100% | ✅ 100% | ✅ 完整 | ✅ 完整 | ✅ |
| **B 延迟 recall 物化** | `apps/gateway/src/octoagent/gateway/services/task_service.py:1550-1725` | ⚠️ 仅审计派生 | ❌ 缺失 | ⚠️ 派生可空 | ✅ | ❌ **不创建** |

**F094 设计缺陷**：路径 B 仅生成 artifact + emit 事件，**不持久化 RecallFrame**。
F096 必须在路径 B 末尾补 RecallFrame 持久化（agent_session_id 视情况设空或派生）。

### 1.3 list_recall_frames Store 层 vs Endpoint

**Store 层支持的 7 维过滤**（`packages/core/src/octoagent/core/store/agent_context_store.py:1152-1206`，
F094 已 ready）：
1. agent_session_id
2. agent_runtime_id（F094）
3. context_frame_id
4. task_id
5. project_id
6. queried_namespace_kind（F094，JSON list contains）
7. hit_namespace_kind（F094，JSON list contains）

**Endpoint 现状**：
- ❌ control_plane Memory 服务 **无** `list_recall_frames` 公开 endpoint
- 唯一内部调用：`apps/gateway/src/octoagent/gateway/services/control_plane/session_service.py:225`
  （内部概览查询，封装在 get_context_continuity_document 内）
- 路由层（`apps/gateway/src/octoagent/gateway/routes/control_plane.py`）无对应 REST endpoint

**F094 推迟项确认**：Store 100% ready，**仅缺 control_plane endpoint + frontend 调用**。

### 1.4 MEMORY_RECALL_COMPLETED emit 路径覆盖矩阵

| 路径 | RecallFrame 持久化 | MEMORY_RECALL_COMPLETED emit | F094 状态 |
|------|---------------------|-------------------------------|----------|
| **A 同步 recall**（agent_context.py:848） | ✅ | ❌ **当前未 emit** | F094 仅延迟路径 emit |
| **B 延迟 recall 物化**（task_service.py:1695） | ❌ | ✅ | F094 已加 |
| **C Worker dispatch** | ❓ 待 spec/plan 验证 | ❓ 待 spec/plan 验证 | 未知 |

**事件 payload 字段**（`packages/core/src/octoagent/core/models/payloads.py:133-157`，F094 已加）：
- agent_runtime_id（路径 B 仅审计派生，可为空）
- queried_namespace_kinds[]（路径 B 派生可空）
- hit_namespace_kinds[]（路径 B 完整）

**F094 推迟项确认**：F096 双向补全：
- 路径 A 加 emit MEMORY_RECALL_COMPLETED（agent_runtime_id / queried_namespace_kinds /
  hit_namespace_kinds 100% 可用）
- 路径 B 加 RecallFrame 持久化（agent_session_id 视情况）

## 2. Behavior 域

### 2.1 BEHAVIOR_PACK_LOADED F095 已就位

- Schema：`BehaviorPackLoadedPayload`（`packages/core/src/octoagent/core/models/behavior.py:290-314`）
  字段 10：pack_id / agent_id / agent_kind / load_profile / pack_source / file_count /
  file_ids / source_chain / cache_state / is_advanced_included
- Helper：`make_behavior_pack_loaded_payload`（`apps/gateway/src/octoagent/gateway/services/agent_decision.py:320-357`，sync）
- EventType：`BEHAVIOR_PACK_LOADED`（`packages/core/src/octoagent/core/models/enums.py:220`）
- pack_id：hash 化 `behavior-pack:{profile_id}:{load_profile.value}:{16-char hex}`
- cache miss 标记：pack.metadata["cache_state"] = "miss"

### 2.2 接入点候选 + sync/async 边界结论

**control_plane 路径（display only，不应在此 emit）**：
- `apps/gateway/src/octoagent/gateway/services/control_plane/agent_service.py:111`
  （AgentProfileDomainService.get_agent_profiles_document）
- `apps/gateway/src/octoagent/gateway/services/control_plane/worker_service.py:168 / 255`

⚠️ **拒绝在此接入**：control_plane 是 GET API 的展示层，每次 web admin 打开 agent 列表
都会 emit，与 LLM "实际装载 pack" 无关，会污染审计语义。

**LLM dispatch 真实接入点（待 plan 阶段精确定位）**：
- `apps/gateway/src/octoagent/gateway/services/agent_decision.py:179` `resolve_behavior_pack`
  （sync，被多处调用）
- `apps/gateway/src/octoagent/gateway/services/agent_decision.py:571` `build_behavior_system_summary`
  （sync，包装 pack + envelope）
- `apps/gateway/src/octoagent/gateway/services/agent_decision.py:655` 第三处 resolve（待 plan 阶段确认调用语境）
- `apps/gateway/src/octoagent/gateway/services/agent_context.py:86` import 了
  resolve_behavior_pack 但 grep 仅 import 行；实际通过 `_build_system_blocks`
  （agent_context.py:3296）封装链构建——plan 阶段须深度 trace

**sync/async 边界结论**：
- ✅ **不需要 sync→async 全链路 refactor**：sync 函数可在 async 上下文中直接调用（无阻塞 IO）
- EventStore.append_event_committed 是 async（`packages/core/src/octoagent/core/store/event_store.py`）
- 接入策略：在 LLM dispatch 真实装载 pack 处（async 入口）添加 emit；
  事务边界用 try-except 隔离（一个 profile emit 失败不影响整体 dispatch）

### 2.3 BEHAVIOR_PACK_USED 事件设计参考

LLM 决策环搜索：
- LLMService.call（`apps/gateway/src/octoagent/gateway/services/llm_service.py`）
- AgentLoopPlan（`packages/core/src/octoagent/core/models/behavior.py:264-269`）含
  `decision: AgentDecision` + `recall_plan: RecallPlan`
- MODEL_CALL_STARTED / MODEL_CALL_COMPLETED 事件已存在

**字段建议（F096 自决）**：
```python
class BehaviorPackUsedPayload(BaseModel):
    pack_id: str           # 关联到 LOADED 实例
    agent_id: str          # = AgentProfile.profile_id
    agent_runtime_id: str  # 与 F094 RecallFrame 同维度
    task_id: str
    session_id: str | None  # A2A / Worker 对话时
    use_phase: str          # "context_preparation" / "decision_reasoning" / ...
    llm_model_alias: str
    input_token_count: int
    output_token_count: int
    memory_hit_count: int   # 本轮 recall 数（可与 RecallFrame 对齐）
    created_at: datetime
```

**LOADED vs USED 语义边界**：
- LOADED：pack 装载到 cache（每次 cache miss 一次）—— 频次由 cache TTL + mtime 决定
- USED：每次 LLM 决策环 emit —— 反映真实使用量（不依赖 cache 状态）
- 关联键：pack_id（hash 化已 ready）

## 3. Frontend 域（Web Memory Console）

### 3.1 现状

- 框架：React + TypeScript + Vite，路由 React Router
- 主目录：`frontend/src/`
- RecallFrameItem TypeScript 接口：`frontend/src/types/index.ts:843-857`
  字段已含 agent_runtime_id / agent_session_id（与 F094 后端对齐）
- 当前展示：仅 ContextContinuityDocument 资源条件展示
  （`ContextContinuityDocument.recall_frames?: RecallFrameItem[]`，`types/index.ts:921-934`）
- MemoryPage（`frontend/src/pages/MemoryPage.tsx`）当前过滤维度：scope / layer / partition

### 3.2 缺口

- ❌ 无 dedicated agent 视角过滤入口
- ❌ MemoryResourceQuery 没有 agent_profile_id / agent_runtime_id 字段
- ❌ 无 agent 分组视图（"Worker `research` 在 session X 召回了哪些 fact"）
- ❌ Memory Console "Recall Audit" 标签页不存在

### 3.3 agent 视角 UI 设计切入点

**API 改动**（块 B 配套）：
- 新增 endpoint：`GET /api/control/recall-frames`
  query：agent_runtime_id / agent_session_id / context_frame_id / task_id / project_id /
  queried_namespace_kind / hit_namespace_kind / created_at_window / limit / offset
- 返回：分页 RecallFrameItem 列表 + scope_hit_distribution 聚合

**前端组件改动**：
- `MemoryFiltersSection.tsx`：新增 agent_filter dropdown（选 agent_profile_id 或
  agent_runtime_id）
- `MemoryResultsSection.tsx`：增加 "Recall Audit" 标签页 / RecallFrameTimeline 子组件
- `shared.tsx`：新增 buildAgentRecallTimelines() helper
- `MemoryPage.tsx`：侧栏 "Agent Recall Activity"

**路由策略**：保留 `/memory` 单一路由，通过 query params 控制视图：
- `/memory?view=recall-audit&agent_runtime_id=xxx`
- `/memory?view=recall-audit&group_by=agent`

## 4. F096 Spec 起草要点

### 块 A 实测落地建议
- 路径 B 延迟 recall 物化补 RecallFrame 持久化（agent_session_id 派生策略 plan 阶段决定）
- RecallFrame 字段 16 个 F094 已完整，**不需新增字段**，只需补全填充

### 块 B endpoint 暴露
- 新增 `list_recall_frames` 公开 endpoint，过滤维度 7 个 + 时间窗 + 分页
- 复用 store 层 100% ready 的能力
- scope_hit_distribution 聚合视图（基于 hit_namespace_kinds 统计）

### 块 C MEMORY_RECALL_COMPLETED 双向补全
- 路径 A 同步 recall 加 emit（payload 字段 100% 可用）
- 路径 B 延迟 recall 物化加 RecallFrame 持久化（与块 A 协同）
- Worker dispatch 路径覆盖（plan 阶段确认）

### 块 D BEHAVIOR_PACK_LOADED EventStore 接入
- **接入点策略**：拒绝 control_plane GET API 路径，定位到 LLM dispatch 实际装载点
- **sync/async 决议**：不需要 sync→async refactor；async 入口直接调用 sync helper + await emit
- **事务边界**：try-except 隔离，emit 失败不阻塞 dispatch
- **新增 BEHAVIOR_PACK_USED**：每次 LLM 决策环 emit，pack_id 关联到 LOADED

### 块 E Web Memory Console agent 视角 UI
- 数据：MemoryResourceQuery 加 agent_runtime_id / agent_profile_id 字段
- API 调用：接入新 list_recall_frames endpoint
- UI：MemoryFiltersSection agent dropdown + MemoryResultsSection RecallFrameTimeline 组件
- 路由：query params 切换视图，不新增路由

### 块 F F095 推迟集成测
- AC-4：delegate_task tool 集成测（验证完整 BEHAVIOR_PACK_LOADED → AgentRuntime 创建 →
  workspace 初始化路径）
- AC-7b：完整集成测验证 AgentProfile.profile_id → AgentRuntime.profile_id →
  RecallFrame.agent_runtime_id 路径
- 复用 F095 handoff 提供的 fixture：
  - `apps/gateway/tests/services/test_agent_decision_envelope.py::test_end_to_end_worker_pack_to_envelope_with_worker_variants`
  - `packages/core/tests/test_behavior_workspace.py::test_worker_profile_e2e_filesystem_with_worker_variants`

## 5. plan 阶段必须深度 trace 的事项

1. **agent_decision.py:655 第三处 resolve_behavior_pack 调用语境**
2. **agent_context.py:_build_system_blocks（line 3296）的 behavior pack 装载链路**
3. **Worker dispatch / A2A dispatch 路径中 behavior pack 真实装载点**
4. **LLM 决策环唯一入口（用于 BEHAVIOR_PACK_USED emit）**
5. **MEMORY_RECALL_COMPLETED 在 Worker dispatch 路径的当前状态**
