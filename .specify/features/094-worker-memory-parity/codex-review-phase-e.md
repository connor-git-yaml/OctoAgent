# Phase E Codex Adversarial Review 闭环

**Phase**: E（octo memory migrate-094 CLI no-op 实现）
**Review 时间**: 2026-05-09
**Model**: Codex CLI (model_reasoning_effort=high)
**输入**: tasks.md Phase E E1-E6 全部 staged diff
**Findings 总数**: 5（0 HIGH + 1 MED + 4 LOW）

## Findings 处理决议

### MED-1: octo memory migrate-094 默认 DB 路径与主 CLI/运行时不一致 ✅ 接受 + 闭环

**Evidence**: `_resolve_default_memory_db_path()` 硬编码 `~/.octoagent/data/memory.db`；但实际 OctoAgent **memory tables 与 core tables 在同一个 octoagent.db**（init_memory_db(store_group.conn) 与 init_db 共用 conn）。`core/config.py:get_db_path()` 返回 `data/sqlite/octoagent.db` 并支持 `OCTOAGENT_DB_PATH` env 覆盖。

**修复**:
- `memory_commands.py` 重命名 `_resolve_default_memory_db_path` → `_resolve_default_db_path`
- 直接复用 `from octoagent.core.config import get_db_path`
- 默认 db_path = `Path(get_db_path())`，自动支持 `OCTOAGENT_DB_PATH` env
- CLI help 信息更新："SQLite 数据库文件路径（默认 get_db_path() / OCTOAGENT_DB_PATH env）"

### LOW-2: migration 模块注释不准（apply 不会主动触发 init）✅ 接受 + 闭环

**Evidence**: `_existing_run_id` 注释说 "apply 路径会先 init 触发兜底再走 INSERT"；实际 apply 在列缺失时直接 raise。

**修复**: 注释更正为 "apply 不会主动触发 init——Phase E 依赖 Phase C schema 已就位"，与 run_apply 实际行为对齐。

### LOW-3: CLI 测试覆盖缺口 ✅ 接受 + 闭环

**Evidence**: rollback not_found exit=1 / dry-run already_applied exit=0 / --json-output 三个 contract 未被 CLI 级测试锁住。

**修复**: 新增 3 个 CLI 测试：
- `test_cli_migrate_094_rollback_not_found_exits_1`：rollback 不存在 run_id 时 exit=1 + 输出 not_found
- `test_cli_migrate_094_dry_run_after_apply_exits_0`：apply 后再跑 dry-run 时 exit=0 + 显示 already_applied_run_id
- `test_cli_migrate_094_json_output`：--json-output 输出合法 JSON 字典

### LOW-4: user-facing docs 未覆盖新 ops 命令 ✅ 推迟到 Phase F

**Evidence**: `docs/blueprint/deployment-and-ops.md` 没 migrate-094 操作说明。

**决策**: Phase F completion-report 阶段一并补；属于 F094 范围内的文档收尾，但不属于 Phase E 实施 task 范围。

### LOW-5: spec 中 idempotency_key 旧短名残留 ✅ 接受 + 闭环

**Evidence**: spec.md US4 / NFR-6 / Risks 仍引用短键 `migration_094_worker_private`；plan/tasks 已锁长键 `octoagent.memory.migration.094.worker_memory_parity.noop.v1`。

**修复**: 用 sed 全文替换 spec.md 中所有 `idempotency_key="migration_094_worker_private"` 为长键；手动修 line 124 不带 backtick 的引用。验证残留：grep `migration_094_worker_private` 仅剩文件路径引用（migration_094_worker_private.py 模块名）—— LOW-5 闭环。

## Codex 验证无 finding 项

- INSERT 字段顺序与 DDL 一致（19 列对齐，已被 test_e2_apply_writes_audit_record 验证 idempotency_key 字段值正确）
- rollback exit code 语义正确（rollback not_found=失败 exit 1；dry-run already_applied=查询成功 exit 0）
- 互斥校验早于 db_path 检查（CLI 参数错误优先）
- B6 captured namespace 在显式 PROJECT_SHARED 路径下不形成 Phase E blocker（Phase F 范围）
- module 入口（python -m）作为 escape hatch 可接受

## 闭环汇总

| Finding | 严重度 | 处理决议 | 落地章节 |
|---------|--------|----------|----------|
| MED-1 | MED | **接受** | get_db_path() 复用 + 默认 OCTOAGENT_DB_PATH env 支持 |
| LOW-2 | LOW | **接受** | 注释更正 |
| LOW-3 | LOW | **接受** | 3 个新增 CLI 测试 |
| LOW-4 | LOW | **推迟到 Phase F** | docs 在 completion-report 阶段一并补 |
| LOW-5 | LOW | **接受** | spec.md 短键全替换为长键 |

## 全量回归验证

- packages/ + apps/gateway/tests（不含 e2e_live）: **3027 passed + 2 skipped + 1 xfailed + 1 xpassed**——0 regression vs Phase B 末（3013 → 3027 +14 Phase E 测试）
- F094 Phase E 专项测试: 14 个全 PASSED
  - test_e1_dry_run_returns_zero_with_reason
  - test_e1_dry_run_does_not_modify_db
  - test_e2_apply_writes_audit_record
  - test_e2_apply_does_not_modify_sor
  - test_e3_apply_is_idempotent
  - test_e4_rollback_removes_audit_record
  - test_e4_rollback_then_apply_again
  - test_e4_rollback_unknown_run_id_returns_not_found
  - test_cli_migrate_094_dry_run
  - test_cli_migrate_094_apply_then_idempotent
  - test_cli_migrate_094_rejects_no_action
  - test_cli_migrate_094_rollback_not_found_exits_1（LOW-3 配套）
  - test_cli_migrate_094_dry_run_after_apply_exits_0（LOW-3 配套）
  - test_cli_migrate_094_json_output（LOW-3 配套）

## Commit message 摘要

`Codex review (Phase E): 0 high / 1 medium 已处理（接受 db_path 复用）/ 4 low 已处理 3（注释/CLI 测试/spec 短键）+ 1 推迟（Phase F docs） / 0 wait`
