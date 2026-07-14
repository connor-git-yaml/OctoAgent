# F111 Behavior Compactor — Completion Report

**日期**: 2026-07-15
**分支**: `feature/111-behavior-compactor`（rebase 自 origin/master `f2081010`，未 push）
**Baseline**: `f2081010`（实测 5207 passed / 14 skipped / 1 xfailed / 1 xpassed）
**最终门**: 5347+ passed / 0 failed（终验号见 §6）+ e2e_smoke/scripted 26 passed（pre-commit 每 commit 过闸）
**评审**: Codex（gpt-5.4）**21 轮迭代至连续两轮 0 finding**（累计 5 P1 + 14 P2 + 3 P3：全部接受修复或拒绝带证据）+ Opus 式对抗自审（抓 3 spec 级 + 1 行为级真问题）

---

## 1. 收窄决策表（尤其岔路③实测结论）

| 岔路 | 结论 | 依据 |
|------|------|------|
| ① 范围 | 单文件内去冗余；跨文件矛盾检测 defer v0.2 | 用户拍板（milestones F111 行）|
| ② 触发 | **cron（03:30，compact_active 默认 False 保守关）+ 手动 `octo behavior compact` 双触发**；cron 复用 F127 编排全套（合成 root spawn 审计容器/单飞两层/占位泄漏防御/quiet hours 归 NotificationService） | 用户拍板 |
| ③ 审批载体 | **独立 `behavior_compact_candidates` 候选表 + REST accept/reject**，不用 F136 gate。**实测三重硬冲突**：a) `gate_behavior_write` 是 `wait_for_decision(300s)` 阻塞模型（write_approval.py:39/416）——凌晨提议 5 分钟即超时丢弃，nightly 零交付；b) 超时刻意不恢复 RUNNING（:431，F101 HIGH-02 v3）——后台任务系统性终 FAILED；c) 审批通知 CRITICAL 豁免 quiet hours（:403）——半夜打扰，与 nightly 语义正好相反。辅助：批量串行/内存 handle 重启丢失/v0.1 无 LLM 工具则 gate 无调用方。**概念错配防治**：不复用 memory 候选表（字段 SOR 记录级），新建文件级表；复用的是五态+atomic claim+CONFLICT+失败二分**模式** | 收窄期实测（spec §0.3 全文）|
| ④ 验证 | 三层全落地：L4/L3 确定性护栏 + `e2e_scripted` 全链 + `e2e_full+real_llm` 质量用例（已真打 1 次 PASS，见 §4）| 用户拍板 |
| ⑤ H1 守界 | cron 后台 subagent 审计容器（minimal profile）；发现端确定性组件（F127 归档偏离同款，坑 7）；**零 agent 自主 commit**（accept 是唯一落盘入口，AC-7 静态断言） | 用户拍板 |

**连带收窄**（spec §0.0）：v0.1 无 `behavior.compact` LLM 工具（拍板双触发不含）；cron 范围 = SHARED∩eligible 3 文件（派生非硬编码）；CLI = 薄 HTTP 壳。

## 2. 每 Phase 实际 vs 计划

| Phase | 计划 | 实际 | 偏离 |
|-------|------|------|------|
| 收窄+spec 评审 | rebase + 收窄 + Codex 0 HIGH | rebase 零冲突；Codex CLI 首跑撞 gpt-5.6-sol 版本墙 → `-c model=gpt-5.4` 覆盖重跑 0 finding（docs-only 未深入）→ 按任务书补对抗自查抓 3 真问题（占位符碰撞/H6 含自身字段/RATIONALE 截断守卫） | 方法偏离归档 |
| A 地基 | 事件/白名单/PROTECTED/store | 8 事件+payload、eligible 白名单（fail-closed 偏离归档）、占位符 helper、五态候选表 | 无 |
| B 发现端 | discovery + config | 全按计划 + 实测抓 1 皱褶（尾换行规范化） | 无 |
| C 审批端+REST | approval + routes | 全按计划 + 实测抓 1 真 bug（claim 未提交写锁 vs versionable 连接死锁 → 后被 Codex P1 重构为单一提交点更优解） | 无 |
| D cron 编排 | 仿 F127 全套 + harness | 全按计划；root 占位后抽单一事实源模块（round5）| 演进 |
| E CLI | 薄 HTTP 壳 | 全按计划 + 4 轮 Codex UX 精化（project 解析/归一化/scope 展示/409 分类/trusted_proxy fail-fast/list-size 按 project）| 精化 |
| F e2e | scripted 全链 + real_llm | 全按计划；AGENTS.md marker 表同步（AC-11 注归档：脚本缝在 message-adapter 协议，v0.1 无决策环工具）| 无 |
| Verify | 双评审+回归+文档 | Codex 21 轮 + 自审 + 真打 real_llm 1 次 | 超预期轮次 |

## 3. 护栏机械验收证据（各一条测试锚）

| 护栏 | 测试锚 |
|------|--------|
| H1 变小（含截断骗 H1 的三层封堵）| `test_f111_compact_discovery.py::test_larger_output_rejected` + `::test_truncated_output_fallback` + `::test_midbody_delimiter_emission_fallback` |
| H2 PROTECTED 字节级（占位符构造性保证）| `test_f111_protected_sections.py::TestVerifyAndReinsert::test_byte_level_roundtrip` + `test_f111_compact_discovery.py::test_protected_violation_rejected` + accept 复验 `test_f111_compact_approval.py::test_protected_duplicated_in_candidate_conflicts` |
| 禁区双层 | `::test_excluded_files_skipped`（第一层）+ `test_f111_compact_approval.py::test_not_eligible_candidate_conflicts`（第二层）+ 白名单派生守卫 `test_f111_behavior_compact_store.py::TestEligibleWhitelist` |
| H6 config parity（含 active_hours/自身字段）| `::test_user_md_config_drift_rejected` + `::test_user_md_active_hours_drift_rejected` |
| C4 红线（发现端零落盘）| `::test_no_autonomous_commit_path`（grep 静态）+ `::test_discover_proposes_without_write`（字节断言）|
| C9 边界 | `::test_no_hardcoded_dedup_rules`（grep 静态）|
| 新鲜度 CONFLICT | `test_f111_compact_approval.py::test_source_changed_conflicts_no_write` |
| F107 快照强前置 | `::test_snapshot_record_failure_restores_file`（快照失败即还原文件）|

## 4. e2e 证明

- **L3 scripted 全链（AC-11）**：`test_e2e_scripted_behavior_compact.py` 2 用例——真 harness bootstrap → 脚本 compact 脑经 `BehaviorCompactionService.llm_client` 公开缝 → 真 REST trigger → 候选 → 真 REST accept/reject → 真落盘 + F107 版本（baseline+新版内容断言）+ 事件链；零真 LLM 三重防御（resolve_for_alias bomb/空凭证/内容贯穿）；已进 pre-commit hook + lane pr + CI（e2e_scripted marker）。
- **real_llm 质量用例（AC-12）已真打 1 次 PASS（GPT-5.5 main alias，8.35s）**：植入三组语义重复 AGENTS.md 变体 → 342→242 字符（-29%），三组各并一条、独立规则语义保留、PROTECTED 字节级原样、rationale 清晰列点。进 release live lane（`e2e_full+real_llm` 双标，AGENTS.md marker 表已同步）。

## 5. F127 复用件 vs 新造

**复用/仿写**：cron+合成 root spawn+单飞两层+capacity skip 编排（memory_consolidation 逐项对照）；发现端 llm_client 注入范式 + "宁缺毋滥"prompt 句 + fallback 语义；候选五态+atomic claim+CONFLICT+失败二分模式；先 commit 后 emit（坑 1）；SYSTEM_INTERNAL_WORK_IDS+guard（坑 3）；幂等白名单式（坑 4）；claim 后全程回滚（坑 5）；确定性组件非 free-loop（坑 7）；LLM 怪癖校验层兜（坑 8）；config 左边界锚定+注释块剥离范式；通知决策表（proposals>0 一条 MEDIUM/全局桶/run_id 幂等）；F136 落盘序列（prepare/commit+record_version+invalidate cache）；F107 版本兜底；attest/remote CLI 的 env/token 解析链。
**新造（真缺口 5+3 件）**：发现端本体（契约 A' 解析+护栏 H1-H6）；PROTECTED 占位符提取/插回（F063 P3 设计从未实现）；文件级候选表+store；审批端（新鲜度+H2 复验+快照强前置+还原）；REST 面；CLI compact 子命令；compact config 两字段；root 占位单一事实源模块。

## 6. 改动与回归数

- **22 commits**（docs 3 + feat 5 + fix/评审闭环 13 + test 1）；净增 ~6100 行（生产 ~2600 + 测试 ~2900 + spec/docs ~600）。
- 新生产文件 8：`behavior_compact_{discovery,approval,config,root}.py` / `behavior_compaction.py` / `routes/behavior_compact.py` / `core/{models/behavior_compact,store/behavior_compact_store}.py` / `behavior_workspace/protected.py`（9 个含 protected）。
- 触碰共享面：enums/payloads/_types/sqlite_init/StoreGroup/octo_harness/main.py/control_plane/_base/behavior_commands/behavior_versioning（strict 参数，默认零变更）/skeleton（project_slug 参数，默认零变更）/tests/AGENTS.md。
- **回归**：全量 `-m "not real_llm"` 终验（见对话终值，≥5347 passed / 0 failed；baseline 5207 → 净增全为 F111 新测试 ~140）；e2e_smoke+scripted 26 passed 每 commit 过闸；F136/F127 影响面（共享 helper）焦点回归通过。

## 7. Codex 21 轮闭环账（5 P1 / 14 P2 / 3 P3）

接受修复（摘要）：审批/发现端 commit 失败诚实降级+补偿关事务一族（4 项）；单一提交点重构（消灭 APPLYING 遗留窗口）；分隔符/fence 解析三重收窄+歧义检测（4 项）；manual 持久态单飞；尺寸闸 masked 基准；SHARED slug 归零；用户空行保留；H6 补 active_hours；快照强前置+失败还原；root 占位单一事实源；REST project_slug 显式必填/limit 参数/pending_count 真总数；CLI 6 项 UX。
拒绝带证据（3 项）：round8 P2 count 复验误伤（section 含 🔒 标记，回归测试复现场景证明通过）；round16 P1 verify→write TOCTOU（零 await 点构造性排除 in-process；跨进程残余与 F127/F136 归档同类）；round5 P2 compact_time 热重载（与 F102/F127 姊妹平台语义一致，统一热重载记 follow-up）。

## 8. Deferred / follow-up 清单

- **v0.2（spec §2.2 既定）**：跨文件矛盾检测；`behavior.compact` LLM 工具 + F136 gate 接线；per-project cron fan-out；bulk_reject；前端候选审批 UI（与 F127 同类缺口合并）；SOUL/IDENTITY 解禁配置。
- **平台级 follow-up（F111 发现、不扩面）**：①F102/F127 `_read_user_md` 同类"只读 snapshot live state，盘外编辑不可见"（F111 已自修为盘优先）；②F136 behavior.write_file / F107 restore 写 USER.md 后不同步 live state（F111 accept 已自修）；③三姊妹服务（F102/F127/F111）cron 时间字段热重载统一方案；④contract 解析的"模型恰好单次中缝分隔符且尾部无信号"残余不可判定场景（H2/H4 兜，已归档）。
- **测试面已知项**：非 e2e 的 f111 测试文件未加 importorskip（与 F127 测试同暴露类——hook 跨树收集时 loud fail 非假绿，沿用既有先例）。

## 9. 合入建议

**建议合入 origin/master**。理由：0 regression（全量终验 + smoke/scripted 每 commit 过闸）；Codex 连续两轮 0 finding + 对抗自审闭环；C4/C7/C9/H1 红线全部机械可验；real_llm 质量真打 PASS；所有偏离显式归档（fail-closed 白名单命名 / 输入幂等 vs F127 输出幂等 / SKIP 不截断 / main alias / DP-8 无预算闸 / REJECTED 分路径 / marker 表措辞）。等用户拍板后 push。
