# Tasks: Feature 024 — Installer + Updater + Doctor/Migrate

**Input**: `.specify/features/024-installer-updater-doctor-migrate/`
**Prerequisites**: `spec.md`, `plan.md`, `data-model.md`, `contracts/*.md`
**Created**: 2026-03-08
**Status**: Implemented

**Task Format**: `- [ ] T{三位数} [P0/P1] [P?] [USN?] 描述 -> 文件路径`

- `[P0]` / `[P1]`: 交付优先级（P0 为 024 主闭环阻塞项）
- `[P]`: 可并行执行（不同文件、无硬前置依赖）
- `[B]`: 阻塞后续关键路径
- `[USN]`: 所属 User Story（US1-US4）；Setup/Foundational 阶段不标注
- `[SKIP]`: 明确不在 024 落地

---

## Phase 1: Setup（模块边界与共享契约落点）

**目标**: 先冻结 024 的共享模型、CLI 接线和 Web 扩展位置，避免实现阶段反复搬边界。

- [ ] T001 [P0] [B] 在 `core.models` 中增加 024 共享 contract 导出骨架：`update.py` + `__init__.py` 导出 -> `octoagent/packages/core/src/octoagent/core/models/`

- [ ] T002 [P0] [P] 创建 provider/dx 侧 024 模块骨架：`install_bootstrap.py`、`update_commands.py`、`update_service.py`、`update_status_store.py`、`update_worker.py` -> `octoagent/packages/provider/src/octoagent/provider/dx/`

- [ ] T003 [P0] [P] 创建 installer 薄壳脚本与测试骨架 -> `octoagent/scripts/install-octo.sh`、`octoagent/packages/provider/tests/test_install_bootstrap.py`

- [ ] T004 [P0] [P] 创建 gateway / frontend 对应测试或类型骨架，固定 024 的 Web 接线范围 -> `octoagent/apps/gateway/tests/test_ops_api.py`、`test_main.py`、`octoagent/frontend/src/types/index.ts`

**Checkpoint**: 024 的代码落点和共享 contract 边界已经稳定。

---

## Phase 2: Foundational（数据模型 / 状态存储 / runtime descriptor 冻结）

**目标**: 先把 update state、runtime descriptor 和 failure report 冻结；所有 CLI/Web 行为都建立在这一层之上。

> **警告**: Phase 2 未完成前，不得开始 `octo update` 或 Web 按钮实现

- [ ] T005 [P0] [B] 实现 `ManagedRuntimeDescriptor`、`RuntimeStateSnapshot`、`UpdateAttempt`、`UpdatePhaseResult`、`MigrationStepResult`、`UpgradeFailureReport`、`UpdateAttemptSummary` 模型 -> `octoagent/packages/core/src/octoagent/core/models/update.py`

- [ ] T006 [P0] [B] 实现 `UpdateStatusStore`，支持 descriptor/runtime-state/latest-update/active-update 的原子读写、锁保护和损坏文件降级 -> `octoagent/packages/provider/src/octoagent/provider/dx/update_status_store.py`

- [ ] T007 [P0] [P] 为 update models / status store 编写单元测试，覆盖序列化、损坏文件回退、active/latest attempt 语义 -> `octoagent/packages/provider/tests/test_update_status_store.py`、`octoagent/packages/core/tests/test_models.py`

**Checkpoint**: 024 的 durability 与共享状态面冻结，可分线并行推进 installer / CLI / Web。

---

## Phase 3: User Story 1 — 新用户有一条一键安装入口（Priority: P1）

**目标**: 用户可以通过官方安装入口完成最小依赖准备，并拿到后续动作与 runtime descriptor。

**Independent Test**: 在临时项目根执行 installer，验证能完成 `uv sync`、descriptor 写入与下一步指引；重复执行不破坏现有实例。

- [ ] T008 [P0] [US1] [B] 实现 `install_bootstrap.py`，完成依赖检查、项目根校验、`uv sync`、可选前端准备、`ManagedRuntimeDescriptor` 写入与 `InstallAttempt` 返回 -> `octoagent/packages/provider/src/octoagent/provider/dx/install_bootstrap.py`

- [ ] T009 [P0] [US1] 实现 `scripts/install-octo.sh` 薄壳，调用 Python bootstrap 逻辑并正确传递参数 -> `octoagent/scripts/install-octo.sh`

- [ ] T010 [P0] [US1] 为 installer 编写测试，覆盖：依赖缺失、已安装幂等、`--force` 覆盖、`--skip-frontend`、descriptor 内容 -> `octoagent/packages/provider/tests/test_install_bootstrap.py`

**Checkpoint**: 024 已交付官方安装入口与 managed runtime 初始化能力。

---

## Phase 4: User Story 2 — 已安装实例可以安全执行 `octo update`（Priority: P1）

**目标**: 用户可以通过 `octo update --dry-run` 和真实 `octo update` 执行阶段化升级。

**Independent Test**: 对 managed runtime 执行 `octo update --dry-run`，验证只读 preview；执行真实 update，验证 `preflight -> migrate -> restart -> verify` 状态按阶段推进。

- [ ] T011 [P0] [US2] [B] 在 CLI 中注册 `octo update`、`octo restart`、`octo verify` 命令，并统一 Rich 摘要输出和退出码语义 -> `octoagent/packages/provider/src/octoagent/provider/dx/update_commands.py`、`cli.py`

- [ ] T012 [P0] [US2] [B] 实现 `UpdateService.preview()`：复用 `DoctorRunner.run_all_checks(live=False)`、runtime descriptor 检查、migration registry preview，确保 dry-run 无 destructive 副作用 -> `octoagent/packages/provider/src/octoagent/provider/dx/update_service.py`

- [ ] T013 [P0] [US2] [B] 实现 migration registry 与 `UpdateService.apply()` 的 preflight/migrate 编排，首批至少覆盖 workspace sync、`octo config migrate`、可选前端 build -> `octoagent/packages/provider/src/octoagent/provider/dx/update_service.py`

- [ ] T014 [P0] [US2] 为 `octo update --dry-run` 与真实 `octo update` 编写 CLI/service 测试，覆盖成功路径、doctor 阻塞、registry 短路、dry-run 无副作用 -> `octoagent/packages/provider/tests/test_update_commands.py`、`test_update_service.py`

**Checkpoint**: CLI 已具备正式 update 入口和可解释 dry-run。

---

## Phase 5: User Story 3 — 升级失败时必须拿到结构化报告（Priority: P1）

**目标**: update 失败时系统必须生成统一 failure report，并在 restart/verify 后仍保留 canonical 状态。

**Independent Test**: 构造 migrate 失败、restart 失败、verify 超时三类场景，验证 `UpgradeFailureReport` 和 `latest-update.json` 正确落盘。

- [ ] T015 [P0] [US3] [B] 实现 detached `update_worker.py`，让真实 apply 能跨 restart 存活，并在每个阶段刷新 `active-update.json` / `latest-update.json` -> `octoagent/packages/provider/src/octoagent/provider/dx/update_worker.py`

- [ ] T016 [P0] [US3] [B] 实现 restart / verify 领域逻辑：managed runtime restart、runtime-state 读取、`/ready` 轮询、verify 超时/失败分支 -> `octoagent/packages/provider/src/octoagent/provider/dx/update_service.py`

- [ ] T017 [P0] [US3] [P] 在 gateway 启动时写入 `RuntimeStateSnapshot`，保证 worker 能读取当前 pid / verify_url / active attempt -> `octoagent/apps/gateway/src/octoagent/gateway/main.py`

- [ ] T018 [P0] [US3] 为 failure report / worker / verify 编写测试，覆盖：unmanaged runtime、restart 不可用、verify timeout、failure report 携带 recovery 线索 -> `octoagent/packages/provider/tests/test_update_service.py`、`octoagent/apps/gateway/tests/test_main.py`

**Checkpoint**: 024 的失败可诊断、状态可恢复链路成立。

---

## Phase 6: User Story 4 — Web recovery 面板能触发 update / restart / verify（Priority: P2）

**目标**: 现有 RecoveryPanel 成为 024 的最小 Web 运维入口，而不是新建第二套控制台。

**Independent Test**: 从 Web API 触发 update dry-run、真实 apply、restart、verify，验证状态和失败摘要能被 RecoveryPanel 读取。

- [ ] T019 [P0] [US4] [B] 在 `routes/ops.py` 上新增 `GET /api/ops/update/status`、`POST /api/ops/update/dry-run`、`POST /api/ops/update/apply`、`POST /api/ops/restart`、`POST /api/ops/verify` -> `octoagent/apps/gateway/src/octoagent/gateway/routes/ops.py`

- [ ] T020 [P0] [US4] 扩展前端 API client 与类型定义，补 `UpdateAttemptSummary` / `UpgradeFailureReport` 对应 TS 类型 -> `octoagent/frontend/src/api/client.ts`、`octoagent/frontend/src/types/index.ts`

- [ ] T021 [P0] [US4] 扩展 `RecoveryPanel`，新增 update 区块、状态展示、失败摘要、轮询逻辑，同时保持原 backup/export 动作不回归 -> `octoagent/frontend/src/components/RecoveryPanel.tsx`

- [ ] T022 [P0] [US4] 为 gateway ops API 编写测试，覆盖 status、dry-run、apply 并发保护、restart/verify 错误码与 summary 结构 -> `octoagent/apps/gateway/tests/test_ops_api.py`

**Checkpoint**: 024 的 Web 最小控制面完成。

---

## Phase 7: Verification / 回归与收口

**目标**: 用自动化验证把 installer、update flow、Web recovery 入口和失败报告固定住。

- [ ] T023 [P0] [B] 运行 targeted tests：provider update/install/store、gateway ops/main -> `octoagent/packages/provider/tests/`、`octoagent/apps/gateway/tests/`

- [ ] T024 [P0] [P] 运行 022 相关回归，确认 backup/export/recovery summary 不被 024 破坏 -> `octoagent/packages/provider/tests/test_backup_service.py`、`octoagent/apps/gateway/tests/test_ops_api.py`

- [ ] T025 [P0] [P] 更新 verification 制品并回填任务状态 -> `.specify/features/024-installer-updater-doctor-migrate/verification/`

---

## Deferred / Boundary Tasks

- [ ] T026 [P1] [SKIP] 引入 Project/Workspace-aware update -> Feature 025 处理
  **SKIP 原因**: 024 明确不引入 project/workspace 模型

- [ ] T027 [P1] [SKIP] 引入 Secret Store 或统一配置中心 -> Feature 025 处理
  **SKIP 原因**: 024 只复用现有 `octo config` / `octo doctor` 基线

- [ ] T028 [P1] [SKIP] 实现完整 Session Center / Scheduler / Runtime Console -> Feature 026 处理
  **SKIP 原因**: 024 只扩展现有 RecoveryPanel

- [ ] T029 [P1] [SKIP] 实现多节点或零停机升级 -> 后续 M3 Feature 处理
  **SKIP 原因**: 当前只做单机/单实例 bounded downtime 升级

---

## 并行建议

在 Phase 2 完成后，可按最大并行拆成三条线：

1. `installer 线`：T008-T010
2. `CLI/update 线`：T011-T018
3. `Web 接线线`：T019-T022

唯一硬前置是：T005-T007（共享模型 / 状态存储）完成后，再进入三线并行。
