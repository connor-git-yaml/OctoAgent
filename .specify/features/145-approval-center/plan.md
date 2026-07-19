# F145 Plan（实施顺序 + 验证策略）

> spec.md v1.0 的落地序。先后端薄读扩展（前端有真契约可对）→ 前端主体 → L1 收尾。
> 每 Phase 一个 commit 检查点；被杀可 `git log origin/master..HEAD` 续。

## Phase A：后端薄读扩展 + 路由测试

1. `routes/approval_center.py`：`GET /api/approval-center/summary`（三 COUNT；behavior_compact
   走既有 `count_candidates`，另两表 raw SQL via `store_group.conn`）。main.py 注册（import +
   include_router protected）。
2. `routes/consolidation_candidates.py`：list item 增 `source_previews`（读 `MemoryService.
   get_memory(sid, layer=SOR)`；截 200 字符；缺失/非 current 占位；敏感候选/敏感源 → 空列表）。
   注意 lazy import 范式与该文件既有风格一致（避免 apscheduler import 链）。
3. 测试：`tests/routes/test_approval_center_api.py`（新）+ `test_consolidation_candidates_api.py`
   扩展（AC-6 / AC-7）。跑三源路由测试全量确认零回归。
4. **hook 防御**：pre-commit 用「venv 最近 sync 树」收集——新模块名若被旧树收集会
   ImportError；新测试文件顶部 `pytest.importorskip("octoagent.gateway.routes.approval_center")`
   （`test_e2e_scripted_behavior_compact.py` 同款先例）。

## Phase B：前端 API 层 + 纯逻辑域（L4 主体）

1. `api/approval-center.ts`：`fetchConsolidationCandidates` / `acceptConsolidationCandidate` /
   `rejectConsolidationCandidate` / `bulkRejectConsolidation` / `fetchCompactCandidates` /
   `acceptCompactCandidate` / `rejectCompactCandidate` / `fetchApprovalSummary`。错误路径读
   response body 的 `status` 字段（D4 映射需要，不能只抛 HTTP status——现 `apiFetchMemory`
   丢 body，需在本模块自带「保留 body.status」的结果解析，token 逻辑仍复用 client.ts 导出件）。
2. `domains/approval-center/approvalModels.ts`：
   - `parseUnifiedDiff(text) → DiffLineRow[]`（+/-/@@/头行 → added/removed/meta 行模型；
     截断尾标记「…（diff 超长已截断）」保留为 meta 行）
   - `mapActionFailure(body|error) → {message, removeCard}`（D4 表）
   - 三源 item → 卡视图模型（人话摘要组装）
   - `sumPending(summary)` 等
3. `approvalModels.test.ts`（AC-4 解析器边界：空 diff/「（无行级差异）」文案/截断标记/混合增删）。

## Phase C：页面主体 + 组件 + 路由/导航切换

1. `ProposalCard.tsx`（F127/F111 共用：类型标签 + 摘要 + `<details>` 折叠 + accept/reject +
   busy + data-testid 锚点）。
2. `ApprovalCenterPage.tsx`（三源并行加载、按源降级、三 section、批量按钮接线、toast、
   操作成功 dispatch `approval-center-changed`）。
3. `pages/ApprovalCenter.tsx` 薄壳；App.tsx 路由 `/approvals` + 旧路由 redirect；
   WorkbenchLayout nav 项「审批」+ badge 切换（`useApprovalCenterCount`）。
4. 删除吸收件：`pages/MemoryCandidates.tsx` / `domains/memory-candidates/` /
   `hooks/useMemoryCandidateCount.ts`（CandidateCard / BatchRejectButton 保留）。
5. 测试：`ApprovalCenterPage.test.tsx`（含迁移用例，AC-1/2/3/8）+ `ProposalCard.test.tsx`
   （AC-3 细粒度）+ `useApprovalCenterCount.test.ts`（AC-5）。
6. 样式：优先复用 `wb-*`；确需新增走 `styles/approval-center.css`（不碰 index.css）。

## Phase D：L1 场景（收窄版，AC-9/10）

1. `serve_l1_gateway.py` 追加场景③ provision：bootstrap 后构造 `BehaviorCompactCandidate`
   （source_hash 按盘上 AGENTS.md 实内容计算——先读 `behavior_compact.py` 模型 +
   `behavior_compact_approval.py` 的 hash 校验确认字段）写 store。两 mode 都注入（bearer 场景
   不消费不受扰）。
2. `e2e/selectors.ts` 登记新锚点 + 组件源码属性同 commit。
3. `e2e/approval-center.spec.ts`：开 `/approvals` → 点接受 → 外部断言（文件系统 + REST +
   bomb）。
4. 撞复杂度墙 → defer 带记录（spec §5 出口），selectors 已登记锚点保留（供后补）。

## Phase E：验证终门 + 评审 + 文档

1. 后端全量：`PYTHONPATH=<六 packages src+gateway src> uv run --project octoagent --no-sync
   python -m pytest`（worktree 锁，禁 uv sync）；记录 baseline 对比 0 regression。
2. 前端：worktree `frontend/` 内 `npm ci` + `npm test` + `npm run check:complexity` +
   `npx tsc -b`。L1：`npm run test:e2e`（本地跑通）。
3. 双评审：Codex final（挑战面：审批语义是否被绕 / 三源状态映射失真 / CONFLICT 呈现 /
   非技术用户 UX / source_previews 敏感泄露）+ Opus 对抗自审 → 0 HIGH。
4. completion-report.md + living-docs：milestones.md F145 行 ✅ + 相关架构文档
   （`docs/codebase-architecture/` 前端结构文档如有）。
5. **不 push origin**，归总报告等拍板。

## 验证矩阵（分层）

| 层 | 覆盖 |
|----|------|
| L4 vitest | approvalModels 纯逻辑 / ProposalCard / Page 交互 / badge hook |
| L4 pytest | summary 端点 / source_previews 三态 / 既有三源路由零回归 |
| L3 | 既有 e2e_scripted（behavior compact 全链）不动、过闸即证审批语义未破 |
| L1 | 收窄单场景：UI 点击 → 真 REST → 真落盘外部断言 |
