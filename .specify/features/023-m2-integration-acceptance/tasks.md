# Tasks: Feature 023 — M2 Integration Acceptance

**Input**: `.specify/features/023-m2-integration-acceptance/`  
**Prerequisites**: `spec.md`, `plan.md`, `data-model.md`, `contracts/*.md`  
**Created**: 2026-03-07  
**Status**: Completed

**Task Format**: `- [ ] T{三位数} [P0/P1] [P?] [USN?] 描述 -> 文件路径`

- `[P0]` / `[P1]`: 交付优先级（P0 为 M2 收口阻塞项）
- `[P]`: 可并行执行
- `[B]`: 阻塞后续关键路径
- `[USN]`: 所属 User Story（US1-US5）
- `[SKIP]`: 明确不在 023 落地

---

## Phase 1: Setup（冻结边界与验收矩阵）

**目标**: 先把 023 的边界固定住，避免“收口 Feature”演变成新增功能 Feature。

- [x] T001 [P0] [B] 完成 023 的 speckit 制品：`spec.md`、`plan.md`、`tasks.md`、`data-model.md`、`contracts/m2-acceptance-matrix.md`、`checklists/requirements.md` -> `.specify/features/023-m2-integration-acceptance/`

- [x] T002 [P0] [B] 把 `docs/m2-feature-split.md` 中的五个 gate 映射到 023 验收矩阵，形成单一事实源 -> `.specify/features/023-m2-integration-acceptance/contracts/m2-acceptance-matrix.md`

**Checkpoint**: 023 的目标、边界、验收矩阵已冻结，可进入实现

---

## Phase 2: Foundational（首次使用断点修补）

**目标**: 只修补真正会阻塞用户主路径的 DX 断点，不扩业务能力。

- [x] T003 [P0] [B] 对齐 `config init` 与 `doctor` 的前置假设，使统一配置成为合法入口，不再要求额外 `.env` 才能继续 -> `octoagent/packages/provider/src/octoagent/provider/dx/config_bootstrap.py`、`doctor.py`

- [x] T004 [P0] [B] 把 Telegram channel 配置纳入可操作闭环，使 `config` / `onboard` 至少有一条稳定主路径，不依赖手工编辑 YAML -> `octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py`、`onboarding_service.py`、`telegram_verifier.py`

- [x] T005 [P0] [B] 将 onboarding 的 first message 完成标准改为“真实入站 task 或等价本地证据”，避免只验证 bot 出站 -> `octoagent/packages/provider/src/octoagent/provider/dx/telegram_verifier.py`、`onboarding_service.py`

- [x] T006 [P0] 为上述 DX 修补补齐 provider 层单元/集成测试 -> `octoagent/packages/provider/tests/test_config_bootstrap.py`、`test_doctor.py`、`test_onboard.py`、`tests/dx/test_telegram_verifier.py`

**Checkpoint**: 首次 working flow 不再被已知断点阻塞

---

## Phase 3: User Story 1 — 首次 working flow 联合验收（Priority: P0）

**目标**: 固定 `config -> doctor -> onboarding -> pairing -> first inbound task` 主链

**Independent Test**: 新项目目录中完成统一配置、Telegram pairing 和首条消息入站 task 创建

- [x] T007 [P0] [US1] [B] 编写 023 首次使用联合验收测试，串联 CLI/provider/gateway/operator action -> `octoagent/tests/integration/test_f023_m2_acceptance.py`

- [x] T008 [P0] [US1] 验证首次 owner pairing 的主路径与降级路径，并在测试中明确 Web operator inbox 为主路径 -> `octoagent/tests/integration/test_f023_m2_acceptance.py`

**Checkpoint**: 首次使用链具备单条自动化证据

---

## Phase 4: User Story 2 — Web / Telegram operator parity（Priority: P0）

**目标**: 证明两个渠道处理的是同一 operator item、同一审计链

**Independent Test**: pairing / approval / retry / cancel / alert ack 至少各有一条 parity 证据

- [x] T009 [P0] [US2] [B] 编写 operator parity 联合验收测试，覆盖 Web / Telegram 对同一 item 的语义一致性 -> `octoagent/tests/integration/test_f023_m2_acceptance.py`

- [x] T010 [P0] [US2] 纳入并执行 gateway 侧 parity 回归用例，覆盖重复动作 `already_handled` / `stale_state` 等语义 -> `octoagent/apps/gateway/tests/test_operator_actions.py`、`test_telegram_operator_actions.py`

**Checkpoint**: operator parity 有自动化闭环

---

## Phase 5: User Story 3 — A2A + JobRunner 联合验收（Priority: P0）

**目标**: 证明协议层和执行层之间没有断层

**Independent Test**: `A2A TASK -> runtime -> RESULT/ERROR` 至少覆盖成功和非成功路径

- [x] T011 [P0] [US3] [B] 编写 A2A + runtime 联合验收测试，使用真实 `DispatchEnvelope` / `A2AMessage` / `WorkerRuntime` 组合 -> `octoagent/tests/integration/test_f023_m2_acceptance.py`

- [x] T012 [P0] [US3] 验证并复用现有 protocol / gateway 胶水，使 `A2A TASK` 可以真正进入执行面 -> `octoagent/packages/protocol/src/octoagent/protocol/adapters.py`、`octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py`

**Checkpoint**: A2A contract 不再停留在 round-trip 级验证

---

## Phase 6: User Story 4 — Import / Memory / Recovery 联合验收（Priority: P0）

**目标**: 证明导入结果进入系统的可恢复边界

**Independent Test**: `import chats -> memory commit -> export -> backup -> restore dry-run`

- [x] T013 [P0] [US4] [B] 编写 durability 联合验收测试，串联 chat import、memory、backup/export/restore -> `octoagent/tests/integration/test_f023_m2_acceptance.py`

- [x] T014 [P0] [US4] 如需最小补丁，补齐 import/recovery 闭环中缺失的审计或状态暴露 -> `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_service.py`、`backup_service.py`

**Checkpoint**: 020/021/022 的 durability boundary 有真实联合证据

---

## Phase 7: User Story 5 — M2 验收报告与回归（Priority: P1）

**目标**: 给出清晰的里程碑结论，而不是只留下测试文件

**Independent Test**: 生成一份可回看的验收报告，包含命令、结果、风险和边界

- [x] T015 [P1] [US5] [B] 产出 023 验收报告，逐项对应五个 M2 gate 与四条联合验收线 -> `.specify/features/023-m2-integration-acceptance/verification/verification-report.md`

- [x] T016 [P1] [US5] 补充 spec review / quality review，明确 023 的范围边界与已知风险 -> `.specify/features/023-m2-integration-acceptance/verification/spec-review.md`、`quality-review.md`

- [x] T017 [P0] [P] 执行回归验证：provider / gateway / protocol / memory / integration tests -> `octoagent/packages/provider/tests/`、`octoagent/apps/gateway/tests/`、`octoagent/packages/protocol/tests/`、`octoagent/packages/memory/tests/`、`octoagent/tests/integration/`

**Checkpoint**: M2 是否完成有明确报告与证据

---

## Deferred / Boundary Tasks

- [ ] T018 [P1] [SKIP] 新增完整 onboarding / operator dashboard -> 后续里程碑处理  
  **SKIP 原因**: 023 只修补断点，不新增新的体验层

- [ ] T019 [P1] [SKIP] 新增 destructive restore apply 或新的 Telegram 功能 -> 后续 Feature / M3 处理  
  **SKIP 原因**: 超出 023 “集成验收” 定义

- [ ] T020 [P1] [SKIP] 对外开放新的 A2A API / transport -> 后续 Feature 处理  
  **SKIP 原因**: 023 只验证既有协议与执行面联通性

---

## 并行建议

在 Phase 2 完成后，可拆成三条并行线：

1. 首次使用 + operator parity：T007-T010
2. A2A + runtime：T011-T012
3. import / recovery + 报告：T013-T017

唯一硬前置是：T001-T006 完成后，再进入三线并行推进。
