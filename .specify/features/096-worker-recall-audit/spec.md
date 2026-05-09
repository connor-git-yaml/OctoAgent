# F096 Worker Recall Audit & Provenance — Spec（v0.1 GATE_DESIGN 草案）

| 字段 | 值 |
|------|-----|
| Feature ID | F096 |
| 阶段 | M5 阶段 1（Worker 完整对等性，**收尾整合点**）|
| 主责设计哲学 | H2 完整 Agent 对等性（Worker 完整 Recall + Behavior 加载可审计）|
| 前置依赖 | F092 DelegationPlane + F093 Worker Full Session Parity + F094 Worker Memory Parity + F095 Worker Behavior Workspace Parity |
| 承接的推迟项 | F094: list_recall_frames endpoint + MEMORY_RECALL_COMPLETED 覆盖 / F095: BEHAVIOR_PACK_LOADED EventStore 接入 + AC-4 / AC-7b 集成测 |
| baseline | dd70854（origin/master，含 F095）|
| 分支 | feature/096-worker-recall-audit |
| 状态 | spec drafted（v0.2，Codex-style review #1 全闭环：3 high + 6 medium + 2 low 接受 / 1 medium 推迟 F107 / 1 low ignore）|
| 完成后开启 | 阶段 2（F097 Subagent Mode Cleanup）|

---

## 1. 目标（Why）

让 Worker / 主 Agent 的 **Recall 召回** 与 **Behavior Pack 加载** 两类核心运行时事件成为**完整可审计 + Web 可视**的链路：

1. **审计源完整**：每条 RecallFrame 都能定位到 `agent_profile_id → agent_runtime_id → agent_session_id → namespace_kind` 完整链路；同步 / 延迟 / Worker dispatch 三条 recall 路径**双向**覆盖（同时持久化 RecallFrame **且** emit MEMORY_RECALL_COMPLETED）。
2. **Behavior 加载真实落账**：F095 已就位 schema + helper，但实际 emit 推迟到 F096——本 Feature 把 BEHAVIOR_PACK_LOADED 接入到 LLM dispatch 的真实装载点（**不是** control_plane GET API 的 display 路径），并新增 BEHAVIOR_PACK_USED 反映"真实 LLM 决策环使用"频次。
3. **Web 可视**：Web Memory Console 增加 agent 视角 UI（按 agent_profile_id / agent_runtime_id 过滤 + 分组展示），让用户能直接看"Worker `research` 在 session X 召回了哪些 fact / 来自哪些 namespace"。

**反命题**：F096 不动 Subagent Mode 显式建模（F097）/ A2A receiver context（F098）/ Ask-back（F099）/ Decision Loop Alignment（F100）/ main direct 走 AGENT_PRIVATE（F107）。

---

## 2. 实测侦察对照（块 A 验收）

> 实测报告完整版：[research/codebase-scan.md](research/codebase-scan.md)。
> baseline = dd70854，路径以 worktree 内 `octoagent/` 为根。

### 2.1 RecallFrame 写入路径覆盖矩阵（baseline）

| 路径 | 位置 | RecallFrame | MEMORY_RECALL_COMPLETED | F096 目标 |
|------|------|-------------|-------------------------|----------|
| **A 同步 recall** | `apps/gateway/src/octoagent/gateway/services/agent_context.py:848-875` | ✅ 16 字段 100% | ❌ **未 emit** | ✅ + ✅ |
| **B 延迟 recall 物化** | `apps/gateway/src/octoagent/gateway/services/task_service.py:1550-1725` | ❌ **不创建** | ✅ | ✅ + ✅ |
| **C Worker dispatch** | 待 plan 阶段精确定位（agent_decision / capability_pack）| ❓ | ❓ | ✅ + ✅ |

### 2.2 list_recall_frames Store vs Endpoint

- **Store 层**：`packages/core/src/octoagent/core/store/agent_context_store.py:1152-1206` ready，
  支持 7 维过滤（agent_session_id / agent_runtime_id / context_frame_id / task_id /
  project_id / queried_namespace_kind / hit_namespace_kind）
- **Endpoint**：❌ control_plane Memory 服务无公开 endpoint；唯一内部调用 `session_service.py:225`

### 2.3 BEHAVIOR_PACK_LOADED 接入点 sync/async 边界

- F095 ready：`BehaviorPackLoadedPayload`（packages/core/.../behavior.py:290-314）+
  `make_behavior_pack_loaded_payload`（agent_decision.py:320-357，sync）+
  `EventType.BEHAVIOR_PACK_LOADED`（enums.py:220）+ pack_id hash 化
- **拒绝接入点**：control_plane GET API（agent_service.py:111 / worker_service.py:168/255）—— display only，每次 web admin 打开就 emit 会污染审计
- **真实接入点**：LLM dispatch 装载 pack 时（plan 阶段精确定位 agent_decision.py:655 / agent_context.py:_build_system_blocks 链路）
- **sync/async 决议**：✅ **不需要 sync→async 全链路 refactor**，sync helper 可直接在 async 上下文调用；EventStore.append_event_committed 是 async；try-except 隔离单点失败

### 2.4 Web Memory Console 现状

- 框架：React + TS + Vite，路由 React Router
- RecallFrameItem 接口（types/index.ts:843-857）已含 agent_runtime_id / agent_session_id
- 仅 ContextContinuityDocument 资源条件展示
- ❌ 无 agent 视角过滤入口
- ❌ MemoryResourceQuery 无 agent_profile_id / agent_runtime_id 字段

---

## 3. 范围

### 3.1 块 B：list_recall_frames endpoint 完整暴露（F094 推迟项 ✅ 必做）

新增 control_plane Memory 域 endpoint：

```
GET /api/control/recall-frames
Query params:
  agent_runtime_id?: str           # 精确匹配
  agent_session_id?: str           # 精确匹配
  context_frame_id?: str
  task_id?: str
  project_id?: str
  queried_namespace_kind?: str     # enum: PROJECT_SHARED | AGENT_PRIVATE | WORKER_PRIVATE
  hit_namespace_kind?: str
  created_after?: datetime
  created_before?: datetime
  limit?: int = 50                 # 默认 50，最大 200
  offset?: int = 0
  group_by?: str                   # "agent_runtime_id" | "agent_session_id" | None
```

返回 schema：
- `frames: list[RecallFrameItem]`（**M7 闭环**：RecallFrameItem 当前 13 字段，F096 实施时扩展补缺失字段（metadata / source_refs / budget），与 RecallFrame model 16 字段对齐；frontend types/index.ts:843 同步扩展）
- `total: int`（过滤后总数，不分页前；**H3 闭环**：依赖 store 层新增 `count_recall_frames(filters)` 方法）
- `scope_hit_distribution?: dict[MemoryNamespaceKind, int]`（基于 hit_namespace_kinds 聚合）
- `agent_recall_timelines?: list[AgentRecallTimeline]`（仅 group_by=agent_runtime_id 时）

> **M11 闭环**：Phase 顺序调整为 A → C → **B** → D → E → F（B 提前到 D 之前；前端 E 依赖 B 的 endpoint，D 不依赖 E）

### 3.2 块 C：MEMORY_RECALL_COMPLETED 覆盖范围扩大（F094 推迟项 ✅ 必做）

| 路径 | F096 改动 |
|------|-----------|
| A 同步 recall | 在 agent_context.py:848-875 区域内，RecallFrame 持久化后立即 emit MEMORY_RECALL_COMPLETED；agent_runtime_id / queried_namespace_kinds / hit_namespace_kinds 100% 可用（无审计派生） |
| B 延迟 recall 物化 | 在 task_service.py:1695 emit 前后补 RecallFrame 持久化（agent_session_id 派生策略 plan 阶段决定，可设空或从 task_metadata 派生） |
| C Worker dispatch | plan 阶段精确定位后补 RecallFrame + emit（与路径 A 等价） |

事件 payload 不新增字段（F094 已加 agent_runtime_id / queried_namespace_kinds / hit_namespace_kinds 完整）。

### 3.3 块 D：BEHAVIOR_PACK_LOADED EventStore 接入 + BEHAVIOR_PACK_USED 新增（F095 推迟项 ✅ 必做）

#### D-1 BEHAVIOR_PACK_LOADED 真接入

接入点 = LLM dispatch **真实装载 pack** 的 async 入口（plan 阶段精确定位 + 锁定）：
- 必须区分 **cache hit vs miss**：cache miss 才 emit（`pack.metadata["cache_state"] == "miss"`）
- 必须 try-except 隔离：emit 失败不阻塞 dispatch（log warn，dispatch 继续）
- `update_task_pointer=False`：不污染 task pointer

#### D-2 BEHAVIOR_PACK_USED 新增

EventType 新增 `BEHAVIOR_PACK_USED`（packages/core/.../enums.py）+ payload schema：

```python
class BehaviorPackUsedPayload(BaseModel):
    pack_id: str           # 关联到 BEHAVIOR_PACK_LOADED 实例（hash 化 pack_id）
    agent_id: str          # = AgentProfile.profile_id
    agent_kind: str        # main / worker / subagent
    agent_runtime_id: str  # 与 F094 RecallFrame 同维度
    task_id: str
    session_id: str | None
    use_phase: str         # "context_preparation" | "decision_reasoning" | ...
    llm_model_alias: str
    input_token_count: int | None
    output_token_count: int | None
    memory_hit_count: int | None
    created_at: datetime
```

emit 频次：每次 LLM 决策环 emit 一次（plan 阶段精确定位决策环唯一入口）。

#### D-3 LOADED vs USED 语义边界（不可混淆）

- LOADED：cache miss 时 emit；反映 "pack 装载到 cache"
- USED：每次 LLM 决策环 emit；反映 "真实使用量"
- 关联键：`pack_id`（F095 ready 的 16 hex hash）

### 3.4 块 E：Web Memory Console agent 视角 UI（原 F096 范围 ✅ 必做）

#### E-1 数据层

`MemoryResourceQuery`（types/index.ts）扩展：
- `agent_runtime_id?: str`
- `agent_profile_id?: str`
- `group_by?: "agent_runtime_id" | "agent_session_id"`

`AgentRecallTimeline` 类型新增（types/index.ts）：
- agent_runtime_id / agent_profile_id / agent_name / recall_frames[] / total_hit_count

#### E-2 UI 组件

| 组件 | 改动 |
|------|------|
| `frontend/src/domains/memory/MemoryPage.tsx` | 新增 "Recall Audit" 标签页 / 侧栏 |
| `frontend/src/domains/memory/MemoryFiltersSection.tsx` | 新增 agent_filter dropdown（profile / runtime 二选一）|
| `frontend/src/domains/memory/MemoryResultsSection.tsx` | 条件渲染 RecallFrameTimeline（group_by=agent_runtime_id 时分组）|
| 新增 `frontend/src/domains/memory/RecallFrameTimeline.tsx` | Agent 分组时间线渲染 |
| `frontend/src/domains/memory/shared.tsx` | 新增 `buildAgentRecallTimelines()` helper |

> **M5 闭环**：frontend 路径全部修正为 `frontend/src/domains/memory/`（旧 spec 的 `frontend/src/pages/` + `frontend/src/components/memory/` 是错的，与 plan §0.8 实测对齐）

#### E-3 路由策略

保留 `/memory` 单一路由，通过 query params 控制视图：
- `/memory?view=recall-audit` → 默认列表
- `/memory?view=recall-audit&agent_runtime_id=xxx` → 单 agent 过滤
- `/memory?view=recall-audit&group_by=agent_runtime_id` → 分组视图

### 3.5 块 F：F095 推迟集成测补全（✅ 必做）

#### AC-4 delegate_task tool 集成测

验证完整链路：
```
delegate_task(profile_id, ...) →
  worker_service.create_worker →
  AgentRuntime 创建 →
  workspace 初始化 →
  BEHAVIOR_PACK_LOADED emit（pack_id 含 worker hash）→
  agent dispatch
```

断言：
- BEHAVIOR_PACK_LOADED 事件存在（query EventStore）
- payload.agent_id == AgentProfile.profile_id
- payload.agent_kind == "worker"
- AgentRuntime 表包含 (profile_id, runtime_id) 二元组

#### AC-7b 完整 audit 链路集成测

验证 `AgentProfile.profile_id → AgentRuntime.profile_id → RecallFrame.agent_runtime_id` 完整路径：
```
worker dispatch + memory.write + memory.recall →
  RecallFrame 持久化 + MEMORY_RECALL_COMPLETED emit + BEHAVIOR_PACK_LOADED emit →
  list_recall_frames(agent_runtime_id=runtime_id) 返回非空
```

断言：
- RecallFrame.agent_runtime_id 与 LOADED.agent_id（profile_id）通过 AgentRuntime 表对齐
- list_recall_frames endpoint 过滤维度生效（query agent_runtime_id 返回该 runtime 的 frames）

复用 F095 handoff 提供的 fixture：
- `apps/gateway/tests/services/test_agent_decision_envelope.py::test_end_to_end_worker_pack_to_envelope_with_worker_variants`
- `packages/core/tests/test_behavior_workspace.py::test_worker_profile_e2e_filesystem_with_worker_variants`

---

## 4. 验收标准（AC）

### 块 A 实测侦察验收（spec 阶段产出）
- [x] **AC-A1**：RecallFrame 字段填充情况实测对照表（同步 vs 延迟）已固化到 `research/codebase-scan.md` §1.2
- [x] **AC-A2**：MEMORY_RECALL_COMPLETED 当前 emit 路径覆盖矩阵已固化 §1.4
- [x] **AC-A3**：BEHAVIOR_PACK_LOADED EventStore 接入的 sync/async 边界已给出明确解决方案 §2.3（不需要全链路 refactor）
- [x] **AC-A4**：Web Memory Console 当前能力 + agent 视角 UI 设计切入点已固化 §2.4 + §3.4

### 块 B：list_recall_frames endpoint
- [ ] **AC-B1**：`GET /api/control/recall-frames` endpoint 实现，含 7 维过滤 + 时间窗 + 分页 + group_by
- [ ] **AC-B2**：response 含 `scope_hit_distribution` 聚合视图
- [ ] **AC-B3**：单测覆盖每个过滤维度（≥ 7 个测试）
- [ ] **AC-B4**：endpoint 集成测（route + auth + 分页边界）

### 块 C：MEMORY_RECALL_COMPLETED 覆盖
- [ ] **AC-C1**：路径 A 同步 recall 加 emit MEMORY_RECALL_COMPLETED（payload 字段 100% 填充）
- [ ] **AC-C2**：路径 B 延迟 recall 物化 加 RecallFrame 持久化（agent_session_id 派生策略 plan 阶段决定）
- [ ] **AC-C3**：Worker dispatch 路径覆盖（plan 阶段精确定位）
- [ ] **AC-C4**：单测覆盖三条路径 emit + RecallFrame 持久化（≥ 3 个测试）

### 块 D：BEHAVIOR_PACK_LOADED EventStore 接入 + BEHAVIOR_PACK_USED
- [ ] **AC-D1**：BEHAVIOR_PACK_LOADED 真接入 `EventStore.append_event_committed`，仅 cache miss 时 emit
- [ ] **AC-D2**：接入点为 LLM dispatch 真实装载点，非 control_plane GET API
- [ ] **AC-D3**：emit 失败不阻塞 dispatch（try-except + log warn）
- [ ] **AC-D4**：BEHAVIOR_PACK_USED EventType + Payload schema 新增，每次 LLM 决策环 emit 一次
- [ ] **AC-D5**：单测覆盖 LOADED cache miss / cache hit 不重复 / USED emit 频次 / pack_id 关联（≥ 4 个测试）

### 块 E：Web Memory Console agent 视角
- [ ] **AC-E1**：MemoryResourceQuery 扩展 agent_runtime_id / agent_profile_id / group_by
- [ ] **AC-E2**：MemoryFiltersSection 加 agent dropdown
- [ ] **AC-E3**：RecallFrameTimeline 组件实现 agent 分组渲染
- [ ] **AC-E4**：MemoryPage "Recall Audit" 标签页接入新 endpoint
- [ ] **AC-E5**：前端 e2e / 组件测试覆盖（≥ 3 个测试，至少 vitest 组件级）

### 块 F：F095 推迟集成测
- [ ] **AC-F1（= F095 AC-4）**：delegate_task tool 集成测 PASS（BEHAVIOR_PACK_LOADED 事件可查 + AgentRuntime 表对齐）
- [ ] **AC-F2（= F095 AC-7b）**：完整 audit 链路集成测 PASS（profile_id → runtime_id → recall_frame.agent_runtime_id 三层对齐 + list_recall_frames endpoint 过滤生效）

### 全局验收
- [ ] **AC-G1**：全量回归 0 regression vs F095 baseline (dd70854)，目标 ≥ 3191 passed
- [ ] **AC-G2**：每 Phase 后 e2e_smoke PASS（pre-commit hook）
- [ ] **AC-G3**：每 Phase 前 Codex review 闭环（0 high 残留）
- [ ] **AC-G4**：Final cross-Phase Codex review 通过
- [ ] **AC-G5**：completion-report.md 已产出，含"实际 vs 计划"对照 + Codex finding 闭环表 + F094/F095 推迟项收口确认 + F097/F098 接入点说明
- [ ] **AC-G6**：Phase 跳过显式归档（若有）

---

### 4.3 F107 schema 演化预留（M9 review finding 闭环）

F096 引入的 BEHAVIOR_PACK_USED.agent_kind / BEHAVIOR_PACK_LOADED 事件 schema 在 F107 完全合并 WorkerProfile/AgentProfile 时可能 break：
- F096 不引入 schema_version 字段（M5 阶段 1 范围内 schema 一次稳定，不预占）
- BEHAVIOR_PACK_USED.agent_kind 仅 emit `main` / `worker`（不预占 `subagent`，由 F097 引入）
- F107 演化时本事件可能 break——届时需在 F107 spec 显式归档迁移路径

## 5. 不在范围（明确排除）

- ❌ Subagent Mode 显式建模 → **F097**
- ❌ A2A receiver context / Worker→Worker 解禁 → **F098**
- ❌ Ask-back / source 泛化 → **F099**
- ❌ Decision Loop Alignment → **F100**
- ❌ main direct 路径走 AGENT_PRIVATE namespace → **F107**
- ❌ share_with_workers 字段彻底删除 → **F107**
- ❌ D2 WorkerProfile 完全合并 → **F107**
- ❌ pack_id 长度扩展（16 hex → 32/64）→ 单用户单 worktree 16 足够，跨用户场景延后
- ❌ BEHAVIOR_PACK_LOADED / USED 事件 retention / cleanup 策略 → 走 EventStore 通用策略

---

## 6. 关键约束 + 设计决策

### 6.1 约束

1. **F094 + F095 推迟项必须全部承接**——块 B/C/D/F 是承接，不是 optional
2. **行为可观测变更约束**：每个新 endpoint / 事件必须有审计 trace + 单测覆盖
3. **每 Phase 后回归 0 regression vs F095 baseline (dd70854)**，e2e_smoke 必过
4. **每 Phase 前 Codex review** + 最后必走 **Final cross-Phase Codex review**
5. **必须产出 completion-report.md**
6. **不主动 push origin/master**：按 CLAUDE.local.md §Spawned Task 处理流程，归总报告等用户拍板
7. **Phase 顺序**：A 实测落地 → C 事件扩大（小） → **B endpoint** → D EventStore 接入 → E Web UI → F 集成测（先简后难；M11 review finding 闭环：B 提前到 D 之前——前端 E 依赖 B 的 endpoint，D 不依赖 E）

### 6.2 关键设计决策

#### 决策 1：BEHAVIOR_PACK_LOADED 接入点 —— 拒绝 control_plane GET API

control_plane agent_service.py:111 / worker_service.py:168 调用 `build_behavior_system_summary` 是为了 **GET API 的 display 输出**（用户打开 web admin 看 agent 列表会跑），**不**是 LLM 实际装载 behavior pack 到 dispatch。

**接入策略**：
- F096 spec 阶段不锁定接入点的精确 file:line（plan 阶段必须深度 trace `agent_decision.py:655` 第三处 resolve_behavior_pack 调用语境 + `agent_context.py:_build_system_blocks` (3296) 装载链）
- spec 阶段约定的不变量：接入点 = **LLM dispatch 真实装载 pack 时**，非 GET API display 路径
- spec 阶段约定 cache miss 才 emit（已有 `pack.metadata["cache_state"]` 标记）

#### 决策 2：sync/async 边界 —— 不需要全链路 refactor

`resolve_behavior_pack` / `build_behavior_system_summary` / `make_behavior_pack_loaded_payload` 全是 sync（纯数据 + 文件系统读，无阻塞 IO）。
EventStore.append_event_committed 是 async。

**约定**：在 LLM dispatch 的 async 入口处直接调用 sync helper 取 payload，再 `await event_store.append_event_committed(event)`。无需把 sync 链改 async。

错误隔离：try-except 包裹（一个 emit 失败不阻塞 dispatch；log warn 即可，依赖 EventStore 的事务安全）。

#### 决策 3：LOADED vs USED 语义边界

- LOADED 频次 = cache miss 频次 ≈ 一次 worktree boot / 一次 pack 文件 mtime 变更
- USED 频次 = LLM 决策环触发频次（每个 task 决策可能多轮）
- 关联键 = pack_id（F095 ready 的 16 hex hash）—— 一个 LOADED 可关联到 N 个 USED

#### 决策 4：路径 B 延迟 recall RecallFrame 持久化的 agent_session_id

延迟 recall 在 artifact 物化时不一定有完整 session 上下文。spec 阶段约定：
- 优先从 task_metadata 派生 agent_session_id
- 派生不到则设空（baseline degrade，不阻塞 RecallFrame 持久化）
- plan 阶段决定具体派生算法

#### 决策 5：endpoint group_by 默认 None

`group_by` 是 opt-in 参数；默认返回扁平列表，避免大量 RecallFrame 触发 N² 分组成本。

### 6.3 性能 / 安全约束

- list_recall_frames endpoint 默认 limit=50，最大 limit=200（防止全表扫描）
- agent_runtime_id 过滤必须用 store 层索引（agent_context_store.py 已 ready）
- BEHAVIOR_PACK_USED emit 不增加 LLM 决策环延迟（async 写事件，不等结果）
- frontend 大量 RecallFrame 渲染用虚拟滚动（≥ 100 条时启用）

---

## 7. 依赖 / Blocker

### 7.1 必须就位（已就位）
- ✅ F094 RecallFrame 双字段（queried_namespace_kinds / hit_namespace_kinds）
- ✅ F094 store 层 list_recall_frames 7 维过滤 ready
- ✅ F095 BehaviorPackLoadedPayload schema + helper + EventType
- ✅ F095 pack_id hash 化（关联键 ready）
- ✅ F093 AGENT_SESSION_TURN_PERSISTED 事件 schema 风格（trace_id 风格 `f"trace-{task_id}"`）—— BEHAVIOR_PACK_USED 沿用同 convention

### 7.2 plan 阶段必须深度 trace
- LLM dispatch 真实装载 pack 的精确 file:line（agent_decision.py:655 / agent_context.py:_build_system_blocks 链）
- LLM 决策环唯一入口（用于 BEHAVIOR_PACK_USED emit 频次约束）
- Worker dispatch 路径中 RecallFrame + MEMORY_RECALL_COMPLETED 当前状态

---

## 8. 修订记录

- v0.1（2026-05-10）：spec 草案，块 A 实测落地，6 个 AC 块（A 实测验收 + B-F 实施 AC）+ 6 块全局 AC + 7 项关键约束 + 5 项设计决策。等待 Codex pre-spec/plan adversarial review。

---

## 9. F097 / F098 接入点（前向声明）

F096 完成后阶段 1 全部关闭，**阶段 2 启动**。

### F097 Subagent Mode Cleanup 的接入点
- F096 新增的 BEHAVIOR_PACK_LOADED / USED 事件可被 F097 SubagentDelegation 复用：
  Subagent 装载 pack 时 emit LOADED（agent_kind="subagent"），LLM 决策环 emit USED
- F096 list_recall_frames endpoint 可被 F097 用于审计 Subagent 召回了哪些 fact
- F096 RecallFrame.agent_runtime_id 完整覆盖 Subagent 路径（Subagent 共享调用方 RuntimeHintBundle/Project/Memory，但有独立 AgentRuntime 行）

### F098 A2A Mode + Worker↔Worker 的接入点
- A2A receiver 在自己 context 工作时 RecallFrame 持久化用接收方 agent_runtime_id
- Worker→Worker 解禁后，`_enforce_child_target_kind_policy` 删除——F096 RecallFrame 审计可暴露"哪个 Worker 召回了什么"
- F096 BEHAVIOR_PACK_USED.session_id 字段已对齐 A2A conversation 的 session 维度

### 阶段 1 收尾后阶段 2 启动条件
- ✅ F096 acceptance gate 全关闭（AC-A1 → AC-G6）
- ✅ Final cross-Phase Codex review 通过
- ✅ 主 session 拍板 + push origin/master
- ✅ F094/F095 推迟项 100% 收口（completion-report 显式 verify）
