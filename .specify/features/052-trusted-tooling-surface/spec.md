---
feature_id: "052"
title: "Trusted Tooling Surface & Permission Relaxation"
milestone: "M4"
status: "Implemented"
created: "2026-03-14"
updated: "2026-03-14"
research_mode: "codebase-scan"
blueprint_ref: "docs/blueprint.md §2 Constitution；§5.1.4 Skills / Tools；§8.4 Skills；§8.5.6 MCP 工具集成；Feature 030/032/036/041/042/046/051"
predecessor: "Feature 030（capability pack / ToolIndex）、Feature 032（built-in tool suite）、Feature 036（guided setup governance）、Feature 041/042（profile-first tool universe）、Feature 046（capability provider centers）、Feature 051（session-native runtime）"
---

# Feature Specification: Trusted Tooling Surface & Permission Relaxation

**Feature Branch**: `codex/052-trusted-tooling-surface`  
**Created**: 2026-03-14  
**Updated**: 2026-03-14  
**Status**: Implemented  
**Input**: 基于当前 OctoAgent 与 Agent Zero / OpenClaw 的源码级对比，重新设计 Tooling / MCP / Skills 的默认权限面：不再把“默认不给工具”当主安全策略，而是给主 Agent 与 Worker 一个更接近本地 coding/runtime agent 的 ambient tool surface，同时保留 OctoAgent 现有 approval / policy / audit / durability 主链。

## Problem Statement

当前 OctoAgent 在 Tooling / MCP / Skills 侧已经具备完整治理底座，但默认运行体验仍明显偏保守，和 Agent Zero / OpenClaw 存在四个结构性差距：

1. **默认 worker tool profile 过窄，主循环天然行动力不足**  
   `general / research / ops / dev` 默认仍从 `minimal` 起步，很多正常的受治理可逆工具默认不挂载。结果是模型常常先被限制成“看起来能干很多，实际上默认只能 inspect/search 少量工具”。

2. **MCP server 已配置但默认仍不自动进入工具面**  
   当前 MCP 工具虽然能根据 annotation 推导 `minimal / standard / privileged`，但 provider 选择层仍把 server 默认视作未启用，导致“server 已装好但模型默认还是看不见”。

3. **Skills 仍以硬白名单方式割裂 ambient tool surface**  
   当前 skill runtime 只有 `tools_allowed` 里的工具才会暴露 schema，并且执行时再次硬拒绝不在白名单里的调用。这样 skill 更像一个权限沙箱，而不是一个在受治理环境里运行的 agent workflow。

4. **LLM 仍主要只看到 `selected_tools`，而不是完整的实际工具宇宙**  
   现在 `selected_tools` 更接近“系统替模型挑出来的一小撮工具”，而不是推荐子集。模型对 `mounted / blocked / why blocked` 的理解仍然不完整，导致主循环的工具决策质量偏弱。

结果是：

> OctoAgent 已经拥有比 Agent Zero / OpenClaw 更强的 policy / approval / audit / durability 底座，但默认工具面仍然偏“先砍权限”，而不是“先给足可逆工具，再对危险动作单独 gate”。

## Product Goal

把 OctoAgent 的工具默认面收口成更 agent-native 的 trusted local 运行模式：

- 在受信本地 workspace 中，`general / research / dev` 默认获得 `standard` 级 ambient tool surface；`ops` 继续保留更强治理但不再被最小权限锁死
- MCP provider 新增 `mount_policy`，默认自动挂载只读/无副作用工具，不再要求用户为每个 server 再做一次显式启用
- Skills 新增 `permission_mode`，默认继承当前运行时工具面，仅在显式 `restrict` 时再用 `tools_allowed` 收窄
- LLM prompt 与 runtime contract 从“只看 `selected_tools`”升级为“完整 `mounted_tools` + `blocked_tools` + `recommended_tools`”
- 所有不可逆或高风险动作仍继续走 ToolBroker / Policy / Approval / Audit 主链，不因为默认权限放宽而旁路治理

## Scope Alignment

### In Scope

- `trusted_local` 默认工具基线
- `McpProvider.mount_policy = explicit | auto_readonly | auto_all`
- `SkillProvider.permission_mode = inherit | restrict`
- `recommended_tools` contract 与 `selected_tools_json` 兼容升级
- `mounted_tools / blocked_tools / reason` 注入 LLM runtime 与 control plane
- capability pack / ToolIndex / LLMService / SkillRunner / MCP registry / control plane / tests / docs

### Out of Scope

- 放开 `privileged` 成为默认 ambient 权限
- 绕过 ToolBroker / Policy / approval
- 新建平行工具执行器
- 重做整个 AgentCenter / Settings 视觉框架
- 为每个第三方 MCP server 自动信任所有可写/破坏性工具

## User Stories & Testing

### User Story 1 - 本机默认 agent 应该能直接使用一组足够实用的标准工具 (Priority: P1)

作为本机单用户 operator，我希望 `general / research / dev` 在 trusted local 环境里默认就有足够可用的标准工具，而不是一上来就被压成 `minimal`。

**Why this priority**: 这是当前和 Agent Zero / OpenClaw 差距最大的地方。如果默认权限面仍然过窄，主循环再强也会表现得像“行动力不足”。

**Independent Test**: 启动 trusted local 环境下的 general/research/dev runtime，验证 `tool_profile` 默认提升为 `standard`，并且实际挂载的工具宇宙包含 session/project/web/browser/delegation 等标准工具族；对不可逆工具仍通过 policy 被拒绝或要求审批。

**Acceptance Scenarios**

1. **Given** 当前运行在 trusted local workspace，**When** 创建 general worker 或 Butler inline runtime，**Then** 其默认 `tool_profile` 为 `standard`，而不是 `minimal`。
2. **Given** 当前请求会命中 `standard` 级 web/browser 或 delegation 工具，**When** 运行主循环，**Then** 这些工具会直接进入 mounted tool universe，而不是必须先靠 special-case 提升 profile。
3. **Given** 某个工具声明为 `privileged` 或 `irreversible`，**When** agent 尝试执行，**Then** 系统仍按 policy/approval gate 拦截，而不是因为 trusted local 就静默执行。

---

### User Story 2 - 已安装的 MCP server 应默认自动挂载只读工具 (Priority: P1)

作为使用 MCP 扩展能力的用户，我希望 server 一旦安装完成，只读/无副作用工具默认就能进入 ambient tool surface，而不是还要再做一次显式 provider enable。

**Why this priority**: MCP 当前是双重默认关闭，严重削弱了“扩展即能力”的体验。

**Independent Test**: 新增一个提供 read-only 工具的 MCP server，在 `mount_policy=auto_readonly` 下刷新 capability pack；验证该 server 的只读工具会默认出现在 mounted tool universe，而 `standard/privileged` 工具仍需显式允许。

**Acceptance Scenarios**

1. **Given** MCP server 已成功注册，且 `mount_policy=auto_readonly`，**When** capability pack 刷新，**Then** 该 server 的 `minimal` 工具默认被选中。
2. **Given** 同一个 server 同时提供 `minimal` 和 `standard` 工具，**When** runtime 构建工具宇宙，**Then** `minimal` 工具自动挂载，`standard` 工具保持 blocked 并给出原因。
3. **Given** 用户把 `mount_policy` 改为 `explicit`，**When** 再次刷新，**Then** 该 server 不会再默认挂载工具，行为回退到显式选择模式。

---

### User Story 3 - Skills 默认继承 ambient tool surface，而不是被硬白名单锁死 (Priority: P1)

作为定义 skill workflow 的用户，我希望 skill 默认继承当前 agent 的工具面，只在需要收紧边界时再显式声明白名单。

**Why this priority**: 这决定 Skills 是“工具受治理的 agent workflow”，还是“被硬编码白名单锁住的迷你沙箱”。

**Independent Test**: 创建一个 `permission_mode=inherit` 的 skill provider，在拥有 `recommended_tools + mounted_tools` 的运行上下文中执行；验证模型可看到 ambient tool schemas，且执行时仍受 ToolBroker / Policy 约束。再创建 `permission_mode=restrict` 的 skill，验证仅允许 `tools_allowed`。

**Acceptance Scenarios**

1. **Given** Skill provider 使用 `permission_mode=inherit`，**When** LLMService 构造 inline skill manifest，**Then** 工具 schema 来自当前 runtime 的实际 mounted tool universe，而不是只来自 `tools_allowed`。
2. **Given** Skill provider 使用 `permission_mode=restrict` 且声明 `tools_allowed`，**When** skill 执行工具调用，**Then** 不在白名单中的工具会被拒绝。
3. **Given** 用户未显式设置 `permission_mode`，**When** 创建新的自定义 skill provider，**Then** 默认模式为 `inherit`，而不是继续默认为 `minimal + empty tools_allowed`。

---

### User Story 4 - 模型应该看到完整工具宇宙和推荐子集，而不是只看到被裁剪后的 top-N (Priority: P1)

作为主循环 Agent，我希望 prompt 中同时拿到 `mounted / blocked / recommended` 三种工具信息，让我自己判断用什么工具，而不是只拿到系统提前裁剪的少量 `selected_tools`。

**Why this priority**: 这直接决定工具选择是否 truly agentic。

**Independent Test**: 对同一请求构建 tool universe，验证 runtime metadata 同时包含 `recommended_tools`、`mounted_tools`、`blocked_tools`；LLM prompt 会读取 `recommended_tools` 作为强推荐，但不会把其当作唯一可见工具集合。

**Acceptance Scenarios**

1. **Given** ToolIndex 给出推荐结果，**When** runtime 生成 metadata，**Then** `recommended_tools` 与 `mounted_tools` 同时存在，且 `recommended_tools` 只是 mounted universe 的子集。
2. **Given** 某个工具因 policy/profile/mount_policy 被挡住，**When** 生成 tool hints，**Then** 该工具会出现在 `blocked_tools` 中，并带 `reason_code / summary / recommended_action`。
3. **Given** 老链路仍依赖 `selected_tools_json`，**When** 兼容字段生成，**Then** 它仍可用，但语义升级为 `recommended_tools` 的兼容映射，而不是唯一真实工具面。

## Edge Cases

- 当运行环境不是 trusted local 时，默认权限不得静默放宽，必须回退到原有更保守基线。
- 当 MCP tool 缺少 annotation 时，系统不得把它误判为只读自动挂载；应按现有 side-effect 推导规则落到 `standard`。
- 当 skill 处于 `inherit` 模式但当前 runtime 没有任何 mounted tool 时，skill 应继续可运行，但不会凭空获得额外工具。
- 当 `recommended_tools` 为空但 mounted universe 非空时，LLM 仍应拿到 mounted tool surface，不能回退成“无工具模式”。
- 当 policy profile 只允许 `minimal` 时，即使 trusted local baseline 请求 `standard`，系统也必须以 policy 为上限。

## Functional Requirements

- **FR-001**: 系统 MUST 定义正式的 trusted local tooling baseline，用于受信本地环境下的默认 `tool_profile` 提升；该 baseline 不得绕过 policy 对 `allowed_tool_profile` 的上限控制。
- **FR-002**: `general / research / dev` 默认运行时在 trusted local baseline 下 MUST 以 `standard` 作为默认 `tool_profile`；`ops` 至少不得继续被固定锁在 `minimal`。
- **FR-003**: 系统 MUST 为 MCP provider 增加 `mount_policy`，至少支持 `explicit`、`auto_readonly`、`auto_all` 三种模式。
- **FR-004**: 在 `mount_policy=auto_readonly` 下，MCP server 的 `minimal` 工具 MUST 默认进入 ambient tool universe；`standard/privileged` 工具 MUST 保持显式选择或更高授权。
- **FR-005**: 系统 MUST 为 Skill provider 增加 `permission_mode`，至少支持 `inherit` 与 `restrict`。
- **FR-006**: 当 `permission_mode=inherit` 时，Skill runtime MUST 继承当前请求的实际 mounted tool universe；当 `permission_mode=restrict` 时，`tools_allowed` MUST 继续作为收窄器生效。
- **FR-007**: `tools_allowed` MUST 从“默认必填白名单”降级为“可选收窄器”；新建 skill provider 的默认模式 MUST 为 `inherit`。
- **FR-008**: Tool selection contract MUST 从“只输出 `selected_tools`”升级为同时输出 `recommended_tools`、`mounted_tools`、`blocked_tools`；其中 `recommended_tools` 是推荐子集，不是完整工具面。
- **FR-009**: `selected_tools_json` 兼容字段 MUST 继续保留，但其语义 MUST 升级为 `recommended_tools` 的兼容镜像，不得再作为唯一真实工具宇宙。
- **FR-010**: LLM runtime MUST 在 prompt / metadata 中显式暴露 `mounted / blocked / recommended / tool_profile / resolution_mode`，以便模型理解实际工具边界。
- **FR-011**: ToolBroker / Policy / approval / audit 主链 MUST 不变；本 Feature 只能调整默认可见工具面与推荐逻辑，不得旁路危险动作治理。
- **FR-012**: control plane / Settings / capability catalog MUST 能展示 `mount_policy`、`permission_mode`、`recommended_tools` 与 `blocked reason` 的真实状态，而不是仅存在于后端 metadata。
- **FR-013**: 本 Feature MUST 提供后端、前端与集成测试，覆盖 trusted baseline、MCP auto mount、skill inherit/restrict、recommended tools 兼容升级。

## Key Entities

- **TrustedToolingBaseline**: 受信本地环境下的默认工具权限基线，决定运行时默认 `tool_profile` 的起点，但不突破 policy 上限。
- **McpMountPolicy**: MCP provider 的默认挂载策略，定义 server 暴露工具如何进入 ambient tool universe。
- **SkillPermissionMode**: Skill provider 的工具权限模式，定义是继承当前环境还是显式收窄。
- **RecommendedToolSet**: ToolIndex 产出的推荐工具子集，用于替代旧 `selected_tools` 的唯一真相语义。
- **MountedToolUniverse**: 当前请求实际可见、可执行的工具集合。
- **BlockedToolExplanation**: 因 profile、policy、mount policy、readiness 等原因未挂载工具的解释对象。

## Success Criteria

- **SC-001**: trusted local 环境下，`general / research / dev` 默认运行时的 `tool_profile` 会提升到 `standard`，且现有治理测试不回归。
- **SC-002**: 至少一个 `auto_readonly` MCP server 在不额外勾选 provider 的情况下，能让只读工具进入 ambient tool universe。
- **SC-003**: 至少一个 `permission_mode=inherit` 的 skill provider 能继承 ambient tool universe 运行；至少一个 `permission_mode=restrict` 的 skill provider 能继续正确收窄权限。
- **SC-004**: runtime metadata 与 prompt 同时暴露 `recommended_tools + mounted_tools + blocked_tools`，并保持 `selected_tools_json` 兼容不破坏历史链路。
- **SC-005**: 高风险或不可逆工具仍继续走 policy/approval gate，不会因为默认权限放宽而产生静默执行回归。

## Implementation Notes

- 已完成 trusted local baseline 收口：`general / research / dev / ops` 默认运行时的 `tool_profile` 从 `minimal` 提升到 `standard`，但实际执行仍受 ToolBroker / Policy 上限约束。
- 已完成 MCP `mount_policy`：provider/catalog/control-plane/runtime 均支持 `explicit | auto_readonly | auto_all`，其中 `auto_readonly` 默认仅自动挂载 `minimal` MCP 工具。
- 已完成 Skill `permission_mode`：自定义 skill provider 默认 `inherit`，`LiteLLMSkillClient` 与 `SkillRunner` 均按 runtime mounted tool universe 继承工具面；`restrict + tools_allowed` 继续作为收窄器保留。
- 已完成推荐工具合同升级：`recommended_tools` 已成为 `selected_tools_json` 的兼容镜像，runtime metadata 会同时暴露 `recommended_tools + mounted_tools + blocked_tools`。
- 已完成 control-plane 配置面升级：skill provider catalog 暴露 `permission_mode`，MCP provider catalog 暴露 `mount_policy`，默认保存值分别为 `inherit` 与 `auto_readonly`。
