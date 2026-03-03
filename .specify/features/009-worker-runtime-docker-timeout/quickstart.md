# Quickstart: Feature 009 Worker Runtime

## 1. 运行前环境变量

```bash
export OCTOAGENT_WORKER_MAX_STEPS=3
export OCTOAGENT_WORKER_TIMEOUT_FIRST_OUTPUT_S=30
export OCTOAGENT_WORKER_TIMEOUT_BETWEEN_OUTPUT_S=15
export OCTOAGENT_WORKER_TIMEOUT_MAX_EXEC_S=180
export OCTOAGENT_WORKER_DOCKER_MODE=preferred   # disabled|preferred|required
```

## 2. 运行测试（目标子集）

```bash
cd octoagent
uv run pytest apps/gateway/tests/test_worker_runtime.py -q
uv run pytest apps/gateway/tests/test_task_runner.py -q
uv run pytest tests/integration/test_f009_worker_runtime_flow.py -q
```

## 3. 手动验证场景

1. 普通任务（standard profile）成功执行并回传 runtime 字段。
2. privileged 未授权被拒绝；加 `metadata.privileged_approved=true` 后放行。
3. 慢速任务触发 `max_exec` 超时并进入 FAILED。
4. 任务 RUNNING 时调用 `/api/tasks/{id}/cancel`，终态为 CANCELLED。
