# Verification Report: Feature 025 — Project / Workspace Domain Model + Default Project Migration

## Status

- 时间: 2026-03-08
- 结果: PASS
- 范围: Feature 025 第一阶段 migration gate（Project/Workspace domain、default project migration、env bridge、validation/rollback、gateway/CLI/bootstrap 接入）

## Commands

```bash
uv run --project octoagent python -m ruff check \
  octoagent/packages/core/src/octoagent/core/models/project.py \
  octoagent/packages/core/src/octoagent/core/store/project_store.py \
  octoagent/packages/core/src/octoagent/core/store/sqlite_init.py \
  octoagent/packages/core/src/octoagent/core/store/__init__.py \
  octoagent/packages/core/src/octoagent/core/models/__init__.py \
  octoagent/packages/core/tests/test_project_models.py \
  octoagent/packages/core/tests/test_project_store.py \
  octoagent/packages/core/tests/test_sqlite_init_project.py \
  octoagent/packages/provider/src/octoagent/provider/dx/project_migration.py \
  octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py \
  octoagent/packages/provider/src/octoagent/provider/dx/backup_service.py \
  octoagent/packages/provider/src/octoagent/provider/dx/chat_import_service.py \
  octoagent/packages/provider/tests/test_project_migration.py \
  octoagent/packages/provider/tests/test_project_migration_cli.py \
  octoagent/packages/provider/tests/test_backup_service.py \
  octoagent/packages/provider/tests/test_chat_import_service.py \
  octoagent/packages/provider/tests/test_backup_commands.py \
  octoagent/packages/provider/tests/test_config.py \
  octoagent/apps/gateway/src/octoagent/gateway/main.py \
  octoagent/apps/gateway/tests/test_main.py

uv run --project octoagent python -m pytest \
  octoagent/packages/core/tests/test_project_models.py \
  octoagent/packages/core/tests/test_project_store.py \
  octoagent/packages/core/tests/test_sqlite_init_project.py \
  octoagent/packages/provider/tests/test_project_migration.py \
  octoagent/packages/provider/tests/test_project_migration_cli.py \
  octoagent/packages/provider/tests/test_backup_service.py \
  octoagent/packages/provider/tests/test_chat_import_service.py \
  octoagent/packages/provider/tests/test_backup_commands.py \
  octoagent/packages/provider/tests/test_config.py \
  octoagent/apps/gateway/tests/test_main.py -q
```

## Results

- `ruff check`: PASS
- `pytest`: `49 passed`

## Covered Assertions

- core 层新增 `Project` / `Workspace` / `ProjectBinding` / `ProjectMigrationRun` 模型可校验、可序列化。
- SQLite 主库新增 `projects` / `workspaces` / `project_bindings` / `project_migration_runs` 表。
- default project migration 在空实例上可创建唯一 default project + primary workspace。
- legacy `tasks` / `memory_*` / `chat_import_*` / backup snapshot / `.env` / `.env.litellm` / `octoagent.yaml` env refs 可回填为 project/workspace bindings。
- migration 重复执行幂等，不会重复生成 canonical records。
- validation failure 会回滚当前 run 的未提交写入。
- rollback 仅允许命中成功 apply 的 run，且 `latest` 会跳过失败 run 指向最近一次成功 apply。
- `config migrate --dry-run` 不落盘；`config migrate --rollback latest` 可清理本次 run 的 canonical records。
- gateway lifespan、backup service、chat import service 已接入 ensure migration。

## Notes

- 发现并修复了一条与当前日期有关的旧测试漂移：`test_export_chats_filters_events_and_artifacts_by_time_window` 原先写死绝对时间，已改为相对时间，避免未来再次因日期推进误报。
- `docs/blueprint.md` 与 `docs/m3-feature-split.md` 的 Feature 025 第一阶段状态尚未同步，本次实现保留为后续文档回写项。
