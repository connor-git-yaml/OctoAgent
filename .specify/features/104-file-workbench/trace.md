# F104 文件工作台 v0.1（diff 视图）— Trace

> 模式：spec-driver feature（完整 10 阶段编排）
> preset：quality-first（全 Opus）/ gate_policy：balanced
> 基线：origin/master da947ce（M6 baseline）

## 初始化

- [init] worktree `feature/104-file-workbench-v01` @ da947ce（EnterWorktree 进入）
- [init] orchestrator-cli.mjs 缺 zod 依赖 → 主 session 手动编排（直接读 `config/orchestration.yaml`）
- [init] feature_dir = `.specify/features/104-file-workbench/`

## Phase 0 / 0.5（inline）

- [Phase 0] constitution_check：PASS（CLAUDE.md Constitution 10 条 + `.specify/memory/` 存在）
- [Phase 0.5] research_mode = **codebase-scan**（F104 是内部 surface Feature，无市场调研需求 → 跳过 product-research；diff 库选型为唯一在线调研点候选）

## 块 A 实测侦察（= Phase 1b tech-research 强化）

- [块A] 方法：主 session 主导 + 2 Explore 子代理并行（backend artifact/snapshot + frontend Workbench）+ 主 session 亲自核实 `artifact_store.py`
- [块A] 产物：`research/tech-research.md`
- [块A] 核心结论：
  - artifact 旧版本**不可取**（put_artifact INSERT-only / ULID PK / version 静态计数器）→ 确认 handoff §6 纠偏，F104 必须动 backend
  - artifact **无逻辑文件概念**（每次 put = 新 ULID 独立行）→ 需先定义"逻辑文件身份"
  - SnapshotStore **无 diff/history**（仅 prefix-cache 冻结快照）→ 不可复用
  - 前端 React 19 + 纯手工 CSS，**无 diff 库**；Files Tab 挂载点明确（App.tsx + WorkbenchLayout）
- [决策] 版本历史存储方案（spec 第一决策点）：用户拍板 **方案 A**（append-only `artifact_versions` 表，逻辑文件 key=(task_id,name)）
- [决策] diff 渲染：用户拍板 **jsdiff + 自建 CSS**（契合纯手工 CSS + 非技术用户 UX）
- [待澄清] artifact 版本数据来源（同一逻辑文件多版本是否有真实写入场景）→ specify 阶段实证

## Phase 2 specify（进行中）

- [实证] 版本数据来源专项侦察 → 确认有真实多版本源（progress-note/tool_output/llm-response，测试证实）；behavior 文件不走 artifact（F104 外）；暴露 UX 设计点（技术性 artifact 不应平铺给非技术用户）
- [Phase 2] 委托 spec-driver:specify（opus）起草 spec.md，含详尽上下文注入（方案 A + jsdiff + 侦察事实 + UX 设计点 + 范围 + Constitution/H1H2H3 约束）
- [Phase 2] spec.md 产出：5 User Story（P1×3/P2×1/P3×1）+ 20 FR + 6 SC + 2 Key Entity + 6 Edge Case + 1 CL，复杂度 MEDIUM
- [Phase 2] 主 session 审查发现 3 个设计 gap → 注入 clarify：
  - CL-2 Files Tab 入口与 task 上下文（全局 NavLink vs key 含 task_id）
  - CL-3 版本内容存储形态（A1 指针 vs A2 独立副本；旧 artifact 行实测未物理删除，FR-001/005/010 有张力）
  - CL-4 版本号并发分配竞态（SD-2 未提并发模型）

## Phase 3 clarify + checklist（并行 DESIGN_PREP_GROUP）

- [Phase 3] 委托 clarify（整理 CL-1~CL-4 + 推荐）+ checklist（spec 质量清单）并行
- [Phase 3] clarify → clarifications.md（CL-1~4，含代码核实纠正 progress_note 合并 inert）；checklist → quality-checklist.md（41 项 ✅37/⚠️4/❌0）
- [Phase 3] 主 session 审查：CL-3 对抗复核（一度倾向 A1，被 session_delete 物理删证据 + Constitution#2 说服 A2 + 大文件指针/删task级联细化）
- [GATE_DESIGN] 硬门禁通过：CL-1 完全隐藏 / CL-2 先选 task 两级导航 / CL-3 A2 独立副本（用户拍板）+ CL-4 自决
- [Phase 3] spec.md 决议固化：§3.4 + SD-7/SD-8 + Status=Approved + 8 处 FR/section 更新 + §9 清空
- [Phase 3] 启动 Codex adversarial review（background, id bof8ey7hh）审 spec 设计（commit 前强制，working-tree untracked 文档）
- [Phase 3] review 跑期间预读 plan backend 上下文（只读，不阻塞）：
  - sqlite_init.py migration 模式：新表用 `CREATE TABLE IF NOT EXISTS` DDL 常量 + init 注册 → artifact_versions 纯新增表天然 0-regression（老库自动建空表）
  - session_delete.py 级联事务：CL-3 接入点 = line 83-85（delete_artifacts）邻接加 `delete_artifact_versions_by_task_ids`，事务内（line 96 commit 前）；A2 大文件指针指向 artifacts storage_ref（已被 collect_storage_refs 清理覆盖），版本表只需删行
- [Phase 3] Codex review 完成（verdict needs-attention，4 finding 全部核实成立）：
  - [critical] A2 大文件指针破坏"独立副本"承诺（session_delete unlink storage_ref → 大文件历史不独立，SC-002 对 storage_ref 不成立）→ 自决修：正名混合方案 + 明确 SC-002 仅小文件 100%
  - [high] (task_id,name) 误判技术审计产物为同一文件（llm-response 每轮/tool_output 每次/chat-import 全局 task+文件名，跨 scope 暴露风险）→ ★ 动摇数据源根基 → 问用户拍板数据源边界
  - [high] MAX(version_no)+1 无强唯一防线（async coroutine 在 read MAX 与 INSERT 间有 await 点 → 重复版本号）→ 自决修：UNIQUE 升 MUST + 冲突重试 + BEGIN IMMEDIATE/async lock + 并发测试
  - [medium] 版本 append 失败策略未定义（同事务回滚 vs 吞错）→ 自决修：同事务原子 + 迁移 fail-fast + 失败可观测 + DB locked/missing table 回归测试
- [Phase 3] finding 2 用户拍板：选项 1 显式 versionable 标记（v0.1 仅 progress-note，排除 llm/tool/chat-import）
- [Phase 3] spec.md 4 finding 全闭环（17+ 处 Edit）：§0 事实 + §3.2 SD-1/2/3 + §3.4 SD-8 混合 + §3.5 SD-9/SD-10 + FR-001/002/005/010/021/022 + Key Entities + SC-002 + §8 + §10 Codex 闭环表
- [Phase 3] Codex re-review round 2 完成（needs-attention，3 finding 全部修复遗留的一致性 gap）：
  - [high] logical_file_id 可空+回退 name → 升强约束 MUST 非空、删回退
  - [high] US4 tool_output 友好命名验收与 SD-9 排除矛盾不可达 → 收窄 progress-note + 补负向 AC
  - [medium] SC-006/Edge Cases 占位备选与 CL-1 冲突 → 删占位
- [Phase 3] round 2 修复完成 + 同步过期文档 + 全文 grep 核查通过
- [Phase 3] Codex re-review round 3 完成（needs-attention，2 finding，收敛趋势 4→3→2）：
  - [high] tech-research.md 漏标过期（§4/§7 仍写旧模型）→ 加 SUPERSEDED banner + §4/§7 标注
  - [medium] SD-9 未排除内部 progress-note:__merged_history__ 合并汇总 → SD-9 排除行 + FR-022 + Story4 AC-4 负向
- [Phase 3] round 3 修复完成（文档同步 + allowlist 边界）
- [Phase 3] Codex re-review round 4（needs-attention，2 残留旧措辞）：
  - [high] §1.1/§1.2/Story3 旧措辞（工具多次调用演进/每次 artifact 写入）→ 统一改 versionable 措辞
  - [medium] checklist §6.1/§7/§11 旧 ✅/⚠️ 断言 → 重写指向新模型 + 标已闭环
- [Phase 3] round 4 修复完成 + grep 复查（Codex rg 清单）：spec.md 主事实源 0 残留；辅助文档 banner 覆盖
- [Phase 3] **Codex re-review round 5 = APPROVE ✅**（聚焦 spec.md，No material findings）。5 轮收敛：4→3→2→2→0 finding，共 11 finding 全闭环
- [GATE: spec 定稿] spec 阶段完成 → commit spec 制品 b65ed17（纯文档，SKIP_E2E）

## Phase 4 plan + Phase 5 tasks

- [Phase 4] 委托 spec-driver:plan（opus）→ plan.md（5 Phase + schema + 事务边界决策 + 测试分层 + 风险）
- [Phase 4] plan 子代理实测纠正：①put_artifact 不自 commit（FR-021 同事务天然满足）→ SD-2 BEGIN IMMEDIATE 与隐式事务冲突风险（标 Phase 1 HIGH 必实测 + Codex review 重点）；②API 鉴权实际是路由级 require_front_door_access（非 handler Bearer）
- [Phase 4] 主 session 审查 plan：忠实 spec + Constitution 10 条核查 + 事务边界 HIGH 处理得当；小发现 FR-020 MAY 未注明跳过（让 tasks 注明）
- [Phase 5] tasks.md 产出（38 task / 5 Phase，FR 21/22 + FR-020 deferred，SC 6/6，FR×Task 矩阵 = analyze 一致性核心）
- [Phase 4-5] plan+tasks Codex review（needs-attention，1C+2H，聚焦单连接事务正确性）：
  - [critical] BEGIN IMMEDIATE put_artifact 开/调用方 commit 在单连接 async 下不可靠（事务连接级跨 await，mixed-writer 污染）
  - [high] UNIQUE 重试无 SAVEPOINT 粒度（冲突 rollback 撤主表 / 不 rollback 下轮 BEGIN 失败）
  - [high] SD-10 失败回滚给不 rollback 的调用方（脏事务 + 失败事件被回滚吞）
- [Phase 4-5] 修复采纳 Codex 核心：versionable 自包含事务 + SAVEPOINT 重试 + durable 失败信号
- [Phase 4-5] plan+tasks re-review round 2（needs-attention，1C+2H 修复遗留矛盾/缺口）：
  - [critical] plan §0.2/§0 旧"调用方 commit"与 §1.2 自包含矛盾 + 逻辑顺序错 → 消除旧文案 + §1.2 改两互斥分支（先校验后事务，主表 INSERT 在事务内）
  - [high] durable 事件无 emit 路径（artifact_store 无 event_store）→ 实测 StoreGroup 持 event_store 共享 conn + append_event_committed 独立提交 → 注入 wiring + events 表断言
  - [high] T5.1 mixed-writer 测试与范围取舍矛盾 → 条件化（依 T1.3 硬 gate：顺序→串行化不变量+合成交错 xfail；并发→升级后测互不污染）
- [Phase 4-5] round 2 修复完成（plan §0.2/§0/§1.2 + §4 + tasks T1.3/T1.4/T1.7/T5.1/T5.4 + 文件索引）
- [Phase 4-5] round 3 re-review：codex 挂起卡住（输出仅 3 行 Turn started）→ 主 session 自查收敛（grep 无矛盾残留，line 40 修正语境；新事务模型 19 处一致贯穿）
- [GATE_TASKS] 硬门禁通过（用户拍板）：✅ 批准进 implement + ✅ mixed-writer = 实测驱动（T1.3 硬 gate）
- [GATE_TASKS] commit plan+tasks 制品 → implement（plan Phase 1 起）

## Implement（plan Phase 1-5）
