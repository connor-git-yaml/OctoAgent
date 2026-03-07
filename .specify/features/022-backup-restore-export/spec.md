---
feature_id: "022"
title: "Backup/Restore + Export + Recovery Drill"
milestone: "M2"
status: "Implemented"
created: "2026-03-07"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §5.1 FR-OPS-4 / §12.4 / §14"
predecessor: "Feature 012（health/diagnostics）、Feature 014（统一 CLI 配置基线）"
parallel_dependency: "可与 Feature 016 / 018 / 020 并行；Feature 023 依赖本特性的 restore dry-run 与 recovery drill 结果"
---

# Feature Specification: Backup/Restore + Export + Recovery Drill

**Feature Branch**: `codex/feat-022-backup-restore-export`
**Created**: 2026-03-07
**Status**: Implemented
**Input**: 基于 `docs/m2-feature-split.md` 的 Feature 022，交付用户可触达的 backup / restore dry-run / chats export / recovery drill 状态入口。
**调研基础**: `research/research-synthesis.md`、`research/product-research.md`、`research/tech-research.md`、`research/online-research.md`

---

## Problem Statement

OctoAgent 的 blueprint 已经定义了数据备份与恢复策略，但目前这些能力还停留在底层设计层：

1. 用户没有稳定的 `backup create` 入口，无法主动生成可迁移的 bundle。
2. 用户没有 `restore dry-run`，无法在恢复前知道会覆盖什么、缺什么、冲突在哪。
3. 用户没有 `export chats`，无法导出对话/任务记录用于迁移、留档或审计。
4. 系统没有把“最近一次恢复验证结果”显式暴露给操作者，因此“可恢复”仍不可被用户验证。

Feature 022 要解决的是“让恢复能力成为产品能力”，而不是再补一份 runbook。系统必须让用户明确知道：

- 现在能备份什么；
- 恢复前会发生什么；
- 哪些内容不安全或不建议直接打包；
- 最近一次恢复演练是否通过；
- 如果恢复准备度不足，下一步该怎么修。

---

## Pre-conditions

| 依赖项 | 当前状态 | 说明 |
|---|---|---|
| `octo` CLI 主入口 | 已交付 | 022 在现有 `provider.dx.cli` 上新增 backup / restore / export 子命令 |
| `core.config` 数据路径约定 | 已交付 | 022 复用现有 `get_db_path()` / `get_artifacts_dir()`，不重新发明目录布局 |
| Health / diagnostics 基础 | 已交付 | 022 需要在现有 health/ops 入口之上暴露最近 recovery drill 摘要 |
| Task / Event / Artifact 持久化 | 已交付 | chat export 基于现有持久化投影，不依赖新的聊天存储 |

前置约束：

- 022 不得在本 Feature 中交付 destructive restore apply，只交付 `restore dry-run`。
- 022 不得默认打包明文 secrets 文件（如 `.env`），但必须明确提示 bundle 的敏感性。
- 022 不得重定义独立于 core 的 backup 主数据模型；Web/CLI 只消费同一组 contract。

---

## User Scenarios & Testing

### User Story 1 - 自助创建可解释的备份包 (Priority: P1)

作为日常操作者，我希望通过一个明确的 CLI 命令创建 backup bundle，并看到其中覆盖了哪些关键数据、输出到了哪里、是否含敏感内容，这样我可以在迁移或排障前先做好备份，而不需要手写脚本。

**Why this priority**: 没有稳定的 backup create 入口，后续的恢复与导出都没有可靠起点。

**Independent Test**: 在有实际 `data/sqlite`、`data/artifacts` 和 config metadata 的项目目录中运行 `octo backup create`，验证命令成功生成 bundle 和 manifest，并输出结构化摘要。

**Acceptance Scenarios**:

1. **Given** 项目中已有任务数据、artifacts 和配置元数据，**When** 用户运行 `octo backup create`，**Then** 系统生成 backup bundle，并输出路径、包含范围和 manifest 摘要。

2. **Given** 项目中存在可能敏感的配置或密钥引用，**When** backup 创建完成，**Then** 系统明确提示 bundle 的敏感性级别和默认未包含的 secrets 类文件。

3. **Given** 数据库正在正常使用中，**When** 用户触发 backup，**Then** 系统仍生成一致性快照，而不是要求先停服务手工复制数据库文件。

---

### User Story 2 - 恢复前先看到 dry-run 计划 (Priority: P1)

作为准备迁移或恢复实例的用户，我希望在真正恢复前先运行 `octo restore dry-run`，看到 bundle 是否完整、版本是否兼容、会覆盖哪些现有文件、缺失了什么，这样我可以先做判断，而不是先恢复再补救。

**Why this priority**: 这是 022 的核心安全价值。没有 dry-run，恢复入口对普通用户仍然不可控。

**Independent Test**: 对一个有效 backup bundle 运行 `octo restore dry-run`，并构造目标目录已有文件、manifest 版本不一致、bundle 缺文件等场景，验证输出的 `RestorePlan` 能解释冲突与建议动作。

**Acceptance Scenarios**:

1. **Given** 用户提供一个有效 backup bundle，**When** 运行 `octo restore dry-run`，**Then** 系统输出结构化恢复计划，包括将恢复的对象、检测到的冲突和建议动作，而不执行真正恢复。

2. **Given** 目标项目中已存在同名 config、SQLite 或 artifact 文件，**When** 用户执行 dry-run，**Then** 系统把这些覆盖风险明确标记为冲突或警告。

3. **Given** bundle 中缺少关键文件、schema version 不兼容或 manifest 损坏，**When** 用户执行 dry-run，**Then** 系统以结构化错误说明阻塞原因，并提供修复或回退建议。

---

### User Story 3 - 导出 chats/session 记录 (Priority: P1)

作为普通操作者，我希望不进入数据库或手工读事件文件，也能导出某个 thread/task 的聊天与任务记录，用于迁移、归档或离线审计。

**Why this priority**: 如果导出能力仍只停留在底层数据文件，用户就无法真正使用“会话导出”这一能力。

**Independent Test**: 创建至少一个 Web chat task，执行 `octo export chats`，验证产出包含 thread/task、事件时间线和最小 artifact 元数据的 export manifest。

**Acceptance Scenarios**:

1. **Given** 系统中存在至少一个带有 thread_id 的聊天/任务记录，**When** 用户运行 `octo export chats`，**Then** 系统导出该范围内的对话与任务记录，而不要求用户直接查询 SQLite。

2. **Given** 用户只希望导出部分会话，**When** 指定 task/thread 或时间窗口筛选，**Then** 系统仅导出匹配范围的数据，并在 manifest 中写明导出边界。

3. **Given** 某些会话关联了 artifacts，**When** 执行导出，**Then** 系统至少导出 artifact 元数据引用，不会让会话记录失去上下文。

---

### User Story 4 - 明确看到最近一次恢复演练状态 (Priority: P2)

作为维护者，我希望在 CLI 或 Web 中直接看到最近一次 recovery drill 是什么时候、是否成功、失败原因是什么，这样我可以判断系统当前是否具备真实的恢复准备度。

**Why this priority**: 没有恢复演练状态，备份能力仍然只是“理论上能恢复”，无法满足 M2 的可恢复承诺。

**Independent Test**: 触发一次 recovery drill 或写入测试记录，然后在 CLI/Web 摘要入口中验证最近演练时间、状态和失败原因被正确显示。

**Acceptance Scenarios**:

1. **Given** 系统已有最近一次 recovery drill 记录，**When** 用户查看 recovery 状态，**Then** 系统显示最近验证时间、状态和摘要。

2. **Given** 最近一次 recovery drill 失败，**When** 用户查看状态，**Then** 系统显示失败原因和下一步修复建议，而不是只显示“失败”。

3. **Given** 还没有任何 recovery drill 记录，**When** 用户查看状态，**Then** 系统明确显示“尚未验证”，而不是误导用户认为恢复能力已验证通过。

---

### Edge Cases

- 当 backup bundle 的 manifest 存在但 checksum 或关键文件缺失时，dry-run 如何区分“可部分导入”和“必须阻塞”？
- 当目标目录已有 `octoagent.yaml`、`litellm-config.yaml` 或 SQLite 文件时，系统如何分类“覆盖风险”和“版本不兼容”？
- 当用户未配置任何 chat/task 数据时，`octo export chats` 如何输出空结果而不是失败？
- 当最近 recovery drill 记录文件损坏或版本不兼容时，系统如何安全降级并提示重新验证？
- 当备份目录或输出文件路径不可写时，CLI 如何返回可执行修复动作，而不是原始堆栈？

---

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 提供 `octo backup create`，用于生成可迁移的 backup bundle，并输出结构化摘要（输出路径、创建时间、覆盖范围、敏感性提示）。

- **FR-002**: 系统 MUST 使用在线 SQLite backup 机制生成数据库快照，不得依赖“先停服务再复制活跃数据库文件”作为默认路径。

- **FR-003**: 系统 MUST 为 backup bundle 生成 manifest，至少包含 schema version、创建时间、包含范围、关键文件列表、完整性信息和敏感性摘要。

- **FR-004**: 系统 MUST 默认包含 tasks / events / artifacts / chats / config metadata 所需的最小数据集合。

- **FR-005**: 系统 MUST 默认排除明文 secrets 文件（如 `.env`、`.env.litellm`）或其他高风险运行时文件，并在结果中明确说明该默认策略。

- **FR-006**: 系统 MUST 提供 `octo restore dry-run`，对指定 backup bundle 生成结构化 `RestorePlan`，且不得在该命令中执行 destructive restore apply。

- **FR-007**: `RestorePlan` MUST 至少包含：目标对象、bundle 完整性检查结果、schema/version 兼容性结果、路径冲突、覆盖提示、缺失文件、建议动作。

- **FR-008**: 当 backup bundle manifest 损坏、schema version 不兼容或缺少关键文件时，系统 MUST 阻止 dry-run 通过，并输出结构化阻塞原因。

- **FR-009**: 系统 MUST 提供 `octo export chats`，支持按 task / thread / 时间窗口导出聊天与任务记录，并产出 `ExportManifest`。

- **FR-010**: `ExportManifest` MUST 记录导出边界（筛选条件）、包含的 task/thread、事件时间线摘要和关联 artifact 元数据引用。

- **FR-011**: 系统 MUST 持久化最近一次 backup 与最近一次 recovery drill 的结果摘要，并让 CLI 与 Web 使用同一份状态源。

- **FR-012**: 系统 MUST 提供最小 Web 入口，使用户至少能查看最近 backup 时间、最近 recovery drill 时间、最新状态，并触发 backup 或 chats export。

- **FR-013**: 系统 MUST 把 backup 生命周期记录为事件，至少覆盖 `BACKUP_STARTED`、`BACKUP_COMPLETED`、`BACKUP_FAILED`。

- **FR-014**: 当最近 recovery drill 失败时，系统 MUST 提供结构化失败原因和修复建议，不得只输出布尔状态。

- **FR-015**: 系统 SHOULD 允许 recovery drill 结果进入现有健康/诊断摘要，让用户无需翻日志即可判断恢复准备度。

- **FR-016**: 系统 MUST NOT 在 Feature 022 中引入 destructive restore apply、远程同步或新的独立 backup 主数据模型。

### Key Entities

- **Backup Bundle**: 表示一次用户可下载或可持久化的备份包，包含数据库快照、artifact 引用文件、config metadata 以及 manifest。
- **Backup Manifest**: 描述 bundle 的结构化元数据，包括 schema version、内容范围、完整性校验和敏感性摘要。
- **Restore Plan**: 表示 `restore dry-run` 的结构化输出，说明将恢复什么、检测到哪些冲突、哪些条件阻塞恢复。
- **Restore Conflict**: 表示路径冲突、覆盖风险、版本不兼容、缺失文件等恢复前问题。
- **Export Manifest**: 表示 chats/session 导出的结构化结果，记录筛选边界、包含的 task/thread、事件摘要和 artifact 元数据。
- **Recovery Drill Record**: 表示最近一次恢复演练的时间、状态、失败原因、修复建议和关联 bundle 信息。

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 用户可以通过 `octo backup create` 在一次命令中生成 backup bundle，并看到输出路径、manifest 摘要和敏感性提示。

- **SC-002**: 对有效 bundle 运行 `octo restore dry-run` 时，系统能稳定输出结构化恢复计划，并在存在覆盖/缺失/版本冲突时给出对应说明。

- **SC-003**: 对损坏或不兼容 bundle 运行 `octo restore dry-run` 时，系统会阻塞并返回可理解的错误原因，而不是原始异常堆栈。

- **SC-004**: 用户可以通过 `octo export chats` 导出指定 task/thread 或时间窗口的聊天与任务记录，而不需要直接查询数据库。

- **SC-005**: CLI 或 Web 至少有一个统一入口能查看最近一次 recovery drill 的时间、状态和失败原因；未验证时会明确显示“尚未验证”。

- **SC-006**: Feature 023 的集成验收可以直接依赖 022 的 dry-run 与 recovery drill 结果判断“系统是否具备可恢复能力”。

---

## Clarifications

### Session 2026-03-07

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 022 是否在本 Feature 中执行真正的 restore apply？ | 否，只做 `restore dry-run` | 保持范围可控，先交付 preview-first 恢复能力 |
| 2 | backup 是否默认包含明文 secrets 文件？ | 否 | 兼顾安全与可用性，符合 FR-OPS-4 和最小权限原则 |
| 3 | chat export 是否等待 021 的导入/记忆治理完成？ | 否，直接基于 task/event/artifact 最小投影 | 保持 022 可并行推进 |
| 4 | Web 入口是否做成完整运维后台？ | 否，只做最小状态与导出入口 | 避免范围膨胀，先交付用户可感知状态面 |

---

## Scope Boundaries

### In Scope

- `octo backup create`
- `octo restore dry-run`
- `octo export chats`
- backup/export/recovery domain models
- 最近 backup / recovery drill 状态持久化
- Web 最小 recovery/backup 状态入口
- backup 生命周期事件

### Out of Scope

- destructive restore apply
- NAS/S3/Litestream 同步
- Vault 全量恢复
- 完整运维后台
- Chat Import / Memory 治理联动
