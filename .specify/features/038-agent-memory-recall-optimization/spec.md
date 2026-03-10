---
feature_id: "038"
title: "Agent Memory Recall Optimization"
milestone: "M3 carry-forward"
status: "Implemented"
created: "2026-03-10"
updated: "2026-03-10"
research_mode: "full"
blueprint_ref: "docs/blueprint.md M3 carry-forward；Feature 020/027/028/033；OpenClaw / Agent Zero / OpenClaw MemU 实际脚本"
predecessor: "Feature 020（Memory Core）；Feature 027（Memory Console）；Feature 028（MemU 深度集成）；Feature 033（Agent Context Continuity）"
parallel_dependency: "Feature 038 为 033 的 memory 主链补强：统一 indexing -> recall 的 runtime contract，并把 recall provenance 暴露给 Agent/runtime/tooling。"
---

# Feature Specification: Agent Memory Recall Optimization

**Feature Branch**: `feat/038-agent-memory-recall-optimization`  
**Created**: 2026-03-10  
**Updated**: 2026-03-10  
**Status**: Implemented  
**Input**: 复核 OctoAgent 当前 Agent Memory 从建立索引到实际使用的端到端流程，对比 OpenClaw、Agent Zero 及 OpenClaw 上实际调用 MemU 的脚本，把真正值得吸收的 recall / indexing 机制接入当前主链，而不是停留在 console-only 或参考文档层面。  
**调研基础**: `research/research-synthesis.md`

## Problem Statement

当前仓库的 Memory governance plane 已具备较完整的写入仲裁、检索、Vault、MemU degrade path，但 runtime recall 的主链仍存在结构性断裂：

1. `MemoryBackendResolver` 只在 console 路径真正生效，主 Agent runtime 与 chat import 写入链路没有统一消费 project-scoped backend。
2. 主 Agent 的记忆读取仍偏向“原 query + 基础 hit 列表”，缺少 query expansion、citation、content preview、backend truth、pending replay 等 recall provenance。
3. 内置工具只有 `memory.read / memory.search / memory.citations`，缺一个可直接给 Agent/Subagent 使用的 `memory.recall` 结构化入口。
4. `chat_import_service` 在 indexing/write path 上仍直接构造裸 `MemoryService(...)`，导致“导入时”和“运行时”未必走同一 memory engine 解析链。
5. 033 已把 recent summary / context frame 接进主 Agent，但 memory recall 仍缺一层独立 contract，难以继续迭代 delayed recall、rerank、tool-side recall 等能力。

因此，问题不再是“有没有 Memory”，而是 **Memory 从 ingest 到 runtime recall 还没有一条一致、可解释、可降级的产品主链**。

## Product Goal

把 `indexing -> backend resolve -> recall -> context assembly -> tool access` 收敛为一条统一 contract：

- 所有 runtime consumer 通过 project/workspace-aware resolver 获取 `MemoryService`
- 引入 `recall_memory()` 作为 Agent/runtime 专用 recall pack，而不是只暴露底层 `search_memory()`
- 在 `ContextFrame` 中保留 recall query、expanded queries、backend status、citation、preview、evidence refs
- 给 built-in tools 增加 `memory.recall`
- 让 chat import / compaction flush / main agent recall 共享同一 backend resolve 语义

## Scope Alignment

### In Scope

- `MemoryRecallHit` / `MemoryRecallResult` 结构化 recall contract
- `MemoryService.recall_memory()` 的 query expansion、citation、preview、backend truth
- `AgentContextService` 使用 recall pack 组装 runtime context
- `TaskService` compaction flush 继续复用 project-scoped memory resolver
- `ChatImportService` indexing/write path 接入 project-scoped memory runtime resolver
- `CapabilityPackService` 增加 `memory.recall`，并让现有 memory tools 优先解析当前 runtime project/workspace
- `ControlPlane` 的 context continuity 资源暴露 recall provenance，便于从索引到实际消费做端到端审计
- 为 delayed recall 建立 durable request/result carrier，落在 task event + artifact + context provenance
- 为 recall 引入内建 `post-filter / rerank` hooks，并保留 trace / fallback provenance
- 单元测试、集成测试、spec/verification 文档

### Out of Scope

- 重写 `MemoryBackend` / MemU bridge 协议
- 引入新的向量数据库或替换 SQLite fallback
- 照抄 Agent Zero 的单一可变 FAISS 作为真相源
- 照抄 OpenClaw 外挂脚本式 MemU 文件落地模型
- 本阶段不实现 delayed recall 的独立后台 worker / queue consumer、LLM rerank、自动 consolidation 改写 SoR
- 本阶段不引入可执行任意脚本的 recall hook 插件面；只提供内建、可观测、可回退的 hook 模式

## User Stories & Testing

### User Story 1 - 主 Agent 在真实对话里拿到可解释的 recall pack (Priority: P1)

作为 owner，我希望主 Agent 在构建 prompt 时不仅拿到“命中列表”，还拿到 query expansion、citation、preview 和 backend 状态，这样回答可以连续、可解释、可调试。

**Independent Test**: 在 project 绑定 memory scope 后发起真实 task，验证 `ContextFrame` 与 LLM request artifact 中出现 recall 命中、citation 和 backend metadata。

### User Story 2 - 导入后的记忆能通过同一 runtime chain 被主 Agent 和工具使用 (Priority: P1)

作为 owner，我希望聊天导入写入的记忆和主 Agent 运行时读取的记忆走同一 project-scoped backend resolve 语义，而不是导入和 recall 各走一套。

**Independent Test**: 执行 chat import 后，通过 `memory.recall` 或主 Agent runtime 读取刚导入的 scope，验证使用的是 project-scoped runtime memory service。

### User Story 3 - Agent/Subagent 可以直接调用结构化 recall 工具 (Priority: P1)

作为 worker/subagent，我希望通过一个轻量工具直接获得 recall pack，而不是手动拼 `memory.search + memory.read + memory.citations`。

**Independent Test**: 执行 `memory.recall`，验证返回 query、expanded queries、hits、citation、backend status。

## Edge Cases

- 当前 task 没有显式 project binding 时，memory tools 应如何回退到 surface selector/default project？
- scope 无法解析时，是否应 fail-hard？本 Feature 选择返回空 recall + `memory_scope_unresolved` degraded reason。
- 高级 backend unavailable 时，是否中断主聊天？否，必须回退到 SQLite fallback 并把 state 写入 recall metadata。
- Vault 默认不可检索；即便 `memory.recall` 暴露，也必须显式 `allow_vault` 才允许读取敏感分区。

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 为 Agent/runtime 定义正式 `MemoryRecallHit` / `MemoryRecallResult` contract。
- **FR-002**: `MemoryService` MUST 提供 `recall_memory()`，至少返回 `query`、`expanded_queries`、`scope_ids`、`hits`、`backend_status`、`degraded_reasons`。
- **FR-003**: `recall_memory()` MUST 从既有 `search_memory()` / `get_memory()` / `resolve_memory_evidence()` 复用治理边界，不得旁路底层表。
- **FR-004**: `recall_memory()` MUST 生成 citation、content preview、evidence refs、derived refs，并把 query expansion 结果保留下来。
- **FR-005**: `AgentContextService` MUST 使用 `recall_memory()`，并把 recall provenance 写入 `ContextFrame.budget["memory_recall"]` 与 `memory_hits` payload。
- **FR-006**: runtime memory consumer MUST 通过 `MemoryRuntimeService` / `MemoryBackendResolver` 解析 project/workspace-scoped `MemoryService`，不得继续在主链里直接裸建 `MemoryService(conn)`。
- **FR-007**: `ChatImportService` MUST 使用同一 runtime resolver，保证导入/indexing 与主 Agent recall 一致对齐 project-scoped backend。
- **FR-008**: `CapabilityPackService` MUST 暴露 `memory.recall`，并让 `memory.read / search / citations` 优先解析当前 runtime project/workspace，而不是盲退回默认 project。
- **FR-009**: 当 scope 无法解析或 backend 降级时，系统 MUST degrade gracefully，并显式返回 degraded reason / backend truth。
- **FR-010**: Feature 038 MUST 提供单元测试和集成测试，证明 recall contract、runtime wiring、chat import resolver 和 built-in tool 均真实接线。
- **FR-011**: `ContextContinuityDocument.frames[*]` MUST 暴露 `memory_hits`、`memory_recall`、`source_refs` 与 budget 摘要，供 Control Plane 审计 recall provenance。
- **FR-012**: 当即时 recall 出现 backend backlog / degraded state / prompt trim 等信号时，系统 MUST 把 delayed recall 的 request/result 以 task-scoped artifact + event 持久化，并把状态回填到 `ContextFrame.budget["delayed_recall"]`。
- **FR-013**: `MemoryService.recall_memory()` MUST 支持内建 `post-filter / rerank` hook 选项，并把候选数、过滤数、fallback、最终模式等 trace 随 recall result 一并返回。
- **FR-014**: 主 Agent runtime 与 `memory.recall` 工具 MUST 可以消费上述 hook 选项；默认 runtime 路径必须启用安全的内建 hook 组合，而不是仅停留在工具可选参数层。

### Key Entities

- `MemoryRecallHit`
- `MemoryRecallResult`
- `MemoryRuntimeService`
- `AgentContextService.memory_recall`
- `memory.recall`

## Success Criteria

- **SC-001**: 主 Agent 真实 prompt/context 路径中可以看到 citation、preview、expanded queries 和 backend truth。
- **SC-002**: `chat_import_service`、`AgentContextService`、`memory.recall` 三条路径共享同一 project-scoped runtime resolver 语义。
- **SC-003**: `memory.recall` 可以独立返回结构化 recall pack，而不需要调用方手动拼多次 memory tool。
- **SC-004**: backend 降级或 scope 缺失时，系统仍然返回可解释结果，不破坏主聊天链路。
- **SC-005**: Control Plane 可以直接查看某次 `ContextFrame` 的 recall provenance，而不需要再查底层存储或日志。
- **SC-006**: delayed recall 即便不依赖进程内 extras，也能在 task events / artifacts / context frame 中被重新审计与恢复消费。
- **SC-007**: recall post-filter 命中过少或误伤时，系统仍能自动回退到未过滤候选，并把 hook trace 写入 recall provenance，不破坏主聊天链路。

## Clarifications

### Session 2026-03-10

| # | 问题 | 选择 | 理由 |
|---|---|---|---|
| 1 | 是否照抄 Agent Zero 的单一向量索引为真相源？ | 否 | OctoAgent 已冻结 SoR / Vault / WriteProposal 治理边界 |
| 2 | 是否照抄 OpenClaw 的外挂脚本式 MemU 文件落地？ | 否 | 可以借鉴其提取/压缩/链接流程，但不能旁路当前治理和项目隔离 |
| 3 | 是否把 recall 继续做成 `search_memory()` 的薄包装？ | 否 | runtime 需要独立的 provenance contract |
| 4 | 是否必须本阶段实现 delayed recall / rerank？ | 分阶段 | 先补主链闭环；delayed recall 与内建 rerank/post-filter 作为 038 deferred 项继续收口 |

## Risks & Design Notes

- OpenClaw 主仓本身并没有正式 MemU runtime 集成；真正值得吸收的是它在实际脚本里采用的“分步提取、压缩、关联”思想，而不是把脚本目录当成正式产品面。
- Agent Zero 的 query prep / post-filter / delayed recall 值得借鉴，但其 `DeferredTask` 非 durable，不适合直接搬进 OctoAgent。
- 如果 038 只增加 `memory.recall` 工具而不修 `chat_import_service` 和 runtime resolver，仍然不算端到端优化。
