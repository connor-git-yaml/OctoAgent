# Frontend Workbench 模块

当前 Web UI 对应 [`octoagent/frontend`](../../../octoagent/frontend)。  
这一层的核心思路不是“每个页面自己打各自 API”，而是：

**先拿一份 control-plane snapshot，再在前端做 domain projection、交互 orchestration 和必要的局部刷新。**

## 1. 当前前端的主要分层

可以先把当前前端分成三层：

1. **Shell / Layout**
   - 工作台框架、导航、全局状态入口

2. **Platform**
   - API client
   - control-plane resource/query/action 合约
   - snapshot 刷新逻辑

3. **Domain**
   - Settings
   - Agents
   - Memory
   - Work
   - Chat

这使前端不是简单页面堆叠，而是“平台能力 + 领域投影”的结构。

## 2. `WorkbenchLayout`: 用户看到的主工作台骨架

位置：[`WorkbenchLayout.tsx`](../../../octoagent/frontend/src/components/shell/WorkbenchLayout.tsx)

### 2.1 `useWorkbench()` / `useOptionalWorkbench()`

职责：

- 从 `WorkbenchContext` 暴露全局工作台数据

这说明当前前端把 snapshot、action 和刷新能力收进了统一上下文，而不是每个页面重复获取。

### 2.2 `WorkbenchLayout`

实现逻辑：

1. 调用 `useWorkbenchData()` 拉取 snapshot
2. 根据 loading / authError / error 决定 shell 首屏状态
3. 组织导航、session 列表、页面 outlet
4. 根据 snapshot 派生工作台状态文案

这使它不仅是 UI 壳，还承担了“把底层状态翻译成用户语言”的工作。

### 2.3 `ChatNavSection`

职责：

- 从 session projection 构建左侧会话列表
- 区分 owner agent、executor、运行中状态

说明 session projection 已经是前端一级对象。

## 3. `useWorkbenchData()`: snapshot 和 action 的前端中枢

位置：[`useWorkbenchData.ts`](../../../octoagent/frontend/src/platform/queries/useWorkbenchData.ts)

这是当前前端最关键的 hook。

### 3.1 `refreshSnapshot()`

职责：

- 拉取整份控制面 snapshot
- 统一处理 loading / authError / error

### 3.2 `refreshResources()`

职责：

- 对已有 snapshot 做局部资源刷新
- 在失败时回退到全量刷新

这说明前端不是每次动作后盲目整页重载，而是尽量走资源级刷新。

### 3.3 `submitAction()`

实现逻辑：

1. 记录 `busyActionId`
2. 根据 action id 构造刷新策略
3. 调 `executeWorkbenchActionWithRefresh()`
4. 保存 `lastAction`
5. 根据资源失效规则刷新 snapshot 或局部资源

这使控制面 action 成为前端交互的统一写路径。

## 4. Settings：当前配置治理主入口

### 4.1 `SettingsPage`

位置：[`SettingsPage.tsx`](../../../octoagent/frontend/src/domains/settings/SettingsPage.tsx)

`SettingsPage` 不是简单的 schema form。它当前实际承担的是：

- 配置草稿构建
- review / apply / quick_connect 三段式配置流
- provider、alias、memory、gateway runtime 等多块联动

#### `buildSetupDraft()` 的调用链

虽然 `buildSetupDraft()` 是组件内部函数，但它代表当前页面的核心逻辑：

1. 先把 field state 转成标准配置 payload
2. 同时整理 secret/env 状态
3. 将配置错误回写到 fieldErrors
4. 输出可交给 `setup.review` / `setup.apply` / `setup.quick_connect` 的 draft

#### `handleReview()`

职责：

- 提交 `setup.review`
- 更新 review summary

#### `handleApply()`

职责：

- 先 review，再 apply
- 判断配置改动是否需要 runtime refresh

#### `handleQuickConnect()`

职责：

- 走更高层的一键接通路径
- 在需要时处理 OpenAI OAuth provider 的专门连接动作

这三者说明当前 Settings 已经是配置治理流程，而不是普通设置页。

### 4.2 `shared.tsx`

位置：[`shared.tsx`](../../../octoagent/frontend/src/domains/settings/shared.tsx)

这里是 Settings 的纯函数支撑层。

#### `buildFieldState()`

职责：

- 从 config schema hints 和当前配置值构建页面 field state

#### `buildConfigPayload()`

职责：

- 把页面 field state 转回标准配置对象
- 同时返回字段错误

这两个函数把“控制面 schema 文档”和“前端编辑状态”解耦开了。

#### `parseProviderDrafts()` / `stringifyProviderDrafts()`

职责：

- 在 Settings 页面内部，把 provider 列表草稿做可编辑态与存储态之间的转换

#### `buildDefaultAliasDrafts()`

职责：

- 为当前默认 provider 生成推荐 alias 模板

## 5. Agents：前端视图模型推导层

位置：[`agentManagementData.ts`](../../../octoagent/frontend/src/domains/agents/agentManagementData.ts)

这不是一个“工具函数集合”，它实际上承担了 Agent 管理页的数据整形层。

### 5.1 `deriveAgentManagementView()`

职责：

- 从控制面 snapshot 推导出：
  - 主助手卡片
  - project agents
  - builtin templates
  - 默认 profile

这一步把后端资源文档变成了真正适合页面渲染的 view model。

### 5.2 `buildAgentEditorDraftFromProfile()` / `buildAgentEditorDraftFromTemplate()`

职责：

- 把已有 profile 或模板转成前端编辑草稿

### 5.3 `buildAgentPayload()`

职责：

- 把前端编辑草稿转回后端 action payload

### 5.4 `buildModelAliasOptions()`

职责：

- 从 snapshot 中读取当前真实可用 alias 列表

这说明前端 Agent 编辑器已经和运行时 alias 配置耦合到同一套事实源里。

## 6. 当前前端架构的关键现实

### 6.1 Snapshot-first

当前工作台最重要的设计不是“每页单独查接口”，而是：

- 先取 control-plane snapshot
- 再做 domain projection
- action 后按资源失效规则刷新

### 6.2 Domain projection 很重

例如：

- Settings 里要从 schema -> field state -> config draft -> review/apply 结果
- Agents 里要从 snapshot -> editor draft -> payload

这意味着前端已经有一层明确的数据转换逻辑，不是纯渲染层。

### 6.3 UI 不是直接暴露内部实现细节

从 `WorkbenchLayout` 的状态文案可以看出来，当前前端在努力把：

- runtime mode
- diagnostics
- active works
- pending approvals

翻译成更接近用户语言的提示。

这符合项目里“Web 默认面向普通非技术用户”的约束。

## 7. 维护时最该注意什么

### 7.1 不要绕过 `useWorkbenchData()`

否则容易出现：

- action 后页面状态不一致
- busy/error/auth 处理重复实现
- 局部刷新规则失效

### 7.2 不要让页面组件直接承担所有数据拼装

当前正确方向是：

- platform hook 负责 snapshot/action
- domain 纯函数负责 projection/payload
- 页面负责交互编排

### 7.3 不要把控制面 schema 和页面 field state 混成一个对象

Settings 现有结构已经说明：

- schema 文档来自后端
- field state 是前端编辑态
- config payload 是提交态

这三层混在一起会让配置页非常难维护。

## 8. F149 Web Pages v2 的稳定边界

F149 将审批、任务、自动化、设置、智能体、记忆、文件、技能和 MCP 十个页面统一到
F148 shell，但没有为每页创建独立 service、transport 或 store。稳定调用方向为：

```text
OpenAPI generated wire types
  → api adapter / runtime decoder
  → platform queries + actions
  → pure projection / resource state
  → page + WorkbenchContext composition
```

generated type 只停留在 adapter boundary。页面消费的是经过 decoder 和 projection
收敛的 view model；`unknown`、递归 JSON、raw task event 和 action schema 不得直接进入
普通 UI。未知、历史或扩展 task event 经净化后只进入默认收起的 Advanced diagnostics，
不驱动业务状态。

### 8.1 唯一 transport 与 action seam

- `src/api/client.ts` 是页面业务请求的唯一 transport。
- `src/platform/queries/useWorkbenchData.ts` 维护 snapshot/resource 读取与共享状态。
- `src/platform/actions/f149Actions.ts` 提供有限、typed 的 F149 action seam。
- 页面和 domain projection 不得直接 `fetch`、自建 token/header helper 或可选 fallback。
- build version watcher 属 shell 基础设施，不是页面业务 transport。

这条边界由 `scripts/check-f149-boundaries.mjs` 与 component/L1 合同共同保护。新增 endpoint
时先扩展 generated contract、adapter 和 application seam，不在页面旁边创建临时 service。

### 8.2 401 与 403 的 ownership

F150 唯一拥有 Web Access Gate：

- 未登录、HTTP 401、session 过期与登出进入 global shell owner。
- canonical client 在 401 时发出内部 `octoagent:front-door-auth-error` 浏览器信号；
  `useWorkbenchData()` 清除失效 snapshot，由既有 `WorkbenchLayout` 自然显示
  `FrontDoorGate`。
- 该信号是 client→platform query 的 private DOM protocol，不是公开 API、第二 store
  或页面订阅机制；不得扩散到第三个 owner。

F149 页面只拥有已登录用户的 origin 403 / resource permission。403 保持 shell，
显示普通语言的资源权限不足，不提供 owner 身份切换或重新登录动作。404 与 409 分别进入
not-found 与 conflict mapper，不并入 auth error。

### 8.3 敏感信息

- secret value 在 Gateway/application boundary 移除，不下发给前端。
- UI 只接收变量名、是否已配置和服务端生成的脱敏摘要。
- edit/create 采用 write-only `keep / replace / remove` mutation；redacted placeholder
  不得回写，提交后清空临时 input。
- response、SSE、错误、日志、DOM、clipboard 与测试 evidence 都不得回显 secret。
- path/command 默认属于 operator-sensitive：普通区域隐藏；Advanced 只能展示经过净化、
  截断且权限允许的摘要。

### 8.4 视觉与客户端边界

Claude Design 最初方案是 Web 与未来 iOS 的共同视觉/交互基线。F148 `--cp-*` 负责共享
主题与组件约束，但现有旧 Web 外观不是基线。页面实现应适配设计的层级、留白、卡片节奏、
信息密度、视觉张力与排版；只有明确功能合同、可用性或无障碍需要才允许偏离并记录原因。

390px 只用于桌面 Web 窄窗口 overflow/focus/reduced-motion 健壮性，不是手机产品入口。
手机产品只走原生 iOS App；iOS 延续同一视觉语言，但使用 SwiftUI / Apple 原生导航、
手势、控件与无障碍语义，不复制 Web 组件结构。

### 8.5 Chat 与 Task Detail 的共享 SSE 边界

`/api/stream/task/{task_id}` 同时服务 Chat scripted loop 与 Task Detail，但两个消费者的
投影职责不同：

- Gateway 只允许有限且可校验的 `state_transition`、`artifact` 与
  `model_call_started / completed / failed` 字段通过。model-call 只保留有限长度的
  `response_summary`、`error`、`skill_id` 和 `artifact_ref`，不得透传 token、
  provider raw payload、工具参数、secret 或其他任意字段。
- Chat 消费上述安全的 model-call payload，用 `response_summary` 或净化后的 `error`
  完成对话回复；不得从原始 provider 事件恢复被 Gateway 丢弃的字段。
- Task Detail 的 runtime decoder 将 model-call 仅投影为 Advanced diagnostics 中的
  `started / completed / failed` phase，不保留回复正文，也不得据此驱动任务阶段、
  页面状态、artifact 或新增普通界面。
- 历史或畸形事件只能降级为有界、净化的 diagnostic；不得因兼容需要恢复无 schema 的
  raw JSON 通道。

因此，扩展 task SSE 时必须同时验证 Gateway finite projection、Chat reducer 与
Task Detail decoder，不能用统一的“全部 diagnostic 化”替代消费者各自的安全合同。
