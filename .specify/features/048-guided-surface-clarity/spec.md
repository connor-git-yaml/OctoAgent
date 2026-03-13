---
feature_id: "048"
title: "Guided Surface Clarity Refresh"
milestone: "M4"
status: "Draft"
created: "2026-03-14"
updated: "2026-03-14"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §2 Constitution；docs/blueprint.md M3 产品化约束（安装/配置/首聊/管理台必须是一条连续路径）；Feature 035（Guided User Workbench）；Feature 036（Guided Setup Governance）；Feature 041（Butler-owned freshness runtime）；Feature 044（Settings Center Refresh）；Feature 047（Frontend Workbench Architecture Renewal）；参考 OpenClaw wizard/dashboard/control-ui"
predecessor: "Feature 035、036、041、044、047"
parallel_dependency: "Feature 049 负责 Butler 行为与补问策略；048 只负责普通用户主路径的页面语言、信息架构与等待反馈。"
---

# Feature Specification: Guided Surface Clarity Refresh

**Feature Branch**: `codex/048-guided-surface-clarity`  
**Created**: 2026-03-14  
**Updated**: 2026-03-14  
**Status**: Draft  
**Input**: 基于真实用户体验复盘，重做 `Home / Settings / Chat` 的普通用户主路径表达。重点修复：首页不知道下一步要做什么、卡片统计口径含糊、设置向导像配置中心而不是可执行引导、实时问题等待过程没有反馈、内部工具/摘要泄漏到普通页面。

## Problem Statement

当前 guided workbench 已经有完整资源和运行时数据，但普通用户主路径仍存在五个高频痛点：

1. **首页没有先回答“现在发生了什么、我该做什么”**  
   `Home` 首屏标题和摘要仍直接来自 readiness 状态拼接，用户看到的是 `再检查一次设置`、`action_required`、`degraded` 一类内部状态，而不是单一明确动作。

2. **卡片统计口径和文案不符合用户心智**  
   `待你确认 1`、`当前工作 19 / 进行中 1`、`记忆摘要 0` 这类信息要么不知道指什么，要么混合了历史累计和当前状态，导致用户无法判断影响与优先级。

3. **Settings 首屏仍然是“配置中心”而不是“首次可用向导”**  
   虽然 044 已经收口了配置结构，但首屏仍偏向 Provider/alias/Memory/security 的结构介绍，缺少“最少先配什么、配完后回哪里验证”的强引导。

4. **聊天等待态几乎不可见**  
   当问题需要 Butler -> Worker 的内部协作时，用户只看到“发送中”或长时间空白等待。系统已经具备 A2A/runtime truth，但这些信息没有转成用户可理解的进度反馈。

5. **普通页面仍泄漏内部技术摘要**  
   首页会直接显示 raw channel summary、`telegram: [object Object]`、最近上下文摘要里的 tool/search 痕迹；聊天侧栏也偏向“技术详情”而不是“协作进展”。

因此，048 的目标不是重做前端架构，也不是增加更多控制台字段，而是：

> 把普通用户第一眼看到的三个页面，改成真正回答“当前状态、影响、下一步、等待中的进展”。

## Product Goal

交付一条更自然的普通用户主路径：

- `Home` 先给出单一结论和单一主行动，而不是状态拼盘
- `Settings` 先强调“最少需要配置什么”，再谈完整配置面
- `Chat` 在等待 Butler / Worker 处理时给出可理解、可折叠的协作进度
- 普通页面不再泄漏 raw runtime summary、tool traces、object stringification 等内部实现细节
- `Advanced` 继续承担深度诊断和内部事实查看角色

## Scope Alignment

### In Scope

- `Home` 首页 hero、summary cards、下一步引导、最近摘要与状态文案重构
- `Settings` 首屏的“最少必要配置”导向、首聊闭环导向和 CTA 重排
- `Chat` 的等待态、协作中态、A2A 进度折叠面板和失败解释
- 统计口径改写：区分“当前待处理”“当前进行中”“最近完成”“历史累计”
- 普通 surface 对 raw runtime summary、raw diagnostics、raw object string 的去泄漏化
- 将现有 runtime/A2A truth 转换为普通用户可读的阶段状态

### Out of Scope

- 重写前端 shared data layer、query registry 或整体模块架构（由 047 负责）
- 新增 backend canonical resources 或平行设置接口
- 修改 Butler / Worker 核心 runtime 语义（由 039/041 负责）
- 在 `Advanced` 中删除深度诊断信息
- 大规模视觉品牌重设或插画系统

## User Scenarios & Testing

### User Story 1 - 首页 5 秒内告诉我现在该做什么 (Priority: P1)

作为第一次上手的用户，我希望打开 `Home` 后能在 5 秒内知道：系统现在能不能用、如果还差一步那是哪一步、我下一步该点哪里。

**Why this priority**: 这是当前最直接的 usability blocker。首页如果不能承担“入口”角色，后面再强的能力都很难被用户正确发现。

**Independent Test**: 打开 `Home`，不看 `Advanced`，仅根据首屏内容完成“判断是否可用并采取下一步动作”的操作。

**Acceptance Scenarios**:

1. **Given** setup 还未完全 ready，**When** 用户打开首页，**Then** 首屏标题和主按钮以用户语言说明“还差哪一步”，而不是显示 `action_required`、`degraded` 或泛化标题。
2. **Given** 当前系统已经可用，**When** 用户打开首页，**Then** 首屏主按钮是“进入聊天”或等价动作，而不是继续推用户回设置页猜问题。
3. **Given** 存在 pending approval、pending setup action 或 degraded runtime，**When** 用户查看首页，**Then** 每种状态都以“影响 + 下一步动作”表达，而不是原始状态名。

---

### User Story 2 - Settings 首屏先告诉我最少要配什么 (Priority: P1)

作为普通用户，我希望进入 `Settings` 后先看到“最少只需要补这几项就能开始用”，而不是先被 Provider / alias / Memory / policy 的全量结构压住。

**Why this priority**: 设置页是首次体验闭环的第二站。如果这里仍像工程配置中心，用户会失去“很快就能开始用”的感觉。

**Independent Test**: 进入 `Settings`，用户只看首屏就能知道“当前是否是 echo 模式”“最少还差哪些配置”“配置完应该回哪里验证”。

**Acceptance Scenarios**:

1. **Given** 当前还是 echo 模式，**When** 用户进入 `Settings`，**Then** 首屏优先强调“连接真实模型”与所需最小字段，而不是完整配置结构。
2. **Given** review 存在阻塞项，**When** 用户浏览首屏，**Then** 用户看到的是清单式下一步和对应入口，而不是仅看到阻塞数量。
3. **Given** 配置已经足够开始聊天，**When** 用户进入 `Settings`，**Then** 首屏明确提示“可以回到聊天验证”，而不是继续停留在配置焦点。

---

### User Story 3 - 等待 Butler / Worker 处理时，我知道系统在做什么 (Priority: P1)

作为正在聊天的用户，我希望在问题需要搜索、委派或较长处理时，界面立刻告诉我“现在正在处理”，并在必要时展示折叠式协作进展，而不是长时间空白等待。

**Why this priority**: 这是当前实时问题体验的最大感知缺口。系统实际已经在做 A2A 协作，但用户体验上像“卡住了”。

**Independent Test**: 发送需要 freshness delegation 的消息后，界面 1 秒内出现等待态或进度态，不需要跳转 `Advanced` 也能理解发生了什么。

**Acceptance Scenarios**:

1. **Given** 用户发送一条消息，**When** 任务已创建但最终回复未完成，**Then** 聊天区立即显示正在处理的占位反馈，而不是只有按钮文字变成“发送中”。
2. **Given** 这轮请求被 Butler 委派给 Research Worker，**When** 用户展开协作面板，**Then** 可以看到类似“已委托 Research / 正在检索 / 已回传结果”的用户语言事件，而不是 raw A2A payload。
3. **Given** 后端受限或工具不可用，**When** 等待态结束，**Then** 系统明确区分“当前工具后端不可用”和“系统本身不会做”。

---

### User Story 4 - 普通页面不再泄漏内部摘要和技术垃圾 (Priority: P2)

作为普通用户，我希望在 `Home / Chat / Settings` 这些主页面看到的是对我有意义的结果，而不是 `telegram: [object Object]`、tool traces、raw summary、内部 ID 或调试术语。

**Why this priority**: 这会直接损害信任感，并违反当前项目对普通用户 UI 的约束。

**Independent Test**: 浏览首页和聊天页，不进入 `Advanced`，验证不再看到 raw object string、tool traces 或内部状态枚举。

**Acceptance Scenarios**:

1. **Given** channel summary 中存在结构化对象，**When** 首页渲染，**Then** 显示为用户语言摘要，而不是 `String(value)` 的结果。
2. **Given** context recent summary 含有 `web.search / tool json / runtime ids`，**When** 首页展示最近摘要，**Then** 普通视图不会原样透出这些内部内容。
3. **Given** 用户需要更深层细节，**When** 用户主动展开或跳转 `Advanced`，**Then** 才能看到完整技术事实。

## Edge Cases

- 当系统同时存在 `setup blocking + pending approvals + degraded runtime` 时，首页必须给出主优先级排序，不允许三个状态并列争抢主行动。
- 当没有任何历史 work，但存在历史 sessions 时，首页不能用“当前工作 0”暗示系统坏了，而要清楚区分“没有正在运行的任务”和“没有历史记录”。
- 当 A2A conversation 尚未落回完整消息，但 active work 已经确认进入 worker 协作时，聊天页需要先展示高层阶段，不强依赖完整 message list。
- 当请求很快完成时，等待态不能造成视觉闪烁；需要最小展示策略。
- 当用户切换到移动端或窄屏时，主行动、状态摘要与等待反馈仍需优先可见。

## Functional Requirements

- **FR-001**: `Home` 首屏 MUST 以单一用户结论和单一主行动表达当前状态，不得继续直接使用 raw readiness label 作为最终标题。
- **FR-002**: 普通 surface MUST 不直接暴露 `action_required`、`degraded`、`wizard.status`、`diagnostics.overall_status` 等内部状态名；这些状态必须被翻译为“影响 + 下一步动作”。
- **FR-003**: 首页 summary cards MUST 采用用户可理解的统计口径，并明确区分“待处理 / 进行中 / 最近完成 / 历史累计”，不得用混合口径误导用户。
- **FR-004**: 首页与普通页面 MUST 修复结构化对象字符串化泄漏问题，包括但不限于 `telegram: [object Object]`。
- **FR-005**: 首页 MUST 不再把 raw context summary、tool traces、runtime IDs 作为普通用户信息块直出。
- **FR-006**: `Settings` 首屏 MUST 提供“最少必要配置”导向，明确指出当前是否仍在 echo 模式、最少缺哪些项，以及完成后建议去哪里验证。
- **FR-007**: `Settings` 首屏 SHOULD 将完整配置结构降级为次级导航，优先展示首次可用 checklist、主 CTA 和返回验证路径。
- **FR-008**: `Chat` MUST 在发送后立即呈现等待态，不得只把状态变化收缩为按钮文字。
- **FR-009**: `Chat` MUST 在存在 Butler -> Worker 内部协作时，提供折叠式“内部协作进度”面板，默认展示用户语言阶段，不暴露 raw prompt 或 chain-of-thought。
- **FR-010**: 协作进度面板 MUST 复用现有 `A2AConversation / A2AMessage / Work runtime_summary` 事实源，不新增平行 runtime 管道。
- **FR-011**: 普通 surface 的失败提示 MUST 明确区分“信息不足，需要你补充”“当前工具/环境不可用”“系统正在降级运行”三类情况。
- **FR-012**: 本 Feature MUST 更新前端测试，覆盖首页主行动、Settings 首屏 checklist、聊天等待态和折叠式协作进度。

## Key Entities

- **HomePrimaryActionState**: 首页最终给用户的单一结论与主 CTA，来自 readiness / pending / runtime 状态综合判断。
- **UserFacingProgressStage**: 面向普通用户的聊天进度阶段，如“主助手已接手”“已委托专门角色”“正在整理结果”。
- **CuratedCollaborationEvent**: 从 `A2AConversation / A2AMessage / Work` 派生的折叠事件项，只保留方向、阶段、影响与下一步。
- **SettingsMinimumChecklist**: 设置首屏的最小必要步骤集合，强调“当前缺什么、配完去哪验证”。

## Success Criteria

- **SC-001**: 新用户在 5 秒内可以从 `Home` 判断当前是否可用以及下一步该点哪里。
- **SC-002**: `Home`、`Settings`、`Chat` 普通视图不再出现 raw object string、raw runtime summary 和内部状态枚举。
- **SC-003**: 发送需要 freshness delegation 的问题后，聊天页在 1 秒内展示等待态，并能在协作发生时显示折叠式进度。
- **SC-004**: 用户无需进入 `Advanced` 也能理解“为什么现在要去设置 / 为什么正在等待 / 为什么这次失败是环境问题还是信息不足”。
- **SC-005**: 相关前端回归通过，覆盖首页、设置首屏和聊天协作等待的核心体验路径。
