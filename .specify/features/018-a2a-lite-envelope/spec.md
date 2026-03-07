---
feature_id: "018"
title: "A2A-Lite Envelope + A2AStateMapper"
milestone: "M2"
status: "Draft"
created: "2026-03-07"
research_mode: "tech-only"
blueprint_ref: "docs/blueprint.md §10.2 / §10.2.1 / §10.2.2 / §11.9 / §14"
predecessor: "Feature 008（Orchestrator 控制平面契约）与 Feature 009（Worker Runtime 基线）已交付"
parallel_dependency: "Feature 019（JobRunner）与 Feature 023（Worker ↔ SubAgent 接线）将直接消费本特性的 contract fixture"
---

# Feature Specification: A2A-Lite Envelope + A2AStateMapper

**Feature Branch**: `codex/feat-018-a2a-lite-envelope`
**Created**: 2026-03-07
**Status**: Draft
**Input**: 基于 `docs/m2-feature-split.md` 的 Feature 018，冻结 OctoAgent 内部 Agent 通信协议，为后续多 Worker / SubAgent / JobRunner 对接提供稳定 contract。
**调研基础**: `research/tech-research.md`

---

## Problem Statement

当前代码库已经有：

- Feature 008 的 `DispatchEnvelope` / `WorkerResult`
- Feature 009 的 Worker Runtime 骨架
- `core` 中的 `TaskStatus` / `Artifact` / `NormalizedMessage`

但还缺少一个正式、可复用、可测试、可版本化的 **A2A-Lite contract package**。结果是：

1. Kernel ↔ Worker 的协议字段仍然分散在不同模型中，没有统一 wire contract。
2. 内部状态虽然已经是 A2A 超集，但没有一个显式的 `A2AStateMapper` 来冻结映射语义。
3. `Artifact` 具备多 part 结构，却没有对外 A2A 兼容视图和 mapper。
4. 后续 019/023 如果继续各自拼消息结构，很容易出现字段漂移、版本漂移和 fixture 不一致。

Feature 018 的目标不是立刻把消息真实投递到网络中，而是先冻结“消息长什么样、如何映射状态和 artifact、如何做协议级守卫”，让后续能力可以在同一 contract 上迭代。

---

## User Scenarios & Testing

### User Story 1 - 编排层能构造统一 A2A-Lite 消息 (Priority: P1)

作为后续实现 Kernel / Worker / JobRunner 的开发者，我希望有一个统一的 `A2AMessage` contract，可以一致地表示 `TASK/UPDATE/CANCEL/RESULT/ERROR/HEARTBEAT` 六类消息，这样不同模块不会各自定义不兼容的 envelope。

**Why this priority**: 没有统一 envelope，Feature 019/023 很容易继续扩散临时字段，后续再收敛会成本更高。

**Independent Test**: 直接实例化六类 message fixture，验证它们都能通过 schema 校验、序列化为稳定字段集合，并保持 `schema_version` / `idempotency_key` / `trace` / `hop_count` 语义完整。

**Acceptance Scenarios**:

1. **Given** 编排层需要向 worker 派发任务，**When** 使用 protocol package 构造 `TASK` 消息，**Then** 消息必须包含版本、trace、幂等键、context 与 payload 等固定字段，而不是依赖临时 dict。

2. **Given** worker 需要回传结果或错误，**When** 构造 `RESULT` 或 `ERROR` 消息，**Then** 两者都必须复用同一 envelope，而不是独立定义不同的顶层结构。

3. **Given** 后续模块需要转发同一消息，**When** 更新目的地并增加 hop，**Then** envelope 必须能够表达 hop 计数，并阻止超过上限的继续传播。

---

### User Story 2 - 运行时能稳定映射状态与 Artifact (Priority: P1)

作为后续实现 A2A 互操作的开发者，我希望能把内部 `TaskStatus` 和 `Artifact` 映射成标准 A2A 语义，同时保留 OctoAgent 的内部治理信息，这样系统既兼容外部协议，又不会丢掉内部状态精度。

**Why this priority**: 这是 blueprint 对 A2A 兼容的核心承诺。如果状态和 artifact 映射不稳定，Worker ↔ SubAgent 的后续对接会失去基础。

**Independent Test**: 针对 `CREATED/WAITING_APPROVAL/PAUSED/REJECTED` 等关键状态，以及 `text/file/json/image` artifact parts，执行双向映射测试并验证终态保持一一对应、压缩语义通过 metadata 保留。

**Acceptance Scenarios**:

1. **Given** 内部任务处于 `WAITING_APPROVAL`，**When** 对外映射为 A2A state，**Then** 结果必须是 `input-required`，并保留 `internal_status=WAITING_APPROVAL` 的扩展元数据。

2. **Given** 内部 artifact 使用 text/file/json/image parts，**When** 映射为 A2A artifact，**Then** part 类型、内容位置和治理字段必须以一致规则转换，不得出现某类 part 缺失映射路径。

3. **Given** A2A `completed/canceled/failed/rejected` 等终态回流到系统内部，**When** 反向映射为 `TaskStatus`，**Then** 终态必须保持一一对应，不允许把 `rejected` 混同为 `failed`。

---

### User Story 3 - 集成方能依赖固定 fixture 与协议守卫 (Priority: P1)

作为后续实现 019/023 的开发者，我希望 protocol package 自带 fixture 和协议守卫，这样我可以直接复用一套稳定样例和校验逻辑，而不是在各自测试中重新发明消息结构和 replay 规则。

**Why this priority**: 018 的价值不只是“有模型”，而是“后续 feature 可以直接消费”。没有 fixture 和 guard，contract 仍然会在下游被各自解释。

**Independent Test**: 导入 fixture builders 生成六类消息，配合 `DeliveryLedger` 验证重复消息、重放消息、版本不兼容和 hop 超限的行为一致。

**Acceptance Scenarios**:

1. **Given** 下游测试想复用标准 task/result message，**When** 导入 protocol fixtures，**Then** 应能直接获得结构化 fixture，而不需要自己拼 payload。

2. **Given** 相同 `(message_id, idempotency_key)` 被重复投递，**When** 经过 delivery guard，**Then** 系统必须识别为 duplicate，而不是当成新的消息。

3. **Given** 不同 `message_id` 复用了同一 `idempotency_key`，**When** 经过 delivery guard，**Then** 系统必须识别为 replay 并阻止继续处理。

---

### Edge Cases

- 当消息 `schema_version` 不是当前支持集合时，系统如何 fail fast 而不是静默接受？
- 当 `hop_count` 已经大于 `max_hops` 时，消息如何在协议层被拒绝，而不是进入业务逻辑后才失败？
- 当 `WAITING_APPROVAL` / `PAUSED` / `CREATED` 被映射为标准 A2A 状态后，外部消费者如何获知原始内部语义？
- 当 artifact 的 file/image part 只有 `storage_ref` 没有显式 `uri` 时，mapper 是否能稳定补全？
- 当 JSON part 来自结构化对象而不是字符串时，映射是否仍可保持可预测、可序列化？

---

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 提供独立的 `octoagent-protocol` package，作为 A2A-Lite contract 的单一事实源。

- **FR-002**: 系统 MUST 定义统一 `A2AMessage` envelope，覆盖 `TASK`、`UPDATE`、`CANCEL`、`RESULT`、`ERROR`、`HEARTBEAT` 六类消息。

- **FR-003**: `A2AMessage` MUST 包含并校验以下顶层字段：`schema_version`、`message_id`、`task_id`、`context_id`、`from`、`to`、`type`、`idempotency_key`、`timestamp_ms`、`trace`、`payload`。

- **FR-004**: `A2AMessage` MUST 支持 `hop_count` 与 `max_hops`，并在协议层拒绝超过跳数上限的消息。

- **FR-005**: 系统 MUST 定义 `A2AStateMapper`，将内部 `TaskStatus` 映射为标准 A2A TaskState，并支持反向映射。

- **FR-006**: `A2AStateMapper` MUST 将 `WAITING_APPROVAL`、`PAUSED`、`CREATED` 等被压缩语义通过 `internal_status` 扩展元数据保留下来，以避免外部兼容层彻底丢义。

- **FR-007**: 系统 MUST 定义 protocol-side artifact 视图与 `A2AArtifactMapper`，支持 `text`、`file`、`json`、`image` 四类 part 的一致映射。

- **FR-008**: `A2AArtifactMapper` MUST 将 `artifact_id`、`version`、`hash`、`size` 等 OctoAgent 治理字段稳定映射到 A2A artifact 的可消费字段或 metadata 中。

- **FR-009**: 系统 MUST 提供 payload models 或等价的结构化 builders，用于构造 `TASK/UPDATE/CANCEL/RESULT/ERROR/HEARTBEAT` 的标准 payload。

- **FR-010**: 系统 MUST 提供协议级 `DeliveryLedger` 或等价守卫，能够区分 duplicate、replay、unsupported-version、hop-limit-exceeded 四类结果。

- **FR-011**: 相同 `(message_id, idempotency_key)` 的重复消息 MUST 被识别为 duplicate；不同 `message_id` 复用同一 `idempotency_key` 的消息 MUST 被识别为 replay。

- **FR-012**: 系统 MUST 提供可导入的 contract fixtures，至少覆盖六类消息各一份标准样例，供 Feature 019 / 023 的测试直接消费。

- **FR-013**: 所有 protocol 模型与 fixture MUST 可稳定序列化为 JSON 兼容结构，不得依赖运行时隐式字段或未声明的 Python 对象。

- **FR-014**: Feature 018 MUST NOT 直接落地 broker/queue/network transport；其职责仅限于冻结 contract、mapper、guard 与 fixture。

- **FR-015**: Feature 018 SHOULD 为现有 `DispatchEnvelope`、`WorkerResult`、`WorkerSession` 提供桥接 builder，降低 019/023 接入成本。

### Key Entities

- **A2AMessage**: Kernel ↔ Worker / Worker ↔ SubAgent 的统一 envelope，包含版本、路由、trace、幂等、跳数和结构化 payload。
- **A2ATaskState**: 对外兼容的任务状态集合，用于表达 submitted / working / input-required / completed / canceled / failed / rejected 等标准语义。
- **A2AStateMapper**: 内部 `TaskStatus` 与 `A2ATaskState` 的双向映射层，并负责处理语义压缩时的 metadata 补偿。
- **OctoArtifactView**: protocol-side 的 artifact 视图，承载 A2A mapping 所需的 `append`、`last_chunk`、`meta` 等增强字段。
- **A2AArtifact**: 对外兼容的 artifact 结构，包含 part 列表和治理字段 metadata。
- **DeliveryLedger**: 协议级投递守卫，用于检测 duplicate、replay、版本不兼容和 hop 超限。
- **Contract Fixture Catalog**: 标准消息样例集合，供 019/023 直接导入使用。

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 六类消息（TASK/UPDATE/CANCEL/RESULT/ERROR/HEARTBEAT）都能通过同一套 envelope schema 校验并稳定序列化。

- **SC-002**: `TaskStatus` ↔ `A2ATaskState` 的映射在终态上保持一一对应；`WAITING_APPROVAL` / `PAUSED` / `CREATED` 的压缩语义可通过 metadata 恢复识别。

- **SC-003**: `text/file/json/image` 四类 artifact part 都有明确映射测试，且治理字段不会在映射过程中静默丢失。

- **SC-004**: delivery guard 能稳定区分 duplicate、replay、unsupported-version、hop-limit-exceeded 四类结果，并有对应测试覆盖。

- **SC-005**: Feature 019 / 023 可以直接导入 fixture builders 获取标准消息样例，而不需要各自手写协议顶层字段。

---

## Clarifications

### Session 2026-03-07

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 018 是否要直接接网络传输？ | 不接 | `docs/m2-feature-split.md` 明确 018 负责冻结 contract，真实执行面在 019/023 |
| 2 | artifact 的 `append` / `last_chunk` / `meta` 是否立刻升级 core DB schema？ | 否，先放入 protocol-side 视图 | 降低对现有持久化回归风险，先满足 contract 冻结 |
| 3 | `WAITING_APPROVAL` 映射后如何保留原义？ | 通过 `internal_status` 元数据补偿 | 对齐 blueprint §11.9 |

---

## Scope Boundaries

### In Scope

- `octoagent-protocol` package
- A2A-Lite envelope / payload models
- `A2AStateMapper`
- `A2AArtifactMapper`
- `DeliveryLedger`
- fixture builders 与协议级测试

### Out of Scope

- Kernel / Worker 真实 transport
- durable message queue / persistent replay ledger
- gateway / worker 真实接线
- artifact store schema migration
