---
feature_id: "046"
title: "Capability Provider Centers"
milestone: "M4"
status: "Draft"
created: "2026-03-13"
updated: "2026-03-13"
research_mode: "codebase-scan"
blueprint_ref: "docs/blueprint.md §2 Constitution；§5.1.4 Skills / Tools；§8.5.6 MCP 工具集成；§8.9.4 多 Provider 扩展；Feature 035/036/044"
predecessor: "Feature 030（Capability Pack + MCP runtime truth）、Feature 035（Workbench IA）、Feature 036（Guided Setup Governance）、Feature 044（Settings Center Refresh）、Feature 058（MCP Install Lifecycle — 已实现 npm/pip 安装向导、安装注册表、持久连接池、install/uninstall actions）"
---

# Feature Specification: Capability Provider Centers

**Feature Branch**: `codex/046-capability-provider-centers`  
**Created**: 2026-03-13  
**Updated**: 2026-03-13  
**Status**: Draft  
**Input**: 为 MCP 和 Skills 分别设计单独的配置页面，采用更简约的 Codex 风格；支持安装、删除和编辑；新增项都视作一种 capability provider；具体每个 Agent 能用哪些 provider，需要在 Agents 页面单独勾选。

## Problem Statement

当前 Workbench 已有 `skill_governance`、`capability_pack` 和 `setup_governance` 主链，但在产品层仍有四个结构性问题：

1. **Settings 仍把“能力目录”和“平台基础设置”混在一起**  
   Provider、模型别名、Memory、安全、Skills readiness 都挤在 `/settings`，用户无法把“模型 Provider”和“能力 Provider”区分开。

2. **MCP 与 Skills 还不是一等配置对象**  
   MCP 只有 runtime truth，没有单独的安装/编辑/删除页面；Skills 只有 capability pack 和治理条目，没有一个面向用户的独立配置中心。

3. **Agent 维度无法精细选择能力 Provider**  
   当前只有 project 级 `skill_selection`，不能表达“同一个项目里，不同 Agent / Worker 模板可见的 MCP / Skill Provider 不同”。

4. **现有能力治理更像 readiness 清单，而不是产品化的 catalog**  
   用户看到的是内部治理视图（availability / blocking / missing requirements），而不是“我安装了什么、还能再装什么、这个 Agent 允许什么”。

## Product Goal

把 MCP 与 Skills 都收敛为平台级的 capability provider catalog：

- `/settings` 只保留平台总览，并提供 `Skills` / `MCP` 两个独立配置入口
- `Skills` 页面负责安装、编辑、删除 skill providers，并区分系统内建与用户自定义
- `MCP` 页面负责安装、编辑、删除 MCP servers，并展示运行状态和工具数量
- `Agents` 页面负责按 Butler / Worker 模板勾选 capability providers，而不是在 Settings 里做全局唯一选择
- runtime 工具过滤要真正吃到 Agent/Worker 的 capability provider 选择，而不只是保存 UI 草稿

## User Scenarios & Testing

### User Story 1 - 用户可以在独立页面管理 Skills catalog (Priority: P1)

作为平台管理员，我希望 `Skills` 有独立页面，用简洁列表查看已安装条目，并能安装、编辑、删除自定义 Skill Provider。

**Why this priority**: Skills 是 capability catalog 的一半。如果仍然藏在 Settings 的风险清单里，用户无法理解哪些能力是系统内建、哪些是自己安装的。

**Independent Test**: 进入 `/settings/skills`，新增一个自定义 skill provider，编辑其名称或 prompt，再删除该条目，验证列表、control-plane 资源和 capability pack 同步更新。

**Acceptance Scenarios**:

1. **Given** 用户进入 `/settings/skills`，**When** 页面完成渲染，**Then** 页面会将系统内建 skills 与用户自定义 skills 分区展示，而不是与 Provider/Memory 混排。
2. **Given** 用户新增一个自定义 skill provider，**When** 保存成功，**Then** 该 skill 会出现在 installed 列表，并带有可编辑、可删除的状态。
3. **Given** 某个 skill provider 为系统内建项，**When** 用户查看操作区，**Then** 页面会明确展示其只读状态，而不是提供误导性的删除按钮。

---

### User Story 2 - 用户可以在独立页面管理 MCP catalog (Priority: P1)

作为要接多个外部工具服务器的用户，我希望 `MCP` 有独立页面来添加、编辑、启停和删除 server 配置，并能直接看到每个 server 当前是否健康、暴露了多少 tool。

**Why this priority**: MCP server 是外部能力接入的正式入口；没有独立页面时，MCP 仍然停留在工程视角，不是用户可操作的配置面。

**Independent Test**: 进入 `/settings/mcp`，新增一个 MCP server 配置、修改命令或 cwd、停用它、再删除它，验证 registry 状态和页面列表同步变化。

**Acceptance Scenarios**:

1. **Given** 用户进入 `/settings/mcp`，**When** 查看已安装列表，**Then** 每个 server 都会显示名称、命令、启用状态、runtime 状态和 tool 数量。
2. **Given** 用户新增或编辑 MCP server 后保存，**When** 刷新 catalog，**Then** 页面会显示最新配置并更新 registry 运行状态。
3. **Given** 某个 MCP server 被停用或配置错误，**When** 页面渲染，**Then** 用户仍能编辑或删除它，并看到降级/错误提示，而不是整页失效。

---

### User Story 3 - 用户可以按 Agent 勾选允许使用的 capability providers (Priority: P1)

作为维护 Butler 和 Worker 模板的用户，我希望在 `Agents` 页面为不同 Agent/模板单独勾选可用的 Skills/MCP providers，让能力范围跟着 Agent 走，而不是只在项目级统一开关。

**Why this priority**: “安装能力”与“谁可以使用能力”是两个不同问题；只有 catalog 没有 agent 侧授权，能力治理仍不完整。

**Independent Test**: 在 `Agents` 页面为 Butler 和某个 Worker 模板分别设置不同的 capability provider 勾选，保存后验证 review/apply 通过，且 runtime tool filtering 不再把未授权 provider 暴露给对应 Agent。

**Acceptance Scenarios**:

1. **Given** 用户打开 Butler 或 Worker 模板编辑区，**When** 查看能力设置，**Then** 能看到来自 catalog 的 capability providers 复选列表，按 `Skills / MCP` 分组呈现。
2. **Given** 用户取消某个 Agent 的 MCP provider，**When** 该 Agent 再解析工具宇宙，**Then** 该 provider 对应的 MCP tools 不会继续出现在其 pack 中。
3. **Given** 用户给某个 Worker 模板勾选了自定义 skill provider，**When** 模板发布并同步到 agent profile，**Then** 该选择会保留在 profile metadata 中，而不是丢失在前端草稿层。

## Edge Cases

- 当 `project-context` 软链缺失导致在线调研上下文不可读时，本 Feature 仍需基于本地 blueprint / codebase 执行，不得阻断实现。
- 当自定义 skill provider 的 `skill_id` 与内建 skill 或现有自定义 skill 重复时，保存必须失败并给出可读错误。
- 当 MCP 配置文件不存在、为空或 JSON 损坏时，MCP 页面必须仍可打开，并允许用户创建第一条可用配置。
- 当 agent 已引用某个 capability provider，而该 provider 后续被删除时，系统必须在 UI 和 runtime 中降级处理，不得导致整体 pack 崩溃。
- 当某个 capability provider 当前 availability 不是 `available` 时，页面仍应允许查看、编辑和分配，但要明确标记风险状态。

## Functional Requirements

### Functional Requirements

- **FR-001**: `/settings` MUST 提供 `Skills` 与 `MCP` 两个独立配置入口，并从原有平台设置流中拆出对应能力管理内容。
- **FR-002**: 系统 MUST 新增独立的 `Skills` 配置页，用于展示、安装、编辑、删除 capability skill providers。
- **FR-003**: 系统 MUST 新增独立的 `MCP` 配置页，用于展示、安装、编辑、启停、删除 MCP providers，并显示运行状态。[**部分已由 Feature 058 覆盖**: npm/pip 安装向导（McpInstallWizard）、安装来源标签、`mcp_provider.install / install_status / uninstall` control plane actions、安装注册表 `mcp-installs.json`、McpProviderItem 扩展（install_source / install_version / install_path / installed_at）均已实现。046 仅需补齐独立路由 `/settings/mcp`、更精细的运行状态展示与编辑/启停交互。]
- **FR-004**: `Skills` 页 MUST 区分系统内建条目与用户自定义条目；系统内建条目默认为只读，自定义条目可编辑和删除。
- **FR-005**: 自定义 skill providers MUST 持久化到 canonical backend 存储，并在 capability pack refresh 后成为真实可用 skill，而不是仅存在于前端展示层。
- **FR-006**: MCP provider 配置 MUST 继续复用现有 MCP registry 主链，不得新造平行的 tool 注册体系。[**已由 Feature 058 遵守**: McpInstallerService 安装完成后通过 `McpRegistryService.save_config() + refresh()` 注入配置，未新建平行注册体系。]
- **FR-007**: 所有新增的 skill / MCP 条目 MUST 以统一的 capability provider item 形式暴露给前端与 Agent 选择层，使用稳定 item ID（如 `skill:<id>`、`mcp:<server>`）。
- **FR-008**: `Agents` 页面 MUST 提供按 Butler / Worker 模板勾选 capability providers 的交互，并按 `Skills / MCP` 分组展示。
- **FR-009**: Butler `agent_profile` 与 Worker `worker_profile` MUST 将 capability provider selection 保存在 profile metadata 中，并在 review/apply/publish 后保留。
- **FR-010**: runtime capability filtering MUST 同时考虑 project 级治理与 agent/profile 级 provider selection，且 agent/profile 级选择拥有更高优先级。
- **FR-011**: Worker 模板发布后同步到 `AgentProfile` 时，系统 MUST 一并同步 capability provider selection 元数据。
- **FR-012**: 本 Feature MUST 复用现有 control-plane canonical actions/resources/snapshot refresh 机制，不得引入绕过 control plane 的前端直写逻辑。
- **FR-013**: 页面设计 MUST 采用更简约的 catalog/list 结构，减少长说明文案，避免暴露 readiness/debug 视角的内部话术。
- **FR-014**: 前后端测试 MUST 覆盖 skill provider catalog、MCP provider catalog、Agent provider selection 与 runtime filtering 的关键回归。

### Key Entities

- **SkillProviderConfig**: 用户可安装的 skill provider 配置，包含 `skill_id / label / description / prompt_template / model_alias / worker_type / tools_allowed / tool_profile / enabled`。
- **McpProviderConfig**: 用户可安装的 MCP provider 配置，包含 `name / command / args / env / cwd / enabled`。
- **CapabilityProviderItem**: 统一暴露给页面和治理层的 catalog 条目，标识一个 `skill` 或 `mcp` provider 的状态、来源、可编辑性与运行信息。
- **CapabilityProviderSelection**: 挂在 `AgentProfile.metadata` 或 `WorkerProfile.metadata` 下的 provider 允许/禁用集合，用于 agent 级能力过滤。

## Success Criteria

### Measurable Outcomes

- **SC-001**: 用户可以从 `/settings` 进入独立的 `/settings/skills` 与 `/settings/mcp` 页面，并在各自页面完成 catalog 管理，而无需再回到单页混合设置。
- **SC-002**: 用户可以新增、编辑、删除至少 1 个自定义 skill provider，并在 capability pack 中看到对应条目。
- **SC-003**: 用户可以新增、编辑、停用、删除至少 1 个 MCP provider，并在页面中看到 server 状态与 tool 数量变化。
- **SC-004**: 用户可以在 `Agents` 页面为至少两个不同 Agent/模板保存不同的 capability provider selection，且 runtime 不会把未授权 provider 的工具暴露给对应 Agent。
- **SC-005**: 相关前后端回归测试通过，覆盖 catalog CRUD、metadata 同步与能力过滤链路。
