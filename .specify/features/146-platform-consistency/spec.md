# F146 平台一致性三小件 — spec/plan（收窄版）

**日期**: 2026-07-19
**规模**: S（一致性收敛，非重构；净生产改动预算 < 300 行）
**Baseline**: master `5311e250`
**问题定义单一事实源**: F111 completion-report §8「平台级 follow-up」①②③
（`.specify/features/111-behavior-compactor/completion-report.md:74`）

## 0. 背景（用户视角）

F111 实施期发现三类平台级不一致，自修了自己的路径、把推广记为 follow-up：

1. 用户在盘上直接编辑 USER.md（`octo behavior edit` / vim / 盘外工具）后，
   **日报（F102）和记忆巩固（F127）到重启前都读旧配置**——改了 `routine_active`
   / `consolidation_active` / 时区，cron 看不见。
2. 通过 **LLM 工具 `behavior.write_file`（F136 审批后落盘）或 Web 文件页
   写入/恢复历史版本（F107）** 改 USER.md 后，进程内 live state 不同步——
   通知 quiet hours、`user_profile.read`、cron 工具的时区解析到重启前都读旧值。
3. 三姊妹 cron 服务（F102 日报 / F127 巩固 / F111 精简）的**触发时间字段改了
   都要重启才生效**（`behavior_compact_config.py:11-14` 文档明写此语义 +
   Codex round5 P2 拒绝带理由「三姊妹统一热重载记 follow-up」）。

## 1. 三件取证现状 → 改什么 / 不改什么

### 件① `_read_user_md` 盘优先推广（F102/F127）

| 取证点 | 现状（行号为 baseline） |
|--------|------|
| F111 参照实现 | `behavior_compaction.py:561-589`：**盘优先**（`resolve_write_path_by_file_id(project_root, "USER.md")` 读盘）→ 失败/缺失 → `snapshot_store.get_live_state("USER.md")` 兜底 → None（#6）|
| F102 现状 | `daily_routine.py:530-543`：**只读 live state**，无盘读；构造器**无 `project_root`** |
| F127 现状 | `memory_consolidation.py:746-758`：同 F102 只读 live state；构造器无 `project_root` |
| USER.md 盘路径 | SHARED 文件 → `behavior/system/USER.md` 单一路径，slug 无关（`paths.py:192-193`）；与 harness `load_snapshot` 的路径（`octo_harness.py:465`）一致 |
| 构造点 | 生产仅 harness 2 处（`octo_harness.py:1605/1677`，`project_root` 变量在作用域内）；测试 7 处（f102 ×1 / f127 ×6），全 keyword 构造 |
| F111 测试锚参照 | `test_f111_compact_trigger.py::TestConfigDiskFirst` 两用例（盘赢 stale snapshot / 盘缺失 snapshot 兜底）|

**改**：
- 新建共享 helper 模块 `services/user_md_cron.py`：`read_user_md_disk_first(project_root, snapshot_store, *, log_prefix) -> str | None`——F111 逐字节等价语义（盘优先 → live state 兜底 → None；两级 log 事件名 `{log_prefix}_read_user_md_disk_failed` / `{log_prefix}_read_user_md_failed` 与三服务既有事件名逐一对上）。收敛判断：三份实现语义同源、收敛成本一个小模块，**不超 S**，做收敛而非三处各抄一份。
- F102/F127 构造器加**必填** keyword `project_root: Path`（对齐 F111 先例，不做 optional 双模式——`None → 永远看不见盘` 是隐性双轨坏味道）；`_read_user_md` 改调 helper。harness 2 处补传；7 处测试构造点机械补传 `tmp_path` 派生 root。
- F111 `_read_user_md` 同步重构为调 helper（去三份重复；行为由既有 `TestConfigDiskFirst` 锚零变更验证）。

**不改**：`_resolve_user_timezone` 三份相同实现（正确且一致，不在三件范围，v0.2 收敛候选）；notification.py:404 quiet hours / `user_profile_tools.py:316` / `cron_tools.py:102` 三个 live state 读点**不改盘优先**（件② 已让进程内写点全同步 live state；纯盘外编辑对这三处的可见性延迟到重启，与 SnapshotStore 冻结/live 二分的设计初衷一致，归 v0.2 评估）。

**测试锚（行为变更=盘外编辑立即可见）**：
- `test_f102_daily_routine_service.py::TestConfigDiskFirst`（新增 2 用例：盘赢 / 盘缺失兜底）
- `test_f127_consolidation_trigger.py::TestConfigDiskFirst`（同 2 用例）
- F111 既有 `TestConfigDiskFirst` 2 用例保持 PASS（helper 重构零回归锚）

### 件② 写 USER.md 后 live-state 同步核查

| 写点 | 现状核查结论 |
|------|------|
| F111 accept（参照）| `behavior_compact_approval.py:314-322` **已同步**（`file_id=="USER.md"` → `update_live_state`，best-effort warning 降级）——F111 自修，无需再动 |
| `user_profile.update` 工具 | 走 `snapshot_store.append_entry/write_through`，**天然同步**，无需动 |
| F136 LLM 工具 `behavior.write_file` | `misc_tools.py` `behavior_write_file`：落盘 + 版本 + cache invalidate 后**缺 live-state 同步** ✗ |
| F107 restore（`behavior.restore_version` action）| `worker_service.py:645-` `_handle_behavior_restore_version`：落盘 + 版本 + cache invalidate 后**缺同步** ✗ |
| Web 编辑器保存（`behavior.write_file` action）| `worker_service.py:563-` `_handle_behavior_write_file`：**取证中发现的同类缺口**（F111 报告字面只点名工具与 restore，但该 action 与工具同名同缺口、同文件顺手补；注：该路径也缺 cache invalidate——**不在本件扩面**，单独归档 follow-up 防止 scope 蔓延到 behavior pack 缓存语义）|
| snapshot_store 可达性 | 工具层：`deps._snapshot_store`（既有惯例 `user_profile_tools.py:197`）；control_plane 层：`ControlPlaneContext` **现无** snapshot_store 字段，需加（default None 降级）+ `_coordinator` 透传 + harness `:1550` 构造补传 `app.state.snapshot_store`（`:474` 赋值，时序在前 ✓）|

**改**：三个写点在成功落盘后按 F111 accept 同款范式补 `file_id == "USER.md"` → `update_live_state("USER.md", content)`（best-effort，异常仅 warning——盘上已 durable，live state 落后一拍不破坏正确性底线）。`ControlPlaneService`/`ControlPlaneContext` 加 `snapshot_store: Any = None` 参数（None → 跳过同步 = 现状，既有测试构造点零改动）。

**不改**：审批状态机 / gate 语义零触碰；非 USER.md 文件不进 live state（live state 只有 USER.md/MEMORY.md 两键，写其他 behavior 文件本就无此问题）；`_handle_behavior_write_file` 缺 cache invalidate 归档 follow-up 不顺手修。

**测试锚**：
- `test_f136_write_approval.py`（或工具层邻近套件）：审批通过写 USER.md 后 live state == 新内容
- `tests/services/test_behavior_restore.py`：restore USER.md confirmed=true 后 live state == 恢复内容
- 同套件补 Web 编辑器 action 写 USER.md 同步锚

### 件③ 三姊妹 cron 时间热重载统一

| 取证点 | 现状 |
|--------|------|
| F102 | `daily_summary_time` 仅 startup `_register_cron` 一次性烘焙（`daily_routine.py:158-200/608-622`）；tick 内 config 重读只惠及 `routine_active`/时区窗口计算/channels，**触发时间本身改了要重启** |
| F127 | `consolidation_time` 同款（`memory_consolidation.py:204-238/253-265`）|
| F111 | `compact_time` 同款（`behavior_compaction.py:201-232/246-259`）；`behavior_compact_config.py:11-14` 文档明写「改时间/时区需 octo restart 生效…统一热重载记 follow-up」= 本件出处 |
| 三处语义 | **一致地不支持热重载**（无分歧），实现三份 |

**改**：统一为一个明确语义——**「下一次已排定的 cron tick 读盘生效，无需重启」**：
- helper 模块 `user_md_cron.py` 加 `register_cron_job(scheduler, *, job_id, callback, cron_expr, timezone_name, misfire_grace_sec) -> tuple[str, str]`（注册/替换 job，返回注册 key `(cron_expr, effective_tz)`）。三服务 `_register_cron` 改调它并记录 `self._registered_cron_key`。
- 三服务 cron tick 回调在 **config 读取之后、active 检查之前** 加 reconcile：重算 key ≠ 已注册 key → `replace_existing=True` 重注册 + log（`{prefix}_cron_rescheduled`）。放在 active 检查前：disabled 服务仍跟踪时间变更，重新启用时时间已正确。
- 语义边界诚实归档：改时间后**最近一次已排定触发仍按旧时间跑**（该 tick 完成 reconcile，其后按新时间）——即最迟一个旧调度周期收敛；`already_running` 单飞 skip 的 tick 不 reconcile（下个 tick 补）；手动路径（F111 `run_manual`）不 reconcile（用户在场动作，不加行为面）。
- `behavior_compact_config.py:11-14` 过时文档同步改写。

**不改**：不做分钟级轮询/watchdog 监听（新增常驻扫描面超 S，且「下个调度周期生效」已消除重启依赖）；不为 reschedule 加审计事件（log 可观测足够，三服务事件 schema 零变更）；APScheduler 在 job 回调内 `add_job(replace_existing=True)` 替换自身 job 定义是标准安全操作（运行中实例不受影响）。

**测试锚（每服务 2 用例）**：
- 改盘上 USER.md 时间字段 → 触发 tick 回调 → 断言 `add_job` 被二次调用（新 crontab）+ `_registered_cron_key` 更新
- 时间未变 → tick → 断言无重注册（`add_job` 调用数不变）
- 落位：`test_f102_daily_routine_service.py` / `test_f127_consolidation_trigger.py` / `test_f111_compact_trigger.py` 各自 `TestCronHotReload`

## 2. 红线自查

- 不动 frontend / front_door / 审批状态机 ✓（改动面：gateway services + harness 装配 + control_plane ctx 传参 + core 零触碰）
- #6 降级链原样：USER.md 缺失/损坏 → live state 兜底 → None → config 默认值；同步失败仅 warning ✓
- 每处行为变更测试锚定（件① ×4 新增 + 2 既有 / 件② ×3 / 件③ ×6）✓
- 净生产改动预估 ~180 行 < 300 ✓

## 3. 实施顺序（每件独立 commit）

1. 件①：helper 模块 + 三服务 `_read_user_md` 收敛 + F102/F127 `project_root` + harness/测试构造点 + 测试锚
2. 件③：helper `register_cron_job` + 三服务 reconcile + config 文档 + 测试锚（与件① 同 helper 模块，顺序做避免自冲突）
3. 件②：三写点同步 + ControlPlaneContext/harness 装配 + 测试锚
4. 终门：全量回归 + e2e_smoke/scripted + 双评审 + completion-report + milestones F146 行 ✅
