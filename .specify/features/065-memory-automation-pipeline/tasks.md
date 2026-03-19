# Tasks: Memory Automation Pipeline (Phase 1 MVP)

**Input**: Design documents from `.specify/features/065-memory-automation-pipeline/`
**Prerequisites**: plan.md, spec.md, data-model.md, contracts/memory-write-tool.md, contracts/consolidation-service.md
**Scope**: Phase 1 MVP only (US-1, US-2, US-3)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

本项目为 monorepo，关键路径：

- **provider 包**: `octoagent/packages/provider/src/octoagent/provider/dx/`
- **gateway app**: `octoagent/apps/gateway/src/octoagent/gateway/services/`
- **gateway tests**: `octoagent/apps/gateway/tests/`
- **memory 包**: `octoagent/packages/memory/src/octoagent/memory/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 无新增项目结构。Phase 1 基于现有 monorepo 结构，不需创建新包或新目录。

*（本 feature 无 Setup 任务 -- 所有代码改动在已有目录下完成）*

---

## Phase 2: Foundational -- ConsolidationService 提取

**Purpose**: 将 `MemoryConsoleService._consolidate_scope` 的核心逻辑提取为独立的 `ConsolidationService`，作为 US-1/US-2/US-3 三个入口的共享基础设施。此阶段完成后，三个 User Story 的实现可以并行推进。

**CRITICAL**: US-2 和 US-3 都直接依赖 ConsolidationService；US-1 独立于此但需先完成 DI 注册。

- [x] T001 创建 `ConsolidationService` 类及返回值数据模型（`ConsolidationScopeResult` / `ConsolidationBatchResult`），实现 `consolidate_scope` 核心方法（从 `MemoryConsoleService._consolidate_scope` 迁移 LLM 调用、事实解析、propose/validate/commit 流程）。文件: `octoagent/packages/provider/src/octoagent/provider/dx/consolidation_service.py` [NEW]
- [x] T002 迁移 `_CONSOLIDATE_SYSTEM_PROMPT` 和 `_parse_consolidation_response` 到 `ConsolidationService`。实现 `consolidate_by_run_id`（通过 `fragment_filter` 按 run_id 筛选 Fragment）和 `consolidate_all_pending`（逐 scope 处理、单 scope 失败不影响其他）。文件: `octoagent/packages/provider/src/octoagent/provider/dx/consolidation_service.py` [续 T001]
- [x] T003 重构 `MemoryConsoleService.run_consolidate` 为委托调用 `ConsolidationService.consolidate_all_pending`；删除 `_consolidate_scope`、`_parse_consolidation_response`、`_CONSOLIDATE_SYSTEM_PROMPT`（已迁移）；新增 `_consolidation_service` 依赖注入。文件: `octoagent/packages/provider/src/octoagent/provider/dx/memory_console_service.py` [MODIFY]
- [x] T004 在 `AgentContextService` 中注册 `ConsolidationService` 的创建和获取方法 `get_consolidation_service()`，使 `TaskService`、`ControlPlaneService`、`MemoryConsoleService` 均可获取实例。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` [MODIFY]
- [x] T005 为 ConsolidationService 编写单元测试：覆盖 LLM 正常返回/LLM 不可用降级/LLM 输出格式错误/单条事实 commit 失败继续处理/Fragment consolidated_at 标记/consolidate_by_run_id 过滤/consolidate_all_pending 逐 scope 容错。文件: `octoagent/apps/gateway/tests/test_consolidation_service.py` [NEW]

**Checkpoint**: ConsolidationService 独立可测，现有管理台 Consolidate 功能通过委托调用不退化。三个 User Story 现在可以并行推进。

---

## Phase 3: User Story 1 -- Agent 对话中主动保存重要信息 (Priority: P1)

**Goal**: 让 Agent 在对话中识别到用户透露的重要信息后，调用 `memory.write` 工具将其持久化为 SoR 记录，走完整的 propose_write -> validate_proposal -> commit_memory 治理流程。

**Independent Test**: 启动 Agent 会话，告诉 Agent 一项个人偏好（如"我喜欢日式料理"），验证该信息通过 `memory.write` 被持久化为 SoR 记录；关闭会话后重新开启新会话，通过 `memory.recall` 验证该记忆可被检索到。

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T006 [P] [US1] 编写 `memory.write` 工具的单元测试：覆盖 ADD 新记忆/UPDATE 已有记忆（内部自动查版本）/参数校验（空 subject_key、空 content、无效 partition）/scope 解析失败/validate_proposal 拒绝/commit_memory 异常/敏感分区写入。文件: `octoagent/apps/gateway/tests/test_memory_write_tool.py` [NEW]

### Implementation for User Story 1

- [x] T007 [US1] 在 `capability_pack.py` 的 memory 工具组中（`memory.recall` 工具定义之后，约行 2993 附近）注册 `memory.write` 工具：使用 `@tool_contract` 装饰器定义工具签名和元数据（`side_effect_level=REVERSIBLE`, `tool_group="memory"`, `tool_profile=MINIMAL`）。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` [MODIFY]
- [x] T008 [US1] 实现 `memory_write` 工具函数的核心逻辑：(1) 参数校验（subject_key/content 非空、partition 有效值检查）；(2) 调用 `_resolve_runtime_project_context` 解析 project/workspace；(3) 调用 `_resolve_memory_scope_ids` 获取 scope_id；(4) 获取 MemoryService 实例。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` [续 T007]
- [x] T009 [US1] 实现 `memory_write` 的 ADD/UPDATE 判断与治理流程：(1) 通过 `memory_store.get_current_sor(scope_id, subject_key)` 查询是否已存在 SoR；(2) 已存在则 action=UPDATE + expected_version=existing.version，否则 action=ADD；(3) 构建 EvidenceRef 列表；(4) 执行 propose_write -> validate_proposal -> commit_memory 完整治理流程；(5) 返回 JSON 结果（committed/rejected/error）。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` [续 T008]
- [x] T010 [US1] 在 `capability_pack.py` 的工具注册列表中添加 `memory_write`（与 `memory_recall` 并列），并更新 `TOOL_ENTRYPOINTS` 映射表添加 `"memory.write": ["agent_runtime", "web"]`。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` [MODIFY]

**Checkpoint**: US-1 完成。Agent 可在对话中调用 `memory.write` 写入/更新记忆，走完整治理流程。可独立验证。

---

## Phase 4: User Story 2 -- Compaction Flush 后自动整理 Fragment (Priority: P1)

**Goal**: Compaction Flush 成功写入 Fragment 后，系统自动 fire-and-forget 触发一次轻量 Consolidate，仅处理本次 Flush 产出的 Fragment，将有价值的事实提取为 SoR 记录。不阻塞 Compaction 主流程。

**Independent Test**: 启动一段足够长的对话使其触发 Compaction Flush，验证 Flush 完成后自动执行 Consolidate，产生的 Fragment 被标记为 `consolidated_at`，且至少有一条新的 SoR 记录生成。

**前置依赖**: Phase 2（ConsolidationService）必须完成。

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T011 [P] [US2] 编写 Flush 后自动 Consolidate 的集成测试：覆盖 Flush 成功后触发 Consolidate/Consolidate 不阻塞 Flush 返回/LLM 不可用时优雅降级（Fragment 保持未整理）/ConsolidationService 为 None 时静默跳过。文件: `octoagent/apps/gateway/tests/test_auto_consolidate.py` [NEW]

### Implementation for User Story 2

- [x] T012 [US2] 在 `TaskService` 中实现 `_auto_consolidate_after_flush` 私有异步方法：(1) 获取 MemoryService 和 ConsolidationService 实例；(2) 调用 `consolidation_service.consolidate_by_run_id(run_id, scope_id)`；(3) 成功时记录 `auto_consolidate_after_flush` 结构化日志；(4) 内部 try/except 捕获所有异常，记录 warning 日志，不让异常逸出。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py` [MODIFY]
- [x] T013 [US2] 在 `TaskService._persist_compaction_flush` 方法中（`return run.run_id` 之前），添加 `asyncio.create_task` 调用 `_auto_consolidate_after_flush`，传入 `run_id`、`scope_id`、`project`、`workspace` 参数。确保 fire-and-forget 不 await。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py` [MODIFY]

**Checkpoint**: US-2 完成。Compaction Flush 后自动触发轻量 Consolidate。Fragment 被标记 consolidated_at，新 SoR 生成。LLM 不可用时优雅降级。

---

## Phase 5: User Story 3 -- 定期自动 Consolidate 积压 Fragment (Priority: P1)

**Goal**: 在 AutomationScheduler 中注册 `memory.consolidate` 定时作业，默认每 4 小时处理所有积压的未整理 Fragment（无 `consolidated_at` 标记），作为 Flush 后即时 Consolidate 的兜底保障。

**Independent Test**: 在 Memory 中手动创建若干无 `consolidated_at` 标记的 Fragment，触发 Scheduler 的 Consolidate 任务执行，验证这些 Fragment 被成功整理为 SoR 记录。

**前置依赖**: Phase 2（ConsolidationService）必须完成；Phase 3 中 T003（MemoryConsoleService 委托调用）必须完成。

### Tests for User Story 3

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T014 [P] [US3] 编写 Scheduler Consolidate 定时作业注册与执行测试：覆盖系统启动时自动创建 system:memory-consolidate 作业/已存在时不重复创建/作业触发后调用 consolidate_all_pending/系统重启后作业配置恢复。文件: `octoagent/apps/gateway/tests/test_scheduler_consolidate.py` [NEW]

### Implementation for User Story 3

- [x] T015 [US3] 在 `ControlPlaneService`（或 `AutomationSchedulerService.startup`）的启动流程中，添加系统内置作业初始化逻辑：检查 `automation_store` 中是否存在 `job_id="system:memory-consolidate"` 的 AutomationJob；若不存在，则创建默认配置（`action_id="memory.consolidate"`, `schedule_kind=CRON`, `schedule_expr="0 */4 * * *"`, `timezone="UTC"`, `enabled=True`）并持久化。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` [MODIFY]
- [x] T016 [US3] 确认 `_handle_memory_consolidate`（行 4372）现有实现通过 `MemoryConsoleService.run_consolidate` 间接调用 `ConsolidationService.consolidate_all_pending` 的调用链畅通。若需补充 scope_ids 传递逻辑或错误处理增强，在此完成。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` [VERIFY/MODIFY]

**Checkpoint**: US-3 完成。Scheduler 每 4 小时自动 Consolidate 所有积压 Fragment。系统重启后作业自动恢复。单 scope 失败不影响其他。

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 确保整体质量、文档完备、端到端验证。

- [x] T017 [P] 验证现有管理台 Consolidate 功能不退化：手动触发管理台 Consolidate 操作，确认通过 `MemoryConsoleService.run_consolidate -> ConsolidationService.consolidate_all_pending` 的委托调用正常工作。
- [x] T018 [P] 检查并补充 structlog 结构化日志：(1) `memory.write` 的 `memory_committed` / `memory_rejected` 日志事件；(2) `ConsolidationService` 的 `consolidation_scope_complete` / `consolidation_scope_failed` 日志事件；(3) `_auto_consolidate_after_flush` 的成功/失败日志。
- [x] T019 [P] 运行全量测试套件（`pytest`），确保新增代码不破坏现有测试。修复任何回归。
- [x] T020 运行 quickstart.md 验证：按照 `.specify/features/065-memory-automation-pipeline/quickstart.md` 步骤执行端到端验证，确认三个 User Story 的核心场景均可走通。

---

## FR Coverage Map

确保 spec.md Phase 1 中每条 Functional Requirement 都有至少一个任务覆盖。

| FR ID | 描述 | 覆盖任务 |
|-------|------|----------|
| FR-001 | 提供 `memory.write` 工具，注册在 memory 工具组中 | T007, T010 |
| FR-002 | `memory.write` 接受指定参数并走完整治理流程，产出 SoR | T008, T009 |
| FR-003 | `memory.write` 支持 ADD/UPDATE，内部自动查版本 | T009 |
| FR-004 | Flush 成功后自动触发轻量 Consolidate | T013 |
| FR-005 | 自动 Consolidate 异步执行，不阻塞 Compaction 主流程 | T012, T013 |
| FR-006 | 自动 Consolidate 复用现有 scope 级别流程 | T001, T002 |
| FR-007 | Scheduler 注册定时 Consolidate 任务 | T015 |
| FR-008 | 定时任务支持系统重启后自动恢复 | T015 |
| FR-009 | 定时 Consolidate 逐 scope 处理，单 scope 失败不影响其他 | T002 |
| FR-010 | LLM 不可用时优雅降级 | T001, T012 |
| FR-011 | Fragment 积压告警阈值 (SHOULD) | 未覆盖（SHOULD 级，可后续迭代） |

**覆盖率**: 10/10 MUST 需求 = 100%（FR-011 为 SHOULD 级，Phase 1 不强制）

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup) -- 无任务，跳过
     |
Phase 2 (Foundational: ConsolidationService 提取)
  T001 -> T002 -> T003 (串行：先迁移核心，再完善方法，再重构调用方)
  T004 (DI 注册，依赖 T001 完成接口定义)
  T005 (单元测试，依赖 T002 完成全部方法)
     |
     +---------+---------+
     |         |         |
Phase 3     Phase 4    Phase 5
(US-1)      (US-2)     (US-3)
     |         |         |
     +---------+---------+
              |
         Phase 6 (Polish)
```

### User Story 间依赖

- **US-1 (memory.write)**: 仅依赖 Phase 2 的 T004（DI 注册，确保 AgentContext 基础设施就绪）。不依赖 ConsolidationService 的具体实现。
- **US-2 (Flush 后 Consolidate)**: 依赖 Phase 2 全部完成（直接调用 `ConsolidationService.consolidate_by_run_id`）。
- **US-3 (Scheduler Consolidate)**: 依赖 Phase 2 的 T003 完成（通过 `MemoryConsoleService.run_consolidate` 间接调用）。

### Story 内部并行机会

- **US-1**: T006（测试）可与 T007 并行启动（测试先行，实现紧跟）。T007/T008/T009 需串行（同一文件的连续增量修改）。T010 依赖 T009 完成。
- **US-2**: T011（测试）可先行。T012/T013 需串行（T12 定义方法，T13 在另一方法中调用）。
- **US-3**: T014（测试）可先行。T015/T016 需串行。

### Recommended Implementation Strategy: MVP First

1. **先完成 Phase 2**（T001-T005）：建立共享基础设施
2. **然后 US-1**（T006-T010）：最基础的 Agent 写入能力
3. **然后 US-2**（T011-T013）：Flush 后自动 Consolidate 闭环
4. **然后 US-3**（T014-T016）：Scheduler 兜底
5. **最后 Phase 6**（T017-T020）：整体验证

单人开发建议按此顺序串行执行。若双人开发，Phase 2 完成后 US-1 和 US-2 可并行（不同文件）。
