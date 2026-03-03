# Quickstart: Feature 008 Orchestrator Skeleton

## 1. 环境准备

在仓库根目录执行：

```bash
cd octoagent
uv sync
```

## 2. 运行新增测试

```bash
# Orchestrator 单元测试
uv run pytest apps/gateway/tests/test_orchestrator.py -q

# TaskRunner 回归（已接入 orchestrator）
uv run pytest apps/gateway/tests/test_task_runner.py -q

# F008 集成测试
uv run pytest tests/integration/test_f008_orchestrator_flow.py -q
```

## 3. 手动验证（可选）

```bash
# 启动 Gateway
uv run uvicorn octoagent.gateway.main:app --reload --port 8000
```

另开终端调用:

```bash
curl -X POST http://127.0.0.1:8000/api/message \
  -H 'Content-Type: application/json' \
  -d '{"text":"hello orchestrator", "idempotency_key":"f008-manual-001"}'
```

预期:
- Task 最终进入 `SUCCEEDED`
- 事件流包含 `ORCH_DECISION`、`WORKER_DISPATCHED`、`WORKER_RETURNED`

## 4. 高风险 gate 验证（单测为主）

通过单测构造 `risk_level=HIGH` 且无授权上下文，预期:
- 不派发 Worker
- `WORKER_DISPATCHED` 不出现
- 任务进入失败终态并带 gate 原因
