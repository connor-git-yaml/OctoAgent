# Quickstart: Feature 010 Checkpoint & Resume Engine

## 1. 运行测试前准备

```bash
cd octoagent
uv sync
```

## 2. 运行 Feature 010 相关测试（目标）

```bash
# 单测：checkpoint store + resume engine + ledger
uv run pytest octoagent/packages/core/tests -k "checkpoint or resume or ledger"

# 集成：task runner 重启恢复 + 并发恢复冲突
uv run pytest octoagent/tests/integration -k "f010 or resume"
```

## 3. 手动验收路径（目标）

1. 创建一个会跨多个节点执行的任务。
2. 在中间节点执行后模拟进程重启。
3. 启动后观察任务是否从最近成功 checkpoint 恢复。
4. 对同一 task 连续触发 2 次 resume，确认不可逆副作用无重复执行。
5. 注入损坏 checkpoint，确认进入可审计失败终态并给出恢复建议。

## 4. 验收日志要点

- 事件流应包含：`CHECKPOINT_SAVED -> RESUME_STARTED -> RESUME_SUCCEEDED/RESUME_FAILED`
- 失败路径必须包含 `failure_type`
- 任务详情应可看到 `latest_checkpoint_id`
