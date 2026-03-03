# Quickstart: Feature 012

## 1. 运行增量检查

```bash
cd octoagent
uv run ruff check \
  packages/tooling/src/octoagent/tooling/broker.py \
  packages/tooling/src/octoagent/tooling/models.py \
  packages/tooling/src/octoagent/tooling/protocols.py \
  apps/gateway/src/octoagent/gateway/routes/health.py \
  apps/gateway/src/octoagent/gateway/middleware/logging_config.py \
  apps/gateway/src/octoagent/gateway/middleware/logging_mw.py \
  apps/gateway/src/octoagent/gateway/middleware/trace_mw.py
```

## 2. 运行核心测试

```bash
cd octoagent
uv run pytest packages/tooling/tests -q
uv run pytest apps/gateway/tests/test_us12_health.py apps/gateway/tests/test_us7_observability.py -q
```

## 3. 手动验证（可选）

```bash
cd octoagent
uv run uvicorn octoagent.gateway.main:app --reload
```

调用：

```bash
curl -s http://127.0.0.1:8000/ready | jq
```

观察 `subsystems` 与 `diagnostics.tool_registry.diagnostics_count` 字段。
