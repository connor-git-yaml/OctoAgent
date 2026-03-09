# Tech Research: Feature 032 — OpenClaw Built-in Tool Suite + Live Graph/Subagent Runtime

## 1. 当前代码基线

### 1.1 capability pack 注册的 built-in tools 仍然极少

证据：

- `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`

当前 `_register_builtin_tools()` 只注册了：

- `project.inspect`
- `task.inspect`
- `artifact.list`
- `runtime.inspect`
- `work.inspect`

结论：

- 030 交付的是 capability framework，不是 OpenClaw 等价的 built-in tool suite。
- 032 的实现必须补“正式工具目录 + 多工具族 + 用户可达入口”。

### 1.2 Graph 目前更接近自定义 pipeline，而不是真正的 pydantic_graph bridge

证据：

- `octoagent/packages/skills/src/octoagent/skills/pipeline.py`
- `octoagent/uv.lock` 包含 `pydantic_graph`

现状：

- `SkillPipelineEngine` 已经实现 deterministic node execution、checkpoint、resume、retry。
- 但运行时并未真正消费 `pydantic_graph` 的 `Graph` / `BaseNode` / `GraphRunContext` 语义。
- `graph_agent` 当前主要出现在 `target_kind` 和 worker profile 上。

结论：

- 032 不应把现有 pipeline 直接改名为 graph。
- 正确路线是：在保留现有 pipeline 治理能力的前提下，新增正式的 graph backend bridge，并把 graph run 接到 control plane。

### 1.3 Delegation Plane 有 target kind，但 live runtime truth 不够

证据：

- `octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py`

现状：

- `_select_target_kind()` 会把不同 worker type 映射到 `GRAPH_AGENT`、`SUBAGENT`、`ACP_RUNTIME`。
- 但实际 dispatch 主链仍然落到 `OrchestratorService -> LLMWorkerAdapter -> WorkerRuntime -> TaskService.process_task_with_llm`。
- 这意味着 target kind 已被建模，但真正独立的 graph/subagent runtime 后端还不够明确。

结论：

- 032 必须显式补 runtime adapter / descriptor / status producer。
- 不能继续把 `target_kind` 当成交付凭证。

### 1.4 主 Agent child work 生命周期不完整

证据：

- `octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py`
- `octoagent/packages/core/src/octoagent/core/models/delegation.py`

现状：

- `merge_work()` 已存在。
- 没有发现正式的 `split_work` 实现。
- child-work create / merge 的 durable state 已有基础，但“split -> assign -> merge” 的闭环还没有形成。

结论：

- 032 必须把 split 补齐，否则不能宣称具备 main-agent create/merge/split worker 能力。

### 1.5 当前 built-in tools 的用户可达入口不足

证据：

- `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
- 当前前端 control plane 与路由代码中未见丰富 built-in tool families 的真实入口

结论：

- 032 必须把“工具存在于 registry”与“工具能被用户真实调用”区分开。
- 验证必须至少覆盖主 Agent / Web / CLI 其中一条贯通链。

## 2. 参考实现启发

### 2.1 OpenClaw 的 built-in tool 面

参考：

- `_references/opensource/openclaw/src/agents/tools/`
- 代表文件：
  - `sessions-spawn-tool.ts`
  - `agents-list-tool.ts`
  - `subagents-tool.ts`
  - `web-fetch.ts`
  - `web-search.ts`
  - `browser-tool.ts`
  - `gateway.ts`
  - `cron-tool.ts`
  - `nodes-tool.ts`
  - `pdf-tool.ts`
  - `image-tool.ts`
  - `tts-tool.ts`
  - `memory-tool.ts`

启发：

- OpenClaw 的 built-in tools 不是“隐藏在系统内部的 helpers”，而是正式 agent tool surface。
- session / subagent 工具是第一类能力，说明 032 不应只做“更多 inspect”。

### 2.2 Pydantic AI 的 multi-agent / graph 路径

参考：

- `_references/opensource/pydantic-ai/docs/multi-agent-applications.md`
- `_references/opensource/pydantic-ai/docs/graph.md`

启发：

- agent delegation 的关键是 parent/delegate usage、deps、control flow 都真实存在。
- graph 的关键是 `BaseNode`、`GraphRunContext`、durable state / checkpoint，而不是给普通 worker 贴上 graph 标签。
- 032 若要宣称支持 Pydantic AI Graph，必须至少做到“有真实 bridge，有真实 run，有真实状态投影”。

## 3. 实现边界决策

### D1. 032 不是重做 030，而是补 live runtime truth

正确做法：

- 复用 030 的 ToolIndex / capability pack / delegation plane / pipeline 基线
- 把“只存在于 target kind / metadata 的能力”补成真实 runtime

### D2. `BuiltinToolCatalog` 应作为正式模型存在

原因：

- 需要统一表达 family、availability、install hint、degraded reason、entrypoint binding
- 也需要让 control plane 和 ToolIndex 共享同一事实源

### D3. Graph backend 应与现有 pipeline 并存，而不是相互替代

原因：

- 现有 pipeline 已有 durable governance 价值
- `pydantic_graph` 更适合作为一个显式 backend / adapter，用于真正需要 graph semantics 的 worker/work

### D4. Subagent 需要正式 runtime/session model

原因：

- 否则 control plane 无法证明 child runtime 真的存在
- 也无法支撑 operator 对 child work 的 inspect / cancel / retry

### D5. Child work 需要 split/merge 的 durable truth

原因：

- 只有 create/merge 没有 split，operator 无法理解工作是如何被拆开的
- parent/child ownership 也无法完整表达

## 4. 推荐实施层次

### Layer A: Tool Truth

- `BuiltinToolCatalog`
- `BuiltinToolAvailabilityResolver`
- install hints / dependency gates

### Layer B: Live Runtime Bridges

- `GraphRuntimeAdapter`
- `SubagentRuntimeAdapter`
- `RuntimeTruthSnapshot`

### Layer C: Main-Agent Child Work

- `split_work`
- child work projection
- merge / cancel / inspect lifecycle

### Layer D: Product Surface

- control plane resources / actions
- 至少一条主 Agent / CLI / Web 真实入口
- integration / e2e verification

## 5. 技术风险

1. 如果继续只做 metadata label，032 会直接重演 030 的“框架已交付、能力却不够可信”的问题。
2. 如果 Graph bridge 只是包装现有 pipeline，而没有真实 `pydantic_graph` 执行路径，用户最终仍然无法使用 Pydantic AI Graph。
3. 如果 subagent runtime 不具备 durable session/state，child work 生命周期会在重启后失真。
4. 如果 availability 只在启动时计算一次，而不和 runtime/config/env/bin 状态联动，control plane 很容易误报可用。
