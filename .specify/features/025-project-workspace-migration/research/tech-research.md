# Tech Research: Feature 025 第二阶段 — Secret Store + Unified Config Wizard

**Date**: 2026-03-08  
**Mode**: full

---

## 1. 当前代码基线

### 1.1 025-A 已经提供了 canonical project/workspace 底座

- `packages/core/models/project.py` 已定义 `Project`、`Workspace`、`ProjectBinding`、`ProjectMigrationRun`。
- `packages/core/store/project_store.py` 已把这些对象落到主 SQLite。
- `provider/dx/project_migration.py` 已经打通 default project migration、legacy metadata backfill、env bridge 与 Gateway startup auto-bootstrap。

结论：

- 025-B 不需要也不应该重做 project/workspace 模型。
- Secret Store 与 CLI 主路径应该建立在现有 `Project` / `ProjectBinding` store 之上，以 additive 扩展方式增加 secret binding 和 active project 语义。

### 1.2 当前配置/引导能力已经存在，但还是“前 M3”形态

- `config_schema.py` 已经提供统一配置根模型，继续使用 `runtime.master_key_env`、provider `api_key_env`、Telegram `bot_token_env` / `webhook_secret_env` 等 env-name 语义。
- `config_wizard.py` 和 `config_commands.py` 已经能原子读写 `octoagent.yaml`。
- `onboarding_models.py` 有固定流程的 onboarding session，但不是 026-A 意义上的通用 `WizardSessionDocument`。
- `init_wizard.py` 是一次性脚本式引导，内部直接把 credential 写进 `.env` / `.env.litellm`。

结论：

- 现有配置模型和 YAML 读写能力可以复用。
- 旧 `init_wizard.py` 只能作为历史参考，不能继续担任 M3 主路径。
- 025-B 需要把“脚本式 init”替换成“可恢复 wizard session + contract 消费”。

### 1.3 当前 credential store 不适合直接成为 M3 canonical Secret Store

- `provider/auth/store.py` 的 `CredentialStore` 当前把 `SecretStr.get_secret_value()` 序列化到 `~/.octoagent/auth-profiles.json`。
- 它有 filelock、0600 权限、损坏恢复，但本质仍是原始 secret material 的本地文件持久化。
- 这套能力对 provider auth profile 很有价值，但不覆盖 project-scoped bindings、runtime injection、channel/gateway secrets，也不符合“统一 Secret Store 主路径”的产品目标。

结论：

- `CredentialStore` 适合保留为 bridge/import source，尤其在 provider auth 场景。
- 025-B 不应把它直接当成所有 secret 的 canonical 产品面。
- canonical 层应以 `SecretRef + project-scoped binding + runtime materialization` 为中心。

### 1.4 024 已经交付 runtime update/restart/verify/recovery 基线，可直接复用

- 024 已经有 `ManagedRuntimeDescriptor`、`RuntimeStateSnapshot`、update/restart/verify worker、gateway ops/recovery 接口。
- 这意味着 025-B 没必要单独发明“secret 生效”路径。

结论：

- `octo secrets reload` 应该复用 024 的 managed runtime / ops / recovery 能力。
- 对 unmanaged runtime，025-B 只需要给出明确的 `action_required/degraded` 结果，而不是伪装成热重载成功。

---

## 2. 差距分析

### 2.1 还没有 first-class SecretRef / secret binding

当前 `ProjectBindingType` 只有：

- `scope`
- `memory_scope`
- `import_scope`
- `channel`
- `backup_root`
- `env_ref`
- `env_file`

这意味着：

- 025-A 只记录了 legacy env bridge，没有正式表达“某个 project 绑定了哪个 secret target”。
- provider/channel/gateway secret 仍然只是 config 中的 env-name 引用，而不是 project-aware 产品对象。

需要新增的最小能力：

- `SecretRef`
- project-scoped secret binding / summary
- 当前 project 的 active binding 解析入口
- runtime materialization summary

### 2.2 还没有 active project 主路径

虽然 canonical `Project` / `Workspace` 已存在，但当前没有正式：

- `octo project create`
- `octo project select`
- `octo project edit`
- `octo project inspect`

缺少这些命令会导致：

- project 仍是内部对象，而不是用户工作单元
- secrets/wizard/config 无法稳定绑定到“当前 project”

### 2.3 还没有对 026-A contract 的真实 consumer

026-A 已冻结了：

- `WizardSessionDocument`
- `ConfigSchemaDocument`
- `ProjectSelectorDocument`

但当前代码里：

- 没有 `uiHints`
- 没有通用 wizard session store
- 没有 project selector 的 contract-facing producer/consumer

所以 025-B 的职责不是重新设计这些对象，而是给它们第一个真实 CLI 消费主路径。

### 2.4 旧环境变量路径仍然是默认路径

当前常见路径还是：

- 改 `octoagent.yaml`
- 填 `api_key_env`
- 手工写 `.env` / `.env.litellm`
- 再尝试运行 doctor/onboard

这与 M3 目标冲突：

- 普通用户不该默认记住 env 细节
- provider/channel/gateway secret 也不该散落在多个入口

---

## 3. 方案评估

### 方案 A：继续以 `.env` / `.env.litellm` 为主，只把 CLI 包一层 wizard

**做法**:

- 维持 env-first 语义
- wizard 只负责帮用户把值写入 env 文件
- project 只保存“哪些 env 名称属于这个 project”

**问题**:

- 仍然没有正式 Secret Store，只是更好看的 env 写入器
- secret 生命周期没有 `audit/apply/reload/rotate`
- project-scoped bindings 仍然只是间接引用，无法支持后续 Web/operator 管理

**结论**: 不采用。

### 方案 B：为每个 project 新增本地明文 secret 文件

**做法**:

- 每个 project 单独落一个 `data/projects/<id>/secrets.json`
- 直接保存 provider/channel/gateway 实值

**优点**:

- 实现快
- 不依赖外部 keychain

**问题**:

- 明文落盘风险高
- 与“不得把 secret 实值写入日志、事件或 LLM 上下文”的安全边界方向相悖
- 后续 Web/operator 面也更难解释“哪些值是 ref，哪些是 materialized”

**结论**: 不采用。

### 方案 C：分层 Secret Store

**做法**:

- canonical 层只保存 `SecretRef + project-scoped bindings + audit/apply/reload/rotate state`
- source 支持 `env` / `file` / `exec` / `keychain`
- runtime 只消费短生命周期 materialization
- 已有 provider auth profile 作为 bridge/import source，而不是唯一事实源

**优点**:

- 与 025-A project model、026-A contract 和 024 runtime 基线都能自然对接
- 既保留高级路径，也能把普通用户路径从 env-first 提升到 project-first
- 有利于 026-B 后续做 thin Web 消费层

**结论**: 采用该方案。

---

## 4. 关键设计决策

### D1: Secret Store 的 canonical 层应该是什么？

- **Decision**: canonical 层是 `SecretRef + project-scoped secret binding + runtime materialization summary`
- **Why**:
  - project 是 M3 的第一隔离边界
  - secret 生命周期需要正式状态，而不是纯粹依赖环境变量
  - 后续 Web/Telegram/operator 面需要可读摘要对象，而不是直接读密钥存储后端

### D2: Secret 实值默认落在哪里？

- **Decision**: 025-B 不新增明文 YAML/SQLite/JSON 落盘路径；默认优先 `keychain`，其他场景走 `env/file/exec`
- **Why**:
  - 既满足普通用户路径，也不强迫高级用户放弃 env/file/exec
  - 不引入新的仓库内或项目内明文 secret 文件
- **Consequence**:
  - keychain 不可用时必须显式降级，不允许 silent fallback
  - provider `CredentialStore` 只作为 bridge/import source，不作为 025-B 的统一产品面

### D3: 现有 `*_env` 配置是否保留？

- **Decision**: 保留
- **Why**:
  - `config_schema.py`、provider/channel/gateway 当前已经广泛依赖这些 env-name 字段
  - 破坏这些字段会引入高风险回归
- **Consequence**:
  - 025-B 的职责是把 project bindings materialize 成 runtime 可消费的 env-name 映射
  - `octoagent.yaml` 继续只保存非 secret 配置和 env-name 引用

### D4: wizard session 应该建立在哪个层次？

- **Decision**: 按 026-A `WizardSessionDocument` 语义建立 provider.dx 的 CLI consumer/store
- **Why**:
  - CLI 是 025-B 的第一条主路径
  - 026-B 之后仍要消费同一会话对象
- **Consequence**:
  - 不能复用 `init_wizard.py` 的一次性局部状态
  - 需要 `start/resume/status/cancel`

### D5: 普通用户路径和高级用户路径是否拆成两套系统？

- **Decision**: 不拆系统，只做同一 contract 的两层呈现
- **普通路径**:
  - `project create/select`
  - wizard session
  - `secrets audit/configure/apply/reload`
- **高级路径**:
  - 直接选择 `env/file/exec/keychain`
  - `apply --dry-run`
  - 手动 `rotate`
- **Why**: 产品心智必须统一，否则 026-B 会接到两套语义。

### D6: 025-B 与 026-B 的边界怎么切？

- **Decision**:
  - 025-B 交付 CLI 主路径、contract producer/consumer、project/secret 状态面
  - 026-B 负责完整 Web 厚页面和多表面控制台
- **Why**:
  - 先让主路径可用，再让 Web 消费层做厚交互
  - 避免“为了等页面完成，CLI 主路径长期不可用”

---

## 5. 推荐落地路径

### 5.1 普通用户路径

1. `octo project create`
2. `octo project select`
3. `octo project edit --wizard`
4. wizard 收集 provider/channel/gateway/model 所需配置
5. `octo secrets audit`
6. `octo secrets apply --dry-run`
7. `octo secrets apply`
8. `octo secrets reload`
9. `octo project inspect`
10. `octo doctor` / `octo onboard`

### 5.2 高级用户路径

- 直接写 `SecretRef(env)`
- 指向受控权限文件的 `SecretRef(file)`
- 使用 `SecretRef(exec)` 对接 1Password / pass / 自定义脚本
- 使用 `SecretRef(keychain)` 对接 OS keychain
- `audit --check` + `apply --dry-run` + `reload`

---

## 6. 测试含义

### 6.1 单元测试

- `SecretRef` 四类 source 的解析和错误分类
- redaction / masking / no-log-leak
- project selector / active project 状态读写
- wizard session persistence / resume / cancel

### 6.2 集成测试

- `project create/select/edit/inspect` 全路径
- `secrets audit/configure/apply --dry-run/apply/reload/rotate`
- managed runtime reload 成功路径
- unmanaged runtime degrade path
- legacy env bridge + project binding 同时存在时的优先级和告警

### 6.3 回归测试

- 025-A project/workspace migration 不回归
- 024 update/restart/recovery 基线不回归
- Telegram / provider config 的 `*_env` 兼容语义不回归
