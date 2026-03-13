# Tasks: Feature 042 Profile-First Tool Universe + Agent Console Reset

**Input**: `.specify/features/042-profile-first-agent-console/`  
**Prerequisites**: `spec.md`, `plan.md`, `data-model.md`, `contracts/profile-first-chat-and-console.md`, `verification/acceptance-matrix.md`

## Phase 1: Contract Freeze

- [x] T001 [P0] 回写 042 `spec.md / data-model.md / contracts/profile-first-chat-and-console.md / verification/acceptance-matrix.md / plan.md`，冻结 `profile-first tool universe` 与 Agent Console 三栏 IA
- [x] T002 [P0] 基于 `ui-ux-pro-max` 收口 UI 规则：高信息密度、清晰导航、badge 驱动状态、键盘与 aria 语义优先

## Phase 2: Chat Profile Binding

- [x] T003 [P0] 在 `octoagent/packages/policy/src/octoagent/policy/models.py` 为 `ChatSendRequest` 增加可选 `agent_profile_id`
- [x] T004 [P0] 在 `octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py` 透传 `agent_profile_id` 到任务处理主链
- [x] T005 [P0] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py` 与 `agent_context.py` 完成 `session > project > system` 的 chat agent 绑定与 runtime metadata 写入
- [x] T006 [P1] 在后端测试中覆盖显式绑定、project 默认绑定、system fallback 与失效 profile 回退

## Phase 3: Effective Tool Universe Backend

- [x] T007 [P0] 在 `octoagent/packages/core/src/octoagent/core/models/` 增加 `EffectiveToolUniverse / ToolResolutionTrace / ToolAvailabilityExplanation` 相关模型或等价契约结构
- [x] T008 [P0] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` 实现 profile-first 核心工具宇宙解析，区分 `core tools` 与 `discovery entrypoints`
- [x] T009 [P0] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py` 将 `tool_index.select` 从 top-k 选择升级为“解析并挂载核心工具宇宙”
- [x] T010 [P0] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py` 改为消费实际挂载的核心工具集，并保持 `selected_tools_json` 兼容
- [x] T011 [P0] 在 `capability_pack.py` / `delegation_plane.py` 中稳定 delegation 核心工具挂载策略，避免 `workers.review / subagents.spawn` 被默认隐藏
- [x] T012 [P1] 保留 `ToolIndex` 作为 discovery / explainability 组件，不再驱动默认 chat 主链

## Phase 4: Runtime Truth + Control Plane Explainability

- [x] T013 [P0] 在 `Work` / runtime metadata / projection 中补 `tool_resolution_mode / effective_tool_universe / tool_resolution_trace / tool_resolution_warnings`
- [x] T014 [P0] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 和相关 models 中补 tool availability explainability 字段
- [x] T015 [P1] 在 `worker_profiles` dynamic context 中增加当前工具 access / warnings 的聚合表达，减少前端跨资源拼接
- [x] T016 [P0] 在 `apps/gateway/tests/` 增加 runtime truth 与 control-plane explainability regression

## Phase 5: Chat UX + Agent Console Reset

- [x] T017 [P0] 在 `octoagent/frontend/src/types/index.ts`、`api/client.ts`、`hooks/useChatStream.ts` 接入 `agent_profile_id` 与 tool resolution explainability 字段
- [x] T018 [P0] 在 `octoagent/frontend/src/pages/ChatWorkbench.tsx` 增加当前 Agent 条带、绑定来源说明与 Agent 快捷入口
- [x] T019 [P0] 在 `octoagent/frontend/src/pages/AgentCenter.tsx` 重构为 `Root Agents / Agent Detail / Runtime Inspector` 三栏主布局
- [x] T020 [P1] 将 AgentCenter 中 041 遗留的混合卡片和重复说明收敛成可复用分区或子组件，降低单文件复杂度
- [x] T021 [P0] 在 `octoagent/frontend/src/pages/ControlPlane.tsx` 收口为深度诊断页，突出 tool resolution trace / blocked reasons / lineage
- [x] T022 [P1] 在 `octoagent/frontend/src/index.css` 落地数据密集但清晰的 Agent Console 样式，并补充 focus / hover / empty state / warning callout 规则

## Phase 6: Verification

- [x] T023 [P0] 跑 `pytest` 覆盖 chat binding、profile-first tool universe、delegation visibility、control-plane explainability
- [x] T024 [P0] 跑 frontend `vitest`，覆盖 ChatWorkbench / AgentCenter / ControlPlane 新主链
- [x] T025 [P0] 跑 `npm run build`，确认 Agent Console 重构不破坏现有 workbench
- [x] T026 [P0] 手工 smoke `chat / agents / advanced`，按 acceptance matrix 检查四类任务路径
