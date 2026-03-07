# Contract: State / Artifact Mapping + Fixtures

## 状态映射

| OctoAgent 内部状态 | A2A TaskState | 备注 |
|---|---|---|
| `CREATED` | `submitted` | 通过 metadata 保留 `internal_status=CREATED` |
| `QUEUED` | `submitted` | 无损归并到 submitted |
| `RUNNING` | `working` | 直接映射 |
| `WAITING_INPUT` | `input-required` | 直接映射 |
| `WAITING_APPROVAL` | `input-required` | metadata 保留 `internal_status=WAITING_APPROVAL` |
| `PAUSED` | `working` | metadata 保留 `internal_status=PAUSED` |
| `SUCCEEDED` | `completed` | 终态一一对应 |
| `FAILED` | `failed` | 终态一一对应 |
| `CANCELLED` | `canceled` | 终态一一对应 |
| `REJECTED` | `rejected` | 终态一一对应 |

## Artifact 映射

| OctoAgent 字段 | A2A 字段 |
|---|---|
| `artifact_id` | `artifactId` |
| `name` | `name` |
| `description` | `description` |
| `parts` | `parts` |
| `append` | `append` |
| `last_chunk` | `lastChunk` |
| `version` / `hash` / `size` | `metadata.version` / `metadata.hash` / `metadata.size` |
| `storage_ref` | 作为 file/image part 的 `uri` 补充来源 |

## Fixture Catalog

本 feature 提供以下稳定 fixture 文件：

- `contracts/fixtures/task.json`
- `contracts/fixtures/update.json`
- `contracts/fixtures/cancel.json`
- `contracts/fixtures/result.json`
- `contracts/fixtures/error.json`
- `contracts/fixtures/heartbeat.json`

每个 fixture 都满足：

1. 使用统一 `schema_version`
2. 带 `trace_id`
3. 带 `idempotency_key`
4. 可被 `A2AMessage.model_validate()` 直接加载
5. 已纳入 `packages/protocol/tests/test_a2a_models.py` 校验

## 当前限制

- `append` / `lastChunk` 当前固定输出默认值 `false`
- replay 保护当前为内存实现，fixture 只覆盖 contract，不覆盖持久化 transport
