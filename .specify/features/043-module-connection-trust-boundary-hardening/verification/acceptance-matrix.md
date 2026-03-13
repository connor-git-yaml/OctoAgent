# Feature 043 Acceptance Matrix

## 目标

验证 043 是否真正把 trust boundary、ack 语义、typed metadata 和 partial degrade 收口成同一条连接主链。

## 矩阵

| Case ID | 场景 | 期望行为 | 验证方式 | 证据目标 |
|---|---|---|---|---|
| MB-001 | 用户通过 ingress metadata 传入 `agent_profile_id` / 恶意字段 | 这些字段不会直接进入 orchestrator control，也不会原样进入 runtime prompt | 自动化 | task/control metadata 分离 + prompt sanitizer test |
| MB-002 | chat 新建 task 时 `create_task()` 抛错 | `/api/chat/send` 返回非 2xx，不能继续 `accepted` | 自动化 | chat route fail-fast regression |
| MB-003 | chat 新建 task 后 enqueue 失败 | `/api/chat/send` 返回非 2xx，调用方能看到错误码 | 自动化 | chat route fail-fast regression |
| MB-004 | delegation request metadata 含 bool/int/object | dispatch envelope / A2A TASK payload 保留 typed contract，不被强制字符串化 | 自动化 | delegation/protocol regression |
| MB-005 | follow-up 未再次携带 `agent_profile_id/tool_profile` | turn-scoped control key 不再从旧轮残留恢复 | 自动化 | task service lifecycle regression |
| MB-006 | child task lineage 依赖 `parent_task_id/parent_work_id` | task-scoped lineage key 跨 follow-up 仍可恢复 | 自动化 | task service lifecycle regression |
| MB-007 | `/api/control/snapshot` 的 `memory` section 抛错 | snapshot 返回 `partial`，其余资源仍可用，`memory` 为 degraded fallback document | 自动化 | control plane snapshot regression |
| MB-008 | `/api/control/snapshot` 的 `imports` section 抛错 | snapshot 返回 `partial`，并带 `resource_errors.imports.code=RESOURCE_DEGRADED` | 自动化 | control plane snapshot regression |

## 通过标准

- 所有 P1 场景（MB-001 ~ MB-007）必须通过
- 不允许再出现：
  - 原始 untrusted metadata 进入 runtime system block
  - task/create/enqueue 失败后仍返回 `accepted`
  - snapshot 因单 section 异常直接 500
