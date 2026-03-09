---
feature_id: "032"
title: "OpenClaw Built-in Tool Suite + Live Graph/Subagent Runtime"
milestone: "M4"
status: "Implemented"
created: "2026-03-09"
updated: "2026-03-09"
research_mode: "full"
blueprint_ref: "docs/m4-feature-split.md Feature 032；docs/blueprint.md M4；Feature 025-B / 026 / 030；OpenClaw tools；Pydantic AI graph / multi-agent docs"
predecessor: "Feature 025-B / 026 / 030"
---

# Feature Specification: OpenClaw Built-in Tool Suite + Live Graph/Subagent Runtime

**Feature Branch**: `032-openclaw-builtin-tool-suite`  
**Created**: 2026-03-09  
**Updated**: 2026-03-09  
**Status**: Implemented  
**Input**: 设计一个新的 Feature 032，对齐 OpenClaw 内置工具能力，并把 Pydantic AI Graph、Worker Spawn Subagent、主 Agent 创建/合并/拆分 Worker 等能力纳入范围；要求避免“只有代码和测试，但用户根本用不上”的伪实现。  
**调研基础**: `research/product-research.md`、`research/tech-research.md`、`research/research-synthesis.md`、`research/online-research.md`

## Problem Statement

当前 master 虽然已经交付：

- 025-B：project/workspace/secret/wizard 主路径
- 026：control plane canonical resources / actions / Web 控制台
- 030：capability pack、ToolIndex、delegation plane、skill pipeline 的第一阶段基线

但“内置工具能力”对最终用户仍然明显不足：

1. 当前 capability pack 真正注册的 built-in tools 只有 `project.inspect`、`task.inspect`、`artifact.list`、`runtime.inspect`、`work.inspect` 五个最小工具。
2. `graph_agent`、`subagent`、`acp_runtime` 已经作为 `target_kind` 出现在 delegation 路径里，但现有代码更接近“语义标签 + metadata”，而不是用户可达、可观察、可恢复的独立运行面。
3. `SkillPipelineEngine` 已经提供 deterministic 节点执行、checkpoint、pause/replay，但当前实现并未真正消费 `pydantic_graph`，也没有把“Graph”作为真实后端暴露给用户。
4. `merge_work()` 已存在，但 `split_work` 语义与用户入口缺失，导致“主 Agent 创建、合并、拆分 Worker”的能力没有形成闭环。
5. control plane 目前更适合看 route reason / tool hit / pipeline status，还不能作为“内置工具与 live runtime truth” 的正式产品面。

结果是系统已经有一层“能力框架”，但距离 OpenClaw 那种用户真正可使用的 built-in tool suite 仍有明显差距。

## Product Goal

把 OctoAgent 从“有 capability framework”推进到“有真实可用的 built-in tool suite + live runtime truth”：

- 对齐 OpenClaw 的高价值 built-in tool families
- 为每个 built-in tool 建立正式 catalog、availability、install hint、degraded reason
- 把 `pydantic_graph` bridge 落成真正可调用、可观测、可恢复的 graph backend
- 把 subagent spawn 落成真正的 child runtime/session，而不是仅有 `target_kind=subagent`
- 把主 Agent 的 child work `create / merge / split / cancel / inspect` 收敛成正式 operator 面
- 让 CLI / Web / Agent runtime 至少有一条真实入口可以使用上述 built-in tools 与 runtime 能力
- 保证所有路径继续走 ToolBroker / Policy / Event / Audit / control plane

## Scope Alignment

### In Scope

- `BuiltinToolCatalog`、`BuiltinToolFamily`、`BuiltinToolAvailability`、`BuiltinToolInstallHint`
- `sessions.* / subagents.* / session.status / agents.list`
- `web.fetch / web.search / browser.*`
- `gateway.* / cron.* / nodes.*`
- `pdf.* / image.* / tts.* / canvas.*`
- `memory.read / memory.search / memory.citations` 等只读治理入口
- `pydantic_graph` live bridge 与 graph runtime resource / action
- subagent spawn 的真实 child runtime / session / lifecycle
- 主 Agent child work 的 create / merge / split / inspect / cancel 生命周期
- control plane 的 tool / runtime truth 投影
- 单元测试、关键集成测试、必要 e2e

### Out of Scope

- Telegram / Discord / Slack / WhatsApp channel action packs
- remote nodes / companion surfaces / PWA polish
- marketplace / public plugin hub
- 重做 026 control plane shell
- 绕过 ToolBroker / Policy / Memory 治理的快捷执行器
- 把 033 以后的 channel action / remote runtime 范围偷带进来

## User Stories & Testing

### User Story 1 - 我可以真正使用一组丰富的内置工具，而不是只看到少量 inspect/list (Priority: P1)

作为 operator，我希望系统默认自带一组足够实用的 built-in tools，并能清楚知道哪些工具可用、为什么不可用、缺什么依赖。

**Independent Test**: 读取 capability/control-plane 资源，至少能看到工具族、可用性、install hint、degraded reason；随后从主 Agent 或 CLI 真实调用至少一种 web/browser、一种 session/subagent、一种 media/doc 工具。

**Acceptance Scenarios**

1. **Given** 当前运行环境缺少某个依赖，**When** 查询 built-in tool catalog，**Then** 返回 `install_required` 或 `degraded`，并给出安装提示，而不是静默隐藏。
2. **Given** 当前环境满足依赖，**When** 调用 built-in tool，**Then** 工具能从真实入口被执行，并写入 audit/event。
3. **Given** 某个工具族不可用，**When** agent 或 operator 查询状态，**Then** control plane 能显示明确 degraded reason，而不是只有空列表。

### User Story 2 - 主 Agent 可以真正生成和管理 child work / subagent，而不是只有标签 (Priority: P1)

作为 operator，我希望主 Agent 能把一项大工作拆给子 Worker / subagent，并且我能看到 child work 的创建、拆分、合并、取消和完成状态。

**Independent Test**: 对一项 work 执行 `split`，生成 child works；至少一个 child work 通过真实 subagent 或 worker session 执行；最终能够 merge 回父 work，并在 control plane 中可见。

**Acceptance Scenarios**

1. **Given** 主 Agent 需要把一个任务分成多个子任务，**When** 触发 split，**Then** 系统创建 durable child works，并记录 parent/child ownership。
2. **Given** 某个 child work 走 subagent runtime，**When** runtime 启动后执行，**Then** control plane 能看到 child session、运行状态和结果摘要。
3. **Given** child works 均已完成，**When** operator 或主 Agent 触发 merge，**Then** 父 work 更新为 merged/ready 状态，并保留 merge summary。

### User Story 3 - Graph 能力必须是可运行的真实后端，而不是“graph_agent”标签 (Priority: P1)

作为 operator，我希望 Graph 是真实可运行的执行模式，支持节点状态、恢复和可视化，而不是仅在 metadata 里写上 `graph_agent`。

**Independent Test**: 创建一条 graph-backed work，使用真实 `pydantic_graph` 或等价 graph adapter 跑通一个节点序列；能够在 control plane 中查看 current node、state snapshot、pause/resume/replay。

**Acceptance Scenarios**

1. **Given** 某个 worker type 路由到 graph runtime，**When** work 启动，**Then** 系统创建 graph run，并把节点状态落到 durable store。
2. **Given** graph run 进入 WAITING_INPUT 或 WAITING_APPROVAL，**When** operator 恢复，**Then** run 可以从上一个 checkpoint 继续。
3. **Given** graph backend 不可用，**When** 系统尝试执行 graph work，**Then** 返回明确 degraded / unavailable，而不是假装成功或静默回落。

### User Story 4 - Control Plane 是 built-in tools 与 runtime truth 的统一入口 (Priority: P2)

作为 operator，我希望不用切换新控制台，就能在现有 control plane 中查看 built-in tool families、availability、subagent/runtime truth、graph runs、child work 关系。

**Independent Test**: 打开 control plane，看到 tool catalog、runtime truth、graph run、child work graph 的 canonical resources 与 actions；Web 入口能直接调起至少一个 built-in tool action。

## Edge Cases

- 工具 schema 已注册，但缺少任何用户可达入口时，必须判定为“未交付”而不是“可用”。
- `graph_agent` 目标存在但 `pydantic_graph` bridge 未启用时，必须 fail-closed 并显式暴露 degraded reason。
- subagent spawn 失败后，child work 不能停留在假 RUNNING；必须进入 FAILED / RETRYABLE 并带错误摘要。
- child work merge 时，若还有未完成子 work，父 work 不能被过早标记为 merged。
- split 后若 operator 取消父 work，子 work 与 child runtime 的取消语义必须明确且一致。
- browser/web/media 类工具缺依赖或网络不可用时，系统必须退化为不可用或 install_required，而不是返回空成功。

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 定义并实现 `BuiltinToolCatalog`，正式建模 built-in tool families、availability、install hint、degraded reason 与真实入口绑定关系。
- **FR-002**: Feature 032 至少 MUST 交付以下 tool families 中的高价值正式工具：`sessions/subagents`、`web/browser`、`gateway/cron/nodes`、`pdf/image/tts/canvas`、`memory(read-only)`。
- **FR-003**: 每个 built-in tool MUST 通过至少一种真实入口可达：主 Agent / Worker、CLI、Web control plane、Telegram 中的一种或多种；没有真实入口的工具 MUST NOT 标记为 shipped。
- **FR-004**: built-in tools 的执行 MUST 继续走 ToolBroker -> Policy -> Tool Handler -> Event/Audit，不能新增旁路执行器。
- **FR-005**: 系统 MUST 为 built-in tools 提供 `available / degraded / unavailable / install_required` 等正式状态，并在 control plane 中可见。
- **FR-006**: 系统 MUST 提供 `pydantic_graph` 或等价 graph backend 的 live bridge；仅有 `graph_agent` target kind 或 runtime label 不算交付 Graph 能力。
- **FR-007**: graph runtime MUST 支持 durable run、current node、state snapshot、pause/resume、checkpoint/replay，并接入 control plane。
- **FR-008**: graph runtime 不可用时 MUST fail-closed，并返回显式 degraded / unavailable reason，不得伪装为普通 worker 成功执行。
- **FR-009**: 系统 MUST 提供真实的 subagent spawn runtime / child session / lifecycle；仅创建 work metadata 而没有 child runtime 不算交付 subagent 能力。
- **FR-010**: subagent runtime MUST 写入 durable session / work state，并允许 operator 查看运行、暂停、失败、完成等生命周期。
- **FR-011**: 系统 MUST 定义并实现主 Agent child work 的 `create / split / merge / cancel / inspect` 正式语义；若 `split` 缺失，则不得宣称具备 worker split/merge 能力。
- **FR-012**: child works MUST 持久化 parent/child ownership、assigned runtime、selected tools、route reason、status、merge summary 等事实。
- **FR-013**: split / merge / subagent / graph runtime 的所有 operator 动作 MUST 接入 026 control plane 的 canonical resources / actions / events，不得另造平行控制台。
- **FR-014**: built-in tool availability 与 runtime truth MUST 兼容 025-B project/workspace/secret 作用域，不得引入新的 project/scope 真相源。
- **FR-015**: memory 相关 built-in tools MUST 仅提供只读治理入口，并保留 citations / evidence refs；不得旁路 020/027/028 的权威写入链。
- **FR-016**: 系统 MUST 补齐 tool-level 单测、关键集成测试与必要 e2e；测试不仅验证 schema/registry，还必须验证真实入口到执行结果的贯通。
- **FR-017**: Feature 032 MUST 明确与后续 channel action packs、remote nodes、companion surfaces 的边界，避免 scope 膨胀。

### Key Entities

- `BuiltinToolCatalog`
- `BuiltinToolFamily`
- `BuiltinToolSpec`
- `BuiltinToolAvailability`
- `BuiltinToolInstallHint`
- `GraphRuntimeDescriptor`
- `GraphRunProjection`
- `SubagentSession`
- `ChildWorkPlan`
- `ChildWorkMerge`
- `RuntimeTruthSnapshot`

## Success Criteria

- **SC-001**: built-in tools 从当前最小 inspect/list 集提升到至少 15 个以上的正式工具，并按 family / availability 正式编目。
- **SC-002**: 至少一个 graph-backed work 能通过真实 graph backend 执行，并在 control plane 中展示 current node / checkpoint / replay。
- **SC-003**: 至少一个 subagent child work 能通过真实 child runtime/session 执行，并在 control plane 中展示生命周期。
- **SC-004**: 主 Agent 的 child work `split / merge / cancel / inspect` 形成闭环，且 parent/child ownership 可恢复。
- **SC-005**: 缺依赖、缺环境变量、缺运行时、缺网络时，tool/runtime 状态会显式 degraded / unavailable，而不是静默假成功。
- **SC-006**: 032 的关键测试覆盖“真实入口 -> 执行结果 -> control plane 可见”的贯通链，不再只停留在 registry/schema 级测试。

## Clarifications

### Session 2026-03-09

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 是否把 OpenClaw 的所有 channel action packs 一起带入 032？ | 否 | 容易 scope 爆炸，更适合后续独立 Feature |
| 2 | Graph 能力是否允许继续沿用自定义 pipeline 并只改名为 graph？ | 否 | 用户明确要求不能假装实现，必须有真实 Graph 后端 |
| 3 | subagent 能力是否允许只有 metadata / route reason？ | 否 | 用户明确要求必须真实可用 |
| 4 | 主 Agent 是否必须具备 formal split/merge worker 语义？ | 是 | 用户明确要求考虑 create/merge/split Worker 能力 |
| 5 | 032 是否可以重做 026 控制台框架？ | 否 | 必须复用现有 control plane |

## Scope Boundaries

### In Scope

- built-in tool suite parity
- live graph runtime
- live subagent runtime
- child work split/merge lifecycle
- control plane runtime truth
- tests / verification / docs sync

### Out of Scope

- channel action packs
- remote nodes
- companion surfaces
- plugin marketplace
- memory write shortcuts

## Risks & Design Notes

- 如果 032 只补 tool registry 而不补用户可达入口，最终仍会落回“看起来有工具，实际上用不了”的旧问题。
- 如果继续用 `graph_agent` label 代替真实 graph backend，会直接违反本轮“非伪实现门禁”。
- 如果 split/merge 只有 API 没有 durable child work model，control plane 将无法解释 main-agent ownership。
- 如果 subagent runtime 不能写 durable session/work state，重启后 child work 会丢失真实生命周期。
