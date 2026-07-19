# F145 统一候选审批中心 — Completion Report

> 2026-07-19。分支 `feature/145-approval-center`（基于 master `5311e250`），**未 push 等拍板**。

## 1. 交付 vs 计划（Phase 对照）

| Phase | 计划 | 实际 | 偏离 |
|-------|------|------|------|
| spec/plan | 收窄 spec + Codex 评审 | ✅ `bea3cef0`；Codex review 0 finding | 无 |
| A 后端薄读 | summary 端点 + source_previews + 测试 | ✅ `24a700ed` | 无 |
| B+C 前端 | API 层 + 纯逻辑域 + 页面 + 导航 + 吸收删除 + 测试 | ✅ `23dc502d`（B/C 合一 commit，文件面天然连续） | 无实质偏离 |
| D L1 场景③ | 收窄版单场景（spec 允许 defer） | ✅ `15d2f140` **真兑现未 defer**：4 passed ×2 轮 + 单跑 986ms PASS | 正向偏离（做成了） |
| E 终门+评审+文档 | 全量回归 + 双评审 + living-docs | ✅ `57bd66e2`（Codex 2 P2 闭环）+ 本 docs commit | 无 |

## 2. AC 达成（spec §7 全表）

| AC | 状态 | 证据 |
|----|------|------|
| AC-1 三源分组渲染 | ✅ | `ApprovalCenterPage.test.tsx`（三分组/人话摘要/技术字段不上卡面）+ 浏览器实景截图验证 |
| AC-2 memory 吸收不回归 | ✅ | 迁移等价用例 3 条（accept/reject/批量）全 PASS；CandidateCard/BatchRejectButton 零改动 |
| AC-3 F127 conflict/pending 分流 | ✅ | Page + ProposalCard 测试；conflict→移除+终态 toast / pending→保留+重试 toast |
| AC-4 F111 diff 折叠渲染 | ✅ | `approvalModels.test.ts` 解析器 5 用例（含 Codex P2 内容行误吞钉住）+ data-diff-kind 着色断言 |
| AC-5 badge 合计+事件刷新 | ✅ | `useApprovalCenterCount.test.ts` 3 用例 + 浏览器实测（accept 后红点消失） |
| AC-6 summary 端点 | ✅ | `test_approval_center_api.py` 5 用例（空/三源/非 pending 排除/缺表降级/真故障 500） |
| AC-7 source_previews | ✅ | `test_consolidation_candidates_api.py` +6 用例（正常/截断/失效占位/敏感候选/敏感源/缺失）；accept/reject 既有用例零改动全 PASS |
| AC-8 redirect + badge 迁移 | ✅ | App.tsx redirect + 「记忆」badge 移除、「审批」badge 新增；全量 vitest 无回归 |
| AC-9 L1 场景③ | ✅ | `approval-center.spec.ts`：UI 点接受 → 盘上逐字节==精简版 + pending 归零 + bomb 未触发 |
| AC-10 testid 契约 | ✅ | selectors.ts + 源码属性同 commit；`l1SelectorsContract.test.ts` 10 passed |

## 3. 验证终门

- **后端全量**（worktree PYTHONPATH 锁 + `-n auto --dist=loadgroup`）：baseline `5356 passed`（改前实测）→ 改后 `5366 passed / 0 failed`（+10 = 本 Feature 新增测试），skipped/xfailed/xpassed 完全一致，**0 regression**。
- **pre-commit hook**：每 commit e2e_smoke + e2e_scripted 过闸（26-27 passed）。
- **前端**：vitest 全量 412 passed / 44 文件（改前基线 377 存量 + 本 Feature 新增 34 + P2 钉住 1，含旧页 7 用例吸收迁移）；`tsc -b` 0 错误；`check:complexity` 全过（ApprovalCenterPage 449/1200，无文件超限，index.css 未动）。
- **L1**：`npx playwright test` 4 passed ×2 轮（场景①②③全绿）；场景③单跑 PASS。
- **浏览器实景**：badge 红点 → /approvals → 折叠 diff（红删绿增）→ 点接受 → 卡移除/空态/红点消失/盘上文件精简/summary 归零，全链目视确认。

## 4. 双评审闭环

| 轮次 | 结果 | 处理 |
|------|------|------|
| Codex spec review（`bea3cef0` 后） | 0 finding | — |
| Codex final review（全分支 diff，gpt-5.4） | **0 HIGH / 2 P2 / 0 low** | 2 P2 全接受修复（`57bd66e2`）：①`parseUnifiedDiff` 误吞 hunk 后 `---`/`+++` 前缀内容行（markdown 分隔线场景，审批卡少展示真实改动）→ seenHunk 门控 + 钉住用例；②summary consolidation 计数降级过宽（DB 锁等真故障被掩成 0）→ 收窄到 `no such table`，其余 500 + 钉住用例 |
| Opus 对抗自审（8 挑战维度） | 见 §4b | 见 §4b |

### 4b Opus 对抗自审结论

（结果在归总报告附上；如有 finding 在此文档 revision 补充闭环记录。）

## 5. 关键设计决策（实施中确认/微调）

- **单页三分组而非 tab/混排**：三源动作语义不同（memory 有 edit+accept、F111 diff 是决策材料、F127 是破坏性合并），分组给全局概览且不藏内容；空分组隐藏、全空统一空态。
- **`/api/approval-center/*` 命名**：避开被工具审批 ApprovalGate 占用的 `/api/approvals`；对话内秒级工具审批显式范围外（生命周期不同）。
- **source_previews 放 list 端点而非独立 detail 端点**：F111 list 已有服务端产 diff 的先例，对称且省一跳；敏感三态（候选敏感/源敏感/失效）与审批端 `_verify_sources_for_commit` 同判定源 `SENSITIVE_PARTITIONS`。
- **失败呈现按 body.status 分流**（HTTP 409 两义）：`ApprovalActionError` 保留 resultStatus；conflict 终态移除卡片不诱导反复重试；detail 技术文案只进 console.warn 不上 UI。
- **L1 场景③直插候选而非重跑 discovery**：L3 `test_e2e_scripted_behavior_compact` 已全覆盖 discovery→候选，L1 只验「UI 点击→REST→落盘」接线；source_hash 按盘上真实内容对账保 accept 走 APPLIED。

## 6. 改动清单

**后端（+2 文件 / 2 文件扩展，零改审批语义）**
- `apps/gateway/src/octoagent/gateway/routes/approval_center.py` 新增（summary 只读端点）
- `apps/gateway/src/octoagent/gateway/routes/consolidation_candidates.py`（+`source_previews` additive 字段 + `_build_source_previews`；accept/reject 零触碰）
- `apps/gateway/src/octoagent/gateway/main.py`（import + include_router 2 处）
- 测试：`tests/routes/test_approval_center_api.py` 新增（5 用例）；`test_consolidation_candidates_api.py` +6 用例
- L1 支撑：`tests/e2e_live/l1_support/scenario_brain.py`（场景③常量 + provision）/ `serve_l1_gateway.py`（bootstrap override 注入）

**前端**
- 新增：`api/approval-center.ts` / `domains/approval-center/{ApprovalCenterPage,ProposalCard,approvalModels,index}` + 3 测试文件 / `pages/ApprovalCenter.tsx` / `hooks/useApprovalCenterCount.{ts,test.ts}` / `e2e/approval-center.spec.ts`
- 修改：`App.tsx`（路由 + redirect）/ `WorkbenchLayout.tsx`（审批 nav + badge 迁移）/ `e2e/selectors.ts` + `e2e/support.ts`（场景③契约）
- 删除（吸收）：`pages/MemoryCandidates.tsx` / `domains/memory-candidates/`（3 文件）/ `hooks/useMemoryCandidateCount.ts`

**文档**：spec/plan/completion-report + `docs/blueprint/milestones.md` F145 行 ✅ + `docs/codebase-architecture/harness-and-context.md` §6 数据流图（badge/页面/事件名更新）

## 7. 已知 limitations / deferred

- **候选审批 Telegram 深链**：通知已有，点击直达 `/approvals` 的深链未接（F127 v0.2 归档项，非本 Feature 范围）。
- **F127 折叠区来源预览不含分区人话名**：partition 原始值未上 UI（技术字段规范），来源预览以内容为主——若用户反馈需要分区上下文再评估。
- **一次性 L1 场景守卫**：外部长驻 server（非 Playwright 托管）下场景③消费后 skip（守卫先验证盘上效果仍成立）；Playwright 正常调用每 run 重启 server 全量重跑，CI 恒 fresh。
- **living-docs 漂移检查**：`docs/` 全 grep 后仅 `harness-and-context.md` 一处旧页引用，已同步；`testing-strategy.md` 等无 F145 相关漂移。
