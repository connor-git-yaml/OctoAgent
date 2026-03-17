# Tasks: 060 Context Engineering Enhancement

**Input**: `.specify/features/060-context-engineering-enhancement/` (spec.md, plan.md, data-model.md, contracts/)
**Prerequisites**: plan.md (required), spec.md (required), data-model.md, contracts/
**Branch**: `claude/festive-meitner`
**Date**: 2026-03-17

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US0, US1, US2, US3, US4, US5)
- Include exact file paths in descriptions

## Path Conventions

本项目为 monorepo，所有源码在 `octoagent/` 前缀下：

```
octoagent/
  apps/gateway/src/octoagent/gateway/services/  # 核心服务层
  apps/gateway/tests/                            # 测试
  packages/provider/src/octoagent/provider/      # Provider 层
  packages/tooling/src/octoagent/tooling/        # 工具层
  packages/core/src/octoagent/core/models/       # 数据模型
  frontend/src/domains/settings/                 # 前端 Settings
```

---

## Phase 0: Foundational -- Token 估算升级 + 全局预算统一 (US0, P0)

**Purpose**: 所有后续 Phase 的地基。统一全局 token 预算，修复中文 token 估算、Skill 注入游离于预算外的架构缺陷。不解决预算断裂，分层压缩、异步压缩都无法正确决策目标 token 数。

**对应 FR**: FR-000, FR-000a, FR-000b, FR-000c, FR-000d, FR-000e, FR-000f
**对应 User Story**: US0 - 全局 token 预算统一管理

**CRITICAL**: Phase 1-4 的所有任务均依赖本 Phase 完成。

### 0A: Token 估算升级

- [x] T001 [P] [US0] 替换 `estimate_text_tokens()` 为中文感知版本 -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py`
  - **改动**: 将 `estimate_text_tokens()` 从 `len(text)/4` 改为 CJK 感知插值公式 `len(text) / (4*(1-r) + 1.5*r)`，其中 `r` 为非 ASCII 字符比例
  - **改动**: 新增模块级 `_tiktoken_encoder` 初始化（`try/except ImportError` 保护），有 tiktoken 时使用 `cl100k_base` 精确计算
  - **改动**: 新增 `_chars_per_token_ratio(text_sample)` 辅助函数
  - **依赖**: 无
  - **验收**: 纯中文文本估算误差 < 30%（对比 tiktoken 基准或已知 token 数）；纯英文行为与现有基本一致；混合文本按比例加权

- [x] T002 [P] [US0] 修正 `_chunk_segments_by_token_budget()` 中的硬编码 `*4` -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py`
  - **改动**: `char_budget = max(256, transcript_budget * 4)` 改为 `char_budget = max(256, int(transcript_budget * _chars_per_token_ratio(sample_text)))`，其中 `sample_text` 取 segments 前 3 段的前 200 字符
  - **依赖**: T001（需要 `_chars_per_token_ratio` 函数）
  - **验收**: 中文 segments 的 char_budget 不再是英文假设的 4 倍

- [x] T003 [P] [US0] Token 估算单元测试 -- `octoagent/apps/gateway/tests/test_context_compaction.py`
  - **改动**: 在现有测试文件中新增测试组：纯英文、纯中文、中英混合、空字符串、tiktoken fallback 场景
  - **依赖**: T001
  - **验收**: 测试覆盖 `estimate_text_tokens()` 和 `_chars_per_token_ratio()` 的各场景，中文估算误差 < 30%

### 0B: ContextBudgetPlanner

- [x] T004 [US0] 创建 `BudgetAllocation` 数据类和 `ContextBudgetPlanner` 类 -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_budget.py` (NEW)
  - **改动**: 新建文件，定义 `BudgetAllocation`（frozen dataclass）和 `ContextBudgetPlanner` 类
  - **改动**: `ContextBudgetPlanner.plan()` 实现：计算 system_blocks_budget、skill_injection_budget、memory_recall_budget、progress_notes_budget、conversation_budget
  - **改动**: 预算不足时的优先级缩减逻辑（progress_notes -> memory -> skill -> conversation 下限 800）
  - **改动**: `estimation_method` 字段反映当前 `estimate_text_tokens()` 使用的算法
  - **依赖**: T001（需要 `estimate_text_tokens` 的 CJK 感知版本来确定 estimation_method）
  - **验收**: `BudgetAllocation` 各部分之和 <= `max_input_tokens`；`conversation_budget >= 800`

- [x] T005 [P] [US0] ContextBudgetPlanner 单元测试 -- `octoagent/apps/gateway/tests/test_context_budget.py` (NEW)
  - **改动**: 新建测试文件，覆盖：正常分配、预算不足缩减、无 Skill、多 Skill、有/无进度笔记、`max_input_tokens < 800` 边界
  - **依赖**: T004
  - **验收**: 所有预算不变量（各部分之和 <= max_input_tokens、conversation_budget >= 800）在各场景下成立

### 0C: Skill 注入修复

- [x] T006 [US0] 在 `_build_system_blocks()` 中新增 `LoadedSkills` 系统块（含截断逻辑） -- `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`
  - **改动**: `_build_system_blocks()` 新增 `loaded_skills_content: str = ""` 和 `skill_injection_budget: int = 0` 参数
  - **改动**: 当 `loaded_skills_content` 非空时，创建 LoadedSkills 系统块并加入 blocks 列表
  - **改动**: 当 Skill 总 token 超出 `skill_injection_budget` 时，按加载顺序保留 Skill，截断超出部分，并在 `block_reasons` 中记录被截断的 Skill 列表（供 control plane 审计，对应 spec Edge Case 8）
  - **改动**: `build_task_context()` 新增 `budget_allocation: BudgetAllocation | None = None` 和 `loaded_skills_content: str = ""` 参数，传递给 `_build_system_blocks()`
  - **依赖**: T004（需要 BudgetAllocation 类型定义）
  - **验收**: LoadedSkills 块参与 `_fit_prompt_budget()` 的 token 计算；加载 5+ Skill 总内容 >2000 token 时按顺序截断并记录被截断列表

- [x] T007 [US0] 移除 `LLMService._build_loaded_skills_context()` 的追加逻辑 -- `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`
  - **改动**: 在 `_try_call_with_tools()` 中移除 `_build_loaded_skills_context()` 追加到 `base_description` 的逻辑
  - **改动**: `_build_loaded_skills_context()` 方法保留（改为内部工具方法），供 `task_service.py` 调用获取 Skill 内容文本
  - **依赖**: T006（确保 Skill 内容已通过 LoadedSkills 系统块注入）
  - **验收**: Skill 内容不再在 `_try_call_with_tools()` 中双重注入；Skill 内容仅通过 LoadedSkills 系统块出现一次

### 0D: 压缩层接口升级 + 集成

- [x] T008 [US0] `build_context()` 接口新增 `conversation_budget` 参数 -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py`
  - **改动**: `build_context()` 新增可选参数 `conversation_budget: int | None = None`
  - **改动**: 当 `conversation_budget` 传入时，`_should_compact()` 和 `target_tokens` 基于 `conversation_budget` 而非 `max_input_tokens`
  - **改动**: 未传入时回退到 `max_input_tokens`（向后兼容）
  - **依赖**: T001、T002
  - **验收**: 传入 conversation_budget 时压缩目标正确；不传时行为与 Feature 034 一致

- [x] T009 [US0] `task_service.py` 集成 BudgetPlanner 和 Skill 注入迁移 -- `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py`
  - **改动**: `_build_task_context()` 开头调用 `ContextBudgetPlanner.plan()`
  - **改动**: 将 `BudgetAllocation.conversation_budget` 传给 `build_context()`
  - **改动**: 调用 `llm_service._build_loaded_skills_content()` 获取 Skill 文本，传给 `build_task_context(loaded_skills_content=...)`
  - **改动**: 将 `budget_allocation` 传给 `AgentContextService.build_task_context()`
  - **依赖**: T004、T006、T007、T008
  - **验收**: 完整调用链 BudgetPlanner -> build_context(conversation_budget) -> build_task_context(budget_allocation, loaded_skills_content) 能运行

- [x] T010 [US0] 全局预算集成测试 -- `octoagent/apps/gateway/tests/test_context_budget.py`
  - **改动**: 在 T005 创建的测试文件中新增集成测试：模拟中文多轮对话 + 2 个 Skill + Memory 场景，验证实际交付 token 不超 max_input_tokens
  - **依赖**: T009
  - **验收**: 端到端验证 SC-000（总 token 不超限）和 SC-000a（中文估算误差 < 30%）

**Checkpoint**: Phase 0 完成后，全局 token 预算统一管理生效，中文估算修正，Skill 注入纳入预算。后续 Phase 可以开始。

---

## Phase 1: US1 - 压缩模型可在 Settings 中独立配置 (Priority: P1)

**Goal**: 用户可以在 Settings 中为上下文压缩指定轻量模型，压缩成本与主模型解耦。
**Independent Test**: 进入 Settings，设置 `compaction` 为 haiku，触发多轮压缩，验证用的是 haiku；删除后验证 fallback 到 summarizer/main。
**对应 FR**: FR-001, FR-002, FR-003, FR-004

### Implementation

- [x] T011 [P] [US1] 在 AliasRegistry 中注册 `compaction` 语义别名 -- `octoagent/packages/provider/src/octoagent/provider/alias.py`
  - **改动**: 在 `_get_default_aliases()` 中新增 `AliasConfig(name="compaction", category="cheap", runtime_group="cheap", description="上下文压缩（推荐轻量模型如 haiku / gpt-4o-mini）")`
  - **依赖**: Phase 0 完成
  - **验收**: `AliasRegistry` 初始化后包含 `compaction` 别名

- [x] T012 [US1] `ContextCompactionConfig` 新增 `compaction_alias` 字段 -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py`
  - **改动**: `ContextCompactionConfig` 新增 `compaction_alias: str = "compaction"` 字段
  - **改动**: 新增环境变量映射 `OCTOAGENT_CONTEXT_COMPACTION_ALIAS`
  - **依赖**: Phase 0 完成
  - **验收**: 配置可通过环境变量覆盖

- [x] T013 [US1] 实现 `_call_summarizer()` 的 `compaction -> summarizer -> main` fallback 链 -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py`
  - **改动**: `_call_summarizer()` 实现三级 fallback：遍历 `[compaction_alias, summarizer_alias, "main"]`，每级 try/except 降级
  - **改动**: 实际使用的 alias 记录到 `CompiledTaskContext.summary_model_alias`
  - **改动**: 压缩事件 payload 新增 `model_alias`、`fallback_used`、`fallback_chain` 字段
  - **依赖**: T011、T012
  - **验收**: compaction alias 不可用时自动 fallback 到 summarizer/main；事件记录包含 fallback 信息

- [x] T014 [P] [US1] Settings 前端展示 `compaction` 别名 -- `octoagent/frontend/src/domains/settings/SettingsProviderSection.tsx`
  - **改动**: 在 alias 编辑器中，当 `alias === "compaction"` 时，显示辅助说明："上下文压缩（推荐轻量模型如 haiku / gpt-4o-mini）"和 fallback 链："compaction -> summarizer -> main"
  - **依赖**: T011（backend alias 存在后 Settings API 自然返回该条目）
  - **验收**: Settings 页面能看到 compaction 别名及其 fallback 说明

- [x] T015 [US1] Fallback 链单元测试 -- `octoagent/apps/gateway/tests/test_context_compaction.py`
  - **改动**: 在现有测试文件中新增测试组：compaction alias 正常调用、compaction 失败 fallback 到 summarizer、summarizer 失败 fallback 到 main、全部失败返回空摘要
  - **依赖**: T013
  - **验收**: 覆盖 FR-002 和 FR-004 的所有场景

**Checkpoint**: US1 完成后，用户可在 Settings 中配置压缩模型，fallback 链正常工作。

---

## Phase 2: US3 - 大消息先廉价截断再 LLM 摘要 (Priority: P1)

**Goal**: 工具输出、长 JSON 等结构化内容先被截断/精简，只在截断后仍超预算时才调用 LLM 摘要，降低压缩成本和延迟。
**Independent Test**: 创建超大工具输出（>2000 token）对话，触发压缩，验证先截断后摘要，LLM 调用次数少于纯 LLM 方案。
**对应 FR**: FR-010, FR-011, FR-012, FR-013

### Implementation

- [x] T016 [P] [US3] 实现 `_smart_truncate_json()` -- JSON 智能精简 -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py`
  - **改动**: 新增 `_smart_truncate_json(text, max_tokens)` 方法：解析 JSON -> `_prune_json_value(value, depth, max_depth=3)` 递归精简（保留 priority keys: status/error/result/message/code/id/name/type，数组只保留前 2 项 + 总数提示）
  - **依赖**: Phase 0 完成（需要 CJK 感知的 estimate_text_tokens）
  - **验收**: 大 JSON 被精简后保留关键字段，数组冗余项被移除

- [x] T017 [P] [US3] 实现 `_head_tail_truncate()` -- 非 JSON 文本截断 -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py`
  - **改动**: 新增 `_head_tail_truncate(text, max_tokens)` 方法：保留头 40% + 尾 10% + 中间 `[... truncated ~N tokens ...]` 标记
  - **依赖**: Phase 0 完成（需要 `_chars_per_token_ratio` 和 `estimate_text_tokens`）
  - **验收**: 超大文本被截断到目标 token 数，保留头尾上下文

- [x] T018 [US3] 实现 `_cheap_truncation_phase()` -- 廉价截断入口 -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py`
  - **改动**: 新增 `_cheap_truncation_phase(messages, conversation_budget)` 方法：遍历消息，单条超过 `conversation_budget * large_message_ratio` 时调用 `_smart_truncate_json()` 或 `_head_tail_truncate()`
  - **改动**: 返回 `(truncated_messages, messages_affected_count)`
  - **依赖**: T016、T017
  - **验收**: 超大消息被截断，计数正确

- [x] T019 [US3] 新增 `CompactionPhaseResult` 数据类和 `CompiledTaskContext` 扩展 -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py`
  - **改动**: 新增 `CompactionPhaseResult` frozen dataclass（phase、messages_affected、tokens_saved、model_used）
  - **改动**: `CompiledTaskContext` 新增 `compaction_phases: list[dict[str, Any]]` 字段
  - **改动**: `ContextCompactionConfig` 新增 `large_message_ratio: float = 0.3` 和 `json_smart_truncate: bool = True`
  - **依赖**: Phase 0 完成
  - **验收**: 数据类可正确序列化，CompiledTaskContext 向后兼容（默认空列表）

- [x] T020 [US3] 在 `build_context()` 中集成两阶段压缩 -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py`
  - **改动**: 在 `build_context()` 中 LLM 摘要之前先调用 `_cheap_truncation_phase()`；截断后仍超预算才进入 LLM 摘要
  - **改动**: 将两阶段的 `CompactionPhaseResult` 记录到 `CompiledTaskContext.compaction_phases`
  - **改动**: 压缩事件 payload 新增 `compaction_phases` 详情
  - **依赖**: T018、T019
  - **验收**: 包含超大工具输出的对话，先截断后摘要；LLM 摘要调用次数 < 纯 LLM 方案

- [x] T021 [US3] 两阶段压缩单元测试 -- `octoagent/apps/gateway/tests/test_context_compaction.py`
  - **改动**: 新增测试组：超大 JSON 截断、超大文本截断、截断后不需要 LLM 摘要的场景、截断后仍需要 LLM 摘要的场景、JSON 解析失败 fallback 到头尾截断
  - **依赖**: T020
  - **验收**: 覆盖 FR-010 ~ FR-013 的所有验收场景

**Checkpoint**: US3 完成后，大消息先廉价截断再 LLM 摘要，压缩成本显著降低。

---

## Phase 3: US2 - 长对话中旧历史分层压缩 (Priority: P1)

**Goal**: 10+ 轮对话后，近期保持完整，中期保留决策，远期只保留骨架，而非全部旧历史被压成一段不分层的摘要。
**Independent Test**: 发送 15 轮对话，检查 CONTEXT_COMPACTION_COMPLETED 事件，验证产出包含 Recent/Compressed/Archive 三层。
**对应 FR**: FR-005 ~ FR-009a
**依赖**: Phase 0 + Phase 2（两阶段压缩是分层压缩的前置步骤）

### Implementation

- [x] T022 [P] [US2] 新增 `ContextLayer` 数据类 -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py`
  - **改动**: 新增 `ContextLayer` frozen dataclass（layer_id、turns、token_count、max_tokens、entry_count）
  - **改动**: `CompiledTaskContext` 新增 `layers: list[dict[str, Any]]` 和 `compaction_version: str` 字段
  - **依赖**: Phase 0 完成
  - **验收**: 数据类可正确实例化和序列化

- [x] T023 [P] [US2] `ContextCompactionConfig` 新增分层配置字段 -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py`
  - **改动**: 新增 `recent_ratio: float = 0.50`、`compressed_ratio: float = 0.30`、`archive_ratio: float = 0.20`、`compressed_window_size: int = 4`
  - **改动**: 新增对应环境变量映射
  - **依赖**: Phase 0 完成
  - **验收**: `recent_ratio + compressed_ratio + archive_ratio == 1.0` 不变量维持

- [x] T024 [US2] 实现 Compressed 层分组策略 `_group_turns_to_compressed()` -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py`
  - **改动**: 新增 `_group_turns_to_compressed(turns)` 方法：按 `compressed_window_size`（默认 4 个 turn = 2 轮 user+assistant 对）固定窗口分组
  - **改动**: 每个 user+assistant 对为原子单元
  - **依赖**: T023
  - **验收**: 分组正确，不拆分 user+assistant 对

- [x] T025 [US2] 实现 Archive 层与 `rolling_summary` 整合 -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py`
  - **改动**: 新增 `_parse_compaction_state(agent_session)` 方法：按 `compaction_version`（v1/v2）解析 rolling_summary 和 compressed_layers
  - **改动**: `AgentSession.metadata` 新增 `compaction_version` 和 `compressed_layers` 键约定
  - **改动**: v1（旧 session）兼容：rolling_summary 整体作为 Archive 层
  - **依赖**: T022
  - **验收**: v1 旧 session 和 v2 新 session 都能正确解析

- [x] T026 [US2] 重构 `build_context()` 实现三层压缩 -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py`
  - **改动**: `build_context()` 核心逻辑重构为：(1) 加载 turns → (2) 廉价截断（T020 已实现）→ (3) 计算各层预算（recent/compressed/archive × conversation_budget）→ (4) Recent 层保留最近 N 轮原文 → (5) 中间 turns 按窗口分组 → 最新组做 LLM 摘要生成 Compressed 层 → (6) 更旧组与已有 Archive 合并
  - **改动**: 持久化：Archive → rolling_summary（v2 语义）；Compressed → metadata["compressed_layers"]
  - **改动**: `CompiledTaskContext` 填充 `layers` 和 `compaction_version` 字段
  - **依赖**: T020、T022、T023、T024、T025
  - **验收**: 15 轮对话后产出包含三层，各层 token 在预算内

- [x] T027 [US2] 收窄 SessionReplay 在当前 session 内的职责 -- `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`
  - **改动**: `_fit_prompt_budget()` 中，当 `has_compressed_layers=True` 时，SessionReplay 使用收窄选项（dialogue_limit=0，只保留 session summary），不再回放当前 session 内的中期历史
  - **改动**: 现有 dialogue_limit 梯度（8->6->4->3->None）保留，仅用于跨 session 场景
  - **依赖**: T006、T026（需要 compressed_layers 存在判断）
  - **验收**: 有 Compressed 层时 SessionReplay 不重复中期历史；无 Compressed 层时行为与 034 一致

- [x] T028 [US2] 分层压缩集成测试 -- `octoagent/apps/gateway/tests/test_context_compaction.py`
  - **改动**: 新增测试组：8 轮对话触发首次三层压缩、20 轮对话递归合并 Archive、v1->v2 迁移兼容、Compressed 层中关键决策保留到 Archive、各层 token 预算遵守
  - **依赖**: T026、T027
  - **验收**: 覆盖 FR-005 ~ FR-009a 的所有验收场景

**Checkpoint**: US2 完成后，长对话分层压缩生效，信息密度显著优于扁平摘要。

---

## Phase 4: US4 - 压缩异步执行不阻塞请求 (Priority: P2)

**Goal**: 压缩在后台异步执行，不增加用户等待模型回复的时间。
**Independent Test**: 多轮对话中对比异步压缩前后的 p50/p95 延迟差异。
**对应 FR**: FR-014 ~ FR-017
**依赖**: Phase 0 + Phase 3（异步压缩需要分层压缩的数据结构）

### Implementation

- [x] T029 [US4] 实现 `schedule_background_compaction()` 和 `await_compaction_result()` -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py`
  - **改动**: 新增类属性 `_compaction_locks: dict[str, asyncio.Lock]` 和 `_pending_compactions: dict[str, asyncio.Task]`
  - **改动**: `schedule_background_compaction()` 方法：为 session 创建后台 `asyncio.Task` 执行压缩，per-session Lock 保护写入
  - **改动**: `await_compaction_result()` 方法：等待后台任务完成（`asyncio.wait_for` 保护，默认 10 秒超时）
  - **改动**: `ContextCompactionConfig` 新增 `async_compaction_timeout: float = 10.0`
  - **依赖**: T026（需要三层压缩的 build_context 逻辑）
  - **验收**: 后台任务正确启动、完成、超时回退

- [x] T030 [US4] 并发控制 -- `record_response_context()` 获取 per-session Lock -- `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py`
  - **改动**: 在 `record_response_context()` 中更新 rolling_summary / compressed_layers 前获取 per-session Lock
  - **改动**: 超时由 `asyncio.wait_for` 保护
  - **依赖**: T029
  - **验收**: 后台压缩和 turn hook 不会对同一 session 并发写入 rolling_summary

- [x] T031 [US4] `task_service.py` 异步压缩集成 -- `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py`
  - **改动**: LLM 调用完成后（`_handle_llm_call` 或等效位置），当 `compiled.final_tokens > budget.conversation_budget * 0.6` 时调用 `schedule_background_compaction()`
  - **改动**: `_build_task_context()` 开头调用 `await_compaction_result()` 消费后台结果
  - **依赖**: T029、T030
  - **验收**: 后台压缩在 turn 间执行；超时/失败回退到同步压缩

- [x] T032 [US4] 异步压缩单元测试 -- `octoagent/apps/gateway/tests/test_context_compaction.py`
  - **改动**: 新增测试组：后台压缩正常完成、后台压缩超时回退同步、后台压缩失败回退同步、重复调度幂等、per-session Lock 不阻塞不同 session
  - **依赖**: T031
  - **验收**: 覆盖 FR-014 ~ FR-017 的所有验收场景；验证 SC-004（延迟不高于同步方案）

**Checkpoint**: US4 完成后，压缩异步执行，请求延迟不增加。

---

## Phase 5: US5 - Worker 进度笔记 (Priority: P2)

**Goal**: Worker 在执行长任务过程中自动记录进度笔记，上下文压缩或重置后可从断点继续。
**Independent Test**: 创建 Worker 长任务（5+ 步），中途触发上下文压缩，验证 Worker 通过读取进度笔记继续工作。
**对应 FR**: FR-018 ~ FR-021
**依赖**: Phase 0（需要 BudgetPlanner 纳入进度笔记预算）；与 Phase 3/4 独立

### Implementation

- [x] T033 [P] [US5] 定义 `progress_note` 工具（Input/Output 模型 + 工具元数据）-- `octoagent/packages/tooling/src/octoagent/tooling/progress_note.py` (NEW)
  - **改动**: 新建文件，定义 `ProgressNoteInput`（Pydantic BaseModel）、`ProgressNoteOutput`（Pydantic BaseModel）、`TOOL_META` 字典
  - **改动**: Input 字段：step_id (str, min_length=1)、description (str, min_length=1)、status (Literal["completed","in_progress","blocked"])、key_decisions (list[str])、next_steps (list[str])
  - **依赖**: Phase 0 完成
  - **验收**: 工具 schema 符合 contracts/progress-note-tool.md 定义

- [x] T034 [US5] 实现 `progress_note` 工具执行逻辑 -- Artifact 存储 -- `octoagent/packages/tooling/src/octoagent/tooling/progress_note.py`
  - **改动**: 实现工具执行函数：构造 `Artifact`（type: progress-note，JSON part），写入 Artifact Store
  - **改动**: artifact_id 格式 `pn-{task_id[:8]}-{step_id}-{ulid}`
  - **改动**: Artifact Store 不可用时返回 `persisted=False`，不阻断 Worker 执行
  - **依赖**: T033
  - **验收**: 进度笔记成功写入 Artifact Store 并可查询

- [x] T035 [US5] 在 `_build_system_blocks()` 中新增 `ProgressNotes` 系统块 -- `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`
  - **改动**: `_build_system_blocks()` 新增 `progress_notes: list[dict] | None = None` 参数
  - **改动**: 当 progress_notes 非空时，构建 `## Progress Notes` 系统块（最近 N 条笔记的 step_id + status + description + next_steps 摘要）
  - **改动**: N 由 `ContextCompactionConfig.progress_note_inject_limit`（默认 5）控制
  - **依赖**: T006（_build_system_blocks 已在 Phase 0 中扩展）
  - **验收**: ProgressNotes 块正确出现在系统消息中，纳入 _fit_prompt_budget 的 token 计算

- [x] T036 [US5] `task_service.py` 集成进度笔记加载和注入 -- `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py`
  - **改动**: `_build_task_context()` 中加载当前 task 的最近进度笔记（查询 Artifact Store type=progress-note）
  - **改动**: 将笔记列表传给 `build_task_context(progress_notes=...)`
  - **改动**: 进度笔记数量传给 `BudgetPlanner.plan(has_progress_notes=..., progress_note_count=...)`
  - **依赖**: T034、T035、T009（BudgetPlanner 集成已在 Phase 0 完成）
  - **验收**: Worker 上下文中包含最近 5 条进度笔记

- [x] T037 [US5] 进度笔记自动合并 -- `octoagent/packages/tooling/src/octoagent/tooling/progress_note.py`
  - **改动**: 新增 `_merge_old_progress_notes(task_id, agent_session_id)` 方法：笔记超过 50 条时，保留最近 10 条，旧笔记合并为一条 `[历史里程碑汇总]` Artifact
  - **改动**: 在工具执行函数末尾调用检查是否需要合并
  - **改动**: `ContextCompactionConfig` 新增 `progress_note_merge_threshold: int = 50`、`progress_note_inject_limit: int = 5`
  - **依赖**: T034
  - **验收**: 超过 50 条后旧笔记被合并，注入上下文时只有最近笔记 + 汇总

- [x] T038 [US5] 进度笔记单元测试 -- `octoagent/apps/gateway/tests/test_progress_note.py` (NEW)
  - **改动**: 新建测试文件，覆盖：笔记写入 Artifact Store、Artifact Store 不可用降级、上下文注入最近 5 条、自动合并阈值、Butler 通过 control plane 查询 Worker 笔记
  - **依赖**: T036、T037
  - **验收**: 覆盖 FR-018 ~ FR-021 的所有验收场景

**Checkpoint**: US5 完成后，Worker 进度笔记协议生效，长任务可从断点恢复。

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 跨 Story 的治理、兼容性、文档、回归

- [x] T039 [P] 验证 Subagent 绕过所有新增压缩机制 -- `octoagent/apps/gateway/tests/test_context_compaction.py`
  - **改动**: 新增测试：Subagent 场景下不触发分层压缩、不触发异步压缩、不注入进度笔记
  - **对应 FR**: FR-023
  - **验收**: Subagent 行为与 Feature 034 完全一致

- [x] T040 [P] 验证所有新增压缩路径走既有审计链 -- `octoagent/apps/gateway/tests/test_context_compaction.py`
  - **改动**: 新增测试：三层压缩、两阶段压缩、异步压缩的事件均包含完整的 layers/phases 信息
  - **对应 FR**: FR-022
  - **验收**: control plane 可审计所有压缩路径的详情

- [x] T041 [P] 验证新增配置项通过 `setup.review -> setup.apply` 流程 -- `octoagent/apps/gateway/tests/test_context_compaction.py`
  - **改动**: 新增测试：compaction alias 通过 Settings API 配置和读取
  - **对应 FR**: FR-024
  - **验收**: 不直写环境变量

- [x] T042 [P] 边界条件和 Edge Case 回归测试 -- `octoagent/apps/gateway/tests/test_context_compaction.py`
  - **改动**: 新增测试组覆盖 spec 中的 Edge Cases：
    - 1-3 轮对话不触发压缩
    - 后台压缩超时 >10s 回退
    - 进度笔记 >50 条自动合并
    - 用户加载 5+ Skill 且总内容 >2000 token 时按顺序截断
    - `ContextBudgetPlanner` 预估偏差 >20% 时 `_fit_prompt_budget()` 兜底
  - **依赖**: Phase 1-5 全部完成
  - **验收**: 所有 Edge Cases 有对应测试

- [x] T043 更新压缩相关事件的 Payload 文档 -- `.specify/features/060-context-engineering-enhancement/contracts/compaction-alias-api.md`
  - **改动**: 确认 `CONTEXT_COMPACTION_COMPLETED` 事件 payload 的新增字段（layers、compaction_phases、model_alias、fallback_used、fallback_chain）与实现一致
  - **验收**: 契约文档与代码实现对齐

---

## FR 覆盖映射表

| FR | 描述 | 任务 ID |
|----|------|---------|
| FR-000 | 引入 ContextBudgetPlanner | T004, T009 |
| FR-000a | BudgetPlanner 扣除系统块/Skill/Memory 预估 | T004, T005 |
| FR-000b | build_context() 接受 conversation_budget | T008 |
| FR-000c | Skill 注入纳入预算计算 | T006, T007, T009 |
| FR-000d | _fit_prompt_budget() 保留作为安全兜底 | T006, T010 |
| FR-000e | estimate_text_tokens() 中文感知版本 | T001, T003 |
| FR-000f | tiktoken 精确计算（可选） | T001, T003 |
| FR-001 | AliasRegistry 新增 compaction 别名 | T011 |
| FR-002 | compaction -> summarizer -> main fallback 链 | T013, T015 |
| FR-003 | Settings 前端展示 compaction 别名 | T014 |
| FR-004 | fallback 行为记录到压缩事件 | T013, T015 |
| FR-005 | 三层压缩结构 Recent/Compressed/Archive | T022, T026 |
| FR-006 | 各层 token 预算可配置 | T023, T026 |
| FR-007 | Compressed 层超出配额递归合并到 Archive | T026, T028 |
| FR-008 | Archive 层大小上限 + rolling_summary 持久化 | T025, T026 |
| FR-009 | CompiledTaskContext 增加 layers 字段 | T022 |
| FR-009a | SessionReplay 职责收窄 | T027, T028 |
| FR-010 | 廉价截断阶段 | T018, T020 |
| FR-011 | JSON 智能截断 | T016 |
| FR-012 | 截断后仍超预算才进入 LLM 摘要 | T020, T021 |
| FR-013 | 两阶段执行记录到事件 | T019, T020 |
| FR-014 | 后台启动压缩任务 | T029, T031 |
| FR-015 | 下一轮消费后台结果 + 等待机制 | T029, T031 |
| FR-016 | 超时/失败回退同步 | T029, T032 |
| FR-017 | 异步结果持久化 | T026, T029 |
| FR-018 | progress_note 工具定义 | T033, T034 |
| FR-019 | 进度笔记持久化到 Artifact Store | T034 |
| FR-020 | 上下文注入 ProgressNotes 系统块 | T035, T036 |
| FR-021 | 进度笔记自动合并 | T037 |
| FR-022 | 审计链不绕过 | T040 |
| FR-023 | Subagent 绕过所有压缩 | T039 |
| FR-024 | 配置通过 setup.review -> setup.apply | T041 |
| FR-025 | 前后端测试覆盖 | T003, T005, T010, T015, T021, T028, T032, T038, T039-T042 |

**覆盖率**: 26/26 FR = **100%**

---

## Dependencies & Execution Order

### Phase 依赖关系

```
Phase 0 (Foundational)     -- 无前置依赖，必须最先完成
  |
  +---> Phase 1 (US1: 压缩模型配置)        -- 依赖 Phase 0
  +---> Phase 2 (US3: 两阶段压缩)          -- 依赖 Phase 0
  |       |
  |       +---> Phase 3 (US2: 分层压缩)    -- 依赖 Phase 0 + Phase 2
  |               |
  |               +---> Phase 4 (US4: 异步压缩) -- 依赖 Phase 0 + Phase 3
  |
  +---> Phase 5 (US5: Worker 进度笔记)     -- 依赖 Phase 0，与 Phase 1-4 独立
  |
  +---> Phase 6 (Polish)                   -- 依赖 Phase 1-5 全部完成
```

### User Story 间依赖

| Story | 依赖 | 可否独立测试 |
|-------|------|-------------|
| US0 (全局预算) | 无 | 是 -- Phase 0 完成后即可验证预算分配 |
| US1 (压缩模型配置) | US0 | 是 -- 配置 compaction alias + fallback 可独立验证 |
| US3 (两阶段压缩) | US0 | 是 -- 超大消息截断 + LLM 摘要可独立验证 |
| US2 (分层压缩) | US0 + US3 | 是 -- 15 轮对话后验证三层结构 |
| US4 (异步压缩) | US0 + US2 | 是 -- 对比异步/同步延迟可独立验证 |
| US5 (进度笔记) | US0 | 是 -- Worker 写入/读取进度笔记可独立验证 |

### Story 内部并行机会

| Phase | 可并行任务 | 说明 |
|-------|-----------|------|
| Phase 0 | T001 / T003 / T005 可与 T004 并行 | Token 估算和 BudgetPlanner 测试不互相依赖 |
| Phase 1 | T011 / T014 可并行 | 后端 alias 注册和前端展示不同文件 |
| Phase 2 | T016 / T017 可并行 | JSON 截断和文本截断不同方法 |
| Phase 3 | T022 / T023 可并行 | ContextLayer 数据类和 Config 字段不同区域 |
| Phase 5 | T033 与 Phase 3/4 的所有任务并行 | 进度笔记工具定义独立于压缩改动 |

---

## Implementation Strategy

### MVP First (推荐)

1. 完成 Phase 0（全局预算统一 + Token 估算）-- **核心地基**
2. 完成 Phase 1（压缩模型配置）-- 成本可控
3. 完成 Phase 2（两阶段压缩）-- 性能提升
4. **STOP and VALIDATE**: 验证 SC-000/SC-001/SC-003
5. MVP 范围 = Phase 0 + Phase 1 + Phase 2 = US0 + US1 + US3

### Incremental Delivery

6. 追加 Phase 3（分层压缩）-- 信息密度提升
7. 并行追加 Phase 5（进度笔记）-- Worker 恢复能力
8. 追加 Phase 4（异步压缩）-- 延迟优化
9. Phase 6 Polish -- 回归 + 审计 + 文档

### 工作量估算

| Phase | 预估工时 | 任务数 |
|-------|---------|--------|
| Phase 0 | ~3 天 | 10 任务 |
| Phase 1 | ~1.5 天 | 5 任务 |
| Phase 2 | ~1.5 天 | 6 任务 |
| Phase 3 | ~2.5 天 | 7 任务 |
| Phase 4 | ~2 天 | 4 任务 |
| Phase 5 | ~2 天 | 6 任务 |
| Phase 6 | ~1 天 | 5 任务 |
| **合计** | **~13.5 天** | **43 任务** |
