# Implementation Plan: Feature 020 — Memory Core + WriteProposal + Vault Skeleton

**Branch**: `codex/feat-020-memory-core` | **Date**: 2026-03-07 | **Spec**: `.specify/features/020-memory-core/spec.md`
**Input**: `.specify/features/020-memory-core/spec.md` + `research/research-synthesis.md`

## Summary

Feature 020 为 OctoAgent 新增一个独立的 `packages/memory`，先把长期记忆治理契约锁死，再把向量检索、聊天导入、Vault 授权等增强能力后置。

技术策略分三层：

1. **模型层**: 定义 `Fragments / SoR / WriteProposal / Vault skeleton` 的强类型模型和枚举。
2. **存储层**: 用 SQLite 实现最小元信息持久化，并通过 partial unique index 硬性保证 `SoR.current` 唯一。
3. **服务层**: 提供 `validate_proposal()`、`commit_memory()`、`search_memory()`、`get_memory()`、`before_compaction_flush()` 五个核心接口，冻结后续 Feature 021/023 可复用的 contract。
4. **backend 层**: 引入 `MemoryBackend` 协议；默认由 `SqliteMemoryBackend` 提供降级能力，M2 可切换到 `MemUBackend` 承担检索、索引和增量同步的大部分工作。

## Technical Context

**Language/Version**: Python 3.12+  
**Primary Dependencies**: `pydantic>=2.10`, `aiosqlite>=0.21`, `python-ulid>=3.1`, `structlog>=25.1`, `octoagent-core`  
**Storage**: SQLite（governance metadata）；backend 可切换到 MemU/向量引擎  
**Testing**: `pytest`, `pytest-asyncio`  
**Target Platform**: 本地 Python package / 单机 SQLite  
**Project Type**: workspace monorepo package  
**Performance Goals**: 单次 memory commit 在 SQLite 内完成；基础 search/get 不依赖外部服务  
**Constraints**:
- 不在 020 直接实现 Chat Import、Vault 授权 UI
- Vault 默认不可检索
- 020 不耦合上下文 GC，仅保留 flush 钩子
**Scale/Scope**: 单用户、单机、M2 内核 contract 冻结

## Constitution Check

| Constitution 原则 | 适用性 | 评估 | 说明 |
|---|---|---|---|
| 原则 1: Durability First | 直接适用 | PASS | Memory 元信息先落 SQLite，保证 current/superseded 可恢复 |
| 原则 2: Everything is an Event | 间接适用 | PASS | 020 先保留 proposal/commit 审计状态；后续接事件流时可继续扩展 |
| 原则 5: Least Privilege by Default | 直接适用 | PASS | Vault 默认拒绝检索，敏感分区不暴露给普通读取 |
| 原则 6: Degrade Gracefully | 直接适用 | PASS | 向量检索与 compaction 后置，SQLite 元信息路径可独立工作 |
| 原则 7: User-in-Control | 间接适用 | PASS | 020 本次不做审批 UI，但默认 deny 策略已先落地 |
| 原则 11: Context Hygiene | 直接适用 | PASS | 检索契约使用 search/get 两段式，不直接塞大正文 |
| 原则 12: 记忆写入必须治理 | 直接适用 | PASS | 所有写入统一经 `WriteProposal -> validate -> commit` |

**结论**: 无硬门冲突，可以进入实现。

## Project Structure

### 文档制品

```text
.specify/features/020-memory-core/
├── spec.md
├── plan.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── memory-api.md
├── checklists/
│   └── requirements.md
├── research/
│   ├── product-research.md
│   ├── tech-research.md
│   ├── research-synthesis.md
│   └── online-research.md
└── tasks.md
```

### 源码变更布局

```text
octoagent/
├── pyproject.toml
└── packages/
    ├── core/
    └── memory/
        ├── pyproject.toml
        ├── src/octoagent/memory/
	        │   ├── __init__.py
        │   ├── backends/
        │   │   ├── __init__.py
        │   │   ├── protocols.py
        │   │   ├── sqlite_backend.py
        │   │   └── memu_backend.py
	        │   ├── enums.py
	        │   ├── models/
        │   │   ├── __init__.py
        │   │   ├── common.py
        │   │   ├── fragment.py
        │   │   ├── proposal.py
        │   │   ├── sor.py
        │   │   └── vault.py
        │   ├── models.py
        │   ├── service.py
        │   └── store/
        │       ├── __init__.py
        │       ├── sqlite_init.py
        │       ├── memory_store.py
        │       └── protocols.py
        └── tests/
            ├── conftest.py
            ├── test_models.py
            ├── test_sqlite_init.py
            ├── test_memory_store.py
            └── test_memory_service.py
```

**Structure Decision**: 020 作为独立 workspace package 落在 `packages/memory`，不改动 `core` 的职责边界，只在根 workspace 注册新成员。

## Architecture

### 模块边界

#### 1. `enums.py`

定义以下枚举：

- `MemoryLayer`: `FRAGMENT | SOR | VAULT`
- `MemoryPartition`: `CORE | PROFILE | WORK | HEALTH | FINANCE | CHAT`
- `SorStatus`: `CURRENT | SUPERSEDED | DELETED`
- `WriteAction`: `ADD | UPDATE | DELETE | NONE`
- `ProposalStatus`: `PENDING | VALIDATED | REJECTED | COMMITTED`

#### 2. `models/`

定义：

- `EvidenceRef`
- `FragmentRecord`
- `SorRecord`
- `VaultRecord`
- `WriteProposal`
- `ProposalValidation`
- `MemorySearchHit`
- `MemoryAccessPolicy`
- `CompactionFlushResult`

#### 3. `store/sqlite_init.py`

负责创建：

- `memory_fragments`
- `memory_sor`
- `memory_write_proposals`
- `memory_vault`

关键索引：

- `idx_memory_sor_current_unique` on `(scope_id, subject_key)` where `status='current'`
- 常用检索索引：`scope_id`, `partition`, `created_at`

#### 4. `store/memory_store.py`

职责：

- 基础 insert / fetch / search
- SoR current / history 查询
- proposal 状态持久化
- Vault skeleton 读写

约束：

- `FragmentRecord` 只追加
- `memory_store` 不做业务验证，验证逻辑留在 service

#### 5. `backends/`

职责：

- 定义 `MemoryBackend` 协议
- 提供默认 `SqliteMemoryBackend`
- 提供 `MemUBackend` adapter 位
- 保证 backend 故障时可降级回本地 metadata 路径

#### 6. `service.py`

职责：

- `validate_proposal()`
- `commit_memory()`
- `search_memory()`
- `get_memory()`
- `before_compaction_flush()`
- `MemoryBackend` orchestration

关键规则：

- 非 `NONE` proposal 必须有 `evidence_refs`
- `UPDATE` / `DELETE` 必须命中 current 记录
- `health` / `finance` 或显式敏感 proposal 路由到 Vault skeleton
- `search_memory()` 默认只查 `SoR.current + Fragments`
- backend search / sync 失败时自动切回 SQLite fallback，不回滚治理写入

## Implementation Phases

### Phase 1: Package 骨架与 workspace 接入

- 新增 `packages/memory/pyproject.toml`
- 更新根 `pyproject.toml` 的 workspace members、sources、dev 依赖

### Phase 2: 数据模型与 SQLite schema

- 实现 enums / models
- 实现 `init_memory_db()`
- 为 current 唯一约束补测试

### Phase 3: Store / Backend / Service

- 实现 MemoryStore CRUD/search
- 实现 `MemoryBackend` 协议与 `SqliteMemoryBackend`
- 插入 `MemUBackend` adapter 接缝
- 实现 proposal 验证与 commit
- 实现 Vault default deny 和 flush 钩子

### Phase 4: Tests + Verification

- 单测：模型、store、service
- 集成测试：完整写入链路、Vault 拒绝、current 约束
- 生成 verification 报告并更新任务状态

## Non-goals

- 不直接实现 Chat Import Core
- 不实现 Vault 审批与浏览 UI
- 不实现自动 compaction 运行时
