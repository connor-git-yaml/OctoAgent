# 技术调研：Frontend Workbench Architecture Renewal

**特性分支**: `codex/047-frontend-workbench-architecture`  
**日期**: 2026-03-13  
**范围**: OctoAgent 前端技术选型、代码组织、状态管理、契约同步、测试与性能基线  
**输入**: 当前仓库 `octoagent/frontend/`、公开仓库 OpenClaw / Agent Zero / OpenHands / Open WebUI / LibreChat

## 1. 当前前端现状

### 技术栈

- React 19 + React Router 7 + Vite 6
- TypeScript
- 当前没有正式的 server-state library
- API 与控制面类型主要手写在 `src/types/index.ts`
- 样式集中在 `src/index.css`

### 结构性问题

1. **页面文件体量失控**
   - `AgentCenter.tsx` 约 4553 行
   - `ControlPlane.tsx` 约 3970 行
   - `SettingsCenter.tsx` 约 1824 行
   - `index.css` 约 3255 行

2. **数据流双轨并存**
   - 新工作台使用 `WorkbenchLayout + useWorkbenchSnapshot`
   - `AdvancedControlPlane -> ControlPlane` 仍自带一套 snapshot/resource/action orchestration

3. **手写前端契约漂移风险高**
   - 后端已是 canonical resource/action 模型
   - 前端仍手写资源类型、路由名和 payload 结构

4. **server-state、本地 draft-state、流式运行态没有明确分层**
   - `useWorkbenchSnapshot` 同时承担 fetch、refresh、action、error、auth error
   - 大页面里继续叠加大量 `useState` 与衍生逻辑

5. **样式没有形成 token / primitive / domain 分层**
   - `index.css` 同时承担 reset、layout、组件、页面视觉细节

## 2. 外部技术路线比较

### OpenClaw

- 路线：平台型 TypeScript monorepo + 原生客户端 + plugin SDK
- 关键特征：
  - 有强约束的质量脚本
  - 多 surface 共享核心契约
  - 通过代码体量门槛防止单文件膨胀
- 适用启发：
  - 前端只是一个 surface，应该复用 core contract，而不是自己演化出第二套状态真相

### Agent Zero

- 路线：后端驱动 + 模板化 Web UI + 原生 JS/HTML store
- 优点：
  - 透明、直观、接近 runtime
- 缺点：
  - 缺少现代前端架构边界
  - 难承载长期复杂协作
- 适用启发：
  - runtime 透明值得学
  - 实现方式不适合作为 OctoAgent 的长期基线

### OpenHands

- 路线：React Router + TanStack Query + Redux + Tailwind + i18n + MSW
- 优点：
  - server-state 与 app-state 分离更清楚
  - 路由、mock、测试与类型体系完整
- 适用启发：
  - OctoAgent 最值得借鉴的是 Query 层和域目录，而不是简单换皮

### Open WebUI

- 路线：SvelteKit + `apis / components / stores / routes`
- 优点：
  - 域分层清晰
  - 页面组织与 API 客户端分离明显
- 风险：
  - 功能广度过大时仍会出现组件森林
- 适用启发：
  - 域模块化是必要的，但还需要更强的设计系统与边界约束

### LibreChat

- 路线：React + Vite + React Query + Recoil + Jotai + Context 混合
- 优点：
  - 富能力聊天交互完整
- 风险：
  - 状态体系并存导致复杂度上升
- 适用启发：
  - 应避免在 OctoAgent 中同时引入多种状态模型

## 3. 技术选型结论

### 不建议做的事

- 不建议重写到 Next.js、Remix、SvelteKit
- 不建议一次性导入多个状态库
- 不建议继续把所有业务演进压在单页面文件里
- 不建议长期手写完整后端契约镜像

### 建议保留

- React + Vite + React Router
- 现有 `WorkbenchLayout` 壳层
- control-plane canonical resources/actions 的后端事实源

### 建议新增

1. **TanStack Query 作为 server-state 层**
   - 统一 snapshot/resource query key
   - 统一 action 后 invalidation / refresh 策略
   - 替代手工 `refreshSnapshot / refreshResources` 分发

2. **域模块目录**
   - `src/domains/home`
   - `src/domains/chat`
   - `src/domains/work`
   - `src/domains/agents`
   - `src/domains/settings`
   - `src/domains/memory`
   - `src/domains/advanced`
   - `src/platform/api`
   - `src/ui/primitives`
   - `src/ui/patterns`

3. **契约生成或半自动同步**
   - 从后端 canonical model 导出前端 schema/type
   - 至少把资源名、action payload、关键文档结构纳入生成链

4. **设计系统分层**
   - `tokens.css`
   - `primitives.css`
   - `shell.css`
   - `domain/*.css` 或 CSS module 等等

5. **路由级 lazy loading**
   - `Agents`、`Settings`、`Advanced` 不应全部首屏 eager import

6. **前端代码体量硬约束**
   - 页面文件、共享 CSS 文件、超大 hook 文件均应设上限

## 4. 推荐的状态模型

### Server State

- 由 Query 层管理
- 包括 snapshot、资源资源片段、历史列表、详情页读取
- 具有缓存、失效、重取与错误态

### Local Draft State

- 仅承载表单草稿、section 展开状态、选择器 UI、临时筛选
- 限定在域内 hooks / reducers

### Streaming / Ephemeral Runtime State

- chat stream、SSE、approval pending toast、live status
- 独立于 Query 层，但能把最终状态回写到 Query invalidation

## 5. 测试与性能建议

### 测试

- 保持 Vitest + RTL 为组件和 hook 主力
- 增加 6-10 条工作台黄金路径 E2E
- 用 mock resource fixtures 取代大量手拼 snapshot

### 性能

- 路由级分包
- 控制大页面 re-render 范围
- 对大型列表和 inspector 做视图层虚拟化或延迟渲染
- 在引入优化前先加 profiler 基线

## 6. 结论

技术上最优路径不是换栈，而是做四件事：

1. 统一数据层
2. 拆域模块
3. 同步契约
4. 建立设计系统与代码体量纪律

只要这四件事落地，当前 React/Vite 基线完全可以支撑长期演化。
