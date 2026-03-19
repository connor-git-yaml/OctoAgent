# Tasks: Memory Automation Pipeline (Phase 3 Advanced Features)

**Input**: Design documents from `.specify/features/065-memory-automation-pipeline/`
**Prerequisites**: plan-phase3.md, spec.md, data-model-phase3.md, contracts/tom-extraction.md, contracts/temporal-decay-mmr.md, contracts/profile-generator.md
**Scope**: Phase 3 only (US-7, US-8, US-9)
**Phase 1 Status**: Completed and merged (T001-T020)
**Phase 2 Status**: Completed and merged (T101-T127)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US7, US8, US9)
- Include exact file paths in descriptions

## Path Conventions

本项目为 monorepo，关键路径：

- **provider 包**: `octoagent/packages/provider/src/octoagent/provider/dx/`
- **gateway app**: `octoagent/apps/gateway/src/octoagent/gateway/services/`
- **gateway tests**: `octoagent/apps/gateway/tests/`
- **memory 包**: `octoagent/packages/memory/src/octoagent/memory/`
- **memory models**: `octoagent/packages/memory/src/octoagent/memory/models/`
- **memory tests**: `octoagent/packages/memory/tests/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Phase 3 不需要创建新项目结构或新包。所有代码改动在已有目录下完成。Phase 1+2 已建立的 ConsolidationService、DerivedExtractionService、memory.write、Scheduler 基础设施直接复用。

*（无 Setup 任务）*

---

## Phase 2: Foundational -- 数据模型与配置扩展

**Purpose**: Phase 3 三个 User Story 共享的数据模型扩展。此阶段完成后，三个 Story 可以并行推进。

**CRITICAL**: US-7 依赖 `ConsolidationScopeResult.tom_extracted` 新字段；US-8 依赖 `MemoryRecallHookOptions` 和 `MemoryRecallHookTrace` 的新字段。

- [x] T201 扩展 `ConsolidationScopeResult`，新增 `tom_extracted: int = 0` 字段，用于记录 ToM 推理产出数。文件: `octoagent/packages/provider/src/octoagent/provider/dx/consolidation_service.py` [MODIFY]
- [x] T202 [P] 扩展 `MemoryRecallHookOptions`，新增 4 个字段：`temporal_decay_enabled: bool = False`、`temporal_decay_half_life_days: float = 30.0`（gt=0.0）、`mmr_enabled: bool = False`、`mmr_lambda: float = 0.7`（ge=0.0, le=1.0）。文件: `octoagent/packages/memory/src/octoagent/memory/models/integration.py` [MODIFY]
- [x] T203 [P] 扩展 `MemoryRecallHookTrace`，新增 5 个字段：`temporal_decay_applied: bool = False`、`temporal_decay_half_life_days: float = 0.0`、`mmr_applied: bool = False`、`mmr_lambda: float = 0.0`、`mmr_removed_count: int = 0`。文件: `octoagent/packages/memory/src/octoagent/memory/models/integration.py` [MODIFY]

**Checkpoint**: 数据模型就绪。三个 User Story 现在可以并行推进。

---

## Phase 3: User Story 7 -- Theory of Mind 推理（用户心智模型）(Priority: P3)

**Goal**: 从 Consolidate 新产出的 SoR 中通过 LLM 推断用户意图、偏好、知识水平和情绪倾向，生成 `derived_type="tom"` 的 DerivedMemoryRecord。ToM 推理为 best-effort，失败不影响 entity/relation/category 提取结果和 SoR 写入。

**Independent Test**: 与 Agent 进行一段包含明显偏好或知识水平信号的对话，触发 Consolidate，验证系统生成 `derived_type=tom` 的 Derived Memory 记录，且 payload 中包含 tom_dimension 字段。

**前置依赖**: T201（ConsolidationScopeResult.tom_extracted 字段）

### Tests for User Story 7

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T204 [P] [US7] 编写 `ToMExtractionService` 单元测试：覆盖 (1) LLM 正常返回 JSON 数组时正确解析并写入 `derived_type="tom"` 记录；(2) LLM 不可用时返回 `errors=["LLM 服务未配置"]` 且 extracted=0，不抛异常；(3) LLM 输出格式错误（非 JSON / 缺少 tom_dimension 字段）时返回 errors；(4) committed_sors 为空时立即返回 extracted=0；(5) derived_memory 写入失败时记入 errors，不影响返回；(6) derived_id 格式验证（`derived:tom:{scope_id}:{timestamp_ms}:{idx}`）；(7) ToM payload 结构验证（包含 tom_dimension、domain、evidence 字段）；(8) confidence 范围验证（0.0-1.0）。文件: `octoagent/apps/gateway/tests/test_tom_extraction.py` [NEW]

### Implementation for User Story 7

- [x] T205 [US7] 创建 `ToMExtractionService` 类：(1) 构造函数接受 `memory_store: SqliteMemoryStore`、`llm_service: LlmServiceProtocol | None`、`project_root: Path`；(2) 定义 `_TOM_SYSTEM_PROMPT`（指导 LLM 从 SoR 事实中推断 intent/preference/knowledge_level/emotional_state 四个维度，输出 JSON 数组，每条包含 derived_type="tom"、tom_dimension、subject_key、summary、confidence、payload）；(3) 定义 `_TOM_USER_PROMPT_TEMPLATE`（将 committed_sors 列表格式化为 `[mem-id] (partition) subject_key: content` 格式供 LLM 审视）。文件: `octoagent/packages/provider/src/octoagent/provider/dx/tom_extraction_service.py` [NEW]
- [x] T206 [US7] 实现 `ToMExtractionService.extract_tom` 方法核心逻辑：(1) 检查 committed_sors 是否为空，空则直接返回；(2) 检查 llm_service 是否可用，不可用则记入 errors 返回；(3) 构建 system + user prompt 拼接所有 SoR 内容；(4) 调用 LLM 获取 JSON 输出；(5) 解析 JSON 数组为 ToM 记录列表（验证 tom_dimension 在四个合法值内）。所有异常内部捕获，记入 result.errors，不抛出。文件: `octoagent/packages/provider/src/octoagent/provider/dx/tom_extraction_service.py` [续 T205]
- [x] T207 [US7] 实现 `ToMExtractionService` 的 derived 记录构建与写入：(1) 将 LLM 输出的每个 JSON 对象转为 `DerivedMemoryRecord`（derived_id 格式 `derived:tom:{scope_id}:{ts}:{idx}`、derived_type="tom"、subject_key 格式 `ToM/{dimension}/{topic}`、payload 含 tom_dimension/domain/evidence/source_memory_ids）；(2) 调用 `memory_store.upsert_derived_records(scope_id, records)` 写入；(3) 记录 `tom_extraction_complete` 结构化日志（scope_id、extracted、skipped、errors[:3]）；(4) 返回 `ToMExtractionResult`。文件: `octoagent/packages/provider/src/octoagent/provider/dx/tom_extraction_service.py` [续 T206]
- [x] T208 [US7] 修改 `ConsolidationService`，添加 ToM 提取 hook：(1) 构造函数新增 `tom_extraction_service: Any | None = None` 可选参数，保存为 `self._tom_service`；(2) 在 `consolidate_scope()` 方法中，Derived 提取完成后（步骤 8.5 之后），若 `_tom_service` 非 None 且 `committed_sors` 非空，best-effort 调用 `extract_tom(scope_id, partition, committed_sors, model_alias)`；(3) 整个 ToM 块在 try/except 中，任何异常只记 warning 日志（`consolidation_tom_extraction_failed`），不影响 consolidate 返回值；(4) 将 tom_result.extracted 写入 ConsolidationScopeResult.tom_extracted。文件: `octoagent/packages/provider/src/octoagent/provider/dx/consolidation_service.py` [MODIFY]
- [x] T209 [US7] 在 `AgentContextService` 中注册 `ToMExtractionService` 的创建：(1) 新增 `get_tom_extraction_service()` 方法，创建并缓存 ToMExtractionService 实例；(2) 修改现有的 `get_consolidation_service()` 方法，在创建 ConsolidationService 时传入 `tom_extraction_service` 参数。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` [MODIFY]

**Checkpoint**: US-7 完成。Consolidate 每次成功 commit SoR 后，除了提取 entity/relation/category，还额外运行 ToM 推理。LLM 不可用时优雅降级（SoR 和 entity/relation/category 提取不受影响）。可独立验证。

---

## Phase 4: User Story 8 -- Temporal Decay + MMR 去重 (Priority: P3)

**Goal**: 在 `MemoryService._apply_recall_hooks()` 的 rerank 阶段之后、Top-K 截断之前，注入时间衰减因子（指数衰减，半衰期 30 天）和 MMR（Maximal Marginal Relevance）去重。两者都是纯计算逻辑，无外部依赖。默认关闭，通过配置启用。

**Independent Test**: 构造一组包含新旧版本和语义重复的记忆数据，配置 `temporal_decay_enabled=True` 和 `mmr_enabled=True` 执行 recall 查询，验证新记忆排名高于旧记忆，且重复记忆被过滤。

**前置依赖**: T202（MemoryRecallHookOptions 新字段）、T203（MemoryRecallHookTrace 新字段）

### Tests for User Story 8

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T210 [P] [US8] 编写 Temporal Decay 单元测试：覆盖 (1) 今天创建的记忆 decay_factor 约等于 1.0；(2) 半衰期（30 天）的记忆 decay_factor 约等于 0.5；(3) 90 天前的记忆 decay_factor 约等于 0.125；(4) decay 后 candidates 按 adjusted_score 降序排列；(5) hit.metadata 中包含 `recall_temporal_decay_factor` 和 `recall_decay_adjusted_score`；(6) `temporal_decay_enabled=False` 时不执行 decay（candidates 顺序不变）；(7) half_life_days 参数可配置（非默认值）；(8) candidates 为空时安全返回空列表。文件: `octoagent/apps/gateway/tests/test_temporal_decay_mmr.py` [NEW]
- [x] T211 [P] [US8] 编写 MMR 去重单元测试（在同一测试文件中）：覆盖 (1) 两条语义相同的结果，MMR 去除重复只保留一条；(2) 三条语义各不相同的结果，MMR 全部保留；(3) mmr_lambda=1.0 时退化为纯相关性排序（不去重）；(4) mmr_lambda=0.0 时最大化多样性；(5) hit.metadata 中包含 `recall_mmr_rank`；(6) `mmr_enabled=False` 时不执行 MMR；(7) candidates <= 1 时跳过 MMR；(8) Jaccard similarity 计算正确（空集返回 0.0、完全相同返回 1.0、部分重叠返回正确比值）。文件: `octoagent/apps/gateway/tests/test_temporal_decay_mmr.py` [续 T210]
- [x] T212 [P] [US8] 编写 `_apply_recall_hooks` 的 decay + MMR 集成测试：覆盖 (1) decay + MMR 同时启用时执行顺序为 rerank -> decay -> MMR -> top-K；(2) MemoryRecallHookTrace 正确记录 temporal_decay_applied / mmr_applied / mmr_removed_count；(3) 只启用 decay 不启用 MMR 时仅执行 decay；(4) 只启用 MMR 不启用 decay 时仅执行 MMR。文件: `octoagent/packages/memory/tests/test_recall_decay_mmr.py` [NEW]

### Implementation for User Story 8

- [x] T213 [US8] 实现 `MemoryService._apply_temporal_decay` 方法：(1) 计算 `decay_constant = ln(2) / max(half_life_days, 1.0)`；(2) 遍历 candidates，根据 `created_at` 计算 `age_days`；(3) 计算 `decay_factor = exp(-decay_constant * age_days)`；(4) 从 hit.metadata 读取已有的 `recall_rerank_score`（或默认 1.0），计算 `adjusted_score = existing_score * decay_factor`；(5) 为每个 candidate 的 metadata 注入 `recall_temporal_decay_factor` 和 `recall_decay_adjusted_score`；(6) 按 adjusted_score 降序重排并返回。文件: `octoagent/packages/memory/src/octoagent/memory/service.py` [MODIFY]
- [x] T214 [US8] 实现 `MemoryService._apply_mmr_dedup` 方法：(1) 从候选的 summary + subject_key 提取文本并空格分词构建 token 集合；(2) 归一化 relevance scores（取 `recall_decay_adjusted_score` 或 `recall_rerank_score` 或 1.0）；(3) 迭代贪心选择 `argmax(lambda * relevance - (1-lambda) * max_jaccard_to_selected)`；(4) 为每个选中 candidate 注入 `recall_mmr_rank` metadata；(5) 返回选中的 candidates 列表（长度 <= max_hits）。文件: `octoagent/packages/memory/src/octoagent/memory/service.py` [MODIFY]
- [x] T215 [US8] 实现 `MemoryService._jaccard_similarity` 静态方法：接受两个 `set[str]` 参数，返回 Jaccard 相似度 `|intersection| / |union|`，空集时返回 0.0。文件: `octoagent/packages/memory/src/octoagent/memory/service.py` [MODIFY]
- [x] T216 [US8] 修改 `MemoryService._apply_recall_hooks` 方法，在现有 rerank 完成后、Top-K 截断之前插入 decay + MMR 两个阶段：(1) 若 `hook_options.temporal_decay_enabled` 且 candidates 非空，调用 `_apply_temporal_decay`，更新 trace 中 temporal_decay_applied 和 temporal_decay_half_life_days；(2) 若 `hook_options.mmr_enabled` 且 candidates > 1，调用 `_apply_mmr_dedup`，更新 trace 中 mmr_applied、mmr_lambda、mmr_removed_count。文件: `octoagent/packages/memory/src/octoagent/memory/service.py` [MODIFY]

**Checkpoint**: US-8 完成。recall 查询可通过配置启用时间衰减和 MMR 去重。两者默认关闭，纯计算无外部依赖。可独立验证。

---

## Phase 5: User Story 9 -- 用户画像自动生成 (Priority: P3)

**Goal**: 新增 ProfileGeneratorService，通过 Scheduler 每天定时从 SoR + Derived Memory 聚合生成用户画像摘要，写入 `partition=profile` 的 SoR 记录。覆盖 6 个维度（基本信息、工作领域、技术偏好、个人偏好、常用工具、近期关注），每个维度独立走 propose/validate/commit 治理流程。

**Independent Test**: 在 Memory 中积累足够的 SoR 和 Derived 记录后，手动触发 `memory.profile_generate` action，验证产出 `partition=profile` 的 SoR 记录，subject_key 格式为 `用户画像/{维度}`，且可通过 recall 检索到。

**前置依赖**: 无 Foundational 依赖（复用 Phase 1 已有的 Scheduler + memory.write 治理流程）

### Tests for User Story 9

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T217 [P] [US9] 编写 `ProfileGeneratorService` 单元测试：覆盖 (1) LLM 正常返回 6 维度 JSON 时逐维度调用 propose/validate/commit，dimensions_generated 正确统计；(2) LLM 不可用时返回 `errors=["LLM 服务未配置"]` 且 skipped=True；(3) SoR < 5 条且 Derived < 3 条时返回 skipped=True 且不调用 LLM（数据不足阈值）；(4) LLM 返回某维度为 null 时跳过该维度；(5) 已有画像维度执行 UPDATE，新维度执行 ADD；(6) 单维度写入失败时记入 errors 但继续处理其他维度；(7) LLM 输出格式错误时返回 errors 且不写入；(8) scope_id 正确传递到 propose_write。文件: `octoagent/apps/gateway/tests/test_profile_generator.py` [NEW]

### Implementation for User Story 9

- [x] T218 [US9] 创建 `ProfileGeneratorService` 类：(1) 构造函数接受 `memory_store: SqliteMemoryStore`、`llm_service: LlmServiceProtocol | None`、`project_root: Path`；(2) 定义类常量 `_PROFILE_DIMENSIONS = ["基本信息", "工作领域", "技术偏好", "个人偏好", "常用工具", "近期关注"]`；(3) 定义 `_PROFILE_SYSTEM_PROMPT`（指导 LLM 基于 SoR/Derived 生成 6 维度画像 JSON，每维度 1-3 句完整自然语言描述，无足够信息的维度返回 null）；(4) 定义 `_PROFILE_USER_PROMPT_TEMPLATE`（格式化 SoR 列表 + Derived 列表 + 已有画像供 LLM 参考）。文件: `octoagent/packages/provider/src/octoagent/provider/dx/profile_generator_service.py` [NEW]
- [x] T219 [US9] 实现 `ProfileGeneratorService.generate_profile` 方法 -- 数据聚合阶段：(1) 检查 llm_service 是否可用，不可用则返回 skipped + errors；(2) 查询 `search_sor(scope_id, partition in [core, profile, work], limit=200)` 获取 SoR 记录；(3) 查询 Derived 记录（`derived_types=["entity", "relation", "tom"]`, limit=100）；(4) 查询已有画像（`search_sor(scope_id, partition=profile)`）；(5) 判断最低数据阈值（SoR < 5 且 Derived < 3 时返回 skipped=True）。文件: `octoagent/packages/provider/src/octoagent/provider/dx/profile_generator_service.py` [续 T218]
- [x] T220 [US9] 实现 `ProfileGeneratorService.generate_profile` 方法 -- LLM 生成阶段：(1) 构建 system + user prompt（SoR 摘要 + Derived 摘要 + 已有画像作为参考）；(2) 调用 LLM 获取 JSON 输出；(3) 解析 JSON 为 `dict[str, str | None]`，key 为维度名。所有异常内部捕获，记入 result.errors，不抛出。文件: `octoagent/packages/provider/src/octoagent/provider/dx/profile_generator_service.py` [续 T219]
- [x] T221 [US9] 实现 `ProfileGeneratorService.generate_profile` 方法 -- 治理写入阶段：(1) 遍历 6 个维度，LLM 返回 null 的跳过；(2) 检查已有画像中是否存在该维度（根据 subject_key `用户画像/{维度}`）；(3) 已有则构建 UPDATE 类型的 WriteProposalDraft（附带 expected_version），新增则构建 ADD 类型；(4) 调用 `memory.propose_write -> validate_proposal -> commit_memory` 完整治理流程，partition=profile；(5) 统计 dimensions_generated / dimensions_updated；(6) 单维度写入失败记入 errors，继续处理其他维度；(7) metadata 中记录 source=profile_generator、generated_at、sor_count、derived_count、tom_count。文件: `octoagent/packages/provider/src/octoagent/provider/dx/profile_generator_service.py` [续 T220]
- [x] T222 [US9] 在 `AgentContextService` 中注册 `ProfileGeneratorService` 的创建和获取：新增 `get_profile_generator_service()` 方法，创建并缓存 ProfileGeneratorService 实例。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` [MODIFY]
- [x] T223 [US9] 在 `ControlPlaneService` 中注册 `memory.profile_generate` action handler：(1) 在 `execute_action` 方法中新增 `"memory.profile_generate"` 分支；(2) 实现 `_handle_memory_profile_generate` 方法：解析 project/workspace 上下文、获取 MemoryService 和 ProfileGeneratorService、逐 scope 调用 generate_profile、汇总 dimensions_generated/dimensions_updated/errors、返回 ActionResultEnvelope。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` [MODIFY]
- [x] T224 [US9] 注册 `system:memory-profile-generate` Scheduler 定时作业：在 `_ensure_system_jobs()` 或 `_initialize_automation_jobs()` 中新增方法 `_ensure_system_profile_generate_job()`，创建 AutomationJob（job_id="system:memory-profile-generate"、action_id="memory.profile_generate"、schedule_expr="0 2 * * *" 每天凌晨 2 点 UTC、enabled=True）。若已存在则跳过。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` [MODIFY]

**Checkpoint**: US-9 完成。Scheduler 每天凌晨自动生成用户画像，写入 partition=profile 的 SoR 记录。LLM 不可用或数据不足时优雅降级。画像可通过 recall 或 memory.read 检索。可独立验证。

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 确保整体质量、导出管理、文档完备、端到端验证。

- [x] T225 [P] 更新 `provider/dx/__init__.py`，导出新增的两个服务类（ToMExtractionService、ProfileGeneratorService）和相关数据类（ToMExtractionResult、ProfileGenerateResult）。文件: `octoagent/packages/provider/src/octoagent/provider/dx/__init__.py` [MODIFY]
- [x] T226 [P] 检查并补充 structlog 结构化日志：(1) ToMExtractionService 的 `tom_extraction_complete` / `tom_extraction_failed` 日志事件；(2) ProfileGeneratorService 的 `profile_generate_complete` / `profile_generate_failed` / `profile_generate_skipped` 日志事件；(3) MemoryService 的 `recall_temporal_decay_applied` / `recall_mmr_applied` 日志事件（可选，在 hook_trace 中已有记录）。
- [x] T227 [P] 验证现有管理台 Consolidate 功能不退化：手动触发 Consolidate 操作，确认 ConsolidationService 的调用链仍然正常工作，ToM 提取 hook 正常触发（或在 ToMExtractionService 为 None 时跳过），Derived 提取不受影响。
- [x] T228 [P] 验证现有 recall 功能不退化：在 `temporal_decay_enabled=False` 和 `mmr_enabled=False`（默认值）的情况下，recall 查询行为与 Phase 2 完全一致，无副作用。
- [x] T229 [P] 运行全量测试套件（`pytest`），确保 Phase 3 新增代码不破坏现有测试。修复任何回归。
- [x] T230 运行 quickstart-phase3.md 验证：按照 `.specify/features/065-memory-automation-pipeline/quickstart-phase3.md` 步骤执行端到端验证，确认三个 User Story 的核心场景均可走通。

---

## FR Coverage Map

确保 spec.md Phase 3 中每条 Functional Requirement 都有至少一个任务覆盖。

| FR ID | 描述 | 级别 | 覆盖任务 |
|-------|------|------|----------|
| FR-019 | Theory of Mind 推理，生成 derived_type=tom 的 DerivedMemoryRecord | MAY | T205, T206, T207, T208 |
| FR-020 | Temporal Decay，对旧记忆施加时间衰减因子 | MAY | T213, T216 |
| FR-021 | MMR 去重，在 recall Top-K 中去除语义重复条目 | MAY | T214, T215, T216 |
| FR-022 | 用户画像自动生成，定期从 SoR/Derived 聚合产出 profile 摘要 | MAY | T218, T219, T220, T221, T224 |

**覆盖率**: 4/4 Phase 3 需求 = 100%（4 个 MAY 全覆盖）

> 注：spec.md 中 FR-022 和 FR-023 在 plan-phase3.md 合并为同一实现（ProfileGeneratorService + Scheduler 注册），此处统一标记为 FR-022。

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup) -- 无任务，跳过
     |
Phase 2 (Foundational: 数据模型扩展)
  T201 (ConsolidationScopeResult.tom_extracted)
  T202 (MemoryRecallHookOptions 新字段) -- [P] 与 T201/T203 并行
  T203 (MemoryRecallHookTrace 新字段) -- [P] 与 T201/T202 并行
     |
     +---------+---------+
     |         |         |
Phase 3     Phase 4    Phase 5
(US-7)      (US-8)     (US-9)
     |         |         |
     +---------+---------+
              |
         Phase 6 (Polish)
```

### User Story 间依赖

- **US-7 (ToM 推理)**: 依赖 T201（ConsolidationScopeResult 扩展）。不依赖 US-8 或 US-9。
- **US-8 (Temporal Decay + MMR)**: 依赖 T202 和 T203（Hook 模型扩展）。不依赖 US-7 或 US-9。
- **US-9 (用户画像)**: 仅依赖 Phase 1+2 已有的 Scheduler 和治理流程。理论上不依赖 Foundational 阶段，但建议在 Foundational 之后以保持一致性。不依赖 US-7 或 US-8。

**三个 Story 完全独立，无交叉依赖。Foundational 完成后可完全并行推进。**

### Story 内部并行机会

- **US-7**: T204（测试）可与 T205 并行启动（测试先行）。T205/T206/T207 需串行（同一文件的连续增量修改）。T208 依赖 T207 完成（需确认 ToMExtractionService 接口稳定）。T209 依赖 T208（需知 ConsolidationService 新参数签名）。
- **US-8**: T210/T211/T212（测试）可并行启动。T213/T214/T215 需串行（同一文件 service.py）。T216 依赖 T213-T215（需三个新方法都实现后再修改 _apply_recall_hooks 调用链）。
- **US-9**: T217（测试）可先行。T218/T219/T220/T221 需串行（同一文件）。T222 和 T223/T224 可与 T221 完成后并行（agent_context.py vs control_plane.py 是不同文件）。

### Recommended Implementation Strategy: Incremental Delivery

1. **先完成 Phase 2**（T201-T203）：3 个任务全部可并行，数据模型就绪
2. **然后 US-8**（T210-T216）：纯计算逻辑、无 LLM 依赖，最易验证和稳定
3. **然后 US-7**（T204-T209）：依赖 T201，与 ConsolidationService 集成
4. **然后 US-9**（T217-T224）：依赖最多组件（Scheduler + ControlPlane + 治理流程），复杂度最高
5. **最后 Phase 6**（T225-T230）：整体验证

单人开发建议按此顺序串行执行。若需并行，US-7/US-8/US-9 在 Foundational 完成后可完全并行（修改不同文件）。

---

## Notes

- [P] 标记 = 不同文件、无依赖，可并行
- [USN] 标记 = 映射到对应 User Story，便于追踪
- Phase 3 任务编号从 T201 起，与 Phase 1 (T001-T020) 和 Phase 2 (T101-T127) 不冲突
- 每个 Story 的测试任务（T204/T210-T212/T217）应先行编写并确认失败
- 提交粒度建议：每完成一个任务或逻辑组即 commit
- 在任意 Checkpoint 处可暂停验证 Story 独立性
- Temporal Decay 和 MMR 默认关闭（渐进式验证），先在配置中手动启用观察效果
