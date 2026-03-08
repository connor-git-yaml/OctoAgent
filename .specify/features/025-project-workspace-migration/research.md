# Research Summary: Feature 025 — Project / Workspace Domain Model + Default Project Migration

本特性采用 `tech-only` 调研模式。

核心结论：

1. **`Project/Workspace` 必须先成为正式 domain model，再谈 UI/selector**。现有代码只有 `project_root`、`scope_id` 和若干 `data/*.json` 状态文件，没有可持久化的 project 主键或 workspace 归属。
2. **M2 -> M3 升级不能先重写 legacy `scope_id`**。`memory_sor` current 唯一约束、`chat_import_cursors` 主键、`chat_import_dedupe` 唯一键都依赖旧 `scope_id`，第一阶段应采用“新增映射层 + dual-read”而不是 destructive rename。
3. **正式模型放在 `packages/core`，迁移和兼容桥放在 `packages/provider/dx` 最合理**。`core` 负责跨系统实体和 SQLite store，`provider.dx` 已经持有 `project_root`、config/env、backup/recovery/import 等旧世界入口，适合承担自动迁移与 rollback orchestration。
4. **default project migration 应该是 additive、幂等、可回滚**。迁移只新增 `projects/workspaces/bindings/migration_runs` 及其记录，不修改历史 task/memory/import/backup snapshot；失败时按 `migration_run_id` 清理新增记录即可回滚。
5. **env 兼容桥在第一阶段只持久化“引用关系”，不迁移 secret 实值**。旧 `.env` / `.env.litellm`、`api_key_env`、`master_key_env`、Telegram token env 名仍是 runtime 真正事实源，但要被 project-scoped bridge 显式登记，供后续 Secret Store 接管。

详细依据见 `research/tech-research.md` 与 `research/online-research.md`。
