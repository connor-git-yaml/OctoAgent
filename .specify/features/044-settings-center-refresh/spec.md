---
feature_id: "044"
title: "Settings Center Refresh"
milestone: "M4"
status: "Draft"
created: "2026-03-13"
updated: "2026-03-13"
research_mode: "story"
blueprint_ref: "docs/blueprint.md §2 Constitution；Feature 035（Guided User Workbench）；Feature 036（Guided Setup Governance）；Feature 043（Module Connection Trust-Boundary Hardening）"
predecessor: "Feature 035（Workbench IA）、Feature 036（Setup Governance）、Feature 043（Provider / control metadata 契约）"
---

# Feature Specification: Settings Center Refresh

**Feature Branch**: `codex/044-settings-center-refresh`  
**Created**: 2026-03-13  
**Updated**: 2026-03-13  
**Status**: Draft  
**Input**: 重新设计 `/settings`：修复布局混乱、说明过长和内部话术外露；Provider 改为多实例共存并可增删；模型别名只引用 provider + model；删除 Butler 相关模块与文案。

## Problem Statement

当前 `SettingsCenter` 已经能接 setup-governance 主链，但产品层仍存在四个明显问题：

1. **信息架构混乱**  
   Provider、模型别名、Memory、Skills、Butler 提示和保存动作同时堆在一页，两栏主次关系不清，用户很难判断“先配什么、后配什么”。

2. **平台内部话术直接暴露给用户**  
   页面出现“把平台连接留在这里，把 Butler 放回 Agents”等迁移期说明，属于设计/开发过程中的边界提示，不是用户任务语言。

3. **Provider UI 仍然围绕单主 Provider 组织**  
   现有数据结构已经允许多 Provider，但界面仍把“第一个启用的 Provider”当主编辑对象，导致“多 Provider 并存 + alias 引用 provider/model”的模型没有被正确表达。

4. **Butler 模块已迁出但仍然占据 Settings 页面**  
   hero、summary、Memory 提示、右侧 rail 和 review 引导中仍保留 Butler 信息，破坏 Settings 作为“平台连接与默认能力中心”的边界。

## Product Goal

把 `/settings` 重构成真正的平台设置中心：

- 页面只保留平台级配置：Provider、模型别名、渠道、Memory、安全与默认能力
- 以清晰分组和更少文字呈现核心设置
- Provider 支持多实例共存、增删、启停与默认顺序
- 模型别名明确绑定 `provider + model`
- 清理所有 Butler/Agents 相关模块、说明和跳转

## User Scenarios & Testing

### User Story 1 - 用户可以快速理解 Settings 页面该配什么 (Priority: P1)

作为首次进入控制台的用户，我希望 Settings 页按“模型接入、别名、渠道、Memory、安全”分组，让我一眼看懂每块负责什么。

**Why this priority**: 信息架构是所有后续配置行为的入口；如果入口仍混乱，后面的配置能力再完整也难以被正确使用。

**Independent Test**: 打开 `/settings`，验证页面不再出现迁移期调试话术，顶层结构可直接看出每个分区职责，且主要动作集中在页面主内容而不是分散在侧边 rail。

**Acceptance Scenarios**:

1. **Given** 用户进入 `/settings`，**When** 页面渲染完成，**Then** hero、summary 和 section 标题只呈现平台配置语义，不再出现 Butler 迁移说明。
2. **Given** 用户浏览 Settings 页面，**When** 查看主要区块，**Then** 能看到清晰的 `Providers / 模型别名 / 渠道 / Memory / 安全与能力 / 保存检查` 结构。

---

### User Story 2 - 用户可以维护多个 Provider 并让模型别名引用它们 (Priority: P1)

作为需要同时接入 OpenAI、OpenRouter、Anthropic 等模型源的用户，我希望在 Settings 里添加、删除、启用多个 Provider，并让每个 alias 指向具体 provider + model。

**Why this priority**: 多 Provider 共存是本次重构的核心业务目标，也是现有 UI 最明显的产品缺口。

**Independent Test**: 在 `/settings` 中新增多个 Provider、删除其中一个、再新增 alias，验证 alias 的 provider 选项来自当前 Provider 列表，且数据仍提交到既有 `providers / model_aliases` 配置字段。

**Acceptance Scenarios**:

1. **Given** 当前已有一个 Provider，**When** 用户新增第二个 Provider，**Then** 页面会保留两个 Provider 项并分别可编辑。
2. **Given** 页面存在多个 Provider，**When** 用户在 alias 行中选择 provider，**Then** provider 字段以可选列表呈现，而不是要求手输自由文本。
3. **Given** 某个 Provider 被删除，**When** 页面同步 alias，**Then** 原本引用该 Provider 的 alias 会切换到剩余可用 Provider 或留空，不保留失效引用。

---

### User Story 3 - 页面不再混入 Butler 模块与迁移期说明 (Priority: P2)

作为日常配置系统的用户，我希望 Settings 只关心平台设置，而不是继续看到 Butler 介绍、Agents 跳转卡片或“迁出说明”。

**Why this priority**: 这直接影响页面是否聚焦，也关系到产品边界是否清楚。

**Independent Test**: 打开 `/settings`，验证 Butler rail、Butler summary card、Memory 中的 Butler 提示和相关按钮全部消失。

**Acceptance Scenarios**:

1. **Given** 页面打开，**When** 用户查看 hero、summary、Memory、review panel，**Then** 不再看到 “Butler 已迁出”“去 Agents 调 Butler”等内容。
2. **Given** review 数据中仍包含 agent_autonomy_risks，**When** Settings 渲染，**Then** 页面不会渲染 Butler 专属模块或跳转引导。

## Edge Cases

- 当当前没有任何 Provider 时，alias 区域如何提示并允许用户先添加 Provider。
- 当默认 Provider 为 OAuth 类型且尚未完成授权时，页面仍要能展示状态并触发既有连接动作。
- 当只剩一个 Provider 且用户将其删除时，页面要保持可编辑，不进入异常状态。
- 当 review 存在阻塞项但与 Provider 无关时，页面仍要把风险显示在统一的保存检查区，而不是重新引入 Butler 模块。

## Functional Requirements

### Functional Requirements

- **FR-001**: `SettingsCenter` MUST 采用新的平台配置 IA，至少包含 `概览 / Providers / 模型别名 / 渠道 / Memory / 安全与能力 / 保存检查` 七个用户可见区块。
- **FR-002**: 页面 MUST 删除所有 Butler 专属模块、说明文案、summary 卡片和跳转入口。
- **FR-003**: Provider 区域 MUST 允许添加、删除、启用和编辑多个 Provider，并继续写入现有 `providers` JSON 字段。
- **FR-004**: 模型别名区域 MUST 让 alias 直接绑定 `provider + model`，且 provider 选择来源于当前 Provider 列表。
- **FR-005**: 页面 SHOULD 保留已有 quick connect / OAuth connect / review / apply 动作，但必须以新的信息架构和更简洁文案呈现。
- **FR-006**: 页面 MUST 减少显性解释文字，避免把调试、迁移、内部边界讨论直接暴露给用户。
- **FR-007**: Channels、Memory、Advanced 配置仍 MUST 复用现有 canonical hints，不新增平行后端接口。
- **FR-008**: review 风险和下一步 MUST 汇总到统一的“保存检查”区，不再依赖右侧 sticky rail。
- **FR-009**: 前端测试 MUST 更新为验证多 Provider 编辑、新 alias provider 选择和 Butler 清理后的新文案/结构。

### Key Entities

- **ProviderDraftItem**: Settings 中可编辑的 Provider 草稿项，包含 `id / name / auth_type / api_key_env / enabled`。
- **ModelAliasDraftItem**: 模型别名草稿，包含 `alias / provider / model / description / thinking_level`。
- **SettingsReviewSummary**: 由 `setup.review` 返回的风险、下一步和 readiness 汇总，用于保存检查区。

## Success Criteria

### Measurable Outcomes

- **SC-001**: `/settings` 首屏不再出现 Butler、Agents 迁移说明或内部调试式文案。
- **SC-002**: 用户可以在同一页上同时维护至少两个 Provider，并为 alias 选择其中任意一个 provider。
- **SC-003**: 主要保存动作和 review 信息集中在页面主内容区，移动端与桌面端都不依赖侧边 rail 才能完成配置。
- **SC-004**: 相关前端回归测试通过，覆盖新的 Provider/alias 交互和 Butler 清理结果。
