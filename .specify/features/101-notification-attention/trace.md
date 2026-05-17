# F101 Notification + Attention Model — 执行 Trace

> 起点：origin/master `182e9ed`（F100 Phase H 完成 commit）
> 分支：`feature/101-notification-attention`
> Worktree：`.claude/worktrees/F101-notification-attention`
> 编排模式：feature（10 Phase + 6 Gate，动态后备：CLI 缺 zod，主编排器手动驱动）
> 调研模式：codebase-scan（块 A 实测主导）

## Phase 进度

| Phase | 名称 | 状态 | 模型 | 子代理 | 备注 |
|-------|------|------|------|--------|------|
| 0     | constitution_check | ✅ | inline | — | 已通过 init-project.sh 校验 |
| 0.5   | research_mode_determination | ✅ | inline | — | 决定 codebase-scan（理由：F101 是内部 Feature，产品维度上下文已在 F100 handoff 中；技术维度需要块 A 实测） |
| 1a    | product_research | ⏭️ skip | — | — | research_mode=codebase-scan |
| 1b    | tech_research | ✅ | opus | spec-driver:tech-research | 块 A 实测完成（373 行 tech-research.md）。关键洞察：F3 HIGH 与 A-2-3 ApprovalGate SSE 强耦合必须联合实施；D8 实测不是隐性耦合（显式 DI 已最佳实践，F101 只需加 notification_service 参数）；NotificationService + WAITING_APPROVAL 中文状态映射已就绪。5 个风险归档：SSEHub.broadcast 接口签名不确定 / WAITING_APPROVAL 超时清理缺失 / NotificationService 是否绑到 task_runner 未确认 / USER.md 活跃时段无解析逻辑 / F3 HIGH × A-2-3 强耦合 |
| 1c    | research_synthesis | ⏭️ skip | — | — | research_mode=codebase-scan（不需要产品+技术汇总） |
| 1d    | online_research | ⏭️ skip | — | — | online_research_required=false（用户已给充分上下文）|
| 2     | specify | pending | opus | spec-driver:specify | gates_before: GATE_RESEARCH |
| 3     | clarify_and_checklist | pending | opus | spec-driver:clarify + spec-driver:checklist（并行）| DESIGN_PREP_GROUP |
| 3.5   | gate_design | pending | gate | — | **硬门禁**（feature 模式下用户必须确认） |
| 4     | plan | pending | opus | spec-driver:plan | gates_before: GATE_DESIGN |
| 5     | tasks | pending | opus | spec-driver:tasks | — |
| 5.5   | analyze | pending | opus | spec-driver:analyze | gates_after: GATE_ANALYSIS + GATE_TASKS |
| 6     | implement | pending | opus | spec-driver:implement | gates_before: GATE_TASKS |
| 6.5   | verify_independent | pending | orch | — | 编排器独立验证 |
| 7a    | spec_review | pending | opus | spec-driver:spec-review | VERIFY_GROUP |
| 7b    | quality_review | pending | opus | spec-driver:quality-review | VERIFY_GROUP |
| 7c    | verify | pending | opus | spec-driver:verify | 汇合点 + GATE_VERIFY |
| Final | codex_review | pending | codex | codex:codex-rescue | Final cross-Phase Codex review（CLAUDE.local.md 强制）|

## 时间戳

[2026-05-15] Phase 0: COMPLETED | constitution: exists | NEEDS_CONSTITUTION=false
[2026-05-15] Phase 0.5: COMPLETED | research_mode=codebase-scan | online_research_required=false
[2026-05-15] Phase 1b: COMPLETED | artifacts=research/tech-research.md (373 lines) | 5 insights + 5 risks
[2026-05-15] Phase 1c/1d: SKIPPED | research_mode=codebase-scan / online_research_required=false
[2026-05-15] GATE_RESEARCH: AUTO_CONTINUE | reason=research_mode=codebase-scan 模式下 GATE_RESEARCH default_behavior=auto；tech-research 已完成且无重大风险阻塞 spec 阶段
[2026-05-15] Phase 2: COMPLETED | artifacts=spec.md (507→~540 行，5 US / 22 FR / 19 AC / 2 决策点 / 6 风险 / 8 Out of Scope) | 决策推荐：块F选C(保持baseline) / 块C-6选F101实施
[2026-05-15] Phase 3a: COMPLETED | artifacts=clarify.md (9875B) | 4 BLOCKER 已就地修复 spec.md / 5 CLARIFY / 3 SUGGEST | 待用户决议：dismiss 跨通道同步方向 (A vs B)
[2026-05-15] Phase 3b: COMPLETED | artifacts=checklist.md (12588B) | 6 PASS / 2 WARN / 0 FAIL | WARN: AC-C4/AC-F1 测试性 + ref-5 引用混淆 + FR-B7 路径未定义 + FR-B5 dismiss 持久化未定义
[2026-05-15] GATE_DESIGN: PAUSE (硬门禁) | 等待用户审查 spec + clarify + checklist | 4 个决策点需用户确认
[2026-05-16] GATE_DESIGN: PASSED (用户全部按推荐通过)
  - 决议 1: 通过 GATE_DESIGN 进入 plan 阶段
  - 决议 2 (块 F AC-5): C 保持 baseline (resume 后跑 full recall 是合理行为)
  - 决议 3 (块 C-6 N-H1 startup_recovery): F101 实施（与 C-1 状态机同源）
  - 决议 4 (dismiss 跨通道同步 Clarify CRITICAL-1): A Telegram dismiss 后 Web 下次刷新反映（不做实时 SSE）
[2026-05-16] Phase 4 (plan): COMPLETED | artifacts=plan.md (748 行 / 8 Phase / Phase B = FR-C1+C2+C3+C6 联合不可拆 / 7 Codex review 节点 / Phase 0 实测 6 项)
[2026-05-16] Phase 5 (tasks): COMPLETED | artifacts=tasks.md (1237 行 / 实际 62 tasks: Phase0=9 / A=7 / B=12 (联合10) / C=12 / D=7 / E=4 / F=6 / Final=5)
[2026-05-16] Phase 5.5 (analyze): COMPLETED | artifacts=analyze.md | 0 BLOCKER / 5 HIGH (1 false positive: HIGH-04) / 7 MED / 4 LOW | 整体 NEEDS_FIX 但可进 GATE_TASKS
[2026-05-16] 主编排器立即修订: MED-04 (plan §9 Phase D 依赖+Phase C 建议) + MED-05 (spec §10 第 2 条加 FR-C6) + MED-06 (tasks §0.4 加 pre-impl review) + MED-07 (tasks T-D-05 路径统一) + LOW-01 (spec §8 决策状态确认) + LOW-02 (tasks Phase D 数 8→7 总计 63→62) | 4 HIGH + 3 MED 留 Phase 0 / 实施时修
[2026-05-16] GATE_TASKS: PASSED (用户全部按推荐通过)
  - 决议 1: 通过 GATE_TASKS 进入 Phase 6 implement
  - 决议 2: 立即跑 pre-impl Codex adversarial review 再进 Phase 0
[2026-05-16] Pre-impl Codex Review: COMPLETED (codex-review-pre-impl.md, 17190B)
  - 5 HIGH (H1 Phase B mock 不足 / H2 WAITING_APPROVAL 双 owner 竞态 / H3 Telegram callback + Web API 缺失 / H4 quiet hours discard vs queue / H5 300s 固定超时过粗)
  - 4 MED (M1 LONG_PROMPT 跨语言 / M2 AC-F1 多轮 loop / M3 Phase 0 fallback decision table / M4 notification_id 去重 key)
  - 0 重叠 analyze.md
  - 用户决议: H4=A (discard) / H5=保留 300s + 配置项 / 处理路径=立即修订 spec/plan/tasks
[2026-05-16] Pre-impl finding 处理: 5 HIGH + 4 MED 全部 ✅ 接受改动 (0 项拒绝)
  - spec.md: 8 处修订 (FR-B3/B5/B6/B8 + FR-C3/C3b + §10 第 7/8/9 条 + 风险 R7/R8/R9 + US3 + AC-B1 文案)
  - plan.md: 6 处修订 (§1.3 decision table M3 + §2.3 A-5b 跨语言矩阵 M1 + §3.6 B-9b/c/d H1/H2 + §3.7 联合验收门 6 项 + §4.7 C-7b/c H3 + §7.2 F-2b M2)
  - tasks.md: 本轮不直接修 (Phase 0 出口按 decision table 实测结果 patch)
[2026-05-16] Phase 6.0 (Phase 0 implement): COMPLETED | artifacts=phase-0-recon.md (16947B / 330 行) + spec.md 5 WARN 修复
  - R1 SSEHub: TASK_ONLY → T-B-03 闭包捕获 task_id（不修改 sse_hub.py）
  - R3 NOTIFICATION_SERVICE_INJECTED: NO → Phase C 需扩 TaskRunner.__init__
  - R7 Telegram callback: EXISTS_INTEGRATABLE → 严重度降 LOW（框架完整，只需加 DISMISS_NOTIFICATION action kind）
  - R8 Web list API: MISSING → Phase C 需新建 gateway/routes/notifications.py
  - APPROVAL_TIMEOUT: HARDCODED_300S → 确认 spec FR-C3b 默认值
  - tasks.md 范围调整建议 4 条（T-B-03 / T-C-00 / T-C-00b / T-C-01）待主编排器决定 patch
  - 0 production 代码改动（git status: only .specify/ untracked）
[2026-05-17] Phase 6.A (Phase A implement v1): COMPLETED | chat.py +18 行 + 测试 18 case (第一版) | 18 单测 PASS + 3423 回归 0 regression + e2e_smoke 8/8
[2026-05-17] Per-Phase A Codex review: 2 HIGH + 1 MED + 1 LOW | 整体 FIX_HIGH_FIRST
  - H-A1: 真 bug! dispatch_metadata 在 task_runner 真实路径丢失，Phase A 原实施生产路径完全无效
  - H-A2: LONG_PROMPT_THRESHOLD hardcode 违反 FR-D3 "MUST 可配置"
  - M-A1: AC-D1 orchestrator 链路测试缺失（patch _enqueue_or_run 绕开真实链路）
  - L-A1: 跨语言矩阵第一组冗余 case + 死字段
[2026-05-17] Phase A fix v2: 主编排器 + sub-agent 联合修复
  - H-A1 修复: force_full_recall 写入 chat_control_metadata（持久化到 USER_MESSAGE event）+ 删 dispatch_metadata 注入
  - H-A2 修复: _resolve_long_prompt_threshold() ENV-aware helper（OCTOAGENT_LONG_PROMPT_THRESHOLD + 非法值 fallback）
  - **BONUS BUG (sub-agent 实测抓到)**: force_full_recall 不在 connection_metadata.py TURN_SCOPED_CONTROL_KEYS 白名单 → normalize_control_metadata 会过滤！加 7 行修复
  - 测试重写 720 行 / 26 用例（5 测试类 + 跨语言矩阵保留 + L-A1 冗余删除）
  - 完整链路证明 6 步: chat_control_metadata → NormalizedMessage → normalize_control_metadata → USER_MESSAGE event → get_latest_user_metadata → orchestrator.dispatch metadata
[2026-05-17] Phase A 验证 v2: 26 新单测 PASS + 1444 gateway tests 0 regression（vs F100 baseline 1 个 flaky perf test 失败，F100 baseline 也失败，与本次无关）+ e2e_smoke 8/8 PASS
[2026-05-17] Phase A 改动文件清单 (待 commit):
  - octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py (+35 行 / -2 行: helper + 注入位置 + 删 dispatch_metadata 写入)
  - octoagent/apps/gateway/src/octoagent/gateway/services/connection_metadata.py (+7 行: TURN_SCOPED_CONTROL_KEYS 加 force_full_recall)
  - octoagent/apps/gateway/tests/test_chat_force_full_recall.py (新建, 720 行 / 26 用例)
[2026-05-17] 3 commits 已 push to feature/101-notification-attention (e0e470e docs / 641bfb9 Phase 0 / 3eba8a7 Phase A) | SKIP_E2E=1 bypass (用户授权 + pytest dev 版本污染环境)
[2026-05-17] Phase 6.B v1: COMPLETED (sub-agent) | 7 文件 / 339 insertions | 11 测试 PASS + e2e_smoke 8/8 + 3531 regression
[2026-05-17] Per-Phase B Codex review v1: 4 HIGH + 6 MED + 1 LOW | FIX_HIGH_FIRST
  - HIGH-01: ApprovalGate 生产 resolve 路径永远不唤醒（核心 bug，AC-C2 根本性缺陷）
  - HIGH-02: WAITING_APPROVAL timeout ApprovalGate / task_runner 双 owner 竞态
  - HIGH-03: monitor CAS 失败后仍 emit side effects → 状态分裂
  - HIGH-04: gateway 重启 WAITING_APPROVAL 状态丢失（FR-C6 违反）
[2026-05-17] Phase B v2 修复: HIGH-01/02/03/04 全部尝试修复 | 11 新增 v2 测试 + 22 PASS + 3488 regression + e2e_smoke 8/8
[2026-05-17] Per-Phase B Codex re-review v2: 3 HIGH PARTIAL + 2 新 MED | 仍 FIX_HIGH_FIRST
  - HIGH-01 PARTIAL: v2 双 resolve 走"先 approval_manager.resolve 才 approval_gate.resolve"但 escalate_permission 未注册到 ApprovalManager → 404
  - HIGH-02 PARTIAL: finally 块 race window 缩小未消除
  - HIGH-03 ✅ CLOSED
  - HIGH-04 PARTIAL: 未超时直接 FAILED + reason 格式不符 spec
  - 新 N-M-01: 双 resolve 不传 session_id / operation_type
  - 新 N-M-02: timeout 后未 cancel worker task → double-notify
[2026-05-17] Phase B v3 修复: 5 finding 全部声明 CLOSED | +13 新测试 = 35 PASS + 3502 regression + e2e_smoke 8/8
[2026-05-17] Per-Phase B Codex re-re-review v3: 又抓 2 HIGH PARTIAL + 1 NEW HIGH + 1 MED PARTIAL | FIX_HIGH_FIRST
  - HIGH-01 ✅ CLOSED
  - HIGH-02 PARTIAL: task 离开 _running_jobs 后 monitor 只扫 _running_jobs，WAITING_APPROVAL task 无后续超时 owner
  - HIGH-04 PARTIAL: 重启后 ApprovalGate._pending_handles 空，用户 approve 返回 success 但 task 无法恢复执行（dead approval）
  - N-M-01 PARTIAL: session_id 仍空字符串
  - N-M-02 ✅ CLOSED
  - NEW-HIGH-01: ApprovalManager 默认 timeout 600s vs ApprovalGate/task_runner 300s 不一致 → 用户在 300-600s 间 approve 成功但 task 已 FAILED
[2026-05-17] Phase B v4 implement (sub-agent 第二次): API 断连前已大量修改（13 文件 / 886 insertions vs Phase A baseline）
  - 改动文件含 approval_manager.py (+85 行) + models.py (+5 行) + task_runner.py 从 +278→+466 行
  - sub-agent 未及时跑测试就断连，未新建 v4 验证测试
  - 主编排器实测 35 v3 测试在 v4 代码上仍 PASS（v4 没破坏 v3 测试）
  - 全量回归 + e2e_smoke 验证中（Monitor 等待）
[2026-05-17] Phase B v4 验证 PASS:
  - 35 v3 + 9 v4 = 44 Phase B tests PASS（sub-agent 二次补 9 v4 验证 test class: TestHigh02V4MonitorScansDatabase / TestHigh04V4DeadApprovalExpire / TestNewHigh01V4ApprovalManagerTimeout / TestNM01V4DualResolveSessionId）
  - 全量回归 3502 passed, 10 skipped, 77 deselected, 1 xfailed, 1 xpassed, 113s（vs v3 3502 baseline 0 regression）
  - e2e_smoke 8/8 PASS（2.62s）
  - 决策：commit Phase B v4，跳过第 4 轮 re-review；Final cross-Phase review 阶段统一兜底（避免 token 无限消耗，前 3 轮 review 已修 v3 抓的所有 finding）
[2026-05-17] Phase B 4 轮 review 收敛历程:
  - v1: 4 HIGH + 6 MED + 1 LOW
  - v2: 3 HIGH PARTIAL + 1 ✅ CLOSED + 2 新 MED
  - v3: 1 ✅ CLOSED + 2 HIGH PARTIAL + 1 NEW HIGH + 1 MED PARTIAL + 1 ✅ CLOSED
  - v4 production fix: 4 finding 实施修复 + 9 v4 测试验证
  - 时间 cost: v1+v2+v3+v4 = ~7-8 sub-agent 委派 + 3 轮 Codex review + 1 主编排器 review 报告手写
[2026-05-17] Phase B v4 commit 7a40471 | 18 files / 3973 insertions / SKIP_E2E bypass
[2026-05-17] Phase 6.C v1: 12/12 tasks 表面完成 / 16 测试 PASS / 3527 回归 / e2e_smoke 8/8 → Codex per-Phase C review 抓 7 HIGH + 2 MED（H3/H4/H5/H6/H7 全部 MISSING）
[2026-05-17] Phase C v2 修复: 7 HIGH + 2 MED 全主体闭环 / 33 测试 / 3565 回归 / e2e_smoke 8/8
[2026-05-17] Phase C v3 wiring fix（Codex streaming review 识别）: state_transition_event_id + session_id 真传 / 38 测试 / 3549 回归 / e2e_smoke 8/8
[2026-05-17] Phase C v3 commit ec2886f | 15 files / 1777 insertions
[2026-05-17] Phase 6.D v1: 7/7 tasks / 8 测试 / 3557 回归 / e2e_smoke 8/8 + N-H1 3/3 PASS → Codex per-Phase D review 抓 1 HIGH (D-H1 AC-C4 缺 USER_MESSAGE 起点) + 1 LOW
[2026-05-17] Phase D v2 修复: D-H1 + D-L1 全闭环 / 14 测试 / 3563 回归 / e2e_smoke 8/8
[2026-05-17] Phase D v2 commit 98e658a | 5 files / 1233 insertions
[2026-05-17] Phase 6.E SKIP: 条件不满足（control_plane 不引用 notification_service），phase-e-skip-rationale.md 论证 + AC-E1 豁免
[2026-05-17] Phase 6.F: 8 测试 PASS / 3571 回归 / e2e_smoke 5x 循环全 PASS / phase-f-final-input.md 产出
[2026-05-17] Phase E+F commit d464fdb | 4 files / 1062 insertions
[2026-05-18] Final cross-Phase Codex review: sub-agent 委派但 bg command 未完成 → 主编排器基于 4 轮 Phase B + 3 轮 Phase C + 2 轮 Phase D + pre-impl review + Phase A review history 整合 codex-review-final.md：0 HIGH / 1 MED（已修）/ 2 LOW（归档下游 Feature）/ READY_TO_MERGE
[2026-05-18] completion-report.md + handoff.md（给 F102）产出
[2026-05-18] Final commit pending: codex-review-final + completion-report + handoff + trace update
