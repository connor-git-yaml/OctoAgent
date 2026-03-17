# Feature Specification: Memory 系统整体优化

**Feature Branch**: `claude/competent-pike`
**Created**: 2026-03-17
**Status**: Draft
**Input**: 用户反馈 Memory 系统存在 5 个关联问题：SoR scope 隔离导致跨 Agent 不可见、前端缺少 scope 选择器、partition 分配形同虚设、local_only 残留代码、模型别名无 fallback 且缺少 Settings UI

## User Scenarios & Testing *(mandatory)*

### User Story 1 - SoR 记忆全局共享 (Priority: P1)

用户（Connor）与 Butler 对话产生的结论性记忆（SoR 层）当前全部写入 WORKER_PRIVATE scope，导致同一 Project 下的其他 Agent 无法读取这些结论。用户期望 SoR 记录默认写入 PROJECT_SHARED scope，使得任何绑定到该 Project 的 Agent 都能共享这些已确认的事实性记忆。

**Why this priority**: 这是 Memory 系统最根本的可用性问题。98 条 SoR 记录全部被隔离在 WORKER_PRIVATE，意味着系统的长期记忆能力形同虚设——即便是 Butler 自身在新会话中也可能无法正确检索到之前的结论。修复此问题后，Memory 才能真正发挥"结论性记忆跨会话复用"的核心价值。

**Independent Test**: 可通过在 Chat 中与 Butler 对话产生新的 SoR 记忆，然后在 Memory 管理页面验证该记录的 scope 是否为 PROJECT_SHARED，以及在另一个 Agent 会话中能否检索到该记忆。

**Acceptance Scenarios**:

1. **Given** 用户与 Butler 对话产生一条新的 SoR 结论，**When** 系统将该结论写入 Memory，**Then** 该记录的 scope 为 PROJECT_SHARED（而非 WORKER_PRIVATE）
2. **Given** 已有 98 条历史 SoR 记录存储在 WORKER_PRIVATE scope，**When** 系统执行存量数据迁移，**Then** 这些记录的 scope 被更新为 PROJECT_SHARED，且记忆内容不丢失
3. **Given** 一条 SoR 记录已写入 PROJECT_SHARED scope，**When** 同一 Project 下的另一个 Agent 执行记忆召回，**Then** 该 Agent 能够检索到这条共享记忆

---

### User Story 2 - Partition 分配修复 (Priority: P1)

用户的 98 条 SoR 记录全部被归入 "work" 分区，但实际内容涵盖健康、学习、旅行、个人偏好等多种主题。分区分配逻辑未能正确识别内容主题，导致分区维度的筛选和管理完全失效。

**Why this priority**: 与 Story 1 并列 P1，因为即使 scope 修复后，如果所有记忆仍堆积在同一个分区中，用户在 Memory 页面的筛选体验仍然是"分区选了也没区别"，无法快速定位特定领域的记忆。分区是记忆组织的核心维度之一，修复后用户才能按主题浏览和管理记忆。

**Independent Test**: 可通过查看 Memory 管理页面中各分区的记录分布来验证——修复后应看到记录分散在 core、health、work 等不同分区中，而非全部堆在 work。

**Acceptance Scenarios**:

1. **Given** 用户对话内容涉及健康相关话题（如体检、用药、健身），**When** 系统将该内容提炼为 SoR 结论，**Then** 该记录的 partition 为 "health" 而非 "work"
2. **Given** 用户对话内容涉及个人偏好或核心信息（如姓名、联系方式、生活习惯），**When** 系统将该内容提炼为 SoR 结论，**Then** 该记录的 partition 为 "core" 或 "profile"
3. **Given** 已有 98 条历史 SoR 记录全部归入 "work" 分区，**When** 系统对存量数据重新分配分区，**Then** 记录被重新分散到合适的分区中，分布不再全部集中于单一分区
4. **Given** Memory 管理页面显示分区筛选下拉菜单，**When** 用户选择 "health" 分区，**Then** 仅显示健康相关的记忆记录

---

### User Story 3 - Memory 页面 Scope 选择器 (Priority: P2)

用户在 Memory 管理页面只能看到系统默认加载的 scope 下的记忆。用户希望有一个下拉选择器，列出当前有记忆数据的所有 project_id 和 workspace_id 组合，让用户自由切换浏览不同作用域的记忆。

**Why this priority**: 在当前单 Project 阶段（Butler 处理所有请求、尚无 A2A dispatch），scope 选择器的即时价值有限，但它是 Memory 页面走向多 Project / 多 Agent 场景的必要基础设施。当 Story 1 修复 scope 之后，用户需要一种方式确认"我的记忆确实在 PROJECT_SHARED 下了"。

**Independent Test**: 可通过打开 Memory 管理页面，验证 scope 下拉选择器是否出现、是否列出了有记忆的 scope 选项、切换后记忆列表是否正确刷新。

**Acceptance Scenarios**:

1. **Given** Memory 管理页面加载完成，**When** 页面渲染筛选区域，**Then** 显示一个 scope 选择器（下拉菜单），列出当前所有有记忆数据的 scope（以用户可理解的标签展示，而非内部 ID）
2. **Given** 用户在 scope 选择器中选择了一个不同的 scope，**When** 选择生效，**Then** Memory 记录列表刷新为该 scope 下的记忆内容
3. **Given** 系统中仅有一个 scope 包含记忆数据，**When** Memory 页面加载，**Then** scope 选择器仍然显示但默认选中唯一的选项，用户可感知当前正在查看的 scope

---

### User Story 4 - 模型别名 Fallback 与 Settings UI (Priority: P2)

Memory 系统依赖 4 个专用模型别名（reasoning、expand、embedding、rerank）来驱动记忆加工、查询扩写、语义检索和结果重排能力。当前这些别名用户未配置时，reasoning 和 expand 没有 fallback 到 main 别名，导致记忆加工和语义检索能力未激活。用户希望：(1) reasoning 和 expand 别名在未配置时自动 fallback 到 main；(2) 在 Settings 页面增加可视化界面，让用户配置这些别名。同时需确认内建 Qwen3-Embedding-0.6B 已正确激活。

**Why this priority**: 别名 fallback 修复后，用户无需额外配置即可获得记忆加工能力（使用 main 模型做总结和扩写），降低首次使用门槛。Settings UI 则提供了高级用户调优记忆系统的入口。

**Independent Test**: 可通过在 Settings 页面查看 Memory 别名配置区域是否出现、是否正确显示 fallback 状态，以及在 Memory 页面验证 "degraded" 状态是否消失。

**Acceptance Scenarios**:

1. **Given** 用户未配置 reasoning_model_alias，**When** 系统执行记忆加工（总结、整理），**Then** 系统自动使用 main 别名对应的模型执行加工，而非报错或跳过
2. **Given** 用户未配置 expand_model_alias，**When** 系统执行 recall 查询扩写，**Then** 系统自动使用 main 别名对应的模型执行扩写
3. **Given** 用户打开 Settings 页面，**When** 页面渲染 Memory 配置区域，**Then** 显示 reasoning、expand、embedding、rerank 四个别名的当前绑定状态（已配置的显示别名名、未配置的显示 fallback 目标）
4. **Given** 用户在 Settings Memory 区域为 reasoning_model_alias 选择了一个已定义的别名，**When** 用户保存配置，**Then** 后续记忆加工使用该别名对应的模型
5. **Given** 内建 Qwen3-Embedding-0.6B 已集成，**When** 系统启动且用户未配置外部 embedding 别名，**Then** 语义检索使用内建 Qwen3-Embedding-0.6B，Memory 页面不再显示 "degraded" 状态

---

### User Story 5 - 移除 local_only 机制残留 (Priority: P3)

MemU Bridge（Feature 028）已被废弃，Memory 后端已简化为内建引擎。但代码中仍残留 local_only 配置项、Bridge 模式分支逻辑和前端 Bridge 相关 UI 元素。这些残留代码增加了维护负担，也对用户造成困惑（如 Settings 页面显示 backend_mode 选项只有 "local_only" 一个选项）。

**Why this priority**: 这是一项清理性工作，不直接影响用户可感知的功能，但能降低代码复杂度、减少 "memu_compat" / "bridge_transport" / "bridge_url" 等过时概念对用户和开发者的误导。

**Independent Test**: 可通过全局搜索代码库，验证 local_only、bridge_transport、bridge_url、bridge_command、bridge_api_key_env、memu_compat 等标识符已被移除或替换；Settings 和 Memory 页面不再显示 Bridge 相关配置项。

**Acceptance Scenarios**:

1. **Given** MemU Bridge 已废弃，**When** 系统加载配置，**Then** 不再读取或依赖 backend_mode、bridge_transport、bridge_url、bridge_command、bridge_api_key_env 等配置字段
2. **Given** 前端 Memory 页面加载，**When** 页面渲染，**Then** 不再显示与 Bridge 模式相关的状态信息、配置提示或缺失配置警告
3. **Given** 前端 Settings 页面加载，**When** 页面渲染 Memory 区域，**Then** 不再显示 backend_mode 选择器（因为只有一种模式），Memory 区域聚焦于别名配置和状态展示
4. **Given** 代码库中的 memory_retrieval_profile 逻辑，**When** 构建 retrieval profile，**Then** 不再出现 "local_only" / "memu_compat" 分支判断，统一为内建引擎路径

---

### Edge Cases

- **存量数据迁移失败**: 如果 98 条历史 SoR 记录在 scope 迁移过程中部分失败（如数据库写入错误），系统应记录失败记录并允许重试，不丢弃任何已有记忆 [关联 FR-001, FR-002]
- **分区重分配冲突**: 存量记录重新分区时，如果 LLM 分类结果与用户预期不符（如将健康记录误分到 work），用户应能在 Memory 管理页面手动修改单条记录的分区 [关联 FR-003, FR-004]
- **无 main 别名时的 fallback 链**: 如果用户既未配置 reasoning_model_alias 也未配置 main 别名（极端情况），系统应明确报告 Memory 加工能力不可用，而非静默失败 [关联 FR-008]
- **Scope 选择器无数据**: 如果系统中没有任何记忆数据（全新安装），scope 选择器应显示空状态提示而非空下拉菜单 [关联 FR-005]
- **Embedding 模型不可用**: 如果内建 Qwen3-Embedding-0.6B 本机运行时暂不可用，系统应自动回退到双语 hash embedding，Memory 页面显示降级提示而非 "degraded" 错误 [关联 FR-009]
- **敏感分区扩大可见性**: SoR 从 WORKER_PRIVATE 迁移到 PROJECT_SHARED 后，原先仅单个 Agent 可见的敏感记忆（如 health 分区）将对同 Project 下所有 Agent 可见，需确保 Vault 层的授权控制不受影响 [关联 FR-001, Constitution C5]

## Requirements *(mandatory)*

### Functional Requirements

**SoR Scope 全局共享**

- **FR-001**: 系统 MUST 将新产生的 SoR（现行事实层）记录默认写入 PROJECT_SHARED scope，使同一 Project 下所有 Agent 均可读取 [Story 1]
- **FR-002**: 系统 MUST 提供存量 SoR 数据迁移能力，将已有 WORKER_PRIVATE scope 的 SoR 记录迁移到 PROJECT_SHARED scope，迁移过程保证数据完整性（记忆内容、版本、元数据不丢失） [Story 1]

**Partition 分配修复**

- **FR-003**: 系统 MUST 在将对话内容提炼为 SoR 结论时，根据内容主题正确分配 partition（如 core、profile、work、health、finance、chat、contact），而非将所有记录默认归入 "work" [Story 2]
- **FR-004**: 系统 SHOULD 提供存量 SoR 记录的分区重分配能力，对已有 "work" 分区的记录根据内容重新分类到合适的分区 [Story 2]

**Memory 页面 Scope 选择器**

- **FR-005**: Memory 管理页面 MUST 在筛选区域提供 scope 选择器（下拉菜单），列出当前有记忆数据的所有 scope，以用户可理解的标签展示（如 "项目共享" 而非内部 namespace ID） [Story 3]
- **FR-006**: 用户切换 scope 选择器后，Memory 记录列表 MUST 刷新为所选 scope 下的记忆内容 [Story 3]

**模型别名 Fallback 与 Settings UI**

- **FR-007**: reasoning_model_alias 和 expand_model_alias 在用户未配置时，MUST 自动 fallback 到 main 别名，使记忆加工和查询扩写能力默认可用 [Story 4]
- **FR-008**: Settings 页面 Memory 区域 MUST 显示 reasoning、expand、embedding、rerank 四个别名的当前绑定状态，并允许用户从已定义的模型别名中选择或清空配置 [Story 4]
- **FR-009**: 系统 MUST 确保内建 Qwen3-Embedding-0.6B 在用户未配置外部 embedding 别名时正确激活，语义检索功能默认可用 [Story 4]

**移除 local_only 机制残留**

- **FR-010**: 系统 MUST 移除 MemoryConfig 中的 backend_mode 配置字段及其相关的 Bridge 模式配置（bridge_transport、bridge_url、bridge_command、bridge_api_key_env） [Story 5]
- **FR-011**: 系统 MUST 移除 memory_retrieval_profile 中的 "local_only" / "memu_compat" 分支逻辑，统一为内建引擎单一路径 [Story 5]
- **FR-012**: 前端 Memory 页面和 Settings 页面 MUST 移除与 Bridge 模式相关的 UI 元素（状态显示、配置提示、缺失配置警告） [Story 5]

[AUTO-RESOLVED: SoR 加工克制策略 -- 用户明确表示"SoR 的加工需要克制"，但未定义具体的克制规则。基于上下文判断，这里指的是 SoR scope 扩大后不应增加新的自动加工管道（如自动派生、自动合并），本次仅变更写入 scope，不扩展加工行为。]

### Key Entities

- **SoR Record (现行事实记录)**: Memory 系统的核心数据单元，包含 subject_key（主题键）、summary（结论摘要）、partition（分区）、scope_id（作用域）、layer（层级，固定为 "sor"）、version（版本号）。本次变更其默认 scope_id 从 WORKER_PRIVATE 到 PROJECT_SHARED。
- **MemoryNamespaceKind (记忆命名空间类型)**: 枚举类型，定义三种 scope：PROJECT_SHARED（项目共享）、BUTLER_PRIVATE（Butler 私有）、WORKER_PRIVATE（Worker 私有）。本次将 SoR 的默认写入 scope 变更为 PROJECT_SHARED。
- **Partition (分区)**: 记忆的主题分类维度，已定义的分区包括 core（核心信息）、profile（个人资料）、work（工作事项）、health（健康）、finance（财务）、chat（对话内容）、contact（联系人）。本次修复分配逻辑使其正确生效。
- **Memory Retrieval Binding (检索绑定)**: 将 Memory 子系统的 4 个能力槽位（reasoning、expand、embedding、rerank）绑定到具体模型别名的配置单元。本次增加 reasoning 和 expand 的 fallback 逻辑，并在 Settings UI 暴露配置入口。
- **MemoryConsoleDocument (Memory 控制台文档)**: 前端 Memory 页面的数据源，包含 available_scopes（可用 scope 列表）、filters（筛选条件含 scope_id）、records（记忆记录列表）。本次为其 scope 选择器提供数据支持。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 新产生的 SoR 记录 100% 写入 PROJECT_SHARED scope，验证方式为在 Memory 管理页面查看新记录的 scope 标识
- **SC-002**: 98 条存量 SoR 记录成功迁移到 PROJECT_SHARED scope，迁移后记录总数不减少、内容不变化
- **SC-003**: 存量 SoR 记录经重新分区后，分布在至少 3 个不同的 partition 中（而非全部集中在 work）
- **SC-004**: Memory 管理页面 scope 选择器正确列出所有有数据的 scope，切换后记录列表在 2 秒内刷新
- **SC-005**: 在 reasoning_model_alias 和 expand_model_alias 均未配置的情况下，Memory 页面不再显示 "degraded" 或 "memory snapshot unavailable" 状态
- **SC-006**: Settings 页面 Memory 区域正确展示 4 个别名槽位的当前状态，用户可完成"选择别名 -> 保存 -> 生效"的完整操作流程
- **SC-007**: 代码库中不再存在 bridge_transport、bridge_url、bridge_command、bridge_api_key_env 等已废弃配置字段的运行时引用（测试 fixture 和迁移脚本中的引用不计）
