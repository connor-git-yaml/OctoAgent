---
feature_id: "050"
title: "Agent Management Simplification"
milestone: "M4"
status: "implemented"
created: "2026-03-14"
updated: "2026-03-14"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §M3/M4：普通用户可安装可配置、Project/Workspace 一等公民、用户友好的 Web 管理台、主 Agent 默认是 supervisor；Feature 039（supervisor-only 主 Agent）、Feature 046（Provider Centers）、Feature 048（guided surface clarity）"
predecessor: "Feature 039、046、048"
project_context_note: "已读取 .specify/project-context.*；本 Feature 完成了本地代码/参考项目调研，并记录了在线调研审计（0 个在线调研点，含 skip_reason）。"
---

# Feature Specification: Agent Management Simplification

**Feature Branch**: `codex/050-agent-management-simplification`  
**Created**: 2026-03-14  
**Updated**: 2026-03-14  
**Status**: Draft  
**Input**: 将 `Agents` 页面重构为普通用户可理解的“主 Agent + 已创建 Agent + 模板创建流”模型。主 Agent 作为当前项目唯一默认 Agent 直接编辑保存；内置 3 个 Agent 仅作为模板起点；已创建 Agent 以列表方式管理，支持编辑和删除；编辑页中的 LLM、默认工具组、固定工具、MCP/Skill 绑定等字段使用单选/多选等结构化控件，避免继续暴露大量开发术语和 debug 风格说明。

## Problem Statement

当前 `Agents` 页面已经具备 runtime、profile、provider binding 等底层能力，但在产品层存在五个结构性问题：

1. **对象层级混乱**
   - 内置模板、已保存模板、运行中的 Worker、Provider 绑定都挤在一个工作台里
   - 用户很难知道“我拥有的 Agent 是哪些”

2. **默认入口不符合普通用户心智**
   - 当前默认进入的是模板工作台，不是当前项目 Agent 列表
   - 这让 `Agents` 页面看起来像系统控制台，而不是 Agent 管理中心

3. **编辑体验过于技术化**
   - `runtime kinds`、`policy refs`、`tool profile`、`archetype`、每行一个 tool group 等字段直接暴露
   - 很多输入仍依赖 textarea，用户不知道该填什么

4. **模板与真实 Agent 混用导致重复对象膨胀**
   - 当用户只想加一点 MCP 或调整一点工具时，当前路径往往会产生一个新的模板副本
   - 最终会出现许多名称相似、描述相似、工具只差一点点的对象

5. **Project 归属和主 Agent 语义没有被产品化表达**
   - 现有数据模型其实支持 `project_id` 和 `default_agent_profile_id`
   - 但 UI 并没有把“每个项目有一个主 Agent”表达成清晰的日常事实

因此，050 的目标不是继续优化“模板工作台”的说明文案，而是：

> 重新定义 `Agents` 页面，使其围绕“当前项目有哪些 Agent、哪个是主 Agent、如何创建和编辑一个 Agent”展开，而不是围绕内部 `worker_profile` 语义展开。

## Product Goal

交付一个普通用户可理解的 Agent 管理中心：

- 当前项目只看到当前项目的 Agent
- 当前项目始终有一个清晰可见的主 Agent
- 内置 3 个 Agent 仅在创建流程中作为模板出现
- 已创建 Agent 以列表形式展示，可直接编辑和删除
- 编辑页优先使用结构化选择器，而不是让用户填写底层字段
- 高级技术字段降级到折叠区或高级入口

## Design Direction

### 050-A 对象心智

- **主 Agent**：当前项目唯一默认 Agent，长期存在，可直接编辑，不可删除
- **已创建 Agent**：当前项目下的其他 Agent，可编辑、可删除
- **内置模板**：仅在“新建 Agent”流程中作为起点出现，不视为用户已拥有的 Agent

### 050-B 页面结构

`Agents` 首页默认展示：

1. 当前项目主 Agent
2. 当前项目已创建 Agent 列表
3. `新建 Agent` 主按钮

不再默认展示模板说明面板、运行时轨道和大量编辑表单。

### 050-C 编辑方式

默认编辑页仅暴露用户真正需要的内容：

- 名称
- Persona / 用途说明
- 所属项目
- 使用的 LLM
- 默认工具组
- 固定工具
- 能力绑定（MCP / Skill）

`runtime kinds / policy refs / tags / metadata` 等内容进入高级设置。

## Scope Alignment

### In Scope

- `Agents` 页面信息架构重构
- 当前项目主 Agent / 已创建 Agent / 模板三层对象心智
- `新建 Agent` 模板选择流
- 以列表方式展示已创建 Agent
- 主 Agent `编辑可见 / 删除隐藏`
- 非主 Agent `编辑 / 删除`
- 结构化编辑控件（单选、多选、搜索选择器）
- 项目归属与当前项目列表过滤
- 现有默认绑定与聊天主链兼容

### Out of Scope

- 重做整个 runtime revision / diagnostic rail 系统
- 重写 `worker_profiles` 的底层存储模型
- 引入模板 marketplace 或复杂版本中心
- 在普通用户路径中暴露完整 `worker_profile` / control-plane 术语
- 全站导航或整个平台的视觉重做

## User Scenarios & Testing

### User Story 1 - 用户能一眼看懂当前项目有哪些 Agent (Priority: P1)

作为普通用户，我希望进入 `Agents` 页面后直接看到当前项目的主 Agent 和已创建 Agent 列表，而不是先理解模板、运行态和系统内部术语。

**Why this priority**: 这是当前页面最核心的可用性问题；如果对象层级不清楚，后续所有配置和绑定都会继续变复杂。

**Independent Test**: 进入当前项目的 `Agents` 页面，不阅读任何帮助文案，也能说清楚“哪个是主 Agent、还有哪些普通 Agent、我该点哪里去新建一个 Agent”。

**Acceptance Scenarios**:

1. **Given** 用户打开 `Agents` 页面，**When** 页面加载完成，**Then** 首屏显示当前项目主 Agent 和已创建 Agent 列表，而不是默认进入模板编辑工作台。
2. **Given** 当前项目存在 1 个主 Agent 和 2 个普通 Agent，**When** 用户浏览列表，**Then** 能清楚区分主 Agent 与普通 Agent，并看到每个 Agent 的用途摘要与基础配置摘要。
3. **Given** 当前项目还没有普通 Agent，**When** 用户进入页面，**Then** 页面给出清晰的空状态和“新建 Agent”入口，而不是显示一整页模板说明。

---

### User Story 2 - 用户可以从模板快速创建一个新的 Agent (Priority: P1)

作为普通用户，我希望内置的 3 个 Agent 只作为创建时的模板起点，而不是默认混在我的 Agent 列表里；我需要创建时再选择它们。

**Why this priority**: 这直接决定“模板”和“我拥有的 Agent”是否会被混淆。

**Independent Test**: 用户点击“新建 Agent”后，通过模板选择和最少必要字段输入，能创建一个新的 Agent，而不需要理解 clone/template/profile 这些技术语义。

**Acceptance Scenarios**:

1. **Given** 用户点击“新建 Agent”，**When** 创建流程打开，**Then** 内置 3 个 Agent 以模板卡片出现，且不与已创建 Agent 列表混在一起。
2. **Given** 用户选择一个内置模板并填写名称、用途和所属项目，**When** 完成创建，**Then** 系统生成一个新的已创建 Agent，并返回列表页展示。
3. **Given** 用户没有选择模板，**When** 选择空白起点创建，**Then** 系统仍允许创建一个新的 Agent，并提供默认的最小配置。

---

### User Story 3 - 用户可以通过清晰的编辑页修改 Agent (Priority: P1)

作为普通用户，我希望编辑 Agent 时主要使用结构化控件，而不是填写一堆不知道含义的原始字段。

**Why this priority**: 当前编辑体验的主要障碍不是功能不够，而是输入方式和字段语言太像内部控制台。

**Independent Test**: 用户在编辑页里可以不依赖帮助文档，仅凭字段标签和控件形式完成名称、Persona、模型、工具组、固定工具和能力绑定的修改。

**Acceptance Scenarios**:

1. **Given** 用户进入任一 Agent 的编辑页，**When** 页面渲染完成，**Then** 模型选择以单选或下拉呈现，默认工具组和固定工具以多选控件呈现，而不是纯 textarea。
2. **Given** 用户想给 Agent 增加 2 个固定工具，**When** 在编辑页选择并保存，**Then** 页面以用户语言反馈改动结果，而不是要求用户手填 tool name。
3. **Given** 编辑页存在高级设置，**When** 用户不展开高级设置，**Then** 不会看到 `runtime kinds / policy refs / tags` 等底层字段。

---

### User Story 4 - 主 Agent 与项目归属保持稳定，不破坏当前聊天绑定 (Priority: P2)

作为系统使用者，我希望每个项目都有一个清晰的主 Agent，同时又不希望这次页面重构破坏现有聊天和 work 的默认 Agent 绑定。

**Why this priority**: 如果主 Agent 与项目语义不稳定，产品心智会混乱，运行时默认路由也会变得不可解释。

**Independent Test**: 切换项目后，`Agents` 页面与聊天默认 Agent 一致；如果当前默认仍是内置模板，也能通过引导迁移到项目自有主 Agent。

**Acceptance Scenarios**:

1. **Given** 用户切换到另一个项目，**When** 打开 `Agents` 页面，**Then** 只看到该项目的主 Agent 与普通 Agent，而不会看到其他项目的 Agent。
2. **Given** 当前项目默认 Agent 仍指向内置模板，**When** 用户首次编辑主 Agent，**Then** 系统引导或自动完成“建立项目自有主 Agent”的迁移，并保持默认绑定正确。
3. **Given** 用户删除一个普通 Agent，**When** 该 Agent 不是当前项目主 Agent，**Then** 删除动作可执行且不会影响主 Agent 绑定。

## Edge Cases

- 当前项目的默认 Agent 仍是 builtin profile，导致“主 Agent 不可直接保存”时，系统必须提供迁移路径。
- 当前项目没有任何自定义 Agent 时，页面必须给出清晰空状态，而不是暴露模板控制台。
- 非主 Agent 正在被活跃聊天或 work 使用时，删除动作必须先提示风险或改为归档。
- 模板后续更新时，已创建 Agent 不得被静默覆盖。
- 工具或能力目录当前部分不可用时，编辑页必须给出不可用说明，而不是直接让保存失败。

## Functional Requirements

- **FR-001**: `Agents` 首页 MUST 默认展示“当前项目主 Agent + 当前项目已创建 Agent 列表”，而不是默认进入模板编辑工作台。
- **FR-002**: 系统 MUST 将普通用户路径中的对象明确分为 `主 Agent`、`已创建 Agent` 和 `内置模板` 三类，并在命名、空状态、动作按钮中保持一致。
- **FR-003**: 当前项目在日常 UI 中 MUST 始终有一个清晰可见的主 Agent；主 Agent MUST 可编辑且 MUST NOT 提供删除按钮。
- **FR-004**: 内置 3 个 Agent MUST 只作为创建时的模板起点出现在“新建 Agent”流程中，不得默认出现在已创建 Agent 列表。
- **FR-005**: 用户 MUST 能从模板或空白起点创建新的 Agent，并在创建完成后直接进入当前项目 Agent 列表。
- **FR-006**: 已创建 Agent 列表中的每个卡片 MUST 显示名称、用途摘要、所属项目、模型摘要、工具摘要以及可执行动作。
- **FR-007**: 非主 Agent MUST 支持编辑和删除；删除前 MUST 有明确确认或风险提示。
- **FR-008**: 编辑页 MUST 以结构化控件表达 `LLM`、`默认工具组`、`固定工具`、`所属项目` 和能力绑定；这些核心字段不得继续主要依赖自由文本输入。
- **FR-009**: `runtime kinds`、`policy refs`、`tags`、`metadata` 等底层字段 MUST 降级到高级设置或等价折叠区，且不得占据普通用户默认主路径。
- **FR-010**: 每个已创建 Agent MUST 明确归属到一个项目；当前项目的日常列表 MUST 只展示该项目拥有的 Agent。
- **FR-011**: 当前项目的主 Agent 语义 MUST 与现有 `default_agent_profile_id` 保持兼容，聊天和 work 默认绑定不得因 UI 重构而失效。
- **FR-012**: 如果当前项目默认 Agent 仍是 builtin template，系统 MUST 提供项目自有主 Agent 的建立/迁移路径，使后续“编辑主 Agent”语义成立。
- **FR-013**: 系统 SHOULD 继续复用现有 `worker_profiles`、project default binding 和 capability binding 数据链，而不是在未明确约定前新造平行 backend truth。
- **FR-014**: 普通用户主界面 MUST 避免直接暴露 `worker profile`、`archetype`、`tool profile`、`runtime kinds` 等内部实现术语。
- **FR-015**: 本 Feature MUST 提供覆盖主 Agent 展示、模板创建、结构化编辑、项目切换和删除边界的自动化测试矩阵。

## Key Entities

- **MainAgent**: 当前项目唯一默认 Agent；从已有 `WorkerProfile` 和 `Project.default_agent_profile_id` 派生，但在 UI 中作为独立产品对象表达。
- **ProjectAgent**: 当前项目下由用户创建和维护的 Agent，对外展示为普通列表项。
- **BuiltinAgentTemplate**: 系统内置的创建起点，仅在新建流程中出现，不作为用户已拥有对象展示。
- **AgentEditorDraft**: 编辑页的结构化草稿模型，承载名称、Persona、模型、工具组、固定工具、能力绑定和高级字段。
- **AgentCapabilityBinding**: Agent 允许使用的 MCP / Skill 能力集合，以用户语言表达。

## Success Criteria

- **SC-001**: 用户进入 `Agents` 页面后，无需阅读长说明文，也能在 30 秒内说清楚当前项目的主 Agent 和已创建 Agent 列表。
- **SC-002**: 用户可以在不输入原始 tool name / tool group 文本的前提下，完成一个新 Agent 的创建与保存。
- **SC-003**: 主 Agent 在列表中始终可见且无删除入口；普通 Agent 均具备明确的编辑和删除动作。
- **SC-004**: 切换项目后，`Agents` 页面显示的 Agent 列表与当前项目默认聊天 Agent 保持一致。
- **SC-005**: 默认编辑页不再直接暴露 `runtime kinds / policy refs / tags` 等底层字段；这些字段仅在高级入口可见。
