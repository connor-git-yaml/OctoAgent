# F117 Handoff — Wave 2 resume 指南

> 状态：**Wave 0+1 完成并 committed，暂停在干净检查点**（用户 2026-06-13 拍板）。Wave 2-4 待续。
> 分支 `feature/117-profile-merge`（**未 push**），worktree `.claude/worktrees/F117-profile-merge`，基于 origin/master `7199f468`。

## 已完成（5 commits）
```
0746e6f0 docs(F117): Wave 1 评审 + plan §5 分波对齐
240dde98 refactor(F117): Wave 1 镜像 populate
23082fad refactor(F117): Wave 0 AgentProfile 吸收 9 worker 字段
8e8d8a4b feat(F117): migration_117（dry-run/apply 副本验证）
4a164fc9 docs(F117): 影响分析 + 迁移设计 + Wave 0 评审
```
- **Phase 1-2 + 双 Gate**：A1 彻底物理合并 + 本次全改名（用户拍板）。migration_117 在真实例两副本验证（幂等 + 零数据丢失），**真实例未动**。
- **Wave 0**（加性）：`AgentProfile` +9 worker 字段 + store save/hydrate + sqlite_init DDL/ALTER。
- **Wave 1**（populate）：两镜像 builder 复制 9 字段进统一行。
- 两波均 **0 regression（4135=baseline）+ e2e_smoke 8/8 + 评审 0 HIGH**。

## 下一步：Wave 2（最高风险，耦合切换）
详见 [refactor-plan.md](./refactor-plan.md) §5 Wave 2。核心：
1. **archive-sync gate（先做，Wave 1 评审 MEDIUM-1）**：baseline `worker_service.py:848 _handle_worker_profile_archive` 只更 worker_profiles 不刷镜像 → 镜像 status 陈旧。Wave 2 读 mirror status **前**必须让 archive 写统一行 status=ARCHIVED（authoring 改写自然闭合）。验收：archived worker 端到端断言 status。
2. **read switch**：`capability_pack._resolve_worker_binding`（:410）+ `chat.py`（:256/:271 _resolve_profile_model_alias / _resolve_owner_turn_executor_kind）改读 agent_profiles(kind=worker)。worker 检测用 `is_worker_behavior_profile`（agent_decision.py:112，dual judgment kind OR metadata，**兼容实例无 kind 列**）。
3. **write switch + authoring**：`worker_profile_ops.py`(43 占)→`agent_profile_ops.py` / `worker_service.py`(51 占) authoring 直写统一表 + 新 `agent_profile_revisions`（store 加 `save/list_agent_profile_revisions`）。
4. **mirror 塌缩**：删两 builder（`worker_profile_ops.py:130` + `entity_ensure.py:971`）；消费方判据收敛；`_coordinator.py:1010` 删 `agent-profile-{id}` 前缀+reverse replace。
5. **命名**：action_id `worker_profile.*`→`agent_profile.*`（action_registry.py:329-385）；视图族 6 类（control_plane/agent.py:39-116）→`AgentProfile*`；枚举 `WorkerProfileStatus`→`AgentProfileStatus`/`WorkerProfileOriginKind`→`AgentProfileOriginKind`；路由（routes/control_plane.py:44/51）`worker-profiles`→`agent-profiles`。

**Wave 3** FE 全改名（9 src + 7 test，types/index.ts 等）。**Wave 4** 删类/删表 + AgentRuntime.worker_profile_id 塌缩(dedup) + works 改名 + migration_117 CLI 注册 + 残留扫描 0 + docs + completion-report。

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
