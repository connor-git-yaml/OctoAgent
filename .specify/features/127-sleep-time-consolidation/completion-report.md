# F127 Sleep-Time Memory Consolidation — Completion Report

**日期**: 2026-07-03
**分支**: `feature/127-sleep-time-consolidation`（35 commits，待用户拍板 push）
**Baseline**: master 0f59bd3e
**结论**: **全部 Phase 闭环，0 HIGH 残留，建议合入 origin/master**（详见 §8 push 闸清单）

---

## 1. 一句话总结

F127 把 OctoAgent 已有的成熟原语（SOR 四态 + MERGE 写管道 + 候选审批范式 + cron + subagent spawn）用**后台编排 + 审批治理**串成"睡眠时记忆巩固"闭环：深夜 cron → 合成 root spawn 后台 subagent → LLM 发现冗余 → `consolidation_candidates` 人审（CONFLICT 终态）→ MERGE commit（源软删可回滚）→ H1 守界通知。**不造 memory 原语，不改 H1/H2/H3。**

## 2. Phase 对照（plan 计划 vs 实际）

| Phase（plan） | 计划范围 | 实际 | 偏离/备注 |
|---------------|---------|------|-----------|
| **A 数据模型+事件+root task** | EventType 8 个 + `consolidation_candidates` 表 + root task/work 合成 + Run 模型 | ✅ 按计划 | 无偏离；events 表 PK(task_id,task_seq) 冲突实施期发现（§4.3） |
| **B 触发编排 cron** | MemoryConsolidationService + config 解析 + spawn/skip/单飞 | ✅ 按计划，**spawn 走通未 fallback**（保 H2 subagent 对等） | Opus 自审抓 2 真回归：ensure root 提前到 emit 前（FK 安全）+ root Work 泄漏用户视图（`SYSTEM_INTERNAL_WORK_IDS` 修复） |
| **C 回顾+提议（发现端）** | subagent 巩固逻辑 + LLM prompt + validate-no-commit + 幂等账本 | ✅ 功能全达 | **★ 架构偏离（归档 §4.1）**：发现端做成确定性组件而非 subagent free-loop |
| **D 审批+commit** | 候选审批路由 + atomic claim + rollback + 敏感强制人审 | ✅ ConsolidationApprovalService + REST 4 端点 | **敏感语义演化（归档 §4.2）**："敏感强制人审"→"敏感三层排除"（原语义不成立：敏感 MERGE commit 会毁内容）；新增 **CONFLICT 终态**（plan 未预见，codex 第二轮 P2 驱动） |
| **E 通知** | pending-review MEDIUM + 无提议不噪声 + channels + 幂等 | ✅ 按计划 | codex P2：Web-only 用户死角 → session_id="" 全局通知桶 |
| **F H1+端到端** | H1 无用户对话 + SUBAGENT_INTERNAL + 全链 event_store 可查 | ✅ 三分支全链 e2e（accept/reject/0 提议）真查 event_store | AC-10 cleanup 真执行链不在本 e2e 复验（spawn 契约由 trigger + capability_pack 测试守，task_runner 真执行属 F097 既有覆盖） |
| **Verify（Codex Final + 强 model + 文档）** | 单次 Final review + 强 model OctoBench + completion-report | ✅ 拆解执行：codex **多轮迭代 review**（贯穿各 Phase，§5）+ **G→G-lite**（§4.4，用户拍板）+ Phase H 文档（本报告） | 多评审 panel 实质达成：codex 断链期间 Opus 自审主审闸（C/D），codex 恢复后补跑全域并迭代到 0 finding |

## 3. 关键设计产出（下游可复用）

1. **合成 root spawn 编排**：`_ensure_consolidation_root()` 合成 **Task+Work 成对**（F102 audit-task 范式扩展——F102 只建 task；`spawn_child` 必需真 parent 对，None 会在 `_launch_child_task` 炸）+ `SYSTEM_INTERNAL_WORK_IDS` 单一事实源排除系统占位 Work 泄漏。后台 routine 派 subagent 的第一条可复用路径。
2. **既有事实的破坏性变更人审范式**：`consolidation_candidates` 表（OQ-1 决议不复用 observation_candidates——数据流错配）+ atomic claim CAS + **commit 前验证**（新鲜度 + 敏感最后闸）+ 失败语义二分（**判定失败=确定性→CONFLICT 终态；自身异常=临时故障→回滚 PENDING 可重试**）。
3. **CONFLICT 终态 + MEMORY_CONSOLIDATION_CONFLICTED 事件**：accept 时系统检测候选失效（源被更新/删除/被共享源候选合并）→ 不回滚 PENDING（内容已失效重审无意义）→ REST 409 引导等下次巩固重新提议。actor=SYSTEM 与用户决策 REJECTED 区分。G-lite 真 LLM 第一跑就自然复现共享源双候选场景，佐证该设计必要性。
4. **敏感分区三层防御**（SENSITIVE_PARTITIONS 单一事实源 `octoagent.memory.enums`，与 write_service `_safe_sor_content` 同判定源）：①发现端窗口排除（根治）②`_propose_group` any 语义（任一源敏感即拒）③审批端 accept 最后闸（防存量/伪装候选）。
5. **幂等账本阻断白名单**：`_DUP_BLOCKING_STATUSES={PENDING,APPLYING,APPLIED}` 白名单式（非黑名单）——REJECTED（用户可重新决定）/ CONFLICT（基于新源重新提议是恢复主流程）不阻断；未来新增终态默认不阻断。
6. **通知语义**：仅 proposals>0 发一条 MEDIUM；0 提议/FAILED/SKIPPED 全静默；quiet hours 内 **discard + 审计**（非延迟投递）——深夜触发被丢弃是预期，用户经 Web 候选列表主动发现（FR-C6）。

## 4. 偏离与决策归档

### 4.1 Phase C 架构偏离：发现端=确定性组件，非 subagent free-loop

发现逻辑（拉窗口→LLM 识别→propose→写候选）放在 `ConsolidationDiscoveryService`（llm_client 注入式确定性组件），Phase B spawn 的后台 subagent 保留为 H2 对等的 SUBAGENT_INTERNAL 审计容器（spawn-and-die + cleanup hook + 并发隔离）。理由：①FR-B `[@test]` 绑定要求确定性单测（free-loop 不可确定性测）；②plan §Phase C 明确"确定性层可单测，LLM 层留强 model 验证"；③`tool_profile="minimal"` 当前挂不到任何工具（无 builtin 工具标 minimal，free-loop 需先打通 per-tool profile override，spec 第一决策点未拍板）。

### 4.2 敏感分区语义演化（codex 第二轮 2 P1 驱动）

plan 原文"敏感分区（HEALTH/FINANCE）强制人审"语义**不成立**：现有 write_service MERGE commit 走 `_commit_add`，敏感 proposal 的 SOR content 被 `_safe_sor_content` 替换成 rationale 且 MERGE 分支不建 vault——accept 会**毁掉敏感记忆**（P1-2）；且敏感性按推断目标 partition 众数算时 LLM 混组会把敏感内容降级明文存储（P1-1）。修复= v0.1 收窄为**敏感不进巩固**（三层防御，§3.4）；敏感合并推 v0.2 vault-aware MERGE（spec §2.2 deferred）。

### 4.3 实施期抓出的既有边界（非 F127 引入）

- **events 表 PK(task_id,task_seq) 冲突**：多事件挂同一 root_task 硬编码 task_seq=0 必撞 UNIQUE → 改 `append_event_committed`（MAX+1 自动重试）+ **先 commit 状态再 emit**（防 seq 重试 rollback 丢状态）。
- **F102 遗留面**：F102 只建 task 不建 work 故无 Work 泄漏问题；F127 因 spawn_child 必需 work 对而**首次引入**该面并修复（`SYSTEM_INTERNAL_WORK_IDS`）。

### 4.4 G→G-lite 决策（用户拍板）

原 plan Phase Verify 含"强 model（订阅 Sonnet/GPT-5.x）跑记忆巩固域 OctoBench task"。用户拍板改 **G-lite**：DeepSeek-V3.2 API key（bench alias）跑发现端真 LLM 验证——避订阅 OAuth 自动化 ToS 灰色地带；强 model 质量评估归 **M7 统一强 model OctoBench 方案**。结果：**6/6 PASS**（2 批 ×3 轮，9 硬断言/轮：≥1 合法 MERGE 提议 / source_sor_ids ⊆ 植入冗余组 / 零污染零幻觉 / C4 源仍 CURRENT / 事件对齐）+ 4 项质量观察（共享源双候选自然复现 / 单源伪组被 MIN_GROUP_SOURCE_COUNT 过滤 / 裸 JSON 解析兜底 / 保守拆分带排除理由）。详见 [glite/result.md](./glite/result.md)。

## 5. Codex review 全轮次闭环（0 HIGH 残留）

| 域 | 轮次 | Finding | 处理 |
|----|------|---------|------|
| **A+B**（触发编排） | 初审 | finding-1 spawn 缺主 Agent 记忆 scope 注入 / finding-2 候选状态转换缺 current-state CAS / finding-3 巩固子 Work 后代泄漏用户视图 / NFR-3 tool_profile readonly 破坏只读边界（改 minimal） | 4 全修 |
| | 复审 round2 | finding-A 跨 tick 单飞补强 / finding-B 系统 Task 泄漏用户面 | 2 全修 |
| | 复审 round3 | finding-C 状态扫描未排除系统 Task / finding-D 配置键名左边界未锚定 | 2 全修 |
| | 复审 round4 | finding-E 后台巩固 Task 完成/失败误推用户通知 | 修 |
| | 复审 round5 | finding-F 配置布尔右边界 / finding-G 多行 HTML 注释块剥离 | 2 全修 |
| **C+D**（发现端+审批） | codex 环境断链（OAuth/MCP AuthorizationRequired） | → **Opus 自审为主审闸**：抓 subject_key 碰撞优雅回滚 + C4 红线静态证明 | 修 + 测试 |
| | 第二轮 codex（恢复后补跑，2026-07-03） | **[P1-1]** 敏感性按目标 partition 众数算，LLM 混组降级明文 / **[P1-2]** 敏感 MERGE commit 走 `_safe_sor_content` 毁内容 / **[P2]** pending 期间源过期仍 commit 旧提议 | v0.1 收窄+三层防御+新鲜度验证+CONFLICT 终态（4 commits） |
| | 复审 round2 | [P2] 幂等账本黑名单式判重吞掉 CONFLICT 后重新提议（恢复主流程断裂） | 改白名单 + 完整恢复链 e2e |
| | 复审 round3 | [P2] 验证步自身异常发生在 claim 后候选卡死 APPLYING | try/except 走回滚路径 + 语义二分（判定失败 vs 自身异常）+ 注入故障测试 |
| | 复审 round4 | **0 finding 闭环** | — |
| **E+F**（通知+e2e） | codex review --base b4477ff1 | [P2] Web-only 用户待审通知服务端死角（session_id=None 不落 /api/notifications） | session_id="" 全局通知桶（路由契约内）+ 范围外归档（§7.4/§7.5） |

合计：**2 P1 + 多 P2 + A-G 系列，全部闭环或带理由归档，0 HIGH（P1）残留**。教训与 F099 一致：**每次修复 commit 后复跑 review，迭代到 0 finding 才收敛**（C/D 域 3+2 finding 花了 4 轮）。

## 6. 测试与回归

- **F127 全套**：**192 passed**（11 个测试文件：trigger 1083 行 / review 651 / approval 1013 / config / notify / e2e / events / models / store / routes API / capability_pack_phase_d；spec Status 历史口径 182 未含 capability_pack_phase_d 的 10 个用例）。Phase H 期间复跑确认全绿（4.08s）。
- **gateway 全量**：2267 passed vs baseline 2257（+10 新测试；唯一 failed=test_plugin_watcher **pre-existing**，baseline 单跑同 fail，F106 域与 F127 无关）。
- **memory+core**：677 passed。
- **e2e_smoke**：8/8。
- **ruff**：8 findings 全 pre-existing（enums.py E501×7 系 F124/F126 注释行 + memory/__init__ I001，baseline 同报，不越范围修）。
- **G-lite（真 LLM）**：6/6 PASS（§4.4）。

## 7. 已知 limitations（诚实归档）

1. **残余 TOCTOU**：`_verify_sources_for_commit` 验证与 commit 间存在 await 点——不动 write_service 事务边界无法消除；兜底=MERGE 源 SUPERSEDED 软删可回滚（FR-C5）。
2. **敏感事实占窗口名额**：敏感过滤在 SQL limit 之后（50 条里 10 条敏感则本次只回顾 40 条非敏感）——窗口本就是 best-effort 截断，下次运行补上；不为此把排除下沉共享 `search_sor` 面。
3. **敏感分区合并 v0.2**：HEALTH/FINANCE 事实合并需 vault-aware MERGE（MERGE 分支支持 persist_vault 建 vault 存原文 + SOR 存脱敏引用）后才能放开。
4. **SSE 隐藏任务订阅 + 前端候选审批 UI 未建**（归 F101 / 前端 follow-up）：Web SSE 订不到隐藏系统任务的通知事件；前端无巩固候选审批页（当前经 REST API + 全局通知桶 `session_id=""` 发现候选）。
5. **F102 session_id=None 同缺口**：daily_routine 通知同样不落 Web 收件箱的问题在 F102 侧仍存在（F127 只修了自己这侧），同归 F101 / 前端 follow-up。
6. **AC-8 剩余部分归 M7**：recall 改善量化（accept 后 recall 返回单条权威事实）+ 记忆巩固域 OctoBench task + 强 model 评估 → M7 统一强 model OctoBench 方案。
7. **G-lite 延迟观察**：DeepSeek-V3.2 单次发现调用 15.6–49.8s（含长 rationale 输出）——深夜后台场景可接受；若未来接近 provider timeout（120s）需截 rationale 输出预算。

## 8. living-docs 漂移闸

**已同步（本 Feature 内）**：

| 文档 | 同步内容 |
|------|---------|
| `docs/blueprint/core-design.md` | 新增 §8.7.6 Sleep-Time Memory Consolidation（触发/发现/审批/事件/通知/deferred 全景） |
| `docs/blueprint/api-and-protocol.md` | 新增 §10.8 Consolidation Candidates API + §10.6 EventType 表加 F127 行（表头改 F084-F127） |
| `docs/blueprint/milestones.md` | M7 F127 行 ✅ 完成 + v0.1 实际范围 + G-lite 结果 |
| `docs/codebase-architecture/modules/05-memory-and-protocol.md` | 新增 §3.3 睡眠时巩固层（memory 包内新增两件 + "写管道新调用方"定位 + CONFLICT 语义） |

**已知 drift（不在本 Feature 改）**：

- `CLAUDE.local.md` M7 战略规划表 F127 行状态——不在版本管理，主 session 用户侧更新。
- `docs/blueprint/module-design.md` Memory 模块小节未提巩固层——core-design §8.7.6 已是权威落点，module-design 是模块清单粒度（F094/F096 粒度亦未逐 Feature 展开），暂不加避免双写漂移。
- `docs/codebase-architecture/modules/02-gateway-runtime-and-control-plane.md` 未列 3 个 consolidation gateway service——该文档聚焦 runtime/control_plane 主链，routine 类 service（daily_routine 同样未列）不在其叙事线；05 号文档 §3.3 已交叉引用 gateway 文件。

## 9. 制品清单

- `spec.md`（Status 行全程演进归档）/ `plan.md`
- `glite/run_glite.py` + `glite/result.md`（真 LLM 验证脚手架，可复跑）
- `completion-report.md`（本文件）
- `handoff.md`（给 F111 Behavior Compactor 的交接）
