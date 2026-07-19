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

- **后端全量**（worktree PYTHONPATH 锁 + `-n auto --dist=loadgroup`）：baseline `5356 passed`（改前实测）→ 最终 HEAD `5367 passed / 0 failed`（+11 = 本 Feature 新增后端测试；1 rerun 属既有 rerun 政策），skipped/xfailed/xpassed 完全一致，**0 regression**。
- **pre-commit hook**：每 commit e2e_smoke + e2e_scripted 过闸（26-27 passed）。
- **前端**：vitest 全量 424 passed / 45 文件（存量 377 + 本 Feature 新增 47，含旧页 7 用例吸收迁移 + Codex P2 钉住 1 + Opus MED-1 API 层 12）；`tsc -b` 0 错误；`check:complexity` 全过（ApprovalCenterPage 449/1200，无文件超限，index.css 未动）。
- **L1**：`npx playwright test` 4 passed ×2 轮（场景①②③全绿）；场景③单跑 PASS。
- **浏览器实景**：badge 红点 → /approvals → 折叠 diff（红删绿增）→ 点接受 → 卡移除/空态/红点消失/盘上文件精简/summary 归零，全链目视确认。

## 4. 双评审闭环

| 轮次 | 结果 | 处理 |
|------|------|------|
| Codex spec review（`bea3cef0` 后） | 0 finding | — |
| Codex final review（全分支 diff，gpt-5.4） | **0 HIGH / 2 P2 / 0 low** | 2 P2 全接受修复（`57bd66e2`）：①`parseUnifiedDiff` 误吞 hunk 后 `---`/`+++` 前缀内容行（markdown 分隔线场景，审批卡少展示真实改动）→ seenHunk 门控 + 钉住用例；②summary consolidation 计数降级过宽（DB 锁等真故障被掩成 0）→ 收窄到 `no such table`，其余 500 + 钉住用例 |
| Opus 对抗评审（8 维度全量交叉核验真实代码） | **0 HIGH / 1 MED / 4 LOW** | MED-1 接受已修；LOW×4 归档带理由（§4b） |
| Fable 自审席（挑战者立场独立逐维 + 物理核验） | 0 HIGH / 0 MED / 3 LOW（与 Opus LOW 高度重叠） | 见 §4b |

### 4b-0 Opus 对抗评审 finding 处理（MED-1 修 + 4 LOW 归档）

- **[MED-1] `postApproval` HTTP body→resultStatus 解析零单测** —— **接受已修**。D4 分流承重前半段（HTTP→status）此前被 Page 测试的模块级 mock 屏蔽：若解析误读字段，全部失败静默降级 unknown（用户对已终态 conflict 候选反复点接受）而套件仍绿。修复：新增 `api/approval-center.test.ts` 12 用例，stub 全局 fetch 回放后端真实 body 三态（409+conflict / 409+pending / 404+not_found / 原生 404 detail-only / 原生 500 / 未知 status 值 / 网络层失败）+ getJson/bulk 调用形态。
- **[LOW-1] claim 竞态失败复用 `conflict` 使 toast「已失效」对「已被并发成功处理」情形措辞不精确** —— 归档：后端语义（两 approval 服务 claim-fail 统一报 conflict），卡片移除行为正确、无双副作用；改文案需后端区分新 status 超「零改审批语义」红线。
- **[LOW-2] 敏感候选 `merged_content` 卡面仍显示（与 previews 置空不对称）** —— 归档（拒绝收窄带理由）：①merged_content 是 F127 REST 既有契约面（列表端点设计上就返回给用户审查），F145 只是呈现层；previews 置空针对的是 F145 **新增**的读扩展面（新面不扩大敏感暴露）；②此类候选属 shouldn't-exist 异常态且 accept 恒 CONFLICT（第三层防御），无 apply 风险；③单用户私有 UI + front-door 鉴权，展示对象即数据 owner——隐正文反而让用户无法理解这条必失效候选为何存在。若 F127 v0.2 引入敏感候选合法路径需重议。
- **[LOW-3] bulk-reject 飞行中单卡仍可操作** —— 归档：后端 CAS 保证任意交错收敛（输者 skipped/conflict），page-level busy 门为单用户罕见 race 增复杂度不值。
- **[LOW-4] 路由级原生 404（无 status 字段）兜底映射 not_found → 卡移除** —— 归档：正常契约下 approval 服务 404 恒带 status 字段，裸 404 仅在降级部署/路径错配出现；彼时移除卡片避免死循环重试，刷新页面可复现列表。

### 4b-1 Fable 自审席结论（挑战者立场独立逐维，含物理核验）

| 维度 | 结论 | 证据 |
|------|------|------|
| 审批语义是否被绕 | ✅ 无绕行路径 | summary 端点纯 `SELECT COUNT`；`_build_source_previews` 仅 `MemoryService.get_memory` 只读；L1 provision 全在 `tests/e2e_live/l1_support/`（launcher 脚本 + 测试树，production 零 import 面）；前端只调既有 REST |
| 三源状态映射失真 | ✅ 全集覆盖（物理核验） | grep 两 approval 服务全部 `status="..."` 赋值：失败态恰为 {not_found, conflict, pending}，与前端 `KNOWN_FAILURE_STATUSES` 完整对齐；FastAPI 原生 HTTPException（detail-only body，如 root-task-ensure 500）→ unknown → 通用重试文案 + 保留卡片（正确保守）；404 无 status 字段兜底映射 not_found |
| CONFLICT 终态呈现 | ✅ 与后端终态语义一致 | conflict（含双 accept 竞态被 claim 拒）→ 移除 + 「已失效」toast 不诱导重试；pending（回滚）→ 保留可重试；移除路径也 dispatch badge 刷新（服务端该候选已出 pending，计数收敛） |
| 非技术用户 UX | ✅ | 卡面零技术字段（candidate_id/run_id/partition/hash 均不上）；失败 detail 只进 console.warn；浏览器实景验证（截图：badge→分组→diff 红删绿增→接受→空态） |
| 红点 badge 计数一致性 | ✅（1 LOW 归档） | 三源操作全部经 `notifyApprovalChanged` → summary 重拉；操作是同步落库后才 dispatch 无脏读窗口 |
| L1 断言真在 UI 外 | ✅ | 三通道全部 node 上下文：REST fetch（pending 归零）/ fs readFileSync（盘上逐字节）/ bomb sentinel 文件；UI 仅 goto+visible+click 薄输入 |
| source_previews 敏感泄露 | ✅ | 候选敏感/候选分区敏感/任一源分区敏感三层全对齐 `SENSITIVE_PARTITIONS` 单一判定源；源敏感在收集中途发现时 `return []` 丢弃已收集项（构造上无先收集后泄露）；异常降级日志只含 error 类型/SQL 文本不含记忆内容 |
| 并发/双击 | ✅（1 LOW 归档） | ProposalCard busy 双按钮禁用；极端双击穿透由服务端 atomic claim 兜底（第二发 conflict→收敛移除），无双副作用 |

**自审席 3 LOW 归档（不修带理由，与 Opus LOW-1/LOW-3 主题重叠）**：
1. F127 list 端点 `pending_count=len(items)` 被 limit=200 截断，与 summary 真实 COUNT 在 >200 pending 时不一致——**存量建模**（F111 同款已修为真 COUNT，F127 未修），单用户 nightly 量级不可达；修它超「零改三源路由行为」红线颗粒度，留给 F127 v0.2。
2. 双击穿透时 toast 顺序为「成功→已失效」——服务端状态正确、无双副作用，纯呈现噪声（≈Opus LOW-1）。
3. summary 500 时 badge 静默不更新（hook catch）——badge 是辅助信号（与前身 useMemoryCandidateCount 同一取舍），页面本体有按源错误呈现兜底。

**评审席位说明**：本 Feature 三席评审 = Codex final（gpt-5.4 外部对抗，0 HIGH/2 P2 已修）+ Opus 对抗评审（独立 agent 交叉核验真实代码，0 HIGH/1 MED 已修/4 LOW 归档）+ Fable 自审席（挑战者立场独立逐维物理核验，结论与前两席交叉印证）。**0 HIGH/0 MED 残留**。

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
