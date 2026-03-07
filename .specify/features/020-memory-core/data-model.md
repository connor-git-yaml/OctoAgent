# Feature 020 Data Model

## 枚举

### MemoryLayer

- `fragment`
- `sor`
- `vault`

### MemoryPartition

- `core`
- `profile`
- `work`
- `health`
- `finance`
- `chat`

### SorStatus

- `current`
- `superseded`
- `deleted`

### WriteAction

- `add`
- `update`
- `delete`
- `none`

### ProposalStatus

- `pending`
- `validated`
- `rejected`
- `committed`

## 实体

### EvidenceRef

| 字段 | 类型 | 说明 |
|---|---|---|
| `ref_id` | `str` | 指向 fragment / artifact / external document 的稳定引用 |
| `ref_type` | `str` | `fragment` / `artifact` / `doc` / `message_window` |
| `snippet` | `str | None` | 可选摘要，不放原文 |

### FragmentRecord

| 字段 | 类型 | 说明 |
|---|---|---|
| `fragment_id` | `str` | ULID |
| `scope_id` | `str` | project/chat 等 scope |
| `partition` | `MemoryPartition` | 业务分区 |
| `content` | `str` | 摘要内容 |
| `metadata` | `dict[str, Any]` | tags/source/type |
| `evidence_refs` | `list[EvidenceRef]` | 证据链 |
| `created_at` | `datetime` | 创建时间 |

### SorRecord

| 字段 | 类型 | 说明 |
|---|---|---|
| `memory_id` | `str` | ULID |
| `scope_id` | `str` | scope |
| `partition` | `MemoryPartition` | 业务分区 |
| `subject_key` | `str` | 稳定主题键 |
| `content` | `str` | 当前定稿 |
| `version` | `int` | 从 1 递增 |
| `status` | `SorStatus` | `current` / `superseded` / `deleted` |
| `metadata` | `dict[str, Any]` | 可扩展字段 |
| `evidence_refs` | `list[EvidenceRef]` | 证据链 |
| `created_at` | `datetime` | 创建时间 |
| `updated_at` | `datetime` | 更新时间 |

### VaultRecord

| 字段 | 类型 | 说明 |
|---|---|---|
| `vault_id` | `str` | ULID |
| `scope_id` | `str` | scope |
| `partition` | `MemoryPartition` | 通常为敏感分区 |
| `subject_key` | `str` | 稳定主题键 |
| `summary` | `str` | 可安全展示的摘要 |
| `content_ref` | `str` | 原文或密文引用 |
| `metadata` | `dict[str, Any]` | 审计/脱敏标记 |
| `evidence_refs` | `list[EvidenceRef]` | 证据链 |
| `created_at` | `datetime` | 创建时间 |

### WriteProposal

| 字段 | 类型 | 说明 |
|---|---|---|
| `proposal_id` | `str` | ULID |
| `scope_id` | `str` | scope |
| `partition` | `MemoryPartition` | 目标分区 |
| `action` | `WriteAction` | `add/update/delete/none` |
| `subject_key` | `str | None` | 主题键 |
| `content` | `str | None` | 新值或摘要 |
| `rationale` | `str` | 提案理由 |
| `confidence` | `float` | 0~1 |
| `evidence_refs` | `list[EvidenceRef]` | 证据链 |
| `expected_version` | `int | None` | update/delete 时的当前版本 |
| `is_sensitive` | `bool` | 是否路由 Vault |
| `status` | `ProposalStatus` | 当前状态 |
| `validation_errors` | `list[str]` | 拒绝原因 |
| `created_at` | `datetime` | 创建时间 |
| `validated_at` | `datetime | None` | 验证时间 |
| `committed_at` | `datetime | None` | 提交时间 |

## 不变量

1. `memory_sor` 中同一 `scope_id + subject_key` 只允许一条 `status=current`。
2. `FragmentRecord` append-only。
3. `WriteProposal.action != none` 时必须带 `subject_key` 和 `evidence_refs`。
4. Vault 默认不可检索。
5. `before_compaction_flush()` 不能直接改写 SoR。

