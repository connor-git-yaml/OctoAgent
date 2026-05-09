# F096 Worker Recall Audit & Provenance — Tasks（v0.2）

> 上游：[spec.md](spec.md) v0.2 / [plan.md](plan.md) v0.2 / [codex-review-spec-plan.md](codex-review-spec-plan.md)
>
> **Phase 顺序（v0.2 review #1 M11 闭环）**：A → C → **B** → D → E → F → Verify（B 提前到 D 之前；前端 E 依赖 B 的 endpoint）

## 实施顺序（v0.2 review #1 M11 闭环）

> **注意**：本文档章节按 v0.1 顺序排版（A → C → D → B → E → F），但**实施时必须按 v0.2 顺序推进**：

| 实施序 | Phase | 章节位置 |
|--------|-------|----------|
| 1/6 | A 路径 B 延迟 recall 补 RecallFrame | 章节 §"Phase A" |
| 2/6 | C 同步 recall + Worker 路径补 emit | 章节 §"Phase C" |
| 3/6 | **B**（提前）list_recall_frames endpoint | 章节 §"Phase B" |
| 4/6 | D BEHAVIOR_PACK_LOADED + USED | 章节 §"Phase D" |
| 5/6 | E Web Memory Console agent 视角 UI | 章节 §"Phase E" |
| 6/6 | F F095 推迟集成测补全 | 章节 §"Phase F" |

理由：B 提前后 E 可直接接入 B 暴露的 endpoint；D 不依赖 E。

## 任务编号约定

- `T-A-N` Phase A 任务（路径 B 延迟 recall 补 RecallFrame）
- `T-C-N` Phase C 任务（同步 recall 路径补 emit）
- `T-D-N` Phase D 任务（BEHAVIOR_PACK_LOADED + USED）
- `T-B-N` Phase B 任务（list_recall_frames endpoint）
- `T-E-N` Phase E 任务（Web Memory Console agent 视角）
- `T-F-N` Phase F 任务（F095 推迟集成测）
- `T-V-N` Verify 阶段任务

每任务标记：
- 类型：`[code]` / `[test]` / `[doc]` / `[review]` / `[commit]`
- 依赖：`(deps: T-X-N)`

---

## Pre-Phase：Codex pre-spec/plan adversarial review

- **T-PRE-1** `[review]` 触发 `/codex:adversarial-review` foreground，输入 spec.md + plan.md + codebase-scan.md。范围 = "F096 spec/plan 整体设计是否走错路径"
- **T-PRE-2** `[review]` 处理 finding：high/medium 闭环 → 接受改 spec/plan；拒绝 → 写入 `codex-review-spec-plan.md` 显式归档
- **T-PRE-3** `[commit]` commit `spec.md + plan.md + tasks.md + codebase-scan.md + codex-review-spec-plan.md + trace.md` 作为 spec-plan-tasks 三件套（不主动 push）

---

## Phase A：路径 B 延迟 recall 补 RecallFrame

**目标 AC**：spec §4 块 C 中 AC-C2

### T-A-1 `[code]` 实施 RecallFrame 持久化（**review #1 H2 闭环**）

文件：`apps/gateway/src/octoagent/gateway/services/task_service.py`

- 在 `_materialize_delayed_recall_once`（line 1550-1725 内）MEMORY_RECALL_COMPLETED emit 之**前**，构造 RecallFrame
- 派生策略（v0.2 简化）：
  - **直接用 `frame.agent_session_id`** — `_materialize_delayed_recall_once:1577` 已 fetch ContextFrame，`ContextFrame.agent_session_id` 是直接可读的强一致来源
  - **直接用 `frame.context_frame_id`**（同 frame 来源）
  - 删除 task_metadata 派生 / session_state 反查 / fallback 空字符串链路
- 调用 `await self._stores.agent_context_store.save_recall_frame(recall_frame)`
- try-except 隔离：persist 失败时 log warn + 继续 emit 路径

### T-A-2 `[test]` 单测覆盖

文件：新建或扩展 `apps/gateway/tests/services/test_task_service_delayed_recall.py`

- test_delayed_recall_persists_recall_frame：物化后 store 查询非空
- test_delayed_recall_recall_frame_field_completion：所有字段填充正确（含空值降级）
- test_delayed_recall_save_recall_frame_failure_does_not_block_emit：persist 失败时 MEMORY_RECALL_COMPLETED 仍 emit

### T-A-3 `[test]` 集成测（**review #1 L13 闭环——命名改**）

新建 `apps/gateway/tests/integration/test_recall_frame_persist_and_emit_paths.py`（v0.1 旧名 `test_recall_frame_dual_path.py` 改 `_persist_and_emit_paths` 避免"dual"歧义——worker dispatch 也走 sync 主路径，不是第三条独立路径）：
- test_delayed_recall_persists_and_emits：trigger 一次 delayed recall → list_recall_frames(task_id=...) 返回该 frame + EventStore 内 MEMORY_RECALL_COMPLETED 可查（同一 task_id）

### T-A-4 `[test]` 全量回归

`pytest -q` 必通（≥ 3191 passed vs F095 baseline）+ `pytest -m e2e_smoke` 必通

### T-A-5 `[review]` per-Phase Codex review

`/codex:adversarial-review` 输入 Phase A commit diff + 测试。处理 finding 写入 `codex-review-phase-a.md`

### T-A-6 `[commit]` Phase A commit

commit message: `feat(F096-Phase-A): 延迟 recall 路径补 RecallFrame 持久化`

---

## Phase C：同步 recall 路径补 emit MEMORY_RECALL_COMPLETED

**目标 AC**：spec §4 块 C 中 AC-C1, AC-C3, AC-C4

### T-C-1 `[code]` 实施同步路径 emit（**review #1 M8 + L12 闭环**）

文件：`apps/gateway/src/octoagent/gateway/services/agent_context.py`

- 在 line 936 `await self._stores.conn.commit()` 之**后**（事务边界 commit，event 写入更稳；L12 闭环），新增 emit MEMORY_RECALL_COMPLETED（按 plan §3.2 代码片段）
- payload 字段全部 100% 填充（agent_runtime_id / queried_namespace_kinds / hit_namespace_kinds / context_frame_id / scope_ids 等）
- **idempotency_key**（M8 闭环）：`f"{recall_frame_id}:event"` —— recall_frame_id 唯一对应一次 build_task_context dispatch；retry/resume 重新 build_task_context 会有新 recall_frame_id；同一 recall_frame_id 不会被多次 emit
- try-except 隔离：emit 失败 log warn + 不阻塞 build_task_context 返回

### T-C-2 `[code]` Worker dispatch 路径覆盖确认

- 不需新代码改动（Worker dispatch 复用 build_task_context 主路径）
- T-C-3 单测必须含 worker dispatch 验证

### T-C-3 `[test]` 单测覆盖

新建 `apps/gateway/tests/services/test_agent_context_recall_emit.py`：
- test_sync_path_emits_memory_recall_completed：build_task_context 后 EventStore 可查 MEMORY_RECALL_COMPLETED
- test_sync_emit_payload_field_completion：所有字段 100% 填充（非审计派生）
- test_sync_emit_failure_does_not_block_dispatch
- test_worker_dispatch_emits_memory_recall_completed：worker_capability 路径同样 emit

### T-C-4 `[test]` 集成测扩展

扩展 T-A-3 创建的 `test_recall_frame_dual_path.py`：
- test_sync_recall_emits_via_build_task_context：sync 路径 emit + RecallFrame 双查
- test_dual_path_consistency：sync 路径 RecallFrame.agent_runtime_id == MEMORY_RECALL_COMPLETED.agent_runtime_id

### T-C-5 `[test]` 全量回归

`pytest -q` 必通 + `pytest -m e2e_smoke` 必通

### T-C-6 `[review]` per-Phase Codex review

输入 Phase A+C cumulative diff，处理 finding 写入 `codex-review-phase-c.md`

### T-C-7 `[commit]` Phase C commit

commit message: `feat(F096-Phase-C): 同步 recall 路径补 emit MEMORY_RECALL_COMPLETED + Worker dispatch 覆盖`

---

## Phase D：BEHAVIOR_PACK_LOADED + BEHAVIOR_PACK_USED

**目标 AC**：spec §4 块 D 中 AC-D1 ~ AC-D5

### T-D-1 `[code]` EventType 新增

文件：`packages/core/src/octoagent/core/models/enums.py`
- 新增 `BEHAVIOR_PACK_USED = "BEHAVIOR_PACK_USED"`

### T-D-2 `[code]` BehaviorPackUsedPayload schema（**review #1 M4 + M9 闭环**）

文件：`packages/core/src/octoagent/core/models/behavior.py`
- 新增 `BehaviorPackUsedPayload`（按 plan §4.3 字段清单）
- M4：`agent_kind` 字段 docstring 明确 F096 仅 emit `main` / `worker`（不预占 `subagent`，由 F097 引入）
- M9：F096 不引入 schema_version 字段（接受推迟到 F107 演化时再加）
- export 进 `__init__.py`

### T-D-3 `[code]` make_behavior_pack_used_payload helper

文件：`apps/gateway/src/octoagent/gateway/services/agent_decision.py`
- 新增 helper（按 plan §4.4）
- 紧邻 `make_behavior_pack_loaded_payload` 后

### T-D-4 `[code]` build_task_context 内 emit 接入（**review #1 H1 + L12 闭环——方案 B**）

文件：`apps/gateway/src/octoagent/gateway/services/agent_context.py`

**review H1 闭环 — 方案 A 不可行**：cache hit 路径（agent_decision.py:199-208）显式 strip `cache_state` 标记，重复 resolve 永远不会 emit LOADED。改方案 B：

实施步骤（按 plan §4.5）：
1. **`_fit_prompt_budget` 签名扩展**（line 3649）：返回 tuple 增 `loaded_pack: BehaviorPack | None` 字段（首次 cache miss 的 pack；None 表示 cache hit 全程）
2. **`_build_system_blocks` / `render_behavior_system_block` 协作**（line 3296）：在调用 render 前 resolve_behavior_pack 一次（取得带 cache_state metadata 的 pack），上报到 _fit_prompt_budget
3. **build_task_context async emit**：在 line 936 `await self._stores.conn.commit()` 之**后**（L12 闭环，事务边界 commit 后更稳）：
   - LOADED：仅 `loaded_pack is not None` 时 emit
   - USED：总 emit（cache hit 时从 `_behavior_pack_cache` 取 cache 内 pack）
4. **`_get_cached_pack_for_used` helper**：cache lookup 取 pack 用于 USED emit
- try-except 隔离

### T-D-5 `[test]` 单测覆盖

新建 `apps/gateway/tests/services/test_behavior_pack_events.py`：
- test_loaded_emits_on_cache_miss
- test_loaded_does_not_emit_on_cache_hit
- test_used_emits_every_dispatch（连续 2 次 dispatch 同 pack → 2 个 USED 事件）
- test_loaded_used_pack_id_matches（同一 pack pack_id 严格相等）
- test_emit_failure_does_not_block_dispatch
- test_pack_used_payload_field_completion

### T-D-6 `[test]` 集成测扩展

扩展 F095 fixture `test_end_to_end_worker_pack_to_envelope_with_worker_variants`：
- 新增 assertion：dispatch 后 EventStore 内 BEHAVIOR_PACK_LOADED + BEHAVIOR_PACK_USED 可查；pack_id 一致

### T-D-7 `[test]` 全量回归

`pytest -q` 必通 + `pytest -m e2e_smoke` 必通

### T-D-8 `[review]` per-Phase Codex review

输入 Phase A+C+D cumulative diff，处理 finding 写入 `codex-review-phase-d.md`

特别关注：方案 A 重复 resolve 的 perf 影响（如 finding 提出，可降级方案 B）

### T-D-9 `[commit]` Phase D commit

commit message: `feat(F096-Phase-D): BEHAVIOR_PACK_LOADED EventStore 接入 + BEHAVIOR_PACK_USED 新增`

---

## Phase B：list_recall_frames endpoint

**目标 AC**：spec §4 块 B 中 AC-B1 ~ AC-B4

### T-B-1 `[code]` Domain service 方法

文件：`apps/gateway/src/octoagent/gateway/services/control_plane/memory_service.py`
- 新增 `async def list_recall_frames(...)` 方法（按 plan §5.3）
- 调用 store 层（agent_context_store.list_recall_frames）
- 计算 scope_hit_distribution
- group_by 启用时计算 agent_recall_timelines

### T-B-2 `[code]` Response models + RecallFrameItem 字段补全（**review #1 M7 闭环**）

文件：`packages/core/src/octoagent/core/models/control_plane.py`（或对应 contracts 文件）
- 新增 `RecallFrameListDocument` / `AgentRecallTimeline`
- **RecallFrameItem 字段补全**（M7 闭环）：当前 13 字段（`packages/core/src/octoagent/core/models/control_plane/session.py:135`），缺 metadata / source_refs / budget；F096 补到 16 字段对齐 RecallFrame model
- frontend `types/index.ts:843` 同步扩展

### T-B-3 `[code]` Endpoint 实现

文件：`apps/gateway/src/octoagent/gateway/routes/control_plane.py`
- 新增 `GET /api/control/resources/recall-frames`（按 plan §5.2）
- 7 维过滤 + 时间窗 + 分页 + group_by

### T-B-4 `[code]` Coordinator DI

文件：`apps/gateway/src/octoagent/gateway/services/control_plane/_coordinator.py`
- 注入 list_recall_frames 入口

### T-B-5 `[code]` Store 层扩展（**review #1 H3 闭环——必做**）

文件：`packages/core/src/octoagent/core/store/agent_context_store.py`

实测 baseline `list_recall_frames`（line 1152）仅 7 维等值过滤 + ORDER BY + LIMIT，**无 offset / 时间窗 / count**——硬契约失配 endpoint signature。F096 必须扩展：

- `list_recall_frames` 签名补 `offset: int = 0` + SQL `LIMIT N OFFSET M`
- 补 `created_after: str | None / created_before: str | None` 字段过滤（SQL `created_at >= ? AND created_at <= ?`）
- 新增 `async def count_recall_frames(self, *, <7 维 filters>, created_after, created_before) -> int`（aggregate query，单 SQL `SELECT COUNT(*) FROM recall_frames WHERE ...`，非 N+1）
- 性能评估：count + list 两次查询；SQLite 单 user 量级 < 100k 行 + indexed query < 10ms 可接受

新增单测：
- test_list_recall_frames_offset_pagination
- test_list_recall_frames_created_after_filter
- test_list_recall_frames_created_before_filter
- test_count_recall_frames_returns_total_unrestricted_by_limit_offset
- test_count_recall_frames_with_filters

### T-B-6 `[test]` Service 单测

新建 `apps/gateway/tests/control_plane/test_memory_service_recall_frames.py`：
- test_filter_agent_runtime_id
- test_filter_agent_session_id
- test_filter_context_frame_id
- test_filter_task_id
- test_filter_project_id
- test_filter_queried_namespace_kind
- test_filter_hit_namespace_kind
- test_pagination_limit_offset
- test_scope_hit_distribution_aggregation
- test_group_by_agent_runtime_id_returns_timelines
- test_invalid_namespace_kind_returns_400

### T-B-7 `[test]` Endpoint 集成测

新建 `apps/gateway/tests/integration/test_recall_frames_endpoint.py`：
- test_endpoint_returns_200_with_filters
- test_endpoint_pagination_boundary
- test_endpoint_auth_required
- test_endpoint_response_schema

### T-B-8 `[test]` 全量回归

`pytest -q` 必通 + `pytest -m e2e_smoke` 必通

### T-B-9 `[review]` per-Phase Codex review

输入 Phase A+C+D+B cumulative diff，处理 finding 写入 `codex-review-phase-b.md`

### T-B-10 `[commit]` Phase B commit

commit message: `feat(F096-Phase-B): list_recall_frames control_plane endpoint 完整暴露`

---

## Phase E：Web Memory Console agent 视角 UI

**目标 AC**：spec §4 块 E 中 AC-E1 ~ AC-E5

### T-E-1 `[code]` Types 扩展

文件：`frontend/src/platform/contracts/controlPlane.ts`
- 新增 `RecallFrameQuery` interface（按 plan §6.2）
- 新增 `RecallFrameListDocument` interface
- 新增 `AgentRecallTimeline` interface

文件：`frontend/src/types/index.ts`
- 复用 / re-export 上述 types

### T-E-2 `[code]` API client

文件：`frontend/src/api/client.ts`
- 新增 `fetchRecallFrames(params: RecallFrameQuery): Promise<RecallFrameListDocument>`（按 plan §6.3）

### T-E-3 `[code]` UI 组件

文件：`frontend/src/domains/memory/MemoryFiltersSection.tsx`
- 新增 agent_filter dropdown（view = "recall-audit" 时启用）

文件：`frontend/src/domains/memory/MemoryResultsSection.tsx`
- 条件渲染 `<RecallFrameTimeline />` 或扁平 frames table

新建 `frontend/src/domains/memory/RecallFrameTimeline.tsx`
- agent 分组时间线渲染

文件：`frontend/src/domains/memory/MemoryPage.tsx`
- 新增 view 切换（query param `view=recall-audit`）

文件：`frontend/src/domains/memory/shared.tsx`
- 新增 `buildAgentRecallTimelines()` helper

### T-E-4 `[test]` 组件测试

新建 `frontend/src/domains/memory/RecallFrameTimeline.test.tsx`（vitest）：
- test_renders_agent_groups
- test_empty_state
- test_filters_propagate_to_query

文件：`frontend/src/api/client.test.ts`
- 新增 test_fetchRecallFrames_query_string_mapping

文件：`frontend/src/domains/memory/MemoryPage.test.tsx`
- 扩展 view 切换 test

### T-E-5 `[test]` 全量回归

`pnpm -C frontend test` 必通 + `pytest -q` 必通（不影响后端）

### T-E-6 `[review]` per-Phase Codex review

输入 Phase E commit diff，处理 finding 写入 `codex-review-phase-e.md`

### T-E-7 `[commit]` Phase E commit

commit message: `feat(F096-Phase-E): Web Memory Console agent 视角 UI`

---

## Phase F：F095 推迟集成测补全

**目标 AC**：spec §4 块 F 中 AC-F1, AC-F2

### T-F-1 `[test]` AC-F1 delegate_task tool 集成测

新建 `apps/gateway/tests/integration/test_f096_audit_chain.py`：
- test_delegate_task_emits_behavior_pack_loaded（按 plan §7.2）

### T-F-2 `[test]` AC-F2 完整 audit 链路集成测

同文件：
- test_audit_chain_profile_runtime_recallframe（按 plan §7.3）

### T-F-3 `[test]` 全量回归

`pytest -q` 必通 + `pytest -m e2e_smoke` 必通

### T-F-4 `[review]` per-Phase Codex review

输入 Phase F commit diff，处理 finding 写入 `codex-review-phase-f.md`

### T-F-5 `[commit]` Phase F commit

commit message: `feat(F096-Phase-F): F095 推迟集成测 AC-4 / AC-7b 补全`

---

## Verify 阶段

### T-V-1 `[test]` 全量回归 vs F095 baseline

`pytest -q` 必通（≥ 3191 passed），与 F095 baseline (dd70854) 对比 0 net regression

### T-V-2 `[test]` e2e_smoke 5x 循环

`octo e2e --loop=5`（参照 F087）

### T-V-3 `[test]` Frontend 测试

`pnpm -C frontend test`

### T-V-4 `[review]` Final cross-Phase Codex review

`/codex:adversarial-review` 输入 plan.md + 全部 6 Phase commit diff，专门检查"是否漏 Phase / 是否偏离原计划且未在 commit message 说明"。处理 finding 写入 `codex-review-final.md`

### T-V-5 `[doc]` 产出 completion-report.md

格式参照 F094/F095 completion-report：
- 实际 vs 计划对照表（Phase A-F + Verify）
- Codex review 全闭环表（pre-spec/plan + per-Phase A-F + Final）
- F094/F095 推迟项收口确认表
- F097/F098 接入点说明
- Phase 跳过显式归档（如有）

### T-V-6 `[doc]` handoff.md（前向声明）

新建 `.specify/features/096-worker-recall-audit/handoff.md`：
- 给 F097 Subagent Mode Cleanup 的接口点
- 给 F098 A2A Mode + Worker↔Worker 的接口点
- 给 F107 Capability Layer Refactor 的接口点（如有）

### T-V-7 `[commit]` Verify 阶段最终 commit

commit message: `docs(F096-Phase-Verify): Final cross-Phase Codex review 闭环 + completion-report + handoff`

---

## 全局约束

- **不主动 push origin/master**：T-V-7 后回归主 session 归总报告，等用户拍板
- **Codex review finding 处理**：参照 CLAUDE.local.md §"Codex Adversarial Review 强制规则"
- **Phase 跳过显式归档**：若实施中发现某 Phase 已 baseline ready 而决定跳过，必须在 completion-report 显式写"Phase X 跳过，理由 Y"
- **每 Phase commit 前回归 0 regression vs F095 baseline**

---

## 任务总数

| Phase | 任务数 |
|-------|--------|
| pre-spec/plan | 3 |
| A | 6 |
| C | 7 |
| D | 9 |
| B | 10 |
| E | 7 |
| F | 5 |
| Verify | 7 |
| **合计** | **54** |
