# Contract: Worker Profiles Resource

## Resource

- `GET /api/control/resources/worker-profiles`

## Purpose

返回 Root Agent 的正式 profile 视图，第一阶段按 `singleton` 模式组织：

- 静态配置
- 动态运行上下文
- 当前作用域

## Response Shape

```json
{
  "resource_type": "worker_profiles",
  "resource_id": "worker-profiles:overview",
  "active_project_id": "project-default",
  "active_workspace_id": "workspace-default",
  "profiles": [
    {
      "profile_id": "singleton:research",
      "name": "Research Root Agent",
      "scope": "system",
      "project_id": "",
      "mode": "singleton",
      "summary": "适合资料检索、归纳和分析。",
      "static_config": {
        "base_archetype": "research",
        "model_alias": "main",
        "tool_profile": "minimal",
        "default_tool_groups": ["project", "artifact", "network"],
        "runtime_kinds": ["worker", "subagent"],
        "capabilities": ["research", "analysis", "summarize"]
      },
      "dynamic_context": {
        "active_project_id": "project-default",
        "active_workspace_id": "workspace-default",
        "active_work_count": 1,
        "running_work_count": 1,
        "attention_work_count": 0,
        "latest_work_id": "01HX...",
        "latest_task_id": "01HX...",
        "latest_work_title": "调研天气与出行建议",
        "latest_work_status": "running",
        "latest_target_kind": "subagent",
        "current_selected_tools": ["web.search"],
        "updated_at": "2026-03-12T12:00:00Z"
      }
    }
  ],
  "summary": {
    "profile_count": 4,
    "singleton_count": 4,
    "active_count": 2,
    "attention_count": 1
  }
}
```

## Rules

- 第一阶段 `mode` 固定为 `singleton`
- `profiles[]` 允许来自系统 starter templates 与后续正式 profile store
- `dynamic_context` 必须以当前 control-plane 选中的 project/workspace 为事实源
- 当前没有运行 work 时，`dynamic_context` 仍必须返回零值结构，而不是缺字段

## Compatibility

- 不删除现有 `capability_pack.pack.worker_profiles`
- 不删除现有 `delegation.works[].selected_worker_type`
- 新控制面优先消费 `worker-profiles` 资源，旧页面可继续消费 legacy 字段
