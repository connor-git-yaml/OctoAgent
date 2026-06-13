# F117 Handoff — Wave 2bc resume 指南

> 状态：**Wave 0/1/2a 完成 committed；Wave 2b read-switch 已 committed 但⚠半成品（BLOCKED）**。
> 分支 `feature/117-profile-merge`（**未 push**），worktree `.claude/worktrees/F117-profile-merge`，基于 origin/master `7199f468`。

## 已完成（9 commits）
```
67201a40 refactor(F117): Wave 2b read-switch ⚠半成品 — BLOCKED on 镜像完整性（双评审）
450299b3 refactor(F117): Wave 2a — agent_profile_revisions store 地基 + 枚举/视图类改名
689732d5 docs(F117): Wave 2 详尽变更地图（7-agent workflow + critic）+ plan §5 子波细化
ef1d7ba0 docs(F117): Wave 2 resume handoff（Wave 0+1 检查点）
0746e6f0 / 240dde98 / 23082fad / 8e8d8a4b / 4a164fc9 = Wave 0/1 + migration + 影响分析
```
- **Phase 1-2 + 双 Gate**：A1 彻底物理合并 + 全改名（拍板）。migration_117 副本验证（幂等 + 零数据丢失），真实例未动。
- **Wave 0/1**（加性）：AgentProfile +9 字段 + 镜像 populate。**Wave 2a**：agent_profile_revisions store 地基 + 枚举/视图类改名。均 0 regression（4135）。
- **Wave 2b read-switch（⚠ 半成品）**：7 站运行时读切到统一镜像 + archive-sync。4135=baseline + e2e 8/8，但**双评审 panel 抓出系统性不变量缺口**（详 [wave-2b-review.md](./wave-2b-review.md)）。

## ⚠ 下一步：Wave 2bc 必须闭合镜像完整性（read-switch 当前不安全）
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
   走 surgical 直写（仿 archive-sync，避免 _sync 的 materialize 副作用）；MED-1「published-only binds
   vs draft-immediately」需 intent 拍板。
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
