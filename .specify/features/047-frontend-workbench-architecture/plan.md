# Implementation Plan: Feature 047 Frontend Workbench Architecture Renewal

**Branch**: `codex/047-frontend-workbench-architecture` | **Date**: 2026-03-13 | **Spec**: `.specify/features/047-frontend-workbench-architecture/spec.md`  
**Input**: `.specify/features/047-frontend-workbench-architecture/spec.md` + `research/*` + Feature 026 / 035 / 042 / 044 / 046 基线

## Summary

047 不更换前端基础栈，而是完成一次长期主义重整：统一 workbench 数据层、消除 `Advanced` 与主工作台的双轨逻辑、把巨型页面拆为域模块，并建立设计 token、共享 primitive、contract sync 和质量治理基线。产品上，日常工作台与高级诊断彻底分层；技术上，server-state、local draft-state、streaming state 明确分工；工程上，页面文件、共享 hook 和共享 CSS 不再无限膨胀。

## Technical Context

**Language/Version**: TypeScript 5.8+、React 19、Vite 6、React Router 7  
**Primary Dependencies**: React, React Router, Vite；建议引入 TanStack Query 作为 server-state 层  
**Storage**: 浏览器内存状态 + local/session storage（front-door token）；服务端事实源仍为 control-plane canonical resources  
**Testing**: Vitest + React Testing Library；建议补关键工作台 E2E  
**Target Platform**: localhost Web workbench  
**Project Type**: Python backend + React frontend monorepo  
**Performance Goals**: 降低首屏 eager import、收敛巨型页面重渲染范围、确保主路径交互稳定  
**Constraints**: 不换框架、不绕开 canonical control-plane API、不一次性大重写、需兼容 044/046 新页面  
**Scale/Scope**: 1 个长期主义架构型 Feature，覆盖工作台壳层、数据层、域模块、设计系统与测试基线

## Constitution Check

- **Durability First**: 前端不能创建第二套“事实源”；所有状态必须以 canonical resources/actions 为准。
- **Everything is an Event**: 前端只重构消费与展示方式，不绕过事件/任务主链。
- **Least Privilege by Default**: `Advanced` 中的深度诊断信息不应默认暴露在 daily surfaces。
- **Observability is a Feature**: 改造必须保留并增强状态、warnings、runtime truth 的可解释性。

结论：通过。047 属于“前端架构与 UX 分层重整”，不违反现有宪法。

## Project Structure

### Documentation (this feature)

```text
.specify/features/047-frontend-workbench-architecture/
├── research/
│   ├── product-research.md
│   ├── tech-research.md
│   ├── research-synthesis.md
│   └── online-research.md
├── spec.md
├── data-model.md
├── contracts/
│   └── frontend-workbench-renewal.md
├── verification/
│   └── acceptance-matrix.md
├── plan.md
└── tasks.md
```

### Source Code (repository root)

```text
octoagent/frontend/src/
├── App.tsx
├── api/
│   └── client.ts
├── components/
│   ├── shell/
│   │   └── WorkbenchLayout.tsx
│   └── ...
├── domains/
│   ├── workbench/
│   ├── home/
│   ├── chat/
│   ├── work/
│   ├── agents/
│   ├── settings/
│   ├── memory/
│   └── advanced/
├── hooks/
│   └── useWorkbenchSnapshot.ts
├── platform/
│   ├── contracts/
│   ├── queries/
│   └── actions/
├── types/
├── ui/
│   ├── primitives/
│   └── patterns/
└── styles/
    ├── tokens.css
    ├── primitives.css
    ├── shell.css
    └── domains/
```

**Structure Decision**: 保留现有 `frontend/src` 根结构，但在其下新增 `domains / platform / ui / styles` 四层。目标是把当前页面巨石逐步迁出，而不是平地重写。

## Design Decisions

### 1. 保留基础栈，不做框架迁移

- React + Vite + React Router 继续保留
- 047 的问题不是框架能力不足，而是边界和组织纪律不足

### 2. 引入统一 query/action 层

- 建立 `ResourceQueryRegistry` 和 `WorkbenchActionMutation`
- 页面不再直接维护资源刷新矩阵
- `Advanced` 与主工作台统一使用该层

### 3. `Advanced` 回归高级视图

- `AdvancedControlPlane` 不再拥有独立控制台架构
- 它只是 shared data layer 上的一组诊断视图

### 4. 用域模块取代页面巨石

- 每个 domain 负责自己的 page sections、hooks、view-models、tests
- 页面主组件只做组合、布局和入口状态控制

### 5. 建立设计系统，而不是继续扩张全局 CSS

- 把 `index.css` 里的职责拆解出来
- 统一 status badge、inspector、catalog list、section panel、callout、empty state 等 pattern

### 6. 契约同步优先于手写镜像

- 后端已是 canonical resource/action 模型
- 前端应建立 contract export / codegen 或至少集中 schema registry
- 目标是让 `types/index.ts` 不再无限增长

### 7. 复杂度治理作为正式交付项

- 页面 LOC、共享 hook LOC、共享 CSS LOC 都应有约束
- 关键黄金路径测试纳入 Feature 范围，而不是作为后补

## Implementation Phases

### Phase 1: Architecture Freeze

- 冻结 `daily vs advanced` surface 边界
- 冻结目标目录结构
- 冻结 shared query/action/contract/tokens 方案
- 明确迁移策略：渐进替换，而不是一次性重写

### Phase 2: Shared Data Layer

- 抽 `platform/queries` 与 `platform/actions`
- 将 `useWorkbenchSnapshot` 演化为统一 data facade
- 迁移 `Advanced` 到 shared data layer

### Phase 3: UI Foundation

- 落地 `ui/primitives` 与 `ui/patterns`
- 拆分 `index.css` 为 token / primitive / shell / domain 样式
- 定义 page header、status card、catalog list、inspector 等共享模式

### Phase 4: Domain Migration

- 先拆 `Agents`
- 再拆 `Settings`
- 再拆 `Memory`
- 最后收口 `Home / Work / Chat` 的 pattern 对齐

### Phase 5: Contract Sync + Quality Guards

- 建立 contract export / codegen 或集中 schema registry
- 建立 golden-path 测试
- 增加页面/样式体量告警与 review 纪律

## Risks

- `.specify/project-context.*` 软链失效，项目级在线调研规则未加载；后续如修复 project-context，应校准 research policy。
- 如果先拆 UI 不先统一 query/action 层，会造成“看起来模块化，底层仍双轨”的假改善。
- 如果 contract sync 没做，页面拆分后类型漂移会更难排查。
- 如果缺少复杂度治理脚本，重构完成后仍会在后续 Feature 中再次膨胀。
- `ControlPlane.tsx` 迁移量大，需明确允许过渡期 legacy section，但必须带收口计划。

## Gate Note

`GATE_DESIGN` 在 feature 模式下按原规则应暂停等待确认。本轮因用户明确要求“规划一个 Feature 完成这个改造”，这里按“继续到 planning 制品产出”的意图执行；真正进入实现前，应再确认范围与优先级。
