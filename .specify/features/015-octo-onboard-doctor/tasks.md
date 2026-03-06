# Tasks: Feature 015 — Octo Onboard + Doctor Guided Remediation

**Input**: `.specify/features/015-octo-onboard-doctor/`
**Prerequisites**: `spec.md`, `plan.md`, `data-model.md`, `contracts/*.md`
**Created**: 2026-03-07
**Status**: Ready

**Task Format**: `- [ ] T{三位数} [P0/P1] [P?] [USN?] 描述 → 文件路径`

- `[P0]` / `[P1]`: 交付优先级（P0 为 M2 onboarding 主闭环阻塞项）
- `[P]`: 可并行执行（不同文件、无前置依赖）
- `[B]`: 阻塞后续关键路径
- `[USN]`: 所属 User Story（US1–US4）；Setup/Foundational 阶段不标注
- `[SKIP]`: 明确不在 015 落地，由后续 Feature 承担

---

## Phase 1: Setup（共享入口与模块骨架）

**目标**: 为 015 建立可复用的 provider/runtime bootstrap 路径和独立模块边界，避免后续把逻辑继续堆进 `cli.py` / `doctor.py`

- [x] T001 [P0] [B] 从 `config_commands.py` 抽取共享 provider/runtime 初始化逻辑到 `config_bootstrap.py`，并让 `octo config init` 继续复用该路径 → `octoagent/packages/provider/src/octoagent/provider/dx/config_bootstrap.py`、`octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py`

- [x] T002 [P0] [P] 创建 015 所需新模块骨架（`doctor_remediation.py`、`onboarding_models.py`、`onboarding_store.py`、`channel_verifier.py`、`onboarding_service.py`） → `octoagent/packages/provider/src/octoagent/provider/dx/`

- [x] T003 [P0] [P] 创建对应测试文件骨架，保证后续任务可并行补测 → `octoagent/packages/provider/tests/test_config_bootstrap.py`、`test_doctor_remediation.py`、`test_onboarding_models.py`、`test_onboarding_store.py`、`test_channel_verifier.py`、`test_onboard.py`

**Checkpoint**: 共享 bootstrap 与模块边界就绪，可进入模型与 store 层实现

---

## Phase 2: Foundational（共享模型 / Store / Contract）

**目标**: 先冻结所有共享模型和跨 Feature contract。任何用户故事都必须建立在这一层之上。

> **警告**: Phase 2 未完成前，不得开始 `octo onboard` 主流程编排

- [x] T004 [P0] [B] 实现 `OnboardingStep`、`OnboardingStepStatus`、`NextAction`、`OnboardingStepState`、`OnboardingSummary`、`OnboardingSession` 模型 → `octoagent/packages/provider/src/octoagent/provider/dx/onboarding_models.py`

- [x] T005 [P0] 为 onboarding 模型编写单元测试，覆盖优先级规则、序列化和默认状态 → `octoagent/packages/provider/tests/test_onboarding_models.py`

- [x] T006 [P0] [B] 实现 `OnboardingSessionStore`（固定路径 `data/onboarding-session.json`、filelock、原子写入、损坏备份、reset） → `octoagent/packages/provider/src/octoagent/provider/dx/onboarding_store.py`

- [x] T007 [P0] 为 `OnboardingSessionStore` 编写单元测试，覆盖首次加载、写入、损坏恢复、restart reset → `octoagent/packages/provider/tests/test_onboarding_store.py`

- [x] T008 [P0] [P] 实现 `ChannelOnboardingVerifier` protocol、`VerifierAvailability`、`ChannelStepResult`、`ChannelVerifierRegistry` 和 registry miss fallback → `octoagent/packages/provider/src/octoagent/provider/dx/channel_verifier.py`

- [x] T009 [P0] 为 verifier registry 编写单元测试，覆盖 register/get/list、missing verifier blocked fallback、fake verifier 返回透传 → `octoagent/packages/provider/tests/test_channel_verifier.py`

- [x] T010 [P0] [B] 实现 `DoctorRemediation`、`DoctorGuidance`、`DoctorRemediationPlanner`，把 `CheckResult` 规范化为 grouped actions → `octoagent/packages/provider/src/octoagent/provider/dx/doctor_remediation.py`

- [x] T011 [P0] 为 remediation planner 编写单元测试，覆盖 blocking/warning 判定、stage 分组和 command/manual action 映射 → `octoagent/packages/provider/tests/test_doctor_remediation.py`

**Checkpoint**: 015 共享模型和 016 并行 contract 已冻结，可开始主流程编排

---

## Phase 3: User Story 1 — 可恢复的首次使用向导（Priority: P1）

**目标**: 新用户只运行 `octo onboard` 就能进入主路径，并在中断后从正确步骤恢复

**Independent Test**: provider/runtime 已完成后中断，再次运行 `octo onboard` 时直接继续 `doctor_live` 或后续阶段，不重复已完成配置

- [x] T012 [P0] [US1] [B] 实现 `OnboardingService.load_or_create_session()`、`resume_from_first_incomplete_step()` 和 step 顺序控制 → `octoagent/packages/provider/src/octoagent/provider/dx/onboarding_service.py`

- [x] T013 [P0] [US1] [B] 在 `OnboardingService` 中接入 provider/runtime 阶段：优先复用现有配置；缺失时走共享 `config_bootstrap`，写入后同步 `litellm-config.yaml` → `octoagent/packages/provider/src/octoagent/provider/dx/onboarding_service.py`

- [x] T014 [P0] [US1] 在 `cli.py` 注册 `onboard` 命令，并实现 `--channel` / `--restart` / `--status-only` 参数解析与退出码语义 → `octoagent/packages/provider/src/octoagent/provider/dx/cli.py`

- [x] T015 [P0] [US1] 为 `config_bootstrap` 抽取后的行为补测，确保 `octo config init` 原路径不回归 → `octoagent/packages/provider/tests/test_config_bootstrap.py`

- [x] T016 [P0] [US1] 为 `OnboardingService` 编写单元测试，覆盖首次运行、resume 命中、已完成项目非破坏性重跑 → `octoagent/packages/provider/tests/test_onboard.py`

- [x] T017 [P0] [US1] 编写 CLI 测试，覆盖 `octo onboard --help`、`--restart` 确认、`--status-only` 输出 → `octoagent/packages/provider/tests/test_onboard.py`

**Checkpoint**: `octo onboard` 已成为统一入口，支持安全退出与 resume

---

## Phase 4: User Story 2 — 失败时得到明确修复动作（Priority: P1）

**目标**: doctor 失败时，用户看到的是动作化 remediation，而不是一堆需要自行解释的检查项

**Independent Test**: 构造 docker/proxy/live_ping 失败场景，验证 `octo doctor` 和 `octo onboard` 输出一致的 remediation action

- [x] T018 [P0] [US2] [B] 在 `doctor.py` 中接入 `DoctorRemediationPlanner`，保留现有 table 输出，同时追加 remediation 摘要渲染 → `octoagent/packages/provider/src/octoagent/provider/dx/doctor.py`

- [x] T019 [P0] [US2] [B] 在 `OnboardingService` 中接入 doctor 阶段：执行 `DoctorRunner.run_all_checks(live=True)`，将 guidance 写入 `last_remediations` 和 `doctor_live` step state → `octoagent/packages/provider/src/octoagent/provider/dx/onboarding_service.py`

- [x] T020 [P0] [US2] 补充 `test_doctor.py`，验证现有 `DoctorReport` 兼容不破坏，并新增 remediation 摘要断言 → `octoagent/packages/provider/tests/test_doctor.py`

- [x] T021 [P0] [US2] 在 `test_onboard.py` 增加 doctor fail -> remediation -> 退出码 1 的场景 → `octoagent/packages/provider/tests/test_onboard.py`

**Checkpoint**: doctor 与 onboard 已共享同一套 remediation 结果模型

---

## Phase 5: User Story 3 — 渠道接入与首条消息验证（Priority: P1）

**目标**: 015 通过 verifier contract 串起 channel readiness 和 first-message verification；verifier 缺位时明确 blocked

**Independent Test**: fake verifier 存在时能走完 readiness + first_message；verifier 不存在时返回 blocked summary 和后续动作

- [x] T022 [P0] [US3] [B] 在 `OnboardingService` 中接入 channel 阶段：调用 registry、处理 availability、串联 `run_readiness()` / `verify_first_message()`，并把结果持久化到 session steps → `octoagent/packages/provider/src/octoagent/provider/dx/onboarding_service.py`

- [x] T023 [P0] [US3] 在测试中提供 fake verifier，实现 available / blocked / timeout 三类返回，用于隔离 015 与 016 → `octoagent/packages/provider/tests/test_channel_verifier.py`、`test_onboard.py`

- [x] T024 [P0] [US3] 在 `test_onboard.py` 编写 verifier missing 场景，断言 summary=`BLOCKED`、包含 `blocked_dependency` action，且不误报 READY → `octoagent/packages/provider/tests/test_onboard.py`

- [x] T025 [P0] [US3] 在 `test_onboard.py` 编写 fake verifier 成功场景，断言 readiness / first_message 两个步骤都落为 `COMPLETED` → `octoagent/packages/provider/tests/test_onboard.py`

**Checkpoint**: 015 与 016 的并行边界已稳定，channel 闭环可通过 fake verifier 验证

---

## Phase 6: User Story 4 — 明确的系统可用摘要（Priority: P2）

**目标**: onboarding 结束时用户看到一个明确且一致的 readiness 终态，而不是自己解释各步骤日志

**Independent Test**: 构造 `READY`、`ACTION_REQUIRED`、`BLOCKED` 三种组合，验证 summary 优先级和 next actions 一致

- [x] T026 [P1] [US4] [B] 实现 summary builder，把四个 step state 汇总为 `OnboardingSummary`，并输出已完成步骤、待完成步骤和优先 next actions → `octoagent/packages/provider/src/octoagent/provider/dx/onboarding_service.py`

- [x] T027 [P1] [US4] 在 `cli.py` 或辅助渲染函数中实现 Rich summary 输出，确保 `octo onboard`、`--status-only`、doctor fail 场景使用同一摘要语义 → `octoagent/packages/provider/src/octoagent/provider/dx/cli.py`（或新建渲染辅助模块）

- [x] T028 [P1] [US4] 为 summary 优先级与输出格式编写测试，覆盖 READY / ACTION_REQUIRED / BLOCKED 三态 → `octoagent/packages/provider/tests/test_onboard.py`

**Checkpoint**: 用户无需阅读底层检查项即可知道“现在能不能用、下一步做什么”

---

## Phase 7: E2E / 回归与边界保护

**目标**: 用可执行测试把 015 的主闭环、恢复语义和并行边界固定住

- [x] T029 [P0] [B] 编写 E2E：首次运行 -> provider 配置 -> doctor 通过 -> fake verifier 成功 -> READY → `octoagent/packages/provider/tests/test_onboard.py`

- [x] T030 [P0] [P] 编写 E2E：provider 已完成 -> doctor 失败 -> remediation -> rerun -> READY → `octoagent/packages/provider/tests/test_onboard.py`

- [x] T031 [P0] [P] 编写 E2E：verifier 未注册 -> BLOCKED summary -> 修复提示指向重新运行 `octo onboard --channel telegram` → `octoagent/packages/provider/tests/test_onboard.py`

- [x] T032 [P0] 执行回归测试：`test_doctor.py`、`test_init_wizard.py`、`test_config_commands.py`（若存在）以及 015 新增测试，确认 F014/F003-b 无回归 → `octoagent/packages/provider/tests/`

---

## Deferred / Boundary Tasks

- [ ] T033 [P1] [SKIP] 实现 Telegram verifier 的真实 adapter（pairing/readiness/first message 网络闭环） → Feature 016 负责
  **SKIP 原因**: 015 只定义 verifier contract，不落地 Telegram transport/pairing 细节

---

## 并行建议

在 Phase 2 完成后，可按最大并发拆成三条线：

1. `doctor/remediation` 线：T018-T021
2. `onboard/session` 线：T012-T017、T026-T028
3. `channel verifier` 线：T022-T025

唯一硬前置是：T004-T011（共享模型 / store / contract）完成后，三条线再并行推进。
