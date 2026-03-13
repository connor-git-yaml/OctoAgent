# Implementation Plan: Feature 045 Memory Panel Clarity Refresh

**Branch**: `codex/045-memory-panel-clarity` | **Date**: 2026-03-13 | **Spec**: `.specify/features/045-memory-panel-clarity/spec.md`  
**Input**: `.specify/features/045-memory-panel-clarity/spec.md` + `octoagent/frontend/src/pages/MemoryCenter.tsx` + `octoagent/frontend/src/pages/SettingsCenter.tsx` + `octoagent/frontend/src/index.css` + `octoagent/frontend/src/App.test.tsx`

## Summary

本次 story 不改 Memory 后端契约，只重构 `/memory` 的产品表达和设置跳转：

1. 用 `memory + config + setup` 推导用户状态，先回答“Memory 现在有没有工作”。
2. 把内部术语与 raw ID 从主视图里拿掉，保留真正可理解的记录摘要和下一步动作。
3. 给出最小配置说明，并把用户引到 `Settings > Memory` 的现成入口，而不是展开 backend 实现细节。
4. 为 Settings 增加 hash deep-link 滚动，保证 Memory 页给出的入口是可落地的。

## Technical Context

**Language/Version**: TypeScript, React 19, Vitest  
**Primary Dependencies**: React Router, Workbench snapshot resources, existing `submitAction("memory.query" | "memory.flush")` flow  
**Storage**: 不新增前端持久化；继续消费 control-plane snapshot 与 action 结果  
**Testing**: `vitest` + React Testing Library  
**Target Platform**: Workbench Web UI（桌面优先，同时兼容移动端）  
**Project Type**: Monorepo frontend page refresh  

## Constitution Check

- **Durability First**: 不新增旁路写入；仍通过 canonical memory actions 刷新/整理视图。
- **Everything is an Event**: 继续复用 `memory.query`、`memory.flush` 等既有 action，不绕过 control-plane。
- **Degrade Gracefully**: 重点就是把 degraded / fallback 状态对用户解释清楚，而不是隐藏。
- **User-in-Control**: 只展示最小可操作项，不把 backend 部署细节压给普通用户。
- **Observability is a Feature**: 让“当前是否工作、为什么、下一步做什么”成为首屏内容，而不是只暴露原始 diagnostics。

## Design Direction

- 视觉上延续 workbench 已有信息卡 + panel 语言，不新造设计体系。
- 文案目标是“短句 + 直接动作”，避免 `scope / index / backend / flush` 这类实现名词直接上屏。
- 高级能力和排障仍可达，但退居二级入口，不占首屏主叙事。

## Code Impact

### Primary Files

- `octoagent/frontend/src/pages/MemoryCenter.tsx`
- `octoagent/frontend/src/pages/SettingsCenter.tsx`
- `octoagent/frontend/src/index.css`
- `octoagent/frontend/src/App.test.tsx`

### Main Refactors

1. **状态推导层**
   - 基于 `memory.backend_state`
   - 基于 `config.current_value.memory`
   - 基于现有记录/summary/warnings 推导用户状态

2. **Memory 页面 IA 重排**
   - Hero 改为“状态 + 下一步 + 模式说明”
   - 清理内部术语与 raw IDs
   - 保留筛选和记录列表，但改成用户语言

3. **Settings Deep Link**
   - 从 Memory 页链接到 `Settings > Memory`
   - Settings 页面按 hash 自动滚动到 `settings-group-memory`

4. **测试更新**
   - 覆盖新状态标题和最小配置指引
   - 覆盖去术语化文案
   - 覆盖 Settings anchor 可达性

## Verification Strategy

- 更新 `App.test.tsx` 的 Memory 页测试，验证新的标题、指引和去术语化结果。
- 新增或扩展对 `Settings#settings-group-memory` hash 行为的断言。
- 运行定向 Vitest，必要时补充一次前端 build 级验证。
