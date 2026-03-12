---
feature_id: "041"
title: "Dynamic Root Agent Profiles + Profile Studio"
milestone: "M4"
status: "Draft"
created: "2026-03-12"
updated: "2026-03-12"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §2732-2777；Feature 026（Control Plane Contract）、Feature 030（Capability Pack / ToolIndex）、Feature 033（Agent Context Continuity）、Feature 035（Guided User Workbench）、Feature 036（Guided Setup Governance）、Feature 039（Supervisor Worker Governance）；Agent Zero profile layering / subordinate spawn；OpenClaw dashboard/control-plane UX"
predecessor: "Feature 026（canonical control plane）、Feature 030（capability pack / tool catalog）、Feature 033（profile/context continuity）、Feature 035（AgentCenter / Workbench 壳）、Feature 036（settings/setup 治理）、Feature 039（Butler + worker review/apply）"
parallel_dependency: "Feature 040 为 guided experience acceptance；041 必须在不破坏 035/036/039 主链的前提下，补齐 WorkerProfile 产品化与动态 Root Agent 生命周期。"
---

# Feature Specification: Dynamic Root Agent Profiles + Profile Studio

**Feature Branch**: `codex/feat-041-dynamic-root-agent-profiles`  
**Created**: 2026-03-12  
**Updated**: 2026-03-12  
**Status**: Draft  
**Input**: 不再把 worker identity 锁死在 `general / ops / research / dev` 枚举上；参考 Agent Zero 的 profile 分层与 subordinate 模式，让 Butler 能帮助用户创建、审查和调用自定义 Root Agent，同时保持 OctoAgent 原有 control-plane、policy、ToolBroker、event/audit 主链完整接线，并把前端 AgentCenter / ControlPlane 演进成真正的 profile 管理产品。第一阶段产品策略采用 `singleton Root Agent`：一个 Root Agent profile 对应一个可观察、可控制的单例运行槽位，先不拆出独立多实例模型。  
**调研基础**: `research/product-research.md`、`research/tech-research.md`、`research/research-synthesis.md`

## Problem Statement

OctoAgent 当前已经具备 026/030/033/035/036/039 打下的正式主链，但在“用户真的能长期拥有和治理自己的 Agent 资产”这件事上，仍然存在四个结构性缺口：

1. **worker identity 仍被固定枚举锁死**  
   当前 `WorkerType` 同时承担了“系统内建 archetype”和“用户世界里的 Agent 身份”两个职责。这样在 NAS、打印机、财经、软路由等真实场景到来时，系统要么不断扩枚举，要么迫使用户继续把所有需求塞回 `research/dev/ops`。

2. **039 解决了 worker 治理，但还没解决 profile 产品化**  
   039 已让 Butler 可以 `review/apply` worker 规划，并把主 Agent 收口成 supervisor；但 assignment、runtime truth、前端展示仍围绕 `worker_type`，没有正式的 `WorkerProfile / revision / snapshot` 对象链。

3. **前端仍把“模板 / 正式 profile / 运行实例”混在一起**  
   当前 `AgentCenter` 的模板来自 capability pack，实例来自 work 聚合，自定义内容主要还是前端本地 draft。用户无法清楚区分：
   - 系统内建模板
   - 我正式保存的 Root Agent
   - 当前正在运行的实例

4. **Butler 还不能像“Agent 管家”那样帮用户管理 Root Agent**  
   当前 Butler 更像固定 worker 的规划器，而不是能帮助用户创建、解释、发布和复用自定义 Root Agent 的长期管家。

因此，041 要解决的不是“放开一个自由创建 worker type 的 tool”，而是：

> 把 `WorkerProfile` 升级为正式产品对象，把 `WorkerType` 降级为少量内建 archetype，并让 Butler、Control Plane 与 AgentCenter 都围绕 `singleton Root Agent profile + 动态运行上下文` 工作，从而先在单例模式下形成可扩展、可治理的 Root Agent 体系。

## Product Goal

交付一条正式可运行的动态 Root Agent 主链：

- 用户可以在 Web 控制台中浏览系统 starter templates、创建或 fork Root Agent profile、发布 revision，并查看这个 Root Agent 的单例运行槽位
- Butler 可以基于用户目标发起“新建/更新 Root Agent”的提案，但所有增权和发布都必须经过 review/apply
- `Work`、child dispatch、runtime truth 与前端 runtime lens 能明确展示 `requested profile / applied profile / profile revision / effective snapshot / actual tools`
- AgentCenter 与 ControlPlane 会把每个 Root Agent 的静态配置和动态上下文并排展示，让用户能同时看见“它被设计成什么样”和“它现在正在做什么”
- 现有四个内建 worker 不再是唯一身份，只作为少量 base archetype 提供默认 runtime kind、tool baseline 与 bootstrap
- 整个流程继续挂在 canonical control-plane resources/actions、ToolBroker、policy、event/audit、delegation graph 上，不创建平行 backend

## Scope Alignment

### In Scope

- `WorkerProfile` 正式领域模型、revision、snapshot 与 store/projection
- `singleton Root Agent` 第一阶段产品模式：一个 profile 对应一个单例运行槽位，UI 直接展示 profile 静态配置与当前动态上下文
- `WorkerType` 向 `WorkerArchetype` 语义收缩，作为内建 starter template 基线
- 扩展 canonical control-plane resources/actions，覆盖：
  - profile registry
  - profile inspect / diff / review / publish / archive
  - 从 profile 启动实例
  - 从运行实例提炼为 profile draft
- 扩展 039 worker review/apply，使其能处理 `profile_id / profile revision / effective snapshot`
- 扩展 `DelegationPlane` / work projection / runtime truth，增加 profile lineage
- 重构 `AgentCenter` 为 `Butler / Profile Library / Runtime Workers`
- 新增 `Profile Studio` 信息架构与交互主线
- 在 `ControlPlane` 中增加 profile lens，解释每个 work 的 profile 继承来源和实际能力
- legacy built-in worker profile 的兼容迁移
- 后端、前端、integration、regression tests

### Out of Scope

- 第一阶段的多实例 Root Agent runtime registry
- 引入任意数量的系统级新 worker enum
- 绕过 ToolBroker / policy / secret store，让模型自由创建不存在的工具或权限
- 新建平行 `/api/agent-builder/*` 私有 backend
- 删除 Butler 作为主 Agent / supervisor 的角色
- 一次性重写所有旧 work 记录
- 多租户 RBAC、团队协作空间、远程 profile marketplace

## Architecture Boundary

### Canonical Backend Rules

041 必须遵守以下接线边界：

- **所有读取** MUST 继续挂在 `GET /api/control/snapshot` 与 `GET /api/control/resources/*`
- **所有变更** MUST 继续挂在 `POST /api/control/actions`
- **Root Agent profile 发布** MUST 走 review/apply 语义，不得由前端或模型直接落库
- **运行时实例派生** MUST 继续通过 `Work / DelegationPlane / TaskRunner / Orchestrator` 主链完成
- **第一阶段产品形态** MUST 采用 `singleton Root Agent` 语义：前端以 profile 为主对象展示，并同时暴露该 profile 当前动态上下文，而不是要求用户先理解独立实例层
- **工具授权** MUST 继续来源于 ToolBroker / capability pack / MCP registry / policy，不得通过 profile prompt 文本自由扩权
- **secret 实值** MUST 永远留在 secret store / runtime injection，不得进入 profile document、action payload、event payload 或前端缓存

### Existing Interface Mapping

#### Profile Registry

- 读取：
  - `GET /api/control/resources/agent-profiles`
  - `GET /api/control/resources/capability-pack`
  - 新增 `GET /api/control/resources/worker-profiles`
  - 新增 `GET /api/control/resources/worker-profile-revisions/{profile_id}`
- 动作：
  - 新增 `worker_profile.create`
  - 新增 `worker_profile.update`
  - 新增 `worker_profile.clone`
  - 新增 `worker_profile.archive`
  - 新增 `worker_profile.publish`

#### Profile Review / Butler-assisted Creation

- 读取：
  - 现有 `delegation`
  - 现有 `sessions`
  - 新增 `worker-profile-review`
- 动作：
  - 扩展 `workers.review`
  - 扩展 `worker.apply`
  - 新增 `worker_profile.review`
  - 新增 `worker_profile.apply`

#### Runtime Binding

- 读取：
  - `GET /api/control/resources/delegation`
  - `GET /api/tasks/{task_id}`
  - `GET /api/tasks/{task_id}/execution`
- 约束：
  - work projection MUST 暴露 `requested_worker_profile_id`
  - work projection MUST 暴露 `requested_worker_profile_version`
  - work projection MUST 暴露 `effective_worker_snapshot_id`
  - legacy `selected_worker_type` 仅作为兼容字段保留

#### AgentCenter / Profile Studio

- 读取：
  - `snapshot.resources.worker_profiles`
  - `snapshot.resources.delegation`
  - `snapshot.resources.capability_pack`
  - `snapshot.resources.project_selector`
- 动作：
  - `worker_profile.*`
  - `worker.spawn_from_profile`
  - `worker.extract_profile_from_runtime`

## User Scenarios & Testing

### User Story 1 - 我可以把一个自定义 Root Agent 保存成长期可复用的 Profile (Priority: P1)

作为用户，我希望把“NAS 管理 Agent”“财经追踪 Agent”“打印机 Agent”这类角色保存为正式 profile，而不是每次都重新解释或继续依赖固定 worker type。

**Why this priority**: 这是 041 的核心价值。如果没有正式可复用的 profile，对用户来说系统仍然只是固定 worker 的组合。

**Independent Test**: 在同一个 project 中，从 starter template fork 一个 Root Agent profile，补充名称、说明、能力边界并发布；验证它能出现在 `Profile Library` 中，并可被后续 session/work 选择。

**Acceptance Scenarios**:

1. **Given** 系统存在内建 starter templates，**When** 用户从 `research` archetype fork 一个“财经追踪 Agent”，**Then** 系统创建一个新的 `WorkerProfile` draft，并允许用户 review/publish。
2. **Given** 用户已发布一个 project-scope Root Agent profile，**When** 打开 `Profile Library`，**Then** 页面能显示 profile 名称、作用域、版本、基础 archetype 和当前可用状态。

---

### User Story 2 - Butler 可以帮我提出新 Agent 方案，但不会绕过治理直接放权 (Priority: P1)

作为用户，我希望直接告诉 Butler “帮我建一个 NAS 管理 Agent”，Butler 能帮我列出它需要的工具、运行方式和风险边界；但真正发布前，系统仍然要求 review/apply。

**Why this priority**: 这决定 Butler 是否真正成为“Agent 管家”，同时也决定动态 profile 是否仍然可控。

**Independent Test**: 在聊天或控制面发起 Butler-assisted profile creation，验证系统生成 profile proposal、列出能力边界和风险摘要，并要求用户显式 apply 后才产生正式 revision。

**Acceptance Scenarios**:

1. **Given** 用户向 Butler 提出“新建一个 NAS 管理 Agent”，**When** Butler 生成 profile review，**Then** 系统明确列出建议的工具组、runtime kind、审批边界和 warnings。
2. **Given** review 中包含更高权限的工具组或 connector，**When** 用户尚未执行 apply，**Then** 系统不得创建正式 profile revision 或运行实例。

---

### User Story 3 - 我能清楚区分模板、正式 Profile 和它当前的单例运行上下文 (Priority: P1)

作为用户，我希望在界面中看到三类对象的清晰分层：系统 starter templates、我保存的 Root Agent profiles、以及每个 Root Agent 当前的单例运行上下文；这样我才不会把“静态配置”和“真实运行时”混淆。

**Why this priority**: 这是动态 Agent 进入日常使用后避免信息混乱的关键 UX。

**Independent Test**: 打开 `AgentCenter`，验证页面可分别展示模板库、profile 库和单例运行上下文，且静态配置和动态状态可以同时查看。

**Acceptance Scenarios**:

1. **Given** 用户尚未创建任何自定义 profile，**When** 打开 `AgentCenter`，**Then** 页面显示内建 starter templates，但 profile 库为空，单例运行上下文区域仅显示系统 archetype 的当前状态。
2. **Given** 用户已创建 profile 且有运行 work，**When** 打开 `AgentCenter`，**Then** 页面能同时展示 profile 元信息和它当前的动态上下文，而不是把两者混成一个模糊列表。

---

### User Story 4 - 我可以从运行中的实例反向提炼出一个正式 Profile (Priority: P2)

作为用户，我希望当某个运行实例调教得不错时，可以把它提炼成正式 Root Agent profile，后续直接复用，而不是重新配置一遍。

**Why this priority**: 这能把即时实践沉淀成长期资产，是动态 profile 真正有复利价值的关键。

**Independent Test**: 从一个已有 runtime worker 发起“提炼为 profile”，验证系统生成 draft，并要求用户 review/publish 后才进入 profile 库。

**Acceptance Scenarios**:

1. **Given** 某个 runtime worker 已具有稳定的工具选择和行为边界，**When** 用户选择“提炼为 profile”，**Then** 系统创建一个可编辑的 profile draft，并保留来源 runtime 引用。

---

### User Story 5 - 我能在运行态里看到某个 Work 实际使用了哪个 Profile 和哪版权限 (Priority: P2)

作为用户，我希望在 `ControlPlane` 或 Work 详情里看到某次执行实际用了哪个 profile、哪一版、哪些工具和 override，这样我才能审计它为什么能做这些事。

**Why this priority**: 如果动态 profile 进入运行链却没有 runtime lens，系统会比现在更难解释。

**Independent Test**: 触发一个由自定义 Root Agent 派生的 work，验证 `delegation` 和 task/execution 详情能展示 profile lineage 与 effective snapshot。

**Acceptance Scenarios**:

1. **Given** work 由某个 profile revision 启动，**When** 用户查看 runtime details，**Then** 页面显示 `requested profile / applied revision / effective snapshot / actual tools`。
2. **Given** 同名 profile 在后续被更新，**When** 用户回看旧 work，**Then** 系统仍能看到当时生效的 snapshot，而不是被新版本覆盖。

## Edge Cases

- 当 legacy work 只有 `selected_worker_type`、没有 `profile_id` 时，系统必须以兼容字段显示，并明确标注为 legacy runtime。
- 当用户尝试创建 profile，但所选工具依赖未就绪的 MCP server 或缺失 secret 时，review 必须给出 readiness 警告或阻塞，而不是发布后运行失败。
- 当 profile 被 archive，但历史 work 仍引用旧 revision 时，系统必须继续保留其 inspect 能力，不得破坏历史审计。
- 当同一 project 已有多个相似 profile 时，Butler 发起的新提案必须优先建议 clone/fork 现有 profile，而不是无穷增生。
- 当 profile upgrade 提高了工具权限或 runtime kind，apply 必须以 diff 方式明确展示风险升级。

## Functional Requirements

- **FR-001**: 系统 MUST 提供正式的 `WorkerProfile` 产品对象，并支持至少 `system` 与 `project` 两种作用域。
- **FR-002**: 系统 MUST 将现有 `WorkerType` 语义收缩为内建 `WorkerArchetype` / starter template，而不是继续作为用户唯一可选的 Agent 身份。
- **FR-003**: `WorkerProfile` MUST 支持版本化 revision，并在第一阶段以 `singleton Root Agent` 模式暴露静态配置与动态运行上下文。
- **FR-004**: control plane MUST 提供 canonical `worker-profiles` 资源与 `worker_profile.create/update/clone/archive/publish` actions，不得通过前端本地 state 伪造正式 profile。
- **FR-005**: Butler MUST 能发起 `worker_profile.review/apply` 提案，帮助用户创建或更新 Root Agent，但在用户显式 apply 前不得发布正式 revision。
- **FR-006**: profile review MUST 明确展示 `base archetype / runtime kind / tool profile / allowed tool groups / selected tools / policy boundary / warnings`。
- **FR-007**: `Work`、child dispatch 与 runtime truth MUST 落盘 `requested_worker_profile_id`、`requested_worker_profile_version` 与 `effective_worker_snapshot_id`，并将 `selected_worker_type` 退化为兼容字段。
- **FR-008**: `AgentCenter` MUST 将 `starter templates / Profile Library / singleton runtime context` 三层对象分开展示，并为不同对象提供不同动作。
- **FR-009**: 系统 MUST 提供 `Profile Studio` 交互，至少覆盖基础身份、能力边界、review/publish 三个阶段。
- **FR-010**: 系统 MUST 支持从 starter template fork profile、从现有 profile clone profile、以及从 runtime worker 提炼 profile draft。
- **FR-011**: `ControlPlane` / Work runtime lens MUST 展示 `requested profile / applied revision / effective snapshot / actual tools`，并提供 Root Agent 静态配置与动态上下文的并排视图，使动态 profile 可解释。
- **FR-012**: profile 中声明的工具和 connector MUST 继续经过 ToolBroker、capability pack、MCP registry 与 policy 校验，不得因 profile 自定义而绕过治理。
- **FR-013**: 041 MUST 提供 legacy migration，使当前四个内建 worker 以 starter template 形式继续可用，并保证旧 work 记录仍可浏览。
- **FR-014**: 041 MUST 提供后端、前端、integration 与 regression tests，覆盖 registry、review/apply、runtime lineage 与 UI 信息架构主链。

## Key Entities

- **WorkerArchetype**: 系统内建的少量基础 archetype，承载默认 runtime kind、默认 bootstrap 与默认 tool baseline。
- **WorkerProfile**: 用户或系统可复用的 Root Agent 正式对象，包含身份、能力边界、策略和作用域。
- **WorkerProfileRevision**: `WorkerProfile` 的已发布版本，用于 diff、审计和回滚。
- **EffectiveWorkerSnapshot**: 某次运行实际生效的 profile 快照，供 work/runtime 追溯。
- **WorkerSingletonContext**: 控制面中的单例运行上下文投影，展示当前 Root Agent 的运行状态、来源 profile、实际工具与负载。
- **WorkerProfileProposal**: Butler 或用户发起的 profile 变更提案，包含 diff、warnings、审批需求与发布意图。

## Clarifications

### Session 2026-03-12

- [AUTO-CLARIFIED: Butler 继续保持系统级主 Agent，不开放为普通可删除 profile — 原因：当前 039 已把 Butler 定义为 supervisor，041 的目标是补齐 Root Agent 资产层，而不是削弱主控角色。]
- [AUTO-CLARIFIED: 041 第一阶段只支持 `system / project` scope 的正式 WorkerProfile，workspace/session/work 的差异通过 effective snapshot 与 override 承接 — 原因：这与 blueprint 的 profile/snapshot 继承链一致，也能降低第一轮对象复杂度。]
- [AUTO-CLARIFIED: profile 可自定义，但其工具来源必须受 ToolBroker/MCP/capability pack/policy 限制，不提供“自由创造 tool”能力 — 原因：这符合 Constitution 中的 `Tools are Contracts` 与 `Least Privilege by Default`。]
- [AUTO-CLARIFIED: `selected_worker_type` 在 041 中进入兼容期而不是立即删除 — 原因：当前 delegation/runtime/frontend/tests 都依赖该字段，直接移除会造成不必要的大面积回归。]
- [AUTO-CLARIFIED: `Profile Studio` 第一轮以桌面三栏和移动端分步抽屉为主，不追求复杂图编辑器 — 原因：当前最关键的是对象边界和 review/apply 可用性，而不是可视化编排器。]
- [AUTO-CLARIFIED: 041 第一阶段按 `singleton Root Agent` 推进，UI 直接把 profile 的静态配置和动态上下文并排展示；多实例 runtime registry 留到后续阶段 — 原因：这样能先快速建立稳定产品形态，同时避免一次性重写 runtime 身份层。]

## Success Criteria

- **SC-001**: 用户可以在 Web 中正式创建、clone、publish 一个 Root Agent profile，并在后续 work/session 中再次选择它。
- **SC-002**: Butler 发起的新 Root Agent 提案在 apply 前不会产生正式 profile revision，apply 后会留下完整 diff、事件与 lineage。
- **SC-003**: `AgentCenter` 能清晰区分 starter templates、Profile Library 和 singleton runtime context，且主要操作不再依赖前端本地草稿作为事实源。
- **SC-004**: `ControlPlane` 与 runtime details 能展示 profile lineage 与 effective snapshot，用户无需从旧 metadata 反推权限来源。
- **SC-005**: 现有四个内建 worker 迁移为 starter templates 后，legacy work 浏览不回归，且 035/036/039 既有主链继续可用。
- **SC-006**: 041 的新增测试覆盖 registry、review/apply、runtime binding 和 AgentCenter/ControlPlane 关键交互，并全部通过。

## Residual Risks

- `WorkerType` 与 `WorkerProfile` 共存期内，前后端会存在一段“双字段”复杂度，若迁移策略不清晰，容易出现展示不一致。
- 如果 Butler-assisted proposal 过于激进，profile 数量可能快速膨胀；需要在后续任务中补“重复 profile 建议复用”策略。
- 如果 `Profile Studio` 的 diff/review 表达不够清楚，动态 profile 反而会增加用户认知负担；前端需优先保证解释性而不是字段数量。
