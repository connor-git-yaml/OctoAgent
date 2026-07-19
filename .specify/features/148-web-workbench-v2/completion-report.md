# F148 设计系统 + Web 主工作台 v2 — completion-report

- **分支**：`feature/148-web-workbench-v2`（worktree），基线 `origin/master a753570b`
- **状态**：实施完成，双评审 0 HIGH，**未 push**（等用户拍板）
- **净改动**：7 commits；纯 `frontend/`，零后端改动（守住 F150 红线）

## 1. 交付内容（对照 spec Phase）

| Phase | 计划 | 实际 | 状态 |
|-------|------|------|------|
| 0 设计系统 | tokens 深色重皮 + 字体/图标 + 三动画 + accent 覆盖 | `tokens.css` `--cp-*` 原地翻转 Spotify 深色（删冗余 dark-media 块，committed dark）+ `@fontsource-variable/figtree` + `remixicon` 自托管 + `theme-v2.css`（octoPulse/octoBar/octoJelly + 旧 accent 覆盖 + 加载页）| ✅ |
| 1 三栏 | 左分组会话 / 中对话 / 右运行状态 | 左：`WorkbenchLayout` 会话按 `project_id` 分组 + 折叠 + octoBar 运行指示 + v2 就绪卡；中：`ChatWorkbench` 外包 2 列 grid + octoJelly 空舞台（内核 JSX 保留）；右：新 `SessionRunPanel`（状态/进度/事件流/工件/打开任务）| ✅ |
| 2 全局浮层 | 1b | 新 `GlobalTaskOverlay`（FAB + 展开列表，读 `delegation.works`，挂 shell 全局）| ✅ |
| 3 加载页 | 1c | `wb-boot` + `RouteFallback` + reconnect 卡加 octoPulse logo | ✅ |

## 2. 硬约束（review 校正 5 条）逐条落实

1. **文案映射禁退役词**：grep 确认 F148 改动文件**零** Butler/LiteLLM/管家 泄漏；设计「Butler」→ 代码真实词「主 Agent / 主助手 / `session_owner_name`」。（SettingsPage 存量 litellm 属 F149，未碰。）
2. **重皮肤化不并造**：`--cp-*` 原地翻转；`theme-v2.css`/`workbench-v2.css` 全消费 `var(--cp-*)`，grep 确认**无平行色板**（无 `--spotify-*` 等）。`index.css` 4477/4480 **零增长**。
3. **Cloudflare 措辞**：F150 territory，本 Feature N/A。
4. **后端现成性先验**（recon §2）：跨项目分组会话 = 现成纯前端；本会话当前运行任务 = 现成（`SessionProjectionItem.task_id/lane/execution_summary`）；**多并发任务列表 = 隐藏后端工，defer**。→ F148 纯前端成立。
5. **交互态清单**：① 右栏空态就绪 ✅ ② 项目折叠+滚动 ✅（浏览器实测折叠 caret ▾→▸）③ 失败态横幅 ✅ ④ 浮层↔右栏同源 `delegation.works` + 同状态词表 ✅ ⑤ octoBar 纯 CSS + `prefers-reduced-motion` 全局 reduce 块自动停 ✅。

## 3. 双评审闭环（0 HIGH）

### Codex（spec + final，gpt-5.4）
- Spec review：0 HIGH（验 font 依赖 + lock 一致性 + spec）。
- Final review 2 finding 全闭环：
  - **P1（已改）**：`ACTIVE_WORK_STATUSES` 原漏 `waiting_input`/`waiting_approval` → 任务等用户输入/审批时从浮层+计数消失。已补入（`WorkStatus` 确有这两态，delegation.py:35-36）。浮层/计数/侧栏 octoBar 三处状态一致。
  - **P2（已改）**：右栏用 `Boolean(taskId)` 判活跃 → 重开已完成会话显示残留旧运行。改为 `taskId && (streaming || !isTerminal || isFailed)`：成功终态回「就绪」，失败终态仍显示失败横幅（不静默消失，Constitution #8）。浏览器实测确认。

### Opus 对抗自审（0 HIGH）
- 逐视图 1a/1b/1c 对照 §M11 书面规格：三栏结构/动画/色板/交互态全落地。
- 数据逻辑复用核查：`SessionRunPanel`/`GlobalTaskOverlay` 消费现有 `useTaskLiveState`/`delegation.works`，**零新 hook/零新 fetch/零协议改**。
- 非技术用户 UX：事件流补友好中文标签 + 兜底不暴露原始事件类型名（原始细节留「打开任务详情」全页）；技术字段进「技术详情」折叠。

## 4. 验收门（全绿）

- vitest **438 passed / 48 files**（baseline 428 + 10 新测试，含 SessionRunPanel 6 / GlobalTaskOverlay 2 / L1 契约 +2）
- tsc `-b` **0**
- `check:complexity` **通过**（index.css 4477/4480 不涨；theme-v2 231/700；workbench-v2 420/700；ChatWorkbench 1012/1200）
- **L1 Playwright 4/4 passed**（含 chat-scripted-loop 打我重构的 ChatWorkbench——一度因我手工 browsing 污染共享 fixture 假红，清 fixture 后 4/4 绿）
- `npm run build` 通过（Figtree + remixicon 打包，无外链）
- 后端 e2e_smoke **26 passed**（pre-commit hook，每 commit）

## 5. 渲染截图证据（本机 L1 fixture 实测）

- 1a 空态：三栏 + octoJelly 空舞台 + 右栏「就绪」+ 绿 send 按钮 + 项目分组侧栏。
- 1a 运行态：右栏状态徽标/进度卡/事件流（加载行为配置·回忆相关背景·模型推理·保存进度·运行技能）/产出文件（remixicon 图标）；绿气泡黑字（Spotify 风）。
- 1a 折叠：DEFAULT PROJECT caret ▾→▸ 会话收纳。
- F149 页（/agents）：token 翻转自动获干净深色（渐进边界佐证）。
- 未截：1b 浮层（fixture 无活跃 delegation works，改由 vitest 覆盖）；1c 加载（瞬态 octoPulse，vitest RouteFallback 覆盖）。

## 6. 已知限制（living-docs 漂移记录）

- **L1 原稿逐像素未自证**：DesignSync MCP 本会话不可达（非交互无法 OAuth），按任务显式 fallback 用 §M11 书面规格 + Spotify 设计语言实现。结构/色板/动画/交互态忠实，但间距/圆角/具体图标细节需用户拿原稿校验。
- **L2 F149 页面 accent 残留**：token 翻转让其余页自动深色，但个别硬编码旧 accent/结构未重构——**渐进边界**，非回归，归 F149。
- **L3 右栏多并发任务列表 defer**：Task 无 `session_id` 列，列同会话多任务需后端工，超纯前端红线。右栏 v1 显示会话当前活跃任务（与现 ChatWorkbench 单 taskId 语义一致）。
- **L4 octo-mark 蓝色**：`public/octo-mark.svg` 是蓝泡泡（非 Spotify 绿），DesignSync assets 不可达无法核对/替换。深色底做 drop-shadow 调绿，作友好 mascot 视觉可接受；用户如有绿版 mark 可替换。
- **L5 SettingsPage litellm 残留**：F081 退役词遗留在设置页，**F149 territory，F148 未碰**（避免抢范围）。建议 F149 顺手清。
- **L6 右栏「停止」控制 defer**：现有前端无直连 cancel action（仅 `operator.task.cancel` 需 operator inbox `item_id`），造直连停止需碰 backend/operator，超纯前端红线。右栏暴露真实可用的「打开任务详情」；取消/审批走既有 banner/审批中心路径。设计稿的「暂停/停止」按钮 defer 到有 wired cancel action 时。
- **L7 committed dark 移除浅色模式**：v2 是 Spotify committed dark，删了 `prefers-color-scheme` 浅色分支——浅色偏好用户也得深色。属 §M11「纯黑」既定方向，非回归。

## 7. 新增文件清单
- `styles/theme-v2.css`（231）｜`styles/workbench-v2.css`（420）
- `components/chat/SessionRunPanel.tsx` + `.test.tsx`
- `components/shell/GlobalTaskOverlay.tsx` + `.test.tsx`
- 依赖：`@fontsource-variable/figtree` + `remixicon`（package.json/lock）
- 改：`tokens.css` / `main.tsx` / `WorkbenchLayout.tsx` / `ChatWorkbench.tsx` / `App.tsx` / `e2e/selectors.ts`
