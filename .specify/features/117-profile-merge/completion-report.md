# F117 Wave 4 完成报告（W4-2b → W4-7）

> 架构债 **D2**：`WorkerProfile` → `AgentProfile` 完全物理合并。
> 本报告覆盖 Wave 4 剩余子波（W4-2b → W4-7），承接已合入 origin/master `114a1c58` 的 W0–W4a + W4-1 + W4-2a。
> 起点 baseline：4139 passed / 13 skipped / 1 xfailed / 1 xpassed + e2e_smoke 8/8。

## 一、范围与结果

| 子波 | commit | 内容 | 回归 |
|------|--------|------|------|
| **W4-2b** | `8e589650` | 删旧 incomplete builder `_build_agent_profile_from_worker_profile`；listing 文档展示喂 canonical `build_worker_agent_profile`（display 与 runtime 对齐） | 4139 = baseline 0 reg |
| **W4-3** | `fd8cc2af` + `3ab511b1` | 删 `WorkerProfile`/`WorkerProfileRevision` 类 + reverse-converter + marker 黑名单 + 死 store 方法；authoring 全切 `AgentProfile(kind=worker)`。★双评审闭环 | 4141（+2 回归测试）0 reg |
| **W4-4** | `8f18ca58` | 删 `worker_profiles`/`worker_profile_revisions` 表 DDL + 索引 + 防御 ALTER | 4141 = 0 reg |
| **W4-5** | `28e10734` | 塌缩 `AgentRuntime.worker_profile_id`（模型/列/dedup 索引 rekey/find_active_runtime/启动 dedup/readers/构造/孤儿来源修复） | collect 4156/0 + targeted 183 +（W4-7 全量复核） |
| **W4-6** | — | **推迟**（用户拍板）：`works.requested_worker_profile_id` 改名并入 W3 FE 改名 | n/a |
| **W4-7** | （本提交） | migration_117 去 works 改名对齐最终 schema + 残留扫描 + living-docs + completion-report + 实例副本 dry-run/apply 验证 | 全量 4141 + e2e 8/8 |

## 二、关键决策与偏离

1. **W4-3 marker 收口（Codex HIGH/MED 闭环）**：删 reverse-converter 后 source_* 持久化标记若泄漏出镜像会引缺陷——
   - HIGH：version 相关的 `source_worker_profile_revision` 进 snapshot → publish 幂等被破坏（每次无改动 publish 都产 spurious revision，不自愈）。
   - MED：clone 历史镜像继承 legacy `behavior_agent_slug` → 新旧 worker 共享 behavior 文件。
   - **裁定**：re-introduce 聚焦 `_CANONICAL_MIRROR_MARKER_KEYS` + `strip_mirror_markers`，**仅用于写入拷贝端 + snapshot**（非 L-1 的读路径剥离——运行时读到的镜像/工作对象保留全部 metadata，L-1 依旧结构性解决）。+2 回归测试。
2. **W4-5 孤儿来源修复**：`session_service` create-project 路径 baseline 只设 worker_profile_id 不设 agent_profile_id（孤儿来源）→ 收口为设 `agent_profile_id`。dedup/find_active/readers 全按 agent_profile_id 等价生效。
3. **W4-6 推迟（用户拍板）**：实测 `requested_worker_profile_id` 改名是 ~104 处 wire 改名（含 19 处持久化 metadata string-key，delegation envelope / event metadata），纯命名收敛、不影响 D2 合并本体、与已推迟的 W3 FE 改名（`WorkProjectionItem`）重叠 → 推迟并入 W3。本波及 W4-7 migration 均不动 works 列。
4. **migration 去 works 改名**：W4-7 删除 migration_117 step 6（works rename）+ dry-run report works 字段 + docstring，与 W4-6 推迟一致。

## 三、行为零变更口径（"运行时输出等价 + 数据语义等价 + 解析路径有意收敛"）

- W4-2b：listing behavior_system 展示 slug（name→id-based）+ bootstrap_template_ids（[]→populated）收敛到运行时实际值（旧 builder 与持久化镜像漂移）。0 测试 pin 这些值。
- W4-3：snapshot_payload.metadata 经 strip_mirror_markers 后**等价 baseline 纯净 metadata**（reverse-converter 时代即纯净）；publish 幂等守恒。
- W4-5：worker 的 agent_profile_id == 旧 worker_profile_id bare（W4-1 收口），readers/dedup/find_active 全部值守恒。

## 四、验证

- 每子波 0 regression vs 4139/4141 baseline + e2e_smoke 8/8（pre-commit hook 每次 commit 自动跑）。
- W4-3 ★双评审：Codex 1H+1M+1L + Opus 0H/0M/3L（ALIGNED），主节点 deterministic 逐条裁定全闭环，0 HIGH 残留。
- **migration_117 实例副本验证**（去 works 改名后重验，真实例 `~/.octoagent/.../octoagent.db` 副本）：
  - schema-lag 确认（agent_profiles 无 kind 列 → `worker_detection: metadata.source_kind`）。
  - dry-run：1 worker merge / 1 revision rekey / **1 orphan backfill** / column collapse / **无 works 改名**。
  - apply：succeeded（worker_profiles + worker_profile_revisions DROP / kind 列 ALTER+回填 / worker_profile_id 列 DROP / 孤儿 backfill 后 0 残留 / works 列保留 / 1 audit 行）。
  - apply again：idempotent（skipped）。
- **W4-5+W4-7 ★双评审闭环**（Codex 对抗 + Opus spec-对齐，审 `8f18ca58..HEAD`）：
  - **Codex 2 HIGH / 1 MED**（全部"已部署 W4-5 代码但 migration 未跑"的过渡窗口）：
    - HIGH：存量孤儿 worker runtime（agent_profile_id='' + worker_profile_id 非空）对新
      find_active/dedup 不可见 → 窗口内可能产生重复 active runtime + 使 migration 建唯一索引撞重失败；
      `_merge_composite_runtimes` 对 legacy composite 行 loose (project,role) fallback。
    - MED：窗口内孤儿 runtime 的 owner 只在 worker_profile_id，readers 返回空 owner。
    - **裁定·修复**：① 启动桥接 `_backfill_worker_runtime_agent_profile_id`（init_db 在 dedup/merge
      **前**，列存在时把 worker 行 agent_profile_id 从 worker_profile_id 回填）→ 一条 UPDATE 闭合孤儿/
      composite/reader 三处；migration DROP 列后 / fresh DB 自动 no-op。② migration 建唯一索引前加
      dedup（自足性兜底）。**实例副本验证**：真孤儿 backfill 1→0；migration apply+幂等+post-state 正确。
  - **Opus 0 HIGH / 1 MED / 2 LOW**（verdict ALIGNED，独立复跑 4141 passed）：
    - MED：第 6 处漏改 `AgentRuntime(worker_profile_id=)`（test_task_service:3098，extra=ignore 静默丢弃→孤儿测试数据）→ **改 agent_profile_id**（completion-report 原"5 处"更正为 6 处）。
    - LOW：sqlite_init:1340 docstring 残留 worker_profile_id → 修；dual-review "待补" 流程项 → 本节即闭合。
  - **panel 价值**：Codex（对抗）证明 W4 原"过渡期 code-level dedup 兜底"对孤儿**不成立**（孤儿
    agent_profile_id='' 对 dedup 不可见）→ 启动 backfill 让该兜底真正成立；Opus（spec-对齐）抓到
    Codex 漏的第 6 处静默丢弃构造。**0 HIGH 残留**。

## 五、残留扫描（§6）

全仓已删符号 **0**：`class WorkerProfile`/`WorkerProfileRevision`、`WorkerProfile(`/`WorkerProfileRevision(` 构造、
`_build_agent_profile_from_worker_profile`/`_ensure_agent_profile_from_worker_profile`/`build_worker_dto_from_agent_profile`/
`_MIRROR_MARKER_METADATA_KEYS`、删的 store 方法、`AgentRuntime.worker_profile_id` 字段读。

**豁免（有意保留）**：
- `source_kind`/`source_worker_profile_id` 标记 — schema-lag 检测 + migration 锚；**真实例迁移补 kind 列、全量 rollout 后**才能删（独立后续清理）。
- `works.requested_worker_profile_id` + 全部 `requested_worker_profile_id` — W4-6 推迟并入 W3。
- `migration_117` 内对 worker_profiles/worker_profile_id 的引用 — 迁移本就操作它们。
- `AgentRuntimeItem.worker_profile_id`（FE DTO）+ `build_agent_runtime_id`/`build_scope_aware_session_id` 的 worker_profile_id 参数 + `_ensure_a2a_agent_runtime` 的 worker_profile_id 参数 — id-builder / DTO / 显式 worker 解析，W3/命名收尾。
- `_save_worker_profile_draft`/`_publish_worker_profile_revision`（authoring 方法名）、`WorkerProfileOpsMixin`/`WorkerProfileDomainService`（类名）、`AgentRuntimeRole.WORKER` — 与被删的 WorkerProfile **模型类**无关。

## 六、已知 limitations / 后续

1. **W4-6（works 改名）→ W3**：与 FE 改名一起做（含 19 处持久化 metadata key 的兼容/迁移）。
2. **source_* 标记移除** → 真实例 migration_117 apply（补 kind 列）全量 rollout 后的独立清理。
3. **snapshot 升级期一次性额外 revision**：实例从 baseline 升级后首次 publish——已由 W4-3 strip_mirror_markers 修复（snapshot 现纯净 = baseline），不再发生。
4. **resource_limits 死列**（两表均不持久化）— F117 范围外既有债，worker 侧随删消失；agent 侧独立 fix。
5. **living-docs 描述性 drift（未改写）**：`docs/design/capability-pack-simplification.md`、`docs/milestone/m3-feature-split.md`、`docs/blueprint/agent-collaboration-philosophy.md`、`docs/blueprint/milestones.md` 含 WorkerProfile 历史性引用（里程碑/设计快照），属当时事实，不做修正式改写。已更新：`architecture-audit.md`（D2 闭合）、`message-model.md`（字段描述）。
6. **migration 无 synthetic pytest 测试**：一次性不可逆迁移，靠真实例副本 dry-run+apply+幂等验证（比 synthetic 构造旧 schema 更faithful，避免 schema-mismatch 假信心）；若需 CI 回归保护可后续补。

## 七、硬门禁（未做，待用户）

1. **真实例 `migrate-117 --apply`**：不可逆（DROP 2 表 + DROP worker_profile_id 列）。必须**用户确认 + 备份**（VACUUM INTO / F022 backup）后才跑——**绝不自行跑**。存量极小（1 worker_profile / 1 revision / 1 孤儿 runtime）。
2. **ff push origin/master**：全波 + 双评审闭环 + 全量回归后，贴归总等用户扫一眼再推。
