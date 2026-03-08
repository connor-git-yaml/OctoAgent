# Contract: Delegation Plane API

## 1. Snapshot / Resources

- `GET /api/control/resources/capabilities`
  - 返回 `CapabilityPackDocument`
- `GET /api/control/resources/delegation`
  - 返回 `DelegationPlaneDocument`
- `GET /api/control/resources/pipelines`
  - 返回 `SkillPipelineDocument`

这些资源也必须出现在 `GET /api/control/snapshot` 中。

## 2. Actions

新增 action ids：

- `delegation.refresh`
- `delegation.work.cancel`
- `delegation.work.escalate`
- `delegation.work.retry`
- `pipeline.run.resume`
- `pipeline.node.retry`

所有动作都继续使用：

- `ActionRequestEnvelope`
- `ActionResultEnvelope`
- `ControlPlaneEvent`

## 3. Telegram Aliases

- `/worker status` -> `delegation.refresh`
- `/subagent cancel <work_id>` -> `delegation.work.cancel`
- `/subagent escalate <work_id>` -> `delegation.work.escalate`
- `/skill retry <run_id> <node_id>` -> `pipeline.node.retry`

## 4. Result Codes

- `WORK_CANCELLED`
- `WORK_ESCALATED`
- `WORK_RETRY_ACCEPTED`
- `PIPELINE_RESUMED`
- `PIPELINE_NODE_RETRIED`
- `DELEGATION_REFRESHED`
- `WORK_NOT_FOUND`
- `PIPELINE_RUN_NOT_FOUND`
- `PIPELINE_NODE_NOT_RETRYABLE`

## 5. Event Family

- `control.action.requested/completed/rejected/deferred`
- `control.resource.projected`

metadata 必须至少包含：

- `work_id`
- `pipeline_run_id`
- `route_reason`
- `worker_type`

## 6. Compatibility

- 不引入平行 DTO
- 不绕过 026 的 route shape
- 不引入 surface-private canonical field
