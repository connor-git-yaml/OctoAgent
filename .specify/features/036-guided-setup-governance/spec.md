---
feature_id: "036"
title: "Guided Setup Governance for Provider / Channel / Profile / Tools / Skills"
milestone: "M4"
status: "Planned"
created: "2026-03-10"
updated: "2026-03-10"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §8.10 M4；docs/m4-feature-split.md；Feature 015 / 025 / 026 / 030 / 035；OpenClaw wizard / pairing / auth profiles / skills status；Agent Zero settings / projects / skills import"
predecessor: "Feature 015（Onboard + Doctor）、Feature 025（Project / Workspace / Secret / Wizard）、Feature 026（Control Plane Contract）、Feature 030（Capability Pack / MCP / ToolIndex）、Feature 035（Guided User Workbench）"
parallel_dependency: "Feature 033 提供主 Agent profile/context continuity 事实源；036 不重做 context，而是治理 provider/channel/profile/policy/tools/skills 的初始化和默认边界。"
---

# Feature Specification: Guided Setup Governance for Provider / Channel / Profile / Tools / Skills

**Feature Branch**: `codex/feat-036-guided-setup-governance`  
**Created**: 2026-03-10  
**Updated**: 2026-03-10  
**Status**: Planned  
**Input**: 再次 review 当前项目在用户初始化配置 `Provider / Channel / Agent Profile / 权限 / Tools / Skills` 时的易用性与安全性；深读 OpenClaw 与 Agent Zero 的相关代码和产品模式，设计一个新的 Feature 036 来做优化，而且必须明确与既有系统接口的接线，避免“做了但是没有关联上”。  
**调研基础**: `research/product-research.md`、`research/tech-research.md`、`research/online-research.md`、`research/research-synthesis.md`

## Problem Statement

OctoAgent 当前已经具备 015/025/026/030/035 打下的大量正式能力，但围绕初始化配置和默认治理，仍然存在五个会直接伤害“小白可用性”和“安全可解释性”的结构性问题：

1. **初始化配置仍然是分裂入口，而不是连续流程**  
   用户在 `octo init`、`octo onboard`、`octo config *`、`octo secrets *`、Web `Settings`、control-plane `config/wizard/capability/agent_profiles` 之间来回切换。系统有能力，但用户感知到的是“步骤越来越碎”。

2. **安全字段存在于 schema，却没有进入主设置路径**  
   `front_door.mode`、`trusted_proxy_*`、Telegram `dm_policy/group_policy/allowlists` 已经存在于 `OctoAgentConfig`，但当前 wizard、onboarding 和图形化设置主路径并没有把这些边界做成人能理解的决策项。

3. **主 Agent / 权限 / Tool level 仍然大量依赖静默默认值**  
   当前 `AgentContextService` 会自动生成 system/project agent profile，默认 `tool_profile="standard"`、`model_alias="main"`。Policy profile 也仅在代码里有 `default/strict/permissive`，却没有正式设置入口。结果是默认值正在真实生效，但用户并不知道自己实际授权了什么。

4. **Tools / Skills / MCP 只有 runtime truth，没有 setup truth**  
   capability pack 和 MCP registry 已经能输出 availability / install hint / runtime kind，但 setup 阶段没有统一表达：
   - 当前主 Agent 默认允许哪些 tools
   - 哪些 skills 真的 ready
   - 缺哪些 secrets / binaries / servers
   - 当前 project 是否适合启用它们

5. **Web 与 CLI 缺少共享的 review/apply 语义**  
   当前 onboarding 更像“输出下一条命令是什么”，而不是“给你一份可复核、可 apply 的配置审查结果”。这会导致 035 的图形化设置和 CLI 很容易再次语义漂移。

因此，036 要解决的不是“增加更多设置字段”，而是：

> 在不绕过既有 wizard / config / control-plane / capability / project / secret 基线的前提下，把 `Provider / Channel / Agent Profile / 权限 / Tools / Skills` 的初始化配置与默认治理收口成一条真正用户可走通、也可审查风险的 canonical setup 主链。

## Product Goal

交付一个建立在既有 control-plane / wizard / onboarding 之上的 `Guided Setup Governance`：

- 让 Web `Settings / Setup Center` 与 CLI `octo init / octo onboard` 共享同一套 setup document、review 语义和 apply 语义
- 把 Provider、runtime、front-door、channel access、Agent Profile、policy preset、Tools / Skills / MCP readiness 统一到同一条配置路径
- 为普通用户提供 `谨慎 / 平衡 / 自主` 等人话化 preset，并映射到真实 policy / tool profile / approval 边界
- 在 apply 之前显式输出风险摘要、阻塞项、缺失 secrets、缺失依赖和暴露面说明
- 保持 secrets refs-only，不泄露实际凭证
- 把所有状态和动作继续挂在 canonical control-plane 体系下，避免做成新的平行 backend

## Scope Alignment

### In Scope

- 扩展首次使用与设置主链，覆盖：
  - Provider / model aliases / runtime
  - front-door 安全边界
  - Channel access / pairing / allowlists
  - 主 Agent profile
  - policy preset / tool profile / approval 强度
  - Tools / Skills / MCP readiness
- 新增 setup 级 canonical resources 与 actions：
  - `setup-governance`
  - `policy-profiles`
  - `skill-governance`
  - `setup.review`
  - `setup.apply`
  - `agent_profile.save`
  - `policy_profile.select`
  - `skills.selection.save`
- 扩展 `WizardSessionDocument` 或等价 durable setup session，使 CLI 与 Web 能共享 draft/review/apply
- 扩展 035 `SettingsCenter` / `Setup Center`
- 扩展 `octo init` / `octo onboard`
- 后端 risk review / readiness projection / secret redaction / regression tests

### Out of Scope

- 新建独立 `/api/setup/*` 私有接口
- 改写 `octoagent.yaml` 存储模型为第二套 schema
- 实现完整 OAuth broker / token exchange 新系统
- 实现 skills marketplace / 远程插件商店
- 引入多用户 RBAC 或复杂组织级权限模型
- 让 036 自己重做 033 的 context continuity 领域逻辑

## Architecture Boundary

### Canonical Backend Rules

036 必须遵守以下接线边界：

- **所有读取** MUST 继续挂在 `GET /api/control/resources/*` 或 `GET /api/control/snapshot`
- **所有变更** MUST 继续挂在 `POST /api/control/actions`
- **首次使用 CLI** MUST 作为 canonical setup 的适配层，而不是绕开 control-plane 单独维护逻辑
- **settings/setup UI** MUST 直接消费 canonical documents，不能创建前端私有真实状态
- **secret 实值** MUST 永远留在 `.env` / secret store，不得出现在 setup docs、action results、events、logs

### Existing Interface Mapping

#### Setup Overview

- 读取：
  - `GET /api/control/resources/wizard`
  - `GET /api/control/resources/config`
  - `GET /api/control/resources/project-selector`
  - `GET /api/control/resources/agent-profiles`
  - `GET /api/control/resources/owner-profile`
  - `GET /api/control/resources/capability-pack`
  - `GET /api/control/resources/diagnostics`
- 新增：
  - `GET /api/control/resources/setup-governance`
  - `GET /api/control/resources/policy-profiles`
  - `GET /api/control/resources/skill-governance`

#### Setup Review / Apply

- 保留：
  - `config.apply`
  - `wizard.refresh`
  - `wizard.restart`
  - `project.select`
  - `capability.refresh`
- 新增：
  - `setup.review`
  - `setup.apply`
  - `agent_profile.save`
  - `policy_profile.select`
  - `skills.selection.save`

#### CLI Bridge

- `octo init` MUST 改为 `setup-governance + setup.review + setup.apply` 的 CLI adapter
- `octo onboard` MUST 改为输出 canonical setup 状态和风险摘要，而不是只输出命令建议

## User Scenarios & Testing

### User Story 1 - 新用户一次就能完成 Provider / Channel / 安全边界设置 (Priority: P1)

作为第一次使用 OctoAgent 的用户，我希望在一个连续设置流程里完成 Provider、模型、Channel 和外部访问边界配置，并且系统用人话告诉我现在暴露给谁、还缺什么、下一步点哪里。

**Why this priority**: 这是首用闭环，没有它，后面的聊天和工作台都只会建立在不稳定或不安全的配置之上。

**Independent Test**: 从空配置项目进入 `Setup Center` 或 `octo init`，完成 provider/runtime/front-door/channel 设置并执行 review/apply；验证不需要手工猜命令，也能明确知道当前暴露面。

**Acceptance Scenarios**:

1. **Given** 项目没有 provider 和 channel 配置，**When** 用户进入 setup 并完成 provider/runtime/channel 基本设置，**Then** 系统给出完整 review summary，并能通过 `setup.apply` 持久化到 canonical config。
2. **Given** Telegram 开启为 webhook 且缺 webhook secret，**When** 用户执行 review，**Then** 系统明确给出阻塞项和推荐修复，而不是只报 schema 错误。

---

### User Story 2 - 我可以直接选择主 Agent 的风格和权限等级，而不是接受静默默认值 (Priority: P1)

作为普通用户，我希望能显式选择主 Agent 的风格、模型、权限和审批强度，例如“谨慎 / 平衡 / 自主”，并知道这会如何影响可用 tools 和确认行为。

**Why this priority**: 这直接关系到用户对“助手到底会不会自己动手”的信任感，也是安全可解释性的核心。

**Independent Test**: 在 setup 中切换不同 preset，执行 review，验证 effective policy/tool profile/approval 变化都可追溯到正式 document 和 action。

**Acceptance Scenarios**:

1. **Given** 当前 project 使用默认静默 profile，**When** 用户选择 `谨慎` preset，**Then** review 明确显示 tool level 降级和更多确认要求。
2. **Given** 用户选择 `自主` preset，**When** 执行 review，**Then** 系统以高风险项明确提示仅适用于受信任环境。

---

### User Story 3 - 我能在启用 Tools / Skills / MCP 之前知道哪些真的可用、缺什么、风险是什么 (Priority: P1)

作为普通用户，我希望在 setup 阶段就看到 skills、built-in tools 和 MCP servers 的 readiness，而不是运行时才发现“注册了但不能用”。

**Why this priority**: 这直接影响功能可发现性和首用成功率，也是“能力看起来很多但实际上接不上”的主要来源。

**Independent Test**: 构造缺 secret、缺 binary、MCP server disabled 三类场景，验证 setup 中能给出统一的 readiness / missing requirements / install hint 结果。

**Acceptance Scenarios**:

1. **Given** 某 skill 依赖 secret 或外部命令，**When** 打开 setup tools/skills 区域，**Then** 页面显示 missing requirements 和补救动作。
2. **Given** 某 MCP server 配置错误，**When** 执行 review，**Then** 系统把它列入 readiness 风险，而不是等运行时再失败。

---

### User Story 4 - Web 和 CLI 说的是同一套 setup 语言，而不是两套心智模型 (Priority: P2)

作为同时使用 Web 和 CLI 的用户，我希望无论从设置页还是命令行进入 setup，看到的步骤、风险总结和 apply 结果都一致。

**Why this priority**: 如果 Web 和 CLI 不一致，后续维护只会不断制造“做了但没接上”的新接缝。

**Independent Test**: 分别通过 `octo init` 和 Web `Setup Center` 完成同一组配置，验证底层使用相同 document / action，并产出一致结果。

**Acceptance Scenarios**:

1. **Given** 同一 project，**When** 用户分别从 CLI 和 Web 进入 setup，**Then** 两边看到的 section、warnings 和 next actions 保持一致。

---

### User Story 5 - 安全风险在新手路径中就可见，而不是藏在高级模式里 (Priority: P2)

作为不熟悉底层系统的用户，我希望在普通设置流程中就能看到 access risk、channel exposure、approval 强度和 secret 缺口，而不是要先学会 Advanced 才知道自己配置得不安全。

**Why this priority**: 小白用户最容易犯的是“先跑起来”，而不是“先设对边界”；如果风险只在高级模式出现，系统就不算 user-safe。

**Independent Test**: 在 `loopback / bearer / trusted_proxy`、`pairing / open / allowlist` 等不同组合下执行 review，验证 setup 总能输出易懂的风险说明。

**Acceptance Scenarios**:

1. **Given** `front_door.mode=trusted_proxy` 且 CIDR 为空，**When** 执行 review，**Then** 系统给出阻塞项和修复说明。
2. **Given** Telegram DM 设置为 `open` 且 allowlist 为空，**When** 执行 review，**Then** 系统至少给出 warning，并解释暴露范围。

## Edge Cases

- project/workspace 缺失或 fallback 时，setup 必须明确显示当前作用域与继承来源，不能静默落到默认 project。
- provider 已存在但 alias 不完整时，review 必须显示“部分配置有效”，而不是把整个 provider section 标记成成功。
- `capability pack` 或 `mcp registry` 不可用时，setup 仍必须能完成 provider/channel/profile 基础配置，但 tools/skills section 必须明确 degraded。
- skills readiness 未就绪时，系统不得假装 skill 已可用，也不得因为单个 skill 的问题阻塞基础聊天配置。
- setup review / apply 的 action result 与 event summary 中不得包含 secret 实值、token、cookie 或 OAuth redirect material。
- 035 Settings 已经打开时，036 新增 setup sections 必须直接接到现有 workbench 壳，不得另起一个脱离路由的页面体系。

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 提供 `setup-governance` canonical resource，用统一投影表达当前 project/workspace 下的 provider、channel、agent、policy、tools、skills setup 状态。
- **FR-002**: 系统 MUST 提供 `policy-profiles` canonical resource，把 `strict / default / permissive` 或等价 preset 暴露为用户可理解的选择项，并明确映射到真实 policy/tool/approval 行为。
- **FR-003**: 系统 MUST 提供 `skill-governance` canonical resource，以 readiness 语义暴露 built-in skills、workspace skills、MCP skills 的可用性、缺失依赖、install hint 和 trust level。
- **FR-004**: 系统 MUST 提供 `setup.review` action，对 draft config、agent profile、policy profile、skills selection 进行统一风险审查，并返回 blocking reasons、warnings 与 recommended actions。
- **FR-005**: 系统 MUST 提供 `setup.apply` action，以统一方式协调 `config.apply`、agent profile 保存、policy preset 应用和 skills 默认选择保存。
- **FR-006**: `octo init` MUST 作为 036 canonical setup 的 CLI adapter，而不是单独维护另一套 wizard 逻辑。
- **FR-007**: `octo onboard` MUST 从“输出命令建议”升级为“输出 canonical setup 状态、风险摘要和下一步动作”的 CLI adapter。
- **FR-008**: 系统 MUST 把 `front_door.mode`、Telegram `dm_policy/group_policy/allowlists` 等安全字段纳入 wizard/setup 主路径，并在 review 中明确解释当前暴露面。
- **FR-009**: 系统 MUST 提供 `agent_profile.save` action，使主 Agent 的 name、persona、model alias、tool profile 成为正式可治理对象，而不是只靠静默默认值。
- **FR-010**: 系统 MUST 提供 `policy_profile.select` 或等价 action，使用户选择的安全 preset 真正作用到运行时 policy，而不是只停留在 UI 标签。
- **FR-011**: 系统 MUST 提供 `skills.selection.save` 或等价 action，用于保存用户对 skills / MCP 默认启用范围的选择。
- **FR-012**: 所有 setup/readiness/review/apply documents 和 events MUST 保持 secrets refs-only 语义，不得包含明文凭证或可重放认证材料。
- **FR-013**: `GET /api/control/snapshot` SHOULD 纳入 `setup_governance`、`policy_profiles`、`skill_governance`，以便 035 首页和设置中心首屏消费。
- **FR-014**: 035 的 `Settings / Setup Center` MUST 直接消费 036 canonical resources/actions，不得另造私有 `settings/*` 或 `setup/*` API。
- **FR-015**: 当 `capability pack`、`mcp registry`、`secret audit` 或 `channel readiness` 任一子系统降级时，setup MUST 以 section 级 degraded 状态继续工作，而不是整页不可用。

### Key Entities

- **SetupGovernanceDocument**: 统一 setup 投影，聚合 provider/runtime、channel access、agent governance、tools/skills readiness 与 review summary。
- **PolicyProfileOption**: 用户可选的安全 preset，包含真实 policy/tool/approval 映射。
- **SkillGovernanceItem**: 面向 setup 的 skill/MCP readiness 项，含 scope、availability、missing requirements、install hint 和 trust level。
- **SetupReviewSummary**: 后端生成的风险审查摘要，包含 risk items、blocking reasons、warnings 和 next actions。
- **SetupDraftPayload**: review/apply 阶段使用的临时输入对象，承载 config、agent profile、policy preset 与 skills selection 的草案。

## Success Criteria

### Measurable Outcomes

- **SC-001**: 新用户可以在一条 setup 流程内完成 provider/channel/profile 基础配置，并在不查 CLI 帮助的前提下完成 apply。
- **SC-002**: Web `Settings/Setup` 与 `octo init`/`octo onboard` 对同一 project 产生一致的 warnings、blocking reasons 和 apply 结果。
- **SC-003**: 至少 90% 的常见安全边界项（front-door、Telegram 暴露面、policy/tool level、skills readiness）在 setup review 中有显式可读说明，而不是依赖默认值或高级模式。
- **SC-004**: setup review / apply / event regression tests 能证明 secrets 不会在 document、action result 或 event summary 中泄露。
- **SC-005**: 至少一条 e2e 能覆盖“从空项目进入 setup -> review -> apply -> doctor/readiness 通过 -> 进入 035 工作台”的完整主路径。
