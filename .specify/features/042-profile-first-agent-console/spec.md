---
feature_id: "042"
title: "Profile-First Tool Universe + Agent Console Reset"
milestone: "M4"
status: "Draft"
created: "2026-03-13"
updated: "2026-03-13"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §2、§2732-2777；Feature 026（Control Plane Contract）、Feature 030（Capability Pack / ToolIndex）、Feature 033（Agent Context Continuity）、Feature 035（Guided User Workbench）、Feature 039（Supervisor Worker Governance）、Feature 041（Dynamic Root Agent Profiles）；Agent Zero tool/profile layering；OpenClaw dashboard/settings UX"
predecessor: "Feature 030（工具治理与 ToolIndex）、Feature 035（Workbench 壳）、Feature 039（Butler + worker review/apply）、Feature 041（WorkerProfile / Profile Studio）"
parallel_dependency: "042 不替代 041，而是把 041 的 WorkerProfile 真正接入默认聊天主链与 Agent 页面主工作台。"
---

# Feature Specification: Profile-First Tool Universe + Agent Console Reset

**Feature Branch**: `codex/feat-042-profile-first-agent-console`  
**Created**: 2026-03-13  
**Updated**: 2026-03-13  
**Status**: Draft  
**Input**: 不再把默认聊天的工具可见性建立在一层硬 top-k tool selection 上，而是改成 `Profile-first`：先确定当前 Agent / Root Agent profile 与治理边界，再把这个 Agent 天然拥有的核心工具宇宙挂载给模型，由模型自己决定是否调用。与此同时重构 Agent 页面 UI，把当前过于晦涩、对象分层不清的 `AgentCenter` 收敛成真正面向用户的 Agent 工作台，并参考 Agent Zero / OpenClaw 的优点，让用户能清楚理解“当前默认 Agent、当前工作、当前能力和为什么这次没做到”。  
**调研基础**: `research/product-research.md`、`research/tech-research.md`、`research/research-synthesis.md`、`research/online-research.md`

## Problem Statement

OctoAgent 当前已经通过 041 引入了正式的 `WorkerProfile / Root Agent` 主链，但真正的默认 Agent 体验仍有四个结构性问题：

1. **默认聊天仍然是 tool-selection first，而不是 profile-first**  
   当前 chat 首跳主要还是 `message -> worker_type -> ToolIndexQuery -> selected_tools_json -> llm`。这会让模型经常只能看到一小撮被系统猜出来的工具，而不是当前 Agent 稳定拥有的能力边界。

2. **041 的 Root Agent profile 还没有进入普通 chat 主链**  
   用户已经可以在 Agent 页面创建/发布 Root Agent，但普通聊天入口仍然没有稳定绑定 `agent_profile_id`。用户视角会感受到“Agent 页面里有 Agent，聊天页里却没有”。

3. **delegation/handoff 核心工具经常被当前工具裁剪链路隐藏**  
   这会导致系统看起来像“不会委派”“不会起 subagent”，但本质上往往是工具可见性问题，而不是模型策略问题。

4. **Agent 页面仍然太乱、太晦涩、太像系统实现视图**  
   041 虽然把 Root Agent / Profile Studio 搭起来了，但页面仍混杂 starter template、profile、runtime、capability、control-plane 术语。用户很难快速回答：
   - 我现在默认在用哪个 Agent？
   - 它现在在干什么？
   - 它为什么能/不能做这件事？

因此，042 的目标不是“给 weather/news 加更多 case-by-case 特判”，而是：

> 把默认 Agent 体验从 `tool selection first` 改成 `profile-first tool universe`，并把 Agent 页面重构成围绕 `Root Agent / Current Work / Tool Access` 的清晰工作台。

## Product Goal

交付一条新的默认 Agent 主链：

- 普通 chat 会显式或隐式绑定当前 Agent / Root Agent profile
- 系统先解析 `effective profile -> effective tool universe`，再把核心工具集交给模型自由选择
- `ToolIndex` 退居 discovery / explainability / long-tail 工具搜索，不再作为默认聊天的主闸门
- delegation/handoff 核心工具在合适的 profile/policy 边界内稳定可见
- `AgentCenter` 重组为用户真正可读、可控的 Agent 工作台
- `ControlPlane` 保持深度诊断与审计角色，不再与 Agent 工作台争夺同一层产品职责

## Scope Alignment

### In Scope

- 普通 chat 请求的 profile 绑定能力
- profile-first tool universe 解析层
- `selected_tools_json` 的兼容升级与 tool resolution trace
- delegation/handoff 核心工具常驻挂载策略
- `ToolIndex` 角色调整为 discovery / explainability
- Agent 页面 IA/UI 重组
- Agent 页面静态配置 + 动态上下文 + tool access 的一屏联动
- runtime truth 中新增 tool resolution explainability
- category-based acceptance matrix 作为验证边界

### Out of Scope

- 新增 weather/news/finance 等单独业务工具作为本 Feature 核心方案
- 多实例 Root Agent runtime registry
- 完整 eval dashboard 与自动 prompt 优化面板
- 团队级 Agent library / marketplace
- 推翻 041 的 `worker_profiles` canonical resource 与 Profile Studio 主链
- 删除 ToolIndex、本地 capability pack 或 policy 系统

## Architecture Boundary

### Canonical Backend Rules

- 所有 profile 相关读写 MUST 继续复用 041 已建立的 `worker_profiles` canonical resources/actions
- 默认聊天的变更 MUST 接在现有 `chat -> task_service -> agent_context -> delegation -> llm_service` 主链上，不得新造平行 chat runtime
- 工具边界 MUST 继续受 ToolBroker / capability pack / MCP registry / policy 控制
- 042 MUST 不通过“硬编码 case 特判”来解决 weather/news 等问题，而应通过通用 tool resolution 机制解决
- `ToolIndex` MAY 继续存在，但 MUST 退化为可选 discovery / explainability 组件，而不是默认聊天主裁判

### Existing Interface Mapping

#### Chat Binding

- 扩展：
  - `POST /api/chat/send`
  - 前端 chat send payload
- 要求：
  - 支持可选 `agent_profile_id`
  - 未显式指定时，按 session / project / system default 回退

#### Tool Resolution

- 读取：
  - `snapshot.resources.worker_profiles`
  - `snapshot.resources.capability_pack`
  - `project_selector`
  - `delegation`
- 新增/扩展：
  - `effective_tool_universe`
  - `tool_resolution_mode`
  - `tool_resolution_trace`
  - `tool_resolution_warnings`

#### Agent Console

- 继续复用：
  - `worker_profiles`
  - `delegation`
  - `capability_pack`
  - `sessions`
- 责任调整：
  - `AgentCenter`：主工作台
  - `ControlPlane`：深度审计 / trace / raw runtime lens

## User Scenarios & Testing

### User Story 1 - 默认聊天会按当前 Agent 的真实能力工作 (Priority: P1)

作为用户，我希望普通聊天默认继承我当前 Project/Session 的 Root Agent，而不是每次都回到一个只会解释限制的泛化 Butler。

**Why this priority**: 这是 042 的核心价值。如果默认聊天还不按当前 Agent 的能力工作，041 的 Root Agent 资产对日常使用价值依旧有限。

**Independent Test**: 绑定一个具备网页/运行态/委派能力的 Root Agent profile，在普通 chat 中分别提出实时外部事实、项目检查、委派型问题，验证系统按 profile 工具宇宙运行，而不是先解释自己为什么不行。

**Acceptance Scenarios**:

1. **Given** 当前 project 绑定了一个具备网页能力的 Root Agent，**When** 用户在普通 chat 提出依赖网页事实的问题，**Then** 系统应优先尝试调用该 Agent 可见的 web 工具，而不是先声称自己没有实时能力。
2. **Given** 当前 session 显式切换到了另一个 Agent profile，**When** 用户继续聊天，**Then** 后续请求应使用该 session 级 Agent 的工具宇宙，而不是继续沿用 project 默认 profile。

---

### User Story 2 - Butler 可以稳定地委派，而不是因为工具不可见而“假装不会” (Priority: P1)

作为用户，我希望当问题更适合拆给 subagent/worker 时，Butler 能稳定调用委派工具，而不是因为系统没把 delegation 工具挂载出来，表现得像不会委派。

**Why this priority**: 这直接决定 OctoAgent 是否像一个 Agent 系统，而不是偶尔会回答问题的聊天 UI。

**Independent Test**: 在 profile 允许 delegation 的情况下，给出需要拆分/外部调研/长链路执行的目标，验证系统可以稳定看到并使用 `workers.review`、`subagents.spawn` 等核心委派工具。

**Acceptance Scenarios**:

1. **Given** 当前 Agent profile 允许 delegation 且 policy 允许 `standard` 工具，**When** 用户提出适合拆分的复杂任务，**Then** 模型应能看到 delegation 核心工具并可执行 review/spawn。
2. **Given** 当前 policy 或 readiness 不允许某些 delegation 动作，**When** 用户提出复杂任务，**Then** 页面和 runtime truth 应明确解释“为何当前不能委派”，而不是静默缺失。

---

### User Story 3 - 我能在 Agent 页面快速看懂“当前默认 Agent / 当前工作 / 当前能力” (Priority: P1)

作为用户，我希望 Agent 页面首先告诉我：现在默认是谁、它在做什么、它有哪些能力和限制，而不是让我先理解 capability pack、worker archetype、runtime 投影这些内部概念。

**Why this priority**: UI 混乱是当前体验的重要问题。如果页面仍然晦涩，042 只会把更多能力堆进一个更难懂的界面。

**Independent Test**: 在不打开 ControlPlane 的情况下，仅通过 Agent 页面完成“识别默认 Agent、查看当前 work、判断为什么某工具不可用”的任务。

**Acceptance Scenarios**:

1. **Given** 用户进入 Agent 页面，**When** 页面加载完成，**Then** 用户能在一个稳定布局里看到 Root Agents 列表、当前选中 Agent 的详情，以及右侧 runtime/tool access inspector。
2. **Given** 某个 Agent 的部分工具当前 degraded 或 unreadied，**When** 用户查看该 Agent，**Then** 页面以 badge/callout 直接解释原因和下一步动作。

---

### User Story 4 - 我能回看某次运行到底拿到了哪些工具、为什么会这样 (Priority: P2)

作为用户，我希望在查看某次 work 或当前 Agent 时，能知道这次运行实际挂载了哪些工具、哪些是 profile 固定能力、哪些是 discovery 附加能力，以及为什么某些工具没给。

**Why this priority**: 如果没有 explainability，Profile-first 只是把隐式复杂度从 ToolIndex 挪到了别的地方。

**Independent Test**: 触发一次由 Root Agent 驱动的任务，查看 work/runtime inspector，验证能看到 tool resolution 结果与原因。

**Acceptance Scenarios**:

1. **Given** 当前 work 已执行，**When** 用户打开 inspector，**Then** 页面展示 `profile core tools / runtime-added tools / blocked or unavailable tools`。
2. **Given** 某工具因 policy、tool_profile 或 readiness 被阻止，**When** 用户查看原因，**Then** 系统给出结构化 explanation，而不是只显示“没选中”。

---

### User Story 5 - 长尾工具仍然能被发现，但不会干扰默认聊天主链 (Priority: P2)

作为用户，我希望默认聊天先稳定使用这个 Agent 的核心工具带；如果需要更长尾的工具，也应该能搜索和发现，但不要让默认行为继续依赖不稳定的语义猜测。

**Why this priority**: 这是“稳定能力边界”和“可扩展工具生态”之间的平衡点。

**Independent Test**: 在核心工具带之外，使用工具发现入口搜索某个长尾工具并成功加入/调用，同时默认聊天仍保持稳定。

**Acceptance Scenarios**:

1. **Given** 当前 Root Agent 只有核心工具带，**When** 用户或模型需要使用一个长尾工具，**Then** 系统可通过 discovery 机制检索并解释该工具，而不是默认每轮都做全量语义猜测。

## Edge Cases

- 当当前 project 默认 profile 已归档或丢失时，系统必须安全回退到可解释的默认 Butler profile，并显示 warning。
- 当某个 Root Agent 没有 `selected_tools` 和 `default_tool_groups` 时，系统必须明确标注“当前主要依赖 discovery”，而不是静默进入表现不稳定状态。
- 当 legacy work 只有 `selected_worker_type` 与旧 `tool_selection` 数据时，页面必须继续可读，并标注为 legacy resolution。
- 当 profile 理论上具备某能力，但 MCP server / browser session / secret binding 未就绪时，系统必须把“不可用原因”展示出来。
- 当核心工具宇宙过大时，系统必须有压缩/分层策略，避免把几十上百个工具无差别塞进模型上下文。

## Functional Requirements

- **FR-001**: 系统 MUST 在普通 chat 主链中解析当前 `agent_profile_id`，并支持 `session > project > system` 的回退顺序。
- **FR-002**: `ChatSendRequest` 与前端聊天发送逻辑 MUST 支持可选 `agent_profile_id`。
- **FR-003**: 系统 MUST 在调用 LLM 前先解析 `effective profile -> effective tool universe`，而不是先跑硬 top-k tool selection。
- **FR-004**: `effective tool universe` MUST 至少包含：profile 固定 `selected_tools`、profile `default_tool_groups` 展开的核心工具、以及治理要求的 session/project/supervision/delegation 核心工具。
- **FR-005**: `ToolIndex` MUST 在 042 中降级为 discovery / explainability / long-tail 工具检索组件，而不是默认聊天的主闸门。
- **FR-006**: 当当前 Agent profile 与 policy 允许 delegation 时，系统 MUST 稳定暴露 delegation 核心工具，而不应让其经常因为 tool selection 被隐藏。
- **FR-007**: 运行时 MUST 继续保留 `selected_tools_json` 兼容字段，但其语义 MUST 升级为“本次实际挂载给模型的核心工具集”。
- **FR-008**: 运行时 MUST 新增 `effective_tool_universe`、`tool_resolution_mode`、`tool_resolution_trace` 和 `tool_resolution_warnings` 等 explainability 字段。
- **FR-009**: `ControlPlane` 与 Agent 页面 MUST 能解释某工具为何可用、不可用、降级或未挂载。
- **FR-010**: 042 MUST 不通过新增 weather/news/finance 等单个 case 特判来修复默认 Agent 行为，而应通过通用 tool resolution 机制解决。
- **FR-011**: `AgentCenter` MUST 重构为面向用户的 Agent 工作台，至少稳定展示 `Root Agents / Agent Detail / Runtime Inspector` 三个主区域。
- **FR-012**: Agent 页面 MUST 直接展示 `当前默认 Agent / 当前工作 / 当前能力边界 / 当前 warnings`，而不是要求用户跳转多个页面拼凑事实。
- **FR-013**: `ControlPlane` MUST 继续保留深度 runtime lineage、raw projection 和审计视角，不与 Agent 页面争夺同层信息架构职责。
- **FR-014**: 042 MUST 兼容 041 的 `worker_profiles` canonical resource、Profile Studio、legacy `selected_worker_type` 和旧 work 记录。
- **FR-015**: 042 MUST 为后续 verify 提供 category-based acceptance matrix，至少覆盖：实时外部事实、项目上下文、delegation/handoff、runtime diagnostics 四类任务。

## Key Entities

- **EffectiveToolUniverse**: 当前 chat/work 绑定 profile 后解析出的稳定工具宇宙，包含核心工具集、降级信息和来源 profile。
- **ToolResolutionTrace**: 解释一次运行中工具为何被挂载、被阻止、被降级或通过 discovery 附加的结构化记录。
- **ToolAvailabilityExplanation**: 面向 UI 展示的工具可用性说明对象，承载 readiness、policy、tool_profile、connector 状态等原因。
- **AgentConsoleView**: Agent 页面聚合视图模型，统一表达 Root Agent 静态配置、当前工作、tool access 与 warnings。
- **CategoryAcceptanceMatrix**: 按任务类别组织的验证矩阵，用于证明 042 修复的是通用能力问题，而不是单一 case。

## Success Criteria

### Measurable Outcomes

- **SC-001**: 在 category-based acceptance matrix 中，具备相应能力的 Agent profile 对“实时外部事实 / 项目上下文 / delegation / runtime diagnostics”四类任务的首轮有效工具或 handoff 触发率达到 90% 以上。
- **SC-002**: 用户可以在 Agent 页面 30 秒内完成 3 个判断：当前默认 Agent 是谁、它当前是否在工作、当前主要 warning 是什么。
- **SC-003**: 用户可以在不进入 ControlPlane 的情况下，完成 Root Agent 识别、当前工具边界查看和一次 launch/continue 决策。
- **SC-004**: 旧的 `selected_worker_type` work 记录在 042 上线后仍可查看，不出现 runtime truth 断裂或页面空白。
- **SC-005**: 默认聊天路径不再依赖 weather/news 等专门 case 补丁，即使扩展到 NAS、软路由、打印机、财经等场景，也能通过同一套 profile-first tool resolution 机制扩展。
