# F148 勘察报告（recon）

> 目标：把 claude.ai/design 的 Spotify 深色 v2 设计落到现有 React 前端。
> 流程：现状勘察 → 收窄 spec → Codex 评审 → 实施 → 双评审 → 归总，**绝不 push**。
> worktree：`.claude/worktrees/F148-web-v2`，分支 `feature/148-web-workbench-v2`，基线 `origin/master a753570b`。

---

## 0. ⚠ 设计原稿可达性（必须先说清）

- 任务要求用 **DesignSync MCP** 读原稿（project `851e3fb2`，`OctoAgent Web.dc.html`）作为"忠实还原的单一事实源"。
- **本会话 DesignSync MCP 不可用**：工具未加载、非交互会话无法走 OAuth、`ToolSearch` 查 `DesignSync` 无结果；本地也无 `*.dc.html` 原稿文件。
- **按任务显式 fallback**：`若 DesignSync 无权限，按 CLAUDE.local.md §M11 设计系统规格实现`。因此本 Feature 实现的是 **§M11 书面设计规格 + Spotify 深色设计语言**（纯黑 + Spotify 绿 `#1ed760` + Figtree + octoBar/octoJelly/octoPulse 三动画 + 三栏 296/中/332 + 浮层 + 加载页），**非逐像素比对未见的 mockup**。
- **影响与限制**：布局结构、色板、动画、交互态按书面规格忠实落地；但"1a/1b/1c 逐像素还原度"无法对照原稿自证，需用户拿到原稿后校验细节（间距/圆角/具体图标/文案微调）。这条在归总里对用户显著标注。

---

## 1. 现有前端工作台结构（事实源）

技术栈：React 19 + Vite 6 + react-router 7，**纯 CSS 无 tailwind/CSS-in-JS**。

### 1.1 样式层与复杂度门（关键约束）
CSS import 顺序（`src/main.tsx`）：`tokens.css → primitives.css → shell.css → workbench-ui.css → index.css`。**末位导入可覆盖前面**。

复杂度门 `repo-scripts/check-frontend-complexity.mjs`（`npm run check:complexity`）：
| 规则 | root | 每文件上限 |
|------|------|-----------|
| 样式层 | `styles/`、`index.css` | **700**（`index.css` 显式 4480）|
| 页面/域模块 | `pages/`、`domains/`、`ui/` | 1200 |
| 共享 hook/query | `hooks/`、`platform/` | 500 |
| **`components/`** | 不在 root 清单 | **不受门限**（新组件落此最安全）|

现状行数：`index.css` **4476/4480**（≈冻结，仅 4 行余量）｜`workbench-ui.css` 500/700｜`tokens.css` 102｜`ChatWorkbench.tsx` 988/1200。
→ **硬结论**：v2 主题/布局 CSS **必须落新的 ≤700 行文件**，`index.css` 一行都不能加；新组件落 `components/` 规避 1200 门。

### 1.2 主题 token（重皮肤化目标）
`styles/tokens.css`：`:root` 是**浅色暖调**——IBM Plex Sans、奶油底、primary `#b76a2b`(琥珀)/secondary `#1f6a5b`(墨绿)，全套 `--cp-*` 变量 + 一个 `@media (prefers-color-scheme: dark)` 暗色块。**630 个 `.wb-*` 类大量消费 `var(--cp-*)`**——翻转 tokens 即翻转全站底色。`index.css` 内约 **100 处硬编码旧主题色**（teal `rgba(31,106,91)`/amber `rgba(183,106,43)`/ink `rgba(20,32,47)`），多为 accent 点缀（nav hover 渐变、drop-shadow、button-primary 渐变）。

### 1.3 Shell 与页面
- `components/shell/WorkbenchLayout.tsx`（588 行）：`.wb-shell` = `grid-template-columns: 296px minmax(0,1fr)`（**左栏 296px 已匹配设计**）。左 `.wb-sidebar`（品牌 lockup + 会话列表 + 导航 8 项 + "当前状态"卡）｜右 `.wb-main`（topbar + `<Outlet/>`）。会话列表 `ChatNavSection`：**扁平列表，过滤 `channel==="web"`，无项目分组**。
- `pages/ChatWorkbench.tsx`（988 行）：**只是中间对话面板**（头部标题/owner + 消息流 + 输入表单 + 审批横幅）。**当前无右栏"本会话运行状态"**——该数据现由 `useTaskLiveState`（3s 轮询 `taskDetail/executionSession/pendingApprovals`）驱动，展示在独立整页路由 `/tasks/:taskId`（`TaskDetail`，在 shell 之外）。头部有 `打开任务` 链接跳过去。
- 加载态：`WorkbenchLayout` 的 `.wb-boot` 卡 + `App.tsx` 的 `RouteFallback`（懒加载切页骨架）——**1c 加载页落点**。
- logo：`public/octo-mark.svg` 是**蓝色渐变泡泡 mark**（`#D8EEFF→#5AA9FF`），非绿色。设计 mark 无法核对；决定沿用它（泡泡形态与 octoJelly 契合），深色底做 drop-shadow 调绿，色差归档待用户确认。

---

## 2. 后端数据现成性核实（硬约束 #3 判定，Explore 子代理取证）

| 问题 | 判定 | 证据 |
|------|------|------|
| **Q1 跨项目分组会话** | ✅ **现成（纯前端）** | `GET /api/control/resources/sessions` 的 `SessionDomainService.get_session_projection()` 用 `task_store.list_tasks()`（无 project 过滤）构建，每条会话各自 `resolve_project_for_scope` 写入 `project_id`——**投影本就跨项目**。前端 `useWorkbenchData` 已整体拿到 `resources.sessions`（全项目）+ `resources.project_selector.available_projects`（全项目名）。"只用 `active_project_id`"是聊天目标/新建入口用的 `current_project_id`，**不是**会话列表过滤条件。 |
| **Q2 按 session 过滤任务** | ⚠ **部分现成** | Task 模型**无 `session_id` 列**（`core/models/task.py`），`/api/tasks` 仅支持 `status` 过滤。**但** `SessionProjectionItem` 已带该会话**当前活跃任务**的 `task_id`/`status`/`lane`(running/queue/history)/`execution_summary`(state/current_step)——**"本会话当前那条运行任务"开箱即用**。列该会话**多条并发任务** = 隐藏后端工（需把内部 `_list_tasks_for_projected_session` 提成 REST 或给 `/api/tasks` 加 session 过滤）。 |
| **Q4 实时流** | per-task only | 仅 `GET /api/stream/task/{task_id}` SSE；`useTaskLiveState` 用 **3s 轮询**非 SSE。无 session 级/全局流。右栏实时化 = 复用 `useTaskLiveState` 轮询会话的 `task_id`（现 ChatWorkbench 已这么做）。 |

**隐藏后端工判定**：
- 左栏跨项目分组：**纯前端** group-by `project_id`。
- 右栏"本会话运行状态"：**纯前端**（复用会话当前 `task_id` + `useTaskLiveState`；`lane`/`execution_summary` 给运行指示）。
- 全局浮层(1b)：**纯前端**（读 `snapshot.resources.delegation.works` 活跃 works，WorkbenchLayout 已在算 `activeWorkCount`）。
- **唯一需后端的**：右栏列**同会话多条并发任务** → **收窄出 F148，defer**（右栏 v1 只显示会话当前活跃任务，与现 ChatWorkbench 语义一致）。
→ **F148 整体可做成纯前端**（守住红线"纯 frontend/，别碰 gateway"）。

---

## 3. 复用 vs 重写清单（硬约束 #4：复用数据逻辑只换视觉/结构）

| 领域 | 处置 | 说明 |
|------|------|------|
| 会话/任务/事件/审批数据 | **复用** | `platform/queries/useWorkbenchData`、`hooks/useChatStream`、`hooks/chatStreamReducer`、`hooks/useTaskLiveState`、`api/client`、REST/SSE 协议 **零改动** |
| 派生逻辑 | **复用** | `domains/chat/{activity,approval,presentation,session}` 全部复用 |
| tokens.css | **重皮肤化（in place）** | `--cp-*` 整体改深色，**不并造第二套** |
| index.css 主题 | **不动**（冻结）+ 少量 accent override 落新文件 | token 翻转自动带走 80%；硬编码旧色 accent 在新 v2 css 覆盖 |
| 左栏 sidebar | **改结构**（WorkbenchLayout）| 扁平会话 → 按项目分组 + 运行指示 octoBar；保留品牌/导航/状态卡的文案/role |
| 中栏对话 | **加壳不改内核**（ChatWorkbench）| 外包 2 列 grid + 右栏；**中栏 JSX/文案/role/class 尽量保留**（vitest 紧耦合，见 §5）|
| 右栏运行状态 | **新增**（`components/chat/SessionRunPanel`）| 消费 `useTaskLiveState` 数据（status/进度/事件流/工件/停止），复用 `StatusBadge`、按需 `ArtifactGrid` |
| 全局浮层(1b) | **新增**（`components/shell/GlobalTaskOverlay`）| 读 `delegation.works`，挂 WorkbenchLayout 全局可见 |
| 加载页(1c) | **增强**（`wb-boot`/`RouteFallback`）| octoPulse logo 动画 + 品牌 |
| 字体/图标 | **新增自托管** | Figtree（`@fontsource`）+ remixicon（npm，离线安全）；install 失败则退系统栈 + 内联 SVG |

## 4. 交互态清单（硬约束 #5，设计稿静态 happy-path 之外必补）
1. 右栏**无运行任务空态**（会话未跑任务 / 已终态）——显示"就绪，等你发消息"占位。
2. 会话/项目**几十个**：项目分组头**可折叠** + 列表滚动（`.wb-sidebar` 已 `overflow-y:auto`）。
3. 任务**失败态**：右栏 + 浮层显示 failed/cancelled/timed_out 明确样式（复用 status pill 语义色）。
4. **浮层(1b) 与右栏(1a) 同一任务状态一致**：两处同源 `SessionProjectionItem`/`delegation.works` + `useTaskLiveState`，单一事实源避免漂移。
5. **多会话同时 octoBar 动画 + 高频 SSE 性能**：octoBar 纯 CSS 动画；`prefers-reduced-motion` 停动画（primitives.css 已有全局 reduce 块，v2 动画自动被覆盖）；运行会话数封顶动画或用轻量实现避免几十条同时重绘。

## 5. 测试契约（不得破坏，硬门）
- **L1 data-testid 单一事实源** `e2e/selectors.ts`，由 vitest `testing/l1SelectorsContract.test.ts` 机械校验"每锚点须在 src/**.tsx 字面出现"。必保留：`chat-input`/`chat-send`/`chat-message-assistant`/`chat-message-user`/frontdoor*/approval-compact*。新增右栏/浮层锚点须**同一 commit 加源码 + 登记 selectors.ts**。
- **vitest `ChatWorkbench.test.tsx` 紧耦合**：`发送` 按钮名、`主助手`/owner 名文案、session 标题渲染为 heading（h3）、`打开任务` link 带 `wb-button-inline`、activity 文案（委派目标/授权工具…）、恢复态文案、编辑会话名按钮。→ **中栏加壳式重构**（保留内核 JSX/文案/role/class），右栏纯增量。
- L1 Playwright：`chat-scripted-loop`/`approval-center`/`front-door-token` 三 spec，靠上述 testid。restructure 保绿。

## 6. 设计词 → 代码真实词 映射（硬约束 #1，禁照抄退役词）
| 设计稿词（推测含）| 代码/UI 真实词 | 依据 |
|------|------|------|
| Butler | **主 Agent**（66×）/ 主助手（23×）/ 会话 `session_owner_name`（动态）| grep 前端 UI 现用词；面向非技术用户优先"主助手"，品牌用 Octo/OctoAgent |
| LiteLLM 正常 | **模型已连接 / 运行正常**（ProviderRouter）| F081 已退役 LiteLLM；诊断已有映射"可直接使用/受限运行/需要检查"（`formatDiagnosticsLabel`）。**注**：`domains/settings/SettingsPage.tsx` 仍有 `litellm_*` 残留——**F149 territory，F148 不碰**，仅避免在新面自造退役词 |
| runtime 状态 | task：running/waiting_input/waiting_approval/created/assigned/escalated/completed/failed/cancelled/timed_out；session lane：running/queue/history；模式：echo vs 已连接 | `useChatStream`/`SessionProjectionItem.lane`/`runtime.llm_mode` |

## 7. 风险 / 限制
- **R1（最重要）**：无 DesignSync 原稿，逐像素还原度不可自证（见 §0）。
- **R2**：重皮肤化影响全站——F149 未重构页面会"深色底 + 少量旧 accent 色残留"，属**渐进边界**，列 known-limitation，非回归。
- **R3**：字体/图标新增 npm 依赖需改 package-lock；若 registry 不可达退系统栈 + 内联 SVG。
- **R4**：右栏 v1 只显示会话当前活跃任务（多并发任务列表 defer，需后端工）。
