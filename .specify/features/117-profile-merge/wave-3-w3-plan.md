# F117 Wave 3 / W4-6 — profile 改名收尾计划

> Spec-Driver Refactor 模式 — 承接 F117 D2 核心合并（已合入 origin/master `cd9a56c3`）的**推迟改名收尾**。
> 上游：[handoff.md](./handoff.md) §下一步 W3 + [completion-report.md](./completion-report.md) §五（豁免清单）+ §六.1（W4-6→W3）。
> Baseline：`cd9a56c3`。worktree `.claude/worktrees/F117-w3`，分支 `feature/117-w3-profile-rename`。

---

## 1. 范围（用户拍板：全量收敛改名）

D2 物理合并后残留的 worker-命名 wire/列/DTO 收敛到 agent 命名。**不含** W3 「面合并」独立 UI Feature（action_id `worker_profile.*`→`agent_profile.*` / `WorkerProfilesDocument`→`AgentProfilesDocument` / 路由），那是单独立项。

### 目标命名

| 旧名 | 新名 |
|------|------|
| `requested_worker_profile_id` | `requested_agent_profile_id` |
| `requested_worker_profile_version` | `requested_agent_profile_version` |
| `effective_worker_snapshot_id` | `effective_profile_snapshot_id`（refactor-plan §2.4 建议）|
| `AgentRuntimeItem.worker_profile_id`（FE DTO 冗余字段）| `agent_profile_id`（合并入已存在的同名字段）|
| `build_agent_runtime_id` / `build_scope_aware_session_id` / `_ensure_a2a_agent_runtime` 的 `worker_profile_id` 形参 | `agent_profile_id` |
| 局部变量 `requested_worker_profile_id` / `worker_profile_id` | 同步改名 |

---

## 2. 三层持久化耦合 + 兼容策略（用户确认：全改 + 双轨兼容）

`requested_worker_profile_*` 家族横跨三层强耦合（模型字段值直接赋给事件 JSON key）：

| 层 | 可变性 | 兼容机制 |
|----|--------|----------|
| **A. `works` DB 列** | 可变状态，可迁移 | `sqlite_init` 幂等防御 `RENAME COLUMN`（开机自动、非用户门禁、符合既有 schema-lag 范式 :1109）+ `CREATE TABLE` 新名 + 索引新名。**不进 migration_117**（保持 W4-7 决策；列改名是数据保全的良性 op，不应绑死不可逆迁移门禁）。 |
| **B. 事件 metadata JSON key** | append-only event_store，**不可改写**（违 Constitution #2）| **`normalize_control_metadata` 单一收敛点 alias old→new**：所有 event-replay 的 control_metadata 经 `merge_control_metadata`→`control_metadata_from_payload`→`normalize_control_metadata`，老事件的老 key 在此 alias 成新 canonical key。白名单收新 key。**保老 event replay 零行为变更。** |
| **C. 内存模型 / DTO / 局部变量** | 纯 Python，无持久化 | 直接改名。 |

### 兼容点收敛（最少改动面）

1. **`normalize_control_metadata`（connection_metadata.py）**：加 alias map `{老 key: 新 canonical key}`（3 项）。源 key 命中老 alias → 输出新名。version int-coercion 分支匹配新名。
2. **白名单**：`TURN_SCOPED_CONTROL_KEYS` + `PROMPT_SAFE_CONTROL_KEYS` 用新名（alias map 保老 key 被接纳归一）。
3. **`resolve_delegation_target_profile_id`（主语义读取点，被 ~15 处调用）**：防御性双读 `metadata.get(新) or metadata.get(老)`——兜底任何绕过 normalize 的 raw-metadata 路径。
4. **`orchestrator._canonical_requested_worker_type` :974**：`singleton:` lane 解析循环容忍新+老 key（compat 期）。
5. **`session_projection_helpers` :127**：`latest_metadata`（经 normalize→新名）+ `latest_work.requested_agent_profile_id`（模型字段改名）双侧收敛新名。
6. **所有 write 站点**：仅 emit 新 key。
7. **dispatch 路径 raw 读（dispatch_service:136 / delegation_plane:164）**：读 **transient 同版本** envelope/request（非老持久化 artifact），直接改新名，无需双读。

> **chokepoint 验证**：grep 确认生产无直接 `event.payload["control_metadata"][key]` 读（仅测试断言）；唯一 event-sourced 读路径是 `normalize_control_metadata`。

### 未来清理（出本任务）

normalize alias map 项 + `resolve_delegation_target_profile_id` 老 key fallback + orchestrator 老 key 容忍 = 过渡 compat shim。待真实例升级 + 老 in-flight task 全 drain 后独立清理（沿用 F090 双轨→F100 塌缩范式）。

---

## 3. 分批 commit 计划（每 commit 全量 0 regression）

> 改名本质原子（不能半改一个字段）；commit 按"可独立 green"切分。

- **C1 — 后端持久化层 + 事件兼容（原子）**：core 模型（`work.py` / `delegation.py`）+ `work_store.py` + `sqlite_init.py`（DDL + 索引 + 防御 RENAME）+ migration_117 注释更新 + connection_metadata 兼容 + gateway services 全 write/read 站点 + `AgentRuntimeItem` + session_service + id-builder 形参 + 后端 tests。必须全量 green。
- **C2 — 前端 TS 同步**：`types/index.ts` + `AgentCenter.tsx` + 3 FE 测试。独立于 Python green。
- **C3 — 残留扫描 + living-docs + completion-report**：§4 扫描 + 文档同步。

> id-builder 形参（纯改名、无持久化）可并入 C1 或独立小 commit；视实施清晰度定。

---

## 4. 残留扫描清单（Phase 4）

全仓 grep 零残留（排除 git 历史 + 本 spec 描述 + migration_117 内对老列名的迁移性引用若有）：
`requested_worker_profile_id` / `requested_worker_profile_version` / `effective_worker_snapshot_id` / `AgentRuntimeItem.*worker_profile_id`（FE DTO 字段）。

**豁免（有意保留）**：
- `normalize_control_metadata` alias map 内的老 key 字面 + `resolve_delegation_target_profile_id` / orchestrator:974 的老 key fallback —— compat shim，注释标注。
- `source_worker_profile_id`（A2A source marker，与本家族无关，F117 schema-lag 锚，独立后续）。
- `worker_profile_id` 局部变量若语义确为"worker 的 profile"且无歧义可保留——但优先改 `agent_profile_id`。
- `WorkerProfileStatus`/`WorkerProfileOriginKind`/`WorkerProfileOpsMixin`/`AgentRuntimeRole.WORKER` —— 与本家族无关符号。

---

## 5. 验证策略（行为零变更）

- **baseline**（PYTHONPATH 锁本 worktree src，禁 worktree uv sync）：
  ```bash
  WT=/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F117-w3
  export PYTHONPATH="$WT/octoagent/packages/core/src:$WT/octoagent/packages/memory/src:$WT/octoagent/packages/policy/src:$WT/octoagent/packages/protocol/src:$WT/octoagent/packages/provider/src:$WT/octoagent/packages/sdk/src:$WT/octoagent/packages/skills/src:$WT/octoagent/packages/tooling/src:$WT/octoagent/apps/gateway/src"
  cd $WT/octoagent && SKIP_E2E=1 uv run --no-sync python -m pytest -q
  uv run --no-sync python -m pytest -m e2e_smoke -q   # 8 passed
  ```
- 每 commit 0 regression vs `cd9a56c3` baseline；e2e_smoke 必过。
- **事件 replay 兼容专项**：补/复用单测断言——老 key 的持久化 control_metadata 经 merge→normalize 后仍能被 resolver 解出（dual-read / alias 生效）。
- FE：`npm test`（vitest）绿。

## 6. 评审结构

- **触持久化 metadata → 强制 Codex + Opus 双评审 panel**（命中"持久化契约/DB schema"重大节点），分歧人裁，0 HIGH 残留。重点审：事件 replay 兼容是否真零行为变更 + 防御 RENAME 幂等性 + 是否有绕过 normalize 的 event-sourced 读路径漏改。
- completion-report + living-docs 漂移闸（`message-model.md` 字段描述）。
- **不主动 push**；归总报告等用户拍板。
