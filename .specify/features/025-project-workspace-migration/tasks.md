# Tasks: Feature 025 — Project / Workspace Domain Model + Default Project Migration

**Input**: Design documents from `.specify/features/025-project-workspace-migration/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/project-workspace-migration.md`

**Tests**: 本次为 migration gate，测试为必选项。

## Phase 1: Setup / Docs

- [x] T001 回写 `.specify/features/025-project-workspace-migration/*` 制品，锁定第一阶段范围与测试矩阵
- [x] T002 [P] 回读 `docs/blueprint.md` 与 `docs/m3-feature-split.md`，准备后续同步 M3 gate 口径

---

## Phase 2: Foundational (Blocking)

**Purpose**: 先建立 canonical domain 与 store，后续 migration/CLI/bootstrap 才有稳定落点

- [x] T003 在 `octoagent/packages/core/src/octoagent/core/models/project.py` 新增 `Project` / `Workspace` / `ProjectBinding` / `ProjectMigrationRun` 与相关枚举
- [x] T004 在 `octoagent/packages/core/src/octoagent/core/models/__init__.py` 导出新模型
- [x] T005 在 `octoagent/packages/core/src/octoagent/core/store/project_store.py` 实现 SQLite project store
- [x] T006 在 `octoagent/packages/core/src/octoagent/core/store/sqlite_init.py` 增加 `projects/workspaces/project_bindings/project_migration_runs` 表与最小 schema migration
- [x] T007 在 `octoagent/packages/core/src/octoagent/core/store/__init__.py` 把 `project_store` 纳入 `StoreGroup`
- [x] T008 [P] 在 `octoagent/packages/core/tests/` 新增 models/store/sqlite_init 覆盖

**Checkpoint**: `Project/Workspace` 领域层与持久化基线完成

---

## Phase 3: User Story 1 — default project migration (Priority: P1) 🎯 MVP

**Goal**: 旧 M2 实例自动获得 default project + primary workspace + legacy metadata bindings  
**Independent Test**: 准备 legacy 项目目录，执行 migration apply，验证 project/workspace/bindings 完整生成且 legacy data 保持可读

### Tests for US1

- [x] T009 [P] [US1] 在 `octoagent/packages/provider/tests/test_project_migration.py` 新增“空实例创建 default project”测试
- [x] T010 [P] [US1] 在 `octoagent/packages/provider/tests/test_project_migration.py` 新增“legacy task/memory/import/backup metadata backfill”测试
- [x] T011 [P] [US1] 在 `octoagent/packages/provider/tests/test_project_migration.py` 新增“重复执行幂等”测试

### Implementation for US1

- [x] T012 [US1] 在 `octoagent/packages/provider/src/octoagent/provider/dx/project_migration.py` 实现 legacy metadata discovery
- [x] T013 [US1] 在 `octoagent/packages/provider/src/octoagent/provider/dx/project_migration.py` 实现 default project + primary workspace seed
- [x] T014 [US1] 在 `octoagent/packages/provider/src/octoagent/provider/dx/project_migration.py` 实现 scope/channel/memory/import/backup bindings backfill

**Checkpoint**: legacy 实例完成 default project migration

---

## Phase 4: User Story 2 — env compatibility bridge (Priority: P1)

**Goal**: legacy `.env` / `.env.litellm` / YAML env names 在 migration 后继续可用，并登记为 project-scoped bridge  
**Independent Test**: migration 后保留旧 env 解析，同时 bindings 中存在 env/file bridge 记录

### Tests for US2

- [x] T015 [P] [US2] 在 `octoagent/packages/provider/tests/test_project_migration.py` 新增 env refs / env files bridge 测试
- [x] T016 [P] [US2] 在 `octoagent/apps/gateway/tests/test_main.py` 新增 gateway startup 自动 ensure default project 测试

### Implementation for US2

- [x] T017 [US2] 在 `octoagent/packages/provider/src/octoagent/provider/dx/project_migration.py` 增加 env/file bridge discovery
- [x] T018 [US2] 在 `octoagent/apps/gateway/src/octoagent/gateway/main.py` 接入 startup ensure migration
- [x] T019 [US2] 在 `octoagent/packages/provider/src/octoagent/provider/dx/backup_service.py` 与 `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_service.py` 接入 ensure migration

**Checkpoint**: legacy env/runtime 路径与 project bridge 并存

---

## Phase 5: User Story 3 — validation / dry-run / rollback (Priority: P1)

**Goal**: migration 可 dry-run、可验证、可回滚  
**Independent Test**: dry-run 不写入；validation 失败自动 rollback；显式 rollback latest 可清理当前 run 产物

### Tests for US3

- [x] T020 [P] [US3] 在 `octoagent/packages/provider/tests/test_project_migration.py` 新增 validation failure -> rollback 测试
- [x] T021 [P] [US3] 在 `octoagent/packages/provider/tests/test_project_migration.py` 新增 rollback latest 测试
- [x] T022 [P] [US3] 在 `octoagent/packages/provider/tests/test_config.py` 或新 CLI 测试文件新增 `config migrate --dry-run / --rollback latest` 测试

### Implementation for US3

- [x] T023 [US3] 在 `octoagent/packages/provider/src/octoagent/provider/dx/project_migration.py` 实现 validation report 与 rollback plan
- [x] T024 [US3] 在 `octoagent/packages/provider/src/octoagent/provider/dx/project_migration.py` 实现 apply 失败自动 rollback
- [x] T025 [US3] 在 `octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py` 实现 `config migrate` 真正逻辑
- [x] T026 [US3] 在 `octoagent/packages/provider/src/octoagent/provider/dx/cli.py` / CLI 测试接入 migration 命令输出

**Checkpoint**: migration gate 完整具备 dry-run / validation / rollback

---

## Phase 6: Polish & Sync

- [ ] T027 回写 `docs/blueprint.md`，把 M3 first-phase gate 与 default project migration 事实对齐
- [ ] T028 回写 `docs/m3-feature-split.md`，恢复并同步 Feature 025 第一阶段与 Design Gates 口径
- [x] T029 [P] 运行 `ruff` 与定向 `pytest`
- [x] T030 使用 `/review` 思维做一次全面代码审查并修复发现
- [x] T031 更新 `verification/verification-report.md`

---

## Dependencies & Execution Order

- Phase 2 是所有实现的阻塞前提
- US1 完成后才能稳定做 US2 / US3
- US2 与 US3 可以在 domain/migration service 稳定后交错推进
- 文档回写与 verification 在全部实现完成后执行
