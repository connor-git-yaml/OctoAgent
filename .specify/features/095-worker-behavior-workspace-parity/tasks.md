# F095 Worker Behavior Workspace Parity — Tasks

> 上游：spec.md v0.2 + plan.md v0.2 + Codex review #1 闭环
> 顺序：A → B → C → D → E（每 Phase 完结后必跑 e2e_smoke + Codex per-Phase review）

## Phase 0 — 实测前置（开工前必做，不写代码）

- [ ] **T0.1**：跑 e2e_smoke baseline（记录 PASS 数量与耗时）
- [ ] **T0.2**：grep `shared_file_ids` 全消费者，记录 contract audit 结果（plan §0.7）
- [ ] **T0.3**：grep `EventStore.record_event` / `events/` 模块，确定 sink 接口签名（plan §0.8）
- [ ] **T0.4**：grep `BehaviorPack` 当前 pack_id / hash 字段是否存在；决策生成策略 + 类型（plan §0.9）
- [ ] **T0.5**：grep Worker AgentProfile 创建入口，列出所有路径（plan §0.6）
- [ ] **T0.6**：commit 设计文档（spec/plan/codex-review-spec-plan/trace/tasks）作为 docs commit

## Phase A — envelope 双过滤收敛 + IDENTITY 修复

- [ ] **TA.1**：修 `build_behavior_slice_envelope` 移除 `share_with_workers AND` 子句（agent_decision.py:329）
- [ ] **TA.2**：函数 docstring 更新，显式说明 `shared_file_ids` 字段语义变更
- [ ] **TA.3**：新增 `TestBehaviorSliceEnvelope` 类（Phase A 部分 5 个测试）：
  - IDENTITY.md 进 Worker envelope
  - share_with_workers=False 不再剥离白名单文件
  - 主 Agent FULL profile envelope 行为零变更
  - private_file_count 计算正确
  - shared_file_ids 字段语义为 "profile 白名单内文件 ID 列表"
- [ ] **TA.4**：AC-2b 加 prompt 拼接顺序 / delegation additional_instructions 与 IDENTITY.worker 优先级测试
- [ ] **TA.5**：跑 `pytest packages/core/tests/test_behavior_workspace.py` 验证 PASS 数 ≥ baseline 53
- [ ] **TA.6**：跑 e2e_smoke
- [ ] **TA.7**：跑全量回归 vs baseline 0 regression
- [ ] **TA.8**：Codex per-Phase review（foreground，spec/plan 闭环 + Phase A diff）
- [ ] **TA.9**：commit Phase A（含 review 闭环说明）

## Phase B — Worker 私有模板（SOUL.worker.md / HEARTBEAT.worker.md）

- [ ] **TB.1**：起草 `SOUL.worker.md`（≤30 行，spec §6.4 哲学守护原则）
- [ ] **TB.2**：起草 `HEARTBEAT.worker.md`（≤40 行，A2A 回报对象改主 Agent）
- [ ] **TB.3**：模板内容 commit 前 Codex 单点 review（哲学守护）
- [ ] **TB.4**：扩 `_BEHAVIOR_TEMPLATE_VARIANTS`：加 `("SOUL.md", True)` / `("HEARTBEAT.md", True)` 条目（共 3 个 worker variant）
- [ ] **TB.5**：新增 `TestWorkerVariantTemplates` 类（4 个测试）：
  - is_worker_profile=True 时 SOUL/HEARTBEAT/IDENTITY 派发 worker variant
  - is_worker_profile=False 时派发主 variant
  - 渲染断言：worker variant 不漏 placeholder
  - worker variant 内容含"主 Agent" / "A2A" 或同义词（哲学守护断言）
- [ ] **TB.6**：新增 `TestWorkerWorkspaceFilesInit`（2 个测试）：worker profile / main profile 派发对比
- [ ] **TB.7**：跑 `pytest` + e2e_smoke + 全量回归
- [ ] **TB.8**：Codex per-Phase review
- [ ] **TB.9**：commit Phase B

## Phase C — Worker allowlist 扩展 + USER 扩入

- [ ] **TC.1**：`_PROFILE_ALLOWLIST[WORKER]` 改为 8 项（去 BOOTSTRAP 加 USER + SOUL + HEARTBEAT）
- [ ] **TC.2**：更新文档串
- [ ] **TC.3**：修老断言（`test_worker_profile_includes_5_files` → `_includes_8_files`，excluded 集只剩 BOOTSTRAP）
- [ ] **TC.4**：扩 `TestBehaviorSliceEnvelope` 类（Phase C 部分 4 个新测试）：
  - SOUL/HEARTBEAT/USER 在 Worker envelope 内（v0.2 关键变更）
  - BOOTSTRAP 不在 Worker envelope 内
  - Worker 加载 8 文件覆盖 5 个 layer（ROLE/COMMUNICATION/SOLVING/TOOL_BOUNDARY/BOOTSTRAP）
  - SOUL 进 envelope 时 content 来自 SOUL.worker.md（用 marker 区分）
- [ ] **TC.5**：新增 1 个 `delegate_task → Worker workspace 初始化 → Worker LLM 决策环加载 8 文件` 端到端集成测，覆盖 §0.6 至少一个真实 Worker 创建入口
- [ ] **TC.6**：跑 `pytest` + e2e_smoke + 全量回归
- [ ] **TC.7**：Codex per-Phase review
- [ ] **TC.8**：commit Phase C

## Phase D — BEHAVIOR_PACK_LOADED 事件 + BehaviorPack.pack_id

- [ ] **TD.1**：BehaviorPack model 增 `pack_id: str` 字段，三条构造点填充（filesystem / metadata raw_pack / default fallback）
- [ ] **TD.2**：实施 §0.9 决策的 pack_id 生成策略（推荐 hash(profile_id + load_profile + source_chain + sha256(layers))）
- [ ] **TD.3**：grep 现有 BehaviorPack 引用，确认 pack_id schema 兼容（无 SQL 字段冲突）
- [ ] **TD.4**：新增 `BEHAVIOR_PACK_LOADED` event payload schema（10 个字段，pydantic BaseModel）
- [ ] **TD.5**：在 `resolve_behavior_pack` 三条 cache miss 路径插 emit；cached 路径不 emit
- [ ] **TD.6**：sink 用 EventStore.record_event（§0.8 决定的接口）
- [ ] **TD.7**：单测（5+ 个）：
  - 单次 dispatch emit 一次
  - cache hit 不 emit（重复 resolve 同 key）
  - 三条 miss 路径 pack_source 字段正确
  - payload 字段完整性
  - pack_id 同 input → 同 pack_id；不同 input → 不同 pack_id
- [ ] **TD.8**：集成测：Worker dispatch emit 一次 BEHAVIOR_PACK_LOADED 含 agent_kind="worker"
- [ ] **TD.9**：跑 e2e_smoke + 全量回归
- [ ] **TD.10**：Codex per-Phase review
- [ ] **TD.11**：commit Phase D

## Phase E — Final + rebase F094 + AC-7b 集成测

- [ ] **TE.1**：等 F094 合入 master（监控 origin/master）
- [ ] **TE.2**：`git fetch origin master && git rebase origin/master` 在 F095 worktree
- [ ] **TE.3**：实施 AC-7b 集成测：Worker dispatch 端到端断言双 agent_id 一致性（F095 BEHAVIOR_PACK_LOADED + F094 RecallFrame）
- [ ] **TE.4**：跑全量回归 vs F094 完成后的新 master 0 regression
- [ ] **TE.5**：Final cross-Phase Codex review（输入 plan + 5 Phase commit diff + AC-7b 结果）
- [ ] **TE.6**：处理 Final review finding 闭环
- [ ] **TE.7**：blueprint.md grep 审计；同步或显式说明无相关章节
- [ ] **TE.8**：harness-and-context.md 同步 worker behavior 章节
- [ ] **TE.9**：CLAUDE.local.md M5 阶段 1 表格 F095 行更新（完成日期 + 测试数 + Codex finding 总闭环）
- [ ] **TE.10**：产出 completion-report.md（实际 vs 计划 + Codex finding 闭环表 + F094 协同结果）
- [ ] **TE.11**：产出 handoff.md（F096 接口点）
- [ ] **TE.12**：commit Phase E（docs + final）
- [ ] **TE.13**：归总报告给主 session（**不主动 push origin/master**）

## 全局验收（每个 Phase 结束都核对，Phase E 之后总体核对）

- [ ] 全量回归 0 regression vs F093 baseline (284f74d) 或 rebase 后的 F094-merged baseline
- [ ] e2e_smoke 每 Phase 后 PASS
- [ ] 每 Phase Codex review 闭环（0 high 残留）
- [ ] Final cross-Phase Codex review 通过
- [ ] **Final 阶段已 rebase F094 完成的 master 后跑全量回归**
- [ ] completion-report.md 已产出（实际 vs 计划 + Codex finding 闭环 + F094 协同）
- [ ] F096 接口点说明（handoff.md：BEHAVIOR_PACK_LOADED + pack_id 引用方式 + USER.md 决策与 worker memory 关系）
- [ ] Phase 跳过显式归档（若有）
- [ ] 文档同步（harness-and-context.md / CLAUDE.local.md / blueprint.md 审计）

## 不在 tasks 范围（明确排除）

- BEHAVIOR_PACK_USED 事件（F096）
- WorkerProfile 完全合并 AgentProfile（F107）
- share_with_workers 字段彻底删除（F107）
- packages/memory/ 改动（F094）
- RecallFrame agent_id schema（F094）
- D6 agent_context.py 拆分（F093 已做）
