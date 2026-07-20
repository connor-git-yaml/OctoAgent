# F148 设计系统 + Web 主工作台 v2 — spec

- **里程碑**：M11 波次①（∥ F150 Cloudflare）
- **规模**：L（前端）
- **基线**：`origin/master a753570b`；vitest 428 passed / tsc 0 / complexity pass 为零回归参照
- **红线**：纯 `frontend/`，不碰 gateway/front_door（F150 地盘）；**绝不 push**

## 1. 目标与范围

把 claude.ai/design 的 **Spotify 深色 v2** 落到现有 React 前端：**设计系统全套 + 主对话工作台三栏(1a) + 全局任务浮层(1b) + 启动加载页(1c)**。**复用现有数据逻辑，只换视觉/结构/交互**。其余页面（智能体/技能/MCP/文件/记忆/审批/定时/设置）归 **F149**，本 Feature **不重构**（它们随 token 翻转自动获深色底，accent 残留列 known-limitation）。

**非目标（显式排除）**：
- 后端任何改动（协议/路由/store）——勘察确认 F148 可纯前端。
- 右栏列**同会话多条并发任务**——需后端工（Task 无 session_id 列），**defer**；右栏 v1 只显示会话**当前活跃任务**（与现 ChatWorkbench 单 `taskId` 语义一致）。
- F149 页面重皮；F150 Cloudflare。
- 逐像素比对原稿（DesignSync 不可达，见 recon §0）。

## 2. 设计系统规格（Phase 0）

### FR-0.1 tokens.css 深色重皮肤化（in place，不并造第二套）
`styles/tokens.css` 的 `--cp-*` **原地**改为 Spotify 深色：纯黑底（`#0a0a0a`/`#121212`）、卡片 `#181818`/`#282828`、主色 `#1ed760`、文字 `#fff`/次级 `#a7a7a7`、边框 `rgba(255,255,255,.09)`、`color-scheme: dark`、字体 `"Figtree Variable", ...`。**committed dark**：删除冗余的 `@media (prefers-color-scheme: dark)` 覆盖块（:root 已是暗色，Spotify 无浅色模式；先 grep 确认无 `.light` 消费者）。变量名/结构不变，630 个 `var(--cp-*)` 消费方零改动即翻转。
- `@test` `src/styles`（无 CSS 单测 → 由 tsc/complexity/L1 渲染 + 截图验证）

### FR-0.2 字体 + 图标（自托管，离线安全）
`@fontsource-variable/figtree` + `remixicon`（已 `npm install`，Vite 打包不外链），main.tsx 导入。Figtree 应用于 body（tokens 字体栈）。remixicon 供图标类。install 已验证成功。

### FR-0.3 三动画（新 v2 主题层，绝不进 index.css）
新文件 `styles/theme-v2.css`（≤700）定义 `@keyframes octoPulse`（加载 logo 呼吸）/ `octoBar`（运行均衡器竖条，仿 Spotify now-playing）/ `octoJelly`（泡泡 mark 果冻摆动，对话空舞台）。全部纯 CSS，受 primitives.css 现有 `prefers-reduced-motion` 全局 reduce 块自动停用。

### FR-0.4 旧 accent 硬编码色覆盖
`theme-v2.css` 覆盖 index.css/workbench-ui.css/primitives.css 中主工作台类的**硬编码旧主题色**（nav hover/active 渐变、brand drop-shadow/tagline、session-item hover、`.wb-button-primary` 渐变、focus outline、message 气泡、中性 ink rgba）→ 绿色/深色系。（token 翻转带走大部分，本条补硬编码残留。）

### FR-0.5 logo
沿用 `public/octo-mark.svg`（蓝泡泡），深色底 drop-shadow 调绿。设计 mark 无法核对——色差归档待用户确认（recon §1.3）。

## 3. 主工作台三栏（Phase 1，1a）

### FR-1.1 左栏（296px，WorkbenchLayout）——项目分组会话 + 运行指示 + 导航 + 就绪卡
- **会话按项目分组**：`sessions[]`（web 优先，退化全部）group-by `project_id`，组头 = `available_projects[].name`（缺省"默认项目"）。**纯前端**（recon §2 Q1）。
- **组头可折叠**（交互态②）+ 列表滚动。运行会话（`status`∈running/waiting_input/waiting_approval 或 `lane==="running"`）显示 **octoBar 运行指示**（交互态⑤：纯 CSS，`prefers-reduced-motion` 停）。
- 保留：品牌 lockup、8 项导航（含审批红点 badge）、新建会话按钮、删除会话。
- "当前状态"卡 → v2 **就绪卡**（复用 `buildShellStatus` 文案逻辑，只换皮）。
- `@test` `src/components/shell/WorkbenchLayout.test.tsx`（新增分组渲染断言）

### FR-1.2 中栏（对话舞台，ChatWorkbench）——加壳不改内核
- 外包 2 列 grid（中 + 右 332px）。**中栏内核 JSX/文案/role/class 保留**（vitest 紧耦合，recon §5）：标题 heading、owner 名、消息流、输入表单(`chat-input`/`chat-send`/"发送")、审批横幅、activity、恢复/空态、编辑别名。
- 空会话舞台加 **octoJelly 泡泡 mark**（视觉，不改逻辑）。委派卡/工件卡（MessageBubble activity）复用，只重皮。
- `@test` `src/pages/ChatWorkbench.test.tsx`（保绿；必要处更新选择器不改语义）

### FR-1.3 右栏（332px，新增 `components/chat/SessionRunPanel`）——本会话运行状态
- 消费 `useTaskLiveState`（ChatWorkbench 已在用）的 `taskDetail`/`executionSession`/`pendingApprovals` + 会话 `lane`/`execution_summary`。
- 显示：运行状态（复用 `StatusBadge` + status 语义色）、进度（`execution_summary.current_step` / 事件序，无数值 progress → 用步骤/事件表述）、**停止**（`executionSession.can_cancel` → 现有 cancel action；**不造后端不存在的"暂停"**，只暴露真实可用控制）、事件流（`taskDetail.events` 精简渲染，技术字段折叠）、工件（`taskDetail` artifacts / 按需复用 `ArtifactGrid`）。
- **空态**（交互态①）：无活跃任务 → "就绪，发条消息开始"。**失败态**（交互态③）：failed/cancelled/timed_out 明确样式。
- 面向非技术用户：id/hash/内部字段进折叠区（`HoverReveal` 或"技术详情"）。
- 新增 testid `session-run-panel`（+ 登记 selectors.ts）。
- `@test` `src/components/chat/SessionRunPanel.test.tsx`（新增：空态/运行态/失败态/停止按钮）

## 4. 全局任务浮层（Phase 2，1b，新增 `components/shell/GlobalTaskOverlay`）
- 挂 WorkbenchLayout，全局可见（右下 FAB 触发展开）。读 `snapshot.resources.delegation.works` 活跃 works（`ACTIVE_WORK_STATUSES`，WorkbenchLayout 已在算）。
- 列活跃任务：标题/状态/所属会话，点击跳该会话或 `/tasks/:id`。
- **与右栏(1a) 同任务状态一致**（交互态④）：同源 `delegation.works` + `SessionProjectionItem`，单一事实源。
- 无活跃任务空态。新增 testid `global-task-overlay`（+ selectors.ts）。
- `@test` `src/components/shell/GlobalTaskOverlay.test.tsx`（空态/有任务/展开收起）

## 5. 启动加载页（Phase 3，1c）
- 增强 `WorkbenchLayout` 的 `.wb-boot` + `App.tsx` `RouteFallback`：octoPulse logo 动画 + 品牌 + tagline，v2 深色。CSS 落 `theme-v2.css`/`workbench-v2.css`。
- `@test` 现有 `App.test.tsx` 保绿（RouteFallback 渲染）。

## 6. 交互态清单（硬约束 #5，逐条实现，见 recon §4）
① 右栏空态 ② 项目/会话几十个折叠+滚动 ③ 任务失败态 ④ 浮层↔右栏同任务一致 ⑤ 多会话 octoBar + `prefers-reduced-motion` + 性能（纯 CSS/封顶）。

## 7. 组件树与文件落点
```
main.tsx                     + import figtree/remixicon + theme-v2.css + workbench-v2.css（末位覆盖）
styles/tokens.css            改：--cp-* 深色重皮（in place）
styles/theme-v2.css          新（≤700）：字体应用 + 3 keyframes + 旧 accent 覆盖 + 加载页
styles/workbench-v2.css      新（≤700）：三栏布局 + 项目分组 sidebar + 右栏 panel + 浮层 + octoJelly 空舞台
components/shell/WorkbenchLayout.tsx   改：会话按项目分组 + octoBar + 就绪卡 + 挂 GlobalTaskOverlay
components/shell/GlobalTaskOverlay.tsx 新（components/ 不受 1200 门）
components/chat/SessionRunPanel.tsx    新（右栏）
pages/ChatWorkbench.tsx      改：外包 2 列 grid + 渲染 SessionRunPanel（内核保留）
e2e/selectors.ts             + session-run-panel / global-task-overlay 锚点
App.tsx                      改：RouteFallback v2 加载页
package.json/lock            + figtree + remixicon（已装）
```

## 8. Phase 顺序
Phase 0 设计系统（tokens 重皮 + 字体/图标 + 动画 + accent 覆盖）→ Phase 1 三栏（左分组 → 右栏 panel → 中栏加壳）→ Phase 2 浮层 → Phase 3 加载页 → Verify。

## 9. 验收门（终门）
- vitest 全绿（≥428，新增测试）+ tsc 0 + `check:complexity` 过（新 css 各 ≤700，**index.css 不涨**）+ L1 Playwright 保绿（build + playwright test）。
- 若碰共享后端：后端全量 0 regression（本 Feature 预期不碰）。
- 双评审（Codex + Opus）0 HIGH：文案映射无退役词 / tokens 真重皮不并造 / 数据逻辑未重写 / 交互态完整 / 复杂度门。
- 截图证 1a/1b/1c 还原度。

## 10. 已知限制（living-docs）
- L1（recon §0）原稿逐像素未自证。
- L2 F149 页面 accent 残留（渐进边界，非回归）。
- L3 右栏多并发任务列表 defer（后端工）。
- L4 octo-mark 色差待用户确认。
- L5 SettingsPage litellm 残留（F149 territory，F148 不碰）。
