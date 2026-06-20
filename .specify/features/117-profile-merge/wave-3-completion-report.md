# F117 Wave 3 / W4-6 完成报告 — profile 改名收尾

> 架构债 **D2** 收尾：`requested_worker_profile_*` 家族 + `effective_worker_snapshot_id` + 残留 worker-命名 wire/DTO/形参收敛到 agent 命名。
> 承接 F117 D2 核心合并（origin/master `cd9a56c3`）。**行为零变更**。
> 起点 baseline：4141 passed / 13 skipped / 1 xfailed / 1 xpassed + e2e_smoke 8/8。

## 一、范围与决策

用户拍板 **全量收敛改名**（非最小范围）。目标命名：

| 旧名 | 新名 |
|------|------|
| `requested_worker_profile_id` | `requested_agent_profile_id` |
| `requested_worker_profile_version` | `requested_agent_profile_version` |
| `effective_worker_snapshot_id` | `effective_profile_snapshot_id` |
| `AgentRuntimeItem.worker_profile_id`（冗余 FE DTO 字段）| **删除**（合并入已存在的 `agent_profile_id`）|
| `build_agent_runtime_id` 的 `worker_profile_id` 形参 | 合并入 `agent_profile_id`（保留 `worker_profile:` ID-scheme 前缀字面）|
| `_ensure_a2a_agent_runtime` 的 `worker_profile_id` 形参 | `worker_behavior_profile_id`（不可合并——call-site 两参值不同）|

## 二、三层持久化耦合 + 兼容策略

`requested_worker_profile_*` 家族横跨三层强耦合（模型字段值直接写进事件 JSON key）：

| 层 | 可变性 | 兼容机制 |
|----|--------|----------|
| **A. `works` DB 列**（+ 索引）| 可变状态 | `sqlite_init._migrate_legacy_tables` 开机**数据保全防御 `RENAME COLUMN`**（老列在且新列不在时改名 + 刷新 work_columns，置于 ADD-if-missing 前防误建空列丢数据）+ `CREATE TABLE` 新名 + 索引 DROP 老名/CREATE 新名。**非用户门禁、开机自动、保留数据**。**不进 migration_117**（保持 W4-7 决策，列改名是良性 op 不绑死不可逆迁移门禁）。 |
| **B. 事件 metadata JSON key** | append-only event_store，不可改写 | `normalize_control_metadata` 单一收敛点 `_LEGACY_CONTROL_KEY_ALIASES` old→new alias：所有 event-replay 的 control_metadata 经 `merge_control_metadata`→`normalize` 把老 key alias 成新 canonical → 老事件 replay 仍按新名出栈；白名单只收新名。**保老 event replay 零行为变更。** |
| **C. 内存模型 / DTO / 形参 / 局部变量** | 纯 Python / TS | 直接改名。 |

**兼容点收敛**（最少改动面）：① normalize alias map（chokepoint）；② `resolve_delegation_target_profile_id` 防御双读（兜底绕过 normalize 的 raw 老事件）；③ `orchestrator._canonical_requested_worker_type` singleton lane 含老 key 兜底；④ 所有 write 站点仅 emit 新 key。

**未来清理（出本任务）**：normalize alias map + resolver 老 key fallback + orchestrator 老 key 容忍 = 过渡 compat shim，待真实例升级 + 老 in-flight task drain 后独立清理（沿用 F090 双轨→F100 塌缩范式）。

## 三、行为零变更口径

- **DB 列**：RENAME COLUMN 保留数据，值守恒；新库 CREATE 直接新名；fresh DB 自动跳过 RENAME（幂等）。
- **事件 key**：写新 key；老持久化事件经 normalize alias 后按新名读出，dual-read 兜底 → 运行时输出等价。
- **ID 形参合并**：`build_agent_runtime_id` 5 个调用点（仅测试）两参传同值，合并 byte-safe，**产出 runtime_id 保留 `worker_profile:` 前缀字面不变**。
- **`_ensure_a2a_agent_runtime`**：两 call-site 两参值不同（source_agent vs source_worker / target_agent vs requested）→ 不合并，仅形参语义改名（纯名、零逻辑变更）。
- **`AgentRuntimeItem.worker_profile_id` 删除**：W4-5 起即恒等于 worker 行的 agent_profile_id / 非 worker 空、无生产 FE 消费 → 删除等价。

## 四、改动文件（30 代码/测试 + 3 docs + 2 spec 制品）+ 验证

- py src 17（gateway services 13 + core models/store 4，含 migration_117 注释）+ py tests 10（test_agent_context_phase_c.py +2 防回归 + test_sqlite_init_works_rename_f117.py +2 迁移）+ FE 3（types/index.ts + App.test.tsx + ChatWorkbench.test.tsx）+ docs 3（message-model / architecture-audit / milestones）+ spec 制品 2（wave-3-w3-plan / wave-3-completion-report）。
- py 文件 py_compile 全通过。
- 针对性测试（8 受影响文件）：158 passed / 1 skipped；agent_context phase 套件 30 passed；works RENAME 迁移 2 passed。
- **全量回归**：4143 passed（含 2 build_context_request 防回归）vs baseline 4141 **0 regression**；+ 2 隔离迁移测试（test_sqlite_init_works_rename_f117）通过 = **4145 effective**。e2e_smoke 8/8。
- 新增 4 防回归/迁移测试均**实证有效**：build_context_request override 测试在 broken 语义下确 FAIL（钉住回归）；works RENAME 测试验证老列数据守恒。
- **e2e_smoke**：8 passed（2.2s）。
- **残留扫描**：全仓旧 3-token 仅余有意豁免——sqlite_init RENAME map / orchestrator 老 key 兜底 / normalize alias map / resolver 双读 / migration_117 迁移引用。0 非预期残留。
- FE：node_modules 不在本 worktree（避免重装，符合并行轻量原则）→ grep 一致性验证：0 旧 token 残留、新名传播到 types + 2 测试 fixture、唯一生产 .tsx worker_profile_id（AgentCenter.tsx:553）是 `agent.create_worker_with_project` 动作结果（W3 面合并范围外，未碰）。vitest 未跑（已记 limitation）。

## 五、豁免（有意保留）

- `_LEGACY_CONTROL_KEY_ALIASES` 老 key 字面 + `resolve_delegation_target_profile_id` / `orchestrator:978` 老 key fallback + sqlite_init RENAME map 老列名 —— compat shim / 迁移源，注释标注。
- `build_agent_runtime_id` 产出的 `worker_profile:` ID-scheme 前缀 —— 持久化 runtime_id 格式，改之破坏 ID 连续性/dedup。
- `source_worker_profile_id`（A2A source marker）/ `_worker_snapshot_id`（方法名）/ `worker-snapshot:`（值前缀）/ `WorkerProfileStatus`/`WorkerProfileOriginKind`/`WorkerProfileOpsMixin`/`AgentRuntimeRole.WORKER` —— 与本家族无关符号。
- `build_scope_aware_session_id`：实测**无** worker_profile_id 形参（W4 已清），任务/W4 豁免清单此项陈旧，no-op。
- AgentCenter.tsx:553 / AgentCenter.test.tsx:719 的 `worker_profile_id`：`agent.create_worker_with_project` 动作结果契约，属 W3「面合并」独立 UI Feature，本任务范围外。

## 六、已知 limitations

1. **事件 key compat shim**：normalize alias + dual-read 是过渡，真实例升级 + 老 task drain 后独立清理。
2. **FE vitest 未跑**：本 worktree 无 node_modules；改动为纯类型 rename + 测试 fixture，靠 grep 一致性 + 无生产消费验证。合并前若需可在主仓 FE 跑一次 `npm test`。
3. **core-design.md:116 `worker_profile_id` 描述**：是 W4 已删的 `AgentRuntime.worker_profile_id` 字段 + butler 时代宽泛 drift（W4 completion-report 已归类"不做修正式改写"），本任务不改写整段。
4. **migration_117 不动 works 列**：works 改名走 sqlite_init 防御 RENAME；migration 注释已同步更正。

## 七、双评审 panel（触持久化 metadata + DB schema 强制）

并行 Codex（对抗）+ 独立 Opus（spec-对齐）双评审。**panel 价值实证**：Opus 抓到 Codex + 主节点都漏的 1 个 HIGH 隐性回归。

### Opus review — 1 HIGH（已修 + 防回归测试）

- **[HIGH] `agent_context.py:_build_context_request` 委托目标 override 塌成死代码**：全局 token 替换把两个语义不同的局部变量（baseline `requested_worker_profile_id`=委托目标 / `requested_agent_profile_id`=session owner）塌缩成同名 → 委托目标被 owner 链无条件覆盖 + `if ...: X = X` 自赋值死代码 → **worker turn 加载 session-owner 而非 delegation-target profile（行为非零变更）**。divergent（target≠owner）的 A2A/delegate 典型场景受影响；全量 4141 未覆盖该分叉。
  - **修复**：委托目标用独立变量名 `requested_delegation_target_id`（去 worker 命名 + 不碰撞），恢复 baseline override 语义。
  - **防回归测试**（test_agent_context_phase_c.py +2）：`test_build_context_request_worker_uses_delegation_target_over_session_owner`（worker + target≠owner → agent_profile_id==委托目标）+ `..._chat_uses_session_owner`（守恒）。**实证**：临时还原 broken 语义该测试 FAIL（`'agent-profile-MAIN-owner' != 'agent-profile-WORKER-target'`），修复后 PASS。
  - **碰撞排查**：grep origin/master 确认唯一双变量碰撞就在此函数；dispatch_service/delegation_plane baseline 无 `requested_agent_profile_id`（那些 `X=X` kwarg 是同变量改名，非碰撞）；chat.py/session_service.py 未在 3-token 替换集。
- 其余 5 维度（事件 replay / RENAME 幂等 / 形参合并 / FE / 范围）全 PASS。

### Codex review — 0 HIGH / 2 MED（已修）

- **[MED×2] works 防御 RENAME 缺「老列+新列同存」坏半迁移态 backfill**：原条件 `old in cols and new not in cols` 跳过该态，老列数据可能成孤儿。真实例（老列 only）不触发，属防御加固。
  - **修复**：加 both-present 分支——`UPDATE works SET new=old`（老列为 RENAME 前权威源）+ `DROP COLUMN old`，四态（fresh/老/已迁移/坏半迁移）全幂等数据安全。注释标四态。
  - **测试**（采纳 Codex 建议，test_sqlite_init_works_rename_f117.py +2）：`..._old_only_preserves_data`（真实例升级路径：老列+数据→RENAME→数据守恒）+ `..._both_present_backfills_and_drops_old`（坏半迁移→backfill+DROP）。fresh/已迁移态由全量 init_db 隐式覆盖。
- 其余维度（事件 replay / 形参合并）全 PASS。

### 主节点独立对抗自查

- 验证 normalize 是事件 control_metadata 唯一读 chokepoint：memory_tools.py:447 直读 payload 但只取 `subagent_delegation`（非本家族 key），无绕过；三 key 所有生产读经 resolver 双读/normalize alias/transient envelope（当前版本）/normalized 来源。

**0 HIGH 残留。** 全部 finding 闭环（1 HIGH 修 + 测试 / 2 MED 修）。

## 八、状态

- **不主动 push**：归总等用户拍板。
- 真实例无需额外 migrate（works 列由 sqlite_init 开机防御 RENAME 自动处理，含坏半迁移态兜底）。
