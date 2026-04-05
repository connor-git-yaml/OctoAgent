---
feature_id: "039"
title: "Supervisor Worker Governance + Internal A2A Dispatch"
milestone: "M4"
status: "implemented"
created: "2026-03-10"
updated: "2026-03-13"
research_mode: "codebase-scan"
blueprint_ref: "docs/blueprint.md M4；Feature 018（A2A-Lite）、Feature 030（Delegation Plane）、Feature 032（Built-in Tool Suite）、Feature 037（Runtime Context Hardening）；OpenClaw control/runtime orchestration；Agent Zero supervisor + worker 分层"
predecessor: "Feature 018（A2A-Lite Envelope）、Feature 030（Delegation Plane / Work graph）、Feature 032（Built-in Tool Suite）、Feature 037（Runtime Context Hardening）"
parallel_dependency: "Feature 035 / 036 负责产品入口与 setup 治理；039 负责把主 Agent / Work / Worker/Subagent 的运行分层和用户审批主链补成真实能力。"
---

# Feature Specification: Supervisor Worker Governance + Internal A2A Dispatch

**Feature Branch**: `codex/feat-039-supervisor-worker-governance`  
**Created**: 2026-03-10  
**Updated**: 2026-03-13  
**Status**: Partially Implemented  
**Input**: 复核 OctoAgent 当前是否已经形成内置的主 Agent / Work / Subagent（或长任务 / Graph）三层结构；主 Agent 是否通过 A2A 把任务传给 Work；是否具备 review/apply worker 拆分、合并、重划分与权限分配能力。如果缺失，则补成正式 Feature 并直接落地。  

## Problem Statement

Feature 030/032/037 已经把 `project -> session -> work -> child work`、Delegation Plane、graph/subagent runtime 和 runtime lineage 主链打通，但针对“三层运行结构”的关键约束仍有三个缺口：

1. **主 Agent 仍然持有过多具体执行工具**  
   `general` worker 虽然承担 orchestrator/supervisor 角色，但默认 tool groups 仍接近通用 worker，导致“主 Agent 不自己干活，只负责审任务和派工”没有变成真实系统边界。

2. **Worker 拆分/重划分只有手工 split/merge，没有正式 review/apply 语义**  
   control plane 可以 `work.split / work.merge`，但缺一个可审查、可赋权、需用户确认后才能执行的 worker planning 层。这样用户看不到“为什么要拆成这些 worker、每个 worker 拿到什么权限”。

3. **A2A-Lite 合同存在，但 live dispatch 只完成了 envelope 归一化，没有形成 message-native 主链**  
   Feature 018 已定义 `TaskMessage` / `DispatchEnvelope` 映射，但 live orchestration 仍主要直接在 `DispatchEnvelope` 上运转；当前实现更像“kernel 归一化后直接调用 worker adapter”，不是 Butler 与 Worker 之间的真实 A2A 往返。

4. **缺少 durable 的 `ButlerSession -> A2AConversation -> WorkerSession` 对象链**  
   运行时虽然有 work / child work / dispatch lineage，但还没有一等的 `A2AConversation`、`A2AMessage` 与 `WorkerSession` 持久对象。结果是系统能做“派工”，却还不能做 blueprint 要求的“可审计 Agent-to-Agent 沟通”。

因此，039 要解决的不是“再加一种 worker 类型”，而是：

> 把主 Agent 收口成 supervisor，把 worker 规划和权限赋予变成可审查、可批准的能力，并让 live dispatch 演进为 Butler 主导的 message-native A2A 主链，从而让主 Agent / Work / Worker(Subagent/Graph) 三层结构在系统里成立。

## Product Goal

交付一条真实可运行的三层主链：

- 主 Agent 默认只持有 `project / session / supervision` 工具面，不再默认持有具体执行工具族
- 系统新增 `workers.review` built-in tool，以及 `worker.review / worker.apply` control-plane actions
- worker plan 会显式产出：
  - proposal kind（split / repartition / merge）
  - assignment 列表
  - 每个 assignment 的 `worker_type / target_kind / tool_profile`
  - warnings / merge candidates
- `worker.apply` 必须在用户显式执行后才真正创建 child tasks / child works
- live dispatch 必须收敛为 `ButlerSession -> A2AConversation -> WorkerSession` 主链，保留 runtime context / work lineage / A2A message 审计

## Scope Alignment

### In Scope

- `CapabilityPackService`
  - `general` worker profile 收口为 supervisor tool groups
  - 新增 `workers.review`
  - 新增 worker plan review/apply helper
- `ControlPlaneService`
  - 新增 `worker.review`
  - 新增 `worker.apply`
  - work runtime summary 补 `requested_tool_profile`
- `DelegationPlaneService`
  - work metadata 落 `requested_tool_profile`
- `TaskRunner`
  - child task metadata 的 `tool_profile` 进入 orchestrator dispatch
- `OrchestratorService`
  - live dispatch 走 message-native A2A 主链
  - 保留 runtime context / work lineage / a2a refs
- `A2AConversation` / `A2AMessage`
  - durable task/update/result events
  - Butler / Worker session 绑定
- backend tests / spec / verification

### Out of Scope

- 重写整个 orchestrator / delegation plane
- 移除所有 legacy direct route fallback
- 新增多租户 RBAC 或独立 worker registry UI
- 新建第二套 A2A runtime store

## Functional Requirements

- **FR-001**: `general` worker profile MUST 默认只暴露 supervisor 级工具组，不再默认持有 network/browser/memory/mcp 等具体执行面。
- **FR-002**: 系统 MUST 提供 `workers.review` built-in tool，供主 Agent / supervisor 生成 worker split/repartition/merge 建议。
- **FR-003**: `workers.review` MUST 为每个 assignment 产出 `worker_type / target_kind / tool_profile / reason / title`。
- **FR-004**: control plane MUST 提供 `worker.review` 与 `worker.apply`，并要求用户显式执行 apply 才真正派生 child tasks。
- **FR-005**: `worker.apply` MUST 把 `tool_profile` 落入 child task metadata，并最终反映到 child work runtime truth。
- **FR-006**: `DelegationPlane` / control plane work projection MUST 暴露 `requested_tool_profile`，让用户看见每个 worker 被授予的权限级别。
- **FR-007**: orchestrator live dispatch MUST 演进为真实 `ButlerSession -> A2AConversation -> WorkerSession` 主链，而不是只完成 `DispatchEnvelope -> A2A task message -> DispatchEnvelope` 的适配层 roundtrip。
- **FR-008**: A2A 主链 MUST 保留 `runtime_context` 与 `work_id/session_id/project_id/workspace_id` lineage，不得因 message dispatch 丢失。
- **FR-009**: Feature 039 MUST 提供回归测试，覆盖 tool catalog、worker review/apply、A2A 主链三条路径。
- **FR-010**: 系统 MUST 定义 durable 的 `A2AConversation` 与 `A2AMessage` 对象，并至少支持 `TASK / UPDATE / RESULT / ERROR` 消息类型与审计查询。
- **FR-011**: `WorkerSession` MUST 成为一等持久对象，不得继续停留在 runtime-only loop state；Butler 与 Worker 的通信历史必须可回放、可压缩、可恢复。
- **FR-012**: 当前用户表面上，Butler MUST 继续是唯一对用户负责的 speaker；Worker 的输出默认只能先回 Butler，再由 Butler 对外综合回复。

## Success Criteria

- **SC-001**: 主 Agent 默认 tool surface 只剩 supervisor 所需工具组。
- **SC-002**: 用户可以在 control plane 上先 review worker plan，再 apply 并看到 child works 带着明确 tool profile 生成。
- **SC-003**: work projection 能直接显示 `requested_tool_profile`，无需再从 metadata 逆向猜测。
- **SC-004**: orchestrator 的 live dispatch 测试能证明 `ButlerSession -> A2AConversation -> WorkerSession -> RESULT -> ButlerReply` 成立，并保留 runtime context。
- **SC-005**: 039 回归测试全部通过，且不破坏既有 032/037 控制平面能力。
- **SC-006**: event chain / control plane 可以直接回放至少一条真实 A2A 会话，不再只有 `WORKER_DISPATCHED` 之类的间接事实。

## Residual Risks

- direct chat/message route 在极端 fallback 场景下仍可能绕过完整 supervisor 语义；本 Feature 先收口默认主链，不在本轮删除所有兼容路径。
- `workers.review` 当前是规则化计划器，不是基于独立规划模型；后续可继续增强建议质量，但不影响治理闭环已成立。
