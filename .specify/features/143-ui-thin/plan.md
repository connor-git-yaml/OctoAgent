# F143 实施计划

> 每件独立 commit（中文、无 Co-Authored-By）；纯前端 commit 用 `SKIP_E2E=1` 跳后端 smoke（先例），frontend pre-commit 检查照跑。阈值收回与达成它的改动同 commit（自证）。

## Phase 0（本 commit）：spec/plan 落盘 → `codex review --base origin/master`（spec 评审，0 HIGH 后进实施）

## Phase 1（件 1）：useChatStream reducer 化
1. 新建 `src/hooks/chatStreamReducer.ts`：类型 + parse + derive + applyMessageOps + reduce 组合 + onerror 纯化 + 常量/构造器。
2. `chatStreamTypes.ts` 收编 `SSEApprovalSnapshot`；`chatStreamHelpers.ts` 收编 makeControlActionRequest / buildPendingConversationScope / normalizeTaskId（导出化去重）/ fillPendingAgentMessage。
3. `useChatStream.ts` 改接线：handleEvent ~15 行；onerror 走 deriveStreamClosedOutcome；closeStream 兜底走 fillPendingAgentMessage；行数 ≤500。
4. 新增 `chatStreamReducer.test.ts`（AC-1 序列用例，含既有 hook 用例的事件序列在 reducer 层的迁移复刻 + 扩展）。
5. 验证：useChatStream.test 9 用例零改动全绿 + ChatWorkbench.test 全绿 + tsc。
6. **本 commit 同步删 check-frontend-complexity.mjs 的 useChatStream 700 行 explicit 行**。

## Phase 2（件 2）：ChatWorkbench 下沉
1. domains/chat 四模块新增 derive 纯函数（session/activity/approval/presentation）+ constants 收编 slash 命令表。
2. ChatWorkbench 体内改调（useMemo 壳保留，deps 不变原则逐个核对）；JSX 与 handler 零改动。
3. 新 derive 函数直测（随本 commit）。
4. 验证：ChatWorkbench.test 29 用例零改动全绿 + l1SelectorsContract 绿。
5. **本 commit 同步删 ChatWorkbench 1250 行 + 收紧 index.css 4600→4480 + 改写 F137 注释**。

## Phase 3（件 3）：8 文件 L4 补测（纯新增，不动生产代码）
## Phase 4（件 4）：删 ApprovalPanel + useApprovals + 注释改写
## Phase 5（件 5）：共享 FakeEventSource + MarkdownContent XSS 断言

## 终门 + 评审
1. `npx vitest run` 全量 / `tsc -b` / `npm run check:complexity` / 尝试本地 `npm run test:e2e`（Playwright；不可跑则归档）。
2. Codex final review（挑战面：reducer 真纯性 / 删码漏 import / 阈值假收（exclude 而非真下沉）/ 新 L4 测行为 vs 实现）→ 处理到 0 HIGH。
3. Opus 自审（spec 对齐 + 红线逐条核）。
4. completion-report.md + living-docs（milestones.md F143 行 ✅）。
5. 归总报告（5 件交付表 / 复杂度前后 / vitest 数前后 / 删除行数 / 合入建议）。**不 push。**

## 风险与对策
- ChatWorkbench.test 2432 行对提取敏感 → 提取只动"派生计算"纯块，JSX/handler/useState 原位；每步跑该文件测试短反馈。
- reducer 行为漂移 → derive 按原 sequential-if 顺序逐分支复刻；hook 侧 messages 应用保持函数式更新语义；useChatStream.test 零改动为等价性外部证据。
- FakeEventSource 收敛引入语义差 → 采超集版；useChatStream.test 不 emit "message" 型事件，超集无影响。
- Playwright 本地依赖缺失 → 契约测试（vitest 层）已覆盖锚点存在性；场景留 CI 首验并归档。
