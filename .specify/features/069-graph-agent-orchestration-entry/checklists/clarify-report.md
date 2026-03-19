# Feature 065 — Clarify Report

> 生成时间: 2026-03-19
> 审查范围: spec.md, research/tech-research.md, constitution.md, Feature 064 spec, SkillPipelineEngine 源码

---

## 1. 与 Feature 064（Subagent 编排）的交互和重叠

### CRITICAL — Pipeline 节点中嵌入 Subagent 调用的行为未定义

spec 中 `PipelineNodeType` 枚举包含 `delegation` 类型（AC-02），但未定义当 Pipeline 节点需要委派给 Subagent 时的行为。场景：Pipeline 中某个节点需要执行一个"不确定性任务"（如代码审查），此时应该是 Pipeline 节点内部 spawn Subagent 还是应该失败？如果允许嵌套，Subagent 的 Free Loop 与 Pipeline 的确定性流转如何协调（Subagent 可能运行数小时）？

**建议**：在 spec 中明确 `delegation` 节点类型的语义边界：
- 是否允许 Pipeline 节点内部 spawn Subagent？
- 如果允许，Pipeline 应如何等待 Subagent 完成（是否进入 PAUSED 状态）？
- 如果不允许，`delegation` 类型节点的实际用途是什么？

### WARNING — graph_pipeline 工具与 subagents 工具的 LLM 选择冲突

Feature 064 的 `subagents.spawn` 工具和 Feature 065 的 `graph_pipeline(action="start")` 工具同时暴露给 LLM。spec FR-065-07 AC-02 仅提供了一句简要的语义区分指引（确定性流程 -> Pipeline，不确定任务 -> Subagent），但 LLM 在以下边界场景可能做出错误选择：
- 有审批门禁的代码部署（看似确定但有人工环节）
- 多步骤但需要 LLM 推理判断每步输出的任务（看似流程但实际不确定）

**建议**：在 FR-065-07 中补充更具操作性的选择标准，或在 `graph_pipeline(action="list")` 返回中增加 `recommended_over_subagent: bool` 字段。

### WARNING — Butler DELEGATE_GRAPH 与 Feature 064 DELEGATE_DEV/OPS 的路由优先级

spec FR-065-04 AC-06 规定 Pipeline 不匹配时 fallback 到 `DELEGATE_DEV/DELEGATE_OPS`，但未说明反向情况：当一个请求同时匹配某个 Pipeline 的 `trigger_hint` 和某个 Worker 类型时，Butler 应优先选择哪个？

**建议**：在 FR-065-04 中补充路由优先级规则，明确 `DELEGATE_GRAPH` 相对于 `DELEGATE_DEV/OPS` 的优先级和判断标准。

---

## 2. SkillPipelineEngine vs GraphRuntimeBackend 的定位澄清

### CRITICAL — 两个 Pipeline 执行引擎并存，边界模糊

当前代码库中存在两个执行后端：
1. **SkillPipelineEngine**（`packages/skills/pipeline.py`）：自研确定性 Pipeline，已深度集成 HITL/Checkpoint/Event 等能力。
2. **GraphRuntimeBackend**（`apps/gateway/services/worker_runtime.py:271`）：pydantic_graph 的包装层，当前只是 `prepare -> execute(process_task_with_llm) -> finalize` 三节点硬编码管线。

spec 约束 C1 声明"不引入新的 Pipeline 执行引擎"并"基于 SkillPipelineEngine 构建"，同时说 GraphRuntimeBackend "保留但不扩展"。然而：
- `DelegationTargetKind.GRAPH_AGENT` 在 WorkerRuntime 中已路由到 `GraphRuntimeBackend`，不是 `SkillPipelineEngine`。
- spec FR-065-05 AC-02 要求新创建的 Work `delegation_target_kind = GRAPH_AGENT`。

这意味着 Feature 065 创建的 Work 会被 WorkerRuntime 路由到 GraphRuntimeBackend（当前的 pydantic_graph 包装层），而非 SkillPipelineEngine。spec 期望的执行路径与实际代码路由存在断裂。

**建议**：明确以下选项之一：
- **选项 A**：修改 WorkerRuntime，使 `GRAPH_AGENT` target kind 路由到 SkillPipelineEngine 而非 GraphRuntimeBackend。
- **选项 B**：新增 `DelegationTargetKind.PIPELINE`（与 GRAPH_AGENT 区分），WorkerRuntime 新增对应路由。
- **选项 C**：Feature 065 的 Pipeline 执行不经过 WorkerRuntime 路由层，由 `graph_pipeline` 工具直接调用 SkillPipelineEngine。

### INFO — GraphRuntimeBackend 的长期定位需要明确

tech-research.md 5.2 建议 GraphRuntimeBackend 用于"需要 pydantic_graph 原生类型系统的高级场景"。但当前实现本质上只是 inline backend 的包装，不具备真正的 pydantic_graph 图能力（无动态节点、无 iter 模式、无 HITL）。

如果 Feature 065 明确选择 SkillPipelineEngine 路线，建议在 spec 中声明 GraphRuntimeBackend 的处置策略：保留不动 / 标记为 deprecated / 在后续 Feature 中重构。

---

## 3. PIPELINE.md 与 SKILL.md 的关系

### CRITICAL — 一个 Skill 能否引用一个 Pipeline？

spec 未定义 SKILL.md 与 PIPELINE.md 的交叉引用关系。以下场景需要澄清：
1. **Skill 引用 Pipeline**：一个 SKILL.md 是否可以声明"加载此 Skill 时同时注册关联的 Pipeline"？例如 `coding-agent` Skill 可能附带一个代码审查 Pipeline。
2. **Pipeline 节点引用 Skill**：PIPELINE.md 的节点 `handler_id` 能否引用一个 SKILL.md 定义的 Skill（如 `handler_id: skill.coding-agent`）？
3. **共享 vs 独立**：两者是完全独立的注册表（PipelineRegistry vs SkillDiscovery），还是有某种组合关系？

**建议**：在 spec 中明确三种场景的设计决策。如果 v0.1 不支持交叉引用，应在约束中显式声明"PIPELINE.md 和 SKILL.md 互不引用"。

### WARNING — PIPELINE.md 的 handler_id 命名空间与 DelegationPlane 已有 handler 冲突

当前 DelegationPlane 已注册 5 个 handler（`route.resolve`、`bootstrap.prepare`、`tool_index.select`、`gate.review`、`finalize`）。这些 handler_id 是 DelegationPlane 内部使用的 delegation pipeline 节点。

spec 中 PIPELINE.md 示例使用了 `terminal.exec`、`approval_gate` 等 handler_id，但未说明：
- 用户定义的 PIPELINE.md handler_id 和 DelegationPlane 内部 handler_id 是否共享同一个 SkillPipelineEngine 实例的 handler 注册表？
- 如果共享，用户 PIPELINE.md 引用 `gate.review` 会发生什么？
- 通用 handler（`terminal.exec`、`approval_gate`）由谁注册？在哪里实现？

**建议**：
- 明确 handler_id 命名空间策略（如内部 handler 使用 `_internal.` 前缀，或用户 Pipeline 使用独立的 Engine 实例）。
- 在 spec 假设 A4 中补充 `terminal.exec` / `approval_gate` 等通用 handler 的实现 owner 和注册时机。

### INFO — PIPELINE.md 与 SKILL.md 的目录结构高度对称

两者的三级目录发现机制完全对齐：
- Skills: `{project}/skills/` > `~/.octoagent/skills/` > `skills/`
- Pipelines: `{project}/pipelines/` > `~/.octoagent/pipelines/` > `pipelines/`

这种对称性设计合理，但需要在用户文档中说明两者的区别，避免用户将 Pipeline 定义错误放入 skills 目录或反之。

---

## 4. Pipeline 执行中的错误恢复和回退策略

### CRITICAL — 节点失败后的回退策略未定义

spec FR-065-05 AC-08 提到"进程重启后可从最后一个 Checkpoint 恢复"，但以下错误恢复场景缺少明确策略：

1. **节点执行失败**：当某个节点 handler 返回 `FAILED` 状态时，Pipeline 是否支持回退到前一个节点？还是只能整体失败？当前 SkillPipelineEngine `_drive()` 方法中 FAILED 直接进入终态。
2. **部分完成的 side-effect**：如果 Pipeline 中 `deploy` 节点执行了一半（如代码已推送但健康检查未通过），回退策略是什么？spec 未定义 rollback/compensation 机制。
3. **Checkpoint 恢复的幂等性**：从 Checkpoint 恢复时，如果被恢复的节点已经产生了 side-effect（如已发送了一封邮件），重新执行该节点是否会导致重复 side-effect？

**建议**：
- 在 spec 中明确 v0.1 的错误恢复策略（如"节点失败 = Pipeline 失败，不支持自动回退；人工可通过 `retry_current_node` 重试当前节点"）。
- 对于幂等性问题，要求 side-effect 类节点的 handler 实现幂等保证（通过 `side_effect_cursor` 字段，已在 `PipelineNodeOutcome` 中定义但未在 spec 中说明用法）。

### WARNING — 节点级超时缺失

spec 风险表提到"Pipeline 节点长时间阻塞导致资源泄漏"，缓解措施为"节点级 timeout_seconds"，但：
- PIPELINE.md 格式定义中节点没有 `timeout_seconds` 字段。
- `SkillPipelineNode` 模型中是否支持超时配置？
- `SkillPipelineEngine._drive()` 中当前没有节点执行超时逻辑。

**建议**：在 FR-065-01 的 PIPELINE.md 格式中为节点增加可选的 `timeout_seconds` 字段，并在 FR-065-05 的执行集成中说明超时处理行为（超时 = 节点 FAILED -> Pipeline FAILED）。

### INFO — retry_current_node 已在 Engine 中实现但 spec 未提及

SkillPipelineEngine 已有 `retry_current_node()` 方法，但 `graph_pipeline` 工具的 action 列表（list/start/status/resume/cancel）中没有 `retry` action。

**建议**：评估是否需要在 `graph_pipeline` 工具中暴露 `action=retry`，或在 v0.1 约束中说明"不对 LLM 暴露重试能力"。

---

## 5. 并发控制

### WARNING — 并发上限的实施层缺失

spec NFR-065-01 规定"系统级并发 Pipeline run 数量上限可配置（默认 10）"，但未指明：
- 并发计数由谁维护？`PipelineRegistry`？`SkillPipelineEngine`？还是一个新的 `PipelineScheduler`？
- 计数范围是全局级别还是 per-worker 级别？
- 进程重启后，未完成的 Pipeline run 是否计入并发配额（它们的状态可能是 RUNNING 但实际没有在执行）？

**建议**：在 NFR-065-01 中补充并发计数的实施位置和崩溃恢复后的配额回收机制。

### WARNING — 同一 Pipeline 多 run 的数据隔离

spec NFR-065-01 声明"各 run 之间状态隔离（通过独立的 run_id、SkillPipelineRun、PipelineCheckpoint 保证）"，但以下隔离方面未覆盖：
- **handler 级状态**：如果某个 handler 在内存中维护状态（如连接池、缓存），多个 run 并发调用同一 handler 时是否安全？
- **外部资源竞争**：两个 `deploy-staging` Pipeline run 同时执行时，它们操作相同的 staging 环境是否会冲突？

**建议**：
- 补充 handler 实现规范：要求 handler 必须是无状态的（或通过 `run.state_snapshot` 传递状态）。
- 评估是否需要 per-pipeline_id 的互斥锁（同一 Pipeline 同一时间只允许一个 run），或在 PIPELINE.md 中增加 `concurrency: 1` 配置项。

### INFO — SkillPipelineEngine.start_run() 是同步阻塞式执行

当前 `start_run()` 调用 `_drive()` 后会一直运行直到 Pipeline 完成或暂停才返回。如果 Pipeline 有长时间运行的节点（如部署操作耗时 30 分钟），调用 `start_run()` 的协程会被阻塞 30 分钟。

`graph_pipeline(action="start")` 工具调用 `start_run()` 意味着 LLM 的 tool call 会阻塞直到 Pipeline 完成/暂停。这与 Subagent 的异步 spawn 模式不同。

**建议**：评估是否需要将 `start_run()` 改为后台执行（创建 asyncio.Task 并立即返回 run_id），使 `graph_pipeline(action="start")` 成为非阻塞调用。如果选择阻塞模式，需在 spec 中说明这一行为及其对 LLM 执行循环的影响。

---

## 6. 其他发现

### INFO — 内置 Pipeline 定义缺失

spec FR-065-02 定义了三级发现路径，包括"内置级：`pipelines/*/PIPELINE.md`（仓库根目录）"。但当前仓库中 `pipelines/` 目录不存在，且 spec 未说明 Feature 065 是否需要交付任何内置 Pipeline 定义。

**建议**：明确 Feature 065 交付物是否包含至少一个内置 Pipeline（如用于测试/演示的 `echo-pipeline`），还是只交付框架而由后续 Feature 添加具体 Pipeline。

### INFO — Pipeline 前端可视化未包含在 scope 中

tech-research.md 5.4 指出"前端 Task Detail 页面需要扩展支持 Pipeline 节点视图"。spec 不包含前端 FR，这是合理的 scope 控制。建议在 spec 依赖关系表中标注"后续前端 Feature"作为下游依赖。

### INFO — Pipeline 定义的版本兼容性

spec AC-01 要求 PIPELINE.md 包含 `version` 字段，风险表提到"解析器按 version 分支处理"。但 v0.1 只有一个版本（1.0.0），建议在约束中明确"v0.1 仅支持 version 1.x.x，其他版本报错"。

---

## 汇总

| # | 级别 | 主题 | 章节 |
|---|------|------|------|
| 1 | CRITICAL | Pipeline 节点嵌入 Subagent 调用的行为未定义 | 1 |
| 2 | WARNING | graph_pipeline 与 subagents 工具的 LLM 选择冲突 | 1 |
| 3 | WARNING | Butler DELEGATE_GRAPH 路由优先级未定义 | 1 |
| 4 | CRITICAL | SkillPipelineEngine 与 GraphRuntimeBackend 的执行路径断裂 | 2 |
| 5 | INFO | GraphRuntimeBackend 长期定位需明确 | 2 |
| 6 | CRITICAL | SKILL.md 与 PIPELINE.md 的交叉引用关系未定义 | 3 |
| 7 | WARNING | handler_id 命名空间与内部 handler 冲突 | 3 |
| 8 | INFO | 目录结构对称性合理 | 3 |
| 9 | CRITICAL | 节点失败后的回退/回滚策略未定义 | 4 |
| 10 | WARNING | 节点级超时在格式定义和引擎中缺失 | 4 |
| 11 | INFO | retry_current_node 已实现但工具未暴露 | 4 |
| 12 | WARNING | 并发上限实施层和崩溃恢复配额回收未定义 | 5 |
| 13 | WARNING | 同一 Pipeline 多 run 的 handler 级和外部资源隔离 | 5 |
| 14 | INFO | start_run() 同步阻塞执行对 LLM 循环的影响 | 5 |
| 15 | INFO | 内置 Pipeline 定义缺失 | 6 |
| 16 | INFO | Pipeline 前端可视化为后续 Feature | 6 |
| 17 | INFO | Pipeline 版本兼容性策略 | 6 |

**CRITICAL**: 4 条 | **WARNING**: 6 条 | **INFO**: 7 条
