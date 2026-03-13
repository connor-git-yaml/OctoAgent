---
feature_id: "047"
title: "Frontend Workbench Architecture Renewal"
milestone: "M4"
status: "Draft"
created: "2026-03-13"
updated: "2026-03-13"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §2 Constitution；Feature 026（Control Plane Contract）、Feature 035（Guided User Workbench）、Feature 041/042（Agent Console 主链）、Feature 044（Settings Center Refresh）、Feature 046（Capability Provider Centers）；公开参考 OpenClaw / Agent Zero / OpenHands / Open WebUI / LibreChat"
predecessor: "Feature 035、042、044、046"
project_context_note: "[参考路径缺失] .specify/project-context.yaml 与 .specify/project-context.md 软链目标不存在，本 Feature 仅基于 blueprint、现有代码和本轮公开在线调研生成。"
---

# Feature Specification: Frontend Workbench Architecture Renewal

**Feature Branch**: `codex/047-frontend-workbench-architecture`  
**Created**: 2026-03-13  
**Updated**: 2026-03-13  
**Status**: Draft  
**Input**: 基于对 OpenClaw / Agent Zero / OpenHands / Open WebUI / LibreChat 的深度调研，从整体上重整 OctoAgent 前端架构与 UX/UI；以长期主义为目标，规划一个独立 Feature，统一工作台数据层、页面边界、设计系统和演进纪律。

## Problem Statement

OctoAgent 前端当前已经拥有可用的 Workbench 壳和 control-plane canonical 资源模型，但整体仍存在五个结构性问题：

1. **前端数据编排存在双轨**
   - `WorkbenchLayout + useWorkbenchSnapshot` 已经是新的工作台主链
   - `AdvancedControlPlane -> ControlPlane` 仍在维护自己的 snapshot/resource/action orchestration
   - 长期会造成行为、缓存、错误处理和交互分叉

2. **页面文件和样式文件开始单体化**
   - `AgentCenter`、`ControlPlane`、`SettingsCenter` 与 `index.css` 已经明显超过单一模块合理体量
   - 当前复杂度不是集中在“域模块”，而是集中在“大页面”

3. **日常工作台与深度诊断仍未彻底分层**
   - 用户主路径需要的是状态、下一步动作和能力边界
   - 而不少调试信息、内部术语、raw control-plane 思维仍会泄漏到第一层体验

4. **后端 canonical 契约很强，前端契约同步却偏弱**
   - 前端仍大量手写资源类型和 action payload
   - 随着 features 增长，后端 schema 与前端 type 漂移风险会持续升高

5. **视觉语言和 IA 还没有形成稳定的长期规范**
   - 现在像“若干功能页面的集合”，还不像一个 Personal AI OS 工作台
   - `Home / Chat / Work / Agents / Settings / Memory / Advanced` 的职责边界仍需进一步稳定

因此，047 的目标不是单独“优化某一页”，而是：

> 在不更换基础栈的前提下，建立 OctoAgent 前端的长期主义底座：统一数据层、域模块、设计系统、页面分层和演进约束。

## Product Goal

交付一个可持续演化的 Frontend Workbench 基线：

- 所有日常 surface 与 `Advanced` 共享同一套数据编排主链
- `Advanced` 回归深度诊断页，不再维护独立前端架构
- `Home / Chat / Work / Agents / Settings / Memory` 形成稳定的信息架构
- 巨型页面拆分为可维护的域模块、pattern 和 inspector
- 前端契约同步与测试基线提升到足以支撑后续 6-12 个月功能演进
- 视觉语言从“解释系统”升级为“可信工作台”

## Scope Alignment

### In Scope

- 统一 `snapshot / resources / actions` 的前端数据层
- 收口 `AdvancedControlPlane` 与主工作台之间的双轨逻辑
- `Agents / Settings / Memory / Home / Work` 的信息架构重整
- 巨型页面拆分与 design system 落地
- 前端类型契约同步机制
- 路由级 lazy loading、共享 query key / invalidation 规则
- 前端演进纪律：LOC / complexity / golden-path 测试基线

### Out of Scope

- 更换 React/Vite/Router 基础栈
- 对后端 control-plane canonical API 做大规模协议重写
- 全量视觉品牌升级或主题系统
- 新增大量业务能力页面作为本 Feature 核心目标
- 完整重写所有现有页面文案和插画

## User Scenarios & Testing

### User Story 1 - 用户能在稳定工作台中完成日常路径，而不会被调试信息打断 (Priority: P1)

作为普通使用者，我希望 `Home / Chat / Work / Agents / Settings / Memory` 这些主路径页面只展示当前状态、能力边界和下一步动作，而不是混入大量调试视图和内部术语。

**Why this priority**: 如果主路径仍然不稳定，再好的技术架构也无法转化为可用体验。

**Independent Test**: 在不进入 `Advanced` 的情况下，用户可以完成“确认当前 readiness、选择 Agent、配置 provider、发起聊天、查看 work、理解主要 warning”的主路径闭环。

**Acceptance Scenarios**:

1. **Given** 用户打开首页，**When** 页面加载完成，**Then** 首页优先展示 readiness、当前 project、下一步动作和关键 warning，而不是 raw debug 信息。
2. **Given** 用户进入 `Agents` 或 `Settings`，**When** 页面完成渲染，**Then** 页面先展示目录、详情和可执行动作，而不是大量内部说明文案。
3. **Given** 用户需要深入排障，**When** 用户进入 `Advanced`，**Then** 高级诊断信息在此可见，但不会反向污染日常页面结构。

---

### User Story 2 - 开发者能在统一数据层上维护所有工作台页面 (Priority: P1)

作为维护前端的开发者，我希望所有页面共享同一套 query/action/resource 编排规则，而不是在 `Advanced` 和主工作台里维护两套逻辑。

**Why this priority**: 双轨数据层是长期维护成本最高的风险点，必须优先消除。

**Independent Test**: 修改某个 canonical resource 的前端消费方式时，只需要在统一 query/action 层和对应域模块中改动，而不需要同步修两套独立 fetch/refresh 逻辑。

**Acceptance Scenarios**:

1. **Given** 任一页面需要刷新 control-plane resource，**When** 触发 action 成功，**Then** 失效与重取规则通过共享机制完成，而不是每页手工拼接。
2. **Given** 用户进入 `Advanced`，**When** 页面读取 snapshot 与资源，**Then** 它复用 shared workbench data layer，而不是自己维护独立控制台 state。
3. **Given** 某资源契约发生变更，**When** 前端更新消费代码，**Then** 变更集中在统一 contract/query 层，而不是散落在多个页面里。

---

### User Story 3 - 页面可以按域持续演化，而不会继续膨胀成巨型文件 (Priority: P1)

作为开发者，我希望 `Agents / Settings / Memory / Advanced` 等复杂页面拆成域模块、section 组件、inspector 组件与局部 hook，这样新需求可以持续叠加而不把页面文件继续做大。

**Why this priority**: 当前最大技术债就是单页单文件膨胀。如果不处理，后续 Feature 会越来越难交付。

**Independent Test**: 实现一个新的 Agent/Provider/Memory 细分交互时，可以落在对应 domain module 中，不需要继续向 1000+ 行页面主文件堆代码。

**Acceptance Scenarios**:

1. **Given** `AgentCenter` 被重构为域模块，**When** 新增一个 capability inspector section，**Then** 开发只需改相应模块与 pattern，不需要编辑巨型单文件主组件。
2. **Given** `SettingsCenter` 新增一个 provider 子功能，**When** 完成交付，**Then** 它落在 settings domain 内部，不影响 workbench shell 或 advanced diagnostics。
3. **Given** 样式扩展一个新 pattern，**When** 落地 CSS，**Then** 该改动进入 token / primitive / domain 分层，而不是继续塞进全局 `index.css`。

---

### User Story 4 - 团队可以用明确规则持续治理前端质量 (Priority: P2)

作为项目维护者，我希望前端有明确的 contract sync、测试、文件体量和可访问性基线，这样未来 Feature 能在同一套纪律下持续推进。

**Why this priority**: 没有演进纪律，重构只会在几轮 Feature 后再次失效。

**Independent Test**: 新增一个前端 Feature 时，CI 或本地检查能发现页面体量异常、契约漂移、关键黄金路径回归或 a11y 基线问题。

**Acceptance Scenarios**:

1. **Given** 前端契约来源更新，**When** 同步类型，**Then** 类型变更来自统一生成或集中注册机制，而不是继续手工散改。
2. **Given** 关键工作台路径发生回归，**When** 跑黄金路径测试，**Then** 能在合并前被及时发现。
3. **Given** 某页面或共享样式再次膨胀，**When** 运行质量脚本，**Then** 会触发可见告警或阻断。

## Edge Cases

- 当 `Advanced` 在迁移期间仍依赖某些老 helper 时，系统必须允许渐进迁移，而不是要求一次性全删。
- 当某个 control-plane resource 当前不可用或降级时，统一 query/action 层必须保留 degraded 状态，而不是回退为整页异常。
- 当页面需要同时处理 server-state、streaming state 和 local draft-state 时，系统必须明确状态归属，避免再把三者混入同一 hook。
- 当 `.specify/project-context.*` 软链失效时，本 Feature 的规划和后续实现仍需继续，但必须把该缺失记录为研究风险。
- 当旧页面在拆分后仍保留少量 legacy section 时，系统必须有明确迁移边界，不允许长期“双实现共存且没人负责收口”。

## Functional Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 保留单一 `Workbench` 壳层，并将 `Home / Chat / Work / Agents / Settings / Memory` 视为日常 surface，将 `Advanced` 视为独立诊断 surface。
- **FR-002**: 所有基于 control-plane snapshot/resources/actions 的页面 MUST 共享同一套前端数据编排主链，不得再维护两套独立 snapshot/action orchestration。
- **FR-003**: `AdvancedControlPlane` MUST 改为复用 shared data layer，而不是继续直接在页面内部管理独立 fetch/refresh/action 逻辑。
- **FR-004**: 前端 MUST 将 server-state、本地 draft-state 与 streaming/ephemeral runtime state 明确分层。
- **FR-005**: `Agents / Settings / Memory / Advanced` 等复杂页面 MUST 按域拆分为模块、section、inspector、局部 hook 与 view-model，而不是继续在单一页面文件中堆叠。
- **FR-006**: 前端 MUST 建立统一的 design token、primitive component/pattern 与域样式分层，降低 `index.css` 的全局堆叠。
- **FR-007**: 前端 MUST 建立后端 canonical contract 到前端类型的同步机制，减少资源类型、action payload 和关键文档结构的手写镜像。
- **FR-008**: 路由层 MUST 支持按页面域进行 lazy loading，避免复杂页面全部首屏 eager import。
- **FR-009**: 日常 surface MUST 优先展示 readiness、当前状态、能力边界与下一步动作，不得默认暴露原始 debug/治理术语。
- **FR-010**: `Advanced` surface MUST 保留 raw diagnostics、audit、lineage 与深度解释能力，但不再承担主工作台 IA 职责。
- **FR-011**: 前端 MUST 为工作台关键黄金路径提供自动化回归基线，至少覆盖首页 readiness、provider 设置、Agent 选择、聊天主路径和 work 查看。
- **FR-012**: 前端 MUST 引入明确的复杂度治理规则，限制页面文件、共享 hook 和共享样式文件的持续单体膨胀。

### Key Entities

- **WorkbenchSurface**: 前端一级 surface，分为 `daily` 与 `advanced` 两类，用于约束页面职责和导航层级。
- **ResourceQueryRegistry**: 统一管理 canonical resources 对应 query key、获取方式、失效策略的前端注册表。
- **WorkbenchActionMutation**: 面向 action 的统一提交与 invalidation 机制，负责承接 control-plane actions 与资源刷新。
- **DomainModule**: 按业务域组织的前端模块边界，如 `agents`、`settings`、`memory`、`advanced`。
- **DesignTokenSet**: 用于约束颜色、排版、间距、状态、表单和 inspector 视觉规则的前端设计 token。
- **ContractArtifact**: 从后端 canonical model 派生的前端类型或 schema 产物，作为 UI 层的事实契约。

## Success Criteria

### Measurable Outcomes

- **SC-001**: 用户可以在不进入 `Advanced` 的情况下完成 readiness 确认、provider 配置、Agent 选择、发起聊天和查看当前 work 的主路径闭环。
- **SC-002**: `Advanced` 页面不再维护独立的 snapshot/action orchestration，而是复用 shared workbench data layer。
- **SC-003**: `AgentCenter`、`SettingsCenter`、`ControlPlane` 不再以单体页面形式继续膨胀；改造完成后，主页面文件规模和共享 CSS 规模显著下降，并建立持续约束。
- **SC-004**: 前端关键契约同步不再主要依赖手工散改，关键 canonical resources/actions 的类型来源可集中追踪。
- **SC-005**: 前端关键黄金路径具备自动化回归基线，并能在新 Feature 合并前发现主路径回归。
