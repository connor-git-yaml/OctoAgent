# Feature Specification: 统一工具注入 + 权限 Preset 模型

**Feature Branch**: `claude/festive-meitner`
**Feature ID**: 061
**Created**: 2026-03-17
**Status**: Draft
**Input**: 将工具注入和权限管理从"多模板 Worker Type 矩阵"重构为"统一工具可见 + 权限 Preset 隔离"模型

## User Scenarios & Testing

### User Story 1 — 权限 Preset 隔离：Agent 实例级工具权限控制 (Priority: P1)

作为 OctoAgent 用户，我希望系统中所有 Agent（Butler、Worker、Subagent）共享同一套工具集，但通过权限 Preset（minimal/normal/full）控制每个 Agent 实例实际可执行的操作范围，从而在保证安全的前提下消除 Worker Type 多模板的维护负担。

**Why this priority**: 权限 Preset 是整个 Feature 061 的核心支撑——Deferred Tools、Bootstrap 简化、Skill-Tool 注入路径优化全部依赖"统一工具可见 + Preset 隔离"这一基础模型。砍掉 Worker Type 多模板系统后，Preset 是唯一的工具权限分层机制。此外，这是影响面最大但风险最低的改动（调研结论），应优先交付。

**Independent Test**: 创建不同 Preset 的 Agent 实例，验证工具调用时的 allow/ask/deny 行为是否符合 Preset 定义，可独立测试并立即交付安全价值。

**Acceptance Scenarios**:

1. **Given** 系统中存在一个 Preset 为 `minimal` 的 Worker，**When** 该 Worker 尝试调用 `side_effect=none` 的只读工具（如 `project.inspect`），**Then** 工具调用被直接 allow 并执行成功。

2. **Given** 系统中存在一个 Preset 为 `minimal` 的 Worker，**When** 该 Worker 尝试调用 `side_effect=reversible` 的写入工具（如 `filesystem.write_text`），**Then** 工具调用触发 soft deny（ask），向用户发起审批请求，而非硬拒绝。

3. **Given** 系统中存在一个 Preset 为 `normal` 的 Worker，**When** 该 Worker 尝试调用 `side_effect=reversible` 的写入工具，**Then** 工具调用被直接 allow 并执行成功。

4. **Given** 系统中存在一个 Preset 为 `normal` 的 Worker，**When** 该 Worker 尝试调用 `side_effect=irreversible` 的工具（如 `docker.exec`），**Then** 工具调用触发 soft deny（ask），向用户发起审批请求。

5. **Given** 系统中存在一个 Preset 为 `full` 的 Agent（如 Butler），**When** 该 Agent 尝试调用任意工具（包括 `side_effect=irreversible`），**Then** 工具调用被直接 allow 并执行成功。

6. **Given** 用户在审批请求中选择了 `always`（对该工具永久允许），**When** 同一 Agent 实例再次调用该工具，**Then** 工具调用被直接 allow，不再触发审批。

7. **Given** 用户在审批请求中选择了 `deny`，**When** LLM 尝试再次调用同一工具，**Then** 本次调用被拒绝，但 LLM 后续仍可再次尝试（deny 仅作用于本次，不是永久黑名单）。

8. **Given** 一个 Worker 创建了 Subagent，**When** Subagent 尝试调用工具，**Then** Subagent 的权限 Preset 继承自其所属 Worker。

9. **Given** Butler 创建 Worker 时，**When** 未指定 Preset，**Then** Worker 默认使用 `normal` Preset。

10. **Given** Butler 自身，**When** 系统初始化时，**Then** Butler 默认使用 `full` Preset。

---

### User Story 2 — Deferred Tools 懒加载：context 优化 (Priority: P1)

作为 OctoAgent 用户，我希望 Agent 在对话过程中不需要将全部工具的完整 JSON Schema 加载到 context 中，而是通过 Core Tools 常驻 + Deferred Tools 按需搜索加载的双层架构，将 context 占用降低 60% 以上，同时不损失工具可发现性。

**Why this priority**: 当前 49 个工具的完整 schema 占用约 10k-25k tokens。随着 MCP 工具和自定义 Skill 增加，context 膨胀将严重影响对话质量和成本。Deferred Tools 是 context 工程的关键优化，与权限 Preset 并列为最高优先级。

**Independent Test**: 启动一个 Agent 对话，验证初始 context 仅包含 Core Tools 的完整 schema 和 Deferred Tools 的名称列表，LLM 能通过 `tool_search` 找到并使用非 Core 工具。

**Acceptance Scenarios**:

1. **Given** Agent 启动一轮新对话，**When** 系统构建初始工具上下文，**Then** 仅 Core Tools（约 10 个高频工具）以完整 JSON Schema 注入，其余工具仅以 `{name, one_line_desc}` 列表形式呈现。

2. **Given** LLM 需要使用一个 Deferred 状态的工具（如 `docker.run`），**When** LLM 调用 `tool_search` 工具并传入自然语言查询，**Then** 系统返回匹配工具的完整 JSON Schema，LLM 可在后续步骤中调用该工具。

3. **Given** Agent 初始 context 已构建，**When** 对比全量注入模式（所有工具 schema 均加载）的 token 数，**Then** Deferred 模式下初始 context 的工具相关 token 减少至少 60%。

4. **Given** `tool_search` 返回了多个匹配工具的 schema，**When** LLM 选择其中一个进行调用，**Then** 该工具调用仍需经过权限 Preset 检查（与 User Story 1 联动）。

5. **Given** ToolIndex 检索服务不可用（降级场景），**When** LLM 调用 `tool_search`，**Then** 系统回退到 Deferred Tools 名称列表全量返回，并记录降级事件（Constitution 原则 6: Degrade Gracefully）。

6. **Given** MCP Server 注册了外部工具，**When** 系统构建工具上下文，**Then** MCP 工具默认以 Deferred 状态加入名称列表，可通过 `tool_search` 检索。

---

### User Story 3 — Bootstrap 模板最小化：角色卡片替代多模板 (Priority: P2)

作为 OctoAgent 用户，我希望系统不再维护 4 种 Worker Type 各自的 bootstrap 模板，而是统一为一个 shared bootstrap + Agent 实例级角色卡片（总计约 200 tokens），减少维护成本并提升灵活性。

**Why this priority**: 当前 bootstrap 模板本身较为简短（150-340 tokens/agent），改造收益主要体现在架构简洁度和维护成本降低，而非性能。此外，本项依赖 Preset 系统就位后才能完全取代 Worker Type 的角色约束职能。

**Independent Test**: 创建一个 Worker 并验证其 bootstrap 内容仅包含 shared 元信息和角色卡片，且 Worker 行为不因模板简化而出现明显偏差。

**Acceptance Scenarios**:

1. **Given** 系统创建一个新的 Worker，**When** 构建 bootstrap prompt，**Then** bootstrap 由两部分组成：`bootstrap:shared`（核心元信息约 50 tokens）+ 角色卡片（角色定位和优先级描述约 100-150 tokens）。

2. **Given** 系统中不再存在 `bootstrap:general`、`bootstrap:ops`、`bootstrap:research`、`bootstrap:dev` 四个独立模板，**When** Butler 创建 Worker，**Then** Worker 的角色引导通过角色卡片（可在创建时自定义）而非固定模板传达。

3. **Given** 一个 Worker 的角色卡片描述为"你是一个专注于运维任务的执行者"，**When** 该 Worker 收到研发类任务，**Then** Worker 仍可执行（不被硬限制），但倾向于将不匹配的任务汇报给 Butler。

4. **Given** `bootstrap:shared` 内容仅包含 project/workspace/datetime/preset 等核心元信息，**When** 对比旧 shared 模板，**Then** 冗余字段（如重复的治理警告、已由 behavior pack 覆盖的行为指导）被移除。

---

### User Story 4 — Skill-Tool 注入路径优化：Skill 自动提升关联工具 (Priority: P3)

作为 OctoAgent 用户，我希望当 Skill 被加载时，其声明的 `tools_required` 中的工具能够自动从 Deferred 状态提升到活跃状态（完整 schema 可用），避免 LLM 在执行 Skill 时还需要额外搜索依赖工具。

**Why this priority**: 这是对 Deferred Tools 的补充优化，解决 Skill 执行时的工具可用性问题。依赖 Deferred Tools 和 Preset 两个前置能力就位后才可实现。

**Independent Test**: 加载一个声明了 `tools_required: [docker.run, filesystem.write_text]` 的 Skill，验证这些工具自动从 Deferred 提升为活跃状态。

**Acceptance Scenarios**:

1. **Given** 一个 Skill 的 SKILL.md 中声明了 `tools_required: [docker.run, terminal.exec]`，**When** 该 Skill 被加载到 Agent 的活跃 Skill 集合中，**Then** `docker.run` 和 `terminal.exec` 的完整 JSON Schema 自动加入当前对话的活跃工具集。

2. **Given** Skill 声明的 `tools_required` 中包含一个超出 Agent 当前 Preset 允许范围的工具，**When** Skill 被加载，**Then** 该工具仍被提升到活跃集合（schema 可见），但实际调用时触发 soft deny（ask）。

3. **Given** Skill 被卸载（从活跃 Skill 集合中移除），**When** 系统评估活跃工具集，**Then** 仅因该 Skill 被提升的工具（且无其他来源需要）回退到 Deferred 状态。

4. **Given** 多个 Skill 声明了对同一个工具的依赖，**When** 其中一个 Skill 被卸载，**Then** 该工具仍保持活跃状态（因其他 Skill 仍依赖它）。

---

### User Story 5 — 二级审批：运行时审批覆盖 (Priority: P1)

作为 OctoAgent 用户，当工具调用触发 soft deny（ask）时，我希望可以选择 `approve`（本次允许）、`always`（对该工具永久允许）或 `deny`（本次拒绝），并且 `always` 决策被持久化到 Agent 实例级别，后续不再重复询问。

**Why this priority**: 审批覆盖是权限 Preset 体系的核心交互闭环，没有它用户无法对 soft deny 进行有效响应。与 User Story 1 紧密耦合，必须同步交付。

**Independent Test**: 触发一个 soft deny 审批请求，分别测试 approve/always/deny 三种响应的行为。

**Acceptance Scenarios**:

1. **Given** 工具调用触发 soft deny，用户收到审批请求，**When** 用户选择 `approve`，**Then** 本次工具调用被允许执行，下次调用同一工具仍触发审批。

2. **Given** 工具调用触发 soft deny，用户收到审批请求，**When** 用户选择 `always`，**Then** 本次工具调用被允许执行，且该授权持久化到 Agent 实例级别。

3. **Given** 用户曾对某工具选择了 `always`，**When** Agent 进程重启后同一 Agent 实例再次调用该工具，**Then** 该工具调用被直接 allow（`always` 授权跨进程持久化）。

4. **Given** 工具调用触发 soft deny，用户收到审批请求，**When** 用户选择 `deny`，**Then** 本次工具调用被拒绝，LLM 收到明确的拒绝反馈（含原因说明）。

5. **Given** 审批请求已发出但用户未响应，**When** 超过超时时间（默认 600s），**Then** 系统执行默认策略（deny），并记录超时事件。<!-- CLR-004 -->

---

### Edge Cases

- **Preset 不匹配场景**：Worker Preset 为 `minimal`，但接收到的任务需要 `irreversible` 工具 — 系统应触发 soft deny（ask）而非硬拒绝或静默失败，用户可通过审批临时提升权限。
- **tool_search 零命中**：LLM 搜索工具时 ToolIndex 无匹配结果 — 系统应返回空结果并附带提示（如"无匹配工具，请尝试更具体的查询"），LLM 可调整查询重试。
- **MCP Server 断连时的工具状态**：MCP 工具已被 tool_search 加载到活跃集合，但 MCP Server 随后断连 — 工具调用应失败并返回清晰的错误信息（如"工具所属 MCP Server 不可用"），不应静默吞没。
- **并发审批**：同一 Agent 同时触发多个工具的审批请求 — 每个审批请求独立处理，不互相阻塞。
- **always 授权的工具被移除**：用户曾对某工具设置 `always`，但该工具后续被从系统中移除 — 下次加载时忽略无效的 `always` 条目，不影响其他工具。
- **Skill 声明不存在的工具依赖**：Skill 的 `tools_required` 中引用了一个未注册的工具名 — Skill 仍可加载，但系统在加载时记录警告事件，LLM 可收到提示。
- **Core Tools 列表变更**：Core Tools 清单变更后已有的 Agent 对话如何过渡 — 新一轮对话步骤中自动应用最新 Core Tools 列表。
- **审批超时与 LLM 行为**：审批超时 deny 后 LLM 可能立即重试同一工具 — system prompt 应引导"审批被拒后不再重试同一工具"，同时通过 ToolLoopGuard 检测异常重试模式。

## Requirements

### Functional Requirements

#### 一、统一工具可见性 + 权限 Preset 隔离

- **FR-001**: 系统 MUST 砍掉 Worker Type（GENERAL/OPS/RESEARCH/DEV）多模板系统，所有 Agent（Butler、Worker、Subagent）共享同一套统一的工具集（内置 Tools + Skills + MCP 工具）。
- **FR-002**: 系统 MUST 引入三级权限 Preset（`minimal` / `normal` / `full`），基于工具声明的 `side_effect` 等级（`none` / `reversible` / `irreversible`）做出 allow 或 ask 决策：
  - `minimal`: `none`=allow, `reversible`=ask, `irreversible`=ask
  - `normal`: `none`=allow, `reversible`=allow, `irreversible`=ask
  - `full`: `none`=allow, `reversible`=allow, `irreversible`=allow
- **FR-003**: 每个 Agent 实例 MUST 独立配置权限 Preset，创建时必须指定（类似模型选择）。
- **FR-004**: Butler MUST 默认使用 `full` Preset。
- **FR-005**: Worker 创建时如未显式指定 Preset，MUST 默认使用 `normal`。
- **FR-006**: Subagent MUST 继承其所属 Worker 的 Preset，不可独立配置。
- **FR-007**: Preset 不允许的操作 MUST 触发 soft deny（ask），向用户发起审批请求，而非硬拒绝。系统中不存在因 Preset 导致的硬拒绝（Constitution 原则 7: User-in-Control）。
- **FR-008**: Agent 的差异化 MUST 通过以下四个维度组合实现，而非 Worker Type 多模板：MD 人格/目标（角色卡片）、模型选择、权限 Preset、Project/Session 上下文。

#### 二、二级审批机制

- **FR-009**: 系统 MUST 实现二级审批：第一级为 Preset 默认态决策，第二级为用户运行时审批覆盖。
- **FR-010**: 用户审批 MUST 支持三种响应：`approve`（本次允许）、`always`（对该工具永久允许）、`deny`（本次拒绝）。
- **FR-011**: `always` 授权 MUST 持久化到 Agent 实例级别，跨进程重启后仍然有效。
- **FR-012**: `deny` 仅作用于本次调用，MUST NOT 永久封禁该工具。
- **FR-013**: 运行时审批覆盖（`always` 记录）MUST 优先于 Preset 默认态决策——即如果用户曾对某工具选择 `always`，后续调用不再经过 Preset 检查。
- **FR-014**: 审批请求超时 MUST 有默认策略（deny），超时时间 SHOULD 可配置（默认 600s）。<!-- CLR-004: 与现有 ApprovalManager 对齐 -->

#### 三、Deferred Tools 懒加载

- **FR-015**: 系统 MUST 将工具分为两个层级：Core Tools（始终加载完整 JSON Schema，约 10 个高频工具）和 Deferred Tools（仅暴露 `{name, one_line_desc}` 列表）。
- **FR-016**: 系统 MUST 提供 `tool_search` 核心工具，接收自然语言查询，返回匹配工具的完整 JSON Schema，使 LLM 能按需加载 Deferred 工具。
- **FR-017**: `tool_search` MUST 复用现有 ToolIndex 基础设施（cosine + BM25 混合打分），不引入新的独立索引。
- **FR-018**: Core Tools 清单 SHOULD 基于实际使用频率数据确定，MUST 至少包含 `tool_search` 自身（保证 LLM 始终能搜索工具）。
- **FR-019**: `tool_search` 返回的工具 MUST 在后续对话步骤中以完整 schema 注入活跃工具集（运行时动态注入）。
- **FR-020**: 每个工具 MUST 有明确的层级标记（Core 或 Deferred），默认为 Deferred。
- **FR-021**: MCP 注册的外部工具 MUST 默认以 Deferred 状态纳入统一工具集。
- **FR-022**: 当 ToolIndex 检索服务不可用时，`tool_search` MUST 降级为返回 Deferred Tools 名称列表全量（Constitution 原则 6: Degrade Gracefully），并生成降级事件。
- **FR-023**: Deferred 模式下的初始 context 工具相关 token MUST 相比全量注入模式减少至少 60%。

#### 四、Bootstrap 模板最小化

- **FR-024**: 系统 MUST 将 bootstrap 从"shared + 4 个 Worker Type 模板"简化为"shared + Agent 实例级角色卡片"。
- **FR-025**: `bootstrap:shared` MUST 仅包含核心元信息（project、workspace、datetime、preset 等），总量 SHOULD 控制在约 50 tokens。
- **FR-026**: 角色卡片 MUST 仅包含角色定位和优先级描述（约 100-150 tokens），具体行为规范由 behavior pack 系统和 Skill 注入的 system prompt 承担。
- **FR-027**: 角色卡片 SHOULD 支持在 Agent 创建时自定义内容，不再受限于 4 种预定义类型。
- **FR-028**: 原有 Worker Type 多模板文件（`bootstrap:general`、`bootstrap:ops`、`bootstrap:research`、`bootstrap:dev`）MUST 被移除。

#### 五、Skill-Tool 注入路径优化

- **FR-029**: Skill MUST 能够通过 `tools_required` 字段声明其依赖的工具列表。
- **FR-030**: Skill 加载时，系统 MUST 自动将 `tools_required` 中的工具从 Deferred 提升到活跃集合（完整 schema 可用）。
- **FR-031**: Skill 提升的工具 MUST 仍受 Preset 权限约束——超出 Preset 允许范围的工具在实际调用时触发 soft deny（ask）。
- **FR-032**: Skill 卸载时，仅因该 Skill 被提升的工具（无其他来源依赖）SHOULD 回退到 Deferred 状态。

#### 六、可观测性与事件记录

- **FR-033**: Preset 检查结果（allow 或 ask）MUST 生成事件记录（Constitution 原则 2: Everything is an Event）。
- **FR-034**: `tool_search` 的每次调用及其结果 MUST 生成事件记录。
- **FR-035**: 用户审批决策（approve/always/deny）MUST 生成事件记录。
- **FR-036**: Skill 加载导致的工具提升 MUST 生成事件记录。
- **FR-037**: ToolIndex 降级事件 MUST 被记录。

#### 七、兼容性与迁移

- **FR-038**: 现有 ToolProfile（minimal/standard/privileged）三级体系 MUST 平滑演进为 PermissionPreset（minimal/normal/full），不引入并存的重复概念。
- **FR-039**: 现有工具的 `@tool_contract` 注册机制 MUST 保持向后兼容，新增层级标记为可选参数。
- **FR-040**: 所有通过 `tool_search` 加载的 Deferred 工具，其 schema MUST 仍经过完整的 schema 反射机制（Constitution 原则 3: Tools are Contracts），确保 schema 与代码签名一致。

### Key Entities

- **PermissionPreset**: Agent 实例级权限等级（`minimal` / `normal` / `full`），决定工具调用的默认 allow/ask 策略。取代现有 ToolProfile 的角色。
- **ToolTier**: 工具层级标记（`CORE` / `DEFERRED`），决定工具在初始 context 中的呈现方式——完整 schema 或仅名称+描述。
- **ApprovalOverride**: 用户运行时审批决策的持久化记录，存储 `always` 授权条目，绑定到 Agent 实例。
- **RoleCard**: Agent 实例级角色卡片，替代 Worker Type 多模板，包含角色定位和优先级描述。

## Success Criteria

### Measurable Outcomes

- **SC-001**: Deferred 模式下，Agent 初始 context 的工具相关 token 占用相比全量注入模式减少至少 60%（通过 token 计数对比验证）。
- **SC-002**: 权限 Preset 检查的延迟不超过 1ms（内存级操作），不对工具调用链路引入可感知的性能开销。
- **SC-003**: 所有工具调用（含 Core 和 Deferred）100% 经过 Preset 权限检查，无旁路（通过事件记录审计验证）。
- **SC-004**: `tool_search` 在当前工具规模（~49 内置 + MCP）下的检索延迟不超过 10ms。
- **SC-005**: `always` 授权在 Agent 进程重启后仍然有效（持久化验证）。
- **SC-006**: Bootstrap 模板简化后，每个 Agent 的 bootstrap token 总量控制在 200 tokens 以内。
- **SC-007**: Worker Type 多模板系统（4 个 bootstrap 文件 + WorkerType 枚举 + `default_tool_groups` 矩阵）被完全移除，代码库中不再存在相关遗留。
- **SC-008**: LLM 在 Deferred 模式下，通过 `tool_search` 成功定位到目标工具的比例不低于全量注入模式下的直接调用成功率（通过 A/B 测试或回归测试验证）。
- **SC-009**: 所有 Preset 检查、审批决策、工具提升事件均可在 Web UI 事件流中查看（Constitution 原则 8: Observability is a Feature）。

## Ambiguity Resolution

- **ToolProfile 与 PermissionPreset 的关系**：现有 `ToolProfile`（minimal/standard/privileged）将被重命名/演进为 `PermissionPreset`（minimal/normal/full），不引入两套并存概念。`standard` 映射为 `normal`，`privileged` 映射为 `full`。迁移过程中旧接口保持短期兼容。
- **Core Tools 具体清单**：本 spec 不硬编码 Core Tools 清单（属于 HOW 层面），仅约束"必须至少包含 `tool_search` 自身"和"约 10 个高频工具"。具体清单建议基于 Event Store 中工具调用频率统计确定。
- **Preset 是否可自定义**：v0.1 阶段仅支持三级固定 Preset（minimal/normal/full），不支持用户自定义 Preset。自定义 Preset 作为后续扩展保留。
- **soft deny 的 UX 表现**：soft deny 触发后的审批交互形式（弹窗/消息/通知）由前端 UX 层决定，本 spec 仅约束后端行为（触发 ask + 等待响应 + 超时处理）。
- **Deferred Tools 与 Claude API `defer_loading` 的关系**：本 Feature 在框架层（Pydantic AI DynamicToolset）实现 Deferred Tools，保证模型无关性。Claude API 原生 `defer_loading` 可作为未来针对 Claude 模型的加速优化，不在本 Feature 范围内。
- **角色卡片生成方式**：角色卡片可以在 Agent 创建时由 Butler 或用户提供，也可以基于 Preset + 已加载 Skill + 创建目标自动生成。自动生成逻辑的具体策略属于实现层面，本 spec 仅约束"角色卡片必须存在且内容约 100-150 tokens"。

## Clarifications

> 以下澄清项由需求澄清阶段（2026-03-17）通过结构化歧义扫描识别并解决。

### CLR-001: `always` 授权持久化存储位置 [RESOLVED — 用户选择方案 A]

**歧义描述**: FR-011 要求 `always` 授权"持久化到 Agent 实例级别，跨进程重启后仍然有效"。但现有 `ApprovalManager._allow_always` 实现为纯内存 `dict[str, bool]`（代码注释明确标注 "M1 仅内存，不持久化"），且 Feature 006 的设计决策记录为 "M1 仅内存，M2 持久化到 SQLite"。

**影响范围**: SC-005（持久化验证）、FR-011、FR-013（覆盖优先级）、User Story 5 场景 3（重启后仍有效）。

**决策**: 方案 A — 新增 SQLite 表 `approval_overrides`，存储 `(agent_runtime_id, tool_name, decision, created_at)`。独立表结构最清晰，与 FR-011 的"Agent 实例级别"语义直接对齐，且支持 Web UI 查看/管理 always 授权列表（SC-009 可观测性要求）。

**决策时间**: 2026-03-17，用户确认。

---

### CLR-002: `always` 授权的作用域隔离 [AUTO-CLARIFIED]

**歧义描述**: FR-011 说"持久化到 Agent 实例级别"，但现有 `ApprovalManager._allow_always` 是全局 `tool_name -> True` 映射，没有按 Agent 实例（`agent_runtime_id`）隔离。如果 Worker A 对工具 X 设置了 `always`，Worker B 是否也自动获得该授权？

**自动解决**: `always` 授权 MUST 绑定到 `agent_runtime_id`，不同 Agent 实例之间的 `always` 授权互相独立。理由——FR-011 明确说"Agent 实例级别"，且 Constitution 原则 5（Least Privilege by Default）要求最小权限分区。一个 Worker 的提权决策不应自动扩散到其他 Worker。

**对实现的影响**: `ApprovalManager` 的 `_allow_always` 数据结构需从 `dict[str, bool]` 改为 `dict[tuple[str, str], bool]`（`(agent_runtime_id, tool_name) -> True`），或由外层按 Agent 实例分发独立的 ApprovalManager / Override 存储。

---

### CLR-003: Preset `full` 与 FR-010a PolicyCheckpoint 强制拒绝的关系 [AUTO-CLARIFIED]

**歧义描述**: 现有 `broker.py` 的 FR-010a 逻辑（L286-309）在没有 `fail_mode=CLOSED` 的 BeforeHook 时，会强制拒绝所有 `irreversible` 工具调用。但 spec FR-002 定义 `full` Preset 允许 `irreversible` 工具直接执行（allow）。如果 `PresetBeforeHook` 对 `full` Preset 放行 irreversible 工具，但 PolicyCheckpoint hook 未注册，FR-010a 仍会硬拒绝。

**自动解决**: `PresetBeforeHook` 的引入替代了现有 broker.py 中硬编码的 Profile 权限检查逻辑。FR-010a 的 PolicyCheckpoint 强制拒绝逻辑是旧安全兜底，在 Preset 体系下由 Preset + 审批覆盖替代。实现时应将 broker.py L272-283 的硬编码 Profile 检查和 L286-309 的 PolicyCheckpoint 强制拒绝逻辑重构为 Hook Chain 驱动：`PresetBeforeHook` 统一判断 allow/ask，`full` Preset 的 `irreversible` 工具走 allow 路径。原有 FR-010a 的安全意图由 Preset 默认态（`minimal`/`normal` 对 `irreversible` 触发 ask）和 Constitution 原则 4（Two-Phase）共同保障。

---

### CLR-004: 审批超时默认值不一致 [AUTO-CLARIFIED]

**歧义描述**: spec FR-014 定义审批超时默认 120s，但现有 `ApprovalManager.__init__()` 的 `default_timeout_s` 为 600.0（10 分钟）。Feature 061 的超时值与 Feature 006 现有实现存在冲突。

**自动解决**: 采用现有 ApprovalManager 的 600s 默认值，而非 spec 中的 120s。理由——10 分钟给予用户更充足的审批时间（移动端用户可能不会立即响应），且 Feature 006 已经过实际验证。FR-014 的描述修正为"默认 600s"。

**对 spec 的影响**: FR-014 中"默认 120s"应修正为"默认 600s"。此修正已在下方 FR-014 补丁中标注。

> **FR-014 补丁**: 审批请求超时 MUST 有默认策略（deny），超时时间 SHOULD 可配置（默认 **600s**）。

---

### CLR-005: ToolBroker 现有 Profile 硬拒绝到 Preset soft deny 的迁移路径 [AUTO-CLARIFIED]

**歧义描述**: spec FR-007 要求 Preset 不允许的操作触发 soft deny（ask），但现有 `broker.py` 的 Profile 权限检查（L272-283）直接返回 `is_error=True` 的 `ToolResult`（硬拒绝），没有 ask/审批桥接。迁移路径不明确——是改造 `execute()` 方法内联逻辑，还是将权限检查完全移入 Hook Chain？

**自动解决**: 与 tech-research.md 推荐方案 C（Policy Engine 集成）对齐——将 broker.py L272-283 的硬编码 Profile 检查完全移除，由 `PresetBeforeHook`（Hook Chain 中的 BeforeHook）替代。`PresetBeforeHook` 对不允许的操作返回 `BeforeHookResult(proceed=False, rejection_reason="ask:preset_denied:...")`，上层通过 `rejection_reason` 的 `ask:` 前缀识别为 soft deny 并桥接到 Pydantic AI 的 `ApprovalRequired` 异常或现有 `ApprovalManager` 审批流。这确保所有权限决策统一走 Hook Chain，与现有 `FailMode.CLOSED` / `FailMode.OPEN` 机制一致。
