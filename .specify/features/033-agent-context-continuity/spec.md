---
feature_id: "033"
title: "Agent Profile + Bootstrap + Context Continuity"
milestone: "M3 carry-forward"
status: "Implemented"
created: "2026-03-09"
updated: "2026-03-11"
research_mode: "full"
blueprint_ref: "docs/m3-feature-split.md Feature 033；docs/blueprint.md M3 产品化约束；Feature 025 / 027 / 030 / 031"
predecessor: "Feature 025（Project / Workspace / Secret / Wizard）；Feature 027（Memory Console）；Feature 030（Capability Pack / Delegation Plane）；Feature 031（M3 Acceptance）"
parallel_dependency: "Feature 033 定义主 Agent 的上下文连续性主链；后续任何 Memory/Agent/Automation 增量都必须消费 033 的 canonical profile/context contract，不得重新拼 prompt metadata"
---

# Feature Specification: Agent Profile + Bootstrap + Context Continuity

**Feature Branch**: `codex/feat-033-agent-context-continuity`
**Created**: 2026-03-09
**Updated**: 2026-03-11
**Status**: Implemented
**Input**: 补齐 OctoAgent 当前主 Agent 没有真实接入 Memory、用户基础信息、Bootstrap 和正式 Agent Profile 的结构性缺口；借鉴 OpenClaw 与 Agent Zero 的产品形态，但在 OctoAgent 现有 Project / Memory / Control Plane / ToolBroker / Policy 体系内落地。  
**调研基础**: `research/product-research.md`、`research/tech-research.md`、`research/online-research.md`、`research/research-synthesis.md`

## Problem Statement

当前 master 已具备 M3 的 Project、Control Plane、Memory Console、MemU、Import Workbench 和 Delegation Plane，但真正落到运行时后仍然缺少一条“主 Agent 上下文连续性主链”。

已确认的真实缺口：

1. **主 Agent 没有正式 Profile 解析链**  
   蓝图和 `docs/m3-feature-split.md` 已把 `AgentProfile` 定义为正式产品对象，但代码里实际落地的只有 `WorkerCapabilityProfile` / worker bootstrap 模板；仓库中没有 `AgentProfile` 的 durable model、store、resolver，也没有 session / automation / work 对 `agent_profile_id` 的正式继承。

2. **用户与 Agent 的基础身份信息没有 canonical object**  
   025 的 wizard 只覆盖 provider/channel/runtime/config；系统没有 Owner/User 基础信息、Assistant identity、interaction preference、working style、boundary notes 这些正式对象，也没有 OpenClaw `BOOTSTRAP.md -> IDENTITY.md / USER.md / SOUL.md` 那类首启引导在 OctoAgent 里的等价物。

3. **主 Agent 调用链没有上下文装配层**  
   `TaskService._call_llm_service()` 当前直接把 `user_text` 传给 `LLMService.call()`；运行时既不读取 project instructions，也不读取 user profile / bootstrap / recent session summary / long-term memory hits，只把 `dispatch_metadata` 当作附加字段透传。

4. **Memory 目前几乎只被导入链和管理台消费**  
   `MemoryService.search_memory()` / `get_memory()` 在生产代码里没有进入主 Agent 的 prompt 组装路径；除导入和 control-plane 外，主 Agent 没有任何基于 `project/workspace/scope` 的 memory retrieval。

5. **短期上下文只存在进程内，不可恢复也不可解释**  
   `LiteLLMSkillClient` 目前把对话历史保存在进程内 `_histories` 字典里；这既不 durable，也不是 session-level canonical context，更无法和 project/profile/memory 形成统一的预算与审计链。

6. **030 的 bootstrap 只给 worker preflight 使用，没有接到主 Agent**  
   当前 `CapabilityPackService.render_bootstrap_context()` 只在 `DelegationPlaneService` 的 preflight pipeline 中写入 `bootstrap_context`；主 Agent 首次启动、session startup、automation startup 并不会消费这些 bootstrap 信息。

因此，当前问题不是“Memory Core 没做”，而是 **主 Agent 根本没有 consume 这些能力的运行时产品面**。如果不补这层，OctoAgent 会继续停留在“有 Memory 和 Profile 的底层能力，但用户实际聊天体验像无记忆 Agent”的状态。

## Product Goal

把 `project -> agent profile -> bootstrap -> session recency -> memory retrieval -> work/automation inheritance` 收敛为一条真实可运行、可恢复、可审计的上下文连续性主链：

- 定义 `AgentProfile`、`OwnerProfile`、`OwnerProfileOverlay`、`BootstrapSession`、`SessionContextState`、`ContextFrame`
- 让主 Agent 在每次 task/session 执行前都通过统一 `AgentContextService` 解析 effective context
- 把短期上下文从“进程内 history”升级为 durable session context state + rolling summary
- 把长期记忆检索正式接到主 Agent / worker / automation / delegation 路径
- 复用 025 的 project/workspace/wizard 基线、027 的 Memory governance、030 的 bootstrap/capability/delegation 基线
- 把 profile / bootstrap / context assembly / retrieval provenance 接入 control plane
- 保证所有记忆读取/写入、工具调用、自动化与委派仍走现有 ToolBroker / Policy / Event / Audit 治理面

## Scope Alignment

### In Scope

- `AgentProfile` / `OwnerProfile` / `OwnerProfileOverlay` / `BootstrapSession` / `ContextFrame` 正式模型与 durable store
- project-scoped default agent profile 绑定、session/automation/work 的 `agent_profile_id` 与 effective config snapshot
- 主 Agent 的统一上下文装配服务：
  - project instructions
  - owner basics / assistant identity
  - bootstrap-derived guidance
  - session recent turns / rolling summary
  - memory retrieval plan + evidence refs
  - delegation/runtime context
- 短期上下文 durable state（不再只依赖进程内 `_histories`）
- 与 `MemoryService.search_memory()` / `get_memory()` 的真实运行时接线
- bootstrap 首启 / project-init 引导，支持 CLI / Web / chat surface 共享同一 canonical session
- control-plane 资源与动作：
  - agent profiles
  - owner profile / assistant identity
  - bootstrap session
  - context sessions / context frames / retrieval audit
- 关键单元测试、集成测试、e2e 测试与验收矩阵

### Out of Scope

- 重做 027 的 Memory Console / Vault 详细领域视图
- 重新定义 025 的 Secret Store 或 026 的 control-plane shell
- M4 remote nodes / companion surfaces / mobile-native bootstrap
- 把所有长期偏好都强行塞进 Memory；Profile 仍应是正式对象，Memory 只承接可检索事实与证据
- 全图形化 prompt editor 或全自由模板系统

## User Stories & Testing

### User Story 1 - 新用户第一次和 Agent 对话时，系统会真正建立“我是谁、你是谁、我们怎么协作”的基础上下文 (Priority: P1)

作为 owner，我希望第一次使用 OctoAgent 时，系统不仅配置 provider/channel，还能完成最小的 Agent bootstrap：收集我的基本信息、Agent 的默认定位、互动偏好和边界，这样后续对话不会每次都像新开一个陌生模型。

**Independent Test**: 在干净实例中发起首条聊天，系统创建 bootstrap session；完成 bootstrap 后再次发送消息，主 Agent 返回内容中体现已生效的 owner/assistant basics，而不是仅有默认 echo/general assistant。

### User Story 2 - 继续对话时，主 Agent 能记住最近上下文和相关长期记忆，而不是只看当前一句话 (Priority: P1)

作为 owner，我希望第二轮、第三轮聊天时，主 Agent 会自动带上最近对话摘要和与当前问题相关的 Memory hits，这样它能连续理解上下文，而不是每轮都重新开始。

**Independent Test**: 在同一 session/thread 中连续发送多轮消息并重启进程后继续对话；验证 recent summary、selected memory hits 和 provenance 仍然存在，且回复不退化为只基于最新一句话。

### User Story 3 - 切换 project 后，Agent 的 persona、记忆、偏好和 bootstrap 互不串用 (Priority: P1)

作为同时维护多个 project 的 owner，我希望不同 project 可以有不同 AgentProfile、不同 bootstrap guidance、不同记忆作用域，切换 project 后不会串上下文。

**Independent Test**: 准备两个 project，分别绑定不同 owner/assistant overlay、memory scope 与 default agent profile；切换 project 后执行聊天和 automation，验证 effective config snapshot 和 memory retrieval 不串用。

### User Story 4 - 我可以在控制台里看见“这次上下文是怎么组装出来的” (Priority: P1)

作为 operator，我希望控制台能展示这次 session/task 使用了哪个 agent profile、引用了哪些 bootstrap 段、最近摘要是什么、命中了哪些 memory hits、为什么 degraded，这样我能调试而不是猜 prompt。

**Independent Test**: 打开 control plane 的 context 视图，验证至少能看到 `agent_profile_id`、owner profile revision、bootstrap session 状态、recency summary、memory hits、context budget 与 degraded reason。

### User Story 5 - automation / delegation / worker 也会继承这套上下文链，而不是只在主聊天里临时生效 (Priority: P2)

作为 operator，我希望 automation run、delegated work 和 worker/subagent 在执行时继承同一套 effective context snapshot，并在控制台/事件链中可追溯。

**Independent Test**: 创建一个绑定 project/default agent profile 的 automation 并触发 delegation；验证 work、pipeline、worker runtime 都能引用同一个 `context_frame_id` 或等价 snapshot ref。

## Edge Cases

- 首条消息到达时 bootstrap 尚未完成，系统应如何 fail-soft，而不是直接让用户卡死在不可用状态？
- Memory backend unavailable 或向量检索降级时，是否仍能用 recent summary + project/profile/bootstrap 保持最小连续性？
- session recent history 过长时，如何在不破坏 durability 的前提下生成 rolling summary 并保留 provenance？
- project 默认 profile 缺失、被删除或版本过期时，如何回退到 safe default，并把 degraded reason 暴露给控制台？
- group/chat surfaces 中如何避免泄露仅 owner main session 可见的长期 profile/memory？
- automation / worker / subagent 若显式覆盖 agent profile，如何保证 override 是可追溯、可恢复、可审计的？

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 定义并持久化 `AgentProfile`，至少包含 persona、instruction overlays、model route、tool profile、policy refs、memory access policy、context budget policy、bootstrap template refs。
- **FR-002**: 系统 MUST 定义并持久化全局 `OwnerProfile` 基线，至少包含 display name、timezone、locale、preferred address、working style、interaction preferences、boundary notes。
- **FR-002A**: 系统 MUST 定义并持久化 project/workspace-scoped `OwnerProfileOverlay`，用于表达不同 project 下的 assistant identity、working style、interaction preference 和 boundary overrides。
- **FR-003**: 系统 MUST 定义 `BootstrapSession` / `BootstrapTemplate`，并支持 system-default 与 project-scoped bootstrap flow。
- **FR-004**: 025 的 project/workspace MUST 能绑定 default `agent_profile_id`；session、automation、work MUST 明确记录实际选择的 `agent_profile_id`。
- **FR-005**: 系统 MUST 提供统一 `AgentContextService`（或等价模块），在每次主 Agent / automation / delegation 执行前解析 effective context。
- **FR-006**: `AgentContextService` MUST 至少装配以下层：
  - project/workspace bindings 与 instructions
  - owner profile / assistant identity
  - bootstrap-derived guidance
  - recent conversation summary / recent artifacts
  - memory retrieval hits / evidence refs
  - route/delegation/runtime metadata
- **FR-007**: 主 Agent 的实际 LLM 调用链 MUST 真实消费该 context assembly 结果，而不是继续仅传递 `user_text`。
- **FR-008**: session recent context MUST durable；系统不得只依赖 `LiteLLMSkillClient` 进程内 `_histories` 作为唯一短期上下文来源。
- **FR-009**: 短期上下文 MUST 支持 rolling summary / checkpoint / restart recovery，并与 session/thread 绑定。
- **FR-010**: 长期记忆检索 MUST 通过既有 `MemoryService.search_memory()` / `get_memory()` 进入运行时；不得直接读取底层表或绕过 020/027/028 的治理边界。
- **FR-011**: bootstrap / profile / context continuity 中涉及长期事实沉淀时，MUST 通过既有 `WriteProposal -> validate -> commit_memory()` 进入权威 Memory。
- **FR-012**: context assembly MUST 生成 durable `ContextFrame`（或等价快照），并记录输入来源、budget、selected memory hits、degraded reason、generated summary refs。
- **FR-013**: automation / work / pipeline / worker runtime MUST 能引用 `context_frame_id` 或等价 snapshot ref，保证继承链可追溯。
- **FR-014**: 当前主 Agent / worker / subagent / automation surfaces MUST 共享同一 canonical profile/context semantics，不得各自拼一套 prompt metadata。
- **FR-015**: control plane MUST 增量提供 `agent_profiles`、`owner_profile`、`bootstrap_session`、`context_session/context_frame` 资源或等价投影，不得重做 026 shell。
- **FR-016**: control plane MUST 能展示 context provenance：profile、bootstrap、recency summary、memory hits、budget、degraded reason。
- **FR-017**: bootstrap flow MUST 支持 CLI、Web 与 chat surface 共用同一 canonical session/state，而不是各自写一套草稿状态。
- **FR-018**: 如果 profile/bootstrap/memory 任一层不可用，系统 MUST degrade gracefully，并在 runtime result 与 control plane 中显式标注 degraded reason。
- **FR-019**: 在 group/shared context 中，系统 MUST 支持将 owner-private bootstrap/profile/memory 标记为 main-session-only，避免跨 surface 泄露。
- **FR-020**: Feature 033 MUST 提供单元测试、关键集成测试和至少一条 e2e：证明“上下文真的被接进实际响应链”，而不是只有模型/store 和伪测试。
- **FR-021**: Feature 033 MUST 与 Feature 031 的 acceptance matrix 衔接，补充“Agent context continuity” 验收 gate，明确其阻塞 live cutover 的风险级别。

### Key Entities

- `AgentProfile`
- `OwnerProfile`
- `OwnerProfileOverlay`
- `AssistantIdentity`
- `BootstrapTemplate`
- `BootstrapSession`
- `SessionContextState`
- `ContextFrame`
- `ContextSourceRef`
- `MemoryRetrievalPlan`
- `EffectiveAgentConfigSnapshot`

## Success Criteria

- **SC-001**: 首次对话后，系统能够形成最小 owner/assistant bootstrap，并在后续对话中真实生效。
- **SC-002**: 同一 session 在进程重启后继续对话时，recent context continuity 仍成立，不依赖进程内 history。
- **SC-003**: 主 Agent 的实际 LLM 输入链能消费 project/profile/bootstrap/recency/memory 五层上下文，而不是仅一层 `user_text`。
- **SC-004**: project 切换不会串用 agent profile、owner overlay、recent context 或 memory retrieval。
- **SC-005**: control plane 能解释至少一条真实响应所使用的 context provenance 和 degraded reason。
- **SC-006**: automation / delegation / worker 至少一条关键路径能继承同一 `context_frame` 或等价 snapshot ref。

## Clarifications

### Session 2026-03-09

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 033 是新增 M4 体验功能，还是 M3 已交付能力的缺口补位？ | M3 carry-forward gap closure | 用户指出的是“当前实际 Agent 不可用”的结构性缺口，不是锦上添花 |
| 2 | 是否允许继续把 `AgentProfile` 隐含在 `AGENTS.md` / bootstrap 文件文本里？ | 否 | 蓝图已要求 `AgentProfile` 是正式对象，不能只靠文本约定 |
| 3 | 是否把短期上下文直接当作长期 Memory 写入？ | 否 | 短期 continuity 与长期治理需要两层对象；长期沉淀仍走 WriteProposal |
| 4 | bootstrap 缺失时是否直接阻断所有首聊？ | 否 | 必须 fail-soft，用 safe default 回答并引导完成 bootstrap |
| 5 | 是否允许 Memory retrieval 旁路 control/audit 直接读底层库？ | 否 | 必须继续服从现有 Memory governance 边界 |

## Scope Boundaries

### In Scope

- agent/owner/bootstrap/context 的正式对象
- 主 Agent 真实 context assembly 接线
- recent summary durability
- memory retrieval integration
- control-plane 可视化与调试面
- tests / docs / acceptance gate 更新

### Out of Scope

- 重做 Memory Console
- Secret Store 实值管理
- remote nodes / companion surfaces
- prompt IDE / workflow editor

## Risks & Design Notes

- 如果 033 只补模型和控制台、不改 `TaskService -> LLMService` 的真实输入链，就仍然是假实现。
- 如果把所有 continuity 都写成 prompt 文本文件而没有 canonical store，就会再次退化成无法继承、无法审计、无法隔离的隐式约定。
- 如果 recent summary 不 durable，只存在 client/session 进程内状态，重启恢复与 automation 路径仍会断。
- 如果 memory retrieval 直接读底层 store 而不是复用 `MemoryService`，会破坏 020/027/028 已冻结的治理边界。
