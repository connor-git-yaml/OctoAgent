# F117 Wave 2c-2c（R 读切 + W 停写）双评审 panel 裁定

> 范围：cluster `fe4ac99b`（R reverse-converter 读切）+ `a93f44a4`（W 停写 + listing + revision + metadata）。
> 评审 = Codex（GPT-5.x）+ 独立 Opus，均对抗式。**本轮两评审收敛**（不同于 2c-2b 的大分歧）：
> 核心 R+W 改动**实测等价**，findings 全部源于**故意推迟的"部分停写"**（agent_service/_coordinator 仍写
> worker_profiles → W4 id-收口）。

## 实测确认等价（双评审 deterministic 一致）
- **publish 原子性**：`_publish` 删内部 commit；3 个 `_sync` 调用点（apply/publish/bind）均 handler 末尾单 commit。无 revision 丢失路径。
- **revision FK 时序**：`agent_profile_revisions` FK→agent_profiles **真启用**（Opus 实测删镜像插 revision → IntegrityError）。但所有 publish 路径 existing/saved 经 `_get_worker_profile_via_mirror`/draft-save，镜像必先于 revision 存在 → FK 满足。无违约路径。
- **metadata round-trip 守恒**：source_work_id/source_task_id/任意 custom key 经 builder(`include_user_metadata=True`)→镜像→reverse-converter 守恒（实测）；UPDATE 路径 existing.metadata 取自已剥离 DTO，marker 不累积。
- **dispatch-after-edit 不丢 user metadata**：authoring 写 canonical 镜像 instruction_overlays 非空 → 2c-2b flip 直接信任 existing **不重建** → materialize（include_user_metadata=False）不触发；且停写后无 worker_profiles 行，materialize 即触发也 return existing。entity_ensure 本 Wave 零改动。
- **resource_limits**：两表读路径 baseline 就**从不持久化**（死列，恒 {}）→ reverse-converter 设 {} 与 baseline 一致。非回归。
- **listing dedup tie-break**：`existing is None or profile_id == logical_id` 两种迭代序均 bare 胜；ULID 使跨 worker logical_id 撞车不可达；实测 create_worker_with_project worker listing 恰一条。

## 裁定汇总

| finding | severity | 裁定 / 处理 |
|---|---|---|
| **M-1** `agent_service:366` resource_limits worker 分支仍 get_worker_profile → 停写后 **404**（影响所有 lifecycle worker，UI 可达回归）| MED（用户视角真回归）| **本 cluster 修**：读切镜像（+ 前缀 fallback + worker guard）→ 200，回写镜像（不复活 worker_profiles）。resource_limits 死列行为同 else agent 分支 |
| **M-2** listing 对 create_worker_with_project worker 暴露**前缀 id**（非 bare）→ 迁移前 revision 历史 UI 不可见 + works 匹配键漂移 | MED（仅 create_worker_with_project worker，测试/实例 **0**）| **归 W4 id-收口**：agent_service/_coordinator 改写 canonical bare 镜像 + project.default=bare，统一 id；存量前缀行 W4 migration reconcile |
| **Codex-HIGH / 部分停写** agent_service create(628/685)/_coordinator(983) 仍写 worker_profiles | Codex HIGH / Opus must-fix | **故意推迟 W4**（id-收口 + migration 耦合）。W 只停 lifecycle（bare-wpid 干净路径）；程序化创建（前缀 id）写切涉 project.default + 存量行，与 migration 同批。**显式归档非遗漏**（reviewers 允许"explicitly archive to W4"）|
| **L-1** reverse-converter 黑名单剥离：user metadata key 撞 `_MIRROR_MARKER_METADATA_KEYS` 名（worker_profile_id/behavior_agent_slug 等 8 名）被静默剥 | LOW（实践 worker metadata 仅 source_*，extract 的 source_work_id 不撞；无 manifestation）| **归档（W4 或独立）**：marker 收敛到单一前缀命名空间物理隔离，去硬编码黑名单。当前无触发 |

## 下游须知（W4 必做，闭合本轮归档）
1. **id-收口**：agent_service create / `_handle_agent_create_worker_with_project`(628/685) + `_coordinator._ensure_default_main_agent_bootstrap`(983) 停写 worker_profiles——agent_service worker 写 canonical **bare** 镜像 + project.default=bare；_coordinator 主 Agent（kind=main）写干净 main profile（去 worker_profiles 行 + 去 agent-profile-{id} 前缀 reverse-replace）。**这同时闭合 M-2 + Codex-HIGH + 删 read helper/listing 的前缀 fallback shim**。
2. **revision backfill**：W4 migration 已拷 worker_profile_revisions→agent_profile_revisions（migration:518）；确认 id-收口后 bare-key 对齐（M-2 的迁移前历史在前缀 worker 收口 bare 后可见）。
3. **L-1 marker namespace**：reverse-converter 黑名单 → 单一 `_mirror.*` 前缀隔离。
4. **works 匹配**：id-收口后 listing/DTO/revision/works 四处 id 一致，works_by_profile_id 不漂移。

回归：M-1 fix 后 4139 passed 0 regression + e2e_smoke 8/8（74 authoring 测试 PASS）。**0 HIGH 残留于本 cluster 实际改动**（partial-stop 显式归档 W4）。
