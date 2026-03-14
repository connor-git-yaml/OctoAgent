---
feature_id: "051"
title: "Home / Settings Information Architecture Reset"
milestone: "M4"
status: "Implemented"
created: "2026-03-14"
updated: "2026-03-14"
research_mode: "tech-only"
blueprint_ref: "docs/blueprint.md §Web UI / UX 规范（普通用户优先、先回答影响与下一步）；Feature 035（Guided User Workbench）；Feature 044（Settings Center Refresh）；Feature 048（Guided Surface Clarity Refresh）"
predecessor: "Feature 044、048"
project_context_note: "本 Feature 基于真实用户体验反馈、本地代码审查，以及本地参考仓库 OpenClaw / Agent Zero 的 onboarding 与 dashboard 路径整理；未新增在线调研。"
---

# Feature Specification: Home / Settings Information Architecture Reset

**Feature Branch**: `codex/051-home-settings-ia-reset`  
**Created**: 2026-03-14  
**Updated**: 2026-03-14  
**Status**: Implemented  
**Input**: 基于真实用户体验，重做 `Workbench shell + Home + Settings` 的信息架构。目标不是继续润色文案，而是把首页从“控制台拼盘”改成“当前是否可用、现在最值得做什么、如果不处理会有什么影响”的普通用户入口；同时把 `Settings` 收成“最少必要配置 + 配完去哪验证”的启动页，而不是配置中心首页。

## Problem Statement

`048` 已经做了一轮文案和等待态优化，但从真实体验看，`Home / Settings` 仍然存在结构性问题：

1. **首页仍然是控制台思维，不是入口页思维**
   - `Home` 同时混入 setup wizard、operator pending、工作统计、记忆统计、Project/Workspace 切换
   - 用户看到了很多数字和块，但不知道哪个是真的要做、哪个只是系统后台状态

2. **大量统计口径对普通用户没有价值**
   - `待确认 2 / 可见 work 35 / 记忆记录 62`
   - `待处理事项 2，审批 0 / 协作请求 0`
   - `系统已经替你记住了多少`
   这些信息不是错就是没意义，甚至会误导

3. **首页仍然泄漏内部实现和后台术语**
   - action code（如 `SETUP_REVIEW_READY`）
   - raw runtime state
   - 控制台式 current record / work count / pending total

4. **Settings 首屏仍然更像配置中心，而不是启动引导**
   - 虽然已经收口成“最少必要步骤”，但首屏还是在讲 Provider、别名、Memory、Agent 能力
   - 用户还是会觉得“我得先学会这个系统”，而不是“我现在最少只要做什么”

5. **Project / Workspace 切换时机不对**
   - 当前只有默认项目与默认 workspace 时，首页仍然渲染切换器
   - 产品目前也没有一条普通用户主路径去创建第二个 project/workspace
   - 这会把未来能力提前暴露成当前噪音

因此，051 的目标不是继续给 `Home` 加卡片，而是：

> 把普通用户第一眼进入系统时看到的内容，收成“一个主结论、一个主动作、一个影响说明”，并把其余控制台信息迁回 Work / Memory / Advanced。

## Product Goal

交付一个真正面向普通用户的入口面：

- `Workbench shell` 不再用全局统计数轰炸用户
- `Home` 只回答三件事：现在能不能用、现在最值得做什么、不处理会有什么影响
- `Home` 的“待处理事项”使用真实事项列表，而不是总数 + 错误子口径
- `Home` 只在存在多个项目/工作区时显示切换入口
- `Settings` 首屏优先强调“最少需要配置什么、哪些事情现在不用管、配完去哪验证”
- 记忆统计、工作累计、原始状态码、后台摘要不再占普通用户主路径

## Scope Alignment

### In Scope

- `WorkbenchLayout` 的顶栏、侧栏、操作结果条的人话化和降噪
- `Home` 的 hero、主 CTA、主要面板、待处理事项表达、最近记录表达
- `Home` 中 Project / Workspace 切换器的显隐逻辑与位置重排
- `SettingsOverview` 的首屏 IA 重做
- 普通用户路径对 action code / raw count / memory record counter 的去泄漏
- 针对真实 operator items 的用户语言整理

### Out of Scope

- `Chat` 协作进度与工具绑定（由另一条 worktree 处理）
- `Advanced / Control Plane` 的深度诊断表达
- 新增 Project / Workspace 创建流
- 重新设计整个 Work / Memory 页面
- 大规模视觉品牌和配色系统重做

## User Scenarios & Testing

### User Story 1 - 首页 5 秒内告诉我现在最值得做什么 (Priority: P1)

作为第一次或低频使用的用户，我希望进入 `Home` 后，5 秒内知道系统是否可直接使用、现在最值得做的动作是什么，而不是先解读后台状态和统计数字。

**Independent Test**: 打开 `Home`，不进入 `Advanced`，仅凭首屏内容判断“是否能直接开始聊天”和“现在应先处理什么”。

**Acceptance Scenarios**:

1. **Given** 系统已可直接使用，**When** 用户进入 `Home`，**Then** 首屏主结论是“可以直接开始聊天”或等价表达，而不是继续强调启动检查。
2. **Given** 当前存在真实待处理事项，**When** 用户进入 `Home`，**Then** 页面显示的是具体事项标题与影响，而不是只显示一个总数。
3. **Given** 当前只是 runtime degraded 但不阻塞对话，**When** 用户进入 `Home`，**Then** 页面明确说明“还能继续做什么”和“受影响的能力是什么”。

---

### User Story 2 - 首页不再展示无意义统计和后台计数 (Priority: P1)

作为普通用户，我不希望在首页和全局顶栏看到 `待确认 2 / 可见 work 35 / 记忆记录 62 / current records` 这类控制台计数，因为这些数字不能帮助我做决策。

**Independent Test**: 浏览 `Workbench shell + Home`，普通视图中不再出现后台累计计数和无法解释的内存/工作统计。

**Acceptance Scenarios**:

1. **Given** 工作台加载完成，**When** 用户浏览侧栏和顶栏，**Then** 不会再看到 `可见 work`、`记忆记录`、`current records` 这类计数。
2. **Given** `operator_summary.total_pending > 0`，**When** 首页展示待处理事项，**Then** 必须同步展示真实事项类型与标题，不得再只展示总数。
3. **Given** 最近一次有 setup action 成功，**When** 页面展示反馈 banner，**Then** 只展示用户语言 message，不展示 `[SETUP_REVIEW_READY]` 这类 code。

---

### User Story 3 - Project / Workspace 只在真的可切换时出现 (Priority: P1)

作为普通用户，我只有在系统里真的存在多个项目或多个工作区时，才需要看到切换入口；否则这块应该消失。

**Independent Test**: 当前只有一个 default project/workspace 时，`Home` 不显示空转的切换器；当存在多个选项时，才显示切换入口。

**Acceptance Scenarios**:

1. **Given** `available_projects.length = 1` 且当前项目只有一个 workspace，**When** 打开首页，**Then** 页面不渲染切换控件。
2. **Given** 当前项目存在多个 workspace，**When** 打开首页，**Then** 页面显示“切换工作上下文”并说明为什么需要切换。
3. **Given** 系统存在多个 project，**When** 用户切换 project/workspace，**Then** 首页文案和后续入口会同步更新到新上下文。

---

### User Story 4 - Settings 首屏先告诉我最少要配什么，什么可以后面再说 (Priority: P1)

作为普通用户，我希望进入 `Settings` 后先看到最少必要配置、哪些东西现在不用急、以及配好之后回哪里验证，而不是立刻被整个配置结构包围。

**Independent Test**: 只看 `Settings` 首屏，不往下滚，也能知道现在最少该做什么、暂时可以忽略什么、做完以后回哪里验证。

**Acceptance Scenarios**:

1. **Given** 当前还是 echo 模式，**When** 用户进入 `Settings`，**Then** 首屏优先强调“接一个真实模型”，而不是并列展示一堆配置域。
2. **Given** review 已 ready，**When** 用户进入 `Settings`，**Then** 首屏明确告诉用户“保存后回聊天验证”，而不是继续要求留在设置页。
3. **Given** Memory、渠道、Agent 能力当前不是阻塞项，**When** 用户查看首屏，**Then** 页面会把这些内容归入“现在不用急着处理”或次级入口，而不是和主步骤并列。

## Edge Cases

- 当存在 `degraded runtime + pending alerts + setup ready` 时，首页必须把“主动作”和“可继续使用”分开表达，不能回退成“一切都去设置检查”。
- 当 `pending items` 里全是 `alert/retryable_failure`，但没有 `approval/pairing` 时，页面不得使用“待确认”文案。
- 当最近对话摘要含有 tool trace、raw JSON 或 search 痕迹时，首页不得直接原样展示。
- 当 `lastAction.message` 缺失或为空时，shell banner 需要优雅回退，不能把 code 暴露出来。
- 当只有一个 project 但有多个 workspace，切换入口需要说明切换的是“工作上下文”而不是新项目。

## Functional Requirements

- **FR-001**: `WorkbenchLayout` MUST 移除普通用户无价值的全局累计计数，包括但不限于 `待确认 N / 可见 work N / 记忆记录 N / current records`。
- **FR-002**: `WorkbenchLayout` MUST 将最近一次 action banner 收口成用户语言 message，不得直接展示内部 action code。
- **FR-003**: `Home` MUST 以单一主结论和单一主 CTA 组织首屏；setup、pending、runtime degraded、active work 只能有一个主优先级。
- **FR-004**: `Home` 的“待处理事项” MUST 基于真实 `operator_items` 渲染标题、摘要和种类，不得继续只展示 `total_pending`。
- **FR-005**: `Home` MUST 去掉“背景记忆计数”“当前提醒三件事”“历史累计 work”等对普通用户无价值的信息块。
- **FR-006**: `Home` MUST 只在存在多个可选项目或工作区时渲染 Project / Workspace 切换器。
- **FR-007**: `Home` SHOULD 以“现在先做什么 / 如果你现在直接开始 / 最近一次记录”这一类用户任务语言组织面板，而不是继续使用 dashboard 术语。
- **FR-008**: `Settings` 首屏 MUST 优先表达“最少必要步骤、当前可以忽略的事情、配完后去哪里验证”。
- **FR-009**: `Settings` 首屏 MUST 降低 Provider / Alias / Memory / Agent 能力结构说明在首屏的占比，不得继续以六宫格配置状态卡为默认焦点。
- **FR-010**: 普通用户路径 MUST 不再显示 raw runtime id、raw status enum、后台累计计数和内部 code。
- **FR-011**: 本 Feature MUST 提供前端自动化测试，覆盖 shell 降噪、Home 新事项表达、Project/Workspace 切换显隐、Settings 首屏 IA。

## Key Entities

- **WorkbenchShellStatus**: 全局 shell 面向普通用户的状态摘要，只表达“现在可用性”和“是否有需要关注的事项”。
- **HomePrimaryNarrative**: 首页主结论、主 CTA 和影响说明的组合体。
- **HomeAttentionItem**: 从 `operator_items`、setup blocking reason 或 active work 派生出的“当前值得处理的事情”。
- **SettingsMinimumPath**: 首屏最少必要步骤、可延后项和验证出口的结构化表达。

## Success Criteria

- **SC-001**: 新用户进入 `Home` 后，不用阅读控制台术语，也能在 5 秒内判断“现在能不能开始”和“现在最值得做的一件事”。
- **SC-002**: 普通用户视图中不再出现 `可见 work`、`记忆记录`、`current records`、`[SETUP_REVIEW_READY]` 这类后台输出。
- **SC-003**: 当存在待处理事项时，首页显示的是具体事项而不是抽象总数。
- **SC-004**: 当前只有一个 project/workspace 时，首页不再展示无意义切换器。
- **SC-005**: `Settings` 首屏能明确区分“现在必须做的”“现在可以以后再做的”和“配完后去哪验证”。
