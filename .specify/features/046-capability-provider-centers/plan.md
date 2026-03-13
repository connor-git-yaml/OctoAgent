# Implementation Plan: Feature 046 Capability Provider Centers

**Branch**: `codex/046-capability-provider-centers` | **Date**: 2026-03-13 | **Spec**: `.specify/features/046-capability-provider-centers/spec.md`  
**Input**: `.specify/features/046-capability-provider-centers/spec.md` + `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` + `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` + `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_registry.py` + `octoagent/frontend/src/pages/SettingsCenter.tsx` + `octoagent/frontend/src/pages/AgentCenter.tsx` + `octoagent/frontend/src/index.css` + `octoagent/frontend/src/App.tsx`

## Summary

046 需要同时重构产品信息架构、扩展控制面资源/动作，并把 agent 级能力治理真正接进 runtime：

1. 把 `Skills` / `MCP` 从 `SettingsCenter` 中拆成独立 catalog 页面。
2. 为 Skills 增加自定义 provider catalog 存储与 control-plane CRUD。
3. 为 MCP 增加 server catalog 读写动作，并复用现有 registry refresh。
4. 在 `Agents` 页面为 Butler / Worker 模板加入 capability provider 勾选。
5. 让 capability pack/tool filtering 读取 agent/profile metadata，真正按 Agent 限制可见能力。

## Technical Context

**Language/Version**: Python 3.12+, TypeScript, React 18  
**Primary Dependencies**: FastAPI control plane, CapabilityPackService, McpRegistryService, React Router, React Testing Library  
**Storage**:

- MCP catalog 继续使用现有 registry 配置文件
- 自定义 skill catalog 新增 canonical 存储文件
- agent/profile selection 存入 `AgentProfile.metadata` / `WorkerProfile.metadata`

**Testing**: `pytest`, `tsc -b`, `vitest`  
**Target Platform**: Workbench Web UI + control-plane canonical backend  
**Project Type**: Monorepo full-stack feature  
**Constraints**:

- 不重做现有 `capability_pack` / `skill_governance` 主链，只做增量扩展
- 不引入平行 REST 体系，必须继续走 control-plane resources/actions
- MCP runtime truth 不能因为 catalog 编辑失败而整体不可用
- 自定义 skills 必须成为真实 registry item，而不是仅前端 mock
- 设计风格参考简约的 catalog/list 结构，但仍需保持仓库现有 Workbench 视觉语言

## Constitution Check

- **Durability First**: MCP catalog、自定义 skill catalog、Agent 选择都必须落到 canonical 持久层；不能只存在 snapshot 内存态。
- **Everything is an Event**: 所有保存/删除动作继续走 control-plane action，保留 action result 与 resource refresh 审计链。
- **Tools are Contracts**: Skill provider / MCP provider 的配置模型必须是强类型结构，前后端对同一字段含义保持一致。
- **Least Privilege by Default**: capability providers 只是“可安装 catalog”；真正可用范围仍由 Agent/Worker 单独勾选控制。
- **Degrade Gracefully**: catalog 文件缺失、MCP server 配置错误、自定义 skill 不可用时，页面必须降级但可继续编辑。
- **Observability is a Feature**: 每个 provider 页面都要展示 availability、error、tool count / model alias / worker type 等解释信息。

## Design Direction

- 页面结构采用 `ui-ux-pro-max` 的 `Swiss Modernism 2.0 + Minimal & Direct` 思路，但保留现有 Workbench 的暗色控制台语境。
- 视觉上采用“单列 hero + 两列列表卡片 + 右侧轻编辑器”的 catalog 形态，减少解释文案，优先展示操作与状态。
- `Skills` / `MCP` 页面都按 `已安装 / 推荐` 分区组织，操作按钮明确为 `安装 / 编辑 / 删除 / 停用`，避免 readiness/debug 术语。
- `Agents` 页新增的 provider 勾选区域放在配置面内部，作为“能力 Provider”分组，不打断既有 Agent 编辑主流。

## Code Impact

### Backend

- `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_registry.py`
- `octoagent/packages/core/src/octoagent/core/models/control_plane.py`

### Frontend

- `octoagent/frontend/src/App.tsx`
- `octoagent/frontend/src/pages/SettingsCenter.tsx`
- `octoagent/frontend/src/pages/AgentCenter.tsx`
- `octoagent/frontend/src/pages/SkillProviderCenter.tsx`
- `octoagent/frontend/src/pages/McpProviderCenter.tsx`
- `octoagent/frontend/src/index.css`
- `octoagent/frontend/src/types/index.ts`
- `octoagent/frontend/src/api/client.ts`
- `octoagent/frontend/src/hooks/useWorkbenchSnapshot.ts`
- `octoagent/frontend/src/workbench/utils.ts`
- `octoagent/frontend/src/App.test.tsx`

## Main Refactors

1. **Capability provider catalog backend**
   - 新增 skill provider 配置模型与持久化文件
   - 为 MCP / Skill 提供 catalog document 与 CRUD action
   - 让 `capability.refresh` 能刷新 catalog 变更后的 pack/runtime truth

2. **Capability pack filtering**
   - 抽象 project selection 与 agent/profile selection 的合并逻辑
   - `get_pack()` / `resolve_profile_first_tools()` 接收 profile 维度选择覆盖
   - Worker 模板同步到 `AgentProfile` 时保留 provider selection metadata

3. **Settings IA**
   - `SettingsCenter` 改为平台总览 + 跳转入口
   - 移除内嵌 skill governance 大块内容，改用 summary card 跳转到独立页面

4. **Skill / MCP catalog pages**
   - 独立页面展示 installed/recommended list
   - 提供 install/edit/delete dialog 或内联编辑
   - 展示 availability / error / runtime truth

5. **Agent capability selection**
   - Butler 配置增加 capability provider 勾选
   - Worker 模板草稿/发布链增加 capability provider 勾选与 metadata 存储
   - 对现有 review/apply/save 流程保持兼容

## Verification Strategy

- 后端：
  - 新增/更新 `pytest` 覆盖 skill provider catalog CRUD、MCP catalog CRUD、selection merge 与 runtime filtering。
- 前端：
  - 更新 `App.test.tsx` 路由与行为断言，覆盖 `/settings/skills`、`/settings/mcp`、Agents provider selection。
- 构建：
  - 运行 `tsc -b`
  - 在环境允许时运行定向 `vitest`
- 风险核查：
  - 验证删除 provider 后页面和 runtime 不崩溃
  - 验证 built-in skill 只读、custom skill 可编辑
  - 验证 project 默认治理与 agent/profile 选择合并后的优先级
