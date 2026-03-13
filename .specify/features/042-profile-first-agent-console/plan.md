# Implementation Plan: Feature 042 Profile-First Tool Universe + Agent Console Reset

**Branch**: `codex/feat-042-profile-first-agent-console` | **Date**: 2026-03-13 | **Spec**: `.specify/features/042-profile-first-agent-console/spec.md`  
**Input**: `.specify/features/042-profile-first-agent-console/spec.md` + research/* + Feature 030 / 033 / 035 / 039 / 041 基线 + Agent Zero / OpenClaw 本地参考 + `ui-ux-pro-max` 设计结论

## Summary

042 不做 weather/news 的 case-by-case 修补，而是把默认聊天主链从 `tool-selection first` 改成 `profile-first tool universe`。系统先解析当前 chat/work 绑定的有效 Root Agent profile，再根据该 profile、policy、connector readiness 和治理要求，挂载一组稳定的核心工具宇宙给模型。模型在这个稳定边界里直接选工具；`ToolIndex` 退到长尾 discovery 与 explainability 二线。

前端同步把 `AgentCenter` 从“混合模板、profile、runtime 和术语解释的大页面”重构成真正面向用户的 Agent Console：左栏管理 Root Agents，中栏看 Agent 详情与配置，右栏专看当前运行、工具可见性和 warnings。`ControlPlane` 保留深度审计视图，不再承担主产品导航职责。

## Technical Context

**Language/Version**: Python 3.12+, TypeScript / React 18  
**Primary Dependencies**: FastAPI, Pydantic, SQLite WAL, React, Vite  
**Storage**: SQLite（tasks / works / worker_profiles / control-plane projections）  
**Testing**: pytest, vitest, manual browser smoke  
**Target Platform**: Web workbench + localhost runtime  
**Project Type**: Backend + frontend monorepo  
**Performance Goals**: 默认 chat 不引入额外重查询；tool resolution 主要基于现有 profile、pack、policy 和 readiness 快照  
**Constraints**: 不新造平行 chat runtime；不绕过 ToolBroker / policy / MCP registry；兼容 041 的 `worker_profiles`、legacy `selected_worker_type` 与现有 snapshot  
**Scale/Scope**: 单用户、本地部署、MVP 范围内的一条 profile-first 主链 + 一个更清晰的 Agent Console

## Constitution Check

- **Durability First**: `agent_profile_id`、工具挂载结果、tool resolution trace 必须进入 runtime truth / work metadata / control-plane projection，不能只存在前端 state。
- **Everything is an Event**: chat 绑定 profile、工具挂载与阻止原因、delegation 可见性变化都必须可在任务或 work 维度解释。
- **Tools are Contracts**: 042 改的是工具“挂载顺序”和“展示方式”，不是放开 profile 任意发明工具；所有工具仍来自 capability pack / broker / MCP registry。
- **Least Privilege by Default**: profile-first 不等于把所有工具都塞给模型；只稳定挂载核心工具宇宙，长尾工具仍通过 discovery 进入。
- **Observability is a Feature**: Agent 页面和 ControlPlane 都必须回答“当前 Agent 能做什么 / 不能做什么 / 原因是什么”。

## Project Structure

### Documentation (this feature)

```text
.specify/features/042-profile-first-agent-console/
├── spec.md
├── plan.md
├── data-model.md
├── contracts/
│   └── profile-first-chat-and-console.md
├── research/
│   ├── product-research.md
│   ├── tech-research.md
│   ├── research-synthesis.md
│   └── online-research.md
├── verification/
│   └── acceptance-matrix.md
└── tasks.md
```

### Source Code (repository root)

```text
octoagent/
├── apps/gateway/
│   ├── src/octoagent/gateway/routes/chat.py
│   ├── src/octoagent/gateway/services/task_service.py
│   ├── src/octoagent/gateway/services/agent_context.py
│   ├── src/octoagent/gateway/services/delegation_plane.py
│   ├── src/octoagent/gateway/services/capability_pack.py
│   ├── src/octoagent/gateway/services/control_plane.py
│   ├── src/octoagent/gateway/services/llm_service.py
│   └── tests/
├── packages/
│   ├── core/src/octoagent/core/models/
│   ├── core/src/octoagent/core/store/
│   └── policy/src/octoagent/policy/models.py
└── frontend/
    └── src/
        ├── hooks/useChatStream.ts
        ├── pages/ChatWorkbench.tsx
        ├── pages/AgentCenter.tsx
        ├── pages/ControlPlane.tsx
        ├── components/shell/WorkbenchLayout.tsx
        ├── api/client.ts
        ├── types/index.ts
        └── index.css
```

**Structure Decision**: 继续复用现有 chat / task / agent_context / delegation / control-plane 主链，不新建独立 app。前端不换 router，不增第二套控制台，只重组 `ChatWorkbench + AgentCenter + ControlPlane` 的职责边界。

## Design Decisions

### 1. 先解析 Profile，再挂载工具

- 默认 chat 不再先走 `ToolIndexQuery(limit=5)` 猜工具
- 第一跳先解析 `session > project > system` 有效 `agent_profile_id`
- 再由 profile 的 `selected_tools + default_tool_groups + tool_profile + policy + readiness` 产出 `EffectiveToolUniverse`
- `selected_tools_json` 继续保留，但语义升级为“本次实际挂载给模型的核心工具集”

### 2. ToolIndex 降级为 Discovery / Explainability

- `ToolIndex` 继续保留，用于：
  - 长尾工具发现
  - tool availability 解释
  - ControlPlane trace / debug
- 默认聊天不再依赖 ToolIndex 作为主闸门
- 长尾工具仍允许按需 discover，但不影响稳定主链

### 3. Delegation / Handoff 是核心能力，不是偶发命中

- `workers.review`、`subagents.spawn`、`subagents.list` 等 delegation 核心工具在允许的 profile / policy 下应稳定挂载
- 是否“能不能委派”应由 policy / readiness 明确解释，而不是因为工具没挂上导致模型表现得像不会

### 4. Chat 只做最小 API 扩展

- `ChatSendRequest` 新增可选 `agent_profile_id`
- 前端 chat 可以显式指定 Agent，也可以沿用 session / project 默认绑定
- 不新开第二条专门的 Agent Chat API

### 5. Agent 页面做 IA 重组，而不是继续叠加卡片

基于 `ui-ux-pro-max` 和 Agent Zero/OpenClaw 参考，采用 `Data-Dense Agent Console`：

- **左栏 Root Agents**
  - 默认 Agent
  - Root Agent Library
  - Starter Templates
- **中栏 Agent Detail**
  - Overview
  - Tool Access
  - Instructions / Boundaries
  - Launch / Bind
- **右栏 Runtime Inspector**
  - Current Work
  - Tool Resolution
  - Readiness / Warnings
  - Recent Activity

### 6. ControlPlane 回归深度诊断角色

- `AgentCenter` 负责日常理解和操作
- `ControlPlane` 负责 raw runtime lens、projection、audit 和 lineage 深挖
- 两页共享同一批 canonical resources，不复制同一层信息

### 7. 视觉语言保持现有工作台，但提高信息密度和可理解性

- 保留当前 workbench 外壳与导航骨架
- 降低 Agent 页面营销式大标题和堆叠说明文案
- 强化：
  - 状态 badge
  - 结构化 key-value
  - 工具 access matrix
  - warnings callout
  - keyboard/focus/aria 语义
- 动效以状态过渡和 inspector 切换为主，不引入花哨动画

## Implementation Phases

### Phase 1: Contract Freeze

- 完成 `data-model.md`
- 完成 `contracts/profile-first-chat-and-console.md`
- 冻结 `verification/acceptance-matrix.md`
- 收口 Agent Console 的 IA、命名和状态层级

### Phase 2: Chat Profile Binding

- 扩展 `ChatSendRequest`
- `chat.py` 接收并传递 `agent_profile_id`
- `useChatStream.ts` / `ChatWorkbench.tsx` 支持显示和发送当前 Agent 绑定
- `task_service` / `agent_context` 将绑定显式落入 context resolve 与 runtime metadata

### Phase 3: Effective Tool Universe Backend

- 在 core / gateway 模型中增加 `EffectiveToolUniverse / ToolResolutionTrace / ToolAvailabilityExplanation`
- `capability_pack` 产出 profile-first 核心工具集与 discovery 信息
- `delegation_plane` 把 `tool_index.select` 从“选 5 个工具”改为“解析并挂载核心工具宇宙”
- 稳定 delegation 核心工具挂载策略

### Phase 4: Runtime Truth + Explainability

- `Work` metadata / projection 补 `tool_resolution_mode / trace / warnings`
- `selected_tools_json` 改为实际挂载核心工具列表
- `control_plane` / `delegation` 资源增加 tool availability explainability
- legacy work 做兼容投影

### Phase 5: Agent Console Reset

- `AgentCenter` 重构为三栏布局
- 复用 041 的 `worker_profiles` / Profile Studio，但把“配置编辑”和“运行观察”彻底分开
- 增加 Agent 详情中的 Tool Access / Warning / Binding 区域
- 增加 Runtime Inspector 的 current work / tool resolution / readiness 模块

### Phase 6: Chat + Advanced UX Polish

- Chat 页加入当前 Agent 条带与切换/继承提示
- `ControlPlane` 收口为深度诊断页，突出 tool resolution trace / blocked reasons
- 清理过多说明文案与重复卡片，统一 badge / pill / empty states

### Phase 7: Verification

- pytest 覆盖 chat binding、delegation visibility、tool resolution explainability
- vitest 覆盖 ChatWorkbench / AgentCenter / ControlPlane 新主链
- 手工 smoke 四类 acceptance matrix：实时外部事实、项目上下文、delegation/handoff、runtime diagnostics

## Risks

- 如果只改 `delegation_plane`，但不把 `agent_profile_id` 真正接进 chat 请求，042 会继续出现“Agent 页面和聊天页像两个系统”的问题。
- 如果完全取消工具分层，把 profile 下所有工具都直接塞给模型，会导致上下文膨胀和调用质量下降；必须区分“核心工具宇宙”和“长尾 discovery”。
- 如果 AgentCenter 继续保留 041 时代混合视图，用户会更难理解 profile-first 的价值；因此本轮 UI 重构不能只是换皮。
- 如果 explainability 只放在 ControlPlane，不回流到 Agent 页面，普通用户依然不知道“为什么这次没做到”。
- 如果 `selected_tools_json` 语义升级但测试没同步，容易出现前后端断言与历史记录解读不一致的问题。
