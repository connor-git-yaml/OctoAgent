---
feature_id: "024"
title: "Installer + Updater + Doctor/Migrate"
milestone: "M3"
status: "Implemented"
created: "2026-03-08"
research_mode: "codebase-scan"
blueprint_ref: "docs/blueprint.md §12.6 / §12.9.2 / M3, docs/m3-feature-split.md Feature 024"
predecessor: "Feature 014（统一配置 / doctor 基线）、Feature 022（backup / recovery 基线）、Feature 023（M2 集成验收基线）"
parallel_dependency: "可与 Feature 025 / 026 control-plane contract 子线并行；本 Feature 不引入 Project/Workspace、Secret Store、Session Center、Scheduler、Memory Console"
---

# Feature Specification: Installer + Updater + Doctor/Migrate

**Feature Branch**: `codex/feat-024-installer-updater-doctor-migrate`
**Created**: 2026-03-08
**Status**: Implemented
**Input**: 基于 `docs/m3-feature-split.md` 与 `docs/blueprint.md`，落实 M3 Feature 024：一键安装入口、`octo update`、`preflight -> migrate -> restart -> verify` 流程、升级失败结构化报告，并把 update / restart / verify 接到现有 Web ops/recovery 入口。
**调研基础**: `research/research-synthesis.md`、`research/online-research.md`

---

## Problem Statement

M2 已经把配置、doctor、backup/recovery、operator inbox 和 Web recovery 面板做出来了，但从普通用户视角，OctoAgent 仍然缺一条真正可重复的安装与升级主路径：

1. 首次安装仍偏手工，用户需要自己拼装 Python/uv、配置文件、启动顺序和 dashboard 打开步骤。
2. 已安装实例没有正式的 `octo update`，升级仍依赖手工拉代码、猜测迁移步骤、自己判断何时 restart/verify。
3. 现有 `octo doctor` 与 `backup/recovery` 基线已经存在，但还没有被串成一条正式的 operator flow。
4. 现有 Web recovery 面板只能做 backup / export，不能执行 update / restart / verify，也无法显示升级失败报告。
5. 升级失败时系统缺少结构化失败摘要，用户只能看到零散错误文本，不知道失败在哪一阶段、现在是否可恢复、下一步应怎么修。

Feature 024 要解决的是：

- 把安装、升级、迁移、重启、验证做成可重复、可恢复、可观察的正式产品能力；
- 让 CLI 与 Web 共享同一条 operator flow，而不是出现“两边都能做一点，但都不完整”的状态；
- 在不提前引入 025/026 范围的前提下，先把“安装与升级可用”这条主路径打通。

---

## Pre-conditions

| 依赖项 | 当前状态 | 说明 |
|---|---|---|
| `octo config` / `octo doctor` | 已交付 | 024 复用现有 provider dx CLI、doctor 模型与配置解析，不重写配置体系 |
| backup / recovery 基线 | 已交付 | 024 复用 `BackupService`、`RecoveryStatusStore` 与 recovery summary 基线 |
| gateway ops API / RecoveryPanel | 已交付 | 024 只扩展现有 `ops` API 与 recovery 面板，不新建第二套运维入口 |
| health / diagnostics 基线 | 已交付 | verify 阶段应复用现有 health/diagnostics 与 doctor 检查，而不是另造探活协议 |

前置约束：

- 024 不得引入 Project/Workspace、Secret Store、配置中心、Session Center、Scheduler、Memory Console。
- 024 不要求交付完整 runtime console，只要求把 update / restart / verify 接到现有 Web ops/recovery 入口。
- 024 以单机/单实例 operator flow 为 MVP，不在本 Feature 内解决多节点 rollout 或零停机升级。

---

## User Scenarios & Testing

### User Story 1 - 新用户有一条一键安装入口 (Priority: P1)

作为首次安装的用户，我希望通过一条明确的安装入口完成运行时准备、最小初始化和下一步引导，这样我不需要先理解内部目录结构、配置文件关系和启动顺序。

**Why this priority**: 如果安装仍然是手工拼装，M3 的“普通用户可上手”目标从第一步就不成立。

**Independent Test**: 在一台干净机器或临时目录中执行安装入口，验证系统能完成基础依赖检查、生成最小可运行骨架，并输出明确的后续动作（如 doctor / config init / dashboard）。

**Acceptance Scenarios**:

1. **Given** 用户在未安装 OctoAgent 的环境中执行安装入口，**When** 安装流程运行完成，**Then** 系统完成最小运行时准备，并输出后续引导，而不是把用户丢给 README 手工拼装。

2. **Given** 主机缺少关键依赖或权限不足，**When** 用户执行安装入口，**Then** 系统返回结构化失败结果和修复建议，而不是原始堆栈。

3. **Given** 用户已安装或已有项目目录，**When** 再次执行安装入口，**Then** 系统以幂等方式识别当前状态，不重复破坏现有实例。

---

### User Story 2 - 已安装实例可以安全执行 `octo update` (Priority: P1)

作为已经在使用 OctoAgent 的操作者，我希望通过 `octo update` 执行一次可预测的升级流程，并在升级前先看到 preflight 结果与迁移计划，这样我不需要靠手工 runbook 才敢升级。

**Why this priority**: M3 的核心之一就是把“升级”从工程师知识变成产品能力。

**Independent Test**: 对一个已有实例执行 `octo update --dry-run` 与真实 `octo update`，验证系统能按 `preflight -> migrate -> restart -> verify` 顺序运行，并在每个阶段输出结构化状态。

**Acceptance Scenarios**:

1. **Given** 当前实例满足升级条件，**When** 用户运行 `octo update --dry-run`，**Then** 系统返回本次计划将执行的阶段、发现的问题和可继续/阻塞结论，而不实际执行迁移或重启。

2. **Given** 当前实例通过 preflight，**When** 用户运行真实 `octo update`，**Then** 系统按顺序执行 migrate、restart、verify，并把每个阶段结果写入统一摘要。

3. **Given** preflight 检测到阻塞条件，**When** 用户运行 `octo update`，**Then** 系统在进入 migrate 前停止，并明确指出阻塞原因和修复建议。

---

### User Story 3 - 升级失败时必须拿到结构化报告 (Priority: P1)

作为维护实例的 owner，我希望升级失败时系统能明确告诉我失败阶段、已完成到哪一步、受影响对象和恢复建议，这样我可以快速判断是重试、修配置还是走 restore 路径，而不是只能翻日志猜。

**Why this priority**: 没有结构化失败报告，就无法满足“可恢复、可诊断”的 M3 operator 承诺。

**Independent Test**: 构造 migrate 失败、restart 超时、verify 不通过等场景，验证系统输出统一失败报告，并且不会把实例留在未记录的半更新状态。

**Acceptance Scenarios**:

1. **Given** migrate 阶段失败，**When** update 流程停止，**Then** 系统输出包含失败阶段、错误摘要、最近成功阶段和建议动作的结构化报告。

2. **Given** restart 成功但 verify 失败，**When** update 流程结束，**Then** 系统把实例标记为“已重启但未验证通过”，并提供下一步诊断建议，而不是误报成功。

3. **Given** 存在最近一次 backup 或 recovery drill 摘要，**When** 生成失败报告，**Then** 系统把可用恢复线索一并附上，帮助用户判断是否需要 restore。

---

### User Story 4 - Web recovery 面板能触发 update / restart / verify (Priority: P2)

作为主要通过 Web 管理实例的操作者，我希望直接在现有 recovery/ops 面板里执行 update、restart 和 verify，并看到最近一次升级结果，这样我不必为了日常运维每次切回终端。

**Why this priority**: M3 要求 Web 成为正式控制面之一；024 的最小达标是让 update/restart/verify 进入现有面板。

**Independent Test**: 在 Web recovery 面板点击 update / restart / verify，验证对应 API 被调用、状态回显正确、失败摘要可见。

**Acceptance Scenarios**:

1. **Given** Web recovery 面板已打开，**When** 用户触发 update dry-run 或真实 update，**Then** 面板显示当前阶段、完成结果或失败摘要。

2. **Given** 用户只想做 restart 或 verify，**When** 从 Web 面板单独触发这些动作，**Then** 系统执行对应最小操作，而不要求完整 update。

3. **Given** 当前已有最近一次升级失败记录，**When** 用户打开 recovery 面板，**Then** 面板能直接看到失败阶段和修复建议摘要。

---

### Edge Cases

- 当用户在一次 update 尚未完成时再次从 CLI 或 Web 触发 update，系统如何防止并发升级？
- 当 preflight 通过但 migrate 中途失败时，系统如何保证 restart 不被误执行？
- 当 restart 成功但 verify 超时或 gateway 不可达时，系统如何把状态标成“部分完成”而不是成功？
- 当实例当前已经是最新版本或没有可执行更新时，`octo update` 如何优雅返回“无需升级”？
- 当安装入口运行在依赖缺失、目录不可写或端口冲突环境中时，系统如何输出用户可执行的修复建议？

---

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 提供一条一键安装入口，用于完成单机/单实例的最小安装准备，并输出明确的后续引导。

- **FR-002**: 安装入口 MUST 在执行前检查关键前置条件（例如 Python/uv、目录可写性、最小运行时依赖）并以结构化方式报告阻塞项。

- **FR-003**: 安装入口 MUST 支持幂等执行；对于已安装或已初始化实例，系统 MUST 识别当前状态并避免破坏现有项目。

- **FR-004**: 系统 MUST 提供 `octo update` 作为正式升级入口。

- **FR-005**: `octo update` MUST 支持 `--dry-run`，并在 dry-run 中输出 preflight、migrate、restart、verify 的阶段计划与阻塞结论，且 MUST NOT 产生 destructive 副作用。

- **FR-006**: 真实升级流程 MUST 以 `preflight -> migrate -> restart -> verify` 的固定阶段顺序执行，并持久化每个阶段的开始、完成或失败状态。

- **FR-007**: preflight 阶段 MUST 复用现有 doctor/config/runtime 检查基线，并在阻塞条件存在时停止后续迁移。

- **FR-008**: migrate 阶段 MUST 基于版本化迁移注册表执行配置、数据或 service entrypoint 迁移；同一迁移步骤 MUST 具备幂等或可安全跳过特性。

- **FR-009**: 当 migrate 阶段失败时，系统 MUST 停止后续 restart / verify，并输出结构化失败报告。

- **FR-010**: restart 阶段 MUST 复用现有运行时入口或 service 管理基线，并在超时、进程异常或 restart 失败时输出明确错误。

- **FR-011**: verify 阶段 MUST 复用现有 health/diagnostics/doctor 能力，对升级后实例给出明确的 pass/fail 结果与摘要。

- **FR-012**: 系统 MUST 持久化最近一次 update 尝试的摘要，使 CLI 与 Web 能读取同一份最近结果。

- **FR-013**: 升级失败报告 MUST 至少包含：失败阶段、错误摘要、最近成功阶段、当前实例状态、建议动作，以及可用的 backup/recovery 线索。

- **FR-014**: 系统 MUST 为 update / migrate / restart / verify 生命周期保留结构化审计记录，不允许只输出一次性控制台文本。

- **FR-015**: Web 端 MUST 在现有 ops/recovery 入口上补充 update dry-run、真实 update、restart、verify 四类最小动作。

- **FR-016**: Web 端 MUST 展示最近一次升级结果、当前阶段或最近失败摘要，而不是只显示“成功/失败”布尔状态。

- **FR-017**: CLI 与 Web 触发的 update / restart / verify MUST 使用同一组领域 contract 与状态源，避免前后端各自维护状态。

- **FR-018**: 系统 MUST 防止并发升级；当已有 update/restart/verify 正在进行时，新的同类请求 MUST 被串行化、拒绝或幂等合并。

- **FR-019**: 系统 SHOULD 复用现有 backup/recovery 基线，为失败报告提供最近 backup / recovery drill 信息；本 Feature MAY 提供 pre-update backup 钩子，但 MUST NOT 把完整 restore 流程扩展进 024。

- **FR-020**: 系统 MUST NOT 在 Feature 024 中引入 Project/Workspace、Secret Store、配置中心、Session Center、Scheduler、Memory Console 或完整 runtime console。

### Key Entities

- **Install Attempt**: 表示一次安装入口执行结果，包含环境检查、执行动作、幂等判定和后续引导。
- **Update Attempt**: 表示一次 `octo update` 的完整运行实例，记录目标版本、阶段状态、起止时间和最终结论。
- **Update Phase Result**: 表示 preflight、migrate、restart、verify 任一阶段的结构化结果。
- **Migration Step**: 表示一个可注册、可追踪、可幂等执行的迁移单元。
- **Upgrade Failure Report**: 表示一次升级失败后的共享报告，供 CLI、Web 与后续恢复判断共同消费。
- **Ops Action Summary**: 表示最近一次 update/restart/verify 的统一摘要，供 Web recovery 面板读取。

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 新用户可以通过一条安装入口完成最小运行时准备，并获得明确的下一步动作，而不需要手工拼装多条 README 命令。

- **SC-002**: 用户可以通过 `octo update --dry-run` 看到完整的 `preflight -> migrate -> restart -> verify` 计划和阻塞项，且 dry-run 不产生 destructive 副作用。

- **SC-003**: 对通过 preflight 的实例执行真实 `octo update` 时，系统能按阶段推进并给出统一结果摘要，而不是黑箱式升级。

- **SC-004**: 对 migrate/restart/verify 任一失败场景，系统会输出结构化失败报告，且不会把失败后的状态留成“无记录的半更新状态”。

- **SC-005**: Web recovery/ops 面板可以触发 update dry-run、真实 update、restart、verify，并读取最近一次升级结果或失败摘要。

- **SC-006**: 024 交付后，后续 Feature 025/026 可以直接复用 024 的 update/restart/verify contract，而不需要推翻当前实现重做。

---

## Clarifications

### Session 2026-03-08

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 一键安装入口是否覆盖桌面分发、App 首启与多种安装器形态？ | 否，024 只交付单机/单实例的官方安装入口 | 与当前代码基线和用户指定范围保持一致，避免把发行渠道扩展进来 |
| 2 | 024 是否同时引入 Project/Workspace、Secret Store 或配置中心？ | 否 | 这些属于 025 的范围，024 只解决安装/升级主路径 |
| 3 | 024 是否要求零停机升级或多节点 rollout？ | 否 | 本 Feature 以 bounded downtime 的单实例升级为 MVP |
| 4 | Web 侧是否交付完整运维控制台？ | 否，只扩展现有 recovery/ops 入口 | 避免提前吞并 026 的 runtime console / session center |
| 5 | 每次 update 是否强制自动创建 full backup bundle？ | 否，024 只复用现有 backup/recovery 基线与恢复线索；pre-update backup 仅作为可选钩子 | 保持范围可控，不把 022 的 restore/backup 产品面重新展开 |

---

## Scope Boundaries

### In Scope

- 一键安装入口
- `octo update`
- `preflight -> migrate -> restart -> verify`
- 版本化 migrate 注册表
- 升级失败结构化报告
- Web recovery/ops 入口上的 update / restart / verify
- 与现有 doctor / backup / recovery / health 基线的复用

### Out of Scope

- Project / Workspace
- Secret Store
- 配置中心
- Session Center
- Scheduler
- Memory Console
- 完整 runtime diagnostics console
- 多节点 / 零停机升级
- 完整 restore apply / rollback orchestration
