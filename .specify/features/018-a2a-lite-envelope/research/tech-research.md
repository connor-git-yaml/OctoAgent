---
feature_id: "018"
title: "A2A-Lite Envelope + A2AStateMapper"
stage: "tech-research"
created: "2026-03-07"
research_mode: "tech-only"
references:
  - "docs/blueprint.md §10.2 / §10.2.1 / §10.2.2 / §11.9"
  - "docs/m2-feature-split.md Feature 018"
  - "_references/opensource/pydantic-ai/docs/a2a.md"
  - "_references/opensource/pydantic-ai/docs/multi-agent-applications.md"
---

# 技术调研：Feature 018 — A2A-Lite Envelope + A2AStateMapper

## 执行摘要

Feature 018 的目标不是把多 Worker 真正跑起来，而是先把后续会复用的协议冻结下来：消息 envelope、状态映射、artifact 映射、版本与幂等守卫、以及供 019/023 直接消费的 fixture。

结合当前代码库现状，推荐方案是：

1. 新增独立 `octoagent-protocol` workspace package，专门承载 A2A-Lite 合约。
2. 复用现有 `octoagent-core` 中的 `TaskStatus`、`Artifact` 与 `DispatchEnvelope`/`WorkerResult` 模型做桥接，而不是把 A2A 逻辑塞回 `core` 或 `gateway`。
3. 以纯模型 + 纯函数 + 轻量内存守卫的方式先冻结 contract，不提前引入 broker/queue/transport。

这样能满足 M2 当前“先冻 contract，再让 019/023 消费”的拆解策略，也不会把还未落地的 JobRunner/Worker transport 逻辑提前耦合进来。

---

## 需求-技术对齐

| 需求 | 当前代码现状 | 技术结论 |
|---|---|---|
| TASK/UPDATE/CANCEL/RESULT/ERROR/HEARTBEAT envelope | 当前只有 Feature 008 的 `DispatchEnvelope` / `WorkerResult`，没有统一 A2A-Lite 包 | 需要新增独立 `protocol` package |
| 内部状态 -> A2A TaskState 映射 | `TaskStatus` 已存在，但没有 `A2AStateMapper` | 可以直接基于 `octoagent.core.models.enums.TaskStatus` 建映射层 |
| Artifact -> A2A Artifact 映射 | `Artifact` / `ArtifactPart` 已存在，`PartType` 已包含 `json` / `image` 预留值 | 需要新增 mapper 和协议侧 artifact 视图 |
| 幂等 / 重放 / 跳数 / 版本兼容 | core event store 已有 `idempotency_key` 唯一约束，但没有协议层守卫 | 先交付内存态 `DeliveryLedger`，后续由 019/023 再接 durable storage |
| 对 019/023 可直接消费的 fixture | 当前没有 | 需要新增 Python fixture builders 和序列化样例 |

---

## 方案对比

### 方案 A：继续扩展 `core.models.orchestrator`

把 A2A-Lite 字段直接并入 `DispatchEnvelope` / `WorkerResult`，同时在 `core` 中加入状态和 artifact 映射。

**优点**

- 变更文件少。
- 直接复用现有 orchestrator 模型。

**缺点**

- `core` 会同时承载 domain model 与跨 Agent 协议，职责变脏。
- 后续 019/023 会把 transport、fixture、guard 继续堆进 `core`。
- 难以区分“内部 domain 模型”和“跨 Agent wire contract”。

### 方案 B：新增独立 `protocol` package

新增 `octoagent/packages/protocol`，承载 A2A-Lite 模型、mapper、guard 与 fixture；`core` 只保留自己的领域模型。

**优点**

- 与 blueprint 目标目录一致。
- 019/023 可以直接依赖 `octoagent-protocol`，边界清晰。
- contract、fixture、guard 可单独测试和版本化。

**缺点**

- 需要更新 workspace 配置。
- 初次引入时文件数略多。

### 推荐方案

选择 **方案 B：独立 `protocol` package**。

理由：

1. `docs/m2-feature-split.md` 明确把 018 定义为“冻结 contract”的特性。
2. `docs/blueprint.md §10.2` 和目标 Repo 结构都把 `packages/protocol/` 作为正式边界。
3. 当前多 Worker / transport 还未落地，独立 package 更适合先交付“可消费 contract”而不是“半成品运行时”。

---

## 关键设计决策

### 决策 1：消息 envelope 使用独立 `A2AMessage`

**Decision**: 定义 `A2AMessage`，包含：

- `schema_version`
- `message_id`
- `task_id`
- `context_id`
- `from` / `to`
- `type`
- `idempotency_key`
- `timestamp_ms`
- `trace`
- `hop_count` / `max_hops`
- `payload`
- `metadata` / `extensions`

**Rationale**:

- 直接对齐 blueprint §10.2。
- 便于对 fixture 做统一序列化测试。

### 决策 2：状态映射保留 `internal_status` 元数据

**Decision**: `A2AStateMapper.to_a2a()` 除返回标准 A2A state 外，还提供 metadata merge helper，在发生语义压缩时写入 `internal_status`。

**Rationale**:

- 对齐 blueprint §11.9。
- 避免 `WAITING_APPROVAL` / `PAUSED` / `CREATED` 在对外映射后彻底丢义。

### 决策 3：artifact 映射引入协议侧视图

**Decision**: 不直接改动当前 ArtifactStore 持久化 schema；在 `protocol` 包引入协议侧 `OctoArtifactView`，承载 `append` / `last_chunk` / `meta`，并可从 `core.Artifact` 升级得到。

**Rationale**:

- 当前 Feature 018 的目标是冻结 contract，不是上线 artifact schema migration。
- 降低对现有持久化和已有测试的回归风险。

### 决策 4：幂等 / 重放 / 跳数 / 版本守卫先做轻量实现

**Decision**: 交付 `DeliveryLedger`（内存态）与结构化 `DeliveryAssessment`，覆盖：

- 支持版本校验
- hop limit 校验
- 同 `(message_id, idempotency_key)` 重复投递识别
- 不同 `message_id` 复用同一 `idempotency_key` 的 replay 检测

**Rationale**:

- 018 只需冻结协议面 contract。
- 真正 durable 的 message ledger 更适合在 019/023 接 transport/store 时落地。

---

## 技术风险

| 风险 | 概率 | 影响 | 缓解策略 |
|---|---|---|---|
| 状态压缩后外部消费者误解 `WAITING_APPROVAL` / `PAUSED` | 中 | 高 | 在 mapper helper 中强制带 `internal_status` 元数据 |
| 未来 transport 直接绕过 protocol fixture，自行拼字段 | 中 | 高 | 提供 importable fixture builders，并在 tests 中冻结字段集合 |
| 当前 core `Artifact` 未持久化 `append/meta` | 中 | 中 | 使用协议侧 `OctoArtifactView` 承载协议增强字段，后续再决定是否升级 core schema |
| schema_version 扩展失控 | 低 | 高 | 在 envelope 和 delivery guard 中集中维护 `SUPPORTED_SCHEMA_VERSIONS` |

---

## 参考结论

- `docs/blueprint.md §10.2` 已给出 envelope、state mapping、artifact mapping 的目标字段与语义。
- `docs/m2-feature-split.md` 明确要求 018 先冻结 contract，再供 019/023 消费。
- `pydantic-ai` 的 A2A 文档强调 task/context/persistence/tracing 是协议层一等公民，这支持把消息 envelope 抽成独立 package，而不是散落在 gateway service 内。

## 推荐实施范围（MVP）

1. `octoagent/packages/protocol` workspace package
2. `A2AMessage` / payload models / enums
3. `A2AStateMapper`
4. `A2AArtifactMapper`
5. `DeliveryLedger`
6. `fixture` builders + pytest contract tests

## 暂不在 018 落地

- 真正的 broker / queue / network transport
- durable message ledger
- gateway / kernel / worker 真实接线
- core ArtifactStore schema migration
