# Tasks: Feature 025 第二阶段 — Secret Store + Unified Config Wizard

**Input**: Design documents from `.specify/features/025-project-workspace-migration/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/project-cli.md`, `contracts/secret-store.md`, `contracts/wizard-cli-session.md`, `contracts/runtime-secret-reload.md`

**Tests**: 本阶段单元测试与关键集成测试均为必选项。

## Phase 1: Setup / Docs

- [x] T001 回写 `.specify/features/025-project-workspace-migration/*` 制品，锁定 025-B 范围、边界与测试矩阵
- [x] T002 [P] 回读 025-A / 024 / 026-A 制品与当前代码基线，确认复用边界

---

## Phase 2: Foundational (Blocking)

**Purpose**: 建立 025-B 的 canonical metadata 与 shared adapters，后续 project CLI / secrets / wizard 才有稳定落点

- [x] T003 在 `octoagent/packages/core/src/octoagent/core/models/project.py` 扩展 `SecretRefSourceType`、`SecretTargetKind`、`SecretBindingStatus`、`ProjectSecretBinding`、`ProjectSelectorState`
- [x] T004 在 `octoagent/packages/core/src/octoagent/core/store/project_store.py` 扩展 project secret bindings / selector state 的 CRUD 与查询 API
- [x] T005 在 `octoagent/packages/core/src/octoagent/core/store/sqlite_init.py` 增加 `project_secret_bindings` 与 `project_selector_state` 表
- [x] T006 在 `octoagent/packages/core/src/octoagent/core/models/__init__.py` 与 `octoagent/packages/core/src/octoagent/core/store/__init__.py` 导出新模型/新 store 能力
- [x] T007 在 `octoagent/packages/provider/src/octoagent/provider/dx/config_schema.py` 或相邻 adapter 中产出 026-A 语义兼容的 `ConfigSchemaDocument + uiHints`
- [x] T008 在 `octoagent/packages/provider/src/octoagent/provider/dx/secret_status_store.py` 建立 apply/materialization JSON 状态存储
- [x] T009 [P] 在 `octoagent/packages/core/tests/` 与 `octoagent/packages/provider/tests/` 新增 model/store/schema adapter 覆盖

**Checkpoint**: 025-B 的 canonical metadata、state store 与 contract adapter ready

---

## Phase 3: User Story 1 — Project CLI Main Path (Priority: P1) 🎯 MVP

**Goal**: 用户能正式 `create/select/edit/inspect` project，并有 active project 语义  
**Independent Test**: 在多 project 测试目录中完成 create/select/inspect，并让后续 wizard/secrets 正确使用 active project

### Tests for US1

- [x] T010 [P] [US1] 在 `octoagent/packages/provider/tests/dx/test_project_commands.py` 新增 `project create/select/inspect` happy path 测试
- [x] T011 [P] [US1] 在 `octoagent/packages/provider/tests/dx/test_project_commands.py` 新增 active project 切换 / 缺失 project / warning summary 测试

### Implementation for US1

- [x] T012 [US1] 新增 `octoagent/packages/provider/src/octoagent/provider/dx/project_selector.py`，封装 active project 读写与 selector summary
- [x] T013 [US1] 新增 `octoagent/packages/provider/src/octoagent/provider/dx/project_commands.py`，实现 `project create`
- [x] T014 [US1] 在 `octoagent/packages/provider/src/octoagent/provider/dx/project_commands.py` 实现 `project select`
- [x] T015 [US1] 在 `octoagent/packages/provider/src/octoagent/provider/dx/project_commands.py` 实现 `project inspect` 的 redacted readiness/binding summary
- [x] T016 [US1] 在 `octoagent/packages/provider/src/octoagent/provider/dx/cli.py` 注册 `project` 命令组

**Checkpoint**: project 成为正式 CLI 操作对象

---

## Phase 4: User Story 2 — Secret Store Lifecycle (Priority: P1)

**Goal**: 用户能对当前 project 执行 `audit/configure/apply/rotate`，并通过 `SecretRef` 管理 provider/channel/gateway/runtime secrets  
**Independent Test**: `env/file/exec/keychain` 四类 source 均可完成 audit/configure/apply/rotate 的 happy path 和 failure path

### Tests for US2

- [x] T017 [P] [US2] 在 `octoagent/packages/provider/tests/dx/test_secret_refs.py` 覆盖 `env/file/exec/keychain` 解析与错误分类
- [x] T018 [P] [US2] 在 `octoagent/packages/provider/tests/dx/test_secret_service.py` 覆盖 `audit/configure/apply --dry-run/apply/rotate`
- [x] T019 [P] [US2] 在 `octoagent/packages/provider/tests/dx/test_secret_service.py` 覆盖 legacy env bridge / provider auth bridge / current project binding 优先级
- [x] T020 [P] [US2] 在 `octoagent/packages/provider/tests/dx/test_secret_service.py` 覆盖“不泄露 secret 明文”的日志/序列化回归测试

### Implementation for US2

- [x] T021 [US2] 新增 `octoagent/packages/provider/src/octoagent/provider/dx/secret_refs.py`，实现 `SecretRef` validation、resolution 与 redaction helper
- [x] T022 [US2] 新增 `octoagent/packages/provider/src/octoagent/provider/dx/secret_service.py`，实现 `audit/configure/apply/rotate`
- [x] T023 [US2] 在 `octoagent/packages/provider/src/octoagent/provider/dx/secret_service.py` 实现 provider auth profile / legacy env bridge 收敛到 project secret binding 的逻辑
- [x] T024 [US2] 新增 `octoagent/packages/provider/src/octoagent/provider/dx/secret_commands.py`，实现 `octo secrets audit/configure/apply/rotate`
- [x] T025 [US2] 在 `octoagent/packages/provider/src/octoagent/provider/dx/cli.py` 注册 `secrets` 命令组
- [x] T026 [US2] 在 `octoagent/pyproject.toml` 与 `octoagent/uv.lock` 引入并锁定 `keyring` 依赖，保持无 backend 时可降级

**Checkpoint**: project-scoped secret lifecycle 闭环可用

---

## Phase 5: User Story 3 — Unified Wizard Session in CLI (Priority: P1)

**Goal**: `project edit --wizard` 变成统一、可恢复、可取消的 CLI wizard 主路径  
**Independent Test**: 用户可 start/resume/status/cancel wizard，并消费同一 `ConfigSchemaDocument + uiHints`

### Tests for US3

- [x] T027 [P] [US3] 在 `octoagent/packages/provider/tests/dx/test_wizard_session.py` 覆盖 start/resume/status/cancel
- [x] T028 [P] [US3] 在 `octoagent/packages/provider/tests/dx/test_wizard_session.py` 覆盖 `ConfigSchemaDocument + uiHints` CLI 消费与 unsupported hints 降级
- [x] T029 [P] [US3] 在 `octoagent/packages/provider/tests/dx/test_project_commands.py` 覆盖 `project edit --wizard` 与 active project 集成

### Implementation for US3

- [x] T030 [US3] 新增 `octoagent/packages/provider/src/octoagent/provider/dx/wizard_session.py`，定义 026-A 对齐的 CLI wizard adapter / producer
- [x] T031 [US3] 新增 `octoagent/packages/provider/src/octoagent/provider/dx/wizard_session_store.py`，持久化 wizard state
- [x] T032 [US3] 在 `octoagent/packages/provider/src/octoagent/provider/dx/project_commands.py` 实现 `project edit --wizard`
- [x] T033 [US3] 在 `octoagent/packages/provider/src/octoagent/provider/dx/cli.py` 中把旧 `init` 调整为兼容 alias、迁移提示或 thin wrapper，避免继续走旧脚本主路径

**Checkpoint**: CLI 统一 wizard session ready

---

## Phase 6: User Story 4 — Runtime Materialization & Reload (Priority: P2)

**Goal**: 当前 project 的 secret 可通过 runtime short-lived injection 生效，并用 024 managed runtime 基线执行 reload  
**Independent Test**: managed runtime 可完成 `reload -> restart/verify`；unmanaged runtime 明确返回 `action_required/degraded`

### Tests for US4

- [x] T034 [P] [US4] 在 `octoagent/packages/provider/tests/dx/test_secret_service.py` 覆盖 materialization summary 只输出 env names / redacted metadata
- [x] T035 [P] [US4] 在 `octoagent/packages/provider/tests/dx/test_secret_service.py` 覆盖 managed runtime `reload` 路径
- [x] T036 [P] [US4] 在 `octoagent/packages/provider/tests/dx/test_secret_service.py` 覆盖 unmanaged runtime degrade path
- [x] T037 [P] [US4] 在 `octoagent/packages/provider/tests/test_doctor.py` 或 `test_onboarding_service.py` 覆盖“bindings 已更新但 runtime 未同步”的状态提示

### Implementation for US4

- [x] T038 [US4] 在 `octoagent/packages/provider/src/octoagent/provider/dx/secret_service.py` 实现 runtime short-lived materialization summary
- [x] T039 [US4] 在 `octoagent/packages/provider/src/octoagent/provider/dx/secret_commands.py` 实现 `octo secrets reload`
- [x] T040 [US4] 在 `octoagent/packages/provider/src/octoagent/provider/dx/secret_service.py` 复用 `UpdateService.restart()` + `verify()` 完成 managed runtime reload
- [x] T041 [US4] 在 `octoagent/packages/provider/src/octoagent/provider/dx/doctor.py`、`onboarding_service.py`、`project_commands.py` 接入 secret readiness / runtime sync 状态

**Checkpoint**: project secret 真正可生效并可诊断

---

## Phase 7: Polish & Verification

- [x] T042 [P] 运行 `ruff` 与 025-B 受影响测试集
- [x] T043 [P] 运行包含 025-A / 024 / 016 受影响面的关键回归集
- [x] T044 回写 `verification/verification-report.md`
- [x] T045 评估 `docs/blueprint.md` / `docs/m3-feature-split.md` 的 025-B 实施状态；本次无需额外回写
- [x] T046 使用 `/review` 思维做一次全面代码审查并修复发现

## Dependencies & Execution Order

- Phase 2 是所有后续实现的阻塞前提
- US1 与 US2 可在 shared domain ready 后并行推进，但 `active project` 完成后 US2 实施会更稳定
- US3 依赖 US1 的 selector 语义与 Phase 2 的 schema adapter
- US4 依赖 US2 的 secret binding 与 apply/materialization summary
- Verification 在全部实现完成后执行
