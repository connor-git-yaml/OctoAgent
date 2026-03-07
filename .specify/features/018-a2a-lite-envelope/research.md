# Research Decisions: Feature 018 — A2A-Lite Envelope + A2AStateMapper

**Feature**: `.specify/features/018-a2a-lite-envelope`  
**Date**: 2026-03-07

---

## D1: 协议代码放到哪里？

- **Decision**: 新增 `octoagent/packages/protocol`
- **Rationale**: 对齐 blueprint 目标 repo 结构，避免把 wire contract 混进 `core` 或 `gateway`
- **Alternatives**:
  - 放进 `packages/core`: domain 与 wire contract 混层
  - 放进 `apps/gateway`: worker / tests 无法自然复用

## D2: artifact 协议增强字段如何承载？

- **Decision**: 使用 `A2AArtifactMapper` 把 `version/hash/size` 放入 `A2AArtifact.metadata`
- **Rationale**: 当前目标是先冻结协议 contract，不扩大到 DB migration
- **Alternatives**:
  - 直接升级 core ArtifactStore schema: 回归面更大

## D3: replay / duplicate 守卫是否立刻持久化？

- **Decision**: 先交付内存态 `A2AReplayProtector`
- **Rationale**: 本 feature 只负责协议面；durable ledger 依赖后续真实 transport/store
- **Alternatives**:
  - 直接接 event store: 018 与运行时耦合过早

## D4: 下游 feature 如何直接消费 contract？

- **Decision**: 提供 `.specify/features/018-a2a-lite-envelope/contracts/fixtures/*.json`
- **Rationale**: fixture 路径稳定，019/023 可直接读取，且已被测试校验
- **Alternatives**:
  - 只写文档示例: 无法自动化校验
  - 只提供 builder API: 下游仍需自己固化 JSON contract
