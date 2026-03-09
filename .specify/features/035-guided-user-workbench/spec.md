---
feature_id: "035"
title: "Guided User Workbench + Visual Config Center"
milestone: "M4"
status: "Planned"
created: "2026-03-09"
updated: "2026-03-09"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §8.10 M4；docs/m4-feature-split.md；Feature 015 / 017 / 025 / 026 / 027 / 030 / 033 / 034；OpenClaw wizard / dashboard / Control UI；Agent Zero projects / settings / memory dashboard"
predecessor: "Feature 015（Onboard + Doctor）、Feature 017（Operator Inbox）、Feature 025（Project / Workspace / Secret / Wizard）、Feature 026（Control Plane Contract）、Feature 027（Memory Console）、Feature 030（Capability Pack / Delegation Plane）、Feature 031（M3 Acceptance）"
parallel_dependency: "Feature 033 提供主 Agent profile/context provenance canonical object；Feature 034 提供 context compaction 状态与 evidence。035 必须直接消费这些接口，不得重定义。"
---

# Feature Specification: Guided User Workbench + Visual Config Center

**Feature Branch**: `codex/feat-035-guided-user-workbench`  
**Created**: 2026-03-09  
**Updated**: 2026-03-09  
**Status**: Planned  
**Input**: 把当前偏 operator/resource 的 Web 控制台，演进成真正面向小白用户的图形化工作台；支持图形化配置主 Agent / Work / Memory / Channels，并把聊天、任务、审批、记忆和高级控制面组织成连续的产品路径，而不是继续要求用户理解底层术语和 CLI 顺序。  
**编号说明**: 用户口头要求“整理成 Feature 034”，但仓库中 `034-context-compression-main-worker` 已存在且已实现；为保持 traceability，本次按下一个可用编号 `035` 建档，并把 Feature 034 作为直接依赖输入，而不是覆盖。  
**调研基础**: `research/product-research.md`、`research/tech-research.md`、`research/online-research.md`、`research/research-synthesis.md`

## Problem Statement

OctoAgent 当前已经具备 015/017/025/026/027/030/031 打下的大量正式能力，但 Web 入口仍然存在四个直接伤害“小白可用性”的结构性问题：

1. **默认入口仍是 operator/resource console，不是用户任务入口**  
   `frontend/src/App.tsx` 现在把 `/` 直接指向 `ControlPlane`；`ControlPlane.tsx` 以 `dashboard / projects / capability / delegation / pipelines / sessions / operator / automation / diagnostics / memory / imports / config / channels` 资源分区展开。对 operator 来说这是合理的，但对普通用户来说，这要求他先理解“capability pack / delegation plane / pipeline replay”等内部术语，才能开始使用产品。

2. **配置变更仍然偏 CLI 和工程化 schema，不是图形化流程**  
   015 已交付 `octo onboard` / `octo doctor` 的首次使用主链，026 已交付 `ConfigSchemaDocument + uiHints` 和 `config.apply`。但当前 Web 并没有把这些 contract 组织成“人话化、分步骤、带状态反馈”的图形化配置流程。普通用户仍然需要理解 provider、wizard、doctor、config.apply、channel mode 这些后端概念。

3. **聊天、任务、审批、记忆是割裂入口，不是一条连续工作流**  
   现有 `ChatUI.tsx` 是一套极简 inline-style 聊天组件；`TaskDetail.tsx` / `TaskList.tsx` 是另一套开发者导向页面；`ControlPlane.tsx` 又是第三套 operator 资源页。用户无法从“状态检查 -> 改设置 -> 发第一条消息 -> 跟踪 work -> 处理审批 -> 看记忆”形成连续路径。

4. **已有系统能力没有被“用户化表达”**  
   017 的 Operator Inbox、027 的 Memory Console、030 的 Delegation Plane、032 的 built-in runtime truth、033 的 context provenance、034 的 compaction evidence，都已经或即将成为正式系统能力；但如果 Web 仍只按资源原样堆出来，用户看到的仍然是后端名词，而不是“待你确认”“正在做的工作”“系统记住了什么”“当前主 Agent 上下文是否健康”。

因此，035 要解决的不是“再做一个更好看的控制台”，而是：

> 在不绕过既有 control-plane / wizard / memory / task / execution contract 的前提下，把 OctoAgent 的 Web 入口重构成真正可上手、可配置、可聊天、可跟踪、可解释的小白工作台。

## Product Goal

交付一个建立在既有 canonical backend 之上的 `Guided User Workbench`：

- 默认首页变成用户导向的 `Home / Chat / Work / Memory / Settings / Advanced` 六个一级入口
- 把主 Agent / Work / Memory / Channels 的常见配置变更改成图形化表单、卡片和向导
- 把聊天工作台做成真正的日常主入口：左侧会话、中间对话、右侧上下文抽屉
- 把 Work / Session / Approval / Execution 用“任务板 + 状态卡 + 右侧细节”的方式解释，而不是直接暴露底层资源名
- 把 Memory 做成可浏览、可解释、带安全边界的用户中心，而不是只有 operator 视图
- 保留现有 `ControlPlane` 作为 `Advanced` 模式，避免丢掉高级能力
- UI 视觉、布局、组件和文案形成正式产品壳，不再依赖大段 inline style 和“页与页之间像不同产品”的拼装感

## Scope Alignment

### In Scope

- Web 路由与信息架构重构：
  - `/` -> `Home`
  - `/chat`
  - `/work`
  - `/memory`
  - `/settings`
  - `/advanced`
- 新的 app shell、导航、状态条、卡片系统、表单系统、抽屉/面板、空状态、错误态和移动端布局
- 图形化配置中心：
  - 主 Agent
  - Work / delegation / approval 默认行为
  - Memory / Vault / compaction 可见性
  - Channels / pairing / readiness
- 聊天工作台：
  - `POST /api/chat/send`
  - `/api/stream/task/{task_id}`
  - `/api/tasks/{task_id}`
  - `/api/tasks/{task_id}/execution`
  - 现有 control-plane `sessions/delegation/memory/operator` 资源
- Work 看板与细节：
  - `SessionProjectionDocument`
  - `DelegationPlaneDocument`
  - `work.cancel / retry / split / merge / escalate`
- Memory 中心：
  - `MemoryConsoleDocument`
  - `MemorySubjectHistoryDocument`
  - `MemoryProposalAuditDocument`
  - `VaultAuthorizationDocument`
- 高级模式：
  - 保留并收编现有 `ControlPlane`
- 033 / 034 的直接消费：
  - 033 的 profile/bootstrap/context provenance
  - 034 的 compaction summary / degraded signal / evidence refs

### Out of Scope

- 新建第二套配置存储或前端私有 DTO 协议
- 重做 015 的 onboarding protocol、025 的 project/secret store、026 的 control-plane contract、027 的 memory governance
- 完整 file browser / editor / diff 工作台
- 语音、多模态、PWA、remote nodes、companion surfaces
- 把 `Advanced` 能力删掉或隐藏到不可达
- 由 035 自己实现 033/034 的领域逻辑；035 只能消费它们的正式接口

## Architecture Boundary

### Canonical Backend Rules

035 必须遵守以下接线边界：

- **首页与主导航状态** MUST 以 `GET /api/control/snapshot` 为首屏事实源
- **单资源刷新** MUST 以 `GET /api/control/resources/*` 为 canonical read API
- **动作执行** MUST 以 `POST /api/control/actions` 为 canonical mutation API
- **控制面事件** MUST 以 `GET /api/control/events` 为 canonical event API
- **聊天发送** MAY 使用 `POST /api/chat/send`
- **任务/执行细节** MAY 使用：
  - `GET /api/tasks/{task_id}`
  - `GET /api/tasks/{task_id}/execution`
  - `GET /api/tasks/{task_id}/execution/events`
  - `POST /api/tasks/{task_id}/execution/input`
- **不得** 为了 UI 方便新造平行 `settings/*`、`chat-workbench/*`、`memory-dashboard/*` 私有 REST

### Existing Interface Mapping

#### Home / Readiness

- 读取：
  - `snapshot.resources.wizard`
  - `snapshot.resources.project_selector`
  - `snapshot.resources.diagnostics`
  - `snapshot.resources.sessions.operator_summary`
  - `snapshot.resources.memory`
- 动作：
  - `wizard.refresh`
  - `wizard.restart`
  - `project.select`
  - `config.apply`
  - 现有 operator / channel / automation action

#### Settings / Visual Config

- 读取：
  - `GET /api/control/resources/config`
  - `GET /api/control/resources/project-selector`
  - 033 完成后增量读取 profile/context 资源
- 动作：
  - `config.apply`
  - `project.select`
  - 必要时在 026 action registry 上增量补 `wizard.step.*` 或等价 action，但不得绕开 action registry
- 约束：
  - 字段渲染 MUST 基于 `ConfigSchemaDocument.schema + ui_hints + current_value`
  - secret MUST 保持 refs-only 语义，不得把明文回显到浏览器缓存

#### Chat Workbench

- 发送与流式：
  - `POST /api/chat/send`
  - `/api/stream/task/{task_id}`
- 细节与上下文：
  - `GET /api/tasks/{task_id}`
  - `GET /api/tasks/{task_id}/execution`
  - `GET /api/control/resources/sessions`
  - `GET /api/control/resources/delegation`
  - `GET /api/control/resources/memory`
  - 033 完成后：context provenance resource
  - 034 完成后：compaction status / evidence ref
- 动作：
  - `session.focus`
  - `session.export`
  - `operator.approval.resolve`
  - `work.cancel / retry / split / merge / escalate`
  - `POST /api/tasks/{task_id}/execution/input`

#### Work Board

- 读取：
  - `GET /api/control/resources/sessions`
  - `GET /api/control/resources/delegation`
  - `GET /api/tasks/{task_id}`
  - `GET /api/tasks/{task_id}/execution`
- 动作：
  - `work.refresh`
  - `work.cancel`
  - `work.retry`
  - `work.split`
  - `work.merge`
  - `work.escalate`

#### Memory Center

- 读取：
  - `GET /api/control/resources/memory`
  - `GET /api/control/resources/memory-subjects/{subject_key}`
  - `GET /api/control/resources/memory-proposals`
  - `GET /api/control/resources/vault-authorization`
- 动作：
  - `memory.query`
  - `memory.flush`
  - `memory.reindex`
  - `vault.access.request`
  - `vault.access.resolve`
  - `vault.retrieve`

#### Advanced

- 现有 `ControlPlane.tsx` 保留为高级模式或其兼容页面
- 035 允许重构其视觉与布局，但不得删除它对 canonical resources / actions 的消费能力

## User Stories & Testing

### User Story 1 - 小白用户打开 Web 后，能立刻知道系统能不能用、下一步做什么 (Priority: P1)

作为第一次接触 OctoAgent 的用户，我希望首页直接告诉我：现在系统是否 ready、卡在哪一步、应该先配置什么、能不能发第一条消息，而不是先看一堆 operator 资源名称。

**Independent Test**: 在一个只有最小配置、一个未完成配置、一个已 ready 的环境中分别打开首页；验证页面都能基于 canonical resources 给出清晰状态卡和下一步动作。

### User Story 2 - 我可以图形化配置主 Agent / Work / Memory / Channels，而不是记命令和 YAML 字段 (Priority: P1)

作为普通用户，我希望在设置中心通过图形化表单完成常见配置变更，包括主 Agent、Work 默认行为、Memory 模式和 Channel readiness，而不是手工运行多条 CLI 或直接理解 schema/raw config。

**Independent Test**: 从设置页切换 project、修改配置字段并保存，验证底层仍走 `ConfigSchemaDocument + config.apply`，并且变更后 snapshot 与实际运行状态一致。

### User Story 3 - 聊天是主入口，并且我能看懂当前任务、work、记忆和审批状态 (Priority: P1)

作为日常用户，我希望聊天工作台既能发消息，也能让我理解“系统正在做什么、需不需要我确认、它记住了什么、这次上下文有没有问题”，而不是只能看到一串输出文本。

**Independent Test**: 在聊天工作台发起一条消息，期间触发 approval、child work 或 execution input；验证界面能在同一工作台内处理和解释这些状态。

### User Story 4 - 我能在同一产品壳里跟踪 Work，而不是在 task 页面和 operator 页面之间来回跳 (Priority: P1)

作为用户，我希望 Work 页面告诉我：哪些任务在运行、哪些等待确认、哪些失败、哪些 child works 已合并，以及每个 work 现在卡在哪个阶段。

**Independent Test**: 构造父 work + child work + retryable failure + merge ready 场景，验证 Work 页面能基于 sessions/delegation/task execution 形成清晰板式视图，并能执行 `work.*` 动作。

### User Story 5 - Memory 对我来说是“系统记住了什么”和“为什么这样记住”，而不是底层表结构 (Priority: P1)

作为用户，我希望在 Memory 页面先看到高层摘要，再逐步展开 subject history、proposal audit 和 Vault 授权，而不是一上来看到大量底层字段。

**Independent Test**: 使用 memory center 查询一组记录，点开 subject history 和 proposal audit；验证页面默认以摘要方式呈现，并仍可追溯到原始 canonical resource。

### User Story 6 - 高级用户仍然能进入完整控制面，不会因为“小白模式”而失去能力 (Priority: P2)

作为 operator 或开发者，我希望新的用户工作台不会砍掉现有 control plane，而是把它降为高级模式，这样我在需要诊断或做深度操作时仍有完整入口。

**Independent Test**: 从 `Advanced` 入口打开旧控制面，验证所有既有 canonical resource/action 仍可访问。

## Edge Cases

- `wizard` 未完成、`diagnostics` degraded、`channels` 未 ready 时，首页必须给出明确阻塞原因和下一步动作，不得只显示空白卡片。
- 当前 project 被删除或 workspace 失效时，UI 必须复用 `ProjectSelectorDocument.fallback_reason` 明确表达，而不是继续保留前端脏 selection。
- 033 尚未可用时，聊天右侧上下文抽屉必须显式显示“context continuity pending/degraded”，不能伪造 profile/context 来源。
- 034 compaction 未触发时，聊天页不得展示伪造的“已压缩”状态；触发后必须引用真实 artifact/event/evidence。
- memory backend degraded 或 vault default deny 时，Memory 页面必须返回安全摘要和申请入口，不得泄露敏感 payload。
- 移动端必须能完成：发消息、看首页状态、处理 approval、切 project、保存设置；不能只在桌面可用。

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 提供新的一级导航：`Home`、`Chat`、`Work`、`Memory`、`Settings`、`Advanced`。
- **FR-002**: 默认根路由 `/` MUST 指向 `Home`，现有资源导向 `ControlPlane` MUST 迁移为 `Advanced` 模式或兼容路由，而不是继续作为默认首页。
- **FR-003**: 首页首屏状态 MUST 仅由 `GET /api/control/snapshot` 驱动；后续刷新 MUST 只使用 canonical resource routes、action route 与 events route。
- **FR-004**: 035 MUST NOT 定义平行控制面 DTO；若现有 resource 缺字段，应先扩 control-plane canonical document，再消费之。
- **FR-005**: 设置中心 MUST 基于 `ConfigSchemaDocument.schema + ui_hints + current_value` 生成图形化表单，不得自行维护另一套配置 schema。
- **FR-006**: 设置中心 MUST 以“主 Agent / Work / Memory / Channels / Projects”用户分组呈现配置；如果某字段当前未被 backend `ui_hints` 暴露，035 MUST 补 backend hint，而不是在前端硬编码隐藏字段。
- **FR-007**: 所有配置保存 MUST 通过 `config.apply` 或等价 action registry 路径完成，并保留 backend validation、audit event 与 resource refresh 语义。
- **FR-008**: 聊天工作台 MUST 使用 `POST /api/chat/send` + SSE + `GET /api/tasks/{task_id}` + `GET /api/tasks/{task_id}/execution` 的真实运行链，不得构造假 transcript。
- **FR-009**: 聊天工作台 MUST 提供与当前 task/work 绑定的上下文抽屉，至少展示：当前任务状态、execution 状态、相关 work、operator/approval 状态、memory 摘要，以及 033/034 可用时的 context/compaction 状态。
- **FR-010**: Work 页面 MUST 复用 `SessionProjectionDocument` 与 `DelegationPlaneDocument` 呈现 session/work 的统一列表与细节，不得再要求用户在多个旧页面之间来回跳转。
- **FR-011**: Work 页面中的取消、重试、拆分、合并、升级 MUST 统一走既有 `work.*` actions，不得直接旁路 task runner 或 delegation store。
- **FR-012**: Memory 页面 MUST 以 `MemoryConsoleDocument` 为首页事实源，并渐进展开 `subject history`、`proposal audit` 与 `vault authorization`；不得把 Memory 重新建模成前端私有 store。
- **FR-013**: Operator / Approval / Pairing / Retry 等待处理项 MUST 在 `Home` 和 `Chat` 中以“待你确认”形式复用现有 operator inbox / actions，而不是另写第二套待办逻辑。
- **FR-014**: `Advanced` 模式 MUST 保留现有 `ControlPlane` 的完整能力，至少覆盖 `Projects / Sessions / Operator / Automation / Diagnostics / Config / Channels / Memory / Imports`。
- **FR-015**: 035 MUST 直接消费 Feature 015 的 wizard session / doctor results，不得新造 Web onboarding 协议。
- **FR-016**: 035 MUST 直接消费 Feature 017 的 operator action semantics，不得把 approval / retry / cancel 改写成 UI 私有结果码。
- **FR-017**: 035 MUST 直接消费 Feature 025 的 project/workspace selector 与 secret boundary，不得在浏览器侧保存 secret 实值。
- **FR-018**: 035 MUST 直接消费 Feature 026 的 snapshot/per-resource/actions/events canonical API；frontend 不得回退为以旧 `/api/ops/*` 或自拼 `/api/tasks/*` 为首要事实源。
- **FR-019**: 035 MUST 直接消费 Feature 027/028 的 memory console / proposal / vault / maintenance contract，不得通过前端直接访问底层 memory 表。
- **FR-020**: 035 MUST 直接消费 Feature 030 的 session/work/capability runtime truth；对 `graph/subagent/work` 的 UI 表达必须引用真实 status/refs，不得 UI 自造“更友好但不真实”的运行态。
- **FR-021**: 035 MUST 在 033 完成后消费其 profile/bootstrap/context provenance canonical resource；在 033 未完成时，UI MUST 显式 degraded，而不是伪装为已支持主 Agent continuity。
- **FR-022**: 035 MUST 在 034 完成后消费 compaction status / artifact refs / evidence，并在聊天工作台中用用户化语言解释，不得重新实现 compaction 逻辑。
- **FR-023**: 035 MUST 建立统一设计系统：颜色、字体、间距、卡片、按钮、表单、抽屉、空状态、错误态和移动端断点；root pages 中不得继续依赖大块 inline style 作为主实现方式。
- **FR-024**: 035 MUST 提供移动端可用布局；至少首页、聊天、设置、待确认三条路径在窄屏下可完成核心操作。
- **FR-025**: 035 MUST 支持 progressive disclosure：小白默认只看到状态、下一步、人话标签；高级细节通过“展开/查看原始详情/跳转 Advanced”进入。
- **FR-026**: 035 MUST 在视觉与文案上显式区分“可立即操作”“需要确认”“系统降级”“高级诊断”四种状态，不能继续把所有模块平权堆叠。
- **FR-027**: Feature 035 MUST 提供 frontend integration tests 与至少一条 e2e，直接验证“无需终端即可完成首页检查、图形化保存配置、发第一条消息、处理 approval、查看 Memory 摘要”。
- **FR-028**: Feature 035 MUST 定义“非伪实现”门禁：凡是页面显示的状态、按钮和结果，均需能追溯到现有 canonical resource/action/detail route，而不是只做静态原型或 mock。

### Key Entities

- `GuidedWorkbenchShellState`
- `HomeReadinessCard`
- `NextActionCard`
- `SettingsSectionModel`
- `ChatWorkspaceState`
- `ChatContextDrawerModel`
- `WorkBoardItem`
- `MemoryOverviewCard`
- `AdvancedConsoleEntry`

## Success Criteria

- **SC-001**: 打开 Web 根路由后，用户无需理解 `capability / delegation / pipeline` 等术语，也能判断当前系统是否 ready 以及下一步操作。
- **SC-002**: 用户可以在图形化设置中心完成至少以下五类变更中的四类，而不需要终端：project 切换、provider/channel 配置、主 Agent 默认设置、Work 默认行为、Memory 参数调整。
- **SC-003**: 用户可以从聊天工作台发起消息，并在同一工作台内看到任务状态、approval、work 状态和 memory/context 摘要。
- **SC-004**: Work 页面能够解释至少一个包含 child work 的真实案例，并通过既有 `work.*` action 完成一次控制动作。
- **SC-005**: Memory 页面能够让用户解释至少一条记录“系统记住了什么、为什么记住、是否涉及 proposal/vault”。
- **SC-006**: `Advanced` 模式仍然可进入并保留原 control plane 的完整能力，不因小白模式而降级。
- **SC-007**: 035 的所有主要页面都建立在既有 canonical backend 之上；实现阶段不得新增平行控制面 API。

## Clarifications

### Session 2026-03-09

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 用户口头提到“Feature 034”，是否覆盖已有 034？ | 否，按 035 建档 | 034 已被 context compaction 占用，覆盖会破坏依赖链 |
| 2 | 035 是重做 control plane backend，还是重组用户入口？ | 重组用户入口，必要时仅增量扩 canonical resource/action | 026 已冻结控制面合同，不应重复造后台 |
| 3 | 图形化配置是否允许前端私有 schema？ | 否 | 必须消费 `ConfigSchemaDocument + ui_hints` |
| 4 | 新工作台是否可以删除旧 `ControlPlane`？ | 否 | 高级诊断和 operator 面仍需保留 |
| 5 | 033/034 尚未或刚完成时，035 如何处理？ | 消费其 canonical output；缺失时显式 degraded | 不能因为 UI 需求回头重做领域逻辑 |

## Scope Boundaries

### In Scope

- 新的信息架构与产品壳
- 图形化设置中心
- 聊天工作台
- Work 页面
- Memory 页面
- Advanced 模式收编
- 对 015/017/025/026/027/030/033/034 的正式接口消费

### Out of Scope

- 新的 config backend
- 新的 memory backend
- 全功能 IDE/file manager
- voice / PWA / remote companion
- 重做 task/execution/runtime 领域模型

## Risks & Design Notes

- 如果 035 只做视觉改版，不把 `Home/Chat/Work/Memory/Settings` 和现有 canonical API 对齐，最终仍然会变成“更好看的假入口”。
- 如果设置页为了提速而绕过 `ConfigSchemaDocument` / `config.apply`，很快就会与 CLI/wizard 语义分叉。
- 如果聊天工作台继续只看 SSE 输出，不接 task/execution/session/work/memory 细节，用户仍然无法理解系统当前在做什么。
- 如果不把现有 `ControlPlane` 收编为 `Advanced`，后续 operator 与诊断能力会再次散落。
