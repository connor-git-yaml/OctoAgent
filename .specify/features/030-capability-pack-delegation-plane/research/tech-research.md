# Tech Research: Feature 030 — Built-in Capability Pack + Delegation Plane + Skill Pipeline

## 1. 当前代码基线

### 1.1 Tool / Policy 基线已经具备

现有实现：

- `packages/tooling/src/octoagent/tooling/broker.py`
- `packages/tooling/src/octoagent/tooling/models.py`
- `packages/policy/src/octoagent/policy/policy_engine.py`
- `packages/policy/src/octoagent/policy/policy_check_hook.py`

结论：

- ToolBroker 已经是唯一工具执行入口，支持 hook 链、事件写入、profile gate。
- PolicyEngine 已经通过 `PolicyCheckHook` 接到 ToolBroker before hook 链。
- 因此 030 不应该新建第二套工具执行器；动态工具注入只能影响“当前暴露给 worker/skill 的工具子集”，真正执行仍必须走 ToolBroker。

### 1.2 Skill 基线只有单 skill 循环

现有实现：

- `packages/skills/src/octoagent/skills/runner.py`
- `packages/skills/src/octoagent/skills/models.py`
- `packages/skills/src/octoagent/skills/manifest.py`
- `packages/skills/src/octoagent/skills/registry.py`

结论：

- 现有 SkillRunner 具备输入/输出校验、循环保护、tool call 执行、重试。
- 缺失的是多节点 deterministic pipeline、节点级 checkpoint、pause/replay/node retry。
- 030 最合理的是在 `packages/skills` 内新增 pipeline engine，保留 SkillRunner 作为节点内部执行器，而不是替换它。

### 1.3 Orchestrator / Worker 仍是单 Worker 主链

现有实现：

- `apps/gateway/src/octoagent/gateway/services/orchestrator.py`
- `apps/gateway/src/octoagent/gateway/services/worker_runtime.py`
- `packages/core/src/octoagent/core/models/orchestrator.py`
- `packages/protocol/src/octoagent/protocol/adapters.py`

结论：

- `OrchestratorService` 当前只有 `SingleWorkerRouter`。
- `DispatchEnvelope` / `WorkerResult` / A2A-Lite adapter 已经能承载 route reason、metadata、hop_count、tool_profile。
- 因此 030 可以在不破坏 018/019 基线的前提下扩展：把 `Work`、worker type、runtime kind、route reason、dynamic tool hit 写入 envelope metadata。

### 1.4 Control Plane 扩展点已经存在

现有实现：

- `apps/gateway/src/octoagent/gateway/services/control_plane.py`
- `packages/core/src/octoagent/core/models/control_plane.py`
- `frontend/src/pages/ControlPlane.tsx`

结论：

- 026 已提供 snapshot/per-resource/actions/events 统一入口。
- 030 只需要增加新的 canonical resources / actions / projections，不应该新增平行 API 或前端 DTO。

## 2. 参考实现启发

### 2.1 OpenClaw 子智能体模式

参考：

- `_references/opensource/openclaw/docs/zh-CN/tools/subagents.md`
- `_references/opensource/openclaw/docs/zh-CN/pi.md`

启发：

- `sessions_spawn` 非阻塞，立即返回 accepted + 稳定 run/session key。
- 子智能体上下文只注入有限 bootstrap 文件，而不是整份用户态记忆。
- 这说明 OctoAgent 的 subagent delegation 也应：
  - 先持久化 work，再返回 accepted
  - 用 capability pack 提供精简 bootstrap
  - 把 subagent 当作 delegation target，而不是普通 task label

### 2.2 Agent Zero subordinate 调用模式

参考：

- `_references/opensource/agent-zero/python/tools/call_subordinate.py`
- `_references/opensource/agent-zero/python/helpers/subagents.py`

启发：

- subordinate 的核心是“受控的上下文继承 + 明确的 superior/subordinate 关系”。
- 对 OctoAgent 来说，应该把这种关系正式化成 `parent_work_id / owner / escalation_target`，而不是只在 prompt 文本里表达。

### 2.3 pydantic-graph 的 durable execution 模式

参考：

- `_references/opensource/pydantic-ai/docs/graph.md`
- `_references/opensource/pydantic-ai/docs/durable_execution/overview.md`
- `_references/opensource/pydantic-ai/pydantic_graph/pydantic_graph/persistence/file.py`

启发：

- 图执行的关键不是“自动跑完”，而是 `iter/iter_from_persistence` 这种 step-by-step 恢复语义。
- persistence 需要记录：
  - state snapshot
  - next node
  - node execution status
  - end result
- OctoAgent 的 pipeline engine 也应该以“逐节点推进 + 每节点落 checkpoint”为核心。

## 3. 实现边界决策

### D1. ToolIndex 放在哪里

候选：

1. 放在 `packages/tooling`
2. 放在 `packages/memory`
3. 放在 Gateway service

决策：放在 `packages/tooling`

理由：

- ToolIndex 的输入是真正的 `ToolMeta`/manifest metadata。
- 动态工具注入要直接作用于 ToolBroker 的 discover/selection 语义。
- 放到 memory 会把“工具检索”和“记忆检索”混成一层；放到 gateway 又会让非 Gateway runtime 复用困难。

### D2. ToolIndex 是否强依赖 LanceDB

决策：实现 `ToolIndexBackend` 抽象，提供：

- `InMemoryToolIndexBackend` 作为默认降级路径
- `LanceToolIndexBackend` 作为优先增强实现（环境可用时启用）

理由：

- blueprint 的能力约束要求语义检索默认走向量数据库；
- 但 Constitution 也要求 degrade gracefully；
- 因此 030 应支持 Lance 风格向量检索，同时在环境无 LanceDB 时显式退化到本地向量/词项混合检索。

### D3. Skill Pipeline 是否替换 SkillRunner

决策：否

实现边界：

- SkillRunner 继续负责“单 skill 自由循环 + tool calls”
- PipelineEngine 负责“多节点 deterministic orchestration”
- pipeline node 可以调用：
  - SkillRunner
  - ToolBroker
  - human gate
  - delegation target

### D4. Work 放在哪里

决策：`packages/core`

理由：

- Work 是主 Agent 的正式委派单位，必须成为 domain model，而不是 gateway 私有 DTO。
- 它和 task、event、project/workspace 都有跨层引用关系，需要 core 统一建模。

### D5. Work 是否要单独持久化

决策：要，使用 SQLite store

最低持久化字段：

- `work_id`
- `task_id`
- `parent_work_id`
- `kind`
- `target_kind`
- `owner_id`
- `project_id`
- `workspace_id`
- `status`
- `route_reason`
- `selected_worker_type`
- `selected_tools`
- `pipeline_run_id`
- `created_at/updated_at`

### D6. 统一委派协议如何设计

决策：定义一套 canonical delegation envelope，而不是为 worker/subagent/graph runtime 各写一套私有参数。

统一字段至少应包含：

- `work_id`
- `task_id`
- `target_kind`
- `requested_capability`
- `payload`
- `route_reason`
- `worker_type`
- `tool_selection`
- `bootstrap_context`
- `timeout_s`
- `project_id/workspace_id`

这样：

- worker target 可转为 `DispatchEnvelope`
- graph target 可转为 pipeline run
- subagent / ACP-like runtime 可保留同一语义并走适配器

### D7. 多 Worker capability registry 放在哪里

决策：Gateway service 层拥有运行时 registry，core 只放共享模型

理由：

- registry 需要知道本进程有哪些 adapter/backend 可用
- 但 worker type/capability/profile/bootstrap 文件的 canonical model 应在 core

### D8. Control Plane 如何接入

决策：在 026 control plane 上新增 resource producer，而不是平行 diagnostics API

新增资源：

- `capability_pack`
- `delegation_plane`
- `skill_pipeline`

同时扩展现有：

- `SessionProjectionDocument.execution_summary`
- `DiagnosticsSummaryDocument.runtime_snapshot`
- action registry

## 4. 测试策略

必须覆盖：

- ToolIndex 查询、metadata filter、fallback
- Pipeline checkpoint / replay / pause / retry
- Work create/assign/cancel/merge/escalation
- 多 Worker route reason 与 single-worker fallback
- control plane snapshot/resource/action/event
- frontend integration：新资源可渲染且动作可触发
- e2e：创建 work -> 路由 -> pipeline pause/resume -> control plane 可见

## 5. 设计结论

030 的最小正确实现不是“把多 Worker 打开”，而是：

1. 正式建模 `Work`
2. 让 ToolIndex 仅负责选工具，不负责执行工具
3. 让 PipelineEngine 仅负责 deterministic 子流程，不替代 Free Loop
4. 让 delegation 统一走 A2A/DispatchEnvelope/adapter 语义
5. 让 control plane 成为 capability/work/pipeline/runtime 的唯一产品面
