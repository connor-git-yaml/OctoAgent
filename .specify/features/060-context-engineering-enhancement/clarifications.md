# Feature 060 — 需求澄清记录

## Session 2026-03-17

### 澄清分析总览

| 类别 | 状态 | 说明 |
|------|------|------|
| 功能范围与行为 | Clear | 6 个 User Story 边界明确，优先级排列合理 |
| 领域与数据模型 | Partial | Compressed 层话题分组模型、ProgressNote 实体可见性需明确 |
| 交互与 UX 流程 | Clear | Settings compaction 别名配置路径清晰 |
| 非功能质量属性 | Partial | 异步压缩并发安全、BudgetPlanner 预估准确度兜底策略需明确 |
| 集成与外部依赖 | Clear | 与 034、AliasRegistry、SkillDiscovery 的边界已在 spec 中描述 |
| 边界条件与异常处理 | Clear | Edge Cases 章节覆盖全面（12 个场景） |
| 术语一致性 | Partial | `conversation_budget` vs `max_input_tokens` 在不同上下文中的使用需统一 |

---

### 自动解决的澄清

| # | 问题 | 自动选择 | 理由 |
|---|------|---------|------|
| 1 | `ContextBudgetPlanner` 应放在哪个模块？spec 提到"新增 `context_budget.py`（或在 `context_compaction.py` 中）"，存在二义性。调用方在 `task_service.py`，消费方跨 `ContextCompactionService` 和 `AgentContextService` | [AUTO-CLARIFIED: 独立模块 `context_budget.py` 放在 `gateway/services/` 下] | BudgetPlanner 是压缩层和装配层的上游协调者，不应属于任一下游。独立模块符合单一职责，且避免 `context_compaction.py` 继续膨胀（当前已 597 行）。`task_service.py._build_task_context()` 在调用 `build_context()` 和 `build_task_context()` 之前先调用 `ContextBudgetPlanner.plan()`，将 `BudgetAllocation` 分别传递给两个下游。 |
| 2 | `_fit_prompt_budget()` 的暴力搜索在 BudgetPlanner 生效后，参数空间应扩展还是缩减？当 Skill 注入移入 `_build_system_blocks()` 后，是否需要增加 Skill 截断级别参数？ | [AUTO-CLARIFIED: 参数空间不扩展，Skill 截断由 BudgetPlanner 预分配处理] | BudgetPlanner 在上游已为 Skill 分配预算（`skill_injection_budget`），`_build_system_blocks()` 中的 LoadedSkills 系统块按预算截断。`_fit_prompt_budget()` 不需要为 Skill 增加新的搜索维度，保持现有组合空间作为纯安全兜底。这与 spec FR-000d 的设计意图一致："预估不准时的安全网"。 |
| 3 | Compressed 层的话题分组算法——spec 说"用户消息开启新话题"，但实际对话中用户常在同一消息中切换话题。分组粒度和边界如何定义？ | [AUTO-CLARIFIED: MVP 使用轮次级分组，不做话题语义分割] | MVP 阶段不引入 NLP 话题检测（增加延迟和复杂度）。分组策略：每个 user+assistant 对为一个原子单元，Compressed 层以固定 window（如 3-4 轮为一组）进行摘要，不做跨消息的话题语义分割。这与 Agent Zero 的 Topic 层级类似——它也是按消息轮次划分而非语义分析。后续可升级为基于 embedding 的话题边界检测。 |
| 4 | 异步压缩（FR-014/015）中，后台 `asyncio.Task` 写入 `AgentSession.rolling_summary` 和 `metadata["compressed_layers"]` 时，与下一轮请求的 `record_response_context()` 可能并发写入同一个 session。需要什么并发控制策略？ | [AUTO-CLARIFIED: 使用 per-session asyncio.Lock，写入前获取锁] | 当前系统是单进程 async 架构，`asyncio.Lock` 足够（无需数据库级锁）。为每个活跃 AgentSession 维护一个 `_compaction_locks: dict[str, asyncio.Lock]`，后台压缩和 turn hook 的 rolling_summary 写入前都获取同一把锁。锁粒度为 session 级，不会影响不同 session 的并行。超时场景（10 秒）由 `asyncio.wait_for` 保护，超时后释放锁并回退同步路径。 |
| 5 | `progress_note` 的可见性：Subagent 是否能看到 Worker 的进度笔记？Butler 是否能看到 Worker 的进度笔记？ | [AUTO-CLARIFIED: Worker 自身可见 + Butler 通过 control plane 可查，Subagent 不可见] | 进度笔记绑定 `task_id + agent_session_id`（FR-019），上下文构建时按当前 session 过滤注入。Subagent 有独立 session，自然看不到 Worker 笔记（且 Subagent 绕过压缩机制，不需要恢复上下文）。Butler 不在 Worker 的 session 中运行，不会自动注入 Worker 笔记，但可通过 control plane API 查询 Worker 的 Artifact（type: `progress-note`）。这与 Constitution 原则 8（Observability is a Feature）对齐。 |

---

### CRITICAL 问题

无 CRITICAL 问题。全部 5 个歧义点已自动解决。

分析理由：
- 所有问题均为实现层面的技术决策，不涉及数据安全/隐私合规
- 不影响功能范围的增删（功能列表在 User Story 中已明确）
- 各选项的架构影响差异在可控范围内（均为模块内部设计，不影响跨层接口契约）

---

### 补充发现（非歧义，但值得记录）

#### 1. Skill 注入移动的影响面

spec FR-000c 要求将 `LLMService._build_loaded_skills_context()` 移到 `_build_system_blocks()` 中。当前 `_build_loaded_skills_context()` 在 `llm_service.py:314-317` 只在 `_try_call_with_tools()` 路径（有挂载工具时）才执行。移到 `_build_system_blocks()` 后，Skill 内容将在所有请求中注入（包括无工具的纯聊天）。

**影响评估**：这是正确的行为——已加载的 Skill 应该在所有上下文中可见，而非仅在有工具时可见。但需注意：
- `_build_system_blocks()` 目前不接收 `loaded_skill_names` 参数，需要从 session metadata 传入
- 需确保 `_build_loaded_skills_context()` 的调用从 `LLMService._try_call_with_tools()` 中移除，避免双重注入

#### 2. `estimate_text_tokens()` 的调用点广度

当前 `estimate_text_tokens()` 在以下位置被调用：
- `context_compaction.py` 内部（压缩决策、批次切分）
- `agent_context.py` 的 `_fit_prompt_budget()` 中通过 `estimate_messages_tokens()`

升级为中文感知版本后，两处都会自动受益。但 `_chunk_segments_by_token_budget()` 中的 `char_budget = max(256, transcript_budget * 4)` 硬编码了 4 chars/token 的假设（第 456 行），需要同步修正为动态计算。

#### 3. rolling_summary 语义升级的迁移兼容

spec 将 `rolling_summary` 从"全部历史的扁平摘要"升级为"Archive 层骨架摘要"。现有 session 的 `rolling_summary` 内容是旧格式。需要考虑：
- 读取旧 session 的 `rolling_summary` 时，如何区分旧格式和新 Archive 层格式
- 建议：在 `AgentSession.metadata` 中增加 `compaction_version` 字段（`"v1"` = 034 扁平摘要，`"v2"` = 060 Archive 层），读取时按版本解析

#### 4. `ContextCompactionConfig` 的配置迁移

当前配置通过环境变量加载（`from_env()`），spec 中的新配置项（`compaction_alias`、`large_message_ratio`、`json_smart_truncate`、层级比例等）也需要通过此模式注入。但 FR-024 要求"通过 `setup.review → setup.apply` 流程验证"。需确认两种配置入口的优先级：
- 环境变量（运维层面）
- Settings API / setup.apply（用户层面）
- 建议：Settings API 写入 DB，优先级高于环境变量默认值
