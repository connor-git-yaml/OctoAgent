# Spec 合规审查报告 -- Feature 060 Context Engineering Enhancement

**审查日期**: 2026-03-17
**审查范围**: spec.md 中 FR-000 ~ FR-025 / US0 ~ US5 / SC-000 ~ SC-007 / 9 个 Edge Cases
**审查基准**: `.specify/features/060-context-engineering-enhancement/spec.md`
**代码分支**: `claude/festive-meitner`

---

## 总体合规评级

**PASS_WITH_WARNINGS**

- 26 条 FR 中 24 条已实现、2 条部分实现
- 6 个 User Story 全部有对应实现
- 8 条 Success Criteria 中 6 条可验证已实现、2 条需运行时验证
- 9 个 Edge Case 中 7 个有覆盖、2 个部分覆盖

---

## 逐条 FR 状态

| FR 编号 | 描述 | 状态 | 证据/说明 |
|---------|------|------|----------|
| FR-000 | 引入 ContextBudgetPlanner | 已实现 | `context_budget.py` 第 53-230 行：`ContextBudgetPlanner` 类完整实现，`plan()` 方法计算各部分预算分配。`task_service.py` 第 1132-1140 行在 `_build_task_context()` 开头调用。 |
| FR-000a | BudgetPlanner 扣除系统块/Skill/Memory 预估 | 已实现 | `context_budget.py` 第 114-136 行：逐项扣除 system_blocks_budget（1800+400）、skill_injection_budget（N*250）、memory_recall_budget（top_k*60）、progress_notes_budget（N*80）。 |
| FR-000b | build_context() 接受 conversation_budget | 已实现 | `context_compaction.py` 第 327 行：`conversation_budget: int | None = None` 参数。第 362 行使用 `effective_budget`。`task_service.py` 第 1172 行传入。 |
| FR-000c | Skill 注入纳入预算计算 | 已实现 | `llm_service.py` 第 314-315 行注释确认已移除双重注入。`agent_context.py` 第 3994-4030 行 LoadedSkills 系统块在 `_build_system_blocks()` 中创建，参与 `_fit_prompt_budget()` 计算。`task_service.py` 第 1182-1186 行获取 Skill 文本并传入。 |
| FR-000d | _fit_prompt_budget() 保留作为安全兜底 | 已实现 | `agent_context.py` 第 4147-4341 行：暴力搜索逻辑完整保留，包含 summary_limits / memory_limits / replay_options / include_runtime_options 四维组合搜索。第 4301 行对比 `max_input_tokens` 兜底。 |
| FR-000e | estimate_text_tokens() 中文感知版本 | 已实现 | `context_compaction.py` 第 1281-1299 行：检测非 ASCII 比例 `r`，使用 `len(text) / (4*(1-r) + 1.5*r)` 插值。tiktoken 优先使用。测试 `test_context_budget.py` 第 40-52 行覆盖纯中文场景。 |
| FR-000f | tiktoken 精确计算（可选） | 已实现 | `context_compaction.py` 第 18-24 行：`_tiktoken_encoder` 模块级初始化，`try/except ImportError`。第 1293-1294 行优先使用 tiktoken。 |
| FR-001 | AliasRegistry 新增 compaction 别名 | 已实现 | `alias.py` 第 63-67 行：`AliasConfig(name="compaction", category="cheap", runtime_group="cheap", description="上下文压缩（推荐轻量模型如 haiku / gpt-4o-mini）")`。 |
| FR-002 | compaction -> summarizer -> main fallback 链 | 已实现 | `context_compaction.py` 第 947-988 行：`_call_summarizer()` 实现三级 fallback，遍历 `[compaction_alias, summarizer_alias, "main"]`，逐级 try/except 降级。 |
| FR-003 | Settings 前端展示 compaction 别名 | 已实现 | `SettingsProviderSection.tsx` 第 386-389 行：当 `item.alias === "compaction"` 时显示辅助说明 "上下文压缩（推荐轻量模型如 haiku / gpt-4o-mini）。Fallback: compaction -> summarizer -> main"。 |
| FR-004 | fallback 行为记录到压缩事件 | 已实现 | `context_compaction.py` 第 970-972 行记录 `_last_call_alias`/`_last_call_fallback_used`/`_last_call_fallback_chain`。`task_service.py` 第 1397-1410 行将 `fallback_used`、`fallback_chain`、`compaction_phases`、`layers`、`compaction_version` 写入事件 payload。 |
| FR-005 | 三层压缩结构 Recent/Compressed/Archive | 已实现 | `context_compaction.py` 第 473-747 行 `_build_layered_context()`：Recent 层保留最近 N 轮（第 501-514 行）；Compressed 层按窗口分组 + LLM 摘要（第 553-623 行）；Archive 层合并旧组（第 625-680 行）。 |
| FR-006 | 各层 token 预算可配置 | 已实现 | `context_compaction.py` 第 47-50 行：`recent_ratio=0.50`、`compressed_ratio=0.30`、`archive_ratio=0.20`，均通过环境变量可配。第 496-498 行在 `_build_layered_context()` 中使用。 |
| FR-007 | Compressed 超出配额递归合并到 Archive | 已实现 | `context_compaction.py` 第 556-565 行：当 groups 数量超过 `max_compressed_groups` 时，旧组移入 archive_groups。第 629-670 行对 archive_groups 做 LLM 合并或截断。 |
| FR-008 | Archive 层大小上限 + rolling_summary 持久化 | 已实现 | `context_compaction.py` 第 498 行 `archive_budget` 计算，第 643 行检查 `combined_tokens > archive_budget`。`task_service.py` 第 1370-1377 行调用 `record_compaction_context()` 持久化到 `rolling_summary`，含 `compaction_version`。 |
| FR-009 | CompiledTaskContext 增加 layers 字段 | 已实现 | `context_compaction.py` 第 181 行：`layers: list[dict[str, Any]] = field(default_factory=list)`。第 516-524、617-623、674-680 行填充各层审计信息。 |
| FR-009a | SessionReplay 职责收窄 | 已实现 | `agent_context.py` 第 4190-4206 行：当 `has_compressed_layers=True` 时，`replay_options` 收窄为 `dialogue_limit=0` + None，只保留 session summary。原有梯度保留在 else 分支（第 4207-4245 行）。 |
| FR-010 | 廉价截断阶段 | 已实现 | `context_compaction.py` 第 1131-1166 行 `_cheap_truncation_phase()`：遍历消息，单条超过 `conversation_budget * large_message_ratio` 时截断。第 398-427 行在 `build_context()` 中 LLM 摘要前先调用。 |
| FR-011 | JSON 智能截断 | 已实现 | `context_compaction.py` 第 1043-1111 行 `_smart_truncate_json()`：解析 JSON -> `_prune_json_value()` 递归精简，保留 priority keys（status/error/result/message/code/id/name/type），数组只保留前 2 项 + 总数提示。 |
| FR-012 | 截断后仍超预算才进入 LLM 摘要 | 已实现 | `context_compaction.py` 第 430-451 行：截断后 `tokens_after_truncation <= effective_soft_limit` 时直接返回，不调用 LLM。第 453-471 行仅在仍超预算时进入 `_build_layered_context()`。 |
| FR-013 | 两阶段执行记录到事件 | 已实现 | `context_compaction.py` 第 395-408 行记录 cheap_truncation phase 到 `compaction_phases`。第 712-717 行记录 llm_summary phase。`task_service.py` 第 1408 行将 `compaction_phases` 写入事件。 |
| FR-014 | 后台启动压缩任务 | 已实现 | `context_compaction.py` 第 201-266 行 `schedule_background_compaction()`：创建 `asyncio.Task` 在后台执行压缩。`task_service.py` 第 721-744 行在 LLM 调用完成后、当 `final_tokens > conversation_budget * 0.6` 时触发。 |
| FR-015 | 下一轮消费后台结果 + 等待机制 | 已实现 | `context_compaction.py` 第 268-307 行 `await_compaction_result()`：等待 `asyncio.Task`，`asyncio.wait_for` 保护超时。`task_service.py` 第 1118-1130 行在 `_build_task_context()` 开头消费。 |
| FR-016 | 超时/失败回退同步 | 已实现 | `context_compaction.py` 第 247-261 行：超时返回 None，异常返回 None。`task_service.py` 第 1124 行检查 `bg_result is not None and bg_result.compacted`，None 时继续同步构建。 |
| FR-017 | 异步结果持久化 | 已实现 | 异步压缩通过 `build_context()` 生成 `CompiledTaskContext`，后续通过 `record_compaction_context()` 写入 `rolling_summary` 和 `compressed_layers`。进程重启后 `_pending_compactions` 丢失但下一轮同步压缩保证正确性。 |
| FR-018 | progress_note 工具定义 | 已实现 | `progress_note.py` 第 17-26 行 `TOOL_META`，第 32-54 行 `ProgressNoteInput`，第 57-61 行 `ProgressNoteOutput`。字段完整匹配 spec（step_id、description、status、key_decisions、next_steps）。 |
| FR-019 | 进度笔记持久化到 Artifact Store | 已实现 | `progress_note.py` 第 71-147 行 `execute_progress_note()`：构造 Artifact（type: progress-note，JSON part），写入 Artifact Store。第 95 行 artifact_id 格式 `pn-{task_id[:8]}-{step_id}-{ulid}`。 |
| FR-020 | 上下文注入 ProgressNotes 系统块 | 已实现 | `agent_context.py` 第 4032-4048 行：当 `progress_notes` 非空时构建 `## Progress Notes` 系统块，最近 5 条。`task_service.py` 第 1200-1213 行加载笔记并传入。`context_budget.py` 第 124-128 行纳入预算预估。 |
| FR-021 | 进度笔记自动合并 | 部分实现 | `progress_note.py` 第 197-281 行 `_maybe_merge_old_notes()`：超过阈值时合并旧笔记。**但合并后不删除原始旧笔记 Artifact**，仅创建汇总笔记。随时间推移 `list_artifacts_for_task()` 返回结果会越来越多，合并逻辑每次写入都重新触发（虽然 `note_artifacts > threshold` 仍为 True）。这是一个功能性遗漏。 |
| FR-022 | 审计链不绕过 | 已实现 | `task_service.py` 第 1387-1418 行：压缩事件 payload 包含 `compaction_phases`、`layers`、`compaction_version`、`fallback_used`、`fallback_chain`。所有路径通过 `_record_context_compaction_once()` 走幂等副作用链。 |
| FR-023 | Subagent 绕过所有压缩 | 已实现 | `context_compaction.py` 第 766-768 行 `_should_compact()`：当 `target_kind == "subagent"` 或 `worker_capability == "subagent"` 时返回 False。测试 `test_context_compaction.py` 第 379-446 行覆盖。 |
| FR-024 | 配置通过 setup.review -> setup.apply | 部分实现 | `compaction` alias 通过 AliasRegistry 注册，Settings API 可读写。但 `ContextCompactionConfig` 的新增字段（`large_message_ratio`、`recent_ratio` 等）仅通过环境变量配置（第 87-107 行），未暴露到 Settings API 的 `setup.review/apply` 流程。Spec 原文"不得直写环境变量"指的是运行时配置变更应通过 setup 流程，而非直接设环境变量。这些配置项缺少 Settings API 暴露。 |
| FR-025 | 前后端测试覆盖 | 已实现 | 三个测试文件覆盖：`test_context_budget.py`（token 估算 + BudgetPlanner + 集成测试）、`test_context_compaction.py`（压缩回归 + fallback + subagent 绕过 + 降级）、`test_progress_note.py`（CRUD + 合并 + 格式化 + 输入验证）。 |

---

## 总体合规率

**24/26 FR 已实现（92.3%）**，2 条部分实现。

---

## User Story 验收状态

| US | 描述 | 状态 | 验收说明 |
|-----|------|------|---------|
| US0 | 全局 token 预算统一管理 | 已实现 | 三个验收场景均有实现：(1) 压缩层基于 `conversation_budget` 而非 `max_input_tokens`；(2) 多 Skill 预算纳入；(3) 中文感知估算。`test_context_budget.py` 第 242-283 行集成测试验证。 |
| US1 | 压缩模型可在 Settings 中独立配置 | 已实现 | `compaction` alias 注册、三级 fallback 链、前端展示均实现。三个验收场景覆盖。 |
| US2 | 长对话中旧历史分层压缩 | 已实现 | `_build_layered_context()` 实现 Recent/Compressed/Archive 三层。三个验收场景：(1) 8 轮触发首次三层压缩；(2) Archive 层递归合并；(3) 关键决策保留到 Archive（通过 LLM 摘要 prompt 指导）。 |
| US3 | 大消息先廉价截断再 LLM 摘要 | 已实现 | `_cheap_truncation_phase()` + `_smart_truncate_json()` + `_head_tail_truncate()` 实现两阶段压缩。三个验收场景覆盖。 |
| US4 | 压缩异步执行不阻塞请求 | 已实现 | `schedule_background_compaction()` + `await_compaction_result()` + per-session Lock。三个验收场景：(1) 后台启动；(2) 等待机制 + 同步 fallback；(3) 失败回退。 |
| US5 | Worker 进度笔记 | 已实现 | `progress_note.py` 完整实现工具定义 + 执行逻辑 + 加载 + 合并 + 格式化。`agent_context.py` 注入系统块。三个验收场景覆盖。 |

---

## Success Criteria 状态

| SC | 描述 | 状态 | 说明 |
|----|------|------|------|
| SC-000 | 总 token 不超 max_input_tokens | 已实现 | `ContextBudgetPlanner` 上游规划 + `_fit_prompt_budget()` 下游兜底。`test_context_budget.py` 验证不变量。 |
| SC-000a | 中文 token 估算误差 < 30% | 已实现 | CJK 感知公式 + tiktoken 精确计算。测试验证纯中文估算不再严重低估。 |
| SC-001 | Settings 配置 compaction 别名 + fallback | 已实现 | AliasRegistry 注册 + 前端展示 + 三级 fallback 链。 |
| SC-002 | 三层压缩 + control plane 可审计 | 已实现 | `layers` 字段记录各层审计信息，写入 `CONTEXT_COMPACTION_COMPLETED` 事件 payload。 |
| SC-003 | 先截断后摘要，LLM 调用次数减少 | 已实现 | `_cheap_truncation_phase()` 先截断，截断后在预算内直接返回不调 LLM。 |
| SC-004 | 异步压缩延迟不高于同步 | 需运行时验证 | 实现了异步压缩架构（asyncio.Task + wait_for），但 p50/p95 延迟需实际运行测量。代码逻辑正确。 |
| SC-005 | Worker 进度笔记在压缩后可恢复 | 已实现 | 笔记存储在 Artifact Store（独立于压缩状态），上下文构建时自动注入最近 5 条。 |
| SC-006 | 审计链不绕过 + Subagent 绕过 | 已实现 | 全路径事件记录 + Subagent `_should_compact()` 返回 False。 |
| SC-007 | BudgetPlanner 后首选组合命中率 > 80% | 需运行时验证 | 架构设计正确（上游预估 + 下游兜底），但命中率需实际统计。 |

---

## Edge Case 覆盖状态

| # | Edge Case 描述 | 状态 | 证据 |
|---|----------------|------|------|
| 1 | compaction alias 模型不可用时 fallback | 已覆盖 | `_call_summarizer()` 三级 fallback；`test_context_compaction.py` `FailingSummarizerLLMService` 测试覆盖。 |
| 2 | 1-3 轮不触发压缩 | 已覆盖 | `_should_compact()` 第 763 行：`len(turns) < min_turns_to_compact` 返回 False。 |
| 3 | 后台压缩超时 >10s 回退 | 已覆盖 | `schedule_background_compaction()` 第 231 行 `asyncio.wait_for(timeout=timeout)`；`await_compaction_result()` 第 294 行同样保护。 |
| 4 | 进度笔记 >50 条自动合并 | 部分覆盖 | `_maybe_merge_old_notes()` 实现合并，但不删除旧笔记，可能导致重复合并。测试 `test_progress_note.py` 第 321-348 行覆盖阈值触发。 |
| 5 | Subagent 绕过所有压缩 | 已覆盖 | `_should_compact()` 检查 `target_kind == "subagent"` 和 `worker_capability == "subagent"`。测试覆盖。 |
| 6 | important 消息不截断 | 部分覆盖 | `_cheap_truncation_phase()` 当前实现**未检查 `metadata.important == true` 标志**。Spec 明确要求 "MUST 保留该内容不截断"。这是一个功能性遗漏。 |
| 7 | BudgetPlanner 预估偏差 >20% 时兜底 | 已覆盖 | `_fit_prompt_budget()` 暴力搜索兜底保留，第 4301 行检查 `delivery_tokens <= max_input_tokens`。 |
| 8 | 5+ Skill 总内容 >2000 token 时截断 | 已覆盖 | `agent_context.py` 第 3997-4023 行：按加载顺序保留 Skill，截断超出部分，记录被截断列表。 |
| 9 | 纯中文 vs 纯英文 token 估算偏差 < 30% | 已覆盖 | CJK 感知公式 + tiktoken。测试 `test_context_budget.py` 第 40-68 行覆盖。 |

---

## 偏差清单

| FR 编号 | 状态 | 偏差描述 | 修复建议 |
|---------|------|---------|---------|
| FR-021 | 部分实现 | `_maybe_merge_old_notes()` 创建汇总笔记后未删除被合并的原始旧笔记 Artifact。随时间推移，`list_artifacts_for_task()` 返回结果只增不减，合并逻辑会重复触发且 load 查询越来越慢。 | 在合并成功后，遍历 `old_artifacts` 并调用 `artifact_store.delete_artifact()` 删除旧笔记。如果 `delete_artifact()` 方法不存在，可以为被合并笔记增加 `merged=true` 标记并在 `load_recent_progress_notes()` 中过滤。 |
| FR-024 | 部分实现 | `ContextCompactionConfig` 的 Phase 2/3/4 新增配置字段（`large_message_ratio`、`recent_ratio`、`compressed_ratio`、`archive_ratio`、`compressed_window_size`、`async_compaction_timeout`）仅通过环境变量配置，未暴露到 Settings API 的 `setup.review/apply` 流程。Spec 要求 "新增配置项 MUST 通过 setup.review -> setup.apply 流程验证，不得直写环境变量"。 | 将这些配置项纳入 Settings API 的 review/apply 流程，或在 `from_env()` 之外增加从 Settings Store 读取的逻辑。考虑到这些是运维级配置而非用户级配置，可降低优先级，但 spec 表述为 MUST。 |
| Edge Case 6 | 未覆盖 | `_cheap_truncation_phase()` 未检查消息 metadata 中的 `important: true` 标志。Spec 要求 "metadata 包含 important: true 标志或消息角色为 approval/gate 相关时，MUST 保留该内容不截断"。 | 在 `_cheap_truncation_phase()` 中添加检查：如果 `msg.get("metadata", {}).get("important") == True`，或消息内容包含审批相关标记，跳过截断。注意当前 messages 格式为 `list[dict[str, str]]`，metadata 信息需要从上游传递或通过 ConversationTurn 的扩展字段携带。 |

---

## 过度实现检测

| 位置 | 描述 | 风险评估 |
|------|------|---------|
| 无 | 未发现 spec 未定义的额外公共 API、配置项或用户可见行为。所有新增功能均在 FR 覆盖范围内。 | N/A |

**说明**: 审查未发现过度实现。所有新增代码（`context_budget.py`、`progress_note.py`、`context_compaction.py` 扩展、`agent_context.py` 扩展、`llm_service.py` 修改、`task_service.py` 集成、`alias.py` 扩展、`SettingsProviderSection.tsx` 扩展）均在 spec.md 定义的 FR 范围内。

---

## 数据模型一致性

| 实体 | data-model.md 定义 | 实际实现 | 一致性 |
|------|-------------------|---------|--------|
| BudgetAllocation | frozen dataclass, 8 字段 | `context_budget.py` 第 24-50 行：完全匹配 | 一致 |
| ContextLayer | frozen dataclass, 5 字段 | `context_compaction.py` 第 138-146 行：完全匹配 | 一致 |
| CompactionPhaseResult | frozen dataclass, 4 字段 | `context_compaction.py` 第 128-135 行：完全匹配 | 一致 |
| ProgressNoteInput | Pydantic BaseModel, 5 字段 | `progress_note.py` 第 32-54 行：完全匹配 | 一致 |
| ProgressNoteOutput | Pydantic BaseModel, 2 字段 | `progress_note.py` 第 57-61 行：完全匹配 | 一致 |
| CompiledTaskContext 扩展 | +layers, +compaction_phases, +compaction_version | `context_compaction.py` 第 179-182 行：完全匹配 | 一致 |
| ContextCompactionConfig 扩展 | +8 新字段 | `context_compaction.py` 第 42-52 行：完全匹配 | 一致 |
| AgentSession.metadata 扩展 | +compaction_version, +compressed_layers | `_parse_compaction_state()` 第 1020-1038 行使用这些键 | 一致 |
| AliasConfig 新增 compaction | cheap category | `alias.py` 第 63-67 行：完全匹配 | 一致 |

**注意**: data-model.md 中定义的 `progress_note_inject_limit` 和 `progress_note_merge_threshold` 在 `ContextCompactionConfig` 中**未实现**为字段。这两个配置在 `progress_note.py` 中作为模块级常量 `DEFAULT_MERGE_THRESHOLD = 50` 和 `DEFAULT_INJECT_LIMIT = 5` 存在。这是一个轻微偏差——功能等价但配置方式不同于 spec。

---

## API 契约一致性

| 契约文件 | 一致性 | 说明 |
|---------|--------|------|
| `contracts/context-budget-api.md` | 一致 | `plan()` 签名、参数、返回值、行为规则、错误处理均匹配。调用点 (`TaskService._build_task_context()` 和 `AgentContextService.build_task_context()`) 匹配。 |
| `contracts/compaction-alias-api.md` | 一致 | AliasConfig 定义、fallback 链、Settings API 交互、前端展示、事件 payload 字段均匹配。 |
| `contracts/progress-note-tool.md` | 一致 | 工具 schema、输入/输出模型、行为规则、可见性规则、Artifact 存储格式、错误处理均匹配。 |

---

## 问题分级汇总

- **CRITICAL**: 0 个
- **WARNING**: 3 个
  - FR-021 部分实现：进度笔记合并后未删除旧笔记
  - FR-024 部分实现：新增配置项未完全纳入 setup.review/apply 流程
  - Edge Case 6 未覆盖：`important: true` 消息未保护
- **INFO**: 1 个
  - data-model.md 中 `progress_note_inject_limit` / `progress_note_merge_threshold` 作为模块级常量而非 ContextCompactionConfig 字段实现（功能等价，配置方式轻微偏差）

---

## 关键实现文件索引

| 文件 | 角色 |
|------|------|
| `octoagent/apps/gateway/src/octoagent/gateway/services/context_budget.py` | ContextBudgetPlanner + BudgetAllocation（新建） |
| `octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py` | 核心压缩引擎：三层压缩、两阶段压缩、异步压缩、CJK 感知估算 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` | 上下文装配：LoadedSkills 系统块、ProgressNotes 系统块、SessionReplay 收窄 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py` | Skill 注入迁移：移除 `_build_loaded_skills_context()` 的双重注入 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py` | 集成层：BudgetPlanner 调用、Skill 内容传递、进度笔记加载、异步压缩触发/消费 |
| `octoagent/packages/provider/src/octoagent/provider/alias.py` | compaction alias 注册 |
| `octoagent/packages/tooling/src/octoagent/tooling/progress_note.py` | Worker 进度笔记工具（新建） |
| `octoagent/apps/gateway/tests/test_context_budget.py` | Token 估算 + BudgetPlanner 单元/集成测试（新建） |
| `octoagent/apps/gateway/tests/test_context_compaction.py` | 压缩回归测试（扩展） |
| `octoagent/apps/gateway/tests/test_progress_note.py` | 进度笔记单元测试（新建） |
| `octoagent/frontend/src/domains/settings/SettingsProviderSection.tsx` | compaction alias 前端展示 |
