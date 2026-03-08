---
feature_id: "031"
title: "M3 User-Ready E2E Acceptance"
milestone: "M3"
status: "Draft"
created: "2026-03-08"
research_mode: "full"
blueprint_ref: "docs/blueprint.md M3；docs/m3-feature-split.md Feature 031；Feature 024-030 已交付基线"
predecessor: "Feature 024-030（M3 主能力，已交付）"
parallel_dependency: "031 为 M3 汇合验收 Feature，不与新增业务能力并行；只允许修补阻塞验收的接缝问题"
---

# Feature Specification: M3 User-Ready E2E Acceptance

**Feature Branch**: `codex/feat-031-m3-user-ready-acceptance`  
**Created**: 2026-03-08  
**Status**: Draft  
**Input**: 在 024-030 已交付的前提下，对 M3 做发布前的真实用户路径、稳定性、迁移与运维验收收口。  
**调研基础**: OpenClaw 官方文档（wizard / onboarding protocol / control UI / updating / export session / subagents）、Agent Zero 官方文档（projects / backup / memory / settings / tunnel），以及当前 master 代码与测试基线。

## Problem Statement

当前 master 已经把 M3 的主能力基本交付完成：

- 024：installer / updater / doctor-migrate operator flow
- 025：project/workspace、secret store、统一 wizard、asset manifest
- 026：control plane backend + Web 控制台 + session/automation/diagnostics/config/channels
- 027：Memory Console + Vault 授权检索
- 028：MemU 深度集成与治理内核对齐
- 029：WeChat Import + Multi-source Import Workbench
- 030：capability pack、ToolIndex、Delegation Plane、Skill Pipeline

但这些能力是多条并行交付线汇合而成，当前仍缺少一层“对外开放前必须成立”的统一证明：

1. 目前没有正式的 M3 acceptance feature、验收矩阵、release report 和剩余风险清单。
2. 024-030 各自有单 Feature 测试，但尚未统一证明 install / project / secret / control plane / memory / import / delegation / update / restore 在一条真实用户路径里共同成立。
3. 当前 control-plane / ops 路由在代码层仍是单 owner、本地或 trusted network 假设；如果不把部署边界写进验收门禁，就会把“可自用”误写成“可直接公网暴露”。
4. 用户已经明确计划把 OpenClaw 迁移到 OctoAgent，M3 若没有一次正式的迁移演练，就无法支撑真实切换。

Feature 031 的目标不是新增能力，而是把 M3 从“功能已合入 master”推进到“产品可签收、可迁移、可开放”的状态。

## Scope Boundaries

### In Scope

- 新增 M3 验收矩阵、release gates、fixture 与 verification report
- 新增 M3 端到端 / 集成 / e2e 验收测试
- 真实用户路径验证：
  - fresh install
  - unified wizard
  - project select / secret apply
  - first chat
  - control plane 日常操作
  - memory / import / delegation / automation
  - update / backup / restore drill
- OpenClaw -> OctoAgent 迁移演练与迁移证据记录
- 部署边界与 trusted-network 假设的显式验收
- 修补阻塞验收的最小接缝问题

### Out of Scope

- 新增 M3 之外的功能域
- 新的控制台大功能
- 新的 import source 类型
- 新的 memory 治理模型
- 新的 remote nodes / companion surfaces
- “顺手重构”已交付 Feature 的主 contract

### Allowed Changes Rule

031 **允许**修改已有 CLI / gateway / frontend / tests / docs，但仅限以下三类：

1. 修补阻塞用户 Ready 验收的接缝问题；
2. 补齐 release gates、acceptance harness 与报告制品；
3. 修正文档与实际交付状态不一致之处。

任何新增业务能力、范围扩张或跨里程碑功能都必须拒绝。

## Pre-conditions

| 依赖项 | 当前状态 | 说明 |
|---|---|---|
| Feature 024：Installer + Updater + Doctor/Migrate | 已交付 | 031 复用 install / preflight / migrate / restart / verify / update status 基线 |
| Feature 025：Project / Workspace / Secret Store / Wizard | 已交付 | 031 复用 project selector、wizard session、secret audit/apply/reload/rotate |
| Feature 026：Control Plane | 已交付 | 031 复用 canonical resources / actions / events 与正式 Web 控制台 |
| Feature 027：Memory Console + Vault | 已交付 | 031 复用 memory subject/proposal/vault 视图与授权检索链 |
| Feature 028：MemU Deep Integration | 已交付 | 031 复用 MemU degrade / evidence / indexing 基线 |
| Feature 029：WeChat Import + Import Workbench | 已交付 | 031 复用 import source / run / report / resume / memory effect contract |
| Feature 030：Capability Pack + Delegation Plane | 已交付 | 031 复用 capability/work/pipeline/delegation/control-plane 投影 |

前置约束：

- 031 不得重新定义 024-030 的 canonical model 与 action semantics。
- 031 应优先消费真实本地组件；外部 provider、Telegram API 等不稳定依赖允许通过 mock transport 稳定测试。
- 031 必须把 remaining risks 写清楚，不得以“测试都过了”替代里程碑结论。

## Release Gates

- `GATE-M3-FIRST-USE`：fresh machine install -> wizard -> project -> first chat -> dashboard 闭环成立
- `GATE-M3-PROJECT-ISOLATION`：跨 project 不串用 secrets、memory、agent profile、automation target
- `GATE-M3-TRUST-BOUNDARY`：控制面部署边界清楚，默认使用方式不会误导用户直接裸暴露未认证入口
- `GATE-M3-UPDATE-RESTORE`：backup-before-update、preflight/migrate/restart/verify、restore dry-run / rollback 建议成立
- `GATE-M3-MEMORY-IMPORT`：import -> artifact/fragment -> proposal/commit -> vault/memu degrade 证据链成立
- `GATE-M3-DELEGATION-AUTOMATION`：session / automation / work / pipeline 的继承链、route reason、降级路径成立
- `GATE-M3-MIGRATION-OPENCLAW`：至少完成一次 OpenClaw -> OctoAgent 迁移演练并留下 mapping / 风险 / rollback 证据
- `GATE-M3-RELEASE-REPORT`：产出最终验收报告、阻塞项清单与 deferred items 清单

## User Scenarios & Testing

### User Story 1 - 新用户可以在一条路径内完成首次可用 (Priority: P0)

作为第一次使用 OctoAgent 的 owner，我希望从一台新机器开始，在一条连续路径内完成 install、wizard、project 选择、首条消息验证和 dashboard 打开，这样我不需要理解内部拓扑才能开始使用。

**Why this priority**: 如果首次使用链仍断裂，M3 不能宣称“普通用户 Ready”。

**Independent Test**: 在干净目录与干净数据目录中，执行 install / wizard / first chat / dashboard 路径，验证无需手工拼装多处 env 或 YAML 即可完成首次 working flow。

### User Story 2 - 我可以安全升级并确认实例可恢复 (Priority: P0)

作为长期运行实例的 owner，我希望在升级前先做 backup / preflight，在升级后有 verify 和恢复建议，并能验证 restore dry-run 可读、可执行，这样升级不会变成一次不可控冒险。

**Why this priority**: 公开使用后的首个重大风险就是升级与恢复失败。

**Independent Test**: 对已初始化实例执行 backup -> update dry-run -> update apply -> verify -> restore dry-run，验证 update summary、failure report、rollback suggestion 与 recovery evidence 均成立。

### User Story 3 - 跨 project 不会串状态，控制面部署边界清楚 (Priority: P0)

作为会维护多个 project 的 owner，我希望切换 project 后 secrets、memory、agent profile、automation target 都不会串用；同时我也希望知道当前控制台是否只能跑在 localhost / trusted network，而不是误以为它已具备公网认证能力。

**Why this priority**: 这是用户易用性和稳定性的底线，也是公开开放前必须写清的边界。

**Independent Test**: 构造两个 project，分别绑定不同 secret/memory/import/automation/profile；验证切换 project 后 effective config 与控制台视图都不会泄漏。同步验证默认部署说明与实际入口边界一致。

### User Story 4 - Import / Memory / Vault / MemU 是一条可解释的链 (Priority: P0)

作为要迁移历史聊天和资料的 owner，我希望导入结果能在 artifact、fragment、proposal、SoR/Vault、MemU 降级信息之间完整追溯，这样我知道系统没有偷偷写入错误事实，也知道失败时该从哪一层恢复。

**Why this priority**: Memory 产品化是 M3 的核心承诺之一。

**Independent Test**: 执行一次 WeChat 或 normalized import，随后在 Import Workbench、Memory Console、Vault Authorization 和 restore dry-run 中验证证据链贯通。

### User Story 5 - Delegation / Automation / Pipeline 的继承链对用户可解释 (Priority: P1)

作为 operator，我希望 session、automation、work、pipeline 不只是“后台跑了”，而是能解释它继承了哪个 project、哪个 agent profile、为什么选这条路由、为什么退化到单 worker。

**Why this priority**: 030 的价值取决于它是否能被用户理解与干预。

**Independent Test**: 创建带 project / agent profile 的 session 与 automation，触发 work 与 pipeline，验证 route reason、selected tools、fallback、ownership 和 effective config snapshot 可见且正确。

### User Story 6 - 我可以完成一次 OpenClaw -> OctoAgent 迁移演练 (Priority: P1)

作为准备从 OpenClaw 迁移的 owner，我希望把至少一个真实项目迁入 OctoAgent，并验证 project、secret refs、导入数据、Memory/Vault、automation 与恢复路径仍可工作，这样我能在正式切换前确认风险范围。

**Why this priority**: 用户已经明确要做真实迁移，这不是理论需求。

**Independent Test**: 选取一个 OpenClaw 项目或等价导出集，完成目标 project 建立、secret 绑定、导入、memory 审计、dashboard 操作与 rollback 记录，输出 migration rehearsal record。

### User Story 7 - 我可以拿到一份 M3 可签收的发布报告 (Priority: P1)

作为项目 owner，我希望在 031 结束时拿到一份完整的 M3 release report，明确哪些 gates 已通过、哪些仍阻塞开放、哪些项被延后，这样我能决定是否正式对用户开放。

**Why this priority**: 没有最终报告，031 会退化成一堆离散测试。

**Independent Test**: 生成一份结构化 release report，覆盖全部 gates、测试命令、证据、remaining risks、deployment boundary 和 migration rehearsal 结论。

## Edge Cases

- control-plane / ops 路由在当前代码中仍未内建认证依赖，若用户偏离默认 localhost/trusted-network 用法直接暴露公网，如何 fail-closed 地写入 release boundary？
- 某些 legacy `scope_id` 无法映射到 project/workspace 时，031 如何把它标成 degraded 而不是误判隔离成功？
- MemU unavailable、Vault unauthorized、import partial success 同时出现时，报告如何准确表达 partial success 和恢复建议？
- automation / delegation / pipeline 在重启后恢复时，如何保证 effective config snapshot 与 route reason 不丢失？
- update / restore drill 若依赖共享 `.venv` 并发执行，如何避免 `uv run` 修改虚拟环境导致测试假失败？
- OpenClaw 迁移演练中若存在无法自动映射的 secrets、skills 或 session metadata，如何要求人工确认并记录 deferred items？

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 产出一份 M3 acceptance matrix，逐项映射 031 的 release gates、测试命令、证据位置和剩余风险。
- **FR-002**: 系统 MUST 新增 M3 端到端验收链，至少覆盖 `install -> wizard -> project -> first chat -> dashboard`。
- **FR-003**: 系统 MUST 新增 upgrade / recovery 联合验收链，覆盖 backup-before-update、dry-run、apply、verify、restore dry-run 与 failure report。
- **FR-004**: 系统 MUST 验证跨 project 的 secret、memory、agent profile、automation target 与 import mapping 隔离；发现 legacy orphan scope 时 MUST 明确标记 degraded。
- **FR-005**: 系统 MUST 把 control-plane 的部署信任边界写入 031 验收与最终报告；如果入口仍未内建认证，MUST 明示默认发布边界为 localhost、trusted network 或受保护反向代理。
- **FR-006**: 系统 MUST 验证 import -> artifact/fragment -> proposal/commit -> vault/memu degrade 的联合证据链，并覆盖 partial success / warning 路径。
- **FR-007**: 系统 MUST 验证 session / automation / work / pipeline 的 effective config inheritance、route reason、fallback 与 ownership 可见且一致。
- **FR-008**: 系统 MUST 执行一次 OpenClaw -> OctoAgent 迁移演练，至少记录 project mapping、secret handling、import scope mapping、memory/vault 审计结果、rollback 方案与 remaining gaps。
- **FR-009**: 031 MUST 生成一份最终 release report，至少包含 gates 结论、测试摘要、deployment boundary、migration rehearsal、remaining risks 和 deferred items。
- **FR-010**: 031 MUST NOT 新增新的产品域或把 M4 能力偷带进来。
- **FR-011**: 031 SHOULD 优先使用真实本地组件；外部 SaaS / Telegram / provider 依赖才允许 mock transport。
- **FR-012**: 031 的验证 harness SHOULD 避免共享 `.venv` 并发 `uv run` 导致的环境竞争；如无法避免，必须序列化相关步骤或显式使用隔离环境。

### Key Entities

- **M3 Acceptance Matrix Row**: 一条 release gate 到测试命令、证据、状态和风险的追踪关系。
- **M3 Release Gate Result**: 单个 gate 的通过/阻塞/降级结论。
- **Trust Boundary Profile**: 当前部署方式的边界说明，例如 localhost-only、trusted-network、reverse-proxy-guarded。
- **Migration Rehearsal Record**: OpenClaw -> OctoAgent 迁移演练记录，包含 mapping、人工决策、风险与 rollback。
- **Acceptance Fixture Pack**: 031 验收所需的项目、secret、import、memory、automation、delegation 测试样本集合。
- **M3 Release Report**: 最终发布前验收报告，汇总全部 gates、证据与 remaining risks。

## Success Criteria

### Measurable Outcomes

- **SC-001**: 新机器能在一条正式路径内完成 install / wizard / first chat / dashboard，且不要求用户手工维护多处 env。
- **SC-002**: 升级链支持 backup-before-update、preflight、apply、verify 与 restore dry-run，失败时有结构化报告和恢复建议。
- **SC-003**: 两个以上 project 的 secrets、memory、agent profile、automation target 与 import mapping 不串用，legacy degraded case 被明确识别。
- **SC-004**: import / memory / vault / memu 的联合证据链能在 control plane 中被追溯，partial success 与降级状态可解释。
- **SC-005**: delegation / automation / pipeline 的 effective config snapshot、route reason、fallback 与 ownership 可在控制台和事件链中查询。
- **SC-006**: 至少完成一次 OpenClaw -> OctoAgent 迁移演练，并输出清晰的阻塞项与人工步骤清单。
- **SC-007**: 最终 release report 能明确回答“M3 是否可对用户开放”，并标出 remaining risks 和发布边界。

## Clarifications

### Session 2026-03-08

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 031 是否允许新增功能？ | 否 | 031 是验收收口，不是功能扩张 |
| 2 | 当前 control plane 若仍未内建认证，是否还能通过 031？ | 可以，但必须把部署边界明确写成 localhost / trusted network / protected proxy，而不是假装已具备公网默认安全性 |
| 3 | OpenClaw 迁移是否属于 031 范围？ | 是 | 用户明确计划真实迁移，且这是发布可信度的一部分 |
| 4 | 031 是否需要独立考虑并发验证稳定性？ | 是 | 当前共享 `.venv` 的并行 `uv run` 存在环境竞争，必须纳入验收 harness 设计 |
