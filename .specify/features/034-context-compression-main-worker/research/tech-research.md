# Tech Research — Feature 034

## Agent Zero 实现复核

### 1. 历史压缩核心

参考：

- `_references/opensource/agent-zero/python/helpers/history.py`

结论：

- `History.compress()` 会把历史按 topic/bulk 压缩，而不是简单裁切
- 当前 topic 保留更多上下文，旧 topic 压缩更激进
- 总体思路是“recent raw + older summarized”

### 2. 压缩嵌入主循环，而不是孤立 helper

参考：

- `_references/opensource/agent-zero/python/extensions/message_loop_end/_10_organize_history.py`
- `_references/opensource/agent-zero/python/extensions/message_loop_prompts_before/_90_organize_history_wait.py`
- `_references/opensource/agent-zero/agent.py`

结论：

- Agent Zero 在 message loop 结束后异步启动压缩
- 下一次 prompt 前如果 history 仍超限，就等待压缩完成
- 这说明压缩是 loop lifecycle 的一部分，而不是离线维护任务

## OctoAgent 接入点判定

### 1. 真正可用的接入点是 `TaskService.process_task_with_llm()`

原因：

- chat route 最终走 `TaskRunner` -> `TaskService`
- worker 路径最终也走 `TaskService`
- 这里是主 Agent 与 Worker 的共同汇合点

因此，如果 034 不改 `TaskService`，就不可能对用户真正可用。

### 2. Subagent 必须显式绕过

当前仓库的 delegation/runtime 边界要求 Subagent 保持更轻的上下文心智模型。034 在技术上通过：

- `dispatch_metadata.target_kind == "subagent"`
- `worker_capability == "subagent"`

两个条件显式绕过。

### 3. Memory 应只吃 evidence，不吃事实旁路

`docs/blueprint.md` 已把 `before_compaction_flush()` 定义为 cheap/summarizer 产出的承接钩子，而不是 SoR 直写口。对应实现选择：

- compaction summary -> artifact
- request context -> artifact
- `MemoryMaintenanceCommand(kind=FLUSH)` -> audit + fragment/proposal path

## 技术结论

Feature 034 的最佳实现不是复刻 Agent Zero 的线程式 extension hook，而是把同样的治理语义内联到 OctoAgent 现有的 per-request `TaskService` 中。

