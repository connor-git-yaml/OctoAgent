# F117 重构计划（refactor-plan.md）

> Spec-Driver Refactor 模式 — Phase 2/5 分批规划
> 上游：[impact-report.md](./impact-report.md)
> **用户已拍板**：决策 A = **A1 彻底物理合并**；决策 B = **本次全改名**（wire key + 持久化列 + TS 类型全改）
> Baseline：`7199f468`

---

## 1. 决策固化

- **A1**：删 `WorkerProfile` / `WorkerProfileRevision` 类 + `worker_profiles` / `worker_profile_revisions` 表；统一进 `agent_profiles`（kind 判别）+ 新 `agent_profile_revisions`；镜像塌缩为单一权威行（运行时直读 kind=worker 行的工具字段）。
- **全改名**：`worker_profiles`→`agent_profiles`（资源 key）、`worker_profile.*`→`agent_profile.*`（action_id）、`worker_profile_id`→`agent_profile_id`（持久化列 + wire + TS）。`requested_worker_profile_id`→`requested_agent_profile_id`。
- **"零变更"重定义**：运行时**输出**等价 + 数据语义等价；解析路径**有意收敛**（镜像 materialize-on-read 塌缩、dedup key 归一）——非字节级路径不变。验证以行为对照 + 0 regression 为准（§7）。

---

## 2. 目标统一 schema

### 2.1 `agent_profiles`（吸收 9 个 worker 列）
现有 18 列（含 ALTER 加的 kind + resource_limits）保留，**新增 9 列**（来自 worker_profiles）：

```sql
-- F117 新增列（ALTER TABLE agent_profiles ADD COLUMN，default 保证既有 main/subagent 行零影响）
summary              TEXT NOT NULL DEFAULT '',
default_tool_groups  TEXT NOT NULL DEFAULT '[]',
selected_tools       TEXT NOT NULL DEFAULT '[]',
runtime_kinds        TEXT NOT NULL DEFAULT '[]',
status               TEXT NOT NULL DEFAULT 'active',   -- ⚠ main/subagent 语义恒 active；worker 行迁移时写真实 status
origin_kind          TEXT NOT NULL DEFAULT 'custom',
draft_revision       INTEGER NOT NULL DEFAULT 0,
active_revision      INTEGER NOT NULL DEFAULT 0,
archived_at          TEXT
```

**字段归并规则（kind=worker 行）**：
- `persona_summary`：保留两个独立列 `persona_summary` + `summary`（**不合并**——summary 是 worker 自述/编辑展示，persona_summary 是运行时人格；镜像曾 `summary→persona_summary` 是有损投影，A1 下两者各自保真，byte-safe）。
- `default_tool_groups/selected_tools/runtime_kinds`：从 worker_profiles 原样迁入（运行时权威）。
- `status/origin_kind/draft_revision/active_revision/archived_at`：从 worker_profiles 原样迁入。
- `version`（agent-only int）：worker 行迁移后 `version = max(active_revision, draft_revision, 1)`（与镜像旧行为一致）。
- **dead resource_limits 修复**：合并后 store 层补齐 `resource_limits` 的写入/hydrate（impact §6 隐患 1）——属顺手清，纳入 Wave 0。

### 2.2 `agent_profile_revisions`（rename 自 worker_profile_revisions，R-A）
```sql
CREATE TABLE agent_profile_revisions (
    revision_id      TEXT PRIMARY KEY,
    profile_id       TEXT NOT NULL,
    revision         INTEGER NOT NULL,
    change_summary   TEXT NOT NULL DEFAULT '',
    snapshot_payload TEXT NOT NULL DEFAULT '{}',
    created_by       TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    FOREIGN KEY (profile_id) REFERENCES agent_profiles(profile_id),
    UNIQUE(profile_id, revision)
);
```

### 2.3 `agent_runtimes`：塌缩 worker_profile_id
- **删 `worker_profile_id` 列**（A1 下 worker 的 profile 即 agent_profile）。
- dedup 唯一索引 `idx_agent_runtimes_active_worker_unique`（sqlite_init.py:925）改 key 到 `agent_profile_id`：
```sql
CREATE UNIQUE INDEX idx_agent_runtimes_active_worker_unique
  ON agent_runtimes(project_id, agent_profile_id)
  WHERE status='active' AND role='worker'
  AND agent_profile_id != '' AND agent_runtime_id NOT LIKE 'subagent-%';
```
  （与现有 main 索引 :931 仅 role 不同——干净）
- startup dedup 逻辑（sqlite_init.py:1426-1602）：WORKER 分支从 `worker_profile_id` 改读 `agent_profile_id`（Wave 1）。
- `AgentRuntime` 模型删 `worker_profile_id` 字段；`_row_to_agent_runtime` / `save_agent_runtime` 同步。

### 2.4 `works`：rename requested_worker_profile_id
- `requested_worker_profile_id`→`requested_agent_profile_id`；`requested_worker_profile_version`→`requested_agent_profile_version`；索引 :891 同步。
- `effective_worker_snapshot_id`（FE WorkProjectionItem :1208）：评估是否一并改名（属 revision snapshot 引用，建议改 `effective_profile_snapshot_id`，Wave 2 定）。

---

## 3. id 约定 reconciliation（合并唯一真实"冲突"）

两套 id 约定（impact §2.3）：
- **同 id**（worker_profile_ops / entity_ensure）：mirror agent_profile.profile_id == worker.profile_id。
- **`agent-profile-{id}` 前缀**（agent_service / _coordinator，`_coordinator.py:1010` 反向 replace）。

**canonical 规则**：统一行 profile_id = **worker 的 profile_id**（稳定编辑 id）。
- 同 id 路径：mirror 行与 worker 行**塌缩为一行**（并字段集），无 id 碰撞。
- `agent-profile-{id}` 路径：把该 mirror agent_profile 的字段并入 `{id}` 行，并**重写所有引用** `agent-profile-{id}`→`{id}`（`projects.default_agent_profile_id`、`agent_runtimes.agent_profile_id`、works 等）。删除 `agent-profile-{id}` 残行。
- 删 `_coordinator.py` 的 `agent-profile-` 前缀生成 + reverse replace 逻辑（Wave 1）。

**实例现状**：仅同 id 的 default octo（worker+mirror 同 `worker-profile-project-default-octo`）→ 实例迁移为 1 行塌缩、0 个 `agent-profile-{id}` 重写。`agent-profile-project-default` 是独立 main（kind=main），不参与。

---

## 4. 迁移策略（migration_117，clone F094 范式）

- 位置：`packages/core/src/octoagent/core/store/migrations/migration_117_profile_merge.py`（core 表 → 放 core 包 migrations；若无该目录则新建，参照 memory migrations 布局）。
- CLI：`octo ... migrate-117 --dry-run|--apply|--rollback`（在 dx/cli.py 注册；core 表迁移挂新 `agent` group 或复用既有 group——Wave 4 定）。
- 三入口返回 JSON report dict（F094 形状）：
  - `run_dry_run`：**只读**。报 `worker_profiles_to_merge` / `revisions_to_rekey` / `mirror_rows_to_collapse` / `agent_profile_prefix_rows_to_rewrite` / `runtimes_to_collapse` / `works_to_rename` / `conflicts[]` / `irreversible_points[]` / `idempotency_key` / `already_applied_run_id`。
  - `run_apply`：幂等短路（idempotency_key 已存在→skipped）→ **单事务** schema 变更 + 数据迁移 + DROP 旧表 → 写 audit 行。
  - `run_rollback`：按 run_id 删 audit 行（**注意**：DROP 表不可逆，rollback 仅释放幂等键，不还原表——dry-run/apply report 显式标注 `irreversible: worker_profiles/worker_profile_revisions DROPPED`）。
- **不可逆点**（report 必列）：DROP `worker_profiles` + DROP `worker_profile_revisions` + DROP `agent_runtimes.worker_profile_id` 列（表 rebuild）。建议 apply 前自动 `VACUUM INTO` 备份（F022 backup 范式）或提示用户先备份。
- **SQLite 机制**：3.51+ 支持 DROP/RENAME COLUMN，但 `agent_runtimes`（删列+改索引）、`works`（改列名+索引）用**表 rebuild**（CREATE new→INSERT SELECT→DROP old→RENAME）更稳，单事务包裹，数据量极小（成本 nil）。
- **apply 顺序**（单事务）：① ALTER agent_profiles ADD 9 列 → ② 迁移 worker_profiles 行（同 id 塌缩 / 前缀 reconcile）→ ③ CREATE agent_profile_revisions + 复制 → ④ rebuild agent_runtimes（删 worker_profile_id，agent_profile_id reconcile）→ ⑤ rebuild works（rename 列）→ ⑥ rewrite `agent-profile-{id}` 引用 → ⑦ DROP worker_profiles + worker_profile_revisions + 旧索引 → ⑧ 重建索引。

---

## 5. 分批实现计划（waves）

> 方法论（F108/F113 验证）：字节级 + 方法级对账；禁 `ruff I001 --fix` 搬运；helpers 抽叶子破环；测试直调私有方法→mixin 继承不能改自由函数。每 wave Codex + Opus 双评审，分歧人裁，0 HIGH 残留。

> **决策（Wave 1 实施期细化）：add-before-remove + read/write 耦合切换**。删 WorkerProfile 是"移除老"，
> 必须在"新就位"之后——Wave 0/1 加性建好统一行，Wave 2 耦合切读+写，Wave 4 才物理删。
> read-switch 与 write-switch（authoring）必须**同波**：authoring 改写统一表后 worker_profiles 不再被写，
> 若读路径仍读 worker_profiles 会丢新建 worker → 两者分波切换会开镜像-lag 窗口，违反零变更。

### Wave 0 — AgentProfile 加性吸收 9 字段（✅ 完成，commit 23082fad）
- `agent_context.py`：`AgentProfile` +9 worker 字段（default 兼容 main/subagent）。**WorkerProfile 类本波不动**。
- `agent_context_store.py`：`save_agent_profile` / `_row_to_agent_profile` 持久化 + 防御性 hydrate 9 字段（缺列回退默认，处理实例 schema-lag）。
- `sqlite_init.py`：agent_profiles DDL +9 列 + 幂等 ALTER（存量库补列）。
- **撤回**：resource_limits（F117 范围外既有死列）不 fold-in（评审 MEDIUM-1）。
- 回归 4135 = baseline + e2e_smoke 8/8。

### Wave 1 — 镜像 populate（✅ 本波）
> 不变量 populate-then-switch（评审 MEDIUM-2）：先让镜像行携全 9 字段，**再**（Wave 2）切读路径。本波仅 populate，加性。
- `_build_agent_profile_from_worker_profile`（worker_profile_ops.py:130）+ `_ensure_agent_profile_from_worker_profile`（entity_ensure.py:971）：复制 9 字段进统一行（summary/default_tool_groups/selected_tools/runtime_kinds/status/origin_kind/draft_revision/active_revision/archived_at）。无运行时消费方读这些字段 → 零变更。
- backfill：materialize-on-read 每次 dispatch 重写镜像，自然回填；存量行由 migration_117 apply 统一。

### Wave 2 — 耦合读+写切换 + 镜像塌缩 + authoring 改写 + 命名（最高风险）
> **强制 gate（Wave 1 评审 MEDIUM-1）：archive→统一行 sync**。baseline archive 路径
> （worker_service.py:848）只更 worker_profiles 不刷镜像 → 镜像 status 陈旧（恒 active）。
> Wave 2 切读路径读 mirror status/archived_at **前**，必须让 archive 直写统一 agent_profiles
> 行 status=ARCHIVED（authoring 改写自然闭合：统一行即权威）。验收：archived worker 端到端断言 status 正确。
- **(read switch)** `capability_pack._resolve_worker_binding`（:410）读 agent_profiles(kind=worker) 工具字段（用 `is_worker_behavior_profile` dual judgment 兼容 schema-lag）；`chat.py`（:256/:271）model_alias + executor-kind 读统一行。
- **(write switch + authoring)** `worker_profile_ops.py`→`agent_profile_ops.py` / `worker_service.py` authoring 走统一 agent_profiles + 新 `agent_profile_revisions`（store 加 `save/list_agent_profile_revisions`）；revision publish 切换。
- **(mirror 塌缩)** 删两个镜像 builder（authoring 直写统一表后冗余）；镜像消费方（agent_decision:123 / resolver:683 / paths:40）判据收敛；`_coordinator.py` 删 `agent-profile-{id}` 前缀 + reverse replace；stale-mirror 写路径（worker_service archive 等）直写统一行。
- **(命名)** action_id `worker_profile.*`→`agent_profile.*`；视图族 `WorkerProfileViewItem`/`WorkerProfilesDocument`/`WorkerProfileRevisionItem` → `AgentProfile*`（resource_type/id 同步）；枚举 `WorkerProfileStatus`→`AgentProfileStatus` / `WorkerProfileOriginKind`→`AgentProfileOriginKind`；`routes/control_plane.py` `worker-profiles`/`worker-profile-revisions` → `agent-profiles`/`agent-profile-revisions`。
- 此后 worker_profiles 表运行时死（不读不写），待 Wave 4 物理删。

### Wave 3 — FE 全改名
- `types/index.ts`：`WorkerProfileItem`→`AgentProfileItem`（与既有 AgentProfileItem 协调，避免重名冲突——可能合并为 kind 判别的单类型）等 8 类型；`AgentRuntimeItem.worker_profile_id`→`agent_profile_id`；`WorkProjectionItem.requested_worker_profile_id`→`requested_agent_profile_id`。
- `api/client.ts`：`fetchWorkerProfileRevisions`→`fetchAgentProfileRevisions`，端点 `worker-profile-revisions`→`agent-profile-revisions`。
- `controlPlane.ts`：资源 manifest key `worker_profiles`→`agent_profiles`，路由 map 同步。
- 组件：`agentManagementData.ts` / `AgentCenter.tsx` / `ChatWorkbench.tsx` / `WorkbenchLayout.tsx` / `SettingsResourceLimitsSection.tsx` / `SettingsPage.tsx`：`snapshot.resources.worker_profiles`→`agent_profiles`，action_id `worker_profile.*`→`agent_profile.*`，`result.data.worker_profile_id`→`agent_profile_id`。
- FE 7 测试 fixture 同步。

### Wave 4 — 物理删除 + dedup 塌缩 + 真迁移 + 测试 bulk + docs
- **删类/表/store 方法**：`WorkerProfile` / `WorkerProfileRevision` 类（agent_context.py）；`worker_profiles` / `worker_profile_revisions` 表 DDL（sqlite_init.py）；store `save/get/list_worker_profile*` + `_row_to_worker_profile*` 方法；`models/__init__` re-export。
- **dedup 塌缩**：`AgentRuntime.worker_profile_id` 字段 + `agent_runtimes.worker_profile_id` 列 + dedup 唯一索引（:925）改 key `agent_profile_id`；`find_active_runtime`（:485-492）去 role 分支；startup dedup（:1426-1602）改 agent_profile_id；`_row_to_agent_runtime`/`save_agent_runtime` 去 worker_profile_id。
- **works 改名**：`requested_worker_profile_id`→`requested_agent_profile_id`（+ version + 索引）。
- `migration_117` CLI 注册 + 测试（test_migration_117 dry-run/apply/rollback/idempotency，clone test_migration_094）；列序对齐 save_agent_profile（评审跨文件 note）。
- 后端 7 测试文件 worker_profile→agent_profile 符号/fixture 更新。
- 残留扫描 0（§6）+ living-docs 漂移闸：`docs/codebase-architecture/module-design.md` + 数据模型文档 + `docs/blueprint/` 章节同步（D2 关闭标记）。
- completion-report.md + handoff.md。
- **真实例迁移**：用户确认 + 备份后跑 `migrate-117 --apply`（单独门禁）。

---

## 6. 残留扫描清单（Phase 4）
合并完成后全仓 grep 零残留（排除 git 历史 + 本 spec 文档描述性引用 + 2 假阳性符号 WorkerProfileDeniedError/WorkerProfileDomainService）：
- `WorkerProfile`（类）/ `WorkerProfileRevision` / `worker_profiles`（表/key）/ `worker_profile_revisions` / `worker_profile_id`（列/字段）/ `worker_profile.`（action_id）/ `source_worker_profile_id` / `worker_profile_mirror` / `_build_agent_profile_from_worker_profile` / `_ensure_agent_profile_from_worker_profile`。
- 保留项（豁免，需注明）：`WorkerProfileStatus`/`WorkerProfileOriginKind` 枚举（仍用）、`WorkerProfileDeniedError`/`WorkerProfileDomainService`（无关符号）、`worker`（运行时角色 AgentRuntimeRole.WORKER 等，与 profile 无关）。

---

## 7. 行为零变更验证策略
- **baseline 锁定**（PYTHONPATH 锁本 worktree src，禁 uv sync）：
  ```
  export WT=/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F117-profile-merge
  export PYTHONPATH="$WT/octoagent/packages/core/src:$WT/octoagent/packages/memory/src:$WT/octoagent/packages/policy/src:$WT/octoagent/packages/protocol/src:$WT/octoagent/packages/provider/src:$WT/octoagent/packages/sdk/src:$WT/octoagent/packages/skills/src:$WT/octoagent/packages/tooling/src:$WT/octoagent/apps/gateway/src"
  cd $WT/octoagent && uv run --no-sync python -m pytest
  ```
- 每 wave 后回归 0 regression vs `7199f468` baseline（计数对照）；e2e_smoke 必过。
- 迁移：dry-run 在实例 DB **副本**跑（只读）；真迁移用户拍板后单独跑 + 前置备份。
- 行为对照：worker dispatch（工具集解析 / model_alias / executor-kind 路由 / dedup）前后输出等价——以 e2e_live worker 域 + 单测断言为锚。

## 8. 评审/闸结构
- **第二道闸（本 Phase 末）**：迁移计划 + dry-run 结果回主 session 等用户拍板（不可逆迁移）。✅ 当前到此。
- 每 wave commit 前：Codex adversarial review + Opus 第二评审 panel（命中"删除≥500 行 / 跨包接口 / DB schema / LLM 工具"重大架构节点），分歧人裁，0 HIGH 残留。
- Final cross-wave review（最后一 wave 前）。
