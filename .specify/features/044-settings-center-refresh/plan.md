# Implementation Plan: Feature 044 Settings Center Refresh

**Branch**: `codex/044-settings-center-refresh` | **Date**: 2026-03-13 | **Spec**: `.specify/features/044-settings-center-refresh/spec.md`  
**Input**: `.specify/features/044-settings-center-refresh/spec.md` + `octoagent/frontend/src/pages/SettingsCenter.tsx` + `octoagent/frontend/src/index.css` + 既有 `setup.review / setup.apply / provider.oauth.openai_codex / setup.quick_connect` 前端链路

## Summary

044 不改后端配置契约，只重构 Settings 页面的产品表达和前端交互：

1. 重新梳理页面 IA，改成单一主内容流，不再依赖右侧 Butler rail。
2. 把 Provider 区域改成真正的多实例管理器，并把 alias provider 改为可选列表。
3. 清理所有 Butler/Agents 迁移话术和相关模块。
4. 保留 review/apply/quick connect/OAuth connect 动作，但放到新的“保存检查”与顶部动作区中。

## Technical Context

**Language/Version**: TypeScript, React 18, Vitest  
**Primary Dependencies**: React Router, Workbench snapshot resources, existing SettingsCenter helper functions  
**Storage**: 仍写回 canonical `config` / `skill_selection` / `secret_values`  
**Testing**: `vitest` + React Testing Library  
**Target Platform**: 本地 Workbench Web UI（桌面优先，同时兼容移动端）  
**Project Type**: Monorepo frontend page refresh  
**Constraints**:

- 不新增 settings backend 或并行 resource
- 不修改 `providers` / `model_aliases` 的序列化格式
- 不在本 Feature 内重做 Agents 页面
- 需兼容已有 `setup.review` / `setup.apply` / `setup.quick_connect` / `provider.oauth.openai_codex`

## Constitution Check

- **Durability First**: 所有改动仍通过既有 apply/quick-connect 写入 canonical config，不引入前端孤岛状态。
- **Everything is an Event**: 页面继续复用 control-plane action 主链，不绕过 `setup.review / apply`。
- **Least Privilege by Default**: 删除 Butler 相关表述后，Settings 只暴露平台级配置，边界更清晰。
- **Degrade Gracefully**: 页面在没有 Provider、只有一个 Provider、OAuth 未连接等状态下都必须可继续编辑。
- **Observability is a Feature**: review 风险统一收口到“保存检查”，让用户知道为什么不能保存。

## Design Direction

- 设计基线采用 `ui-ux-pro-max` 的 `Enterprise Gateway / Trust & Authority` 方向。
- 视觉目标是更像控制台而不是 onboarding 长文档：标题短、卡片少量高密度、说明尽量只保留一层。
- 桌面端用清晰的分区和卡片层级，移动端折叠成单列，不保留 sticky rail 依赖。

## Code Impact

### Primary Files

- `octoagent/frontend/src/pages/SettingsCenter.tsx`
- `octoagent/frontend/src/index.css`
- `octoagent/frontend/src/App.test.tsx`

### Main Refactors

1. **页面结构重排**
   - 精简 hero 和 summary
   - 新增 section 导航/概览
   - 删除右侧 Butler rail，改为底部统一 review/action panel

2. **Provider 多实例管理**
   - 提供多 Provider 列表编辑
   - 支持新增 preset/custom Provider、删除、启停、默认顺序
   - OAuth Provider 维持连接按钮，API Key Provider 维持 secret 输入

3. **Alias provider 选择器**
   - alias 的 provider 从文本输入改为 select
   - Provider 删除时同步修正 alias 引用

4. **Settings 文案清理**
   - 清除 Butler、迁移、平台内部调试语句
   - 压缩重复解释型文本

## Verification Strategy

- 更新现有 Settings 测试断言，覆盖新标题、新分区和去 Butler 化结果。
- 增加/改写多 Provider 交互断言，验证新增 Provider 后 alias 可选择新 provider。
- 跑 `vitest` 定向回归，必要时配合 Playwright 本地页面快照复检结构。
