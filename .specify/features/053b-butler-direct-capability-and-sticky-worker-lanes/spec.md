---
feature_id: "053"
title: "Butler Direct Capability & Sticky Worker Lanes"
milestone: "M4"
status: "Implemented"
created: "2026-03-14"
updated: "2026-03-14"
research_mode: "codebase-scan"
blueprint_ref: "docs/blueprint.md §2 Constitution；Feature 049/051/052；OpenClaw main-agent loop；Agent Zero loop-first orchestration"
predecessor: "Feature 049、051、052"
---

# Feature Specification: Butler Direct Capability & Sticky Worker Lanes

**Feature Branch**: `codex/053-butler-direct-capability-and-sticky-worker-lanes`  
**Created**: 2026-03-14  
**Updated**: 2026-03-14  
**Status**: Implemented  
**Input**: 继续把 OctoAgent 收口到更接近 OpenClaw / Agent Zero 的 loop-first 主链：Butler 默认直接拥有基础 Web / Filesystem / Terminal 能力，先自己解决有界问题；当识别到长期、复杂、持续同题材的任务时，再由 Butler 显式改写 handoff objective，并把相同题材问题稳定路由到某个特定 Worker lane。

## Problem Statement

虽然 051/052 已经把 OctoAgent 的主链推进到 session-native + trusted tooling surface，但当前仍有三个结构性缺口：

1. **profile-first 入口还没有完全归一到 single-loop canonical path**  
   当前显式 `requested_worker_profile_id` 请求，在 `delegation_plane` 内部能解析成 worker type，但 `orchestrator` 主入口仍主要按 `requested_worker_type` 判断 single-loop 资格。这导致一部分 profile-first 请求仍会掉回 retained A2A delegation 路径，而不是稳定进入 Butler single-loop。

2. **保留的 A2A delegation 语义仍然过薄**  
   当请求确实需要交给 Worker 时，Butler 目前主要做的是“选 worker + 挂工具”，A2A `TASK` payload 仍基本透传 raw user query。这样 Worker 接到的不是经过 Butler 收口后的任务目标，而更像“把用户原话转发给另一个 Agent”。

3. **Butler 直解能力面还不完整**  
   虽然 Web / Browser / MCP 默认面已经放宽，但仓库里 עדיין没有把 Filesystem / Terminal 作为正式 builtin tools 暴露给 Butler 单循环主链。结果是 Butler 还不能像 OpenClaw 主 Agent 那样，直接读取项目文件、列目录、执行受治理命令来解决有界问题。

这三个缺口叠加起来，造成当前体验仍然不像一个真正强壮的主 Agent：

- 可以直解的问题还不够多
- 该委派时没有高质量 handoff
- 同题材 follow-up 还没有稳定 sticky worker lane

## Product Goal

把 OctoAgent 的主助手收口成一条更明确的主链：

- Butler 默认是 **可直接解决问题的主 Agent**
- Butler 默认挂载基础 `web.search / web.fetch / filesystem.* / terminal.exec` 等受治理工具
- 对 bounded task，Butler 应优先在单循环里直接解决
- 对长期、复杂、持续同题材任务，Butler 应优先创建或继续使用同一个 specialist worker lane
- 就算进入 retained A2A delegation，Butler 发给 Worker 的也必须是：
  - objective
  - context capsule
  - tool contract
  - return contract
  而不是 raw user query

## Scope Alignment

### In Scope

- `requested_worker_profile_id -> requested_worker_type` 的早期规范化
- Butler single-loop 主链继续扩展到 profile-first worker lens
- Butler generic delegate objective composer
- generic sticky worker lane routing（跨同题材 follow-up）
- Butler 默认基础工具面：Web / Filesystem / Terminal
- `behavior/system/*.md` 与默认 behavior templates 对齐上述行为
- 后端回归测试与 feature 制品

### Out of Scope

- 重做整个前端 workbench trace 视觉系统
- 把所有 retained A2A path 都改成流式 push transport
- 一次性做完整 topic clustering / semantic routing service
- 下放 governance / policy / approval 到 prompt 自治

## User Stories & Testing

### User Story 1 - Butler 应该直接解决有界问题 (Priority: P1)

作为用户，我希望主助手在当前工具足够时直接完成查网页、读文件、执行受治理命令，而不是先把事情扔给 Worker。

**Independent Test**: 发起一个需要 `project.inspect + filesystem.read_text + terminal.exec` 的 bounded task，验证请求直接进入 Butler single-loop executor，并产生真实 `TOOL_CALL_*` 事件。

### User Story 2 - 同题材复杂任务应稳定进入同一条 Worker lane (Priority: P1)

作为用户，我希望当我持续追问同一个长期复杂主题时，Butler 能继续把问题交给同一类 Worker，而不是每轮都像第一次一样重新路由。

**Independent Test**: 连续两轮同题材 research/dev 请求，验证第二轮会优先沿用上一轮同 topic 的 worker lens / profile，并复用该 worker runtime continuity。

### User Story 3 - Butler 发给 Worker 的应是任务目标而不是原话转发 (Priority: P1)

作为用户，我希望内部委派看起来像“主助手在下任务”，而不是“把我的原话简单转发给 Research Worker”。

**Independent Test**: 触发 retained delegation，检查 A2A `TASK` payload，验证其中存在 objective/context/tool contract/return contract，而不是只包含 raw user_text。

## Functional Requirements

- **FR-001**: `orchestrator` MUST 在 single-loop eligibility 判断前，把 `requested_worker_profile_id` 规范化为 canonical worker lens，并在可解析时回写 `requested_worker_type`。
- **FR-002**: Butler 默认 worker profile MUST 把 `filesystem` 与 `terminal` 纳入可挂载基础工具组，且工具调用仍必须经过 ToolBroker / Policy / Audit。
- **FR-003**: 系统 MUST 提供正式 builtin tools：至少包含 `filesystem.list_dir`、`filesystem.read_text`、`terminal.exec`。
- **FR-004**: `terminal.exec` MUST 受 workspace root / cwd 边界约束，并保留不可逆动作审批链。
- **FR-005**: retained delegation 路径 MUST 使用 Butler-composed handoff objective，而不是直接把 raw user text 作为 Worker 输入。
- **FR-006**: ButlerDecision / ButlerLoopPlan contract MUST 支持表达 sticky worker lane 的意图，至少包含 continuity topic、preferred worker profile 或 equivalent metadata。
- **FR-007**: 系统 MUST 在同 task/thread/session 的 follow-up 中优先复用同题材 worker lane，前提是不违反当前权限、工具和 project/workspace 边界。
- **FR-008**: `behavior/system/AGENTS.md`、`TOOLS.md`、`HEARTBEAT.md` 或默认等效模板 MUST 显式规定：
  - Butler 优先直解 bounded tasks
  - 长期复杂任务优先进入 specialist worker lane
  - 委派时 Butler 需要改写 objective/context contract
- **FR-009**: 本 Feature MUST 提供后端测试，覆盖 single-loop canonicalization、builtin tools、sticky worker lane 与 composed handoff payload。

## Success Criteria

- **SC-001**: 显式 `requested_worker_profile_id=singleton:research/dev/ops` 的请求会稳定进入正确的 single-loop lens，而不再意外跌回 retained delegation。
- **SC-002**: Butler 可以直接使用基础 Web / Filesystem / Terminal 工具完成 bounded task，并留下真实工具事件。
- **SC-003**: retained delegation 的 A2A `TASK` payload 不再只是 raw user query，而是 Butler handoff contract。
- **SC-004**: 对连续同题材复杂请求，第二轮能优先沿用上次 specialist worker lane。
