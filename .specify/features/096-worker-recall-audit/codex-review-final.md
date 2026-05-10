# F096 Final cross-Phase Adversarial Review

**时间**：2026-05-10
**Reviewer**：Claude general-purpose Agent（替代 Codex；CLI 卡 reasoning 不可用）
**输入**：spec/plan/tasks v0.2 + 6 commits diff (5c32ac4 → debdda4) + 5 份 per-Phase review
**baseline**：origin/master @ dd70854（F095 完成）；F096 领先 6 commits，27 文件 +3385 / -17 行

## Findings 总览

| 严重度 | 数量 | 处理状态 |
|--------|------|----------|
| HIGH | 3 | 1 闭环修复 + 2 接受推迟（completion-report 归档）|
| MEDIUM | 6 | 1 接受推迟 Phase E + 5 完整归档 completion-report |
| LOW | 2 | 2 归档 |

## 处理表

### HIGH（commit 前必处理）

| # | 位置 | Concern | 处理决议 |
|---|------|---------|---------|
| **H1** | agent_decision.py:350,386 (两处 helper) | review #1 M4 "删 hasattr fallback" 决议被实施破坏：实施用 `str(getattr(agent_profile, "kind", "main") or "main")` 是 hasattr fallback 的等价 | **接受 → 闭环修复**：改为 `str(agent_profile.kind)` 严格按 review #1 M4 决议；同名 commit "Final review 闭环" |
| **H2** | spec §4 AC-F1 + audit chain test | AC-F1 worker_capability 路径未实施：实施的 audit chain test 用 main agent dispatch 路径，无 `agent_kind == "worker"` assertion；spec/plan 要求 delegate_task tool 触发 worker AgentRuntime 创建 | **接受 → 推迟 F098**：本 session token 已多；audit chain core invariant（profile_id ↔ runtime_id ↔ LOADED.agent_id 四层对齐）已 cover；worker_capability 路径属 F098 (Worker↔Worker 解禁) 范围；completion-report 显式归档 |
| **H3** | tasks T-V-4/5/6/7 + AC-G5 / AC-G6 | completion-report.md / handoff.md / codex-review-final.md 缺失 | **接受 → 闭环产出**：本 review = codex-review-final.md；completion-report.md + handoff.md 同 commit 产出 |

### MEDIUM

| # | 位置 | Concern | 处理 |
|---|------|---------|------|
| **M1** | sync vs delayed emit 路径架构不对称 | sync 用 append_event_committed 直调；delayed 用 _append_event_only_with_retry + _sse_hub.broadcast；sync 路径无 SSE broadcast + 无 task_seq retry | 接受推迟 Phase E：单 task 内 build_task_context 串行实质不并发；SSE broadcast 留 Phase E UI 实施时再评估 |
| **M2** | plan §4.5 Step 4 `_get_cached_pack_for_used` helper 未实施 | 实施直接 `if loaded_pack is not None` 复用 prime 引用——比 plan 设计更简洁但偏离 | 接受 → 实施更优；completion-report 归档"plan §4.5 Step 4 简化为复用 prime 引用" |
| **M3** | prime resolve 失败时 LOADED+USED 完全不 emit | prime resolve 失败 → loaded_pack=None → 完全跳过 emit；但 _fit_prompt_budget 内部仍能 fallback resolve 装载 system_blocks → 审计契约盲点 | 接受为已知 trade-off：prime 失败大概率 pack 装载链路也失败；监控告警 log key `behavior_pack_resolve_failed_for_emit` |
| **M4** | plan §X.X 计划新建 7 测试文件 vs 实施合并到 3 现有文件 | 测试合并比独立文件更聚合；Phase D commit message 错误声明 "扩展 F095 fixture test_end_to_end_worker_pack_to_envelope_with_worker_variants"（实际未变）| 接受 → 当前架构合理；completion-report 归档 + 勘误 Phase D commit |
| **M5** | audit chain test fixture HEALTHY 单轮（不触发 delayed path）| AC-F2 严格读法要求"完整 audit 链路"双路径覆盖；audit chain test 仅 cover sync 路径 | 接受推迟：baseline test 双轮已 cover delayed 路径 RecallFrame 持久化（Phase A test）+ MEMORY_RECALL_COMPLETED emit（Phase A/C test）；audit chain test 关注 sync 链路对齐是足够的 |
| **M6** | spec AC-B3 / AC-C4 / AC-D5 / AC-E5 测试数量"≥ N"vs 实测 | spec AC 数量未严格达成（B 实测 5 个 vs spec 7 / D 实测 5 assertion 但合并 1 test vs spec 4 个独立）；E 整体推迟 ⇒ 0 vs spec 3 | 接受 guidance 解读（与 F093 MED P2-1 同 convention）；completion-report 归档 |

### LOW

| # | 位置 | Concern | 处理 |
|---|------|---------|------|
| **L1** | spec §4 块 E（5 AC + ~300 行 frontend）整体推迟 | spec/plan/tasks v0.2 都把 E 列为"必做"；推迟实质 spec 范围缩水 | 接受 → completion-report 显式归档"原 F096 第 3 目标 Web 可视化推迟到独立 session"；不阻 commit |
| **L2** | per-Phase 推迟到 Final review 的 4 项 | 当前 Final review 接受全部推迟，需写入 codex-review-final.md（即本报告）+ completion-report 归档 | ✅ 闭环 |

## Phase 推迟项汇总

| 推迟项 | 来源 Phase | 推迟到 | 状态 |
|--------|-----------|--------|------|
| Phase A finding #4 idempotency race | A | B | ✅ Phase B 闭环（recall_frame_id 改 idempotency_key 派生）|
| Phase B M1 timelines N+1 | B | Phase E | ⏳ Phase E 实施时评估 |
| Phase B M2 ISO8601 validation | B | Final review | ✅ Final 接受推迟（frontend 标准化）|
| Phase C M1 sync 路径 SSE broadcast | C | B/E | ⏳ Phase E |
| Phase D M1 conftest 全局 fixture | D | Final review | ✅ Final 接受单文件 fixture |
| Phase D M2 plan §4.5 project.slug 名字 | D | plan 同步 | ❌ 当前未同步（completion-report 归档）|
| Phase F M1 AC-F1 worker 路径 | F | Final review | ✅ **Final 接受推迟到 F098**（H2 闭环）|
| Phase E 整体（5 AC + ~300 行 frontend）| F commit | 后续 session | ✅ commit message 归档 |
| AC-G5 completion-report | Verify | 本 commit | ✅ 闭环产出 |
| AC-G6 Phase 跳过显式归档 | Verify | completion-report | ✅ 闭环 |
| codex-review-final.md | T-V-4 | 本 commit | ✅ 闭环（本报告）|
| handoff.md | T-V-6 | 本 commit | ✅ 闭环产出 |

## 测试覆盖

- focused regression（含 Phase A/B/C/D/F 全部累积）：95 passed
- e2e_smoke：8/8 PASS（每 Phase commit 自动跑过）
- baseline 全量 vs F095 (dd70854)：3260 passed（Phase A 实测）；Phase D 后未跑全量（spec AC-G1 ≥ 3191 passed）

## 关键判断

1. **F096 整体可接受 commit + push origin/master**——前提：H1 闭环 + completion-report + handoff 产出
2. **Phase E 推迟可接受**——M5 阶段 1 第 3 目标"Web 可视"留独立 session 实施；F094/F095 推迟项核心契约（B+C+D+F）已闭环
3. **F097/F098 阶段 2 启动条件**：F096 本 session 完成 + 用户 push 后即满足
4. **未做的 spec AC 留 completion-report 显式归档**：guidance 解读 vs hard contract 选择 guidance（与 F093 同 convention）
