# Contract: Secret Store Lifecycle

## 1. SecretRef Sources

### `env`

- locator: `env_name`
- 解析规则：读取当前进程环境

### `file`

- locator: `path`, optional `reader=text|dotenv`
- 解析规则：读取文件或从 dotenv 中取值

### `exec`

- locator: `command`, optional `timeout_seconds`
- 解析规则：执行命令并读取 stdout

### `keychain`

- locator: `service`, `account`
- 解析规则：通过 OS keychain backend 读取；不可用时显式返回 degrade/error

## 2. Commands

### `octo secrets audit`

**Checks**

- 必需 target 是否缺失 binding
- ref 是否可解析
- legacy env bridge 与 project binding 是否冲突
- 是否存在疑似明文 secret 落盘风险
- runtime 是否需要 reload/restart

### `octo secrets configure`

**Behavior**

- 收集或更新当前 project 的 secret binding 计划
- 不直接写入 runtime
- 只输出 redacted 计划

### `octo secrets apply`

**Flags**

- `--dry-run`

**Behavior**

- dry-run：仅输出计划与 materialization preview
- apply：原子落盘 canonical binding 与 apply summary

### `octo secrets rotate`

**Behavior**

- 替换 `SecretRef` 或其目标 material
- 生成结构化轮换摘要

## 3. Security Rules

- secret 实值不得进入 CLI 输出、日志、事件、artifact、YAML
- canonical store 只保存 `SecretRef` metadata 与 target/binding 状态
- `SecretStr` 只允许在 runtime materialization 边界解出明文
