# F143 UI 变薄扩 L4 — 收窄 Spec

> M9 波④，前端-only。基线 origin/master `d22378b8`（vitest 29 文件 / 204 passed / 2.78s）。
> 上游输入：qa_audit_survivors.md ui-split 审计 + milestones.md M9 F143 行 + F137 ratchet 注释。

## 0. 现状复核（2026-07-13 worktree 实读，行号以本基线为准）

| 审计线索 | 复核结果 |
|---------|---------|
| ChatWorkbench.tsx 1204 行 | **1207 行**（chip 文案修复后略涨），explicit limit 1250，ratchet 注释要求 F143 后删行回落 1200 |
| useChatStream.ts 660 行 | **659 行**，handleEvent 内联分支在 374-479，explicit limit 700，ratchet 目标 ≤500 |
| chatStreamHelpers.ts 387 行下沉先例 | 确认，387 行；含 storage/extract/sanitize/restore 工具，无专属测试 |
| ApprovalPanel 死代码 | **证据仍成立**：全 src 无目录外 import；`useApprovals` 仅被 ApprovalPanel 引用（另有 api/client.ts:256 与 client.test.ts:38 两处**注释**提及）；`/api/approve/{id}` 前端唯一调用方是 useApprovals；三文件均零 className（无 CSS 陪葬）；无专属测试文件 |
| FakeEventSource 双份 | 确认：useChatStream.test.tsx:25-73 与 TaskDetail.test.tsx:87-138 各 ~50 行；TaskDetail 版是超集（构造函数收 url + emit "message" 时也派发 onmessage）。e2e/chat-scripted-loop.spec.ts 另有 Playwright 注入版，属 L1 设施不在收敛范围 |
| MarkdownContent XSS 单测缺口 | 确认：MarkdownContent.test.tsx 仅 1 个 fenced code block 用例，零消毒断言；组件用 marked + DOMPurify(window) + afterSanitizeAttributes 钩子（A 标签强制 target=_blank + rel） |
| 3600 行纯逻辑零专属测试 | 确认（现行数）：roundSplitter 1041 / activity.ts 681 / agentManagementData.ts 592 / chatStreamHelpers 387 / approval.ts 287 / phaseClassifier 267 / workbench/utils 259 / operator/userFacing 232 ≈ 3746 行，`find` 无对应 .test 文件 |
| index.css 4477>3300 | 现 **4476 行**，explicit limit 4600。F137 注释设想"F143 UI 变薄后回收到 3300"，但 F143 五件范围内无 CSS 下沉工作、ApprovalPanel 删除不带走任何 CSS——**3300 不可在本 Feature 达成**（见 §6 偏离归档） |
| L1 锚点 | `chat-input`/`chat-send`（ChatWorkbench 两处表单）、`chat-message-assistant`/`chat-message-user`（MessageBubble）、`frontdoor-*`（FrontDoorGate）；契约测试按「src/**.tsx 源码字面存在」机械校验 |

## 1. 范围（5 件）

### 件 1：useChatStream handleEvent 分支 reducer 纯函数化

**目标**：659 → ≤500 行；事件分支逻辑成为 `(state, event) → state` 纯函数，L4 vitest 可直测事件序列。

设计（**新文件 `src/hooks/chatStreamReducer.ts`**，而非并入 chatStreamHelpers.ts）：

- **偏离说明**：任务书写"下沉到 chatStreamHelpers.ts"，但 387 + ~200（reducer）> 500 会当场击穿 hooks 目录 complexity 闸——为兑现 ratchet 而放宽 helpers 阈值是自相矛盾。取其精神（下沉到既有先例同级的纯模块），落成同目录兄弟文件。
- 导出（全部纯函数，零 DOM/网络/存储副作用）：
  - `ChatStreamEventState`：`{ messages, streaming, error, liveApproval, approvalSignal, activeAgentMessageId }`——handleEvent 触碰的全部状态原子 + 原 `activeAgentMessageIdRef` 收编为 state 字段（reducer 视角）。
  - `parseChatStreamEvent(raw: string)`：JSON 解析 + 形状收敛；malformed（心跳）返回 null → 忽略，等价现 try/catch。
  - `deriveChatStreamEventOutcome(event, activeAgentMessageId, nextPlaceholderId)`：**事件分支唯一事实源**。按现 374-479 行相同顺序的 sequential-if 复刻六类分支（STARTED 可见 / COMPLETED 可见 / FAILED 可见或 ERROR / APPROVAL_REQUESTED / APPROVAL 终态五型 / final），输出 `{ nextActiveAgentMessageId, messageOps, streaming?, error?, liveApproval?, approvalBump, shouldCloseStream }`。占位消息 id 由调用方预生成注入（`nextPlaceholderId`），保持函数纯。
  - `applyMessageOps(messages, ops)`：消息数组纯变换。op 语义封闭枚举：`appendPlaceholder` / `complete`（content 替换 + isStreaming false；**空正文兜底文案"已收到回复，但没有可显示的正文。"在 derive 阶段已折算进 op.content**，复刻现 393 行——Codex spec 评审 MED-1 闭环）/ `markFailed`（isStreaming 或占位文案时替换为失败文案，否则 content||失败文案——复刻现 410-423 行三元）/ `markApproval`（hasApproval + isStreaming true）/ `clearStreaming`。
  - `reduceChatStreamEvent(state, raw, nextPlaceholderId) → { state, shouldCloseStream }`：parse→derive→apply 的组合形态，供 L4 直接按事件序列折叠测试；内部完全复用 derive/applyMessageOps，不允许第二份分支实现。
  - `deriveStreamClosedOutcome(activeAgentMessageId)`：onerror CLOSED 分支的纯化（清 streaming + 清活跃消息 isStreaming）。**显式声明的行为修复（Codex spec 评审 HIGH 闭环）**：现实现在 updater 闭包内读 `activeAgentMessageIdRef.current`、随后同步清 null——React 18 批处理下 updater 实际执行时 ref 已为 null，map 沦为 no-op（占位消息 isStreaming 残留 true）。纯化后先快照 id 再派 op，确定性清掉 isStreaming——这是**有意修复该时序竞态**（代码明显意图即清 flag），非静默漂移；无任何既有测试依赖竞态旧行为，commit message 单列。
  - `CHAT_STREAM_EVENT_TYPES` 常量、`makeAgentPlaceholderMessage(id)`、审批快照构造（payload → SSEApprovalSnapshot）。
- Hook 侧只剩接线：handleEvent = parse → derive（读 `activeAgentMessageIdRef.current`，同步写回 `nextActiveAgentMessageId`）→ 各原子 setState（messages 走 `setMessages(prev => applyMessageOps(prev, ops))` 函数式更新，与现状同语义，杜绝 stale 快照）→ `shouldCloseStream && closeStream()`。
- **为何不整体 useReducer**：closeStream 兜底依赖 activeAgentMessageId 的**同步读**（COMPLETED+final 同事件时 ref 已清 → 不触发兜底拉取）；改 useReducer 后该读取变异步会改变兜底行为。保 ref + 纯 derive 是行为零变更的最小面。
- 顺手下沉（非分支逻辑，减行）：`SSEApprovalSnapshot` 挪 chatStreamTypes.ts（hook re-export 保兼容）；`makeControlActionRequest` / `buildPendingConversationScope` / `PendingConversationScope` / `normalizeTaskId`（与 helpers 私有版去重）挪 chatStreamHelpers.ts；closeStream 兜底的 map 变换复用 `applyMessageOps` 的 `complete-if-pending` 语义（helpers 暴露 `fillPendingAgentMessage`）。
- 既有 9 个 hook 级测试**原样保留**（接线层回归）；reducer 新增 L4 直测（§AC-1）。

### 件 2：ChatWorkbench 内联编排下沉

**目标**：1207 → ≤1200（explicit 行删除回落默认），实际目标 ~1000±50。

复核修正：审计标注 ":75-180 内联交互编排"在现基线已漂移——75-180 是 session/restore 解析（多为 hook 入参装配，抽走反而破坏 useMemo deps 语义）。真正可抽的纯派生块是 **202-533**：

| 块 | 现行号 | 去向 | 新纯函数 |
|----|--------|------|---------|
| A. work/会话/A2A 上下文解析 | 202-257 | domains/chat/session.ts | `deriveActiveWorkContext(options)` |
| B. worker 活动 + 兜底活动构建 | 259-327 | domains/chat/activity.ts | `buildWorkerActivityItems(...)` / `buildFallbackWorkerActivity(...)` |
| C. 审批横幅解析（inbox/synthetic/executionSession/live 四源合一 + 倒计时） | 416-488 | domains/chat/approval.ts | `deriveActiveApprovalPresentation(options)`——`approvalNow` 作显式入参；**沿现状不包 useMemo、每渲染直调**（现代码本就内联无 memo，避免引入 deps 冻结倒计时——Codex spec 评审 MED-2 闭环） |
| D. 会话头部展示（techRefs/owner 名/别名/占位符文案） | 388-415, 500-533 | domains/chat/presentation.ts | `deriveChatHeaderPresentation(options)` |
| E. slash 命令表 + 匹配 | 57-73, 489-495 | domains/chat/constants.ts | `CHAT_SLASH_COMMANDS` / `matchSlashCommands(input)` |

原则：**只搬派生、不搬交互**——useState/useEffect/事件 handler（含 handleOperatorAction/submitCurrentInput/键盘编排）留在组件；JSX 零改动（L1 锚点 `chat-input`/`chat-send` 原位保留）；useMemo 保留在组件内、体内改调纯函数。新 derive 函数随件 2 附直测（新代码出生即有测试）。

### 件 3：已抽出纯逻辑补 L4（8 文件，每文件抓行为主干 5-15 用例）

| 文件 | 测试主干 |
|------|---------|
| utils/roundSplitter.ts | groupByAgent 归并/排序；splitIntoRounds 典型事件序列→轮切分、空输入、错误节点、轮边界；computeTimelineLayout 基本布局不变量 |
| domains/chat/activity.ts | buildAgentActivity/buildWorkerActivity 状态→文案/tone 矩阵抽样；buildToolTimelineRecords 配对（started/completed/failed）；buildAgentTraceEntries 直连 vs 委派 |
| domains/agents/agentManagementData.ts | deriveAgentManagementView 典型 snapshot；editor draft 三构造；buildAgentPayload 回写；capability entries/selection merge；parseAgentReview 容错 |
| hooks/chatStreamHelpers.ts | sanitizeAgentVisibleText（tool transcript 剥离/正常 JSON 保留/fence 块）；buildMessagesFromTaskDetail（artifact 回退/内部事件跳过/失败占位）；extractFailureMessage 归一化映射；isUserVisibleModelEvent；findLastAgentContentInCurrentTurn 轮边界；buildRestoreCandidateTaskIds 去重 |
| domains/chat/approval.ts | parseApprovalCommand；mapOperatorQuickAction 全 kind；buildSynthetic/ExecutionSession 项；readPendingApprovalEvent 生命周期（requested→resolved 消除）；readLatestApprovalContext 两级回退；payloadMatchesWork；formatCountdown |
| utils/phaseClassifier.ts | classifyStateTransition/classifyEvents 阶段归类；TERMINAL_STATUSES；formatFileSize 边界 |
| workbench/utils.ts | formatSessionDisplayTitle（alias/title/fallback 矩阵）；get/setValueAtPath；widgetValueToFieldState/parseFieldStateValue 往返；findSchemaNode；categoryForHint |
| domains/operator/userFacing.ts | describeOperatorItemForUser 各 kind；mapOperatorQuickAction 与 chat 版语义一致性 |

红线：**测行为不测实现**——断言输入输出契约，不 spy 内部调用、不快照大对象整体（字段级断言）。

### 件 4：删死代码 ApprovalPanel + useApprovals

- 删除 `src/components/ApprovalPanel/`（ApprovalPanel.tsx / ApprovalCard.tsx / index.ts，320 行）+ `src/hooks/useApprovals.ts`（130 行）。无测试文件、无 CSS 类陪葬。
- 顺手改两处**注释**（api/client.ts:256、client.test.ts:38 提及 useApprovals → 改为现存调用方表述），非行为变更。
- **后端 `/api/approve/{id}` 路由不动**（后端事，归总报告注记：该路由自此前端零调用方）。

### 件 5：杂项

- **共享 FakeEventSource**：新 `src/test/fakeEventSource.ts`，取 TaskDetail 版超集语义（构造收 url、emit "message" 同步派发 onmessage、instances 静态收集、stubGlobal 安装器）；useChatStream.test / TaskDetail.test 两处内联实现删除改 import。Playwright 注入版（e2e/chat-scripted-loop.spec.ts）语境不同不合并。
- **MarkdownContent XSS 断言**（jsdom 可测部分）：`<script>` 剥离；`<img onerror>` 事件属性剥离；`[x](javascript:alert(1))` href 消毒；HTML 实体/嵌套注入不复活；A 标签 target=_blank + rel=noopener noreferrer 钩子行为；正常 markdown（代码块/链接/表格）保留。真 DOM 渲染差异留 L1（已归档）。

## 2. 阈值收回（兑现 F137 ratchet）

| explicit limit 行 | 动作 |
|----|------|
| ChatWorkbench.tsx 1250 | **删除**（回落默认 1200；实际 ~1000） |
| useChatStream.ts 700 | **删除**（回落默认 500；实际 ≤500） |
| index.css 4600 | **收紧到 4480**（现 4476 + 微量余量，继续只挡增长）。3300 目标**本 Feature 不可达**：五件范围无 CSS 下沉，需独立的样式拆分工作（follow-up 归档），F137 注释同步改写为诚实状态 |

## 3. 红线（不可违反）

1. 前端-only：`octoagent/frontend/**` + `repo-scripts/check-frontend-complexity.mjs` 阈值行；后端零触碰；不碰 F141（.githooks/.github/repo-scripts 其余）与 F139（packages/provider）地盘。
2. F140 L1 锚点不丢：`chat-input`/`chat-send`/`chat-message-*`/`frontdoor-*` 源码字面保留；l1SelectorsContract.test 必须绿。Playwright 场景若需跟改只许选择器/等待，不许改断言语义。
3. vitest 全量 ≥204 且 0 fail；`tsc -b` 0 error；`npm run check:complexity` 收回后 PASS。
4. 行为零变更（件 1/2/4 是重构与删除）：既有 204 用例零改动优先；确需改动仅限死代码引用清理，逐条在 commit message 说明。
5. reducer 必须真纯：零 setState/ref/DOM/网络/存储访问；不确定性输入（占位 id、当前时间）一律参数注入。

## 4. 验收（AC）

| AC | 判据 | 绑定测试 |
|----|------|---------|
| AC-1 | reducer 纯函数直测事件序列：乱序、漏事件+final 兜底信号、轮边界、审批生命周期、malformed 心跳忽略、COMPLETED+final 同事件不触发兜底 | `src/hooks/chatStreamReducer.test.ts`（新） |
| AC-2 | useChatStream ≤500 行且既有 9 用例全绿（接线层等价） | `src/hooks/useChatStream.test.tsx`（零改动） |
| AC-3 | ChatWorkbench ≤1200 行且其 29 用例全绿（JSX/交互零变更） | `src/pages/ChatWorkbench.test.tsx`（零改动） |
| AC-4 | 8 个纯逻辑文件各有专属 .test 文件、每文件 ≥5 用例 | 各 `*.test.ts`（新） |
| AC-5 | ApprovalPanel/useApprovals 删除后全量绿 + `grep -r ApprovalPanel\|useApprovals src` 仅剩零命中（注释已改写） | 全量 vitest |
| AC-6 | FakeEventSource 单一实现（src/test/fakeEventSource.ts），两测试文件 import 之 | 两文件全绿 |
| AC-7 | MarkdownContent XSS 断言 ≥5 条消毒用例 | `MarkdownContent.test.tsx` |
| AC-8 | check:complexity 在阈值收回后 PASS；l1SelectorsContract 绿 | `npm run check:complexity` + vitest |

## 5. 终门

`npx vitest run` 全量（数字 ≥204+新增，0 fail）→ `tsc -b` → `npm run check:complexity`（收回后）→ l1SelectorsContract 包含在 vitest 内 → 本地 Playwright（能跑则跑，跑不了归档留 CI 首验）→ Codex final review + Opus 自审 0 HIGH → completion-report + living-docs。

## 6. 偏离归档

1. reducer 落 `chatStreamReducer.ts` 而非并入 chatStreamHelpers.ts（避免击穿 500 行闸，见件 1）。
2. index.css 3300 ratchet 不在本 Feature 兑现（无 CSS 工作项），收紧至 4480 只挡增长 + F137 注释改写；完整回收留独立 follow-up。
3. 审计 ":75-180 内联交互编排" 行号漂移，实抽 202-533 纯派生块（交互编排本体 useState/handler 留组件，符合"UI 变薄=逻辑下沉"本意）。

## 7. Codex spec 评审闭环（2026-07-13，gpt-5.4 high）

第一轮 `codex review --base origin/master`：docs-only diff，0 finding。补充 `codex exec` 设计对抗（读 spec + 现码交叉）：

| Finding | 处理 |
|---------|------|
| HIGH：onerror CLOSED 纯化改变时序语义（现实现 updater 执行时 ref 已 null → map no-op） | **接受为显式修复**——spec 件 1 已改写声明（有意修复竞态，代码意图即清 flag，无测试依赖旧竞态），commit message 单列 |
| MED-1：complete op 缺空正文兜底文案 | **接受**——spec 已补：兜底在 derive 折算进 op.content |
| MED-2：审批横幅提炼若包 useMemo 漏 approvalNow deps 会冻结倒计时 | **接受**——spec 已改：沿现状不 memo、approvalNow 显式入参 |
| LOW×3（提炼块读 ref / 删码动态引用 / L1 testid 风险） | Codex 自证**顾虑不成立**（202-533 无 ref 读取；无动态 import/字符串路由；锚点原位） |

0 HIGH 残留，进入实施。
