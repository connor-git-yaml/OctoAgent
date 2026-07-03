# F127 → F111 Handoff（Behavior Compactor，M7 同域下一棒）

**日期**: 2026-07-03
**给谁**: F111 Behavior Compactor（LLM 智能合并行为规则文件去冗余，F063 Phase 3 推迟项）——与 F127 同属"LLM 智能合并"族，但操作对象从 memory SOR 换成 behavior 文件。
**前置阅读**: [spec.md](./spec.md) §0.1（编排 vs 原语定位）+ [completion-report.md](./completion-report.md) §3（可复用件）§4（偏离）。

---

## 1. F127 建好的可复用件（F111 直接仿/复用）

### 1.1 破坏性变更人审范式（C4/C7，最核心）

F127 把"LLM 提议改用户既有数据 → 人审 → apply"走通了一整条可复制的路：

- **候选表设计**（`memory/store/consolidation_store.py` + `models/consolidation.py`）：五态 PENDING→APPLYING→APPLIED / REJECTED / **CONFLICT**；atomic claim（条件 UPDATE + rowcount CAS，源自 memory_candidates 范式）；content_hash 幂等账本（**阻断白名单** `{PENDING,APPLYING,APPLIED}`——REJECTED/CONFLICT 不阻断，允许重新提议，未来新增终态默认不阻断）。
- **accept 三段式**：claim → **commit 前验证** → commit；**失败语义二分**——验证**判定失败**（确定性：源过期/敏感）→ CONFLICT 终态 + REST 409 引导重新提议；验证**自身异常**（临时故障）→ 回滚 PENDING 可重试。F111 照抄这个语义分叉，别合并成一种失败。
- **REST 面**：`routes/consolidation_candidates.py` GET/accept/reject/bulk_reject 四端点 + 409 契约。
- **F111 差异点**：F127 源是 SOR（自带 SUPERSEDED 软删可回滚）；F111 源是 behavior 文件——**可回滚兜底直接挂 F107 W1 的 behavior 版本历史 + Two-Phase 恢复**（已在 master），不需要自建软删层。CONFLICT 验证对应物 = 提议时源文件 hash vs accept 时现 hash（behavior 文件比 SOR 更容易被用户并发编辑，CONFLICT 命中率会更高，别省这道验证）。

### 1.2 LLM 发现端模式（`gateway/services/consolidation_discovery.py`）

确定性编排组件全套可仿：`llm_client` 注入式 Protocol（测试 stub / production `ProviderRouterMessageAdapter`）→ prompt（C9：让 LLM 判冗余，**不写关键词/相似度规则**；"宁缺毋滥，合并是破坏性操作要谨慎"这句实测有效——G-lite 观察到 LLM 真的会保守拆分并给排除理由）→ `parse_llm_json_array` 复用（F065，code fence 剥离 + 正则兜底）→ **专属组校验**（id 白名单挡幻觉 + `MIN_GROUP_SOURCE_COUNT=2` 挡单源伪组 + `MAX_PROPOSALS_PER_RUN=20` 防淹没审批）→ validate-no-commit → 写候选。G-lite 实测真 LLM 会产单源"无可合并"伪组（confidence 0.0）——组校验必须有，别信 prompt 约束。

### 1.3 后台巩固编排（若 F111 也做定时后台跑）

`gateway/services/memory_consolidation.py`：cron 触发（AutomationSchedulerService）+ **合成 root Task+Work 成对** ensure（F102 audit-task 范式扩展）+ `spawn_child(target_kind="subagent", callback_mode="async")` + capacity rejected 优雅 SKIPPED + 进程内 bool 单飞（第一个 await 前 check-then-set）。注意：F111 若走同路，建**自己的** root 占位 id（别共用 `_memory_consolidation_root`），并把新 Work id 加进 `control_plane/_base.py` 的 `SYSTEM_INTERNAL_WORK_IDS`。

### 1.4 敏感/禁区单一事实源模式

F127 用 `SENSITIVE_PARTITIONS`（`octoagent.memory.enums`，与 write_service 同判定源）做三层防御（窗口排除 / any 语义拒组 / accept 最后闸）。F111 对应物 = "哪些 behavior 文件不可自动合并"（建议至少 SOUL/IDENTITY 核心人格文件默认排除）——同样定义**单一事实源常量 + 多层防御**，别散落判定。

### 1.5 配置与通知范式

- config：USER.md 机器可读字段 + **不动模板**（1800 字符预算，memory `project_user_md_template_budget`）——字段缺失全走默认，用户显式加才生效；**默认关**（改用户既有数据的功能保守默认）。解析复用 F102/F127 范式：key 左边界锚定正则（防 `previous_xxx_active` 说明性字段误匹配）+ HTML 注释行/块剥离 + 非法值 fallback + WARNING。
- 通知：仅 proposals>0 发一条 MEDIUM；0 提议/FAILED/SKIPPED 全静默；channels 复用 `summary_channels`（别新增专属字段）；quiet hours 是 discard 非延迟。

### 1.6 G-lite 真 LLM 验证脚手架（`glite/run_glite.py`）

独立脚本模式直接可仿：临时隔离 SQLite（每轮新库，不碰 ~/.octoagent 数据）+ 只借实例 provider 配置（bench alias → DeepSeek-V3.2 API key，避订阅 OAuth ToS）+ alias 重定向 wrapper + **硬断言（管道通+质量下限）/ 质量观察（不作断言）二分** + n≥3 看稳定性 + 原始响应录制归档。F111 对应植入物 = 若干条语义重复的行为规则小变体。

## 2. 踩过的坑（F111 会撞的按概率排序）

1. **events 表 PK(task_id, task_seq)**：多事件挂同一 root task，硬编码 task_seq=0 必撞 UNIQUE → 用 `append_event_committed`（MAX+1 自动重试）；且**先 commit 业务状态再 emit**——seq 冲突重试会 rollback 整个连接事务，顺序错了候选/状态跟着丢。
2. **`spawn_child` 必需真 parent 对**：签名容忍 None 但 `_launch_child_task` 硬解引用 `parent_task.thread_id/requester/...` + `parent_work.work_id`——必须合成真 Task+Work 成对，root Task 显式赋 thread_id/requester/scope_id。
3. **系统占位泄漏用户面是一族坑不是一个**（codex A+B 域 5 轮里 4 个 finding 都是它）：root Work 进委派/Worker 视图（`SYSTEM_INTERNAL_WORK_IDS` 排除）/ 系统 Task 进用户任务列表 / 状态扫描把系统 Task 当业务任务捡起 / 后台 Task 完成失败误推用户通知。新增任何系统占位对象后，**grep 全部 list/scan/notify 面逐一排除**。
4. **幂等账本必须白名单式**：黑名单式（status != REJECTED）会让新终态（CONFLICT）默认阻断，吞掉"等下次重新提议"的恢复主流程——F127 在 codex 复审 round2 才抓到。
5. **claim 后任何一步都要 try/except 走回滚**：验证步自身异常发生在 claim 后会让候选卡死 APPLYING（不在 pending 列表且 claim/CAS 全失败，无人能救）——round3 抓到。
6. **敏感/特殊内容先查 commit 路径再设计**："敏感强制人审"直觉方案在 F127 不成立（`_safe_sor_content` 会毁内容）——F111 动 behavior 文件前先确认 F107 版本历史对目标文件真覆盖（deny-list 边界），别假设。
7. **`tool_profile="minimal"` 挂不到工具**：无 builtin 工具标 minimal，free-loop subagent 做发现端需先打通 per-tool profile override（F127 spec 第一决策点，未拍板）——这是 F127 发现端做成确定性组件的理由之一；F111 想 free-loop 会撞同一堵墙，建议同样确定性组件。
8. **LLM 输出怪癖靠校验层兜不靠 prompt**：单源伪组 / 裸 JSON 无 fence / 混组——G-lite 全部实测出现过，组校验 + parse 兜底都不是过度设计。
9. **codex review 迭代到 0 finding 才算收敛**：C/D 域 3 finding 修完，复审又出 2 个新 P2（都是修复引入的边界），round4 才 0 finding。修复 commit 后必须复跑。

## 3. F127 侧未闭合项（与 F111 无依赖，但同域知悉）

- 前端候选审批 UI + SSE 隐藏任务订阅 → F101 / 前端 follow-up（F111 若也出候选，UI 需求可合并设计一次做）。
- 敏感分区 vault-aware MERGE → v0.2。
- recall 改善量化 + 记忆巩固域 OctoBench task + 强 model 评估 → M7 统一强 model OctoBench 方案（F111 的效果验证大概率也归这套方案，设计 benchmark task 时两域一起定义）。
