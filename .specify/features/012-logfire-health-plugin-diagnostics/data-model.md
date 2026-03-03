# Data Model: Feature 012

## 1. RegisterToolResult

用途：`ToolBroker.try_register()` 的结构化返回体。

字段：
- `ok: bool` 注册是否成功
- `tool_name: str` 工具名
- `message: str` 结果说明
- `error_type: str | None` 失败类型（成功时为空）

## 2. RegistryDiagnostic

用途：记录一次注册失败或告警诊断。

字段：
- `tool_name: str`
- `error_type: str`
- `message: str`
- `timestamp: datetime`

## 3. ReadyResponseExtension

`GET /ready` 响应新增：

- `subsystems: dict[str, str]`
  - 值域：`ok` / `unavailable` / `degraded`
  - 关注子系统：`orchestrator` / `worker_runtime` / `checkpoint` / `watchdog` / `tool_registry`
- `diagnostics: dict[str, dict[str, int]]`
  - 当前包含：`tool_registry.diagnostics_count`

## 4. 兼容性约束

1. `register()` 行为不变：冲突时抛异常。
2. `try_register()` 与 `registry_diagnostics` 为增量能力，不影响既有调用。
3. `/ready` 的 `checks` 与 `status` 判定保持向后兼容。
