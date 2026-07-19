# F145 统一候选审批中心 — Spec（收窄版 v1.0）

> M10 首波 Feature（M，前端为主）。上游：`docs/blueprint/milestones.md` M10 表 F145 行 +
> `CLAUDE.local.md` §M10。baseline：master `5311e250`。
>
> **一句话**：把散在三处的后台提议（memory 候选 / F127 记忆合并提议 / F111 规则精简提议）
> 收进一个「审批中心」页——每晚后台产的提议，早上 Web/手机划一划批完。

## 1. 问题与目标

三个后台提议源都已有完整后端审批 REST，但用户面残缺：

| 源 | 表 | REST（已确认，2026-07-19 实测 grep） | 现有前端 |
|----|----|----|----|
| memory 候选（F084） | `observation_candidates` | `GET /api/memory/candidates` / `POST .../{id}/promote`（可带 fact_content=edit+accept） / `POST .../{id}/discard` / `PUT .../bulk_discard` | ✅ `MemoryCandidates.tsx` 页 + 「记忆」nav 红点 badge |
| F127 记忆合并提议 | `consolidation_candidates` | `GET /api/consolidation/candidates` / `POST .../{id}/accept` / `POST .../{id}/reject` / `PUT .../bulk_reject`；accept 失败三态 not_found→404 / conflict→409（终态） / pending→409（可重试） | ❌ 仅 REST |
| F111 规则精简提议 | `behavior_compact_candidates` | `GET /api/behavior/compact/candidates`（item 含服务端 unified `diff`） / `POST .../{id}/accept` / `POST .../{id}/reject`；同款三态映射；**无 bulk 端点** | ❌ 仅 REST |

目标（M10 体验核心）：**一个入口、一眼看全、一键批完**。零改后端审批语义。

## 2. 范围边界（红线）

- **零改三源审批语义**：accept/reject/promote/discard 的服务层、状态机、事件 emit 一概不碰。
- **后端只允许薄读扩展**（本 spec 只批准两处，见 §4）。
- **不动** `api/client.ts` SSE 鉴权段（F134 地盘）/ gateway `services/`、`packages/core` 读写路径（F146 地盘）/ front_door。
- **显式范围外**：工具调用实时审批（ApprovalGate，`/api/approvals`）——那是对话内秒级审批（SSE + chat 内按钮 + Telegram 按钮），生命周期与「隔夜提议」完全不同，不进本页。`/api/approvals` URL 命名空间已被它占用，本 Feature 的新端点避开。
- **显式范围外**：F127 v0.2 的候选审批 Telegram 深链、通知点击直达（通知文案已有，深链另立）。

## 3. UX 结构决策（岔路已定，理由随附）

### D1 合流列表 vs 三 tab → **单页三分组（section），非 tab、非混排**

- 三源动作语义不同（memory 有「编辑后接受」；F111 的 diff 是核心决策材料；F127 是破坏性合并），
  混排单列表需要每张卡自解释类型，认知负担反而高。
- 分组给全局概览：早上打开一眼「3 条新记忆 + 1 条记忆合并 + 1 条规则精简」。
- tab 藏内容、多一步交互，与「划一划批完」冲突；单用户 nightly 量级（F111 ≤3 提议/晚）不需要 tab 分流。
- 空 section 隐藏；三源全空 → 单一 empty state（「暂无待处理的提议」）。
- 每个 section 自带批量操作（memory=bulk_discard、F127=bulk_reject；F111 无 bulk 端点，不伪造）。

### D2 导航与路由 → 新「审批」nav 项 + `/approvals` 路由；旧路由 redirect

- 新 nav 项「审批」（描述「确认 Agent 的后台提议」），badge = 三源 pending 合计（红点数字）。
- 路由 `/approvals`；`/memory/candidates` → `<Navigate to="/approvals" replace />`（兼容旧收藏/肌肉记忆）。
- 「记忆」nav 项上的旧 badge **移除**（避免同一事项双红点；规则精简本就不属于「记忆」概念）。
- 旧 `MemoryCandidatesPage` 被吸收进审批中心（memory section 复用其交互与组件），页面与
  domain 目录删除，不留死代码（CandidateCard / BatchRejectButton 组件保留复用）。

### D3 非技术用户呈现（CLAUDE.md Web UI 规范）

- 卡面只有人话：类型标签（「新记忆」/「记忆合并」/「规则精简」）、摘要、相对时间、（memory）置信度百分比。
- 技术字段（candidate_id / run_id / partition / subject_key / source_turn_id）**不上卡面**；
  diff、合并理由、来源记忆预览进 `<details>` 折叠区（「查看详情」）。
- F111 卡摘要：「建议精简 `<file_id>`：约 X 字 → 约 Y 字」+ 折叠区含 rationale + diff 渲染
  （复用 F107 `DiffLineList` 行渲染件，unified diff 文本由纯函数解析为行模型）。
- F127 卡摘要：「建议把 N 条相似记忆合并为一条」+ 合并后内容正文 + 折叠区含 rationale +
  来源记忆预览（§4-2）；`is_sensitive=true` 显示「敏感内容」标签且不展示来源预览。
- 错误呈现按结果状态映射人话（§D4），REST detail 技术文案不直接上 UI。

### D4 CONFLICT 终态呈现（F127/F111 共用映射）

accept/reject 非 2xx 时按响应 body `status` 字段映射（HTTP 409 两义，必须读 body）：

| body.status | 含义 | UI 行为 |
|---|---|---|
| `conflict` | 终态：候选已失效（F127=源记忆已变化/敏感防御；F111=文件盘上内容已漂移） | toast「这条提议在等待期间已失效，已自动关闭」+ 卡片移除（不可重试，不诱导反复点） |
| `pending` | 执行失败已回滚，可重试 | toast「处理没有成功，请稍后重试」+ 卡片保留 |
| `not_found` | 已被并发处理/不存在 | toast「这条提议已被处理」+ 卡片移除 |
| 其他/网络错 | — | toast「操作失败，请重试」+ 卡片保留 |

memory promote/discard 沿用现状（409 → toast + 保留；它无终态语义）。

### D5 badge 汇总

- 新 hook `useApprovalCenterCount`：拉 §4-1 summary 端点取 `total_pending`；监听新全局事件
  `approval-center-changed`（审批中心页任何操作成功后 dispatch）。
- 旧 `useMemoryCandidateCount` + `memory-candidates-changed` 事件删除（消费方仅 badge 一处，闭环内改名）。

## 4. 后端薄读扩展（仅两处，只读）

### 4-1 `GET /api/approval-center/summary`（新文件 `routes/approval_center.py`）

响应：`{"memory_pending": int, "consolidation_pending": int, "behavior_compact_pending": int, "total_pending": int}`。

- 三条 COUNT 查询：`observation_candidates` / `consolidation_candidates` 走 `store_group.conn`
  raw SQL（`memory_candidates.py` 路由已有 raw SQL 先例，三表同库）；`behavior_compact_candidates`
  复用已有 `store_group.behavior_compact_store.count_candidates(status=PENDING)`。
- 不为 ConsolidationStore 加 count 方法（packages/memory 是 F146 邻区，routes 层 COUNT 已够薄）。
- 注册 main.py `protected` 依赖组（与三源路由同款）。
- 为什么不让前端拉三个 list 端点算数：`/api/behavior/compact/candidates` 每次响应都为全部
  pending 候选做盘读 + difflib unified diff——badge 每次 layout mount 都拉，摘要端点把这条
  热路径省掉，且是任务书预留的「计数端点」扩展位。

### 4-2 `GET /api/consolidation/candidates` item 增列 `source_previews: list[str]`（additive）

- 动机：F127 accept 是**破坏性 MERGE**（源标 SUPERSEDED），现列表只有 `source_count`，用户
  看不到「哪几条记忆会被合并掉」就要拍板——审批中心的核心价值是知情决策。
- 实现：对每条候选按 `source_sor_ids` 逐条 `MemoryService.get_memory(sid, layer=SOR)`（与
  `consolidation_approval._verify_sources_for_commit` 同款读法，routes 层只读），取 content
  截 200 字符；源缺失/已非 current → 该条预览为「（该记忆已变化）」占位。
- **敏感纵深对齐**：候选 `is_sensitive` 或 partition ∈ SENSITIVE_PARTITIONS、或任一源 SOR
  partition 敏感 → `source_previews` 整体为空列表（与审批端第三层防御同一判定源；此类候选
  accept 必 CONFLICT，预览无意义且不应外泄内容）。
- 附加字段向后兼容；accept/reject 处理器零触碰。单用户 nightly 量级（候选数 × 源数 ≤ 数十次
  点查）无性能顾虑。

## 5. L1 场景（做，收窄版）

**做 1 条 loopback 场景** `approval-center.spec.ts`（F140 deferred 的审批场景，现在有确定性触发器）：

- **候选注入**：L1 launcher（`serve_l1_gateway.py`）启动序列尾追加「场景③ provision」——直接
  构造一条 `BehaviorCompactCandidate` 写入 `behavior_compact_store`（source_hash 按盘上
  AGENTS.md 真实内容计算，保证 accept 不走 CONFLICT）。不重跑 discovery 链
  （`test_e2e_scripted_behavior_compact` L3 已全覆盖 discovery→候选；L1 只验「UI 点击 →
  REST → 落盘」的接线，不重复下层）。
- **薄输入**：打开 `/approvals` → 断言规则精简卡可见 → 点「接受」testid 锚点。
- **外部断言**（UI 外）：①盘上 AGENTS.md 内容 == 候选 compacted_content（文件系统通道）；
  ②`GET /api/behavior/compact/candidates` pending 归零（REST 通道）；③零真 LLM bomb 未触发
  （`assertBombNotTripped` 既有件）。
- **testid 契约**：新锚点登记 `e2e/selectors.ts` + 组件源码字面量同 commit
  （`l1SelectorsContract.test.ts` 机械校验）。锚点最小集：精简卡接受按钮 + 卡根节点。
- **降级出口**：若 launcher 注入撞上未预期的模型必填字段/hash 校验复杂度导致超预算，defer 此条
  并在 completion-report 写明（spec 显式允许，不硬塞）。

## 6. 前端结构（F143 范式对齐）

```
api/approval-center.ts                 # 新：consolidation + behavior-compact + summary 调用层
                                       #（复用 apiFetchMemory 通用 fetch，token 逻辑不重造）
domains/approval-center/
  ApprovalCenterPage.tsx               # 页主体：三源并行加载 + 分组渲染 + toast（<400 行）
  approvalModels.ts                    # 纯逻辑（L4 主战场）：unified diff 文本→行模型解析、
                                       # 结果 status→人话映射、三源 item→卡视图模型、汇总
  ProposalCard.tsx                     # F127/F111 共用卡（折叠详情 + accept/reject + busy）
  *.test.tsx / *.test.ts
pages/ApprovalCenter.tsx               # 薄壳（仿 pages/MemoryCandidates.tsx 5 行式）
hooks/useApprovalCenterCount.ts        # badge hook（替代 useMemoryCandidateCount）
```

- memory section 复用既有 `components/memory/CandidateCard` + `BatchRejectButton` 零改动。
- diff 渲染复用 `components/diff/DiffBody.tsx` 导出的 `DiffLineList`（行模型渲染件）；
  unified diff 文本解析器为 approvalModels 纯函数（服务端已产 diff 文本，前端不重跑 jsdiff）。
- **样式不进 index.css**（4477/4480 仅 3 行余量）：复用现有 `wb-*` 类为主，新样式走
  `styles/` 下新文件（复杂度闸样式层 default 700 行）或组件内 tokens inline（DiffBody 先例）。
- 删除：`pages/MemoryCandidates.tsx`、`domains/memory-candidates/`（页与测试吸收进
  approval-center）、`hooks/useMemoryCandidateCount.ts`。

## 7. 验收标准（AC ↔ test 显式绑定，SDD 强化）

| AC | 内容 | Test |
|----|------|------|
| AC-1 | `/approvals` 页三源分组渲染：三源各插 fixture → 三 section 可见、摘要为人话、技术字段不在卡面 | `domains/approval-center/ApprovalCenterPage.test.tsx` |
| AC-2 | memory 候选 accept/edit+accept/reject/批量与旧页行为等价（吸收不回归） | 同上（迁移自 `MemoryCandidatesPage.test.tsx` 等价用例） |
| AC-3 | F127 卡 accept 调 `/api/consolidation/candidates/{id}/accept`；conflict 409 → 移除 + 终态 toast；pending 409 → 保留 + 重试 toast | 同上 + `ProposalCard.test.tsx` |
| AC-4 | F111 卡 accept 调 `/api/behavior/compact/candidates/{id}/accept`；折叠区渲染 diff 行（增/删行着色模型正确） | 同上 + `approvalModels.test.ts`（diff 解析器） |
| AC-5 | badge = 三源合计；审批操作成功后经 `approval-center-changed` 刷新 | `hooks/useApprovalCenterCount.test.ts` |
| AC-6 | summary 端点三源计数正确 + 空库全 0 | `apps/gateway/tests/routes/test_approval_center_api.py` |
| AC-7 | consolidation list `source_previews`：正常源出预览（截断）、缺失源占位、敏感候选空列表；accept/reject 行为零变化 | `apps/gateway/tests/routes/test_consolidation_candidates_api.py`（扩展） |
| AC-8 | 旧路由 `/memory/candidates` redirect；「记忆」nav 无 badge、「审批」nav 有 badge | `ApprovalCenterPage.test.tsx` / `App.test.tsx` 相关断言 |
| AC-9 | （L1，收窄）注入 F111 候选 → UI 点接受 → 盘上文件已覆写 + pending 归零 + bomb 未触发 | `frontend/e2e/approval-center.spec.ts` |
| AC-10 | testid 锚点两侧同 commit 登记 | `frontend/testing/l1SelectorsContract.test.ts`（既有机械校验） |

**门禁**：后端全量 0 regression（vs 实测 baseline）+ e2e_smoke/e2e_scripted 过闸；前端 vitest 全绿 +
`check:complexity` 全过（新文件按各层默认限）+ `tsc -b` 0 错误。

## 8. 风险与对策

| 风险 | 对策 |
|------|------|
| F127 route 响应加字段破坏既有消费者断言 | 唯一消费者是路由测试（前端从未接）；additive 字段 + 跑 `test_consolidation_candidates_api.py` 确认 |
| L1 launcher 注入候选的模型/hash 细节超预算 | spec §5 显式降级出口：defer 带记录 |
| 与 F134 并行同在 frontend | 文件区不相交（F134=api/client.ts SSE 段 + front_door；本 Feature 不碰）；rebase 时人工核对 |
| index.css 余量 3 行 | 新样式一律不进 index.css（§6） |
| 三源并行加载一源失败 | 按源降级：失败 section 显示该源错误 + 重试，其余源正常可操作（Constitution #6） |
