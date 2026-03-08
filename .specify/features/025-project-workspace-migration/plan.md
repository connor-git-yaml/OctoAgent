# Implementation Plan: Feature 025 — Project / Workspace Domain Model + Default Project Migration

**Branch**: `codex/feat-025-project-workspace-migration` | **Date**: 2026-03-08 | **Spec**: `.specify/features/025-project-workspace-migration/spec.md`
**Input**: Feature specification from `.specify/features/025-project-workspace-migration/spec.md`

## Summary

本阶段把 M3 Feature 025 收敛成 migration gate：在 `packages/core` 新增正式 `Project/Workspace` 域模型与 SQLite store，在 `packages/provider/dx` 新增 default project migration orchestrator、env compatibility bridge、validation/rollback 和 CLI/bootstrap 接入。整体策略是 additive schema + typed bindings + dual-read compatibility，不重写现有 `scope_id` 主键，不引入 secret 实值存储。

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, aiosqlite, click, structlog, filelock, FastAPI  
**Storage**: SQLite WAL + `project_root/data/*.json` + `octoagent.yaml` + `.env` / `.env.litellm`  
**Testing**: pytest, ruff  
**Target Platform**: 本地单用户 Mac/Linux 服务进程 + CLI + Gateway startup  
**Project Type**: uv workspace monorepo（`packages/core` + `packages/provider` + `apps/gateway`）  
**Performance Goals**: migration 对单实例应在秒级完成；重复执行保持幂等  
**Constraints**:
- 不重写 legacy `scope_id`
- 不持久化 secret 实值
- migration 失败不得把实例留在半升级状态
- 旧 runtime/env 解析必须继续可用  
**Scale/Scope**: 单实例 `project_root` -> 单个 `default project` 的 first-phase migration gate

## Constitution Check

- **Durability First**: 通过。`Project` / `Workspace` / `MigrationRun` 全部落盘；rollback 按 `migration_run_id` 执行，不依赖内存态。
- **Everything is an Event**: 部分通过。本阶段不引入新的 event type，但 migration 审计通过 `ProjectMigrationRun` 显式落盘；后续若需要 operator-visible timeline，可补事件接入。
- **Least Privilege by Default**: 通过。env bridge 仅记录 env/file reference，不存 secret 值。
- **Degrade Gracefully**: 通过。memory/import 表不存在时迁移跳过相应扫描，不阻断整体升级。
- **User-in-Control**: 通过。提供 dry-run、validation、rollback。
- **Observability is a Feature**: 通过。migration report / validation / rollback plan 为结构化输出。

## Project Structure

### Documentation (this feature)

```text
.specify/features/025-project-workspace-migration/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── spec.md
├── contracts/
│   └── project-workspace-migration.md
├── research/
│   ├── tech-research.md
│   └── online-research.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code

```text
octoagent/
├── packages/
│   ├── core/
│   │   ├── src/octoagent/core/models/
│   │   │   ├── __init__.py
│   │   │   └── project.py
│   │   └── src/octoagent/core/store/
│   │       ├── __init__.py
│   │       ├── project_store.py
│   │       └── sqlite_init.py
│   └── provider/
│       └── src/octoagent/provider/dx/
│           ├── cli.py
│           ├── config_commands.py
│           ├── backup_service.py
│           ├── chat_import_service.py
│           └── project_migration.py
├── apps/
│   └── gateway/
│       └── src/octoagent/gateway/main.py
└── tests/
```

**Structure Decision**: `Project/Workspace` 属于跨系统 domain object，放进 `packages/core`；迁移 orchestration、legacy env bridge 与 CLI/bootstrap 接入放进 `packages/provider/dx`；Gateway startup 只负责调用 bootstrap，不持有 domain 细节。

## Design Gate Decision

本次按 `docs/m3-feature-split.md` 历史 `Project Migration Gate` 收口，范围锁定为 F025-T02 / T09 / T10 的第一阶段实现：

- 做 `Project` / `Workspace` / migration run / bindings
- 做 default project migration + env bridge + validation/rollback
- 不做 selector、Secret Store 实值、Wizard UI、Config Center 页面

用户本轮输入已经显式批准该范围，因此本次设计门禁按“已批准范围锁定”继续实施。

## Implementation Phases

### Phase 1 — Domain & Store

- 新增 `project.py`
- 新增 `project_store.py`
- `sqlite_init.py` 增加 project tables
- `StoreGroup` 导出 `project_store`

### Phase 2 — Migration Orchestrator

- 新增 `project_migration.py`
- 实现 legacy metadata discovery
- 实现 dry-run / apply / validation / rollback

### Phase 3 — Runtime Surface Integration

- `config_commands.py` 实现 `config migrate`
- `gateway/main.py` startup 自动 ensure default project
- `backup_service.py` / `chat_import_service.py` 进入持久化路径前确保 migration

### Phase 4 — Test Matrix & Docs

- core store/model tests
- provider migration service/CLI tests
- gateway startup bootstrap test
- 回写 `docs/blueprint.md` 与 `docs/m3-feature-split.md`

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 无 | - | 采用 additive schema，无需额外架构违反项 |
