---
mode: codebase-scan
created: "2026-03-08"
sources:
  - docs/blueprint.md
  - docs/m3-feature-split.md
  - octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py
  - octoagent/packages/provider/src/octoagent/provider/dx/doctor.py
  - octoagent/packages/provider/src/octoagent/provider/dx/onboarding_service.py
  - octoagent/packages/provider/src/octoagent/provider/dx/backup_commands.py
  - octoagent/packages/provider/src/octoagent/provider/dx/backup_service.py
  - octoagent/packages/provider/src/octoagent/provider/dx/recovery_status_store.py
  - octoagent/apps/gateway/src/octoagent/gateway/routes/ops.py
  - octoagent/apps/gateway/src/octoagent/gateway/routes/health.py
  - octoagent/frontend/src/components/RecoveryPanel.tsx
---

# Feature 024 代码库调研汇总

## 上游约束

`docs/m3-feature-split.md` 与 `docs/blueprint.md` 对 024 的共同约束已经明确：

- 必须交付“一键安装入口 + `octo update` + `preflight -> migrate -> restart -> verify`”这一条 operator flow。
- 升级失败时必须有结构化报告和恢复建议，不能只留原始堆栈。
- Web 端只要求把 update / restart / verify 接到现有管理面，不要求在 024 内同时交付完整控制台。
- 025 的 Project/Workspace、Secret Store、统一配置中心，以及 026 的 Session Center / Scheduler / Runtime Console 都是后续 Feature，不得被 024 提前吞并。

## 现有可复用基线

### 1. provider dx CLI 与 doctor 基线已经存在

现有 `provider.dx` 已具备：

- `octo config` 命令组与 `octo config migrate`
- `octo doctor` / `octo doctor --live`
- `onboarding_service` 的步骤化 readiness 检查
- Rich 风格的 CLI 输出和友好错误包装

这意味着 024 不需要重新发明 CLI 框架；新增 install / update / migrate / verify 能力应继续落在现有 dx 命令面与模型体系之上。

### 2. backup / recovery 持久化与摘要入口已经存在

现有 `Feature 022` 已交付：

- `BackupService`
- `RecoveryStatusStore`
- 最近 backup / recovery drill 的统一状态源
- `backup create` / `export chats` 能力

024 可以直接复用这些对象来保存升级尝试摘要、最近一次失败报告引用，以及恢复建议关联信息，而不必新建独立状态孤岛。

### 3. Web ops / recovery 入口已经存在，但动作面不足

现状：

- gateway 已提供 `/api/ops/recovery`
- gateway 已提供 `/api/ops/backup/create`
- gateway 已提供 `/api/ops/export/chats`
- `RecoveryPanel` 已有状态展示、backup create、export chats 按钮

缺口：

- 没有 `/api/ops/update/*`、`/api/ops/restart`、`/api/ops/verify`
- RecoveryPanel 没有升级相关状态、失败摘要和动作按钮

因此 024 的 Web 范围应定义为“扩展现有 ops/recovery 面板”，而不是新建第二套运维 UI。

## 当前缺口

通过代码扫描确认，仓库中目前不存在以下正式能力：

- 一键安装入口
- `octo update`
- 独立的 `octo migrate` 升级迁移注册表
- 更新失败结构化报告模型
- Web 可触发的 update / restart / verify API

这说明 024 不是“补几条路由”，而是需要补一条完整的 operator workflow。

## 设计影响

### 设计决策 D1：024 以“单机/单实例 operator flow”作为 MVP

当前代码库并不存在 app 分发、桌面安装器或多节点编排基线，因此 024 的最小交付应聚焦：

- 本地单机安装入口
- 单实例升级
- 有界停机 restart
- 同一实例上的 verify

不在 024 内扩展多节点 rollout、零停机部署或 project-aware 运维。

### 设计决策 D2：`octo update` 必须显式阶段化

因为 doctor、backup/recovery、health summary 都已是分层对象，024 最合理的实现方式不是“一条黑箱 update 命令”，而是：

- preflight
- migrate
- restart
- verify

每个阶段都需要可持久化、可复盘、可在 Web 中展示。

### 设计决策 D3：Web 入口应复用 RecoveryPanel，而不是新建大控制台

这样可以直接消费现有 `/api/ops/recovery` 摘要与 recovery 状态源，避免在 024 内把 026 的“完整 runtime console”偷渡进来。

### 设计决策 D4：失败报告必须成为共享 contract

CLI、Web、后续恢复建议都需要消费同一份失败信息，因此 024 需要一个结构化 `UpgradeFailureReport` / `UpdateAttemptSummary` 类契约，而不是临时字符串拼接。

## 推荐落点

- CLI/DX：继续扩展 `packages/provider/src/octoagent/provider/dx`
- 领域服务：新增 update / migrate / verify service，尽量共用 `BackupService` 与 `RecoveryStatusStore`
- Gateway：在现有 `routes/ops.py` 上扩展 update / restart / verify
- Frontend：在现有 `RecoveryPanel.tsx` 上扩展状态与动作

## 本轮冻结的范围边界

### In Scope

- 一键安装入口
- `octo update`
- preflight / migrate / restart / verify 流程
- 升级失败结构化报告
- Web ops/recovery 入口扩展

### Out of Scope

- Project / Workspace
- Secret Store
- 配置中心
- Session Center
- Scheduler
- Memory Console
- 完整 runtime console
- 多节点 / 零停机升级
