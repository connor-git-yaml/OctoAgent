# Quickstart: Feature 018 — A2A-Lite Envelope + A2AStateMapper

## 1. 构造标准 TASK 消息

```python
from octoagent.core.models import DispatchEnvelope
from octoagent.protocol import build_task_message

envelope = DispatchEnvelope(
    dispatch_id="dispatch-001",
    task_id="task-001",
    trace_id="trace-001",
    contract_version="1.0",
    route_reason="health-check",
    worker_capability="worker.ops",
    hop_count=1,
    max_hops=3,
    user_text="检查 Docker 是否可用",
)

message = build_task_message(
    envelope,
    context_id="thread-001",
    to_agent="agent://worker.ops",
)
```

## 2. 映射内部状态

```python
from octoagent.core.models import TaskStatus
from octoagent.protocol import A2AStateMapper

a2a_state = A2AStateMapper.to_a2a(TaskStatus.WAITING_APPROVAL)
internal_state = A2AStateMapper.from_a2a(a2a_state)
```

## 3. 生成标准 fixture

- 直接读取 `.specify/features/018-a2a-lite-envelope/contracts/fixtures/`
- fixture 文件已被 `packages/protocol/tests/test_a2a_models.py` 校验

## 4. 检查 duplicate / replay

```python
from octoagent.protocol import A2AReplayProtector

protector = A2AReplayProtector()
assessment = protector.inspect(message)
```
