# Implementation Plan: Feature 012 Logfire + Health/Plugin Diagnostics

## 1. 实施目标

在不破坏现有网关主链路的前提下，完成三项最小增量能力：

1. ToolBroker fail-open 注册能力（`try_register` + diagnostics）
2. `/ready` 子系统健康扩展（`subsystems` + diagnostics 摘要）
3. Logfire 初始化增强（开关控制 + 初始化失败降级）

## 2. 设计决策

1. **register/try_register 双轨语义**
- `register()` 保持 strict 失败（兼容既有调用方）
- `try_register()` 提供 fail-open 返回体与诊断累积

2. **健康检查“增强不加压”**
- `checks`（core readiness）保持现有判定逻辑
- `subsystems` 仅做可观测诊断，不额外改变 `ready/not_ready` 结果

3. **Logfire 初始化 fail-open**
- 仅在 `LOGFIRE_SEND_TO_LOGFIRE=true` 时尝试初始化
- 失败只打 warning，不阻断应用启动

## 3. 变更范围

### tooling

- `packages/tooling/src/octoagent/tooling/models.py`
- `packages/tooling/src/octoagent/tooling/protocols.py`
- `packages/tooling/src/octoagent/tooling/broker.py`
- `packages/tooling/src/octoagent/tooling/__init__.py`

### gateway

- `apps/gateway/src/octoagent/gateway/routes/health.py`
- `apps/gateway/src/octoagent/gateway/middleware/logging_config.py`
- `apps/gateway/src/octoagent/gateway/middleware/logging_mw.py`
- `apps/gateway/src/octoagent/gateway/middleware/trace_mw.py`

### tests

- `packages/tooling/tests/test_broker.py`
- `packages/tooling/tests/test_models.py`
- `packages/tooling/tests/test_protocols_mock.py`
- `apps/gateway/tests/test_us12_health.py`
- `apps/gateway/tests/test_us7_observability.py`

## 4. 风险与缓解

- 风险：中间件上下文串扰导致 trace_id 污染
  - 缓解：`trace_id` 仅基于 header/state/path/request_id 计算，不复用旧 context
- 风险：子系统探测访问缺失属性抛错
  - 缓解：统一 `getattr` 容错并返回 `unavailable`

## 5. GATE 记录

- GATE_DESIGN：PASS（用户“推进需求 012”批准继续）
- GATE_TASKS：PASS（任务拆解后进入实现）
