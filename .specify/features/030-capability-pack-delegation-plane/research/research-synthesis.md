# Research Synthesis: Feature 030 — Built-in Capability Pack + Delegation Plane + Skill Pipeline

## 1. 综合判断

Feature 030 可以在现有 master 上以 additive 方式落地，不需要推翻 025-B/026 的基线。

原因：

- 025-B 已经把 project/workspace/secret/wizard 的作用域真相源做出来了；
- 026 已经把 control plane 的 canonical route shell 做出来了；
- 004/005/006/018/019 已经把 ToolBroker、Policy、A2A、Execution/JobRunner 的治理链打通了。

所以 030 的正确路线不是“新建一个 agent runtime”，而是在既有主链上补出三层增强：

1. capability pack
2. delegation plane
3. skill pipeline

## 2. 产品与技术共识

### 2.1 必须显式化的对象

产品侧要求“用户可理解”，技术侧要求“状态可恢复”，两边都指向同一件事：

- `Work` 必须成为正式对象
- `route_reason` 必须可见
- `selected_tools / tool hits` 必须可见
- `pipeline checkpoints` 必须可回放
- `runtime target` 必须可见

### 2.2 不能重做的底座

本 Feature 不应重做：

- ToolBroker / Policy Engine
- A2A-Lite envelope
- control plane shell
- project/workspace
- automation scheduler

正确做法是消费它们：

- ToolIndex 只决定“当前建议暴露哪些工具”
- 真正工具执行仍走 ToolBroker + Policy
- delegation 通过 `DispatchEnvelope` / A2A metadata 承载
- control plane 只增量增加 capability/work/pipeline 资源

### 2.3 必须保留的降级路径

综合 Constitution + online research，030 需要明确两条退路：

- 多 Worker 不可用 -> 单 Worker fallback
- ToolIndex 不可用 -> 静态工具集 fallback

且都必须显式进入 control plane degraded state，而不是静默行为。

## 3. 推荐实现轮廓

### Layer A: Capability Foundation

- `BundledCapabilityPack`
- `WorkerCapabilityRegistry`
- `WorkerBootstrapFile`
- `ToolIndex`

### Layer B: Deterministic Execution

- `SkillPipelineDefinition`
- `SkillPipelineRun`
- `PipelineCheckpoint`
- `PipelineReplayFrame`
- `node retry / pause / resume`

### Layer C: Delegation Control

- `Work`
- `DelegationEnvelope`
- `DelegationTarget`
- `DelegationPlaneService`
- `route_reason / ownership / escalation`

### Layer D: Product Surface

- control-plane resources:
  - `capability_pack`
  - `delegation_plane`
  - `skill_pipeline`
- 复用既有 diagnostics/session/automation 资源，把 route reason、runtime status、tool hits 接进去

## 4. 关键设计决策

1. `Work` 用 SQLite 持久化，不能只放 event payload。
2. ToolIndex 采用 backend abstraction，默认本地 fallback，优先兼容 Lance 风格 query/filter。
3. Skill Pipeline 作为 Worker 工具，而不是第三种“主执行模式”。
4. delegation target 统一建模，worker/subagent/ACP-like runtime/graph agent 用同一 envelope + adapter 体系。
5. control plane 只增量扩展，不破坏 026 contract 与前端壳层。

## 5. MVP 边界

### 本轮必须做

- 真实 `Work` 生命周期
- 真实 ToolIndex 查询/过滤/命中结果
- 真实 pipeline checkpoint/pause/replay/retry
- 至少三种 worker type：`ops` / `research` / `dev`
- control plane 能看见 capability/work/pipeline/runtime

### 本轮允许降级

- `subagent` 与 `ACP-like runtime` 先通过本地 adapter 落统一协议，不引入远端节点
- Lance backend 可选启用；环境缺失时走本地 fallback
- frontend 先在既有 control plane 中增加新面板，不要求可视化拖拽 graph editor

## 6. 最终建议

030 应按以下顺序实施：

1. 先落 core models + stores（Work / pipeline）
2. 再落 ToolIndex + capability registry + bundled pack
3. 再接 Orchestrator / WorkerRuntime / adapters
4. 最后扩 control plane / frontend / e2e

这样可以保证每一层都可独立测试和回滚。
