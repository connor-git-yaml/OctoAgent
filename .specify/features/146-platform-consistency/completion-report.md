# F146 平台一致性三小件 — Completion Report

**日期**: 2026-07-19
**分支**: `feature/146-platform-consistency`（基于 master `5311e250`，未 push）
**提交链**: `7cc49795` spec → `00420302` 件① → `4b398a04` 件③ → `fe4d4a01` 件② → 本 docs 收口
**最终门**: 全量 `-m "not real_llm"` **5361 passed / 0 failed**（= master 基线 5347 + 本
Feature 新增 14 测试，算术自洽 0 regression）；e2e_smoke/scripted 26 passed 每 commit
hook 过闸 ×3
**评审**: Codex（gpt-5.4）spec 轮 + final 轮均 **0 finding**（final 轮实读 diff + 写核
write.py + 自行 py_compile 8 文件）+ Opus 式对抗自审 20 维度（唯一产出=harness 注释
方法名校正，无行为问题）——**0 HIGH 残留**

---

## 1. 三件「取证结论 → 实际改动」表

### 件① `_read_user_md` 盘优先推广（F102/F127）

| 取证结论 | 实际改动 |
|----------|----------|
| F102 `daily_routine.py:530` / F127 `memory_consolidation.py:746` 确认**只读 snapshot live state**（内存 dict），盘外编辑到重启前不可见；两构造器均无 `project_root` | 新建共享 helper `services/user_md_cron.py::read_user_md_disk_first`（叶子模块只依赖 core；log 事件名经 `log_prefix` 参数化与三服务既有事件名逐一对上）；F102/F127 构造器加**必填** keyword `project_root`（F111 先例，不做 optional 双模式）；`_read_user_md` 收敛到 helper；F111 自身内联实现同步收敛（去三份重复，行为由其既有 `TestConfigDiskFirst` 2 锚零变更验证）；harness 2 构造点 + 7 测试构造点补传 |
| USER.md 是 SHARED 单一路径（`paths.py:192`，slug 无关），与 harness `load_snapshot` 路径一致 → 盘优先语义安全 | 测试锚：`test_f102_daily_routine_service.py::TestConfigDiskFirst` + `test_f127_consolidation_trigger.py::TestConfigDiskFirst` 各 2 用例（盘赢 stale snapshot / 盘缺失 live state 兜底）|

### 件② 写 USER.md 后 live-state 同步核查

| 写点 | 核查结论 | 实际改动 |
|------|----------|----------|
| F111 accept（`behavior_compact_approval.py:314`）| **已同步**（F111 自修，参照范式）| 无需改（诚实核查结论）|
| `user_profile.update` 工具 | 走 `snapshot_store.append_entry/write_through` **天然同步** | 无需改 |
| F136 LLM 工具 `behavior.write_file`（misc_tools）| 落盘+版本+cache invalidate 后**缺同步** ✗ | 补 F111 同款 best-effort 块（`deps._snapshot_store` 既有惯例访问）|
| F107 restore（worker_service `_handle_behavior_restore_version`）| **缺同步** ✗ | 抽 `_sync_user_md_live_state` 私有 helper 补齐 |
| Web 编辑器保存（worker_service `_handle_behavior_write_file`）| **取证中发现的同名同缺口**（F111 报告字面未点名，同文件顺手补）| 同 helper 补齐 |
| snapshot_store 可达性 | control_plane 层不可达 | `ControlPlaneContext`/`ControlPlaneService` 加 `snapshot_store: Any = None`（None 降级跳过=既有行为，既有测试构造点零改动）+ coordinator 透传 + harness 补传 `app.state.snapshot_store` |

测试锚 4：F136 工具审批通过同步 + **拒绝不污染反向锚** / golden action 写同步（含非
USER.md 不触碰断言）/ restore 同步。

### 件③ 三姊妹 cron 时间热重载统一

| 取证结论 | 实际改动 |
|----------|----------|
| 三服务触发时间**一致地** startup 一次性烘焙、改了要重启（`behavior_compact_config.py:11-14` 文档明写 + Codex round5 P2 拒绝带理由「统一热重载记 follow-up」= 本件出处）；实现三份 | 统一语义 = **「下一次已排定的 cron tick 读盘生效，无需重启」**：helper 加 `register_cron_job`（注册/替换返回 key）；三服务 `_register_cron` 收敛 + 记录 `_registered_cron_key`；tick 回调在 config 读后、active 检查前加 `_reconcile_cron`（disabled 服务也跟踪时间变更）；三份内联 CronTrigger 注册代码删除；compact_config 过时文档改写 |
| — | 测试锚 6：三测试文件各 `TestCronHotReload` 2 用例（改时间→重注册+key 更新 / 未变→不重注册；`delenv OCTOAGENT_USER_TIMEZONE` 防宿主环境假红）|

## 2. 语义边界（诚实归档）

- 件③：改时间后**最近一次已排定触发仍按旧时间跑**（该 tick 完成 reconcile 后按新
  时间）；`already_running` 单飞 skip 的 tick 不 reconcile（下个 tick 补）；F111
  `run_manual` 不 reconcile（用户在场动作，不加行为面）；reconcile 失败仅 log 旧调度
  保持（#6）。不做分钟级轮询/watchdog（新增常驻扫描面超 S，且「下个调度周期生效」
  已消除重启依赖）。
- 件①：性能面 = 每服务每日 1 次 cron tick + startup 各读一次 ~2KB 文件，可忽略；
  #6 降级链逐级等价（盘缺失/损坏 → live state → None → config 默认值）。
- `register_cron_job` 注册 key 记录**请求时区名**而非降级结果——极端非法名场景 key
  比对可能触发一次幂等重注册，无行为影响（上游 F115 校验已挡非法值，docstring 归档）。

## 3. 已知 limitations / follow-up（不在本件扩面）

1. **写核非原子窗口（既有，非 F146 引入）**：`commit_behavior_file_write` 是非原子
   `write_text`（write.py 文档明写）——盘读方（F111 accept 自 round9 起 + 本件推广的
   F102/F127）在微秒级写窗口内理论可读到半文件 → config 解析对缺失字段落默认值
   （下个 tick 自愈）。治本 = 写核改 os.replace 原子写，属 core 写路径变更，归档
   follow-up。
2. **Web 编辑器保存路径缺 behavior pack cache invalidate**（取证发现的独立既有缺口，
   `worker_service._handle_behavior_write_file` 无 invalidate 而 LLM 工具/restore/
   F111 accept 均有）——不在件② live-state 范围，归档 follow-up。
3. **live state 其余读点未盘优先**（notification quiet hours / `user_profile.read` /
   cron_tools 时区）：件② 已让全部进程内写点同步 live state；纯盘外编辑对这三处的
   可见性延迟到重启，与 SnapshotStore 冻结/live 二分设计初衷一致，归 v0.2 评估。
4. `_resolve_user_timezone` 三份相同实现未收敛（正确且一致，不在三件范围，v0.2
   候选）。

## 4. living-docs 漂移闸

- `docs/blueprint/milestones.md` M10 表 F146 行 ✅（本 docs commit）。
- `behavior_compact_config.py` 模块文档（原「改时间需 octo restart」）已随件③改写。
- 检查过 `docs/codebase-architecture/` 无描述三服务 cron 注册时机/USER.md 读取路径的
  细节段落需要同步（daily-routine/consolidation 未见专章；file-workbench.md 不涉及）。

## 5. 合入建议

**建议合入 origin/master**。理由：0 regression（5361 passed 全量 + smoke/scripted
每 commit 过闸）；Codex spec+final 两轮 0 finding + 对抗自审 0 HIGH；三件行为变更
全部测试锚定（14 新增用例）；净生产改动 ~230 行 < 300 红线；发现的两个既有缺口
（写核非原子/Web 写缺 invalidate）诚实归档未扩面。等用户拍板后 push。
