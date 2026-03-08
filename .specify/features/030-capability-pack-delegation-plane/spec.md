---
feature_id: "030"
title: "Built-in Capability Pack + Delegation Plane + Skill Pipeline"
milestone: "M3"
status: "Implementing"
created: "2026-03-08"
updated: "2026-03-08"
research_mode: "full"
blueprint_ref: "docs/m3-feature-split.md Feature 030；docs/blueprint.md ToolIndex / Skill Pipeline / Worker / A2A / Policy / ToolBroker；Feature 025-B / Feature 026"
predecessor: "Feature 004 / 005 / 006 / 018 / 019 / 025-B / 026"
---

# Feature Specification: Built-in Capability Pack + Delegation Plane + Skill Pipeline

**Feature Branch**: `030-capability-pack-delegation-plane`  
**Created**: 2026-03-08  
**Updated**: 2026-03-08  
**Status**: Implementing  
**Input**: 落实 M3 Feature 030：Built-in Capability Pack + Delegation Plane + Skill Pipeline。  
**调研基础**: `research/product-research.md`、`research/tech-research.md`、`research/online-research.md`、`research/research-synthesis.md`

## Problem Statement

当前 master 已具备：

- 025-B：project/workspace/secret/wizard 的正式作用域与 CLI 主路径
- 026：control plane canonical resources、actions、events 与正式 Web 控制台
- 004/005/006：ToolBroker + SkillRunner + Policy Engine
- 018/019：A2A-Lite contract + execution console / jobrunner

但系统仍然缺少 M3 真正需要的“可解释增强层”：

1. 工具集仍是静态注册后全量暴露，缺少 ToolIndex 和动态工具注入。
2. skill 仍停留在单循环执行，没有 deterministic pipeline、checkpoint、replay、pause、人工介入和节点重试。
3. Orchestrator 仍以单 Worker 路由为主，缺少 `Work` 这一层正式 delegation unit。
4. control plane 仍看不到 tool hit、pipeline graph、route reason、work ownership、subagent/runtime status。

结果是系统能跑，但在多能力、多 worker、多子流程场景下既不够可解释，也不够可恢复。

## Product Goal

把“能力选择 -> 委派 -> 子流程 -> 运行态 -> 控制台可见性”收敛成同一条增强主链：

- 定义并实现 bundled capability pack
- 实现 ToolIndex（向量检索 + metadata filter + 动态工具注入）
- 实现 Skill Pipeline Engine（checkpoint / replay / pause / HITL / node retry）
- 定义 `Work` 作为主 Agent 的正式 delegation unit
- 实现统一 delegation protocol，覆盖 worker / subagent / ACP-like runtime / graph agent
- 实现 ops / research / dev 多 Worker capability registry 与派发策略
- 把 tool hit、pipeline、route reason、work ownership、runtime status 接入既有 control plane
- 保证所有新增路径继续走 ToolBroker / Policy Engine / manifest / audit event

## Scope Alignment

### In Scope

- bundled skills / bundled tools / worker bootstrap files
- ToolIndex backend / query model / dynamic tool selection / fallback
- Skill Pipeline Engine 及其持久化、checkpoint、replay、pause、resume、retry
- `Work` 领域模型、持久化、create/assign/merge/cancel/timeout/escalation
- delegation protocol 与 runtime adapters
- 多 Worker capability registry 与路由策略
- control-plane resources / actions / events / frontend integration
- 单元测试、集成测试、关键 e2e 与 verification

### Out of Scope

- M4 remote nodes / companion surfaces
- 重做 026 的 control plane 基础框架
- 绕过 ToolBroker / Policy / 审计链的快捷执行路径
- Memory Console / Vault 的详细领域视图
- 全新可视化 workflow editor

## User Stories & Testing

### User Story 1 - 我能看到系统默认自带哪些能力，并知道当前 worker 为什么拿到这些能力 (Priority: P1)

作为 operator，我希望系统有正式的 bundled capability pack 和 ToolIndex 命中结果，这样我能理解 worker 当前暴露的 skills/tools/bootstrap，而不是只能猜。

**Independent Test**: 读取 control-plane capability 资源，能看到 bundled skills、bundled tools、worker bootstrap files、worker type capability map；执行一条 delegation 路径后，能看到 tool hits 与 selected tools。

**Acceptance Scenarios**

1. **Given** 系统启动并完成 capability pack 初始化，**When** 读取 capability resource，**Then** 能看到 bundled skills、bundled tools、worker bootstrap files 与降级基线。
2. **Given** ToolIndex 可用，**When** 对某个 work 进行工具检索，**Then** 返回 top-N hits、score、metadata match、selected tools。
3. **Given** ToolIndex backend 不可用，**When** 仍需执行 work，**Then** 系统显式退化到静态工具集，并在 control plane 标注 degraded。

### User Story 2 - 主 Agent 可以把工作正式委派给不同目标，并且我能解释路由理由 (Priority: P1)

作为 operator，我希望主 Agent 的 delegation 不是黑盒，而是正式的 `Work` 生命周期：谁创建、派给谁、为什么派、失败后怎么升级、能否取消。

**Independent Test**: 创建一个 work，验证其可被 assign 给 `ops/research/dev` 或 graph/subagent runtime，并持久化 `route_reason`、`owner`、`target_kind`、`status`；取消或失败后状态可恢复。

**Acceptance Scenarios**

1. **Given** 主 Agent 收到需要调研的请求，**When** Delegation Plane 评估 worker type，**Then** work 被分配给 `research`，并写入 route reason。
2. **Given** 某个 work 进入超时或失败，**When** operator 触发 escalation 或 retry，**Then** work 生命周期更新并进入统一审计链。
3. **Given** 系统处于单 Worker 模式，**When** 仍发生 delegation，**Then** work 仍被创建，但 route reason 明确为 fallback，而不是假装多 Worker 生效。

### User Story 3 - 我可以把关键子流程当成可恢复的 pipeline 来管理 (Priority: P1)

作为 operator，我希望关键流程不是一段长黑盒逻辑，而是有节点、有 checkpoint、有 replay、有暂停/恢复的 deterministic pipeline。

**Independent Test**: 创建一条 pipeline run，验证节点级 checkpoint、pause/resume、node retry、replay history 与控制台投影全部可用。

**Acceptance Scenarios**

1. **Given** pipeline 执行到需要审批的节点，**When** node 返回 WAITING_APPROVAL，**Then** run 进入暂停态，保留 checkpoint 与待恢复信息。
2. **Given** pipeline 某个节点失败且允许重试，**When** operator 触发 node retry，**Then** 系统只重跑该节点而不是整条流程重建。
3. **Given** 系统重启后，**When** 读取 pipeline run，**Then** 可以从最近 checkpoint replay/恢复，而不是丢失状态。

### User Story 4 - Telegram / Web 对新的 delegation 与 pipeline 控制语义保持一致 (Priority: P1)

作为 operator，我希望 work cancel、pipeline retry/resume、delegation status 这些动作在 Web 和 Telegram 上使用同一 `action_id` 语义。

**Independent Test**: 对同一 work/pipeline 动作，验证 Web 和 Telegram alias 最终落到同一 action executor 和统一结果码。

### User Story 5 - Control Plane 能展示 capability/work/pipeline/runtime 运行面 (Priority: P2)

作为 operator，我希望现有 control plane 继续作为统一入口，但现在还能看到 tool hit、pipeline graph、route reason、work ownership、subagent/runtime status。

**Independent Test**: 打开 control plane snapshot，验证新增 resource/projection 已接入 dashboard / sessions / diagnostics / delegation 面板。

## Edge Cases

- ToolIndex 命中为空时，如何回退到静态工具集并保留可解释性？
- work 目标是 `subagent` 或 `ACP-like runtime` 但当前实例只支持本地 worker 时，如何显式 degrade？
- pipeline 在 WAITING_INPUT/WAITING_APPROVAL 时被取消，如何保证 run status 与 work status 同步？
- pipeline node retry 时，如何避免重复副作用？
- 多 Worker registry 不完整或指定 worker type 不可用时，如何回退且不丢失 route_reason？
- 同一 task 下存在 parent work 和 child works 时，control plane 如何表达 ownership / merge 状态？

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 定义并实现 `BundledCapabilityPack`，至少包含 bundled skills、bundled tools、worker bootstrap files 与静态 fallback toolset。
- **FR-002**: 系统 MUST 实现 `ToolIndex`，支持向量检索、metadata filter、top-N 命中结果与 selected toolset 输出。
- **FR-003**: ToolIndex 命中与动态工具注入 MUST 继续消费 ToolBroker / manifest metadata；实际工具执行 MUST 继续走 ToolBroker + Policy Engine，不得旁路执行。
- **FR-004**: 系统 MUST 提供 ToolIndex backend 的显式降级路径；当增强 backend 不可用时，MUST 回退到本地静态工具集，并在 control plane 标注 degraded。
- **FR-005**: 系统 MUST 实现 `SkillPipelineEngine`，支持节点输入/输出 contract、checkpoint、replay、pause、resume、node retry。
- **FR-006**: Skill Pipeline MUST 支持 WAITING_APPROVAL 与 WAITING_INPUT 这两类 HITL pause 语义。
- **FR-007**: pipeline 的节点执行与迁移 MUST 写入事件链，并允许控制台读取当前节点、历史 checkpoint 和 replay frame。
- **FR-008**: 系统 MUST 定义 `Work` 作为主 Agent 的正式 delegation unit，并提供 create / assign / merge / cancel / timeout / escalation 语义。
- **FR-009**: `Work` MUST 持久化到 durable store，并保存 `parent_work_id`、`owner`、`target_kind`、`route_reason`、`selected_worker_type`、`selected_tools`、`project_id/workspace_id` 等最小事实字段。
- **FR-010**: 系统 MUST 定义统一 delegation protocol，使 worker / subagent / ACP-like runtime / graph agent 共享同一 canonical delegation envelope 与 result semantics。
- **FR-011**: 系统 MUST 提供 `ops`、`research`、`dev` 至少三种 worker type 的 capability registry，并支持 route reason、worker availability 与 fallback。
- **FR-012**: 当多 Worker 路由不可用时，系统 MUST 回退到单 Worker / 静态工具集路径，并将 `route_reason` 明确标记为 fallback，而不是静默退化。
- **FR-013**: Worker bootstrap 文件 MUST 兼容 025-B project/workspace 选择态；不同 worker type MUST 可基于当前 project/workspace 生成对应 bootstrap context。
- **FR-014**: Delegation Plane 与 Skill Pipeline 的所有 side-effect path MUST 保持 `ToolBroker -> Policy Engine -> Tool Handler -> Event/Audit` 的既有治理链。
- **FR-015**: control plane MUST 增量发布 capability pack、delegation plane、skill pipeline 资源，或等价扩展既有 snapshot/per-resource 路径，不得重做 026 基础框架。
- **FR-016**: control plane MUST 展示 tool hit、pipeline graph / current node、route reason、work ownership、runtime / target status。
- **FR-017**: Telegram / Web MUST 共享至少 work cancel、work escalation/retry、pipeline resume、pipeline node retry、delegation status refresh 这些 action semantics。
- **FR-018**: Feature 030 MUST 兼容 025-B 的 project/workspace/secret/wizard 基线，不得引入新的 project/scope 真相源。
- **FR-019**: Feature 030 MUST NOT 引入 M4 remote nodes / companion surfaces。
- **FR-020**: Feature 030 MUST 提供单元测试、关键集成测试、必要 e2e，并明确与 Feature 031 验收矩阵的衔接。

### Key Entities

- `BundledCapabilityPack`
- `BundledToolDefinition`
- `BundledSkillDefinition`
- `WorkerCapabilityProfile`
- `WorkerBootstrapFile`
- `ToolIndexQuery`
- `ToolIndexHit`
- `DynamicToolSelection`
- `Work`
- `DelegationEnvelope`
- `DelegationTarget`
- `DelegationResult`
- `SkillPipelineDefinition`
- `SkillPipelineRun`
- `PipelineCheckpoint`
- `PipelineReplayFrame`

## Success Criteria

- **SC-001**: capability pack、ToolIndex、Work、pipeline 四类核心对象均有正式模型与 producer。
- **SC-002**: 至少三种 worker type（`ops/research/dev`）具备 capability registry 与可解释 route reason。
- **SC-003**: ToolIndex 支持 top-N 查询、metadata filter 和 degraded fallback，且命中结果能进入 control plane。
- **SC-004**: pipeline 支持 checkpoint / replay / WAITING_INPUT / WAITING_APPROVAL / node retry。
- **SC-005**: work 支持 create / assign / cancel / timeout / merge / escalation，并在重启后仍可恢复状态。
- **SC-006**: control plane 能展示 route reason、tool hits、work ownership、pipeline/runtime status。
- **SC-007**: Telegram / Web 对新增 delegation/pipeline 控制动作共享相同 `action_id` 语义。
- **SC-008**: Feature 031 可以直接消费 030 提供的 capability/work/pipeline/runtime 状态面，编写用户 ready 验收矩阵。

## Clarifications

### Session 2026-03-08

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 是否允许绕过 ToolBroker / Policy Engine 直接做动态工具执行？ | 否 | 用户明确要求不能绕过治理面 |
| 2 | 是否需要把 M4 remote nodes / companion surfaces 一起带进来？ | 否 | 用户明确禁止偷带 M4 能力 |
| 3 | 是否可以重做 026 control plane 基础框架？ | 否 | 030 只能增量扩展 |
| 4 | subagent / ACP-like runtime 是否必须先做远端分布式版本？ | 否 | 本阶段先统一协议与本地 adapter，远端节点留给 M4 |
| 5 | 是否必须保留单 Worker / 静态工具集降级路径？ | 是 | 这是用户明确要求和 Constitution 要求 |

## Scope Boundaries

### In Scope

- capability pack
- ToolIndex
- skill pipeline
- work / delegation plane
- multi-worker capability registry
- control-plane 增量扩展
- 关键 action semantics
- tests / verification / docs sync

### Out of Scope

- remote nodes
- companion surfaces
- 新 control-plane shell
- Memory/Vault 详细视图
- 全图形 workflow designer

## Risks & Design Notes

- 若 ToolIndex 只返回字符串列表而不保留 score/filter 命中信息，control plane 无法解释动态注入结果。
- 若 Work 不落盘，只依赖 task event 推导，会导致 ownership/merge/timeout 语义难以恢复。
- 若 pipeline retry 不隔离副作用 cursor，会造成重复 side effects。
- 若 Telegram/Web 为 delegation/pipeline 动作继续各写一套私有解析逻辑，会直接破坏 026 已建立的统一 action registry。
