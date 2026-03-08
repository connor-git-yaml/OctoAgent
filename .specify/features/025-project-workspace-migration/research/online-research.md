---
required: true
mode: tech-only
points_count: 2
tools:
  - web.open
  - web.find
queries:
  - "SQLite official PRAGMA user_version / schema_version / integrity_check"
  - "Django official RunPython reverse_code reversible data migrations"
findings:
  - source: "https://www.sqlite.org/pragma.html#pragma_user_version"
    summary: "SQLite 的 `user_version` 是留给应用自行使用的整型版本位；`schema_version` 会在 schema 变化时由 SQLite 自动递增，手工修改可能导致错误结果甚至数据库损坏。"
  - source: "https://www.sqlite.org/pragma.html#pragma_integrity_check"
    summary: "`PRAGMA integrity_check` 会返回 `ok` 或具体错误，适合在迁移后做低层一致性校验；而 `foreign_key_check` 需单独执行。"
  - source: "https://docs.djangoproject.com/en/5.2/ref/migration-operations/#runpython"
    summary: "可逆 data migration 需要显式 `reverse_code`；schema 与 state 变化应避免混在一个不可回滚步骤里，必要时分离 database/state 操作。"
impacts_on_design:
  - "本特性使用应用自管的 `ProjectMigrationRun` / `migration_run_id`，而不是手工碰 SQLite `schema_version`。"
  - "迁移验证阶段加入 SQLite 完整性检查，作为质量门的一部分。"
  - "迁移实现坚持 additive schema 与可逆回滚，不把 schema rewrite 和 legacy data backfill 混成单个不可逆步骤。"
---

# Online Research Notes

## Point 1 — SQLite application-managed migration metadata

- 官方文档说明 `PRAGMA user_version` 是给应用自定义使用的版本整数，SQLite 自身不会消费它。
- 同页明确警告不要把 `schema_version` 当成应用自己的迁移版本位来手工修改，因为这可能导致旧 prepared statement 基于过期 schema 继续运行。

**Design take-away**:

- OctoAgent 的 Project/Workspace migration 不应依赖手工修改 SQLite `schema_version`。
- 应由应用层显式记录 `ProjectMigrationRun`、validation 结果和 rollback plan。

## Point 2 — Reversible data migration and validation separation

- Django 官方 `RunPython` 文档要求可逆迁移提供 `reverse_code`，否则迁移是 irreversible。
- 官方同时提醒 schema 变化与 data migration/state update 混在一起容易带来 transaction/state drift 问题。

**Design take-away**:

- 本 Feature 的 rollback 不能靠“最好别失败”，而要有显式的 run-scoped cleanup。
- schema 建表、legacy metadata 扫描、binding backfill、validation/rollback 应分成清晰阶段。
