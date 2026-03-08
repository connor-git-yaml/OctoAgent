# Requirements Checklist: Feature 025 第二阶段

## Scope Lock

- [x] 只包含 Secret Store 分层、CLI wizard session、`octo secrets *`、`octo project create/select/edit/inspect`
- [x] 明确复用 025-A 已交付的 Project / Workspace / migration 基线
- [x] 明确消费 026-A 已冻结的 wizard / config schema / project selector 语义
- [x] 明确排除完整 Web Config Center、Session Center、Scheduler、Runtime Console

## Secret Safety

- [x] 已定义 `SecretRef(env/file/exec/keychain)`
- [x] 已明确 project-scoped secret bindings 与 runtime short-lived injection
- [x] 已明确 secret 明文不得进入 YAML、日志、事件、artifact 或 LLM 上下文
- [x] 已明确 `audit -> configure -> apply -> reload -> rotate` 生命周期

## Project Main Path

- [x] 已要求 `octo project create`
- [x] 已要求 `octo project select`
- [x] 已要求 `octo project edit`
- [x] 已要求 `octo project inspect`
- [x] 已要求 active project 语义与 readiness/warnings 摘要

## Wizard / Contract Reuse

- [x] 已要求 CLI wizard 可 start/resume/status/cancel
- [x] 已要求 CLI 消费 `ConfigSchemaDocument + uiHints`
- [x] 已要求普通用户路径与高级路径共用同一 contract，不得分裂为两套系统

## Runtime / Binding Integration

- [x] 已覆盖 provider / channel / gateway secret bindings
- [x] 已覆盖 `*_env` 兼容语义与 project materialization
- [x] 已要求复用 024 managed runtime / ops / recovery 基线完成 reload
- [x] 已要求 unmanaged runtime 明确降级，不得伪装成功

## Test Matrix

- [x] 覆盖 `SecretRef` 解析、遮罩、故障路径与审计
- [x] 覆盖 project create/select/edit/inspect CLI 主路径
- [x] 覆盖 wizard session 恢复与 `config schema + uiHints` CLI 消费
- [x] 覆盖 `audit/configure/apply --dry-run/apply/reload/rotate`
- [x] 覆盖“不泄露 secret 明文”的单元测试与关键集成测试
- [x] 明确与 026-B 的消费边界，不在本阶段承诺厚 Web 页面
