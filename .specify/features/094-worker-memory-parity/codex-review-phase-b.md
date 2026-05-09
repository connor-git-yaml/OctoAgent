# Phase B Codex Adversarial Review 闭环

**Phase**: B（核心新行为：AGENT_PRIVATE 真生效 + 废弃 worker WORKER_PRIVATE 路径）
**Review 时间**: 2026-05-09
**Model**: Codex CLI (model_reasoning_effort=high)
**输入**: tasks.md Phase B B0-B9 全部 staged diff
**Findings 总数**: 6（1 HIGH + 4 MED + 1 LOW）

## Findings 处理决议

### HIGH-1: memory.write worker default 解析失败后 fallback 违反 NFR-3 ✅ 接受 + 闭环

**Evidence**: B4 在 `WorkerMemoryNamespaceNotResolved` 时 `log.warning + fallback` 到 baseline `resolve_memory_scope_ids`——可能把 worker 私有 fact 错误写入 PROJECT_SHARED 或 task.scope_id。

**修复**:
- `memory_tools.py:392-498` 改为**fail-closed**：未传 scope_id + agent_runtime_id 非空 → helper 解析失败立即返回 `SCOPE_UNRESOLVED rejected`，不降级
- 显式 scope_id 路径仍走 `resolve_memory_scope_ids` 白名单
- Legacy 路径（无 agent_runtime_id）走 baseline
- 错误信息显式标注 `F094 NFR-3 fail-closed`
- 单测 `test_f094_b3_resolve_worker_default_scope_id_helper` 已覆盖 raise 路径

### MED-2: MEMORY_RECALL_COMPLETED 新字段未实现 ✅ 接受 + 闭环

**Evidence**: B6 task 要求 recall payload 加 `agent_runtime_id / queried_namespace_kinds / hit_namespace_kinds`，但 staged diff 仅改 MEMORY_ENTRY_ADDED，未改 MemoryRecallCompletedPayload + emit 点。

**修复**:
- `packages/core/.../models/payloads.py:133` `MemoryRecallCompletedPayload` 加 3 字段（默认空，向后兼容）
- `task_service.py:1648+` MEMORY_RECALL_COMPLETED emit 路径派生：
  - `agent_runtime_id` 从 ContextFrame.agent_runtime_id
  - `queried_namespace_kinds` 从 ContextFrame.memory_namespace_ids 反查 namespaces 的 kind 集合
  - `hit_namespace_kinds` 从 recall.hits 的 metadata.namespace_kind 派生
- audit lookup 失败仅 log warning，不破坏 emit 主路径
- 单测 `test_f094_b6_memory_recall_completed_payload_carries_new_fields`

### MED-3: MEMORY_ENTRY_ADDED namespace 反查 race + 显式 PROJECT_SHARED 缺字段 ✅ 接受 + 闭环

**Evidence**: emit 路径只在 agent_runtime_id 非空时按 (project_id, agent_runtime_id) 反查 namespace。显式 PROJECT_SHARED 写入找不到（因 PROJECT_SHARED 通常不绑定 agent_runtime_id）；commit→emit 间 archive race 也会空字段。

**修复**:
- `memory_tools.py:392-540` scope 解析阶段就**captured** `namespace_id / namespace_kind`（避免 commit 后反查 race）
- emit 路径直接用 captured 变量，不再反查
- 显式 scope_id 路径在 captured 缺失时按 (project_id, scope_id contains) 二级查找——覆盖 PROJECT_SHARED 写入
- 失败时 captured 留空（degraded，不破坏写入主路径）

### MED-4: worker default 范围超 Phase B（main agent 也受影响）✅ 设计决策保留 + 文档化

**Evidence**: 当前实现只检查 agent_runtime_id 非空就走 worker default——main agent unscoped memory.write 也会进自己的 AGENT_PRIVATE namespace。

**决策**: 这是 spec 设计预期：spec §0 + §1 US1 acceptance 2 锁定"worker / main 统一用 AGENT_PRIVATE namespace"，包括 main agent 自己的私有 fact 也进 AGENT_PRIVATE。这个统一是 Codex spec HIGH-1 闭环的一部分。F094 不区分 worker / main role 在 memory.write 行为上——symmetric 处理是 H2 哲学一致性所需。

**文档化**: 在 commit message + spec §1 US1 / §3.1 块 B 描述里 explicit 标注此设计选择。后续 F107 (WorkerProfile 与 AgentProfile 完全合并) 会自然消除 worker / main 概念差异。

### MED-5: B7 测试覆盖不足 ✅ 接受 + 闭环

**Evidence**: B7 测试缺端到端 dispatch / memory.write commit + emit / main recall 不命中 worker AGENT_PRIVATE / multiple active raise 等场景。

**修复**: 新增 4 个测试（在原 3 个基础上补 4 个）：
- `test_f094_b7_main_agent_recall_does_not_hit_worker_agent_private`：main 视角下不命中 worker AGENT_PRIVATE（spec §1 US1 acceptance 3）
- `test_f094_b5_recall_priority_private_before_project_shared`：recall 优先级 AGENT_PRIVATE 排在 PROJECT_SHARED 前（spec §5 B3）
- `test_f094_b3_helper_raises_on_multiple_active_namespaces`：helper 在三元组 unique 失效时 raise（不 fallback）
- `test_f094_b6_memory_recall_completed_payload_carries_new_fields`：payload schema 含新字段

**B7 范围决策**：完整端到端 worker dispatch + memory.write commit + 实际 emit 验证属于 F087 e2e_live 范围（real LLM）；F094 单测层覆盖 store / helper / payload 层面已足以验证业务逻辑正确。

### LOW-6: 测试变量名残留 worker_private_* ✅ 接受 + 推迟到 F107

**Evidence**: bulk replace 后变量名仍叫 `worker_private_scope_ids`、测试名 `test_task_service_worker_private_writeback_*`，但 kind 已是 AGENT_PRIVATE。

**决策**: F094 范围内仅做 enum 替换，命名重整推迟到 F107 (WorkerProfile/AgentProfile 完全合并)——届时 worker / main 概念差异消失，可统一改为 `agent_private_scope_ids` / 测试名加 prefix。F094 已在 enum 定义注释 + commit message 标注此 LOW-6 ignored 决策。

## Codex 验证无 finding 项

- B0 metadata 接线主路径正确：task_service:879-882 注入 effective_agent_runtime_id；orchestrator/worker_runtime 分别从 envelope.metadata 读取
- multiple active raise 行为合理（不 fallback）
- F087 e2e_smoke 5 域不覆盖 worker dispatch；不会被 Phase B 影响
- `memory/private/main/runtime:<worker-runtime-id>` 不会让 main 误读（按 namespace 表 + agent_runtime_id 过滤）

## 闭环汇总

| Finding | 严重度 | 处理决议 | 落地章节 |
|---------|--------|----------|----------|
| HIGH-1 | HIGH | **接受** | memory.write fail-closed + 错误信息 explicit + 单测覆盖 |
| MED-2 | MED | **接受** | MemoryRecallCompletedPayload 加 3 字段 + emit 派生 + 单测 |
| MED-3 | MED | **接受** | scope 解析阶段 captured namespace 信息，emit 直接用，避免 race |
| MED-4 | MED | **设计决策保留** | spec §1 US1 锁定 worker / main 统一；commit message 文档化 |
| MED-5 | MED | **接受** | 4 个新增专项测试 |
| LOW-6 | LOW | **推迟到 F107** | 命名重整属于 F107 范围 |

## 全量回归验证

- packages/ + apps/gateway/tests（不含 e2e_live）: **3013 passed + 2 skipped + 1 xfailed + 1 xpassed**——0 regression vs Phase D 末（3006 → 3013 +7 Phase B 测试）
- F094 Phase B 专项测试: 7 个全 PASSED
  - test_f094_b3_resolve_worker_default_scope_id_helper（5 场景）
  - test_f094_b3_helper_raises_on_multiple_active_namespaces
  - test_f094_b5_recall_priority_private_before_project_shared
  - test_f094_b6_memory_recall_completed_payload_carries_new_fields
  - test_f094_b7_worker_dispatch_creates_agent_private_only
  - test_f094_b7_two_workers_agent_private_isolation
  - test_f094_b7_main_agent_recall_does_not_hit_worker_agent_private
- B7 grep 验收：worker dispatch 路径 0 命中 `kind=WORKER_PRIVATE` 写入 ✅

## Commit message 摘要

`Codex review (Phase B): 1 high / 4 medium 已处理（接受 4 修改 + 1 设计决策保留）/ 1 low 推迟（命名重整 F107） / 0 wait`
