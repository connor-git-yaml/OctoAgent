# Feature 082 — 迁移依赖盘点

> 作者：Connor · 日期：2026-04-27 · 上游：plan.md

P0-P4 修改清单 + 老用户兼容窗口策略。

---

## 类别 A：Python 模型 + Schema（P0 修改）

| 文件 | 改动 | Phase |
|------|------|------|
| `packages/core/.../models/agent_context.py:188` | `Field(default="你")` → `Field(default="")` + 新增 `last_synced_from_profile_at: datetime \| None` | P0 ✅ |
| `packages/core/.../store/sqlite_init.py:344-360` | DDL `DEFAULT '你'` → `DEFAULT ''` + 新增 `last_synced_from_profile_at TEXT` 列 | P0 ✅ |
| `packages/core/.../store/sqlite_init.py:_migrate_legacy_tables` | 新增 `ALTER TABLE owner_profiles ADD COLUMN last_synced_from_profile_at TEXT` | P0 ✅ |
| `packages/core/.../store/agent_context_store.py:save_owner_profile` | INSERT/UPDATE 含新列 | P0 ✅ |
| `packages/core/.../store/agent_context_store.py:_row_to_owner_profile` | 老表（无新列）通过 `row.keys()` 兜底 | P0 ✅ |
| `apps/gateway/.../services/startup_bootstrap.py:_ensure_owner_profile` | 显式 `preferred_address=""` | P0 ✅ |

---

## 类别 B：Bootstrap 状态机（P1 修改）

| 文件 | 改动 | Phase |
|------|------|------|
| `packages/core/.../behavior_workspace.py:147` `is_completed()` | 仍只看 `onboarding_completed_at`（保持简单语义）；实质完成检查移到新服务 | P1 |
| `packages/core/.../behavior_workspace.py:274` `_detect_legacy_onboarding_completion` | 收紧：要求实质证据（OwnerProfile 非默认 OR USER.md 已填充）才回填 | P1 |
| `apps/gateway/.../services/bootstrap_integrity.py`（新建）| `BootstrapIntegrityChecker.check_substantive_completion(project_root) → bool` | P1 |
| `apps/gateway/.../services/agent_context.py:resolve_behavior_workspace` | 加载前调 IntegrityChecker 决定是否注入 BOOTSTRAP.md | P1 |

---

## 类别 C：完成路径 + Profile 回填（P2 修改）

| 文件 | 改动 | Phase |
|------|------|------|
| `apps/gateway/.../services/bootstrap_orchestrator.py`（新建）| `BootstrapSessionOrchestrator.complete_bootstrap()` 编排器 | P2 |
| `apps/gateway/.../services/inference/profile_generator_service.py:generate_profile` | 末尾调 `_sync_to_owner_profile()` | P2 |
| `apps/gateway/.../services/inference/profile_generator_service.py:_sync_to_owner_profile`（新增）| 字段冲突策略（用户显式 > LLM 推断 > 默认）；写 `last_synced_from_profile_at` | P2 |
| `apps/gateway/.../services/builtin_tools/bootstrap_tools.py`（新建）| LLM 工具 `bootstrap.complete()` | P2 |

---

## 类别 D：USER.md 动态生成（P3 修改）

| 文件 | 改动 | Phase |
|------|------|------|
| `packages/core/.../behavior_templates/USER.md.j2`（新建）| Jinja2 模板 | P3 |
| `apps/gateway/.../services/user_md_renderer.py`（新建）| `UserMdRenderer.render(owner_profile, profile_data)` + `write(path)` | P3 |
| `apps/gateway/.../services/agent_context.py:_build_system_blocks` | 检测 USER.md 含占位符 → fallback（不注入 USER.md 块） | P3 |
| `BootstrapSessionOrchestrator.complete_bootstrap` | 调 `UserMdRenderer.render() + write()` | P3 |

---

## 类别 E：CLI 命令 + 多 root 收敛（P4 修改）

| 文件 | 改动 | Phase |
|------|------|------|
| `packages/provider/.../dx/config_commands.py` | 新增 `octo bootstrap reset / migrate-082 / rebuild-user-md` 子命令 | P4 |
| `packages/provider/.../dx/config_commands.py` | 新增 `octo cleanup duplicate-roots` 子命令 | P4 |
| `apps/gateway/.../main.py` lifespan | 启动时检测多 root + warn | P4 |
| `apps/gateway/.../services/bootstrap_migrate_082.py`（新建） | 检测/重置逻辑 | P4 |

---

## 类别 F：测试（贯穿 P0-P4）

| Phase | 测试 |
|-------|------|
| P0 | `test_owner_profile_default_empty.py`（默认 `""`，非 `"你"`）/ `test_sqlite_init_owner_profile_v2.py`（DDL + ALTER 兼容老表）|
| P1 | `test_bootstrap_integrity_checker.py`（6 条：默认/真完成/误标/legacy 收紧/mark_completed/USER.md 填充）|
| P2 | `test_bootstrap_orchestrator.py`（5 条：完整链路/回滚/字段冲突/同步成功/时间戳）|
| P3 | `test_user_md_renderer.py`（4 条：默认值/含画像/列表为空/写入路径）+ `test_agent_context_user_md_fallback.py`|
| P4 | `test_bootstrap_cli.py`（6 条：reset/migrate-082 dry-run/rebuild-user-md/cleanup duplicate-roots）|

---

## 类别 G：老用户兼容窗口策略

| 状态 | 启动行为 | 推荐操作 |
|------|----------|----------|
| 全新用户 | 默认 `preferred_address=""` + Bootstrap 真实跑通 | 走引导 |
| 老用户（`preferred_address='你'` 是伪默认）| P0 后**不静默清洗**——保留 `'你'`（避免误改用户数据）；P4 `octo bootstrap migrate-082` 检测后建议 reset | 跑 `octo bootstrap migrate-082 --dry-run` 确认；可选 `reset` |
| 老用户（`preferred_address='你'` 是真用户输入）| 同上保留 | 用户手动改 |
| 老用户（已真完成 + profile 有真实数据）| `is_completed=True` 正常 | 无 |
| 多 root 并存 | warn + 不阻断 | `octo cleanup duplicate-roots` |

**关键决策**：P0 不在启动时静默清洗 `'你'` → `''`——SQLite 层面保留兼容性，让用户主动选择 reset。

---

## P0 验收 checklist

- [x] `OwnerProfile.preferred_address` 默认 `""`
- [x] `OwnerProfile.last_synced_from_profile_at` 字段存在
- [x] SQLite DDL 默认 `''`
- [x] SQLite migration 加新列（兼容老库）
- [x] `agent_context_store` INSERT/UPDATE/SELECT 处理新列
- [x] `_ensure_owner_profile` 显式 `preferred_address=""`
- [x] 老库（无 `last_synced_from_profile_at` 列）能继续读取（row.keys() 兜底）
- [ ] Feature 081 的 2081 条测试继续通过
- [ ] P0 测试：默认值 + DDL + migration（待加）
