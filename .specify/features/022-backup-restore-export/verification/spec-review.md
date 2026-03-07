# Spec Review: Feature 022 — Backup/Restore + Export + Recovery Drill

**特性分支**: `codex/feat-022-backup-restore-export`
**审查日期**: 2026-03-07
**审查范围**: FR-001 ~ FR-016

## 结论

- 结论: **PASS**
- 说明: 022 的 CLI / provider DX / gateway / frontend 最小入口均已打通，`backup create -> restore dry-run -> recovery summary -> chats export` 形成用户可达闭环。

## FR 对齐检查

| FR | 状态 | 证据 |
|----|------|------|
| FR-001 | ✅ | `backup_commands.py` 新增 `octo backup create` |
| FR-002 | ✅ | `backup_service.py::_snapshot_sqlite()` 使用 SQLite online backup |
| FR-003 | ✅ | `BackupManifest` + ZIP `manifest.json` |
| FR-004 | ✅ | bundle 默认覆盖 sqlite / artifacts / config / chats scope 摘要 |
| FR-005 | ✅ | 默认排除 `.env` / `.env.litellm` 并在 manifest 中显式记录 |
| FR-006 | ✅ | `octo restore dry-run` 仅生成 `RestorePlan`，不执行 destructive restore |
| FR-007 | ✅ | `RestorePlan` 包含 restore items / conflicts / warnings / next actions |
| FR-008 | ✅ | manifest 缺失、checksum 错误、schema version 不兼容均阻塞 |
| FR-009 | ✅ | `octo export chats` 支持 task/thread/time filter |
| FR-010 | ✅ | `ExportManifest` 记录 filters / task refs / event_count / artifact refs |
| FR-011 | ✅ | `RecoveryStatusStore` 持久化 `latest-backup.json` 与 `recovery-drill.json` |
| FR-012 | ✅ | Web 提供 recovery summary + backup/export 触发入口 |
| FR-013 | ✅ | `BACKUP_STARTED/COMPLETED/FAILED` 写入 dedicated operational task |
| FR-014 | ✅ | recovery drill 失败返回结构化 `failure_reason + remediation` |
| FR-015 | ✅ | `/ready` diagnostics 补充 recovery ready 摘要 |
| FR-016 | ✅ | 未实现 destructive restore / 远程同步 / 第二套主数据模型 |

## 边界场景检查

- bundle 缺 `manifest.json`: ✅ 阻塞并给出 invalid bundle 说明
- 目标目录已存在配置文件: ✅ 标记为 `PATH_EXISTS` blocking conflict
- 没有匹配 chats 数据: ✅ 允许空结果导出，返回码仍为成功
- recovery 状态文件损坏: ✅ 自动备份为 `.corrupted` 并回落到默认状态
- backup 生命周期审计: ✅ 使用 `ops-recovery-audit` operational task 统一落事件
