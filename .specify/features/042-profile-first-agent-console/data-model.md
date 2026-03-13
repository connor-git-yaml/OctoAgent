# Data Model: Feature 042 Profile-First Tool Universe + Agent Console Reset

## 1. 设计原则

042 的数据模型不重做 041 的 `WorkerProfile` 主链，而是在它之上补一层“运行时真正如何挂载工具”的正式事实层。核心原则：

- `WorkerProfile` 继续定义“这个 Agent 是谁、边界在哪里”
- `EffectiveToolUniverse` 定义“这次运行它实际看到了哪些核心工具”
- `ToolResolutionTrace` 定义“为什么是这些工具，而不是别的工具”
- `ToolIndex` 从主裁判变成辅助发现器

## 2. 现有对象与 042 的关系

### WorkerProfile（已存在，继续复用）

来源：041  
责任：

- 身份
- archetype
- model alias
- tool profile
- default tool groups
- selected tools
- policy refs

042 不替换它，只增加“从 profile 到实际挂载工具”的解释层。

### Work（已存在，继续扩展）

来源：030 / 041  
当前已有：

- `agent_profile_id`
- `requested_worker_profile_id`
- `effective_worker_snapshot_id`
- `selected_tools`
- `metadata`

042 将把工具挂载事实与解释写回 `selected_tools + metadata`，而不是另开平行运行记录。

## 3. 新增或扩展的关键对象

### ChatAgentBinding

描述一次 chat 请求最终绑定了哪个 Agent。

字段建议：

- `requested_agent_profile_id: str`
- `effective_agent_profile_id: str`
- `binding_source: "explicit" | "session" | "project" | "system_fallback"`
- `binding_warnings: list[str]`

用途：

- chat header 展示“你当前在和谁对话”
- runtime truth 解释为什么这次落到了哪个 Agent

### EffectiveToolUniverse

描述某次 chat/work 在 LLM 调用前真正挂载的稳定工具宇宙。

字段建议：

- `profile_id: str`
- `profile_revision: int`
- `resolution_mode: "profile_first_core" | "legacy_tool_index" | "discovery_augmented"`
- `tool_profile: str`
- `core_tools: list[str]`
- `delegation_tools: list[str]`
- `session_context_tools: list[str]`
- `discovery_entrypoints: list[str]`
- `warnings: list[str]`

约束：

- `core_tools` 是真正直接挂给模型的工具
- `discovery_entrypoints` 是发现长尾工具的入口，不等于本轮直接注入全部长尾工具

### ToolAvailabilityExplanation

面向 UI 的单个工具可用性说明对象。

字段建议：

- `tool_name: str`
- `status: "mounted" | "discovery_only" | "blocked" | "unavailable"`
- `source_kind: "profile_selected" | "group_expansion" | "governance_required" | "discovery"`
- `reason_code: str`
- `summary: str`
- `recommended_action: str`
- `connector_ref: str`

用途：

- Agent 页面 Tool Access 面板
- ControlPlane trace 解释

### ToolResolutionTrace

结构化记录某次运行的工具解析过程。

字段建议：

- `effective_profile_id: str`
- `effective_profile_revision: int`
- `policy_profile_id: str`
- `tool_profile_requested: str`
- `tool_profile_effective: str`
- `mounted_tools: list[ToolAvailabilityExplanation]`
- `blocked_tools: list[ToolAvailabilityExplanation]`
- `discovery_candidates: list[str]`
- `degraded_reasons: list[str]`

用途：

- 控制面 explainability
- 回答“为什么这个工具没给”

### AgentConsoleView（聚合视图对象）

面向前端 Agent 页面的一屏聚合模型，不替代 canonical resource，只是前端组合视角。

组成：

- `WorkerProfileViewItem`
- 当前 `ChatAgentBinding`
- 当前 `EffectiveToolUniverse`
- 当前活跃 `Work`
- 当前 warnings / readiness

## 4. 对现有对象的扩展建议

### ChatSendRequest

新增：

- `agent_profile_id?: string`

语义：

- 不传则走 `session > project > system`
- 传入则本轮 chat 显式绑定

### Work.metadata

新增建议：

- `tool_resolution_mode`
- `effective_tool_universe`
- `tool_resolution_trace`
- `tool_resolution_warnings`
- `chat_agent_binding`

兼容策略：

- `selected_tools_json` 保留
- 语义升级为“本次实际挂载给模型的核心工具集”

### WorkerProfileDynamicContext

建议增强：

- `current_resolution_mode`
- `current_tool_warnings`
- `current_blocked_tools_count`
- `current_discovery_entrypoints`

用途：

- Agent 页面右栏 inspector 直接可读

### Delegation / ControlPlane 投影

建议增强：

- `requested_agent_profile_id`
- `effective_agent_profile_id`
- `tool_resolution_mode`
- `mounted_tools`
- `blocked_tools`
- `tool_resolution_warnings`

## 5. 生命周期关系

```text
ChatSendRequest
  -> ChatAgentBinding
  -> AgentProfile / WorkerProfile resolve
  -> EffectiveToolUniverse resolve
  -> LLM sees mounted core tools
  -> Work persists selected_tools + tool_resolution_trace
  -> ControlPlane / AgentCenter read the same truth
```

## 6. 兼容策略

- `selected_worker_type` 保留，作为 legacy archetype / trace 字段
- `selected_tools_json` 保留，供旧 skill runner / tests / UI 继续消费
- 当旧 work 没有 `tool_resolution_trace` 时：
  - `resolution_mode = legacy_tool_index`
  - UI 标记为 legacy
- 041 的 `worker_profiles` canonical resource 继续是 Root Agent 静态真相来源

## 7. 非目标

042 的数据模型不包括：

- 多实例 Root Agent registry
- Tool marketplace
- 自动学习型工具推荐系统
- 专门的 weather/news/finance 业务对象
