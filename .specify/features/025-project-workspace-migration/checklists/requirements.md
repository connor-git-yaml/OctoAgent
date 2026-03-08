# Requirements Checklist: Feature 025 第一阶段

## Scope Lock

- [x] 只包含 `Project` 正式模型、workspace 持久化、default project migration、legacy metadata backfill、env bridge、validation/rollback
- [x] 明确排除 Secret Store 实值存储
- [x] 明确排除 Wizard UI / Config Center 页面
- [x] 明确排除 project selector UI/CLI 契约

## Migration Gate

- [x] 已覆盖 `Project Migration Gate`
- [x] 要求自动生成 `default project`
- [x] 要求旧 `scope/channel/memory/import/backup` 元数据回填到 project/workspace 映射
- [x] 禁止“新装一遍再手工迁移”作为默认升级路径

## Safety

- [x] 明确禁止 destructive rewrite legacy `scope_id`
- [x] 明确要求 migration dry-run
- [x] 明确要求 validation report
- [x] 明确要求 rollback strategy
- [x] 明确要求 secret bridge 只记录引用，不存 secret 实值

## Test Matrix

- [x] 覆盖 domain model + store schema
- [x] 覆盖 default project migration apply / idempotency
- [x] 覆盖 memory/import/backup/env/channel backfill
- [x] 覆盖 validation failure -> rollback
- [x] 覆盖 CLI dry-run / rollback
- [x] 覆盖 Gateway startup auto-bootstrap
