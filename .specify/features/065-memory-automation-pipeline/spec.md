# Feature Specification: Memory Automation Pipeline

**Feature Branch**: `claude/competent-pike`
**Created**: 2026-03-19
**Status**: Draft
**Input**: 实现 OctoAgent Memory 系统的自动化管线，将目前完全手动的记忆加工流程升级为自动化系统

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Agent 对话中主动保存重要信息 (Priority: P1)

用户与 Agent 对话过程中，Agent 识别到用户透露的重要个人信息、偏好、事实或决策（如"我下周要去东京出差"、"我的预算上限是 5 万"），Agent 主动调用 `memory.write` 将这些信息持久化到 Memory 系统，无需用户手动操作。下次对话时，Agent 能基于这些记忆提供个性化服务。

**Why this priority**: 这是整个自动化管线的基础入口。没有 Agent 主动写入能力，后续所有自动整理、派生提取都缺乏数据源。当前 Memory 系统只有读取工具，写入只能通过人工管理台或 Compaction Flush，无法在对话中实时捕获信息。

**Independent Test**: 启动 Agent 会话，告诉 Agent 一项个人偏好（如"我喜欢日式料理"），验证该信息通过 `memory.write` 工具被持久化为 SoR 记录；关闭会话后重新开启新会话，通过 `memory.recall` 验证该记忆可被检索到。

**Acceptance Scenarios**:

1. **Given** Agent 正在与用户进行自由对话, **When** 用户说"我的生日是 3 月 15 日", **Then** Agent 调用 `memory.write` 提交写入提案，经过 `propose_write` -> `validate_proposal` -> `commit_memory` 完整治理流程后，生成一条 `subject_key` 为 "用户生日" 的 SoR 记录，`evidence_refs` 指向对话消息 [CL-001]
2. **Given** `memory.write` 工具已注册在 memory 工具组中, **When** Agent 运行时加载工具列表, **Then** `memory.write` 与 `memory.read/search/citations/recall` 并列显示，且 Agent 可在工具调用中选择使用
3. **Given** 用户告知了一条已存在的信息（如之前已保存"喜欢咖啡"，现在说"我不喝咖啡了"）, **When** Agent 调用 `memory.write`, **Then** 系统执行 UPDATE 动作，生成新版本的 SoR 记录，旧版本标记为 SUPERSEDED
4. **Given** 用户提到敏感信息（如健康或财务相关）, **When** Agent 调用 `memory.write` 且 partition 属于 SENSITIVE_PARTITIONS, **Then** 写入提案须经过额外验证或拒绝，符合 Least Privilege 原则

---

### User Story 2 - Compaction Flush 后自动整理 Fragment (Priority: P1)

当对话上下文达到压缩阈值触发 Compaction 时，系统将对话摘要 Flush 为 Fragment。Flush 完成后，系统自动触发一次轻量级 Consolidate，仅处理本次 Flush 产生的 Fragment，将其中有价值的事实提取为 SoR 记录。用户无需手动执行 Consolidate 操作，对话中产生的重要信息自动沉淀为长期记忆。

**Why this priority**: 这是将"被动堆积 Fragment"转变为"自动加工 SoR"的关键闭环。当前 Flush 只写 Fragment，Fragment 必须手动 Consolidate 才能成为可用的 SoR 记忆。这导致大量对话信息停留在未加工状态，Memory 系统的实际价值被严重削弱。

**Independent Test**: 启动一段足够长的对话使其触发 Compaction Flush，验证 Flush 完成后自动执行 Consolidate，产生的 Fragment 被标记为 `consolidated_at`，且至少有一条新的 SoR 记录生成。

**Acceptance Scenarios**:

1. **Given** 对话上下文触发 Compaction 并成功 Flush 生成了 Fragment, **When** Flush 操作返回成功, **Then** 系统自动发起一次轻量 Consolidate，仅处理本次 Flush 产生的 Fragment（通过 `run_id` 或 `flush_idempotency_key` 关联）
2. **Given** 本次 Flush 产生了 3 条 Fragment, **When** 轻量 Consolidate 执行, **Then** LLM 分析这 3 条 Fragment 并决定 MERGE/UPDATE/KEEP_SEPARATE/SKIP，产出结果写入 SoR，Fragment 的 metadata 中记录 `consolidated_at` 时间戳
3. **Given** LLM 服务不可用（降级场景）, **When** 自动 Consolidate 失败, **Then** Fragment 保持未整理状态（无 `consolidated_at` 标记），系统记录警告日志，不影响正常对话流程，后续 Scheduler 可重试

---

### User Story 3 - 定期自动 Consolidate 积压 Fragment (Priority: P1)

系统定时运行 Consolidate 任务，扫描所有 scope 下未整理的 Fragment（无 `consolidated_at` 标记），批量调用 LLM 将其整合为 SoR 记录。这确保即使 Flush 后的即时 Consolidate 失败或遗漏，积压的 Fragment 最终都会被处理。

**Why this priority**: 这是自动化管线的"兜底保障"。Flush 后即时 Consolidate 可能因 LLM 不可用、网络抖动等原因失败，定时任务确保 Fragment 不会无限积压。同时也处理历史遗留的未整理 Fragment。

**Independent Test**: 在 Memory 中手动创建若干无 `consolidated_at` 标记的 Fragment，配置 Scheduler 的 Consolidate 任务并触发执行，验证这些 Fragment 被成功整理为 SoR 记录。

**Acceptance Scenarios**:

1. **Given** 系统存在 10 条未整理的 Fragment 分布在 2 个 scope 中, **When** Scheduler 定时 Consolidate 任务触发, **Then** 系统逐 scope 处理所有未整理 Fragment，产出 SoR 记录，并标记已处理的 Fragment
2. **Given** Scheduler 中注册了 memory consolidate 定时任务, **When** 系统启动（或从崩溃恢复）, **Then** 定时任务自动恢复调度，不丢失任务状态
3. **Given** 某个 scope 的 Consolidate 执行失败, **When** 任务运行出错, **Then** 该 scope 的 Fragment 保持未处理状态留待下次重试，不影响其他 scope 的处理，错误被记录到 Maintenance Run

---

### User Story 4 - Consolidate 后自动提取 Derived Memory (Priority: P2)

每次 Consolidate 成功产出新的 SoR 记录后，系统自动从这些 SoR 中提取结构化的 Derived Memory（entity/relation/category 类型），丰富 Memory 系统的知识图谱。例如，从"用户 3 月 15 日要去东京出差"中提取出 entity:"东京"、relation:"用户-出差-东京"、category:"差旅计划"。

**Why this priority**: Derived Memory 为高级检索和智能推荐提供结构化索引。当前 DerivedMemoryRecord 模型已定义但只在 import pipeline 触发，日常对话产生的 SoR 无法自动派生。这限制了 Memory 系统的深度利用。

**Independent Test**: 通过 Consolidate 生成若干 SoR 记录，验证系统自动提取出对应的 DerivedMemoryRecord，类型涵盖 entity/relation/category，并关联到源 SoR。

**Acceptance Scenarios**:

1. **Given** Consolidate 刚产出了一条新的 SoR（subject_key="用户差旅计划/东京"）, **When** Consolidate 完成后触发 Derived 提取, **Then** 系统生成至少一条 DerivedMemoryRecord，`derived_type` 为 entity/relation/category 之一，`source_fragment_refs` 指向源 Fragment
2. **Given** Consolidate 执行了 UPDATE 动作更新已有 SoR, **When** Derived 提取运行, **Then** 对应的 Derived 记录也被更新或追加，不产生重复
3. **Given** LLM 提取 Derived Memory 失败, **When** 提取出错, **Then** SoR 写入不受影响（Derived 提取是 best-effort），错误被记录

---

### User Story 5 - Memory Flush Prompt 优化（静默 Agentic Turn）(Priority: P2)

在 Compaction 触发之前，系统注入一个静默的 agentic turn，让 LLM 主动审视当前对话并决定哪些信息值得持久化，而非简单地对整段对话做机械摘要。这提升了 Flush 产出的 Fragment 质量，减少噪声信息的沉淀。

**Why this priority**: Fragment 质量直接决定后续 Consolidate 和 Derived 提取的效果。当前 Compaction Flush 是对整段对话做摘要，包含大量无价值的过程信息。优化 Flush Prompt 让 LLM 主动选择性保存，是提升整个管线质量的杠杆点。

**Independent Test**: 对同一段对话，分别用原始 Flush 和优化后的 Flush Prompt 生成 Fragment，对比两组 Fragment 的信息密度和与用户长期偏好的相关性。

**Acceptance Scenarios**:

1. **Given** 对话上下文即将触发 Compaction, **When** 系统注入静默 agentic turn, **Then** LLM 输出一份结构化的"值得记住的信息列表"，而非对话的全文摘要
2. **Given** 对话中包含大量过程性讨论和少量关键结论, **When** 优化后的 Flush 执行, **Then** 产出的 Fragment 主要包含关键结论和用户偏好，过程性讨论被过滤
3. **Given** 对话中无明显值得持久化的信息, **When** 静默 agentic turn 判断无需保存, **Then** 系统可以跳过 Flush 或生成极简 Fragment，不浪费存储

---

### User Story 6 - Retrieval 增加 Reranker 精排 (Priority: P2)

当 Agent 检索 Memory（通过 `memory.recall` 或 Prefetch Recall）时，初始粗排结果经过轻量级本地 Reranker 模型精排，返回与查询意图更匹配的记忆。用户感受到 Agent 的回忆更精准，减少无关记忆的干扰。

**Why this priority**: 当前检索依赖 embedding 相似度 + keyword overlap 的启发式策略，对复杂语义查询的命中率不够理想。Reranker 是提升检索质量的成熟方案，且轻量级本地 Reranker 模型体积小、推理快，可本地部署。

**Independent Test**: 构造一组已知答案的查询-记忆对，分别用有无 Reranker 的检索流程运行，对比 Top-K 命中率和排序质量。

**Acceptance Scenarios**:

1. **Given** Memory 中存在多条语义相近但主题不同的 SoR 记录, **When** Agent 发起一次 recall 查询, **Then** 经过 Reranker 精排后，与查询意图最匹配的记录排在前列
2. **Given** Reranker 模型不可用（未下载或推理失败）, **When** recall 查询执行, **Then** 系统降级到原有的 heuristic rerank 模式，不影响检索功能
3. **Given** recall 返回少于 2 条候选结果, **When** Reranker 被调用, **Then** 系统跳过 rerank 步骤直接返回，避免无意义的精排开销

---

### User Story 7 - Theory of Mind 推理（用户心智模型）(Priority: P3)

系统从对话中推断用户的意图、偏好、知识水平和情绪状态，生成 ToM（Theory of Mind）类型的 Derived Memory。Agent 利用这些推断在后续对话中提供更贴合用户认知水平和当前情绪的响应。

**Why this priority**: ToM 是最高级的个性化能力，需要前面所有管线（write -> consolidate -> derive）稳定运行后才有意义。属于"锦上添花"的增强功能。

**Independent Test**: 与 Agent 进行一段包含明显情绪和知识水平信号的对话（如用户表现出对技术的不熟悉），验证系统生成 `derived_type=tom` 的 Derived Memory 记录，且后续对话中 Agent 的回复风格有所调整。

**Acceptance Scenarios**:

1. **Given** 用户在对话中反复使用非技术语言描述问题, **When** ToM 推理运行, **Then** 系统生成一条 `derived_type=tom` 的记录，标注用户在该领域的知识水平为"初学者"
2. **Given** 用户在对话中表达了明确的偏好倾向, **When** ToM 推理运行, **Then** 系统生成偏好类型的 ToM 记录，后续 recall 可检索到

---

### User Story 8 - Temporal Decay + MMR 去重 (Priority: P3)

检索时，较旧的记忆获得较低的相关性分数（时间衰减），同时通过 MMR（Maximal Marginal Relevance）算法去除语义重复的结果，确保召回结果既新鲜又多样。

**Why this priority**: 随着 Memory 规模增长，旧信息噪声和重复记忆会显著降低检索质量。但在 MVP 阶段 Memory 规模有限，优先级低于核心管线建设。

**Independent Test**: 构造一组包含新旧版本和语义重复的记忆数据，执行 recall 查询，验证新记忆排名高于旧记忆，且重复记忆被合并显示。

**Acceptance Scenarios**:

1. **Given** 同一主题存在 3 个月前和昨天的两条 SoR 记录, **When** recall 查询该主题, **Then** 昨天的记录得分高于 3 个月前的记录
2. **Given** 检索返回 5 条语义高度相似的结果, **When** MMR 去重应用, **Then** 返回的 Top-K 结果中无语义重复，覆盖更多不同的主题角度

---

### User Story 9 - 用户画像自动生成 (Priority: P3)

系统定期从 SoR 和 Derived Memory 中聚合生成用户画像摘要（profile summary），涵盖用户的基本信息、偏好、工作领域、常用工具等维度。Agent 在每次会话开始时可快速加载画像，无需重复检索散落的记忆碎片。

**Why this priority**: 用户画像是 Memory 系统的最终"消费形态"，依赖所有前序管线的成熟运行。在 Phase 3 实现更合理。

**Independent Test**: 在 Memory 中积累足够的 SoR 和 Derived 记录后，触发画像生成任务，验证产出的 profile 包含用户的核心维度信息，且可通过 API 读取。

**Acceptance Scenarios**:

1. **Given** Memory 中已有 50+ 条 SoR 记录和相关 Derived 记录, **When** 画像生成任务触发, **Then** 系统产出一份结构化的用户画像，涵盖 profile partition 的核心维度
2. **Given** 新的 SoR 产生了与画像矛盾的信息, **When** 下次画像更新运行, **Then** 画像中对应维度被更新为最新信息

---

### Edge Cases

- **memory.write 并发冲突**: Agent 的多个工具调用同时尝试写入同一 `subject_key` 时，系统如何处理？须通过 `expected_version` 乐观锁或队列串行化保证一致性 [关联 FR-003]
- **Flush 后即时 Consolidate 超时**: LLM 调用可能超时或响应缓慢，不能阻塞 Compaction 主流程的返回。Consolidate 须异步执行，Flush 返回后不等待 Consolidate 结果 [关联 US-2]
- **Scheduler Consolidate 与即时 Consolidate 并发**: 定时任务和 Flush 后的即时任务同时处理同一批 Fragment 时，须通过幂等机制（`consolidated_at` 标记）避免重复处理 [关联 FR-007]
- **敏感分区写入**: `memory.write` 尝试写入 HEALTH/FINANCE 分区时，是否需要用户审批？须与 Policy Engine 联动 [关联 US-1 场景 4]
- **LLM 服务全面不可用**: Consolidate、Derived 提取、ToM 推理均依赖 LLM。当 LLM 服务持续不可用时，系统须优雅降级：Fragment 继续累积，Scheduler 标记失败并在恢复后重试，不影响 Agent 正常对话 [关联 FR-010]
- **Memory 存储空间耗尽**: Fragment 持续写入但 Consolidate 从未成功时，Fragment 可能无限累积。须设定每 scope 的 Fragment 上限或告警阈值 [关联 FR-011]
- **Reranker 模型加载失败**: 首次使用时 Reranker 模型可能未就绪。须提供自动下载或手动配置路径的引导 [关联 US-6 场景 2]
- **空对话 Flush**: 对话中无实质内容（如仅寒暄）时，Flush 产出的 Fragment 无价值。优化后的 Flush Prompt 应能识别并跳过 [关联 US-5 场景 3]

## Requirements *(mandatory)*

### Functional Requirements

**Phase 1 -- 最小闭环**

- **FR-001**: 系统 MUST 提供 `memory.write` 工具，注册在 memory 工具组中，与 `memory.read/search/citations/recall` 并列，供 Agent 在对话中主动写入记忆
- **FR-002**: `memory.write` MUST 接受 `subject_key`、`content`、`partition`、`evidence_refs` 参数，并走完整的治理流程（`propose_write` -> `validate_proposal` -> `commit_memory`），产出 SoR 记录（`evidence_refs` 关联到对话消息作为溯源依据，不额外创建 Fragment）[CL-001]
- **FR-003**: `memory.write` MUST 支持 ADD 和 UPDATE 两种动作；UPDATE 时工具内部自动查询当前 SoR 版本并使用 `expected_version` 乐观锁保证一致性，Agent 无需手动传入版本号 [CL-002]
- **FR-004**: 系统 MUST 在 Compaction Flush 成功写入 Fragment 后，自动触发一次轻量 Consolidate，仅处理本次 Flush 产出的 Fragment
- **FR-005**: Flush 后的自动 Consolidate MUST 异步执行，不阻塞 Compaction 主流程的返回
- **FR-006**: 自动 Consolidate MUST 复用现有的 scope 级别 consolidate 流程（LLM 分析 -> propose_write -> validate -> commit），并将已处理的 Fragment 标记 `consolidated_at`
- **FR-007**: 系统 MUST 在 AutomationScheduler 中注册 memory consolidate 定时任务，定期处理所有 scope 下未整理的 Fragment（无 `consolidated_at` 标记）
- **FR-008**: 定时 Consolidate 任务 MUST 支持系统重启后自动恢复调度，不丢失任务配置
- **FR-009**: 定时 Consolidate MUST 逐 scope 处理，单个 scope 失败不影响其他 scope 的执行
- **FR-010**: 当 LLM 服务不可用时，所有依赖 LLM 的操作（Consolidate、Derived 提取）MUST 优雅降级，记录失败日志并保持数据未处理状态留待重试
- **FR-011**: 系统 SHOULD 对每个 scope 的未整理 Fragment 数量设置告警阈值，当积压超过阈值时生成系统事件

**Phase 2 -- 质量提升**

- **FR-012**: 系统 SHOULD 在每次 Consolidate 成功产出新 SoR 后，自动提取 Derived Memory（entity/relation/category 类型的 DerivedMemoryRecord）
- **FR-013**: Derived Memory 提取 SHOULD 为 best-effort，提取失败不影响 SoR 写入结果
- **FR-014**: 系统 SHOULD 在 Compaction 触发前注入静默 agentic turn，让 LLM 主动选择值得持久化的信息，替代当前的全文摘要式 Flush
- **FR-015**: 优化后的 Flush Prompt SHOULD 能在对话无有价值信息时跳过或产出极简 Fragment
- **FR-016**: 系统 SHOULD 支持 Reranker 精排模式，接入轻量级本地 Reranker 模型，在 recall 粗排后进行精排
- **FR-017**: Reranker MUST 支持降级：模型不可用时回退到现有 heuristic rerank 模式
- **FR-018**: 当 recall 候选结果少于 2 条时，系统 SHOULD 跳过 Reranker 步骤

**Phase 3 -- 高级功能**

- **FR-019**: 系统 MAY 支持 Theory of Mind 推理，从对话中推断用户意图、偏好、知识水平，生成 `derived_type=tom` 的 DerivedMemoryRecord
- **FR-020**: 系统 MAY 支持 Temporal Decay，在检索打分中对旧记忆施加时间衰减因子
- **FR-021**: 系统 MAY 支持 MMR 去重，在 recall Top-K 结果中去除语义高度重复的条目
- **FR-022**: 系统 MAY 支持用户画像自动生成，定期从 SoR/Derived 聚合产出 profile 摘要

### Key Entities

- **FragmentRecord**: 过程性记忆对象（append-only）。由 Compaction Flush 产出（`memory.write` 直接走 SoR 治理流程，不产出 Fragment [CL-001]），是记忆加工的原始输入。关键属性：fragment_id、scope_id、partition、content、metadata（含 consolidated_at 标记）、evidence_refs
- **SorRecord（Source of Record）**: 权威记忆记录。由 Consolidate 或 memory.write 经治理流程产出，是 Memory 系统的核心事实层。关键属性：memory_id、scope_id、partition、subject_key、content、version、status（CURRENT/SUPERSEDED/DELETED）
- **VaultRecord**: 敏感分区记录。HEALTH/FINANCE 等敏感数据的独立存储，需额外授权访问
- **DerivedMemoryRecord**: 派生层记录。从 SoR 中自动提取的结构化知识（entity/relation/category/tom）。关键属性：derived_id、derived_type、subject_key、summary、payload、confidence、source_fragment_refs
- **WriteProposalDraft**: 写入提案草案。memory.write 和 Consolidate 的中间产物，经 validate/commit 流程后写入 SoR
- **MemoryMaintenanceCommand/Run**: 维护命令和执行记录。FLUSH/CONSOLIDATE/COMPACT 等操作的审计和追踪载体
- **AutomationJob**: 自动化任务定义。Scheduler 中注册的定时 Consolidate 任务配置

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Agent 在对话中识别到用户透露的重要信息后，能在 3 秒内完成 `memory.write` 调用并持久化为 SoR 记录，成功率不低于 95%
- **SC-002**: Compaction Flush 后的自动 Consolidate 在 30 秒内完成（不含 LLM 推理等待时间），成功处理本次 Flush 的 Fragment 比例不低于 80%
- **SC-003**: 定时 Consolidate 运行后，系统中未整理 Fragment 的积压量稳定在合理范围（单 scope 不超过 50 条未整理 Fragment）
- **SC-004**: 新会话开启时，Agent 能通过 recall 检索到之前通过 `memory.write` 保存的信息，recall 命中率不低于 90%
- **SC-005**: 在 LLM 服务中断并恢复后，系统能在下一次 Scheduler 运行周期内自动处理积压的未整理 Fragment，无需人工干预
- **SC-006**: Reranker 精排后的 recall Top-3 命中率相比 heuristic rerank 提升不低于 10 个百分点（Phase 2）
- **SC-007**: 所有自动化操作（write、consolidate、derive）在 Memory Maintenance Run 中留有完整审计记录，可通过管理台查看

## Dependencies & Assumptions

### Dependencies

- **现有 Memory 基础设施**: FragmentRecord、SorRecord、DerivedMemoryRecord 数据模型和对应的存储层已实现且稳定运行
- **Compaction 机制**: Compaction Flush 流程已实现，能在对话上下文达到阈值时生成 Fragment 并持久化
- **治理流程**: `propose_write` -> `validate_proposal` -> `commit_memory` 三阶段写入治理流程已实现且可复用
- **AutomationScheduler**: 系统级定时任务调度服务已实现，支持持久化 AutomationJob 配置和系统重启后自动恢复
- **LiteLLM Proxy**: 模型网关已部署，可提供 Consolidate、Derived 提取等操作所需的 LLM 推理能力
- **向量检索基础设施**: embedding 生成和向量检索（LanceDB）已实现，Reranker 精排在此基础上叠加

### Assumptions

- 单用户场景（Personal AI OS），不需要考虑多租户隔离
- LLM 服务为外部依赖，存在不可用的可能，所有依赖 LLM 的自动化操作须支持降级和重试
- Reranker 模型需本地部署，首次使用时可能需要下载模型文件
- Memory 数据量在可预见的 MVP 阶段内不超过单 SQLite 数据库的承载能力

## Clarifications

### Session 2026-03-19

以下为需求澄清阶段识别并解决的歧义点。

#### CL-001: `memory.write` 产出物 -- Fragment 与 SoR 的关系 [AUTO-CLARIFIED]

**问题**: FR-002 规定 `memory.write` 产出"SoR 记录和对应的 Fragment"，但现有治理流程（`propose_write` -> `validate_proposal` -> `commit_memory`）只产出 SoR，不自动产出 Fragment。spec 中 US-1 的 "生成一条 SoR 记录和对应的 Fragment" 含义不明确 -- 是 `memory.write` 需要额外创建一条 Fragment 作为证据链底层记录，还是 Agent 直接写 SoR 足矣？

**解决**: `memory.write` 走治理流程后直接产出 SoR 记录，**不额外创建 Fragment**。理由：(1) Fragment 是过程性记忆（对话摘要、导入片段），而 `memory.write` 是 Agent 主动识别的结构化事实，天然就是 SoR 层级；(2) `evidence_refs` 已提供溯源能力，指向对话消息或 artifact，无需额外 Fragment 中间层；(3) 现有 `propose_write` -> `validate_proposal` -> `commit_memory` 流程已经完备，无需修改核心治理层。FR-002 的描述应修正为"产出 SoR 记录（通过治理流程），`evidence_refs` 关联到对话消息"。

#### CL-002: `memory.write` 的 `expected_version` 获取方式 [AUTO-CLARIFIED]

**问题**: FR-003 要求 UPDATE 时使用 `expected_version` 乐观锁，但 Agent 调用 `memory.write` 时如何获取当前版本号？当前 `memory.read` 返回的 `MemorySubjectHistoryDocument` 包含 SoR 版本信息，但 Agent 是否需要先 read 再 write？还是 `memory.write` 内部自动查询当前版本？

**解决**: `memory.write` 工具在 UPDATE 模式下**内部自动查询当前 SoR 的 version**，Agent 无需手动传 `expected_version`。理由：(1) 要求 LLM 管理版本号是不合理的认知负担，极易出错；(2) 从用户调用 `memory.write(action=update, subject_key=...)` 到实际 `propose_write` 之间是同步的原子流程，自动查询无竞态风险；(3) 在极端并发场景下，乐观锁冲突由 `validate_proposal` 捕获并返回错误即可。工具签名中 `expected_version` 设为内部参数，不暴露给 Agent。

#### CL-003: 定时 Consolidate 的调度间隔与注册方式 [AUTO-CLARIFIED]

**问题**: FR-007/FR-008 要求在 AutomationScheduler 中注册定时 Consolidate 任务，但未指定：(1) 默认调度间隔是多少？(2) 是通过硬编码在系统启动时自动注册，还是通过 AutomationJob 持久化配置？(3) 用户是否可以通过管理台调整间隔？

**解决**: 采用**系统内置 AutomationJob + 用户可通过管理台调整**的方案。具体：(1) 默认调度间隔为 **每 4 小时执行一次**（cron: `0 */4 * * *`），兼顾及时性和 LLM 成本；(2) 系统首次启动时，若不存在 memory-consolidate 类型的 AutomationJob，则自动创建一条默认配置并持久化；(3) 用户可通过管理台（Control Plane）或 API 调整间隔、启用/禁用；(4) 重启后通过 `AutomationSchedulerService.startup()` 从持久化的 AutomationJob 恢复调度。这与现有 AutomationScheduler 的设计模式一致。

#### CL-004: scope 级别 consolidate 逻辑的归属位置 [AUTO-CLARIFIED]

**问题**: FR-006 要求"复用现有的 scope 级别 consolidate 流程"，但当前该逻辑实现在面向管理台的高层服务中，而非核心 Memory 服务层。管理台服务依赖 LLM 调用接口。Flush 后的自动 Consolidate 和 Scheduler Consolidate 应调用哪个层级的接口？

**解决**: **将 scope 级别 consolidate 的核心逻辑下沉到独立的 Consolidation 服务层**（或从管理台服务中提取为可复用的公共方法），使其可被三个入口调用：(1) 管理台手动触发；(2) Flush 后异步触发；(3) Scheduler 定时触发。新的 Consolidation 服务层接受 Memory 核心服务 + LLM 调用接口作为依赖，不绑定管理台上下文。这避免了在 gateway 层直接依赖管理台服务来执行自动 Consolidate 的不合理依赖路径。

#### CL-005: Flush 后即时 Consolidate 的触发机制 [AUTO-CLARIFIED]

**问题**: FR-004/FR-005 要求 Flush 后自动触发异步 Consolidate，但未明确：(1) 在哪个组件中触发？当前 Flush 发生在 Compaction 持久化流程中；(2) "异步"的具体含义 -- 是 fire-and-forget 后台任务，还是提交到某个任务队列？(3) 如果后台任务异常了怎么处理？

**解决**: 在 Compaction Flush 持久化流程成功返回 `run_id` 后，通过 **fire-and-forget 后台任务** 启动轻量 Consolidate。具体：(1) 在 Flush 持久化流程末尾，若 Flush 成功（返回有效 `run_id`），则启动后台任务调用 Consolidation 服务仅处理本次 Flush 产出的 Fragment；(2) 后台任务内部自行捕获所有异常，记录 warning 日志和 MemoryMaintenanceRun（状态 FAILED），不抛出；(3) 失败的 Fragment 保持未 consolidated 状态，由 Scheduler 兜底处理。这与 FR-005 "不阻塞主流程" 一致，且符合现有系统的异步后台任务模式。
