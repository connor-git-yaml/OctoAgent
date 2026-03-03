# Contract: Observability + Tooling Diagnostics (Feature 012)

## 1. ToolBroker API

### `register(tool_meta, handler) -> None`

- 语义：strict
- 冲突行为：抛 `ToolRegistrationError`

### `try_register(tool_meta, handler) -> RegisterToolResult`

- 语义：fail-open
- 冲突行为：返回 `ok=false`，并写入 `registry_diagnostics`

返回结构：

```json
{
  "ok": false,
  "tool_name": "echo_tool",
  "message": "Tool 'echo_tool' already registered ...",
  "error_type": "ToolRegistrationError"
}
```

### `registry_diagnostics -> list[RegistryDiagnostic]`

- 只读快照，不暴露内部可变列表引用

## 2. Readiness API

### `GET /ready`

响应新增字段：

```json
{
  "status": "ready",
  "profile": "core",
  "checks": {
    "sqlite": "ok",
    "artifacts_dir": "ok",
    "disk_space_mb": 1024,
    "litellm_proxy": "skipped"
  },
  "subsystems": {
    "orchestrator": "unavailable",
    "worker_runtime": "ok",
    "checkpoint": "ok",
    "watchdog": "ok",
    "tool_registry": "degraded"
  },
  "diagnostics": {
    "tool_registry": {
      "diagnostics_count": 1
    }
  }
}
```

约束：

1. `checks` 维持 readiness 主判定逻辑。
2. `subsystems` 为诊断增强层，允许组件缺失返回 `unavailable`。
