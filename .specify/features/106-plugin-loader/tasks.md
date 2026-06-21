# F106 User Plugin Loader — Tasks（Phase A：declarative 安全切片）

**Plan**: [plan.md](./plan.md) §2 / §6 | **Spec**: [spec.md](./spec.md) §13 Phase A
**范围**: declarative plugin only（无代码执行/watchdog/git）。安全可独立合入。**B/C 任务在各自会话细化。**
**不变量**: 0 regression vs f3d8a267；e2e_smoke 必过；PYTHONPATH 锁 worktree；禁 worktree uv sync。

| # | 任务 | 文件 | FR/AC | test |
|---|------|------|-------|------|
| **T1** | 数据模型：`PluginManifest`/`PluginProvides`/`PluginCapability`/`PluginState`/`PluginRejectedReason`/`PluginRecord`（未知字段宽容、name kebab 校验） | `packages/skills/src/octoagent/skills/plugins/manifest.py`（新） | FR-1.2/9.1 | `test_plugin_manifest.py::test_manifest_parse_and_validate` |
| **T2** | 纯 stat 发现+分类：扫 plugins_dir、`yaml.safe_load` manifest、校验、code-capable 触发文件集、**绝不 import** | `plugins/discovery.py`（新） | FR-1.1/1.3/1.4 | `test_plugin_discovery.py::test_pure_stat_classification`、`::test_no_import_during_discovery` |
| **T3** | EventType `PLUGIN_LOADED/REJECTED/TOGGLED/REMOVED` | `packages/core/src/octoagent/core/models/enums.py` | FR-9.1 | `test_plugin_events.py::test_event_types_defined` |
| **T4** | SkillDiscovery 扩 `scan(plugin_skill_dirs=)` + `SkillMdEntry.provenance` + plugin 源**最低优先级 reject-on-collision**；plugin_skill_dirs=None 字节级等价 | `packages/skills/.../discovery.py`、`skill_models.py` | FR-3.1/2.2 | `test_skill_discovery.py::test_plugin_dirs_lowest_priority_reject_collision`、`::test_no_plugin_dirs_baseline_equivalent` |
| **T5** | `PluginRegistry`：discover→威胁扫描(`scan_memory`)→注册 declarative skill→PluginRecord+事件；`asyncio.Lock`；单 plugin try/except 降级隔离；威胁 fail-open vs degraded-block | `plugins/registry.py`（新） | FR-2.5/3.1/4.2/4.4/5.1/5.3/7? | `test_plugin_registry.py::test_declarative_plugin_skill_no_approval`、`::test_bad_plugins_isolated_good_loads`、`::test_threat_flagged_rejected`、`::test_scanner_fail_open_vs_degraded_block` |
| **T6** | behavior overlay fallback-fill（仅 `KNOWLEDGE.md`，最低优先级，路径守卫）——**可选/暂缓**（若 invasive，标 plan §10 推 A.5） | `apps/gateway/.../services/agent_decision.py` | FR-3.5 | `test_plugin_behavior_overlay.py::test_knowledge_fallback_and_allowlist_and_path_traversal` |
| **T7** | bootstrap：`plugins_dir` DI + 段 7.5 `_bootstrap_user_plugins`（capability_pack 后、executors 前）+ 整段 try/except 降级 + `app.state.plugin_registry` | `apps/gateway/.../harness/octo_harness.py` | FR-1.5/10.1/10.2 | `test_plugin_bootstrap.py::test_bootstrap_order_and_degradation`、`::test_plugins_dir_di_isolation` |
| **T8** | REST `routes/plugins.py`：list/get/toggle/delete(仅 plugins_dir 内)/refresh + `main.py` include_router(protected) | `apps/gateway/.../routes/plugins.py`（新）、`main.py` | FR-6/8.1/8.2/8.4/8.5/8.6/8.8 | `test_plugins_api.py::test_list_get_toggle_delete_refresh`、`::test_delete_path_escape_403` |
| **T9** | toggle 持久：`.disabled` marker 增删 + 跨重启 | （T5/T8 内） | FR-3.1/3.3 | `test_plugin_registry.py::test_toggle_disable_enable_persists` |
| **T10** | e2e：tmp plugins_dir 混装 [好/坏 manifest/威胁/名冲突/code-capable] → 隔离降级 + gateway 正常 | `apps/gateway/tests/e2e/test_plugin_degradation_e2e.py`（新） | SC-002/005/006/010/011 | 该文件 |
| **T11** | 0 regression：全量 `pytest`（PYTHONPATH 锁 worktree）+ `pytest -m e2e_smoke` | — | FR-11.3/SC-012 | 全量 |
| **T12** | living-docs：新 `docs/codebase-architecture/plugin-loader.md`（Phase A 范围 + B/C roadmap + §0.3 residual）+ Blueprint module-design/milestones 同步标记 | docs | spec §8 | — |

## 验收门（Phase A 完成）

- 全量回归 0 regression vs f3d8a267 + e2e_smoke 8/8。
- declarative plugin 端到端：发现→注册→`skills list` 可见→主 Agent 可 load。
- code-capable plugin：发现+列出 `pending_approval`，**不注册其制品**（B 才审批加载）。
- 坏 plugin 隔离降级，gateway 正常（e2e 实证）。
- 名冲突不覆盖内置；威胁拒载无原文。
- Phase A 命中"代码执行安全敏感"边缘但本身无代码执行 → Codex + 第二模型 review（A 较轻，B/C 重点）。

## Phase B/C（roadmap，本会话不做，handoff）

见 plan §3。B = code_hash + approval + 专用 loader + pending 惰性 + hooks（Gate B 双评审 0 HIGH）；C = watchdog + git 硬化。
