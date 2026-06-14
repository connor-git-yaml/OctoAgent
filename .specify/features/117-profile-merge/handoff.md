# F117 Handoff — Wave W3/W4 resume 指南

> 状态：**…2c-2b + 2c-2c（R 读切 reverse-converter + W authoring 停写 worker_profiles）完成；R+W cluster 双评审收敛 0 HIGH（M-1 已修，partial-stop 显式归 W4）。下一步 W3（FE 全改名 + wire）+ W4（migration + 删 materialize-on-read + 删类表 + AgentRuntime 塌缩 + agent_service/_coordinator id-收口 + completion）**。
> 分支 `feature/117-profile-merge`（25 commits，**未 push**），worktree `.claude/worktrees/F117-profile-merge`，基于 origin/master `7199f468`。

## 已完成（21 commits）
```
a621bce0 refactor(F117): Wave 2c-2b 完整性 guard（Codex 双评审 [2] 源头修复）
4aca1d5e refactor(F117): Wave 2c-2b 翻转 materialize-on-read 为 create-if-absent（行为零变更）
75e3013d refactor(F117): Wave 2c-2a authoring 持久化镜像统一 canonical builder（行为零变更）
b315b7dc refactor(F117): Wave 2c-1 规范化 worker 镜像 builder（行为零变更基础）
71c0a044 / 0f7a4b99 / 67201a40 = Wave 2b+2bc read-switch 安全 + 再评审
450299b3 = Wave 2a store 地基 + 枚举/视图改名 ... Wave 0/1 + migration + docs
```
- **Phase 1-2 + 双 Gate**：A1 + 全改名（拍板）。migration_117 副本验证，真实例未动。
- **Wave 0/1/2a/2b/2bc + 2c-1**：吸收 9 字段 + populate + revision store 地基 + 枚举/视图改名 + 7 站运行时读切统一镜像 + 镜像完整性闭合 + 抽 canonical `build_worker_agent_profile`（entity_ensure 委托）。
- **Wave 2c-2a**（75e3013d）：authoring **两条持久化 runtime 镜像路径**（`_sync_worker_profile_agent_profile` + `_save_worker_profile_draft` draft-refresh）从旧 incomplete builder 切到 canonical `build_worker_agent_profile`。镜像此后含运行时读的 instruction_overlays + memory_recall ≡ materialize-on-read 输出。slug 保 name-based；溯源 key 收敛 source_worker_profile_id；_sync 删 revision 形参。**范围收窄**：worker_service:152 瞬态文档镜像不动（喂 build_behavior_system_summary 读 bootstrap_template_ids，canonical 改文档输出）；旧 `_build_agent_profile_from_worker_profile` 仅 152 用本波保留。4137=baseline + e2e 8/8。

- **Wave 2c-2b**（4aca1d5e flip + a621bce0 guard）：`_resolve_agent_profile` 翻转 always-rebuild → create-if-absent——已存在的**完整** worker 镜像（`is_worker_behavior_profile` + `instruction_overlays` 非空）直接信任，不再每 dispatch 从 worker_profiles 重建覆盖；残缺/缺失镜像 fallback 重建（保留 self-heal）。**2c-2 cluster 双评审**（Codex 4 HIGH / Opus 0 HIGH，主节点 deterministic 裁定，详 [wave-2c2b-review.md](./wave-2c2b-review.md)）：[1][5] 幻觉/等价驳回（git show master builder 实证不 merge worker_profile.metadata）+ [4] pre-existing 驳回 + [3] 采纳归 2c-2c + **[2] 采纳已加完整性 guard 源头修复**。4139=baseline+2 测试 0 regression + e2e 8/8。**0 HIGH residual**。

- **Wave 2c-2c-R**（fe4ac99b）：authoring by-id 读切统一 agent_profiles 镜像——reverse-converter `build_worker_dto_from_agent_profile`（剥 source_* marker → DTO metadata 等价 baseline）+ helper `_get_worker_profile_via_mirror`（前缀 fallback + worker guard）。**返 WorkerProfile 避类型 cascade**（forward A1 探针教训）。4139=baseline。
- **Wave 2c-2c-W**（a93f44a4 + M-1 fix）：authoring lifecycle（draft/publish/archive）停写 worker_profiles 直写镜像（Option B 单写）。**stop-write 触发 3 耦合一并解**：atomic publish（删 _publish commit，[3] 根除）+ listing 读切 list_agent_profiles+dedup + revision 切 agent_profile_revisions（FK 强制）+ metadata-carrying（`include_user_metadata`，extract source_work_id 守恒）。**R+W cluster 双评审收敛 0 HIGH**（详 [wave-2c-rw-review.md](./wave-2c-rw-review.md)）：核心实测等价；M-1 resource_limits 404 已修；M-2/partial-stop/L-1 显式归 W4。4139=baseline+e2e 8/8。

## ⚠ 下一步：W3（FE 全改名）+ W4（删除 + migration + id-收口）
> **2c-2c 主体完成**：worker_profile_ops/worker_service authoring 已脱离 worker_profiles（读 reverse-converter + 写直写镜像 + revision 切 agent_profile_revisions）。仅 agent_service create/_coordinator 程序化创建 + materialize-on-read legacy 读仍用 worker_profiles（W4 收口）。

**W3 — FE 全改名 + wire**：action_id `worker_profile.*`→`agent_profile.*` / resource_type + `WorkerProfilesDocument`→`AgentProfilesDocument` / 路由 / TS 类型 16 文件 + FE 测试（详 refactor-plan §5 Wave 3）。机械改名，Codex + 自查 grep。

**W4 — 物理删除 + 真迁移**（详 refactor-plan §5 Wave 4 + [wave-2c-rw-review.md](./wave-2c-rw-review.md) §下游）：
- **agent_service/_coordinator id-收口**（闭 review M-2 + Codex-HIGH + partial-stop）：create(628/685)/_coordinator(983) 停写 worker_profiles——agent_service 写 canonical bare 镜像 + project.default=bare；_coordinator 主 Agent 写干净 main profile（去前缀 reverse-replace）。删 read helper/listing 的前缀 fallback shim + reverse-converter 黑名单 namespace 化（闭 L-1）。
- 删 materialize-on-read（entity_ensure:950，读 worker_profiles 随表删）+ 旧 `_build_agent_profile_from_worker_profile`（worker_service:152 文档路径迁 canonical）+ `_sync_worker_profile_agent_profile`。
- 删 WorkerProfile/WorkerProfileRevision 类 + worker 表 + store 方法 + AgentRuntime.worker_profile_id 塌缩 + works 改名。
- migration CLI + revision backfill 对齐（bare-key）+ 残留扫描 + completion-report + 真实例迁移（**用户确认+备份**）。
> **每子波 0 regression（4139）+ e2e + 双评审 + deterministic。** 真实例 migrate-117 --apply + push 均待用户拍板。

### （历史）Wave 2bc 修复方向（已完成，留档）
> **核心教训**：2b/2c 拆分（read-switch 先于 authoring write-switch）漏耦合。read-switch 依赖
> 「每个运行时可达 worker 都有当前+完整镜像」，但镜像只在 publish/bind 建 → draft/created/cloned
> 未发布 worker 无镜像 → read-switch 退化（builtin_fallback / session 误判，HIGH×2）。测试全绿仅因
> test helper 无条件预建镜像（且漏 wp.metadata）。**必须先闭合再继续**。

修复方向（[wave-2b-review.md](./wave-2b-review.md) 末有完整 finding 表 + fix 计划）二选一：
1. **镜像完整性**（较小，过渡）：所有 authoring 写路径同步同 id 镜像（携 9 字段 + metadata）——
   `_save_worker_profile_draft`（worker_profile_ops:786，覆盖 create/update/clone/extract）+
   agent_service create（:632，改同 id 镜像非 `agent-profile-{id}` 前缀）+ resource_limits（:366）；
   GAP-A/B 加 is_worker_behavior_profile guard；migration UPDATE 补 name/model_alias/metadata；
   test helper 复制 wp.metadata + version + 加 worker-metadata 回归测试。**注意**：draft-save 同步镜像
   走 surgical 直写（仿 archive-sync，避免 _sync 的 materialize 副作用）。
   **✅ MED-1 已拍板（用户 2026-06-13）：保 baseline「草稿即时生效」**——未 publish 的编辑也立即影响运行时。
   故所有 authoring 写路径（draft save / apply-without-publish / create / clone / extract / resource_limits）
   **都必须同步镜像**，不采"仅发布态 binds"。验收：改草稿后不 publish，运行时 binding 立即反映新 tools/model_alias。
   **本 fix 用户选 fresh session 做**（不在 read-switch 提交的超长 session 末尾仓促修）。
2. **直接耦合 Wave 2c**（较大）：authoring 直写统一 agent_profiles 行（无镜像）+ 删镜像 builder + 全改名，一步到位。

**Wave 2c**（authoring 改写 + 全 wire 改名）：worker_profile_ops→agent_profile_ops / worker_service authoring 直写统一表 + agent_profile_revisions；删两镜像 builder（worker_profile_ops:130 + entity_ensure:971）；`_coordinator.py:1010` 删 `agent-profile-{id}` 前缀。**wire 字符串留 Wave 3**：action_id `worker_profile.*`→`agent_profile.*`、resource_type、路由、WorkerProfilesDocument（与 FE 原子改，详 plan §5）。
**Wave 3** FE 全改名 + wire 字符串。**Wave 4** 删类/删表 + AgentRuntime.worker_profile_id 塌缩(dedup) + works 改名 + migration CLI + 残留扫描 + completion-report。

## 关键 gotcha（resume 必读）
- **schema-lag**：托管实例 `agent_profiles` **无 kind 列**（落后 F090 ALTER），worker 镜像仅靠 `metadata.source_kind='worker_profile_mirror'`。所有 worker 检测走 dual judgment，迁移/store PRAGMA 驱动。
- **add-before-remove**：每波保 green，WorkerProfile 类/表删除留 Wave 4。
- **populate-then-switch**：Wave 1 已 populate，Wave 2 才 switch。
- **方法论**：字节级对账；禁 `ruff I001 --fix` 搬运；测试直调私有方法→mixin 继承不能改自由函数；每波 Codex+Opus 双评审 0 HIGH。
- **deferred 出 F117**：resource_limits 死列（agent+worker 两侧 store 不持久化，`update_resource_limits` 持久化丢失）——F117 落地后独立 fix（worker 侧随删消失）。migration_117 列序对齐 save_agent_profile（Wave 4）。
- **真实例迁移**：Wave 4 后用户确认 + 备份才跑 `migrate-117 --apply`，**绝不主动 push / 绝不未拍板跑真迁移**。

## resume 验证命令（禁 worktree uv sync）
```bash
WT=/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F117-profile-merge
export PYTHONPATH="$WT/octoagent/packages/core/src:$WT/octoagent/packages/memory/src:$WT/octoagent/packages/policy/src:$WT/octoagent/packages/protocol/src:$WT/octoagent/packages/provider/src:$WT/octoagent/packages/sdk/src:$WT/octoagent/packages/skills/src:$WT/octoagent/packages/tooling/src:$WT/octoagent/apps/gateway/src"
cd $WT/octoagent && SKIP_E2E=1 uv run --no-sync python -m pytest -q   # 期望 4135 passed
uv run --no-sync python -m pytest -m e2e_smoke -q                      # 期望 8 passed
```
baseline 4135 passed / 13 skipped / 1 xfailed / 1 xpassed。
