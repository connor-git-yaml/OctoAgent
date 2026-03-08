# Research Synthesis: Feature 025 第二阶段 — Secret Store + Unified Config Wizard

**Date**: 2026-03-08  
**Inputs**: `research/tech-research.md`、`research/product-research.md`、`research/online-research.md`、Feature 025-A verification、Feature 026-A spec

---

## 1. 综合结论

### 1.1 025-B 必须把“project + wizard + secret store”收敛成一条连续主路径

综合 blueprint、m3-feature-split、OpenClaw onboarding/secrets 以及 Agent Zero project UX，可以得到一个很清晰的产品结论：

- `project create/select` 是 onboarding 原语
- wizard 负责配置采集和计划
- `audit/configure/apply/reload/rotate` 负责 secret 生命周期
- `project inspect` 和 `doctor/onboard` 负责收口验证

这意味着 025-B 不能只补几个分散命令，而要给出一条完整 CLI 主路径。

### 1.2 普通用户路径与高级用户路径必须共用同一 contract

产品侧复核和 026-A contract 都指向同一个结论：

- 普通用户只应看到必填字段、推荐默认值和最小操作面
- 高级用户才展开 `env/file/exec/keychain`、`apply --dry-run`、`rotate`、providers-only 等细节
- 但二者必须共用同一个 `WizardSessionDocument` / `ConfigSchemaDocument` / `ProjectSelectorDocument`

否则后续 Web 与 CLI 会不可避免地分裂为两套语义。

### 1.3 Secret Store 的 canonical 层应是 “引用 + 绑定 + 注入摘要”，不是“新明文文件”

从代码现状和在线研究都能得出同一结论：

- `CredentialStore` 可保留为 provider auth bridge，但不适合扩成统一产品面
- `keychain` 可作为优先推荐的 source，但不是硬依赖
- canonical 层应保存 `SecretRef`、project bindings、audit/apply/reload/rotate 状态和 runtime materialization summary
- 真正的明文只在最后 runtime short-lived injection 边界出现

### 1.4 025-B 的运行时接线应直接复用 024 基线

024 已经交付 update/restart/verify/recovery/managed runtime 的稳定骨架，因此：

- `octo secrets reload` 不应另造新的 runtime control plane
- 只要 managed runtime 存在，就应走既有 reload/restart/verify 基线
- unmanaged runtime 则按 `degraded/action_required` 返回

### 1.5 026-B 的边界必须提前切清

025-B 现在应交付：

- CLI 主路径
- contract producer/consumer
- project/secret/wizard 的状态面

025-B 现在不应交付：

- 厚 Web 配置中心
- Session Center
- Scheduler
- Runtime Console

这样 026-B 才是“消费已冻结状态面并做厚控制台”，而不是反过来定义 025-B 的底层语义。

---

## 2. 设计冻结建议

### 2.1 用户主路径

推荐冻结为：

1. `octo project create`
2. `octo project select`
3. `octo project edit --wizard`
4. `octo secrets audit`
5. `octo secrets configure`
6. `octo secrets apply --dry-run`
7. `octo secrets apply`
8. `octo secrets reload`
9. `octo project inspect`
10. `octo doctor` / `octo onboard`

### 2.2 Secret 层次

- global bridge：既有 provider auth profile、legacy env bridge
- project canonical：project-scoped secret bindings
- runtime effective：short-lived materialization / env snapshot

### 2.3 Upstream/Downstream 边界

- upstream truth：
  - 025-A project/workspace/migration
  - 026-A wizard/config/project selector contract
- downstream consumer：
  - 026-B Web/console/thin UI

---

## 3. 对 spec 的直接影响

因此，025-B 的 spec 必须明确：

1. 不重做 025-A migration 与 canonical model
2. 不重定义 026-A contract
3. SecretRef 至少覆盖 `env/file/exec/keychain`
4. `audit/configure/apply/reload/rotate` 是正式生命周期，而不是可选脚本
5. `project create/select/edit/inspect` 是正式 CLI 主路径
6. runtime short-lived injection 与 no-secret-leak 是本阶段硬约束
7. 026-B 只消费状态面，不在本阶段承诺厚 Web 页面
