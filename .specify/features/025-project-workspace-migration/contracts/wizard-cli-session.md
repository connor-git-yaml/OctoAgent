# Contract: Wizard Session in CLI

## 1. Session Lifecycle

### start

- 入口：`octo project edit --wizard`
- 结果：创建或恢复 `WizardSessionRecord`

### resume

- 若当前 project 已有 active session，则继续推进现有 step

### status

- 返回 current step、blocking reason、next actions、schema ref

### cancel

- 标记当前 session 为 cancelled，保留可审计摘要

## 2. Contract Reuse

- wizard 语义以上游 026-A `WizardSessionDocument` 为准
- 配置字段定义以上游 `ConfigSchemaDocument + uiHints` 为准
- CLI 仅决定渲染方式，不改字段语义

## 3. CLI Rendering Rules

- 普通用户模式优先展示 required + recommended 字段
- 高级模式再展开 `env/file/exec/keychain` 和原始 source 细节
- unsupported `uiHints` 允许降级，但不得改变 validation

## 4. Handoff to Secret Lifecycle

- wizard 产出的是 draft config + draft secret binding plan
- 真正生效必须经过 `audit/configure/apply/reload`
