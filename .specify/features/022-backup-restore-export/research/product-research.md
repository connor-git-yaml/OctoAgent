# Feature 022 产品调研：Backup/Restore + Export + Recovery Drill

**特性分支**: `codex/feat-022-backup-restore-export`
**调研日期**: 2026-03-07
**调研模式**: full（产品 + 技术 + 在线补充）

## 1. 目标重述

Feature 022 的目标不是“补一个运维脚本入口”，而是把已有底层备份策略提升为普通操作者可用的自助恢复能力：

- 用户可以主动创建 backup bundle，而不是只依赖定时任务或 shell 手工打包；
- 用户可以执行 restore dry-run，提前看到冲突、缺失和覆盖风险，而不是先还原再发现坏了；
- 用户可以导出 chats/session 记录，完成迁移、留档或离线审计；
- 系统会明确展示最近一次恢复演练时间、结果和修复建议，而不是把“可恢复”停留在文档假设中。

## 2. 用户价值

### 2.1 Owner / 日常操作者

- 出现环境问题时，先看 dry-run 和最近恢复验证记录，就能判断“能不能安全迁移/恢复”。
- 需要迁移到另一台机器时，可以先导出关键数据，再决定是否做完整恢复。
- 不需要记忆 SQLite、artifacts、config 分别在哪个目录，也不需要手写 tar/rsync 命令。

### 2.2 维护者 / 调试者

- 恢复失败时能拿到结构化冲突原因，而不是模糊的“文件不对”。
- 最近恢复演练记录可直接用于验收、值守和支持排障。
- Web 侧最小入口能降低“必须进终端才能知道恢复状态”的门槛。

## 3. 竞品体验启示

### 3.1 Agent Zero

- 官方安装文档把 Backup & Restore 放在 Settings UI 中，明确定位为升级和迁移的最安全路径。
- Web UI 允许 load/save chats，用户对“聊天和状态可以带走”有直接感知。
- 备份预览支持冲突策略（override / skip / backup），说明“先看影响、再决定”是用户可理解的默认路径。

### 3.2 OpenClaw

- OpenClaw 的迁移文档虽然仍以文件复制为主，但把 `doctor` 定位成迁移后的“safe boring command”。
- 这说明恢复能力不仅是“能还原文件”，还要给出迁移后诊断、修复和状态确认。
- OpenClaw 文档明确提醒 backup 含 secrets，要求按生产敏感数据对待，这对我们的 bundle 安全策略有直接启示。

### 3.3 对 OctoAgent 的直接要求

1. 022 不能只做“创建备份文件”，必须把 restore dry-run 和恢复演练结果一起做出来。
2. 用户要能先看结论，再决定是否继续，而不是被迫先运行 destructive restore。
3. 备份/导出能力必须服务普通操作者，而不是默认假设对方熟悉目录布局和底层脚本。
4. 备份 bundle 不能默默打包 secrets 却不给风险提示。

## 4. 当前 OctoAgent 用户缺口

### 4.1 蓝图有策略，产品入口还不存在

当前 blueprint 已明确：

- SQLite 应使用在线 backup；
- Artifacts / Vault / Event Store 有备份策略；
- 每月应做恢复验证；
- Web/CLI 都应支持 backup/export，restore 至少支持 dry-run。

但当前代码里仍缺少：

- 用户可调用的 `octo backup create`；
- 用户可调用的 `octo restore dry-run`；
- 用户可调用的 `octo export chats`；
- 最近恢复验证状态的查询入口和展示入口。

### 4.2 “可恢复”目前不可被普通用户验证

现在即使底层数据还在，用户也无法快速回答：

1. 我最近一次恢复验证是什么时候？
2. 当前 bundle 恢复时会覆盖哪些东西？
3. 有没有 schema/version/path 冲突？
4. 我要迁移时该导出哪些聊天或任务记录？

这意味着“Durability First”在用户体验上还没有真正落地。

### 4.3 导出能力缺位

当前系统有：

- task / event / artifact 的持久化；
- Web chat task 路由；
- thread_id / scope_id 语义。

但没有面向用户的 chat export。用户无法用一个明确入口导出：

- 某个 thread 的对话记录；
- 某个时间窗口的聊天和事件；
- 对应 artifacts 的最小元数据。

### 4.4 恢复风险沟通不足

从用户视角，restore 最怕的不是“命令不存在”，而是：

- 覆盖现有实例；
- 导入了不兼容版本；
- bundle 缺文件但事前看不出来；
- 备份里含敏感内容却没人提示。

022 的产品设计必须先解决这些风险沟通问题。

## 5. 范围边界

### In Scope（本 Feature 必做）

- `octo backup create`
- `octo restore dry-run`
- `octo export chats`
- BackupBundle / RestorePlan / ExportManifest 基础模型
- 最近恢复演练记录的持久化与查看入口
- Web 最小入口：查看最近 backup / recovery drill 状态、触发导出
- 备份敏感性提示与恢复冲突说明

### Out of Scope（本 Feature 不做）

- 真正执行 destructive restore apply
- 云端/远程备份同步（NAS/S3/Litestream）
- Vault 全量加密恢复流程
- 大而全的运维控制台
- Memory 导出治理（归 020/021）

## 6. 成功标准（产品视角）

1. 用户能通过单条 CLI 命令创建 backup bundle，并得到清晰的输出路径、覆盖范围和敏感性提示。
2. 用户能在 restore 前执行 dry-run，并看到冲突、缺失文件、版本不兼容、覆盖提示和建议动作。
3. 用户能导出 chats/session 记录，不必直接读 SQLite 或手工拼事件。
4. Web 入口至少能展示最近一次 backup 和 recovery drill 结果，让非 CLI 场景也能判断系统恢复准备度。
5. 最近恢复演练时间和结果成为显式状态，而不是隐藏在 runbook 或日志里。

## 7. 产品风险

- 风险 1：022 只做 backup create，不做 dry-run，导致恢复仍不可控
  - 策略：restore dry-run 和 recovery drill 结果必须是 MVP。
- 风险 2：bundle 含敏感数据但没有显式提示
  - 策略：manifest 中输出 sensitivity summary；CLI/Web 均展示提示。
- 风险 3：chat export 范围定义过大，拖慢交付
  - 策略：先聚焦 thread/task 导出，不把 import/memory 搭进来。
- 风险 4：Web 入口做成完整运维后台，范围膨胀
  - 策略：只做最小状态面板和导出入口。

## 8. 结论

Feature 022 合理且必要，但它的 MVP 应锁定为：

1. 一个安全的 backup create 入口；
2. 一个可解释的 restore dry-run；
3. 一个普通用户能用的 chat export；
4. 一个明确暴露“最近一次恢复验证结果”的最小控制面。

如果这四点没有一起交付，OctoAgent 仍然不能宣称自己具备用户可感知的可恢复能力。
