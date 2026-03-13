# Contract Notes: Feature 042 Profile-First Chat + Agent Console

## 1. Chat API

### POST `/api/chat/send`

#### Request

新增可选字段：

```json
{
  "message": "检查一下这个项目的目录结构",
  "task_id": "task-optional",
  "agent_profile_id": "worker-profile-project-guide"
}
```

字段说明：

- `agent_profile_id`
  - 可选
  - 显式指定当前消息应绑定哪个 Agent
  - 缺省时走 `session > project > system` 默认解析

#### Response

当前响应体无需强制变更：

```json
{
  "task_id": "task-123",
  "status": "accepted",
  "stream_url": "/api/stream/task/task-123"
}
```

约定：

- 有效绑定结果通过任务流和 control-plane snapshot 可见
- 不要求立即在 `send` 响应体内重复回传完整 profile 信息

## 2. Runtime / Work Metadata

### 兼容字段

以下字段继续保留：

- `selected_worker_type`
- `selected_tools_json`
- `requested_worker_profile_id`
- `effective_worker_snapshot_id`

### 新增解释字段

建议进入 `Work.metadata` / runtime summary：

```json
{
  "chat_agent_binding": {
    "requested_agent_profile_id": "worker-profile-project-guide",
    "effective_agent_profile_id": "worker-profile-project-guide",
    "binding_source": "explicit",
    "binding_warnings": []
  },
  "tool_resolution_mode": "profile_first_core",
  "effective_tool_universe": {
    "profile_id": "worker-profile-project-guide",
    "profile_revision": 3,
    "tool_profile": "standard",
    "core_tools": ["project.inspect", "session.status", "workers.review"],
    "delegation_tools": ["workers.review", "subagents.spawn"],
    "discovery_entrypoints": ["mcp.tools.list"],
    "warnings": []
  },
  "tool_resolution_trace": {
    "mounted_tools": [],
    "blocked_tools": [],
    "degraded_reasons": []
  },
  "tool_resolution_warnings": []
}
```

## 3. Worker Profiles Canonical Resource

现有 `worker_profiles` resource 继续沿用，但建议为 UI 增加更直接的运行态解释字段。

### 建议增强字段

在 `dynamic_context` 或相邻字段中补充：

- `current_resolution_mode`
- `current_tool_warnings`
- `current_blocked_tools_count`
- `current_discovery_entrypoints`

目标：

- AgentCenter 不需要再从多个 resource 拼接“它为什么现在做不到”

## 4. Delegation / ControlPlane Resource

### 现有职责

- `delegation` 展示 work/runtime/progress
- `control plane` 负责资源聚合与深度诊断

### 042 要补充的解释字段

每条 work 至少要可读：

- `effective_agent_profile_id`
- `tool_resolution_mode`
- `mounted_tools`
- `blocked_tools`
- `tool_resolution_warnings`

这样可以直接回答：

- 当前 work 继承了哪个 Agent
- 这次挂载了哪些工具
- 哪些 delegation / web / browser 工具被阻止
- 原因是 policy / tool_profile / connector readiness 还是仅 discovery

## 5. Frontend View Contract

### ChatWorkbench

应新增：

- 当前 Agent badge / label
- 绑定来源说明（显式 / 继承 project / system fallback）
- 可选切换入口或跳转 Agent Console 的快捷入口

### AgentCenter

三栏主布局：

1. `Root Agents`
2. `Agent Detail`
3. `Runtime Inspector`

要求：

- 左栏负责“选谁”
- 中栏负责“它是什么”
- 右栏负责“它现在怎样”

### ControlPlane

仍展示：

- raw projection
- lineage
- tool resolution trace
- blocked reasons

但不再承担“默认用户第一眼要看什么”。

## 6. 兼容约束

- 旧 chat 请求体不带 `agent_profile_id` 仍必须正常工作
- 旧 work 没有 `tool_resolution_trace` 时，前端要能降级显示
- 旧前端若只读 `selected_tools_json`，也不应崩溃
- 041 的 Profile Studio actions / resources 不能被 042 破坏
