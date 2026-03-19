# Tasks: Session 驱动统一记忆管线

**Feature**: 067-session-driven-memory-pipeline
**Input**: `.specify/features/067-session-driven-memory-pipeline/` (plan.md, spec.md, data-model.md, contracts/)
**Date**: 2026-03-19

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行执行（不同文件，无依赖）
- **[USN]**: 所属 User Story
- 每个任务包含精确文件路径

---

## Phase 1: Foundational -- Cursor 基础设施 (阻塞后续所有 Story)

**Purpose**: AgentSession 模型变更 + Store 方法扩展，是所有 US 的前置依赖

- [x] T001 [US3] 在 `AgentSession` 模型新增 `memory_cursor_seq: int = 0` 字段（含 `Field(default=0, ge=0)` 和 description）。文件: `octoagent/packages/core/src/octoagent/core/models/agent_context.py` [MODIFY]
- [x] T002 [US3] 在 `SqliteAgentContextStore` 新增 schema 迁移：`ALTER TABLE agent_sessions ADD COLUMN memory_cursor_seq INTEGER NOT NULL DEFAULT 0`。在 `_ensure_schema()` 或等效初始化方法中添加。文件: `octoagent/packages/core/src/octoagent/core/store/agent_context_store.py` [MODIFY]
- [x] T003 [US3] 在 `SqliteAgentContextStore` 新增 `list_turns_after_seq(agent_session_id: str, after_seq: int, limit: int = 200) -> list[AgentSessionTurn]` 方法，查询 `turn_seq > after_seq` 的 turns 并按 `turn_seq ASC` 排序。文件: `octoagent/packages/core/src/octoagent/core/store/agent_context_store.py` [MODIFY]
- [x] T004 [US3] 在 `SqliteAgentContextStore` 新增 `update_memory_cursor(agent_session_id: str, new_cursor_seq: int) -> None` 方法，`UPDATE agent_sessions SET memory_cursor_seq = ? WHERE agent_session_id = ?`。文件: `octoagent/packages/core/src/octoagent/core/store/agent_context_store.py` [MODIFY]
- [x] T005 [US3] 确保 `save_agent_session()` 和 `get_agent_session()` 方法正确读写 `memory_cursor_seq` 列（INSERT/SELECT 语句补充字段）。文件: `octoagent/packages/core/src/octoagent/core/store/agent_context_store.py` [MODIFY]
- [x] T006 [US3] 编写 Cursor 基础设施单元测试：覆盖 cursor 默认值 0、`list_turns_after_seq` 正确过滤、`update_memory_cursor` 正确更新、cursor 持久化到 SQLite 后重新读取一致。文件: `octoagent/apps/gateway/tests/test_session_memory_cursor.py` [NEW]

**Checkpoint**: Cursor 基础设施就绪，所有 Store 方法可用，后续 Phase 可开始

---

## Phase 2: US1 + US4 -- 核心提取服务 (Priority: P1 + P2)

**Goal**: 实现 SessionMemoryExtractor 核心类，每次 Agent 响应后自动从新增 turns 中提取记忆并写入 SoR。单次 LLM 调用提取四类记忆（facts/solutions/entities/ToM）。

**Independent Test**: 发送包含个人偏好的消息，Agent 响应后 Memory UI 自动出现对应 SoR 记录。

### 数据模型

- [x] T007 [P] [US1] 在新文件中定义 `ExtractionItem` dataclass（type/subject_key/content/confidence/action/partition + solution/entity/tom 特有字段）和 `SessionExtractionResult` dataclass（session_id/scope_id/turns_processed/counts/skipped_reason/errors）。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/session_memory_extractor.py` [NEW -- 数据模型部分]

### 核心服务

- [x] T008 [US1] 实现 `SessionMemoryExtractor.__init__()`：接收 `agent_context_store`、`memory_store`、`llm_service`、`project_root`；初始化 per-Session `asyncio.Lock` 字典 `_session_locks: dict[str, asyncio.Lock]`。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/session_memory_extractor.py` [NEW -- 续]
- [x] T009 [US1] 实现 `extract_and_commit()` 主方法骨架：(1) 检查 session.kind 白名单 (2) try-lock (3) 查询新增 turns (4) 无 turn 则跳过 (5) 推导 scope_id (6) 调用 `_build_extraction_input` (7) 调用 LLM (8) 解析 (9) commit (10) 创建溯源 Fragment (11) 更新 cursor。全部异常内部捕获，返回 `SessionExtractionResult`。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/session_memory_extractor.py` [续]
- [x] T010 [US1] 实现 `_build_extraction_input(turns: list[AgentSessionTurn]) -> str`：将 turns 格式化为文本，Tool Call 类型 turn 压缩为 `[Tool: {tool_name}] {summary}` 摘要格式，保留 role/content/summary 信息。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/session_memory_extractor.py` [续]
- [x] T011 [US4] 实现统一 LLM extraction prompt（system prompt + user prompt）：system prompt 描述四类记忆提取规则（facts/solutions/entities/ToM）和 JSON 输出格式；user prompt 填入格式化后的对话内容。使用 `fast` alias、temperature=0.3、max_tokens=4096。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/session_memory_extractor.py` [续]
- [x] T012 [US4] 实现 `_parse_extraction_output(raw_response: str) -> list[ExtractionItem]`：复用 `parse_llm_json_array` 解析 LLM 输出 JSON 数组为 `ExtractionItem` 列表，处理格式异常（非 JSON、截断输出）。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/session_memory_extractor.py` [续]
- [x] T013 [US1] 实现 `_commit_extractions(items: list[ExtractionItem], scope_id: str, ...) -> tuple[int, int, int, int]`：逐条通过 `propose_write → validate_proposal → commit_memory` 治理流程写入 SoR。按 type 分类统计 committed 数量。部分写入失败时记录 error 但继续处理其余条目。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/session_memory_extractor.py` [续]
- [x] T014 [US1] 实现 `_resolve_scope_id(agent_session, project, workspace) -> str | None`：复用 `_resolve_memory_namespace_by_kind` 和 `_select_writeback_scope` 现有逻辑推导目标 scope_id。如果无法推导返回 None。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/session_memory_extractor.py` [续]
- [x] T015 [US1] 实现 per-Session try-lock 语义：`_session_locks` 字典按 `agent_session_id` 键存储 `asyncio.Lock`；`extract_and_commit` 中使用 `lock.acquire()` 非阻塞尝试，失败即跳过。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/session_memory_extractor.py` [续]
- [x] T016 [US1] 添加结构化日志事件：`session_memory_extraction_started`、`session_memory_extraction_completed`、`session_memory_extraction_skipped`、`session_memory_extraction_llm_failed`、`session_memory_extraction_parse_failed`。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/session_memory_extractor.py` [续]

### 单元测试

- [x] T017 [P] [US1] 编写 SessionMemoryExtractor 单元测试：覆盖正常流程（mock LLM 返回有效 JSON -> SoR 写入 -> cursor 推进）、空结果（LLM 返回 [] -> 无写入 -> cursor 推进）、LLM 失败（静默跳过 -> cursor 不变）、解析失败（非法 JSON -> cursor 不变）、try-lock 跳过（并发调用第二次被跳过）、Subagent session 跳过。文件: `octoagent/apps/gateway/tests/test_session_memory_extractor.py` [NEW]

**Checkpoint**: SessionMemoryExtractor 核心逻辑完成，可独立单测验证

---

## Phase 3: US1 + US5 -- 触发点注入 + Fragment 溯源 (Priority: P1 + P2)

**Goal**: 在 `record_response_context()` 末尾注入 fire-and-forget 调用，并将提取产出的 Fragment 作为 SoR 溯源证据关联。

**Independent Test**: 完整对话后检查 SoR 自动产出，Fragment 包含 `evidence_for_sor_ids` 关联。

### 实现

- [x] T018 [US1] 在 `AgentContextService` 中创建 `SessionMemoryExtractor` 实例：在 `__init__` 或延迟初始化中构造，传入 `agent_context_store`、`memory_store`、`llm_service`、`project_root`。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` [MODIFY]
- [x] T019 [US1] 在 `record_response_context()` 末尾注入 fire-and-forget 调用：在 `_record_private_tool_evidence_writeback` 之后，添加 `asyncio.create_task(self._session_memory_extractor.extract_and_commit(...))`。仅当 `agent_session is not None` 时触发。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` [MODIFY]
- [x] T020 [US5] 在 `_commit_extractions` 成功后创建溯源 Fragment：调用 `run_memory_maintenance` 写入包含原始对话段落的 Fragment，metadata 中添加 `evidence_for_sor_ids: list[str]` 关联已提交的 SoR ID。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/session_memory_extractor.py` [MODIFY]

### 集成测试

- [x] T021 [US1] 编写集成测试：模拟完整对话流程 -> 自动触发 record_response_context -> 验证 SessionMemoryExtractor 被调用 -> 验证 SoR 和 Fragment 产出。文件: `octoagent/tests/integration/test_f067_session_memory_pipeline.py` [NEW]

**Checkpoint**: 统一管线端到端可用，每次 Agent 响应自动提取记忆

---

## Phase 4: US2 -- 废弃碎片化写入路径 (Priority: P1)

**Goal**: 移除 4 条旧记忆写入路径，统一由 Session 级管线处理。消除多路径并发写入导致的 Fragment 碎片化。

**Independent Test**: 完整对话 + Compaction 流程后，无旧路径产出（无 writeback Fragment、无 FlushPromptInjector 调用、无自动 Consolidation 触发）。

### Path 1: 删除 _record_memory_writeback

- [x] T022 [US2] 删除 `_record_memory_writeback` 方法整体（行 1927+），以及 `record_response_context` 中对该方法的调用（行 1252-1263 区域）。保留 `_record_private_tool_evidence_writeback` 不动。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` [MODIFY]

### Path 2: 删除 FlushPromptInjector 调用逻辑

- [x] T023 [US2] 在 `_persist_compaction_flush` 中删除 FlushPromptInjector 的调用逻辑（行 1866-1919 的 try 块）。保留原有的 `run_memory_maintenance(FLUSH)` 调用（压缩摘要 Fragment 仍有上下文恢复价值）。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py` [MODIFY]

### Path 3: 删除 FlushPromptInjector 文件

- [x] T024 [P] [US2] 删除 `flush_prompt_injector.py` 整个文件。文件: `octoagent/packages/provider/src/octoagent/provider/dx/flush_prompt_injector.py` [DELETE]
- [x] T025 [P] [US2] 删除 `test_flush_prompt_injector.py` 测试文件。文件: `octoagent/apps/gateway/tests/test_flush_prompt_injector.py` [DELETE]

### Path 4: 删除 _auto_consolidate_after_flush

- [x] T026 [US2] 删除 `_auto_consolidate_after_flush` 方法整体（行 1974+），以及 `_persist_compaction_flush` 中的 `asyncio.create_task(self._auto_consolidate_after_flush(...))` 调用（行 1953-1962 区域）。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py` [MODIFY]

### 清理引用

- [x] T027 [US2] 清理 `agent_context.py` 中的 `get_flush_prompt_injector()` 方法（行 2954-2970）及相关 `_flush_prompt_injector` 属性。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` [MODIFY]
- [x] T028 [US2] 清理 `provider/dx/__init__.py` 中 `FlushPromptInjector` 和 `FlushPromptResult` 的 `__all__` 导出和 `__getattr__` 延迟导入逻辑。文件: `octoagent/packages/provider/src/octoagent/provider/dx/__init__.py` [MODIFY]
- [x] T029 [US2] 全局 grep 验证：确认 `FlushPromptInjector`、`_record_memory_writeback`、`_auto_consolidate_after_flush` 在 `octoagent/` 源码目录下无任何残留引用（spec/docs 目录除外）。文件: 无文件变更，验证步骤

**Checkpoint**: 4 条旧路径完全移除，记忆写入路径收敛为统一管线 + memory.write

---

## Phase 5: US3 -- Cursor 增量处理与崩溃恢复验证 (Priority: P1)

**Goal**: 验证 Memory Cursor 机制在各种场景下的正确性——增量处理、崩溃恢复、cursor-SoR 写入原子性。

**Independent Test**: 模拟进程中断（LLM 调用后 cursor 更新前 kill），重启后验证相同 turn 被重新处理。

- [ ] T030 [US3] 编写增量处理测试：Session 中先处理 5 个 turn（cursor=5），新增 turn 6-8 后触发提取，验证只处理 turn 6-8 且 cursor 更新为 8。文件: `octoagent/apps/gateway/tests/test_session_memory_extractor.py` [MODIFY -- 新增测试用例]
- [ ] T031 [US3] 编写崩溃恢复测试：mock LLM 成功但在 cursor 更新前模拟异常，验证 cursor 保持崩溃前的值；下一次提取重新包含未确认的 turns。文件: `octoagent/apps/gateway/tests/test_session_memory_extractor.py` [MODIFY -- 新增测试用例]
- [ ] T032 [US3] 编写首次提取测试：新 Session（cursor=0）发送第一条消息，验证处理所有 turns 并更新 cursor。文件: `octoagent/apps/gateway/tests/test_session_memory_extractor.py` [MODIFY -- 新增测试用例]
- [ ] T033 [US3] 编写空 Session / 无新 turn 测试：cursor 等于最新 turn_seq 时，验证直接返回 skipped_reason="no_new_turns"，不调用 LLM。文件: `octoagent/apps/gateway/tests/test_session_memory_extractor.py` [MODIFY -- 新增测试用例]

**Checkpoint**: Cursor 机制全面验证，增量处理和崩溃恢复行为符合 spec

---

## Phase 6: US6 -- 兜底与手动通道验证 (Priority: P3)

**Goal**: 验证 Scheduler Consolidation、管理台手动 Consolidation、`memory.write` 工具在统一管线上线后仍正常工作。

**Independent Test**: 禁用统一管线触发点，发送对话后手动触发 Consolidation，验证 Scheduler 能处理遗留 Fragment。

- [ ] T034 [US6] 编写 Scheduler Consolidation 兜底测试：模拟统一管线 LLM 不可用跳过数次提取后，手动调用 `consolidate_all_pending`，验证积累的 Fragment 被正常整合。文件: `octoagent/tests/integration/test_f067_session_memory_pipeline.py` [MODIFY -- 新增测试用例]
- [ ] T035 [US6] 编写 `memory.write` 工具保留测试：验证 Agent 对话中调用 `memory.write` 工具后，记忆通过 propose-validate-commit 正常写入 SoR。文件: `octoagent/tests/integration/test_f067_session_memory_pipeline.py` [MODIFY -- 新增测试用例]
- [ ] T036 [US6] 验证管理台手动 Consolidation 入口正常：`MemoryConsoleService.run_consolidate()` 在废弃旧路径后仍可正常调用。文件: `octoagent/tests/integration/test_f067_session_memory_pipeline.py` [MODIFY -- 新增测试用例]

**Checkpoint**: 所有保留通道正常运行，系统韧性保障到位

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 全局验证、文档、清理

- [ ] T037 [P] 端到端集成测试：多种 Session Kind（BUTLER_MAIN、WORKER_INTERNAL、DIRECT_WORKER 触发；SUBAGENT_INTERNAL 不触发）。文件: `octoagent/tests/integration/test_f067_session_memory_pipeline.py` [MODIFY -- 新增测试用例]
- [ ] T038 [P] 端到端集成测试：并发防护验证——同一 Session 快速连续两次触发，验证 try-lock 跳过第二次。文件: `octoagent/tests/integration/test_f067_session_memory_pipeline.py` [MODIFY -- 新增测试用例]
- [ ] T039 [P] 运行 quickstart.md 验证：按 `.specify/features/067-session-driven-memory-pipeline/quickstart.md` 步骤执行完整验证流程。文件: 无文件变更，验证步骤
- [ ] T040 全局代码清理：检查废弃路径的所有 import 语句是否已清除，确保无未使用的 import 残留。文件: 多文件检查

---

## FR 覆盖映射表

| FR | 描述 | 覆盖任务 |
|----|------|---------|
| FR-001 | Agent 响应完成后自动触发记忆提取管线 | T019 |
| FR-002 | fire-and-forget 异步执行 | T019 |
| FR-003 | AgentSession 持久化 memory_cursor_seq | T001, T002, T005 |
| FR-004 | 只读取 cursor 之后的新增 turn | T003, T009, T030 |
| FR-005 | SoR 写入成功后才更新 cursor | T009, T013, T031 |
| FR-006 | Tool Call 压缩为摘要格式 | T010 |
| FR-007 | 单次 LLM 调用提取四类记忆 | T011 |
| FR-008 | 无值得记忆时返回空结果 | T012, T017 |
| FR-009 | 移除"响应后自动写入通用记忆 Fragment" | T022 |
| FR-010 | 移除"Compaction 碎片化写入 Fragment" | T023 |
| FR-011 | 移除"Compaction 后注入静默记忆提取 turn" | T024, T025 |
| FR-012 | 移除"Fragment 写入后自动触发 Consolidation" | T026 |
| FR-013 | 保留 memory.write 工具 | T035 |
| FR-014 | 保留 Scheduler 定期 Consolidation | T034 |
| FR-015 | 保留管理台手动 Consolidation | T036 |
| FR-016 | 提取产出通过 propose-validate-commit 写入 SoR | T013 |
| FR-017 | Fragment 记录溯源证据并关联 SoR | T020 |
| FR-018 | 全自动运行无需审批 | T009, T019 |
| FR-019 | LLM 不可用时静默跳过 | T009, T016, T017 |
| FR-020 | LLM 按语义边界自行决定粒度 | T011 |
| FR-021 | 使用 fast alias | T011 |
| FR-022 | per-Session 互斥 try-lock | T015, T038 |
| FR-023 | 仅 BUTLER_MAIN/WORKER_INTERNAL/DIRECT_WORKER 触发 | T009, T037 |
| FR-024 | 从 AgentSession 推导 scope_id | T014 |

**FR 覆盖率**: 24/24 = 100%

---

## 依赖与并行说明

### Phase 依赖关系

```
Phase 1 (Foundational)
    |
    v
Phase 2 (核心提取服务) ──────> Phase 3 (触发点注入)
                                    |
                                    v
                              Phase 4 (废弃旧路径)
                                    |
                                    v
                         Phase 5 (Cursor 验证) + Phase 6 (兜底验证)  [可并行]
                                    |
                                    v
                              Phase 7 (Polish)
```

### User Story 间依赖

- **US3 (Cursor)**: Phase 1 独立完成基础设施，Phase 5 验证依赖 Phase 2+3 的核心服务
- **US1 (自动提取) + US4 (LLM 提取)**: 合并在 Phase 2 实现（US4 是 US1 的内部子集）
- **US5 (Fragment 溯源)**: Phase 3 与 US1 触发点注入同步实现
- **US2 (废弃旧路径)**: Phase 4 必须在 Phase 3 之后（新路径就绪后才能删旧路径）
- **US6 (兜底通道)**: Phase 6 独立于 Phase 5，两者可并行

### Story 内部并行机会

- Phase 1: T001 和 T002-T005 有依赖（先改模型再改 Store），T006 依赖前面所有
- Phase 2: T007 可与 T008 并行（数据模型独立定义），T009-T016 串行，T017 依赖 T009-T016
- Phase 4: T022-T029 中 T024+T025 可并行（删文件），其余按依赖串行
- Phase 5 和 Phase 6 可完全并行执行

### 推荐实现策略

**Incremental Delivery（增量交付）**:

1. Phase 1 -> 基础设施就绪
2. Phase 2 + Phase 3 -> 统一管线端到端可用（**MVP 交付点**）
3. Phase 4 -> 旧路径清除，路径收敛完成
4. Phase 5 + Phase 6（并行） -> 全面验证
5. Phase 7 -> 收尾

MVP 范围为 Phase 1-3（US1 自动提取 + US3 Cursor + US4 LLM 提取 + US5 Fragment 溯源），交付后系统即具备"每轮对话自动沉淀知识"的核心能力。Phase 4 (US2 废弃旧路径) 紧随其后，消除双写风险。
