# Data Model: Feature 018 — A2A-Lite Envelope + A2AStateMapper

## 1. A2AMessage

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | `str` | 协议版本，当前默认 `0.1` |
| `message_id` | `str` | 消息唯一标识 |
| `task_id` | `str` | 关联任务 |
| `context_id` | `str` | 对话上下文 ID |
| `from` / `to` | `str` | 源 / 目的 agent URI |
| `type` | `A2AMessageType` | TASK / UPDATE / CANCEL / RESULT / ERROR / HEARTBEAT |
| `idempotency_key` | `str` | 协议级幂等键 |
| `timestamp_ms` | `int` | 毫秒时间戳 |
| `trace` | `A2ATraceContext` | trace / span 关联 |
| `hop_count` / `max_hops` | `int` | 跳数控制 |
| `payload` | `dict[str, Any]` | 结构化消息体 |
| `metadata` / `extensions` | `dict[str, Any]` | 扩展信息 |

## 2. A2ATaskState

标准外部状态集合：

- `submitted`
- `working`
- `input-required`
- `completed`
- `canceled`
- `failed`
- `rejected`
- `auth-required`
- `unknown`

## 3. OctoArtifactView

协议侧 artifact 视图，兼容 core `Artifact` 并增加：

| 字段 | 说明 |
|---|---|
| `append` | 是否为流式追加 |
| `last_chunk` | 是否是最后一块 |
| `meta` | 额外治理信息 |

## 4. DeliveryAssessment

| 字段 | 类型 | 说明 |
|---|---|---|
| `decision` | `DeliveryDecision` | 接受 / duplicate / replay / unsupported-version / hop-limit-exceeded |
| `reason` | `str` | 结构化原因 |
| `existing_message_id` | `str | None` | 已存在消息时回填 |
| `existing_idempotency_key` | `str | None` | 已存在幂等键时回填 |
