# Tasks: Feature 046 Capability Provider Centers

**Input**: `.specify/features/046-capability-provider-centers/`

## Phase 1: Feature 制品

- [x] T001 [P0] 补齐 `.specify/features/046-capability-provider-centers/spec.md`
- [x] T002 [P0] 补齐 `.specify/features/046-capability-provider-centers/plan.md`
- [x] T003 [P0] 补齐 `.specify/features/046-capability-provider-centers/tasks.md` 与 `specs/046-capability-provider-centers/spec.md` redirect

## Phase 2: 控制面 catalog 后端

- [ ] T004 [P0] 在 `control_plane.py` / `control_plane` models 中新增 Skills / MCP catalog resource 契约
- [ ] T005 [P0] 在 `capability_pack.py` 增加自定义 skill provider catalog 的加载、保存、删除与 registry refresh
- [ ] T006 [P0] 在 `mcp_registry.py` 或对应控制面动作中补齐 MCP server catalog 的保存、删除、启停支持
- [ ] T007 [P0] 为 Skills / MCP catalog 新增 control-plane action：保存、删除、刷新

## Phase 3: Agent capability selection

- [ ] T008 [P0] 在 `capability_pack.py` 实现 project selection 与 agent/profile selection 的合并和过滤优先级
- [ ] T009 [P0] 在 `control_plane.py` 扩展 `agent_profile.save` / `worker_profile.review` / `worker_profile.apply` / publish sync，持久化 capability provider selection metadata
- [ ] T010 [P1] 验证 `resolve_profile_first_tools` 与 Worker 运行链真正读取新的 profile metadata

## Phase 4: 前端页面与交互

- [ ] T011 [P0] 重构 `SettingsCenter.tsx`，加入 `Skills` / `MCP` 独立入口并移除内嵌能力治理大块
- [ ] T012 [P0] 新增 `SkillProviderCenter.tsx`，实现 installed/recommended、安装、编辑、删除交互
- [ ] T013 [P0] 新增 `McpProviderCenter.tsx`，实现 installed/recommended、安装、编辑、启停、删除交互
- [ ] T014 [P0] 在 `AgentCenter.tsx` 为 Butler / Worker 模板加入 capability provider 勾选 UI
- [ ] T015 [P1] 更新 `App.tsx`、`api/client.ts`、`useWorkbenchSnapshot.ts`、`workbench/utils.ts`、`types/index.ts` 以支持新资源与新路由
- [ ] T016 [P1] 在 `index.css` 补齐新的 catalog/list 样式，保持简约、清晰且兼容移动端

## Phase 5: 验证

- [ ] T017 [P0] 更新后端测试，覆盖 catalog CRUD、selection merge、runtime filtering
- [ ] T018 [P0] 更新 `octoagent/frontend/src/App.test.tsx`，覆盖新页面路由与 Agents 勾选交互
- [ ] T019 [P0] 运行 `pytest`、`tsc -b` 与可用的前端定向测试，记录验证结果
