# F143 UI 变薄扩 L4 — Completion Report

> 2026-07-13。worktree `F143-ui-thin`（feature/143-ui-thin），基线 origin/master `d22378b8`。
> 7 commits，**未 push，等用户拍板**。

## 1. 交付对照（计划 vs 实际）

| 件 | 计划 | 实际 | 状态 |
|----|------|------|------|
| 1 | useChatStream handleEvent 分支 reducer 纯函数化，660→≤500 | 659→**487**；新 `chatStreamReducer.ts`（315 行纯函数：parse/derive/applyMessageOps/reduce 组合/onerror 纯化/审批快照）+ 24 条序列直测；既有 9 hook 用例零改动全绿 | ✅ |
| 2 | ChatWorkbench 内联编排下沉，1204→≤1200 | 1207→**989**；五块纯派生下沉 domains/chat（session/activity/approval/presentation/constants），JSX/handler/useState 零改动；新 derive 函数 67 条直测 | ✅ |
| 3 | 3600 行已抽出纯逻辑补 L4（8 文件各 5-15 用例） | 8 文件全兑现（roundSplitter/phaseClassifier/workbench utils/operator userFacing/agentManagementData/chatStreamHelpers 6 个新文件 79 用例 + activity/approval 并入件 2 的 67 用例内）；全部输入输出契约断言，0 spy/0 整体快照 | ✅ |
| 4 | 删死代码 ApprovalPanel + useApprovals | 净删 450 行（组件 320 + hook 130）；证据三方复核（grep 全 src / Codex spec 评审自证 / Opus 复核零残留）；两处注释改写 | ✅ |
| 5 | 共享 FakeEventSource + MarkdownContent XSS | `src/test/fakeEventSource.ts` 单一实现（超集语义），收敛 3 处内联副本（主副本 ×2 + 2 特化变体）；MarkdownContent +8 条消毒断言 | ✅ |
| 阈值 | 兑现 F137 ratchet | ChatWorkbench 删 1250 行回默认 1200（实际 989）；useChatStream 删 700 行回默认 500（实际 487）；index.css 4600→**4480**（收紧只挡增长）| ✅（index.css 3300 见 §4）|

## 2. 数字

| 指标 | 前 | 后 |
|------|----|----|
| ChatWorkbench.tsx | 1207（限 1250） | **989**（默认限 1200） |
| useChatStream.ts | 659（限 700） | **487**（默认限 500） |
| vitest | 29 文件 / 204 passed | **41 文件 / 382 passed**（+178，0 fail） |
| tsc -b / npm run build | 0 error | 0 error |
| check:complexity | PASS（放宽态） | **PASS（收回态）** |
| L1 Playwright | — | 本地真跑 **3/3 passed**（9.7s，loopback+bearer 双 webServer） |
| l1SelectorsContract | 8 passed | 8 passed（锚点零丢失） |
| diff | — | 35 文件 +4253/-1194 |

## 3. 评审闭环

- **Codex spec 评审**（gpt-5.4 high，实施前）：`codex review --base` 0 finding（docs-only）+ 补充 `codex exec` 设计对抗抓 **1 HIGH + 2 MED**——①onerror CLOSED 纯化实为修复 baseline updater 读 ref 已 null 的时序竞态 → 改为**显式声明修复**；②complete op 补空正文兜底文案；③审批倒计时派生不包 useMemo（approvalNow 显式入参）。全接受改写进 spec（§7）。3 LOW 自证顾虑不成立。
- **Codex final review**（gpt-5.4 high，全 diff）：**0 finding**（其自行复跑 vitest + tsc 验证）。
- **Opus 席独立评审**：**0 HIGH / 0 MED / 3 LOW（全信息性）**——逐分支对照 baseline 确认 reducer 语义等价 + COMPLETED+final 不触发兜底等承重时序不变量有直测护栏；四块下沉字段级保真、丢弃中间量确属块内私有；阈值收回靠真下沉非 exclude 放水；红线零越界。

## 4. 偏离与 limitations（已归档 spec §6）

1. **reducer 落 `chatStreamReducer.ts`** 而非并入 chatStreamHelpers.ts：387+~200 会击穿 hooks 500 行闸，为兑现 ratchet 而放宽阈值自相矛盾；取任务书精神落同目录兄弟纯模块。
2. **index.css 3300 目标本 Feature 不可达**：五件范围无 CSS 拆分工作、ApprovalPanel 无样式陪葬；收紧 4600→4480 只挡增长 + F137 注释改写为诚实状态。完整回收需独立样式拆分 follow-up。
3. **审计行号漂移**：":75-180 内联交互编排"在现基线是 hook 入参装配（抽走破坏 useMemo deps 语义）；实抽 202-533 纯派生块。
4. **一处显式行为修复**（非零变更豁免，双评审确认）：onerror CLOSED 分支 baseline 因 ref 时序在 React 18 下清 isStreaming 沦为 no-op；纯化后确定性清除（即代码原意）。无既有测试依赖旧竞态。
5. **后端 `/api/approve/{id}` 路由自此前端零调用方**——后端事不动，留给后端侧未来清理评估。
6. 前端测试实践仍未进 testing-strategy.md（审计 gate|gap-low）——文档域改动超前端-only 红线（milestones 行更新是流程明令例外），归 follow-up。

## 5. living-docs 漂移闸

- `docs/blueprint/milestones.md` M9 F143 行 → ✅（随本 commit）。
- 前端无专属 codebase-architecture 文档，无其他 code↔doc 漂移面。
- `repo-scripts/check-frontend-complexity.mjs` 注释即其自身文档，已同步改写。

## 6. 合入建议

**建议合入 origin/master**：全量 vitest/tsc/complexity/L1 Playwright 本地四连绿；双评审 0 HIGH 残留；与并行的 F141（gate 编排）/F139（packages/provider tests）文件面零交集（本分支只碰 `octoagent/frontend/**` + complexity 阈值行 + `.specify/features/143-ui-thin/` + milestones.md 一行）。唯一潜在合并点：若 F141 也改 check-frontend-complexity.mjs 或 milestones.md，属琐碎文本冲突。
