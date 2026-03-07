# Quickstart: Feature 019

## 目标

验证 019 的四条关键路径：

1. execution session 可查询；
2. execution events 可回放；
3. live input 可接入；
4. restart-after-waiting-input 可恢复。

## 建议命令

```bash
cd octoagent
uv run pytest \
  apps/gateway/tests/test_execution_api.py \
  apps/gateway/tests/test_task_runner.py \
  apps/gateway/tests/test_worker_runtime.py \
  packages/core/tests/test_models.py -q
```

## 手动验证思路

1. 启动 gateway app。
2. 创建一个使用交互式测试 LLM 的任务，使其进入 `WAITING_INPUT`。
3. 调用 `GET /api/tasks/{task_id}/execution`，确认 session 返回等待输入态。
4. 调用 `GET /api/tasks/{task_id}/execution/events`，确认可看到 `status / step / input_requested`。
5. 先不带 approval_id 调用 `POST /api/tasks/{task_id}/execution/input`，验证高风险输入会被拒绝。
6. 完成 approval 后再次提交输入，确认任务恢复并成功结束。
7. 在等待输入后重启 runner，再次提交输入，确认任务仍能恢复成功。
