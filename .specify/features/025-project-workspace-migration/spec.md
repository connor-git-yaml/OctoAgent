---
feature_id: "025"
title: "Project / Workspace Domain Model + Default Project Migration"
milestone: "M3"
status: "Draft"
created: "2026-03-08"
research_mode: "tech-only"
blueprint_ref: "docs/blueprint.md §2697-2701 / docs/m3-feature-split.md Feature 025 + Design Gates"
predecessor: "Feature 014（统一配置基线）、Feature 020（Memory Core）、Feature 021（Chat Import Core）、Feature 022（Backup/Restore）"
parallel_dependency: "本阶段是 Feature 025 的 migration gate，Feature 026 / 027 / 031 依赖其稳定的 project/workspace 基线"
---

# Feature Specification: Project / Workspace Domain Model + Default Project Migration

**Feature Branch**: `codex/feat-025-project-workspace-migration`  
**Created**: 2026-03-08  
**Status**: Draft  
**Input**: 基于 `docs/m3-feature-split.md` 的 Feature 025 第一阶段，先落实 `Project` 正式模型、project/workspace 持久化、default project migration、legacy metadata backfill、env 兼容桥、迁移校验与 rollback。  
**调研基础**: `research/tech-research.md`、`research/online-research.md`

---

## Problem Statement

M2 当前已经具备可运行的 gateway / memory / import / backup 闭环，但整个系统仍停留在“单实例 `project_root/data` + 旧 `scope_id` 命名空间”阶段：

1. 系统没有 first-class `Project` / `Workspace` 实体，只有 `project_root`、`scope_id`、`thread_id` 和若干 `data/*.json` 状态文件。
2. `task/chat`、`memory`、`chat import`、`backup/recovery` 的历史元数据分散在 SQLite、YAML 和 JSON snapshot 里，后续无法稳定接入 project selector、Secret Store、Config Center 和 Session Center。
3. `docs/m3-feature-split.md` 的 **Project Migration Gate** 要求 M2 既有实例升级到 M3 时自动生成 `default project`，并把旧 `scope/channel/memory/import/backup` 元数据回填到 project/workspace 映射；当前仓库还没有这条升级路径。
4. `.env` / `.env.litellm` 与 `octoagent.yaml` 当前仍是 runtime 真正配置源；如果在没有兼容桥的情况下直接引入新 project model，既有实例会在升级时丢失 provider/channel/runtime 解析上下文。

本 Feature 第一阶段的目标不是交付完整 wizard / selector / Secret Store，而是先把 **迁移门禁** 打通：

- 把 `Project` / `Workspace` 做成正式、可持久化、可查询、可验证的领域对象；
- 让现有 M2 实例在升级后自动得到 `default project`；
- 让旧 metadata 能通过稳定映射继续被访问；
- 让整个迁移过程具备 dry-run、validation 和 rollback 能力。

---

## Pre-conditions

| 依赖项 | 当前状态 | 说明 |
|---|---|---|
| Feature 014 `octoagent.yaml` 统一配置 | 已交付 | 本阶段复用 runtime/provider/channel 配置与 env 名引用 |
| Feature 020 Memory Core | 已交付 | 旧 memory scope / partition 需要回填到 workspace bindings |
| Feature 021 Chat Import Core | 已交付 | 旧 import batches/cursors/dedupe/report 需要映射到 project/workspace |
| Feature 022 Backup/Restore | 已交付 | 旧 backup/recovery/export metadata 需要映射到 default project |
| M3 Design Gate: Project Migration Gate | 必须通过 | 本阶段即 gate 本身，不得跳过 |

前置约束：

- 本阶段 **不包含** secret 实值存储、Wizard UI、配置中心页面、project selector UI/CLI 契约。
- 本阶段 **不得** destructive rewrite 现有 `scope_id` / `thread_id` / memory/import 主键。
- 本阶段 **不得** 把 secret 实值写入新的 project/workspace 持久化层。
- 本阶段 **必须** 提供迁移验证与 rollback 策略；不能用“失败了手工恢复”替代。

---

## User Scenarios & Testing

### User Story 1 - 既有 M2 实例自动获得 default project (Priority: P1)

作为已在运行 M2 实例的 owner，我希望在升级后系统自动生成 `default project` 和默认 workspace，并把旧 `scope/channel/memory/import/backup` 元数据回填到映射层，这样我不用重装实例或手工迁数据，也不会丢失现有任务和聊天历史。

**Why this priority**: 这是 M3 Project Migration Gate 的硬要求；不满足它，后续的 project selector、配置中心和 session/chat center 都没有可靠底座。

**Independent Test**: 在一个只有 legacy task/memory/import/backup 数据的临时项目目录中触发 migration，验证系统生成 default project、primary workspace 和完整 bindings，同时原有 task/memory/import/backup 数据仍可按旧路径读取。

**Acceptance Scenarios**:

1. **Given** 一个已有 `tasks/events/artifacts` 的 M2 项目目录，**When** 系统首次执行 project/workspace migration，**Then** 自动创建唯一的 `default project` 和 primary workspace，并把现有 task scope 绑定到该 workspace。
2. **Given** 一个含有 memory 与 chat import 历史数据的 M2 项目目录，**When** migration 完成，**Then** 旧 `memory_*` 和 `chat_import_*` 的 scope 元数据都能被回填到 workspace bindings，而不改写原表主键。
3. **Given** 一个已有 backup / recovery / export 状态文件的项目目录，**When** migration 完成，**Then** 这些 metadata 被绑定到 default project，并可在后续 project-aware 读路径中查询到。

---

### User Story 2 - 旧配置与 env 路径在 migration 后继续可用 (Priority: P1)

作为使用 `.env` / `.env.litellm` 和 `octoagent.yaml` 的现有用户，我希望升级后 provider/channel/runtime 解析仍然可用，同时系统把这些 legacy env/config 关系登记为 project-scoped bridge，这样后续 Secret Store 接入时不会失去兼容路径。

**Why this priority**: 如果 default project migration 完成但 runtime/env 解析断了，系统仍然无法进入“可升级、可配置”的 M3 主路径。

**Independent Test**: 准备包含 `octoagent.yaml`、`.env`、`.env.litellm` 的 legacy 项目，执行 migration 后验证现有 env 解析逻辑仍然工作，同时 project bindings 中存在对应 env/file bridge metadata。

**Acceptance Scenarios**:

1. **Given** `octoagent.yaml` 中配置了 `runtime.master_key_env`、provider `api_key_env` 和 Telegram token env 名，**When** migration 完成，**Then** 对应 env references 都被登记到 default project 的 env bridge 中。
2. **Given** legacy 项目目录中存在 `.env` 和 `.env.litellm`，**When** migration 完成，**Then** 系统记录这些文件桥接关系，但不会复制 secret 实值到新的持久化模型中。
3. **Given** Gateway 或 CLI 在 migration 后启动，**When** 读取 runtime/provider/channel 配置，**Then** 仍然沿用现有 env 解析路径，不因为新 project/workspace 层而失效。

---

### User Story 3 - migration 必须可验证、可 dry-run、可回滚 (Priority: P1)

作为维护者，我希望在真正写入之前先看到 migration 计划，并在写入后得到结构化 validation report；如果 migration 失败或验证不通过，我还需要一个明确可执行的 rollback 路径，这样实例不会卡在半升级状态。

**Why this priority**: 本阶段本质上是 migration gate；没有 validation / rollback，就不满足“普通用户可升级、可恢复”的 M3 基线。

**Independent Test**: 对 legacy 项目先执行 `--dry-run` 验证计划，再执行真实迁移；人为制造 validation 失败，验证系统会回滚本次新增记录且保留失败报告。

**Acceptance Scenarios**:

1. **Given** legacy 项目目录准备完成，**When** 运行 migration dry-run，**Then** 系统输出将创建的 project/workspace/bindings 数量、发现的 legacy metadata 和 validation 计划，而不写入新持久化记录。
2. **Given** migration 已经执行，**When** validation 检测到缺失 binding 或一致性失败，**Then** 系统回滚当前 run 新增的 project/workspace/binding 记录，并产出失败报告。
3. **Given** 最近一次 migration run 已成功或失败，**When** 用户请求 rollback latest，**Then** 系统能按 `migration_run_id` 删除本次 run 新增的记录，而不破坏 legacy data。

---

### User Story 4 - 后续 M3 能消费统一的 Project / Workspace 领域层 (Priority: P2)

作为后续 Feature 026/027/031 的开发者，我希望本阶段交付的 project/workspace 具备稳定模型、持久化 store 和读取入口，这样后续 selector、config center、memory console 和 session center 可以直接消费，而不需要再重做迁移基座。

**Why this priority**: 这决定了本阶段是不是一次性的“迁移脚本”，还是可持续复用的产品底座。

**Independent Test**: 在测试中通过 store/service API 查询 default project、workspace 与 bindings，验证后续 feature 能在不碰 legacy 表的前提下拿到 canonical project/workspace 视图。

**Acceptance Scenarios**:

1. **Given** migration 已完成，**When** 通过 domain store 查询 default project，**Then** 能得到正式 `Project` 实体，而不是临时 JSON 片段。
2. **Given** migration 已完成，**When** 查询 workspace bindings，**Then** 能以 typed model 返回 scope/channel/import/backup/env bridge 绑定记录。
3. **Given** migration 已重复执行，**When** 后续 feature 读取 default project，**Then** 不会因重复迁移出现重复 project/workspace 或不稳定主键。

---

### Edge Cases

- 当 memory/import 表尚未初始化时，migration 如何跳过这些子系统而不是把实例升级流程判定为失败？
- 当 legacy 实例没有 `octoagent.yaml`，只有 `.env` 或空项目目录时，default project migration 是否仍然能成功建立最小 project/workspace？
- 当 `RecoveryStatusStore` 或 onboarding 状态文件损坏时，migration 如何保留可恢复路径并避免把损坏 JSON 当作硬阻塞？
- 当 legacy scope 数量很大时，bindings 回填如何保证幂等，不因重复执行造成重复记录？
- 当 migration 在写入 project/workspace 过程中中断时，系统如何确保不会留下半写入记录？

---

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 提供正式 `Project` 域模型，至少包含 `project_id`、`slug`、`name`、`status`、`is_default`、`metadata` 与时间戳字段。

- **FR-002**: 系统 MUST 提供正式 `Workspace` 域模型，至少包含 `workspace_id`、`project_id`、`slug`、`name`、`kind`、`root_path`、`metadata` 与时间戳字段。

- **FR-003**: 系统 MUST 为 `Project` / `Workspace` 提供持久化 store，并将其纳入现有 OctoAgent SQLite 主库，而不是另建孤立数据库。

- **FR-004**: 系统 MUST 提供 typed `ProjectBinding` 或等价模型，用于承载 legacy `scope/channel/memory/import/backup/env` 到 project/workspace 的映射关系。

- **FR-005**: 系统 MUST 在既有 M2 实例上自动生成唯一的 `default project` 与 primary workspace，且该过程必须幂等。

- **FR-006**: 系统 MUST 将 legacy task/chat scope 映射到 default project 的 workspace bindings，但 MUST NOT 在第一阶段重写 `tasks.scope_id` 或其他 legacy scope 主键。

- **FR-007**: 系统 MUST 将 legacy memory scope、chat import scope 与 backup/recovery/export 元数据回填到 project/workspace bindings，且旧表/旧 JSON snapshot 保持可读。

- **FR-008**: 系统 MUST 提供 env 兼容桥，记录 `.env` / `.env.litellm` 文件桥接关系及 provider/channel/runtime 使用的 env name references，但 MUST NOT 持久化 secret 实值。

- **FR-009**: 系统 MUST 提供 `ProjectMigrationRun` 或等价审计对象，记录每次 migration 的 run_id、状态、摘要、validation 结果、rollback 信息与错误原因。

- **FR-010**: 系统 MUST 支持 migration dry-run，展示将创建的 project/workspace/bindings 及 validation 计划，而不写入最终 project/workspace 持久化记录。

- **FR-011**: migration apply MUST 在 validation 失败时自动回滚当前 run 的新增 project/workspace/binding 数据，且 MUST NOT 破坏 legacy task/memory/import/backup 数据。

- **FR-012**: 系统 MUST 支持显式 rollback 最近一次 migration run，按 `migration_run_id` 清理该 run 创建的新增记录。

- **FR-013**: 系统 MUST 在 Gateway startup 与至少一个 CLI migration 入口中自动或显式触发 default project migration，确保旧实例升级到 M3 时不需要手工导入数据。

- **FR-014**: 对于未初始化的 memory/import 子系统，migration MUST 优雅降级并跳过相应扫描，而不是整体失败。

- **FR-015**: project/workspace 新增层 MUST 提供 dual-read 兼容策略：新读路径优先查 canonical project/workspace/bindings，查不到时仍可回退 legacy `scope_id` / `project_root`。

- **FR-016**: 本阶段 MUST NOT 交付 secret 实值存储、project selector UI/CLI 协议、配置中心页面或 Wizard UI。

- **FR-017**: 本阶段 SHOULD 提供结构化 validation report，至少包含发现的 legacy metadata 数量、已回填 binding 数量、未覆盖项和后续动作建议。

### Key Entities

- **Project**: M3 的正式产品对象，代表一组统一隔离的 instructions、memory、secret bindings、workspace 与 channel/A2A bindings。
- **Workspace**: Project 下的工作空间对象，代表后续 files / knowledge / session / chat / ops scope 的承载边界。
- **Project Binding**: 将 legacy scope、channel、memory/import/backup metadata、env references 映射到 project/workspace 的 typed binding 记录。
- **Project Migration Run**: 一次 default project migration 的执行记录，承载 status、validation、rollback 和错误信息。
- **Legacy Env Bridge**: 旧 `.env` / `.env.litellm` 文件与 env name references 的 project-scoped 兼容桥接信息。

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 在 legacy M2 项目目录上首次运行 migration 后，系统能稳定生成且只生成一个 `default project` 与一个 primary workspace。

- **SC-002**: 测试中的 legacy task/memory/import/backup/env metadata 至少 95% 能被回填为显式 project/workspace bindings，剩余未覆盖项会出现在 validation report 中。

- **SC-003**: 对同一实例重复执行 migration 时，project/workspace/binding 主记录数量保持稳定，不会产生重复对象。

- **SC-004**: 当 validation 人为构造失败时，migration 会回滚本次 run 新增记录，且 legacy task/memory/import/backup 数据保持不变。

- **SC-005**: Gateway startup 和 CLI migration 入口都能在 legacy 实例上触发 default project bootstrap，不需要用户手工导入旧 metadata。

- **SC-006**: migration 后现有 provider/channel/runtime env 解析仍然工作，且 project-scoped env bridge 中能查询到对应 env/file bindings。

---

## Clarifications

### Session 2026-03-08

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 第一阶段是否要实现 Secret Store 实值存储？ | 否 | 用户已显式排除，先做 bridge metadata 与 migration gate |
| 2 | 是否直接把 legacy `scope_id` 重写成 `project_id/workspace_id`？ | 否 | 现有 memory/import 约束仍强依赖 `scope_id`，先走 additive mapping |
| 3 | 既有实例是否允许“新装一遍再手工迁移”？ | 否 | `Project Migration Gate` 明确禁止把手工重建当默认路径 |
| 4 | `.env` / `.env.litellm` 是否在本阶段迁入新 secret store？ | 否 | 本阶段只登记 bridge，不搬 secret 实值 |
| 5 | backup/export/recovery snapshot 是否在本阶段重写为 project-aware 新格式？ | 否 | 第一阶段只做 bindings backfill，历史 snapshot 保持只读兼容 |
