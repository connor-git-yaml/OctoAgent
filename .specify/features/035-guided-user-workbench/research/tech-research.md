# Tech Research: Feature 035 — Guided User Workbench + Visual Config Center

## 1. 当前代码基线

## 1.1 路由入口仍然过于单薄

证据：

- `octoagent/frontend/src/App.tsx`

现状：

- `/` -> `ControlPlane`
- `/tasks/:taskId` -> `TaskDetail`

结论：

- 当前没有正式的产品壳、首页、聊天页、设置页、记忆页、工作页。
- 035 必须从路由层开始重构。

## 1.2 `ControlPlane.tsx` 已消费 canonical API，但呈现方式偏 operator/resource

证据：

- `octoagent/frontend/src/pages/ControlPlane.tsx`
- `octoagent/frontend/src/api/client.ts`
- `octoagent/frontend/src/types/index.ts`

现状：

- 前端已经正确消费 `snapshot/resources/actions/events`
- 但 UI 直接按 `dashboard / projects / capability / delegation / pipelines / sessions / operator / automation / diagnostics / memory / imports / config / channels` 分区展开

结论：

- 026 的 backend contract 是可复用的
- 真正缺的是用户工作台级别的页面组织与 view-model 适配层

## 1.3 图形化配置的后端基础已经在，但前端没有产品化

证据：

- `ConfigSchemaDocument`
- `control_plane.py` 中的 `config.apply`
- `wizard_session` / `project_selector` / `diagnostics_summary`

现状：

- `config` resource 已有 `schema`、`ui_hints`、`current_value`
- wizard 已有 `current_step`、`steps`、`next_actions`
- `project.select`、`config.apply` 等 action 已注册

结论：

- 035 不应该新造 settings backend
- 应该把这些 contract 组织成图形化设置中心

## 1.4 聊天链路和 control-plane 仍然分开

证据：

- `frontend/src/hooks/useChatStream.ts`
- `apps/gateway/src/octoagent/gateway/routes/chat.py`
- `apps/gateway/src/octoagent/gateway/routes/tasks.py`
- `apps/gateway/src/octoagent/gateway/routes/execution.py`

现状：

- `useChatStream()` 只做 `POST /api/chat/send + SSE`
- `TaskDetail` 独立消费 `/api/tasks/{task_id}`
- 聊天并没有把 `execution`、`sessions`、`delegation`、`memory` 接到同一工作台

结论：

- 035 必须把 chat / task / execution / control-plane projections 统一到一个聊天工作台

## 1.5 任务与 Work 的事实源已经存在

证据：

- `SessionProjectionDocument`
- `DelegationPlaneDocument`
- `control_plane.py` 中的 `work.*` actions

现状：

- work 已有：
  - `work_id`
  - parent/child
  - runtime summary
  - `work.cancel/retry/split/merge/escalate`
- session 已有：
  - thread_id / task_id / project_id / runtime kind / latest message summary

结论：

- 035 不需要重做 work backend
- 只需要做一个用户能看懂的 Work 页面，并复用既有 actions

## 1.6 Memory 的正式产品面已经存在，但仍偏 operator

证据：

- `MemoryConsoleDocument`
- `MemorySubjectHistoryDocument`
- `MemoryProposalAuditDocument`
- `VaultAuthorizationDocument`

现状：

- 027 已把 memory/vault/proposal 都做成 canonical resources
- 但前端当前仍主要放在大控制面中，缺少单独“用户可读”的摘要与路径组织

结论：

- 035 应该做 Memory 页面，但仍然完全消费现有 canonical resources

## 1.7 033 / 034 将成为聊天工作台的关键输入

证据：

- `.specify/features/033-agent-context-continuity/spec.md`
- `.specify/features/034-context-compression-main-worker/spec.md`

现状：

- 033 规划了 profile/bootstrap/context provenance
- 034 已把 compaction event/artifact/evidence 接进主链

结论：

- 035 必须预留并直接消费 033/034 的 resource/projection
- 不能在 UI 层复制一套“上下文解释”逻辑

## 2. 设计边界决策

### D1. 不新增私有 workbench API

正确做法：

- 用 frontend adapter 组合现有 resource/document/detail route
- 只有确实缺字段时，扩现有 canonical document

### D2. `Advanced` 保留为兼容层

原因：

- 现有 `ControlPlane` 仍是 operator 诊断面
- 直接删除会让高级用户退无可退

### D3. 设计系统必须先行

原因：

- 当前页面大量 inline style
- 如果不先统一 shell/token/component，再做多页改造，只会把视觉债务扩散

### D4. 配置必须坚持 `schema + hints + action`

原因：

- 这是防止 Web/CLI/wizard 三边语义漂移的唯一办法
- 也是“做了但没接上”的最大防线

## 3. 推荐实施层次

### Layer A: Shell / Navigation / Tokens

- app shell
- responsive nav
- global state strip

### Layer B: Home / Settings

- readiness cards
- next actions
- graphical config

### Layer C: Chat Workbench

- send / stream / execution
- context drawer
- approvals / execution input

### Layer D: Work / Memory

- board / detail drawer
- memory summary / history / proposal / vault

### Layer E: Advanced Compatibility

- 旧 control plane 收编
- legacy routes / deep links

## 4. 技术风险

1. 如果新增私有 workbench API，035 会立即重演“前后端两套语义”问题。
2. 如果设置页跳过 `config.apply`，Web 会很快和 CLI / wizard 漂移。
3. 如果聊天工作台不接 task/execution/work/memory，仍然只是一个漂亮的聊天壳。
4. 如果 033/034 没有预留消费位点，后续主 Agent context/compaction 仍然会在 UI 中缺席。
