# Feature Specification: Memory 提取质量、索引利用与审计优化

**Feature Branch**: `claude/competent-pike`
**Feature ID**: 066-memory-quality-indexing-audit
**Created**: 2026-03-19
**Status**: Draft
**Input**: 基于四系统对比调研和用户需求，优化 Memory 系统的三个维度——提取质量（Consolidation 策略与覆盖范围）、索引利用（Agent 工具层查询能力）、审计机制（用户事后查看/编辑/删除记忆）
**调研基础**: `research/tech-research.md`（技术调研，方案 A 增量式扩展）

---

## 术语表

| 术语 | 含义 |
|------|------|
| SoR（Source of Record） | 现行事实记录。经系统整理确认的一条长期记忆，代表当前被认可的事实版本。每条 SoR 有唯一主题标识（subject_key）、生命周期状态和版本号 |
| partition（记忆分区） | 记忆的顶层归类维度，将记忆按领域分组（如核心信息、工作、健康、财务等），便于按领域浏览和筛选 |
| scope（记忆范围） | 记忆的归属范围，标记一条记忆属于哪个项目或全局上下文 |
| fragment（信息片段） | 对话中提取的原始信息碎片，尚未经过整理确认。是系统整理记忆的输入素材 |
| consolidation（记忆整理） | 系统将零散的信息片段（fragment）整理归纳为长期记忆（SoR）的自动化流程 |
| derived（派生记忆） | 从 SoR 进一步推导生成的结构化记忆，如用户画像（Profile）、心理模型（Theory of Mind）等 |
| superseded（已替代） | SoR 的历史状态。当一条记忆被编辑或更新后，旧版本标记为 superseded，保留在系统中供追溯 |
| subject_key（主题标识） | 一条记忆的结构化标签，标识这条记忆"关于什么"，如"妈妈/兴趣爱好"、"技术偏好/编程语言" |
| recall（记忆召回） | Agent 在执行任务时从记忆系统中检索相关信息的过程 |
| evidence_refs（证据引用） | 一条 SoR 所引用的原始信息来源链接，用于追溯记忆的依据 |

---

## User Scenarios & Testing

### User Story 1 - Agent 浏览记忆目录 (Priority: P1)

Agent 在执行任务时，需要了解"我记住了用户哪些信息"，而不仅仅通过关键词搜索碰运气。Agent 应能按 subject_key 前缀、partition、scope 等维度浏览记忆的结构化目录，获取分组统计和概览，从而更精准地决定需要深入读取哪些记忆。

**Why this priority**: 这是 Agent 侧改善最大的单点突破。当前 Agent 只能通过 `memory.search(query=...)` 做文本搜索，无法浏览"我知道什么"，导致大量相关记忆被遗漏。补齐 browse 能力后，Agent 可以主动发现并利用已有记忆，直接提升所有下游场景的记忆利用率。

**Independent Test**: 在 Agent 对话中输入"你还记得我之前提过的技术偏好吗"，Agent 调用 `memory.browse` 列出 subject_key 以"用户偏好/"开头的记忆条目，然后按需调用 `memory.read` 获取详情，给出准确回答。

**Acceptance Scenarios**:

1. **Given** Agent 处于对话中且 memory 系统中存在多个 partition（core/work/life）的 SoR 记忆，**When** Agent 调用 `memory.browse(group_by="partition")`，**Then** 返回按 partition 分组的记忆概览，包含每组的 subject_key 列表、条目数量和最近更新时间
2. **Given** Agent 需要查找用户的家庭相关记忆，**When** Agent 调用 `memory.browse(prefix="家庭/")`，**Then** 返回所有 subject_key 以"家庭/"开头的记忆条目摘要列表
3. **Given** memory.browse 返回结果超过 limit 上限（默认 20 条），**When** Agent 请求浏览，**Then** 返回前 20 条并包含 `has_more=true` 标记和 `total_count`，Agent 可通过 `offset` 参数翻页
4. **Given** Agent 调用 `memory.browse` 但指定的 scope 下无任何记忆，**When** 返回结果，**Then** 返回空列表和 `total_count=0`，不报错

---

### User Story 2 - 用户通过 UI 编辑记忆内容 (Priority: P1)

用户在 Memory UI 中查看记忆列表时，发现某条 SoR 记忆的内容不准确（如过时的偏好、错误的事实归因），需要直接修改其内容或 subject_key。编辑操作应保留原版本的完整审计链，用户可以看到修改前后的对比。

**Why this priority**: 用户明确要求的核心审计能力。当前 Memory UI 只能只读查看，用户无法纠正 Agent 自动提取的错误记忆，导致错误信息持续影响 Agent 行为。编辑能力是"用户掌控感"（User-in-Control）的基础保障。

**Independent Test**: 在 Memory UI 中选择一条 SoR 记忆，点击"编辑"，修改内容后保存，验证新版本生效、旧版本保留为 superseded 状态、UI 可查看版本历史。

**Acceptance Scenarios**:

1. **Given** 用户在 Memory UI 的记忆详情页面中查看一条 SoR 记忆（status=current），**When** 用户点击"编辑"按钮并修改 content 字段后点击"保存"，**Then** 系统创建新版本的 SoR（status=current），原版本自动变为 superseded，UI 刷新显示新内容
2. **Given** 用户修改了一条 SoR 记忆的 subject_key，**When** 保存成功，**Then** 记忆在目录和搜索中以新 subject_key 出现，原 subject_key 的历史版本链保留可查
3. **Given** 用户编辑一条 SoR 记忆，**When** 保存操作触发，**Then** 系统在审计日志中记录此次编辑事件（含操作人、时间、变更内容摘要），用户可在记忆操作历史中查看

---

### User Story 3 - 用户通过 UI 归档/删除记忆 (Priority: P1)

用户发现 Memory 中存在不再需要的记忆（如过时的项目信息、错误提取的噪声条目），需要将其归档或删除。归档后的记忆不再出现在 Agent 的 recall 结果中，但仍可在"已归档"视图中查看和恢复。

**Why this priority**: 与编辑同为核心审计能力，共同构成用户对记忆系统的完整管控闭环。缺少删除/归档意味着用户无法清理噪声记忆，长期会降低 recall 质量。

**Independent Test**: 在 Memory UI 中归档一条记忆，验证其从默认列表中消失、不再被 Agent recall 命中；切换到"已归档"视图可看到该记忆；点击"恢复"后回到正常列表。

**Acceptance Scenarios**:

1. **Given** 用户在 Memory UI 中选中一条 SoR 记忆，**When** 用户点击"归档"按钮并确认二次确认对话框，**Then** 记忆 status 变为 archived，从默认记忆列表中消失
2. **Given** 一条 SoR 记忆已被归档（status=archived），**When** Agent 执行 recall 或 search，**Then** 该记忆不出现在结果中（除非显式指定 `status="archived"` 筛选）
3. **Given** 用户在"已归档"筛选视图中查看归档记忆，**When** 用户点击"恢复"按钮，**Then** 记忆 status 恢复为 current，重新出现在默认记忆列表和 recall 结果中
4. **Given** 用户执行归档操作，**When** 操作完成，**Then** 系统在审计日志中记录此次归档事件，包含操作人、时间、被归档的 subject_key

---

### User Story 4 - Consolidation 全生活域覆盖 (Priority: P1)

当 Agent 执行 CONSOLIDATE 整理时，不应只关注"项目决策和偏好"，而应覆盖用户生活的各个维度：人物关系、家庭事件、情感状态、健康信息、消费习惯、技术选型、生活习惯、兴趣爱好等。提取出的记忆应正确归因到信息主体（如"A 提到 B 喜欢跑步"应归因到 B，不是 A）。

**Why this priority**: 提取质量是记忆系统价值的根基。如果 Consolidation 只覆盖狭窄的工作偏好维度，大量有价值的生活信息会被遗漏，记忆系统沦为"工作偏好数据库"而非真正的个人记忆系统。

**Independent Test**: 在一段包含生活场景的对话后触发 consolidate，验证提取出的 SoR 覆盖至少 5 个不同维度的信息（如人物关系、健康、消费、兴趣、日程），且人物归因正确。

**Acceptance Scenarios**:

1. **Given** 用户在对话中提到"我妈妈下个月生日，她喜欢园艺"，**When** consolidate 执行，**Then** 提取的 SoR 的信息主体（subject_key）指向"妈妈"而非用户自身，内容包含生日时间和兴趣爱好
2. **Given** 用户在对话中涉及技术选型讨论和个人健身计划，**When** consolidate 执行，**Then** 分别产生"技术选型"类和"健康/运动"类的 SoR 记忆，而非只提取技术讨论
3. **Given** consolidate prompt 的提取维度已扩展到 10+ 类别，**When** 对话内容中不包含某些维度的信息，**Then** 不强制为空维度生成 SoR，只提取实际出现的信息

---

### User Story 5 - Solution 记忆提取与自动匹配 (Priority: P2)

Agent 在解决问题时积累的成功方案（problem + solution 结构）应被单独提取和存储。当 Agent 遇到类似问题（特别是执行出错）时，系统应自动搜索匹配的历史解决方案，帮助 Agent 避免重复踩坑。

**Why this priority**: Solution 记忆是提升 Agent 自主能力的关键差异化功能（参考 Agent Zero 的 solutions_sum），但其价值依赖于 P1 的基础记忆能力先就位，且需要一定的使用积累才能发挥效果。

**Independent Test**: Agent 解决了一个 Docker 构建错误后，Solution 被自动提取存储；之后当 Agent 遇到类似的 Docker 构建错误时，系统自动搜索并注入匹配的历史 Solution。

**Acceptance Scenarios**:

1. **Given** Agent 在对话中成功解决了一个技术问题（如调试了一个 API 超时），**When** consolidate 执行，**Then** 系统提取一条 Solution 记忆，包含结构化的 problem 描述和 solution 描述，partition 标记为 `solution`
2. **Given** Agent 执行工具调用时遇到错误，且 memory 中存在与该错误相似的 Solution 记忆，**When** 错误发生，**Then** 系统自动执行一次 Solution recall，将匹配结果（如有）注入 Agent 的下一轮上下文
3. **Given** Agent 手动调用 `memory.search(partition="solution")` 搜索历史方案，**When** 搜索执行，**Then** 返回按相关度排序的 Solution 列表，每条包含 problem 摘要和 solution 摘要

---

### User Story 6 - Consolidation 策略丰富化 (Priority: P2)

Consolidation 流程除了现有的 ADD（新增）和 UPDATE（更新），还应支持 MERGE（将多条高度相关的记忆合并为一条综合记忆）和 REPLACE（当新记忆与旧记忆相似度极高时，直接替换过时记忆）。这些策略由 LLM 在 consolidate 时自动选择，不需要用户干预。

**Why this priority**: 策略丰富化可以减少记忆冗余、提高信息密度，但核心功能不依赖于此——ADD/UPDATE 已能覆盖基本场景。

**Independent Test**: 触发 consolidate 后检查输出的 action 列表，验证 MERGE 和 REPLACE 操作在合适场景下被正确选择和执行。

**Acceptance Scenarios**:

1. **Given** 存在三条高度相关的 SoR 记忆（如"用户偏好 Python"、"用户喜欢 Python 的类型系统"、"用户选择 Python 作为主力语言"），**When** consolidate 执行，**Then** LLM 选择 MERGE 策略，将三条合并为一条综合性的 SoR 记忆，原三条标记为 superseded
2. **Given** 存在一条旧 SoR"用户住在 A 市"和一条新 fragment"用户最近搬到了 B 市"，**When** consolidate 执行且新旧信息语义矛盾，**Then** LLM 选择 REPLACE 策略，创建新 SoR"用户住在 B 市"，旧 SoR 标记为 superseded
3. **Given** consolidate 执行 MERGE 或 REPLACE 操作，**When** 操作完成，**Then** 被合并/替换的原始记忆保留为 superseded 状态，完整审计链可追溯

---

### User Story 7 - 扩展 memory.search 结构化筛选 (Priority: P2)

Agent 在搜索记忆时，除了文本关键词，还应能按 derived_type、时间范围、置信度阈值、SoR 状态等结构化维度筛选，从而实现更精准的记忆检索。

**Why this priority**: 增强现有工具的查询精度，补齐 Agent 按结构化元数据筛选的能力缺口。优先级低于 browse（P1）因为 browse 解决的是"从无到有"的浏览能力，而此项是"从有到优"的精度提升。

**Independent Test**: Agent 调用 `memory.search` 附带 `derived_type="tom"` 和 `min_confidence=0.7` 参数，验证返回结果只包含符合条件的高置信度 Theory of Mind 记忆。

**Acceptance Scenarios**:

1. **Given** memory 中存在不同 derived_type（profile/tom/solution）的记忆，**When** Agent 调用 `memory.search(query="编程", derived_type="profile")`，**Then** 只返回 derived_type 为 profile 的匹配记忆
2. **Given** Agent 需要查找最近一周更新的记忆，**When** Agent 调用 `memory.search` 并附带时间范围参数，**Then** 只返回 `updated_at` 在指定范围内的记忆
3. **Given** Agent 调用 `memory.search` 时使用新增的筛选参数，**When** 这些参数为空或未提供，**Then** 行为与当前版本完全一致（向后兼容）

---

### User Story 8 - Profile 信息密度提升 (Priority: P3)

当前每个 Profile 维度只有 1-3 句话的概括，无法承载丰富的用户画像信息。应允许 Profile 每个维度包含多段详细描述，支持随时间积累逐步丰富。

**Why this priority**: Profile 是 Agent 理解用户的重要来源，但当前稀疏的信息密度限制了其价值。不过 Profile 增强依赖于 Consolidation 质量提升（P1）先产出更丰富的原始素材。

**Independent Test**: 触发 profile_generate 后，检查生成的 Profile 中至少有 3 个维度包含多段描述（而非仅一句话），信息量显著高于当前版本。

**Acceptance Scenarios**:

1. **Given** memory 中存在某个维度（如"技术偏好"）的多条 SoR 记忆，**When** profile_generate 执行，**Then** 该维度的 Profile 输出包含多段落的详细描述，覆盖语言偏好、框架选择、编码风格等子维度
2. **Given** Profile 某个维度的信息随时间积累越来越多，**When** 重新生成 Profile，**Then** 新版本的信息密度不低于旧版本（不丢失历史积累），同时整合新信息

---

### Edge Cases

- **并发编辑冲突**: 用户在 UI 中编辑一条 SoR 的同时，Agent 的记忆整理也在更新同一条 SoR，如何处理？采用乐观锁机制——编辑/归档请求携带期望版本号，系统检查当前版本是否匹配，不匹配时提示冲突，前端引导用户刷新后重试 [AUTO-CLARIFIED: 乐观锁 — 比"最后写入胜出"更安全，避免静默覆盖用户或 Agent 的修改]
- **归档后被引用**: 归档一条 SoR 后，其他 SoR 的证据引用仍指向它，如何处理？归档的记忆仍保留在存储层，证据引用链接保持有效，仅从 recall 结果中排除
- **browse 返回量过大**: 某个 scope 下有数千条记忆，browse 请求如何避免响应膨胀？强制 limit 上限（默认 20，最大 100），返回分组聚合而非逐条详情
- **Solution 记忆误匹配**: 错误时自动搜索 Solution 可能匹配到不相关的历史方案，如何处理？设置相似度阈值（如 0.7），低于阈值不注入；Agent 可选择忽略建议
- **MERGE 策略产出质量**: LLM 合并多条记忆时可能丢失重要细节，如何保障？MERGE 后的综合记忆必须保留证据引用指向所有原始记忆，用户可通过审计链追溯原文
- **空记忆系统冷启动**: 新用户首次使用时记忆为空，browse/search/recall 全部返回空，Agent 应正常降级处理而非报错
- **Vault 记忆的审计限制**: Vault 层记忆（敏感信息）的编辑/归档应遵循 Least Privilege 原则，需要额外授权确认

---

## Requirements

### Functional Requirements

#### 索引与利用

- **FR-001**: 系统 MUST 提供 `memory.browse` 工具，允许 Agent 按 subject_key 前缀、partition、scope 等维度浏览记忆目录，返回分组统计和条目摘要列表 [Story 1]
- **FR-002**: `memory.browse` MUST 支持 `group_by` 参数（按 partition / scope / subject_key 前缀分组），返回每组的条目数量、subject_key 列表和最近更新时间 [Story 1]
- **FR-003**: `memory.browse` MUST 支持分页（`offset` + `limit`），默认 limit=20，最大 limit=100 [Story 1]
- **FR-004**: `memory.search` SHOULD 新增可选参数：`derived_type`（按派生类型筛选）、`time_range`（按更新时间范围筛选）、`status`（按 SoR 状态筛选）。本迭代暂不实现 `min_confidence` 筛选，因为当前记忆记录本身不包含置信度属性，新增该属性的改造成本高于收益，Agent 可通过 `derived_type` + `status` 组合达到类似筛选效果 [AUTO-CLARIFIED: 暂不实现 min_confidence] [Story 7]
- **FR-005**: 新增参数 MUST 全部为可选参数，未提供时行为与当前版本完全一致（向后兼容） [Story 7]

#### 审计机制

- **FR-006**: 系统 MUST 提供 SoR 编辑能力——修改 SoR 的 content 和/或 subject_key，创建新版本（status=current），原版本自动变为 superseded。编辑操作 MUST 经过与自动写入相同的"提议-验证-提交"流程以保证审计链完整，且 MUST 标记为用户手动编辑以区别于 Agent 的自动整理写入 [AUTO-CLARIFIED: 复用已有写入流程 — 满足 Constitution "Side-effect Must be Two-Phase" 和 "Everything is an Event"，且避免引入独立的写入路径] [Story 2]
- **FR-007**: 系统 MUST 提供 SoR 归档能力——将 SoR status 设为 archived，使其从默认 recall/search 结果中排除 [Story 3]
- **FR-008**: 系统 MUST 提供 SoR 恢复能力——将 archived 状态的 SoR 恢复为 current [Story 3]
- **FR-009**: SoR 的编辑、归档、恢复操作 MUST 在审计日志中记录事件，包含操作人、时间、变更内容摘要 [Story 2, 3]
- **FR-010**: Memory UI 的记忆详情页面 MUST 新增"编辑"和"归档"操作按钮 [Story 2, 3]
- **FR-011**: 归档操作 MUST 要求用户二次确认 [Story 3]
- **FR-012**: Memory UI MUST 支持"已归档"筛选视图，允许用户查看和恢复归档记忆 [Story 3]
- **FR-013**: SoR/Derived/ToM/Profile 的自动写入（consolidation、derived 生成等）MUST 保持全自动化，不需要人工审批 [AUTO-RESOLVED: 用户明确要求审计 = 事后查看修改，不是事前审批]

#### 提取质量

- **FR-014**: Consolidation prompt MUST 覆盖全生活域提取维度，至少包括：人物关系、家庭事件、情感状态、健康信息、消费习惯、技术选型、项目决策、生活习惯、兴趣爱好、日程安排 [Story 4]
- **FR-015**: 当对话中 A 提到关于 B 的信息时，consolidation MUST 将信息主体归因到 B（信息主体），而非 A（说话人） [Story 4]
- **FR-016**: Consolidation 流程 SHOULD 支持 MERGE 策略——将多条高度相关的 SoR 合并为一条综合记忆，原始记忆标记为 superseded。MERGE 作为独立的写入操作类型，语义不同于 UPDATE（UPDATE 是单条更新，MERGE 是多条合并为一条） [Story 6]
- **FR-017**: Consolidation 流程 SHOULD 支持 REPLACE 策略——当新信息与旧 SoR 语义矛盾且相关度极高时，创建新 SoR 替换旧版本。REPLACE 可复用现有 UPDATE 操作的机制（本质是替代旧记忆 + 创建新记忆），通过写入元数据标记原因为 replace 以区分 [Story 6]
- **FR-018**: MERGE 和 REPLACE 操作 MUST 保留完整审计链——被合并/替换的原始记忆保留为 superseded 状态 [Story 6]
- **FR-019**: Consolidation SHOULD 识别并单独提取 Solution 记忆（problem + solution 结构），存储到 partition=solution 分区 [Story 5]
- **FR-020**: 系统 SHOULD 在 Agent 遇到工具执行错误时，自动执行一次 Solution recall，将匹配结果注入 Agent 下一轮上下文 [Story 5]
- **FR-021**: Solution 自动匹配 MUST 设置相似度阈值，低于阈值的结果不注入 [Story 5]
- **FR-022**: Profile 生成 SHOULD 支持每个维度输出多段详细描述，而非限制在 1-3 句话的概括 [Story 8]

#### 系统约束

- **FR-023**: 所有新增工具 MUST 遵循现有工具注册规范，确保工具定义与实际行为一致 [Constitution: Tools are Contracts]
- **FR-024**: 所有新增审计操作 MUST 生成审计日志记录 [Constitution: Everything is an Event]
- **FR-025**: Vault 层记忆的编辑/归档 MUST 遵循 Least Privilege 原则，需要额外授权确认 [Constitution: Least Privilege by Default]
- **FR-026**: `memory.browse` 在语义检索服务不可用时 MUST 降级到结构化查询，不导致系统不可用 [Constitution: Degrade Gracefully]

### Key Entities

- **SoR（Source of Record）**: 记忆系统的核心实体。代表一条经确认的长期记忆，包含内容、主题标识、分区、范围、生命周期状态和版本号。本次新增 `archived`（已归档）状态，作为软删除机制——archived 可恢复，区别于永久删除
- **Solution 记忆**: SoR 的一种特化形式，存储在独立的 solution 分区中。结构化包含 problem 描述和 solution 描述，用于 Agent 遇到类似问题时自动复用历史方案
- **Fragment（信息片段）**: 对话中提取的原始信息碎片，是记忆整理流程的输入源。本次不修改
- **Profile（用户画像）**: 从 SoR 聚合生成的多维用户画像。本次增强其信息密度，允许每个维度输出多段详细描述

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: Agent 在需要了解用户已有记忆时，能通过 `memory.browse` 在一次工具调用内获取结构化的记忆目录概览，无需多次盲搜
- **SC-002**: 用户能在 Memory UI 中完成对任意 SoR 记忆的编辑、归档和恢复操作，操作响应时间不超过 2 秒
- **SC-003**: 归档的记忆不再出现在 Agent 的 recall/search 默认结果中，但用户可在"已归档"视图中查看和恢复
- **SC-004**: Consolidation 执行后提取的记忆覆盖至少 5 个不同生活维度（当对话内容包含这些维度时），而非仅限于项目决策和偏好
- **SC-005**: 所有审计操作（编辑、归档、恢复）均有完整的操作记录，用户可在记忆操作历史中追溯每次变更的操作人、时间和变更内容
- **SC-006**: 新增工具和参数完全向后兼容，现有 Agent 行为无回退
- **SC-007**: Solution 记忆被正确提取后，当 Agent 遇到相似错误时能自动获取匹配的历史解决方案建议

---

## Clarifications

### Session 2026-03-19

#### 歧义 1: SoR 生命周期状态缺少 `archived`

**上下文**: spec 多处假设 SoR 有 `archived` 状态（FR-007, FR-008, Story 3），但当前 SoR 的生命周期状态只有 `current`/`superseded`/`deleted`。

**决策**: [AUTO-CLARIFIED] 新增 `archived`（已归档）状态。`archived` 与 `deleted` 的语义区分：archived 表示用户主动归档（可恢复），deleted 表示系统或用户永久删除（不可恢复）。

#### 歧义 2: 记忆分区缺少 `solution` 类型

**上下文**: FR-019/Story 5 要求 Solution 记忆存储到 `partition=solution`，但当前记忆分区中不含 solution 分区。

**决策**: [AUTO-CLARIFIED] 新增 `solution` 记忆分区。Solution 作为独立分区（而非放在 `work` 分区内）有利于 Agent 按分区精确筛选和浏览分组。

#### 歧义 3: 记忆记录无置信度属性，spec 却要求 min_confidence 筛选

**上下文**: FR-004 原始描述包含 `min_confidence` 参数，但当前记忆记录本身不包含置信度属性。

**决策**: [AUTO-CLARIFIED] 本迭代暂不实现 `min_confidence` 筛选。理由：(1) 为记忆记录新增置信度属性需要数据迁移；(2) Agent 可通过 `derived_type` + `status` 组合达到类似筛选效果；(3) 保持 P2 Story 7 的实现范围可控。

#### 歧义 4: 用户编辑 SoR 的写入路径——直接操作 vs Proposal 流程

**上下文**: FR-006 描述了编辑能力，FR-009 要求审计日志记录，但未明确是通过独立的编辑接口还是复用现有的记忆写入流程。

**决策**: [AUTO-CLARIFIED] 复用已有的记忆写入流程。用户编辑操作走与自动写入相同的"提议-验证-提交"三步流程，并标记来源为用户手动编辑。理由：满足 Constitution "Side-effect Must be Two-Phase"，且避免引入独立写入路径带来的一致性风险。

#### 歧义 5: 并发编辑冲突检测机制

**上下文**: Edge Cases 提到并发冲突，但原始描述"以最新操作为准"语义模糊——可以是乐观锁（版本号检查）或最后写入胜出（Last Write Wins）。

**决策**: [AUTO-CLARIFIED] 采用乐观锁。编辑/归档请求须携带期望版本号，系统检查记忆当前版本是否匹配。不匹配时提示冲突，前端引导用户刷新后重试。理由：SoR 已有版本追踪机制，乐观锁实现成本低且避免静默覆盖。

#### 歧义 6: MERGE 和 REPLACE 策略对写入操作类型的影响

**上下文**: FR-016/FR-017 引入 MERGE 和 REPLACE 策略，但当前写入操作类型只有 add/update/delete/none。

**决策**: [AUTO-CLARIFIED] MERGE 需要作为新的操作类型（语义不同于 UPDATE：UPDATE 是单条更新，MERGE 是多条合并为一条新记忆）。REPLACE 可复用 UPDATE 的操作机制（底层操作相同：替代旧记忆 + 创建新记忆），通过写入元数据中的原因标记区分。
