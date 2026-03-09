# Implementation Plan: Feature 035 Guided User Workbench + Visual Config Center

**Branch**: `codex/feat-035-guided-user-workbench` | **Date**: 2026-03-09 | **Spec**: `.specify/features/035-guided-user-workbench/spec.md`
**Input**: `.specify/features/035-guided-user-workbench/spec.md` + Feature 015 / 017 / 025 / 026 / 027 / 030 / 033 / 034 基线 + OpenClaw / Agent Zero 本地参考

---

## Summary

035 不做“第二套控制台”，而是在既有 canonical backend 之上交付一个真正给普通用户使用的 `Guided Workbench`：

1. 重新定义 Web 入口的信息架构，让默认首页从 operator console 变成用户工作台；
2. 把主 Agent / Work / Memory / Channels 的常见配置改成图形化流程，但仍然完全复用 `ConfigSchemaDocument`、wizard 和 action registry；
3. 把聊天、任务、审批、记忆、上下文健康度统一到同一产品壳里；
4. 把当前 `ControlPlane` 降为 `Advanced` 模式，保留所有高级能力。

核心目标不是“更好看”，而是：

> 让已经存在的系统能力终于形成一条普通用户可走通的 Web 主路径。

---

## Technical Context

**Language / Version**:

- TypeScript 5.8
- React 19.1
- React Router 7
- Vite 6
- Python 3.12+（仅在需要增量扩 control-plane resource / action / projection 时）

**Primary Dependencies**:

- 现有 `frontend/src/api/client.ts`
- 现有 `frontend/src/types/index.ts`
- `ControlPlane` canonical routes
- `chat/task/execution` detail routes

**Target Platform**:

- 单 owner、本地或可信内网环境
- 桌面浏览器优先，同时覆盖移动端窄屏

**Testing Strategy**:

- `frontend/src/pages/*.test.tsx`
- `frontend/src/components/*.test.tsx`
- `octoagent/apps/gateway/tests/test_control_plane_api.py`
- `octoagent/apps/gateway/tests/e2e/test_control_plane_e2e.py`
- 新增 frontend e2e / integration smoke

**Constraints**:

- 不得新造平行 control-plane API
- 不得绕过 wizard / config.apply / action registry
- 不得在前端保存 secret 实值
- 不得把 033/034 重新实现到 UI 层
- 不得删除 `Advanced` 控制台

---

## Constitution Check

| Constitution 原则 | 适用性 | 评估 | 说明 |
|---|---|---|---|
| 原则 1: Durability First | 直接适用 | PASS | 配置、任务、审批、context、memory 状态都必须来自 durable backend |
| 原则 2: Everything is an Event | 直接适用 | PASS | UI 发起的 config / approval / work / memory 动作必须继续走 action/event 链 |
| 原则 3: Tools are Contracts | 间接适用 | PASS | 图形化配置不能绕过 schema/action contract |
| 原则 6: Degrade Gracefully | 直接适用 | PASS | wizard/memory/channels/context 未就绪时，工作台必须明确降级而非空白 |
| 原则 7: User-in-Control | 直接适用 | PASS | approval、dangerous config、vault access 仍必须显式确认 |
| 原则 8: Observability is a Feature | 直接适用 | PASS | Chat/Work/Memory 必须可解释“当前做了什么、为什么这样做” |

**结论**: 035 可以直接进入设计与实现，但必须把“canonical backend consumption”作为头号硬门禁。

---

## Project Structure

### 文档制品

```text
.specify/features/035-guided-user-workbench/
├── spec.md
├── plan.md
├── tasks.md
├── checklists/
│   └── requirements.md
├── contracts/
│   ├── guided-workbench-shell.md
│   └── guided-config-chat-contract.md
├── research/
│   ├── product-research.md
│   ├── tech-research.md
│   ├── research-synthesis.md
│   └── online-research.md
└── verification/
    └── verification-report.md
```

### 预期代码与测试变更布局

```text
octoagent/frontend/src/
├── App.tsx
├── index.css
├── api/client.ts
├── types/index.ts
├── components/
│   ├── shell/
│   ├── cards/
│   ├── forms/
│   ├── chat/
│   └── workbench/
├── pages/
│   ├── Home.tsx
│   ├── ChatWorkbench.tsx
│   ├── WorkbenchBoard.tsx
│   ├── MemoryCenter.tsx
│   ├── SettingsCenter.tsx
│   └── AdvancedControlPlane.tsx
└── hooks/
    ├── useWorkbenchSnapshot.ts
    ├── useChatWorkbench.ts
    └── useResponsiveShell.ts

octoagent/apps/gateway/src/octoagent/gateway/services/
├── control_plane.py
└── (仅在 resource/action/ui_hints 缺口时增量扩展)

octoagent/apps/gateway/tests/
├── test_control_plane_api.py
└── tests/e2e/test_control_plane_e2e.py
```

---

## Architecture

### 1. Guided App Shell

新增统一 app shell：

- 顶部状态条：当前 project、系统状态、memory 状态、待确认数量、最近错误
- 左侧主导航：`Home / Chat / Work / Memory / Settings / Advanced`
- 移动端抽屉式导航
- 全局 `selected project`、`frontdoor auth`、`snapshot refresh`、`pending actions` 状态

该 shell 的首屏事实源固定为 `fetchControlSnapshot()`。

### 2. Presentation Adapters，而不是平行 DTO

前端可以建立轻量 `view-model adapters`，但只能做：

- canonical resource -> 用户卡片
- action result -> toast / inline result
- task/execution detail -> chat/work detail 面板

不能做：

- 新的持久化 schema
- 前端私有“真实状态”
- 未经 backend 确认的 optimistic fake runtime

### 3. Guided Configuration Flow

设置中心按用户语言组织，但底层继续吃 backend schema：

- `Main Agent`
  - provider / model / policy / profile summary
- `Work`
  - delegation / approval / runtime 默认行为
- `Memory`
  - backend / maintenance / vault / visibility
- `Channels`
  - readiness / pairing / mode / token ref

关键设计决策：

- 表单分组来自 `ui_hints.section` 和 feature 内新增的 grouping map
- 字段说明、人话标签、风险提示来自 backend hint；缺少时补 hint，不在前端硬写长期真相
- 保存只走 `config.apply`

### 4. Chat Workbench

聊天工作台不是简单的消息列表，而是一个三栏工作区：

- 左栏：会话列表 / 最近活动 / 待你确认
- 中栏：消息流、输入框、任务状态、停止/继续、执行输入
- 右栏：上下文抽屉
  - 当前 task
  - 当前 work
  - memory 摘要
  - 033 provenance
  - 034 compaction
  - tool/runtime health

真实链路：

1. `POST /api/chat/send`
2. `EventSource(/api/stream/task/{task_id})`
3. 读取 `task detail` / `execution session`
4. 以 `sessions/delegation/memory` 资源刷新右栏
5. 所有控制按钮仍走 action registry 或 execution input route

### 5. Work Board

Work 页面按用户可理解的状态呈现：

- `进行中`
- `等待确认`
- `可合并`
- `失败 / 需重试`
- `已完成`

背后仍由 `SessionProjectionDocument` + `DelegationPlaneDocument` 驱动。

重点不是重做 delegation，而是把：

- parent/child work
- runtime kind
- selected tools
- retry / merge / escalate

变成可扫读、可操作、可解释的 UI。

### 6. Memory Center

Memory 首页先回答三个问题：

1. 系统当前记住了哪些主题？
2. 这些记忆是否可信、是否待确认？
3. 是否涉及受保护内容？

然后再渐进展开：

- subject history
- proposal audit
- vault authorization
- maintenance / degraded state

### 7. Advanced Mode Coexistence

现有 `ControlPlane` 不删除：

- 保留为高级模式或诊断模式
- 从新 shell 进入
- 深链接继续可用

这样可以避免为了“简洁”牺牲掉 operator / diagnose 能力。

### 8. Design System Direction

这轮 UI 不能继续走“每页自己写 inline style”的路径，必须冻结一套正式视觉系统：

- 统一 CSS variables / tokens
- 明确层级：shell、surface、card、panel、drawer、form
- 清晰功能色：正常 / 待确认 / 警告 / 错误 / 降级
- 文案使用人话标签；底层术语仅在 advanced/detail 里出现
- 移动端优先考虑抽屉、底部操作区和 sticky status bar

允许引入轻量 headless primitives，但前提是：

- 不破坏当前包体和构建复杂度
- 保持无障碍和键盘交互
- 不把样式系统外包成“库默认长相”

---

## Interface Strategy

### 必须直接复用的接口

- `GET /api/control/snapshot`
- `GET /api/control/resources/*`
- `POST /api/control/actions`
- `GET /api/control/events`
- `POST /api/chat/send`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/execution`
- `POST /api/tasks/{task_id}/execution/input`

### 允许的增量 backend 变更

只允许做以下两类增量：

1. **补字段 / hint / ref**
   - 例如 `ConfigSchemaDocument.ui_hints`
   - `SessionProjectionItem.detail_refs`
   - `DelegationPlaneDocument.summary`
   - 033/034 context/compaction projection

2. **补 action registry 中缺失的 canonical action**
   - 例如 wizard step resume / next
   - 但仍必须通过 `/api/control/actions`

### 禁止事项

- 新建 `GET /api/workbench/home`
- 新建 `POST /api/settings/save`
- 新建 `GET /api/chat/context`
- 在 frontend localStorage 里持久化真实 config draft 作为事实源

---

## Phase Plan

### Phase 0: Contract Freeze & Anti-Fake Gates

目标：

- 冻结 IA、canonical API 边界、route map、page-level data/action matrix
- 先补测试，防止后续“做成漂亮 demo”

### Phase 1: Shell & Design System

目标：

- App shell、导航、tokens、layout、responsive 基线落地
- `/` 不再直接进入 operator console

### Phase 2: Guided Home & Settings

目标：

- 首页 readiness / next actions
- 设置中心图形化配置
- wizard / project / diagnostics / channel readiness 接线

### Phase 3: Chat Workbench

目标：

- 用真实 chat/task/execution/session/work/memory 链路拼出聊天工作台
- approval / execution input / 033 provenance / 034 compaction 接进右侧上下文抽屉

### Phase 4: Work & Memory Centers

目标：

- Work 看板和 detail drawer
- Memory 摘要页与渐进细节

### Phase 5: Advanced Mode & Verification

目标：

- 收编旧 ControlPlane
- 做 frontend/backend/e2e 验证
- 输出最终验收报告

---

## Risks & Tradeoffs

### Tradeoff 1: 不直接重写 backend，可以节省成本，但会要求前端更严格地尊重 canonical contract

- 选择：优先尊重 026 contract；只有确实缺字段时才增量扩 backend

### Tradeoff 2: 小白模式与高级模式并存，信息架构复杂度更高

- 选择：保留 `Advanced`，因为删掉它会直接牺牲 operator / diagnose 能力

### Tradeoff 3: 035 必须依赖 033/034 的输出才能完整展示主 Agent 上下文健康度

- 选择：明确依赖，不在 035 中重做领域逻辑；在 033/034 未到位时显式 degraded

### Tradeoff 4: 视觉正规化需要做 design system，不是简单页面替换

- 选择：先统一 tokens、layout、form/card/drawer primitives，再大面积替换页面

---

## Release Gates

035 完成前必须同时满足：

1. 根路由不再是资源导向 operator console
2. 至少一条图形化配置保存路径真实走 `config.apply`
3. 至少一条聊天路径真实走 `chat.send -> SSE -> task/execution/context`
4. 至少一条 work 动作真实走 `work.*` action
5. 至少一条 memory 操作真实走 `memory.* / vault.*` action
6. `Advanced` 模式仍然可用
7. 033/034 缺失时 UI 显式 degraded，不伪造 context/compaction truth
