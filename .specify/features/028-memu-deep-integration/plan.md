# Implementation Plan: Feature 028 — MemU Deep Integration

**Branch**: `028-memu-deep-integration` | **Date**: 2026-03-08 | **Spec**: `.specify/features/028-memu-deep-integration/spec.md`
**Input**: `.specify/features/028-memu-deep-integration/spec.md` + `research/research-synthesis.md`

## Summary

Feature 028 不重做 020 的治理核心，也不重做 027 的产品面，而是在两者之间补齐一个可产品化的高级 memory engine plane：

1. **治理层保持不变**：`WriteProposal -> validate -> commit`、SoR current 唯一、Vault default deny 仍由 `MemoryService` 控制。
2. **引擎层深度集成 MemU**：扩展 `MemoryBackend` / `MemUBridge`，让检索、同步、诊断、ingest、派生层与 maintenance 都通过统一 contract 暴露。
3. **产品层兼容扩展 027**：不新造 Memory Console canonical DTO，只在既有 027 resource 上增加 backend / diagnostics / evidence / derived 扩展字段与 hooks。
4. **控制面只消费 hook**：026 继续只看 diagnostics summary 与 maintenance actions，不新增 memory control-plane canonical resource。

当前实现已经完成并通过定向验证，核心交付包括：

- backend contract 扩展
- SQLite fallback / MemU failback 基线
- project-scoped `HttpMemUBridge` + provider-side backend resolver
- 持久化 sync backlog / replay / reconnect 语义
- 多模态 ingest / derived layer / evidence chain
- 027 memory resource 的兼容字段扩展
- 026 diagnostics 与 maintenance action hook

## Technical Context

**Language/Version**: Python 3.12+  
**Primary Dependencies**: `pydantic>=2.10`, `aiosqlite>=0.21`, `structlog>=25.1`, `octoagent-core`, `octoagent-provider`, `octoagent-memory`  
**Storage**: SQLite governance store + MemU bridge / engine integration  
**Testing**: `pytest`, `pytest-asyncio`, changed-file `ruff check`  
**Target Platform**: monorepo backend services + gateway control plane  
**Constraints**:
- 不允许 MemU 直接旁路写 SoR / Vault
- Feature 027 已交付，028 只能兼容扩展其 canonical product resources
- Feature 026 只允许 diagnostics / action hook，不允许新增 memory control-plane canonical resource
- MemU 不可用时必须自动退回核心 Memory 能力

## Constitution Check

| Constitution 原则 | 适用性 | 评估 | 说明 |
|---|---|---|---|
| 原则 1: Durability First | 直接适用 | PASS | MemU 只是 engine；权威事实仍回落到 SQLite governance store |
| 原则 2: Everything is an Event | 间接适用 | PASS | maintenance / diagnostics / projection 接缝为后续事件化提供统一对象 |
| 原则 5: Least Privilege by Default | 直接适用 | PASS | Vault default deny 继续生效；advanced results 不得泄漏敏感原文 |
| 原则 6: Degrade Gracefully | 直接适用 | PASS | 增加 backend 状态、fallback 与自动 failback |
| 原则 7: User-in-Control | 间接适用 | PASS | 026 仅暴露 maintenance hooks，不绕过审批/控制面 |
| 原则 11: Context Hygiene | 直接适用 | PASS | evidence / derived / diagnostics 以 projection 暴露，不直接塞 raw payload |
| 原则 12: 记忆写入必须治理 | 直接适用 | PASS | derived / ingest / maintenance 只能产出 fragment / derived / proposal |

## Project Structure

### 文档制品

```text
.specify/features/028-memu-deep-integration/
├── spec.md
├── plan.md
├── data-model.md
├── contracts/
│   └── memu-integration-api.md
├── checklists/
│   └── requirements.md
├── research/
│   ├── product-research.md
│   ├── tech-research.md
│   ├── research-synthesis.md
│   └── online-research.md
├── tasks.md
└── verification/
    └── verification-report.md
```

### 源码变更布局

```text
octoagent/
├── apps/gateway/
│   ├── src/octoagent/gateway/services/control_plane.py
│   └── tests/test_control_plane_api.py
├── packages/core/
│   └── src/octoagent/core/models/control_plane.py
├── packages/provider/
│   └── src/octoagent/provider/dx/memory_console_service.py
└── packages/memory/
    ├── src/octoagent/memory/
    │   ├── __init__.py
    │   ├── models/
    │   │   ├── __init__.py
    │   │   └── integration.py
    │   ├── backends/
    │   │   ├── protocols.py
    │   │   ├── sqlite_backend.py
    │   │   └── memu_backend.py
    │   ├── models.py
    │   └── service.py
    └── tests/
        └── test_memory_backends.py
```

## Implementation Phases

### Phase 1: Engine Contract & Failback Baseline

- 扩展 `MemoryBackend` / `MemUBridge` 协议
- 引入 `MemoryBackendStatus`、`MemorySyncBatch`、`MemoryIngestBatch`、`MemoryMaintenanceRun` 等 integration models
- 在 `MemoryService` 中实现 backend 状态跟踪、fallback 与 failback
- 将 memory backend diagnostics 接到 027 resource 与 026 diagnostics summary

### Phase 2: Sync / Replay / Diagnostics Hardening

- 完善 backlog、replay、retry_after、failure code 状态传播
- 让 sync / replay 进入结构化 `MemorySyncResult`
- 为 provider/gateway 层补齐 diagnostics 展示与 deep refs

### Phase 3: Multimodal Ingest Base

- 定义 `text | image | audio | document` ingest handoff
- 统一 `artifact -> fragment -> derived -> proposal(optional)` 证据链
- 实现 ingest partial success / idempotency / safe fallback

### Phase 4: Derived Layers

- 实现 Category / relation / entity / ToM 派生层查询契约
- 保证 derived 结果只能作为 projection / proposal draft，不能直接变成 SoR

### Phase 5: Maintenance Execution Chain

- 实现 `flush / reindex / bridge.reconnect / sync.resume` 等 maintenance 命令
- 记录 `MemoryMaintenanceRun`、输出 refs、失败摘要和 diagnostics 贡献

### Phase 6: Verification & Docs Sync

- 补齐 028 对应的 unit / integration 测试
- 同步 spec / plan / tasks / verification 文档
- 与 027 contract 和 026 hooks 做最终一致性校验

## Non-goals

- 重做 027 Memory Console UI
- 重做 026 control-plane canonical resources
- 让 MemU 直接接管 SoR / Vault 写路径
- 在本阶段实现完整 Memory Console / Scheduler / Runtime Console 页面
