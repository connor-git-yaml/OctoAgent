# Tasks: Memory Automation Pipeline (Phase 2 Quality Improvement)

**Input**: Design documents from `.specify/features/065-memory-automation-pipeline/`
**Prerequisites**: plan-phase2.md, spec.md, data-model-phase2.md, contracts/derived-extraction.md, contracts/model-reranker.md
**Scope**: Phase 2 only (US-4, US-5, US-6)
**Phase 1 Status**: Completed and merged (ConsolidationService, memory.write, Scheduler -- T001-T020 all done)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US4, US5, US6)
- Include exact file paths in descriptions

## Path Conventions

本项目为 monorepo，关键路径：

- **provider 包**: `octoagent/packages/provider/src/octoagent/provider/dx/`
- **gateway app**: `octoagent/apps/gateway/src/octoagent/gateway/services/`
- **gateway tests**: `octoagent/apps/gateway/tests/`
- **memory 包**: `octoagent/packages/memory/src/octoagent/memory/`
- **memory models**: `octoagent/packages/memory/src/octoagent/memory/models/`
- **memory store**: `octoagent/packages/memory/src/octoagent/memory/store/`
- **memory tests**: `octoagent/packages/memory/tests/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Phase 2 不需要创建新项目结构或新包。所有代码改动在已有目录下完成。Phase 1 已建立的 ConsolidationService、memory.write、Scheduler 基础设施直接复用。

*（无 Setup 任务）*

---

## Phase 2: Foundational -- 数据模型与存储层扩展

**Purpose**: Phase 2 三个 User Story 共享的数据模型扩展和存储层方法。此阶段完成后，三个 Story 可以并行推进。

**CRITICAL**: US-4 依赖 `upsert_derived_records` 写入方法和 `CommittedSorInfo` 数据类；US-6 依赖 `MemoryRecallRerankMode.MODEL` 枚举值。

- [x] T101 创建 `CommittedSorInfo` 和 `DerivedExtractionResult` 数据类。在 `consolidation_service.py` 文件顶部（现有 `ConsolidationScopeResult` 定义之后）添加两个 `@dataclass(slots=True)` 定义。同时为 `ConsolidationScopeResult` 新增 `derived_extracted: int = 0` 字段。文件: `octoagent/packages/provider/src/octoagent/provider/dx/consolidation_service.py` [MODIFY]
- [x] T102 [P] 为 `MemoryRecallRerankMode` 枚举新增 `MODEL = "model"` 值。文件: `octoagent/packages/memory/src/octoagent/memory/models/integration.py` [MODIFY]
- [x] T103 [P] 在 `SqliteMemoryStore` 中新增 `upsert_derived_records(scope_id, records)` 方法：接受 `scope_id: str` 和 `records: list[DerivedMemoryRecord]`，逐条 INSERT OR REPLACE 写入 `derived_memory` 表，返回成功写入数。复用已有的 `_conn` 和 derived_memory 表 schema。文件: `octoagent/packages/memory/src/octoagent/memory/store/memory_store.py` [MODIFY]

**Checkpoint**: 数据模型和存储层就绪。三个 User Story 现在可以并行推进。

---

## Phase 3: User Story 4 -- Consolidate 后自动提取 Derived Memory (Priority: P2)

**Goal**: 每次 Consolidate 成功产出新的 SoR 记录后，系统自动通过 LLM 从这些 SoR 中提取 entity/relation/category 类型的 DerivedMemoryRecord，写入 SQLite derived_memory 表。提取为 best-effort，失败不影响 SoR 写入。

**Independent Test**: 通过 Consolidate 生成若干 SoR 记录，验证系统自动提取出对应的 DerivedMemoryRecord，类型涵盖 entity/relation/category，并关联到源 SoR。

**前置依赖**: T101（CommittedSorInfo / DerivedExtractionResult 数据类）、T103（upsert_derived_records 写入方法）

### Tests for User Story 4

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T104 [P] [US4] 编写 `DerivedExtractionService` 单元测试：覆盖 (1) LLM 正常返回 JSON 数组时正确解析并写入 derived 记录；(2) LLM 不可用时返回 errors 列表且不抛异常；(3) LLM 输出格式错误（非 JSON / 缺字段）时返回 errors；(4) committed_sors 为空时返回 extracted=0；(5) 部分 derived 写入失败时已成功的保留，失败的记入 errors；(6) derived_id 格式验证（`derived:consolidate:{scope_id}:{ts}:{index}:{type}`）。文件: `octoagent/apps/gateway/tests/test_derived_extraction.py` [NEW]

### Implementation for User Story 4

- [x] T105 [US4] 创建 `DerivedExtractionService` 类：(1) 构造函数接受 `memory_store: SqliteMemoryStore`、`llm_service: LlmServiceProtocol | None`、`project_root: Path`；(2) 定义 `_EXTRACTION_SYSTEM_PROMPT`（指导 LLM 从 SoR 中提取 entity/relation/category，输出 JSON 数组）；(3) 定义 `_EXTRACTION_USER_PROMPT_TEMPLATE`（将 committed_sors 列表格式化为 `[mem-id] (partition) subject_key: content` 格式）。文件: `octoagent/packages/provider/src/octoagent/provider/dx/derived_extraction_service.py` [NEW]
- [x] T106 [US4] 实现 `DerivedExtractionService.extract_from_sors` 方法核心逻辑：(1) 检查 committed_sors 是否为空，空则直接返回；(2) 检查 llm_service 是否可用，不可用则记入 errors 返回；(3) 构建 prompt 拼接所有 SoR 内容；(4) 调用 LLM 获取 JSON 输出；(5) 解析 JSON 数组为 derived 记录列表。所有异常内部捕获，记入 result.errors，不抛出。文件: `octoagent/packages/provider/src/octoagent/provider/dx/derived_extraction_service.py` [续 T105]
- [x] T107 [US4] 实现 `DerivedExtractionService` 的 derived 记录构建与写入：(1) 将 LLM 输出的每个 JSON 对象转为 `DerivedMemoryRecord`（设置 derived_id、scope_id、partition、derived_type、subject_key、summary、payload、confidence、source_fragment_refs）；(2) 调用 `memory_store.upsert_derived_records(scope_id, records)` 写入；(3) 记录 `derived_extraction_complete` 结构化日志；(4) 返回 `DerivedExtractionResult`。文件: `octoagent/packages/provider/src/octoagent/provider/dx/derived_extraction_service.py` [续 T106]
- [x] T108 [US4] 修改 `ConsolidationService`，添加 Derived 提取 hook：(1) 构造函数新增 `derived_extraction_service: DerivedExtractionService | None = None` 可选参数；(2) 在 `consolidate_scope()` 方法中，SoR commit 循环完成后收集 `committed_sors: list[CommittedSorInfo]`；(3) 若 `_derived_service` 非 None 且 `committed_sors` 非空，best-effort 调用 `extract_from_sors`；(4) 整个 derived 块在 try/except 中，任何异常只记 warning 日志，不影响 consolidate 返回值；(5) 将 derived_result.extracted 写入 ConsolidationScopeResult.derived_extracted。文件: `octoagent/packages/provider/src/octoagent/provider/dx/consolidation_service.py` [MODIFY]
- [x] T109 [US4] 在 `AgentContextService` 中注册 `DerivedExtractionService` 的创建：(1) 新增 `get_derived_extraction_service()` 方法；(2) 修改现有的 `get_consolidation_service()` 方法，在创建 ConsolidationService 时传入 `derived_extraction_service` 参数。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` [MODIFY]

**Checkpoint**: US-4 完成。Consolidate 每次成功 commit SoR 后自动提取 Derived Memory。LLM 不可用时优雅降级（SoR 不受影响）。可独立验证。

---

## Phase 4: User Story 5 -- Memory Flush Prompt 优化（静默 Agentic Turn）(Priority: P2)

**Goal**: 在 Compaction 触发前注入一次静默 LLM 调用，让模型审视当前对话并决定哪些信息值得持久化，通过 `memory.write` 工具走完整治理流程写入 SoR。替代当前的全文摘要式 Flush 产出的低质量 Fragment。对话无有价值信息时可跳过。Flush Prompt 失败时降级到原有全文摘要 Flush。

**Independent Test**: 对同一段对话，分别用原始 Flush 和优化后的 Flush Prompt 生成产出物，验证 Flush Prompt 产出的 SoR 信息密度更高，过程性讨论被过滤。

**前置依赖**: 无 Foundational 依赖（复用 Phase 1 已有的 memory.write 工具）

### Tests for User Story 5

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T110 [P] [US5] 编写 `FlushPromptInjector` 单元测试：覆盖 (1) LLM 正常返回 JSON 数组时逐条调用 memory_write_fn 并统计 writes_committed；(2) LLM 返回空数组（无需保存）时 skipped=True 且 writes_attempted=0；(3) LLM 不可用时返回 fallback_to_summary=True 且不抛异常；(4) LLM 输出格式错误时返回 errors 和 fallback_to_summary=True；(5) memory_write_fn 部分调用失败时 writes_committed < writes_attempted 且 errors 记录失败原因；(6) conversation_messages 为空时直接返回 skipped=True。文件: `octoagent/apps/gateway/tests/test_flush_prompt_injector.py` [NEW]

### Implementation for User Story 5

- [x] T111 [US5] 创建 `FlushPromptInjector` 类：(1) 构造函数接受 `llm_service: LlmServiceProtocol | None` 和 `project_root: Path`；(2) 定义 `_FLUSH_SYSTEM_PROMPT`（指导 LLM 审视对话、按判断标准选择值得保存的信息、输出 JSON 数组格式 `[{"subject_key", "content", "partition"}]`）；(3) 定义 `_FLUSH_USER_PROMPT_TEMPLATE`（将 conversation_messages 格式化为对话摘要供 LLM 审视）；(4) 定义 `FlushPromptResult` 数据类（如果 T101 未包含，在此文件顶部定义）。文件: `octoagent/packages/provider/src/octoagent/provider/dx/flush_prompt_injector.py` [NEW]
- [x] T112 [US5] 实现 `FlushPromptInjector.run_flush_turn` 方法核心逻辑：(1) 检查 conversation_messages 是否为空，空则返回 skipped；(2) 检查 llm_service 是否可用，不可用则返回 fallback_to_summary；(3) 构建 system + user prompt；(4) 调用 LLM 获取 JSON 输出；(5) 解析 JSON 数组为记忆写入列表；(6) 逐条调用 `memory_write_fn(subject_key, content, partition, evidence_refs)` 走完整治理流程；(7) 统计 writes_attempted / writes_committed / errors；(8) 所有异常内部捕获，不抛出。文件: `octoagent/packages/provider/src/octoagent/provider/dx/flush_prompt_injector.py` [续 T111]
- [x] T113 [US5] 修改 `TaskService._persist_compaction_flush` 方法，在现有 Flush 逻辑之前注入 Flush Prompt：(1) 通过 `self._agent_context.get_flush_prompt_injector()` 获取注入器；(2) 若注入器可用，调用 `run_flush_turn()` 传入 conversation_messages、scope_id、memory_write_fn；(3) 记录 `flush_prompt_completed` 结构化日志（writes, skipped, fallback）；(4) 整个 Flush Prompt 块在 try/except 中，失败时记 warning 日志；(5) 无论成功/失败/跳过，继续执行原有 Flush 流程不变。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py` [MODIFY]
- [x] T114 [US5] 在 `AgentContextService` 中注册 `FlushPromptInjector` 的创建和获取：新增 `get_flush_prompt_injector()` 方法，创建并缓存 FlushPromptInjector 实例。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` [MODIFY]

**Checkpoint**: US-5 完成。Compaction 前注入静默 agentic turn，LLM 主动选择性保存关键信息为 SoR。LLM 不可用时降级到原有全文摘要 Flush。对话无值得保存信息时跳过。可独立验证。

---

## Phase 5: User Story 6 -- Retrieval 增加 Reranker 精排 (Priority: P2)

**Goal**: 在 `MemoryService.recall_memory` 的 rerank 环节新增 `MODEL` 模式，接入 Qwen3-Reranker-0.6B 本地 cross-encoder 模型做精排。模型不可用时自动降级到现有 HEURISTIC 模式。候选结果 < 2 条时跳过 rerank。

**Independent Test**: 构造一组已知答案的查询-记忆对，分别用 HEURISTIC 和 MODEL 模式运行 recall，对比 Top-K 命中率和排序质量。

**前置依赖**: T102（MemoryRecallRerankMode.MODEL 枚举值）

### Tests for User Story 6

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T115 [P] [US6] 编写 `ModelRerankerService` 单元测试：覆盖 (1) 模型正常加载后 is_available=True；(2) rerank 返回与 candidates 一一对应的 scores 且非降级；(3) candidates < 2 时返回 degraded=True、reason 包含 "candidates < 2"；(4) 模型未加载时返回 degraded=True；(5) 推理异常时返回 degraded=True、reason 包含错误信息；(6) 模型加载失败时 is_available=False。文件: `octoagent/apps/gateway/tests/test_model_reranker.py` [NEW]
- [x] T116 [P] [US6] 编写 `MemoryService._apply_recall_hooks` 的 MODEL rerank 集成测试：覆盖 (1) rerank_mode=MODEL + reranker 可用时按 scores 重排候选；(2) rerank_mode=MODEL + reranker 降级时回退到 HEURISTIC；(3) rerank_mode=MODEL + reranker 为 None 时回退到 HEURISTIC；(4) rerank_mode=MODEL + candidates < 2 时跳过 rerank；(5) hit.metadata 中包含 recall_rerank_score / recall_rerank_mode / recall_rerank_model。文件: `octoagent/packages/memory/tests/test_recall_rerank_model.py` [NEW]

### Implementation for User Story 6

- [x] T117 [US6] 创建 `ModelRerankerService` 类：(1) 定义类常量 `_RERANKER_MODEL_ID = "Qwen/Qwen3-Reranker-0.6B"`、`_MIN_CANDIDATES_FOR_RERANK = 2`、`_RERANK_INSTRUCTION`；(2) 构造函数接受 `auto_load: bool = True`，初始化 `_model`、`_model_loaded`、`_load_attempted`、`_load_error` 状态字段；(3) 实现 `is_available` property；(4) 实现 `_schedule_warmup` 方法（创建后台 asyncio.Task 调用 `_warmup_model`）。文件: `octoagent/packages/provider/src/octoagent/provider/dx/model_reranker_service.py` [NEW]
- [x] T118 [US6] 实现 `ModelRerankerService._warmup_model` 方法：(1) 延迟导入 `from sentence_transformers import CrossEncoder`；(2) 在 `asyncio.to_thread` 中加载模型（`CrossEncoder(model_id, trust_remote_code=True, device="cpu")`）；(3) 成功时设 `_model_loaded=True`，记录 `model_reranker_ready` 日志；(4) 失败时设 `_load_error`，记录 `model_reranker_warmup_failed` 日志；(5) 设置 60 秒退避避免频繁重试。文件: `octoagent/packages/provider/src/octoagent/provider/dx/model_reranker_service.py` [续 T117]
- [x] T119 [US6] 实现 `ModelRerankerService.rerank` 方法：(1) candidates < MIN 时返回 degraded（"candidates < 2, skipped"）；(2) 模型不可用时返回 degraded（load_error 或 "not loaded"）；(3) 构建 `[{"query": query, "passage": c}]` pairs；(4) 在 `asyncio.to_thread` 中调用 `self._model.predict(pairs)`；(5) 转换 scores 为 `list[float]`；(6) 推理异常时返回 degraded。文件: `octoagent/packages/provider/src/octoagent/provider/dx/model_reranker_service.py` [续 T118]
- [x] T120 [US6] 修改 `MemoryService._apply_recall_hooks` 方法，新增 MODEL rerank 分支：(1) MemoryService 构造函数新增 `reranker_service: ModelRerankerService | None = None` 参数；(2) 在现有 `HEURISTIC` 分支后添加 `elif rerank_mode is MODEL` 分支；(3) candidates >= 2 且 reranker 可用时调用 `reranker.rerank(query, candidate_texts)` 并按 scores 重排；(4) degraded 时降级到 `_rerank_recall_candidates`（HEURISTIC）；(5) 为每个 reranked candidate 注入 metadata（recall_rerank_score / recall_rerank_mode / recall_rerank_model）。文件: `octoagent/packages/memory/src/octoagent/memory/service.py` [MODIFY]
- [x] T121 [US6] 在 `AgentContextService` 中注册 `ModelRerankerService` 的创建和获取：(1) 新增 `get_reranker_service()` 方法，创建 `ModelRerankerService(auto_load=True)` 单例并后台 warmup；(2) 修改现有 `MemoryService` 创建逻辑，注入 `reranker_service` 参数。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` [MODIFY]

**Checkpoint**: US-6 完成。recall 查询可使用 MODEL reranker 精排。模型不可用时降级到 HEURISTIC。candidates < 2 时跳过。默认 rerank_mode 保持 HEURISTIC，用户可手动切换到 MODEL。可独立验证。

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 确保整体质量、依赖管理、文档完备、端到端验证。

- [x] T122 [P] 验证 `sentence-transformers` 依赖在 `pyproject.toml` 中已声明（Phase 1 embedding 已引入，确认 CrossEncoder 所需版本兼容）。若需升级或添加额外依赖，在此完成。文件: `octoagent/packages/provider/pyproject.toml` [VERIFY/MODIFY]
- [x] T123 [P] 更新 `provider/dx/__init__.py`，导出新增的三个服务类（DerivedExtractionService、FlushPromptInjector、ModelRerankerService）和相关数据类（CommittedSorInfo、DerivedExtractionResult、FlushPromptResult、RerankResult）。文件: `octoagent/packages/provider/src/octoagent/provider/dx/__init__.py` [MODIFY]
- [x] T124 [P] 检查并补充 structlog 结构化日志：(1) DerivedExtractionService 的 `derived_extraction_complete` / `derived_extraction_failed` 日志事件；(2) FlushPromptInjector 的 `flush_prompt_completed` / `flush_prompt_failed` 日志事件；(3) ModelRerankerService 的 `model_reranker_ready` / `model_reranker_warmup_failed` / `model_reranker_degraded` 日志事件。
- [x] T125 [P] 验证现有管理台 Consolidate 功能不退化：手动触发 Consolidate 操作，确认通过 ConsolidationService 的调用链仍然正常工作，derived 提取 hook 正常触发（或在 DerivedExtractionService 为 None 时跳过）。
- [x] T126 [P] 运行全量测试套件（`pytest`），确保 Phase 2 新增代码不破坏现有测试。修复任何回归。
- [x] T127 运行 quickstart-phase2.md 验证：按照 `.specify/features/065-memory-automation-pipeline/quickstart-phase2.md` 步骤执行端到端验证，确认三个 User Story 的核心场景均可走通。

---

## FR Coverage Map

确保 spec.md Phase 2 中每条 Functional Requirement 都有至少一个任务覆盖。

| FR ID | 描述 | 级别 | 覆盖任务 |
|-------|------|------|----------|
| FR-012 | Consolidate 成功后自动提取 Derived Memory | SHOULD | T105, T106, T107, T108 |
| FR-013 | Derived 提取 best-effort，失败不影响 SoR | SHOULD | T106, T108 |
| FR-014 | Compaction 前注入静默 agentic turn | SHOULD | T111, T112, T113 |
| FR-015 | 无价值信息时跳过或产出极简 Fragment | SHOULD | T112 |
| FR-016 | 支持 Reranker 精排模式，接入本地 Reranker | SHOULD | T117, T118, T119, T120 |
| FR-017 | Reranker 降级：模型不可用时回退 HEURISTIC | MUST | T119, T120 |
| FR-018 | recall 候选 < 2 条时跳过 Reranker | SHOULD | T119, T120 |

**覆盖率**: 7/7 Phase 2 需求 = 100%（1 个 MUST + 6 个 SHOULD 全覆盖）

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup) -- 无任务，跳过
     |
Phase 2 (Foundational: 数据模型与存储层扩展)
  T101 (CommittedSorInfo + ConsolidationScopeResult 扩展)
  T102 (MemoryRecallRerankMode.MODEL) -- [P] 与 T101/T103 并行
  T103 (upsert_derived_records) -- [P] 与 T101/T102 并行
     |
     +---------+---------+
     |         |         |
Phase 3     Phase 4    Phase 5
(US-4)      (US-5)     (US-6)
     |         |         |
     +---------+---------+
              |
         Phase 6 (Polish)
```

### User Story 间依赖

- **US-4 (Derived 提取)**: 依赖 T101（数据类）和 T103（derived 写入方法）。不依赖 US-5 或 US-6。
- **US-5 (Flush Prompt)**: 仅依赖 Phase 1 已有的 memory.write 工具。不依赖 Foundational 阶段任何任务。理论上可最早开始，但建议在 Foundational 之后以保持一致性。
- **US-6 (Reranker)**: 依赖 T102（MODEL 枚举值）。不依赖 US-4 或 US-5。

### Story 内部并行机会

- **US-4**: T104（测试）可与 T105 并行启动（测试先行）。T105/T106/T107 需串行（同一文件的连续增量修改）。T108 依赖 T107 完成（需确认 DerivedExtractionService 接口稳定）。T109 依赖 T108（需知 DerivedExtractionService 构造签名）。
- **US-5**: T110（测试）可先行。T111/T112 需串行（同一文件）。T113 和 T114 可并行（不同文件：task_service.py vs agent_context.py），但 T113 依赖 T114 的方法存在后，建议 T114 先于 T113。
- **US-6**: T115/T116（测试）可并行启动，且与 T117 并行。T117/T118/T119 需串行（同一文件）。T120 依赖 T119（需确认 rerank 接口）。T121 依赖 T120（需知 MemoryService 注入签名）。

### Recommended Implementation Strategy: Incremental Delivery

1. **先完成 Phase 2**（T101-T103）：3 个任务全部可并行，数据模型就绪
2. **然后 US-5**（T110-T114）：Foundational 依赖最少，且直接提升 Flush 质量
3. **然后 US-4**（T104-T109）：依赖 T101/T103，与 Consolidate 集成
4. **然后 US-6**（T115-T121）：依赖 T102，需要模型下载验证
5. **最后 Phase 6**（T122-T127）：整体验证

单人开发建议按此顺序串行执行。US-4 和 US-5 在 Foundational 完成后可完全并行（不同文件）。US-6 也可与 US-4/US-5 并行（不同文件）。

---

## Notes

- [P] 标记 = 不同文件、无依赖，可并行
- [USN] 标记 = 映射到对应 User Story，便于追踪
- Phase 2 任务编号从 T101 起，与 Phase 1 (T001-T020) 不冲突
- 每个 Story 的测试任务（T104/T110/T115/T116）应先行编写并确认失败
- 提交粒度建议：每完成一个任务或逻辑组即 commit
- 在任意 Checkpoint 处可暂停验证 Story 独立性
