# Verification Report: Feature 025 第二阶段 — Secret Store + Unified Config Wizard

## Status

- 时间: 2026-03-08
- 结果: PASS
- 范围: Feature 025-B（active project selector、project CLI、unified wizard session、Secret Store lifecycle、runtime short-lived injection、doctor/onboard 提示闭环）

## Commands

```bash
uv run ruff check \
  packages/core/src/octoagent/core/models/project.py \
  packages/core/src/octoagent/core/store/project_store.py \
  packages/core/src/octoagent/core/store/sqlite_init.py \
  packages/provider/src/octoagent/provider/dx/cli.py \
  packages/provider/src/octoagent/provider/dx/config_schema.py \
  packages/provider/src/octoagent/provider/dx/control_plane_models.py \
  packages/provider/src/octoagent/provider/dx/doctor.py \
  packages/provider/src/octoagent/provider/dx/onboarding_service.py \
  packages/provider/src/octoagent/provider/dx/project_commands.py \
  packages/provider/src/octoagent/provider/dx/project_selector.py \
  packages/provider/src/octoagent/provider/dx/secret_commands.py \
  packages/provider/src/octoagent/provider/dx/secret_models.py \
  packages/provider/src/octoagent/provider/dx/secret_refs.py \
  packages/provider/src/octoagent/provider/dx/secret_service.py \
  packages/provider/src/octoagent/provider/dx/secret_status_store.py \
  packages/provider/src/octoagent/provider/dx/wizard_session.py \
  packages/provider/src/octoagent/provider/dx/wizard_session_store.py

uv run pytest \
  packages/provider/tests/dx \
  packages/provider/tests/test_onboard.py \
  packages/provider/tests/test_project_migration_cli.py \
  packages/provider/tests/test_update_commands.py \
  packages/provider/tests/test_doctor.py \
  packages/core/tests/test_project_models.py \
  packages/core/tests/test_project_store.py \
  packages/core/tests/test_sqlite_init_project.py -q
```

## Results

- `ruff check`: PASS
- `pytest`: `148 passed`

## Additional Smoke

- 使用 `CliRunner` 跑通 `project create -> project edit --wizard -> project edit --apply-wizard -> secrets configure -> secrets audit -> secrets apply --dry-run`
- 覆盖 `env/file/exec/keychain` 四类 `SecretRef` 解析与错误分类
- 覆盖 managed / unmanaged runtime `secrets reload` 路径

## Covered Assertions

- core 层新增 `ProjectSecretBinding` / `ProjectSelectorState` 模型与 SQLite 表，能稳定持久化 project-scoped secret metadata 和 active project selection。
- `ConfigSchemaDocument + uiHints` 由现有配置模型产出，CLI wizard 以同一 contract 渲染字段，而不是维护独立字段语义。
- `octo project create/select/edit/inspect` 已成为正式主路径；`inspect` 输出 readiness、binding 摘要、runtime sync 状态，且不会暴露 secret 明文。
- `project edit --wizard` 支持 start / resume / status / cancel；`apply-wizard` 只落盘 redacted draft config，并把 session 推进到 `secrets` 待处理状态，而不是伪装成全流程完成。
- `octo secrets audit/configure/apply/reload/rotate` 覆盖 project-scoped secret lifecycle；canonical store 只保存 `SecretRef` metadata，不保存 secret 实值。
- `secrets apply --dry-run` 只输出 materialization preview；真实 apply 会把 binding 标成 `needs_reload`，等待 runtime sync。
- `secrets reload` 在 managed runtime 下复用 024 `restart + verify`；在 unmanaged runtime 下明确返回 `action_required`，不伪装成热重载成功。
- `rotation_pending` 会被 `audit` / `doctor` / `project inspect` 视为待重新 apply；unmanaged reload 也会刷新 binding 状态，避免长期卡在 `needs_reload`。
- secret apply / materialization 状态文件已按 `project_id` 分目录持久化，multi-project inspect / audit 不再串读别的 project 生命周期状态。
- disabled provider 不再派生 secret target；schema hints 中 provider secret target 也已发布 canonical key（`providers.{provider_id}.api_key_env`）。
- `doctor` 与 `onboard` 已纳入 secret readiness / runtime sync 提示，能把缺失 binding、未 reload、解析失败等问题收口成下一步动作。
- `keyring` 依赖已加入并锁定；无 backend 时仍会显式降级，不会 silent no-op。

## Security Notes

- secret 实值未写入 `octoagent.yaml`、SQLite canonical records、`secret-apply.json`、`secret-materialization.json`、CLI 输出或 wizard session 持久化内容。
- runtime short-lived injection 仅在 `secrets reload` 的临时环境变量边界解出明文，命令返回后会恢复当前进程环境。

## Notes

- `apply-wizard` 后 session 状态刻意保持为 `action_required/secrets`，因为 config 已写入但 secret lifecycle 仍待 `configure/apply/reload`；这比直接标记 `completed` 更符合 025-B 的交付边界。
- 本次无需额外回写 `docs/blueprint.md` / `docs/m3-feature-split.md`，因为 025-B 仍处于当前 worktree 的实现闭环阶段，后续统一 merge 时再同步里程碑状态更稳妥。
