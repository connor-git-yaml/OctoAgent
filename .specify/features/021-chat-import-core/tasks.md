# Tasks: Feature 021 — Chat Import Core

**Input**: `.specify/features/021-chat-import-core/`
**Prerequisites**: `spec.md`, `plan.md`, `data-model.md`, `contracts/*.md`
**Created**: 2026-03-07
**Status**: Completed

**Task Format**: `- [ ] T{三位数} [P0/P1] [P?] [USN?] 描述 -> 文件路径`

- `[P0]` / `[P1]`: 交付优先级（P0 为 M2 Chat Import 主闭环阻塞项）
- `[P]`: 可并行执行（不同文件、无硬前置依赖）
- `[B]`: 阻塞后续关键路径
- `[USN]`: 所属 User Story（US1-US4）；Setup/Foundational 阶段不标注
- `[SKIP]`: 明确不在 021 落地

---

## Phase 1: Setup（模块边界与依赖接线）

**目标**: 先把 021 的落点、workspace 依赖和测试入口固定住，避免实现阶段跨 `core/provider/memory` 反复搬代码。

- [x] T001 [P0] [B] 为 provider 增加 `octoagent-memory` workspace 依赖，确保 `provider/dx` 可以直接编排 020 memory contract -> `octoagent/packages/provider/pyproject.toml`

- [x] T002 [P0] [P] 创建 021 所需模块骨架：`imports/__init__.py`、`models.py`、`sqlite_init.py`、`store.py`、`service.py`、`chat_import_commands.py`、`chat_import_service.py` -> `octoagent/packages/memory/src/octoagent/memory/imports/`、`octoagent/packages/provider/src/octoagent/provider/dx/`

- [x] T003 [P0] [P] 创建对应测试文件骨架，保证后续可并行补测 -> `octoagent/packages/memory/tests/test_import_models.py`、`test_import_store.py`、`test_import_service.py`、`octoagent/packages/provider/tests/test_chat_import_commands.py`、`test_chat_import_service.py`

**Checkpoint**: 021 的代码落点和 package 依赖已经稳定，可进入共享 schema 设计

---

## Phase 2: Foundational（共享 schema / 输入契约 / 审计事件冻结）

**目标**: 先冻结 generic input、导入 durability schema 和 lifecycle event contract；任何用户故事都建立在这一层之上。

> **警告**: Phase 2 未完成前，不得开始 CLI preview 或真实导入实现

- [x] T004 [P0] [B] 实现 `ImportedChatMessage`、`ImportFactHint`、`ImportBatch`、`ImportCursor`、`ImportDedupeEntry`、`ImportWindow`、`ImportSummary`、`ImportReport` 模型 -> `octoagent/packages/memory/src/octoagent/memory/imports/models.py`

- [x] T005 [P0] [B] 实现 `chat_import_*` SQLite schema 初始化与唯一约束，覆盖 batch/cursor/dedupe/window/report 五张表 -> `octoagent/packages/memory/src/octoagent/memory/imports/sqlite_init.py`

- [x] T006 [P0] [B] 实现 import store CRUD，至少支持：创建/更新 batch、读取/更新 cursor、写 dedupe entry、写 window、写 report -> `octoagent/packages/memory/src/octoagent/memory/imports/store.py`

- [x] T007 [P0] [P] 在 core 中新增 `CHAT_IMPORT_STARTED/COMPLETED/FAILED` 事件类型与 `ChatImportLifecyclePayload` -> `octoagent/packages/core/src/octoagent/core/models/enums.py`、`payloads.py`

- [x] T008 [P0] [P] 为 import models / schema / payloads 编写单元测试，覆盖序列化、唯一键、dry-run 无持久化前提所需的只读接口 -> `octoagent/packages/memory/tests/test_import_models.py`、`octoagent/packages/core/tests/test_models.py`

- [x] T009 [P1] 同步回写 blueprint / M2 拆解文档，补上 021 的 CLI 入口、dry-run 预览和 ImportReport 要求 -> `docs/blueprint.md`、`docs/m2-feature-split.md`

**Checkpoint**: 021 的共享 contract 已冻结，可分线并行推进 preview / durable import / governed fact write

---

## Phase 3: User Story 1 — 先预览，再安全导入历史聊天（Priority: P1）

**目标**: 用户可以先 dry-run 看清楚会写入什么，再决定是否执行真实导入

**Independent Test**: 使用一份带重复消息的 `normalized-jsonl` 文件，先执行 `--dry-run`，确认返回新增/重复/窗口统计且无副作用

- [x] T010 [P0] [US1] [B] 实现 `normalized-jsonl` 解析器、CLI override 合并和 message key 生成规则 -> `octoagent/packages/memory/src/octoagent/memory/imports/service.py`

- [x] T011 [P0] [US1] [B] 实现 dry-run 只读路径：读取 dedupe/cursor、计算窗口、生成 `ImportReport(dry_run=true)`，不写任何持久化副作用 -> `octoagent/packages/memory/src/octoagent/memory/imports/service.py`

- [x] T012 [P0] [US1] 在 CLI 中注册 `octo import chats`，实现 `--input`、`--format`、`--source-id`、`--channel`、`--thread-id`、`--dry-run`、`--resume` 参数和 Rich 摘要输出 -> `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_commands.py`、`cli.py`

- [x] T013 [P0] [US1] 为 dry-run 编写命令与服务测试，覆盖：输入文件不存在、schema 错误、重复消息命中、CLI override 生效、无副作用断言 -> `octoagent/packages/provider/tests/test_chat_import_commands.py`、`test_chat_import_service.py`、`octoagent/packages/memory/tests/test_import_service.py`

**Checkpoint**: 用户已具备真实可用的导入预览入口

---

## Phase 4: User Story 2 — 导入后的聊天必须隔离且可审计（Priority: P1）

**目标**: 真实导入后，原文、摘要、事件链和 scope 隔离都成立

**Independent Test**: 导入一个 thread 的历史聊天后，验证 raw messages 进入 artifact、summary 进入 fragment、scope 独立且事件链可回放

- [x] T014 [P0] [US2] [B] 实现 provider-facing `ChatImportService`，在同一连接上执行 `create_store_group()` 后补 `init_memory_db(conn)` 与 `init_chat_import_db(conn)`，避免 core 反向依赖 memory -> `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_service.py`

- [x] T015 [P0] [US2] [B] 实现真实导入路径中的 batch / report / cursor / dedupe 持久化闭环 -> `octoagent/packages/memory/src/octoagent/memory/imports/service.py`、`store.py`

- [x] T016 [P0] [US2] [B] 把原始窗口写入 `ops-chat-import` artifact，并生成 deterministic summary fragment，保证 `scope_id=chat:<channel>:<thread_id>` -> `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_service.py`、`octoagent/packages/memory/src/octoagent/memory/imports/service.py`

- [x] T017 [P0] [US2] [P] 写入 `CHAT_IMPORT_STARTED/COMPLETED/FAILED` 生命周期事件，统一挂在 dedicated operational task `ops-chat-import` -> `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_service.py`

- [x] T018 [P0] [US2] 为真实导入编写测试，覆盖：独立 chat scope、artifact provenance、summary fragment metadata、失败时仍保留 batch/error -> `octoagent/packages/provider/tests/test_chat_import_service.py`、`octoagent/packages/memory/tests/test_import_service.py`

**Checkpoint**: 导入的 provenance / 隔离 / 审计三条链路已固定

---

## Phase 5: User Story 3 — 导入必须支持中断恢复和增量继续（Priority: P1）

**目标**: 同一 source 可重复执行并安全 resume，而不是每次从头重导

**Independent Test**: 模拟导入到一半中断，再用 `--resume` 重跑，确认只补写缺失消息

- [x] T019 [P0] [US3] [B] 实现 cursor 读取/更新与 dedupe ledger 写入，保证同一 `(source_id, scope_id, message_key)` 只落盘一次 -> `octoagent/packages/memory/src/octoagent/memory/imports/store.py`、`service.py`

- [x] T020 [P0] [US3] 实现 `--resume` 语义：优先使用 cursor，缺 cursor 时退化为 dedupe-only 增量导入 -> `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_commands.py`、`chat_import_service.py`

- [x] T021 [P0] [US3] 为重复执行 / 中断恢复编写测试，覆盖 source_message_id 去重、hash 去重、resume 只导新消息、部分失败后重跑 -> `octoagent/packages/memory/tests/test_import_store.py`、`test_import_service.py`、`octoagent/packages/provider/tests/test_chat_import_service.py`

**Checkpoint**: 021 的 durability 与日常增量使用场景成立

---

## Phase 6: User Story 4 — 事实提取必须继续受 Memory 治理约束（Priority: P2）

**目标**: 只有受约束的事实候选才进入 SoR；普通聊天默认 fragment-only

**Independent Test**: 输入含 `fact_hints` 的窗口时，系统通过 proposal 验证链写 SoR；无 hints 或验证失败时不污染 SoR

- [x] T022 [P1] [US4] [B] 实现 `fact_hints` -> `propose_write()` -> `validate_proposal()` -> `commit_memory()` 联动 -> `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_service.py`、`octoagent/packages/memory/src/octoagent/memory/imports/service.py`

- [x] T023 [P1] [US4] 实现 `fragment-only` / `NONE` fallback，当 hint 缺证据、冲突不明或验证失败时记录 warnings 而不污染 SoR -> `octoagent/packages/memory/src/octoagent/memory/imports/service.py`

- [x] T024 [P1] [US4] 为 governed fact write 编写测试，覆盖 proposal 成功、validation 失败、无 hints 只写 fragment 三类路径 -> `octoagent/packages/memory/tests/test_import_service.py`、`octoagent/packages/provider/tests/test_chat_import_service.py`

**Checkpoint**: 021 已完成与 020 memory contract 的受治理接线

---

## Phase 7: Verification / 回归与收口

**目标**: 用自动化验证把 021 的主闭环、导入 durability 和 020/022 集成边界固定住

- [x] T025 [P0] [B] 运行 targeted tests：memory import models/store/service、provider chat import commands/service、core models 回归 -> `octoagent/packages/memory/tests/`、`octoagent/packages/provider/tests/`、`octoagent/packages/core/tests/`

- [x] T026 [P0] [P] 补一条与 022 相关的回归验证：确认导入产生的 artifact / DB 内容不会破坏现有 backup/export 路径 -> `octoagent/packages/provider/tests/test_backup_service.py` 或新增集成测试

- [x] T027 [P0] [P] 更新 verification 制品并回填任务状态 -> `.specify/features/021-chat-import-core/verification/`

---

## Deferred / Boundary Tasks

- [ ] T028 [P1] [SKIP] 实现微信历史解析 adapter -> 后续 Feature / M3 处理
  **SKIP 原因**: 021 只冻结 generic input contract，不承诺具体 adapter

- [ ] T029 [P1] [SKIP] 实现 Web 导入面板 / 批次管理后台 -> 后续 Feature 处理
  **SKIP 原因**: 本轮以 CLI first 满足 M2 最小可用入口

- [ ] T030 [P1] [SKIP] 实现开放式 LLM 事实抽取器 -> 后续增强处理
  **SKIP 原因**: 021 MVP 采用 deterministic summary + optional fact hints，避免在线模型依赖

---

## 并行建议

在 Phase 2 完成后，可按最大并行拆成三条线：

1. `preview 线`：T010-T013
2. `durable import 线`：T014-T021
3. `governed facts 线`：T022-T024

唯一硬前置是：T004-T009（共享 schema / event / 输入契约 / docs sync）完成后，再进入三线并行。
