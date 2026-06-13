# F117 Wave 2b — 双评审记录（Codex + Opus dual panel）

> Wave 2b = 运行时 read-switch（7 站从读 worker_profiles 切到读统一 agent_profiles 镜像）+ archive-sync。
> 回归 4135 = baseline（0 测试回归），但**双评审 panel 抓出系统性不变量缺口**——read-switch 不可独立安全。
> 评审：Codex（a7e5fc9，6 finding）+ Opus（a2949 2b，HIGH×2 + MEDIUM×3 + LOW×2 + CLEAN 清单）。

## 核心结论（两 panel 收敛）：read-switch 与 authoring write-switch 是耦合的，2b/2c 拆分漏耦合

read-switch 依赖一个**未声明的不变量**：「每个运行时可达的 worker 都有当前+完整的镜像」。
实际镜像只在 **publish/bind** 时建（_sync）。因此以下 worker 无完整镜像 → read-switch 退化：
- **draft/created/cloned/extracted 未发布 worker**：无镜像 → `resolve_worker_binding` 落 `builtin_fallback`（profile_id 变 `singleton:general`、工具变 builtin、source_kind 变）；`_resolve_direct_session_worker_profile` 返 None（session 被拒）；`_is_worker_profile_id` 返 False（session owner 误判）；chat model_alias 返空。
- **update/apply-without-publish**：改 worker_profiles 不同步镜像 → 运行时读到上次发布态（baseline 立即生效）。
- **resource_limits update**：不同步镜像（当前无运行时读 resource_limits，latent，Wave 4 删表后爆）。
- **部署→migrate 窗口**：旧代码归档/编辑的 worker 镜像 stale-active，直到 migrate-117 apply 才 reconcile。

**测试为什么全绿**：`_save_worker_with_mirror` test helper 无条件预建镜像，掩盖了上述缺口（且 helper 漏 `wp.metadata` → 同时掩盖 capability_pack `_resolve_profile_skill_selection` 删 fallback 的回归）。

## 逐 finding（合并 Codex + Opus，按严重度）

| # | 站点 | 问题 | 修复 |
|---|------|------|------|
| **HIGH-1** | capability_pack:415 resolve_worker_binding（经 worker_service:1136 spawn_from_profile 允许 DRAFT）+ worker_profile_ops:786 `_save_worker_profile_draft` / agent_service:632 create | draft/created/cloned worker 无镜像 → builtin_fallback | `_save_worker_profile_draft` + create/clone/extract 路径建/同步同 id 镜像（携 9 字段 + metadata）|
| **HIGH-2** | chat:247/256 + session_service:783 + session_projection_helpers:107 | 同根：无镜像 worker 在 4 个 presence-判别站误判（owner/model_alias/可用性/projection）| 同 HIGH-1 根修 |
| **MED-1** | worker_service:731/910 update/apply-without-publish | 已发布 worker 改草稿不同步镜像 → 运行时读旧发布态 | **✅ 拍板（用户 2026-06-13）：保 baseline「草稿即时生效」**——apply/update/draft 所有写路径同步镜像，未 publish 编辑也立即生效。不采"仅发布态 binds"。加回归测试：改草稿不 publish → binding 立即反映新 tools/model_alias |
| **MED-2 (codex)** | dispatch_service:686 + entity_ensure:218 GAP-A/B | 无 `is_worker_behavior_profile` guard：worker_profile_id 若指向 main/subagent，新代码用其 name/summary 作 worker persona（旧 fallback）| 加 worker guard，不满足按旧行为 fallback |
| **MED-2 (opus)** | agent_service:366 resource_limits | 写 worker_profiles.resource_limits 不同步镜像（latent，Wave 4 爆）| 同步镜像（仿 archive-sync）|
| **MED-3** | migration_117:417（Codex HIGH-4）+ test helper（Opus MED-3）| migration 既有镜像 UPDATE 只刷 9 列不刷 name/model_alias/metadata；test helper 漏 wp.metadata + version → 掩盖 dropped-fallback 回归 | migration UPDATE 补 name/model_alias/tool_profile/metadata；helper 复制 wp.metadata（update-then-overlay 仿生产）+ version；加 worker-metadata 回归测试（capability_provider_selection / permission_preset）|
| **LOW-1** | 部署→migrate 窗口 | 旧归档 worker 镜像 stale-active | 文档化「migrate-117 与 Wave 2b 同部署」或加 startup reconciliation |
| **LOW-2** | worker_service archive-sync | 镜像 metadata.worker_profile_status 未同步（cosmetic）| 归档时一并写或注明 |

## 评审判为 CLEAN（机械切换正确）
guard 放置方向正确（presence 站有 guard / 共享 model_alias 无 guard）；GAP-A/B persona `.summary` 忠实（镜像 summary==persona_summary==worker.summary）；`_resolve_direct_session_worker_profile` 返回类型改 AgentProfile 调用方仅用 truthiness；resolve_worker_binding worker 分支字段映射对已发布 worker 忠实；非切换的 get_worker_profile（编辑期 SoT 读）正确保留。

## 决策 + 下一步（Wave 2bc 耦合）

**read-switch 不可作为独立安全 wave 提交完成态**。修复方向二选一：
1. **镜像完整性**（较小）：所有 authoring 写路径（_save_worker_profile_draft / create / clone / extract / update-apply / resource_limits）同步同 id 镜像（携 9 字段 + metadata）+ GAP guard + migration 字段补全 + test helper metadata。read-switch 即安全。镜像在 Wave 2c authoring 直写统一表后塌缩。
2. **直接耦合 2c**（较大）：authoring 直写统一 agent_profiles 行（无镜像）+ 删镜像 builder + 全改名。一步到位。

**注意**：方案 1 的 _sync 加法在 Wave 2c 会被移除（authoring 直写统一表后镜像消失），属过渡。但方案 1 让 read-switch 现在即安全可提交。

**behavior-preservation 注意**：MED-1 的"published-only binds vs draft-immediately"是真实语义抉择（需 intent 拍板）；draft-save 同步镜像若走 `_sync_worker_profile_agent_profile` 会引入 materialize 副作用（仿 archive-sync 用 surgical 直写镜像规避）。这些细节使修复 correctness-critical，需谨慎（不可在 context 紧张时仓促）。

> 本 review 是双评审 panel 价值实证：测试全绿（helper 掩盖）下，两 panel 收敛抓出 read-switch 系统性不变量缺口。提交前不可视 Wave 2b 为完成。
