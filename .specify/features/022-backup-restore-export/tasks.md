# Tasks: Feature 022 — Backup/Restore + Export + Recovery Drill

**Input**: `.specify/features/022-backup-restore-export/`
**Prerequisites**: `spec.md`, `plan.md`, `data-model.md`, `contracts/*.md`
**Created**: 2026-03-07
**Status**: Ready

**Task Format**: `- [ ] T{三位数} [P0/P1] [P?] [USN?] 描述 → 文件路径`

- `[P0]` / `[P1]`: 交付优先级（P0 为 M2 recovery 主闭环阻塞项）
- `[P]`: 可并行执行（不同文件、无前置依赖）
- `[B]`: 阻塞后续关键路径
- `[USN]`: 所属 User Story（US1–US4）；Setup/Foundational 阶段不标注
- `[SKIP]`: 明确不在 022 落地

---

## Phase 1: Setup（模块边界与骨架）

**目标**: 先把 022 的共享边界固定住，避免后续把 bundle、dry-run、Web 状态分别写成三套语义。

- [x] T001 [P0] [B] 创建 022 所需模块骨架：`backup.py`、`backup_commands.py`、`backup_service.py`、`recovery_status_store.py`、`backup_audit.py`、`routes/ops.py`、`components/RecoveryPanel.tsx` → `octoagent/packages/core/src/octoagent/core/models/`、`octoagent/packages/provider/src/octoagent/provider/dx/`、`octoagent/apps/gateway/src/octoagent/gateway/routes/`、`octoagent/frontend/src/components/`

- [x] T002 [P0] [P] 创建对应测试文件骨架，保证后续可并行补测 → `octoagent/packages/core/tests/test_backup_models.py`、`octoagent/packages/provider/tests/test_backup_service.py`、`test_backup_commands.py`、`test_recovery_status_store.py`、`octoagent/apps/gateway/tests/test_ops_api.py`

**Checkpoint**: 代码落点与测试入口清晰，可进入模型与状态层实现

---

## Phase 2: Foundational（共享 schema / 状态文件 / 契约冻结）

**目标**: 先冻结 CLI/Web 共用的 domain model 和状态文件；任何用户故事都建立在这一层之上。

> **警告**: Phase 2 未完成前，不得开始 backup create / dry-run / Web 面板实现

- [x] T003 [P0] [B] 实现 `BackupScope`、`SensitivityLevel`、`RestoreConflictType`、`RecoveryDrillStatus` 及 `BackupManifest`、`BackupBundle`、`RestorePlan`、`ExportManifest`、`RecoveryDrillRecord` 模型 → `octoagent/packages/core/src/octoagent/core/models/backup.py`

- [x] T004 [P0] [B] 更新 `core.models.__init__`、必要的 `EventType` / `payloads` 导出，补齐 022 所需共享 schema 出口 → `octoagent/packages/core/src/octoagent/core/models/__init__.py`、`enums.py`、`payloads.py`

- [x] T005 [P0] 为 core backup models 编写单元测试，覆盖枚举值、序列化、默认值和 `RestorePlan.compatible` 判定 → `octoagent/packages/core/tests/test_backup_models.py`、`test_models.py`

- [x] T006 [P0] [B] 实现 `RecoveryStatusStore`（`latest-backup.json` / `recovery-drill.json` 原子写入、损坏备份、默认空状态） → `octoagent/packages/provider/src/octoagent/provider/dx/recovery_status_store.py`

- [x] T007 [P0] 为 `RecoveryStatusStore` 编写测试，覆盖首次加载、写入、损坏恢复、默认 `NOT_RUN` 语义 → `octoagent/packages/provider/tests/test_recovery_status_store.py`

- [x] T008 [P0] [P] 冻结 bundle layout / manifest serializer 辅助函数，确保 backup create / restore dry-run / gateway API 消费同一格式 → `octoagent/packages/provider/src/octoagent/provider/dx/backup_service.py`

**Checkpoint**: 022 的共享 schema 和状态源已冻结，可分线并行

---

## Phase 3: User Story 1 — 自助创建可解释的备份包（Priority: P1）

**目标**: 用户通过 `octo backup create` 一次命令拿到可迁移 bundle 和清晰摘要

**Independent Test**: 在真实 tmp 项目目录中运行 `octo backup create`，生成包含 SQLite 快照、config metadata、artifact 内容和 manifest 的 ZIP bundle

- [x] T009 [P0] [US1] [B] 在 `BackupService` 中实现 SQLite 在线快照、config metadata 收集、artifact 遍历和 ZIP bundle 写入 → `octoagent/packages/provider/src/octoagent/provider/dx/backup_service.py`

- [x] T010 [P0] [US1] [B] 在 backup create 流程中生成 manifest、sensitivity summary，并更新 `latest-backup.json` → `octoagent/packages/provider/src/octoagent/provider/dx/backup_service.py`、`recovery_status_store.py`

- [x] T011 [P0] [US1] [P] 实现 backup 生命周期审计封装（优先 Event Store operational task；至少保留结构化 payload 统一） → `octoagent/packages/provider/src/octoagent/provider/dx/backup_audit.py`、`octoagent/packages/core/src/octoagent/core/models/payloads.py`

- [x] T012 [P0] [US1] 在 CLI 中注册 `octo backup create`，实现 `--output` / `--label` 参数和 Rich 摘要输出 → `octoagent/packages/provider/src/octoagent/provider/dx/backup_commands.py`、`cli.py`

- [x] T013 [P0] [US1] 为 `BackupService.create_bundle()` 编写单元测试，覆盖默认排除 `.env`、SQLite 快照存在、manifest 条目完整、latest-backup 更新 → `octoagent/packages/provider/tests/test_backup_service.py`

- [x] T014 [P0] [US1] 为 `octo backup create` 编写 CLI 测试，覆盖默认输出目录、自定义输出路径、不可写路径失败 → `octoagent/packages/provider/tests/test_backup_commands.py`

**Checkpoint**: backup create 已成为稳定入口，并固化最近一次 backup 状态

---

## Phase 4: User Story 2 — 恢复前先看到 dry-run 计划（Priority: P1）

**目标**: restore 先做 preview，用户能在真正恢复前看到风险与建议动作

**Independent Test**: 使用有效/损坏 bundle、目标路径冲突和版本不兼容三类场景，验证 `octo restore dry-run` 输出结构化 `RestorePlan`

- [x] T015 [P0] [US2] [B] 在 `BackupService` 中实现 `plan_restore()`：校验 ZIP、manifest、schema version、关键文件和路径冲突 → `octoagent/packages/provider/src/octoagent/provider/dx/backup_service.py`

- [x] T016 [P0] [US2] [B] 实现 `RestoreConflict` 分类与 `RecoveryDrillRecord` 更新逻辑，让 dry-run 结果同步写入 `recovery-drill.json` → `octoagent/packages/provider/src/octoagent/provider/dx/backup_service.py`、`recovery_status_store.py`

- [x] T017 [P0] [US2] 在 CLI 中注册 `octo restore dry-run --bundle ... [--target-root ...]`，输出 blocking conflicts / warnings / next actions 摘要 → `octoagent/packages/provider/src/octoagent/provider/dx/backup_commands.py`、`cli.py`

- [x] T018 [P0] [US2] 为 `plan_restore()` 编写测试，覆盖 manifest 缺失、schema version 不兼容、目标配置已存在、空目标目录成功等场景 → `octoagent/packages/provider/tests/test_backup_service.py`

- [x] T019 [P0] [US2] 为 `octo restore dry-run` 编写 CLI 测试，断言返回码 0/1/2 语义和 `recovery-drill.json` 更新 → `octoagent/packages/provider/tests/test_backup_commands.py`

**Checkpoint**: restore preview-first 路径已稳定，recovery drill 状态开始可见

---

## Phase 5: User Story 3 — 导出 chats/session 记录（Priority: P1）

**目标**: 用户不碰数据库，也能导出 thread/task 级别的聊天和任务记录

**Independent Test**: 创建至少一个 web chat task 后执行 `octo export chats`，验证导出 manifest 记录 task/thread、事件数与 artifact 引用

- [x] T020 [P0] [US3] [B] 在 `BackupService` 中实现 `export_chats()`：基于 task/event/artifact 投影按 task/thread/时间窗口筛选 → `octoagent/packages/provider/src/octoagent/provider/dx/backup_service.py`

- [x] T021 [P0] [US3] 在 CLI 中注册 `octo export chats`，实现筛选参数与输出路径选项 → `octoagent/packages/provider/src/octoagent/provider/dx/backup_commands.py`、`cli.py`

- [x] T022 [P0] [US3] 为 `export_chats()` 编写测试，覆盖指定 thread、空结果导出、artifact refs 输出 → `octoagent/packages/provider/tests/test_backup_service.py`

- [x] T023 [P0] [US3] 为 `octo export chats` 编写 CLI 测试，断言导出 manifest 路径、空结果返回码和筛选边界落盘 → `octoagent/packages/provider/tests/test_backup_commands.py`

**Checkpoint**: chats export 已具备独立 CLI 闭环，且不依赖 021

---

## Phase 6: User Story 4 — 明确看到最近一次恢复演练状态（Priority: P2）

**目标**: CLI/Web 能读同一份恢复准备度状态，普通操作者无需翻日志

**Independent Test**: gateway API 返回最近 backup / recovery drill 摘要；TaskList 首页显示 RecoveryPanel，并能触发 backup/export

- [x] T024 [P1] [US4] [B] 新增 `GET /api/ops/recovery`，返回 `RecoverySummary`，读取 `latest-backup.json` 与 `recovery-drill.json` → `octoagent/apps/gateway/src/octoagent/gateway/routes/ops.py`、`main.py`

- [x] T025 [P1] [US4] [P] 新增 `POST /api/ops/backup/create` 与 `POST /api/ops/export/chats`，通过 gateway 触发 provider DX 服务 → `octoagent/apps/gateway/src/octoagent/gateway/routes/ops.py`

- [x] T026 [P1] [US4] 扩展 gateway 路由测试，覆盖 recovery summary、backup create、export chats 三个接口 → `octoagent/apps/gateway/tests/test_ops_api.py`

- [x] T027 [P1] [US4] 扩展前端类型与 API client，新增 recovery summary / backup create / export chats 请求封装 → `octoagent/frontend/src/types/index.ts`、`api/client.ts`

- [x] T028 [P1] [US4] 实现 `RecoveryPanel` 组件，展示最近 backup / 最近 recovery drill / 最近失败原因 / 两个操作按钮 → `octoagent/frontend/src/components/RecoveryPanel.tsx`

- [x] T029 [P1] [US4] 将 `RecoveryPanel` 接入 `TaskList`，保持现有任务列表不回归，并对触发结果给出最小反馈 → `octoagent/frontend/src/pages/TaskList.tsx`、`index.css`

**Checkpoint**: Web 最小 recovery 入口完成，CLI/Web 状态保持一致

---

## Phase 7: E2E / 回归与边界保护

**目标**: 用自动化验证把 022 的主闭环、状态持久化和最小 Web 入口固定住

- [x] T030 [P0] [B] 编写 provider 集成测试：backup create -> restore dry-run -> recovery-drill 更新 -> export chats → `octoagent/packages/provider/tests/test_backup_service.py`、`test_backup_commands.py`

- [x] T031 [P0] [P] 扩展 gateway 健康/ops 相关测试，验证未执行 dry-run 时 `ready_for_restore=false`、执行后状态翻转 → `octoagent/apps/gateway/tests/test_ops_api.py`、`test_us12_health.py`

- [x] T032 [P0] [P] 执行回归验证：core tests、provider tests、gateway tests、frontend `vite build`，确认 014/015/012 无回归 → `octoagent/packages/core/tests/`、`octoagent/packages/provider/tests/`、`octoagent/apps/gateway/tests/`、`octoagent/frontend/`

---

## Deferred / Boundary Tasks

- [ ] T033 [P1] [SKIP] 实现 destructive restore apply → 后续 Feature 或 M3 处理
  **SKIP 原因**: 022 只交付 preview-first 恢复能力

- [ ] T034 [P1] [SKIP] 接入 NAS/S3/Litestream 远程同步 → 后续里程碑处理
  **SKIP 原因**: 超出单机自助恢复范围

---

## 并行建议

在 Phase 2 完成后，可按最大并发拆成三条线：

1. `backup create` 线：T009-T014
2. `restore dry-run` 线：T015-T019
3. `export + web` 线：T020-T029

唯一硬前置是：T003-T008（共享 schema / 状态文件 / manifest 约定）完成后，再进入三线并行推进。
