# Contract: memory.write Tool

**Feature**: 065-memory-automation-pipeline
**Type**: Agent Tool Contract
**Date**: 2026-03-19

## Tool Identity

| 属性 | 值 |
|------|-----|
| name | `memory.write` |
| tool_group | `memory` |
| side_effect_level | `REVERSIBLE` |
| tool_profile | `MINIMAL` |
| manifest_ref | `builtin://memory.write` |

## Tool Signature

```python
async def memory_write(
    subject_key: str,
    content: str,
    partition: str = "work",
    evidence_refs: list[dict[str, str]] | None = None,
    scope_id: str = "",
    project_id: str = "",
    workspace_id: str = "",
) -> str:
```

## Parameters

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `subject_key` | `str` | YES | - | 记忆主题标识，用 `/` 分层（如 `用户偏好/编程语言`） |
| `content` | `str` | YES | - | 记忆内容，完整的陈述句 |
| `partition` | `str` | NO | `"work"` | 业务分区：core/profile/work/health/finance/chat |
| `evidence_refs` | `list[dict]` | NO | `None` | 证据引用 `[{"ref_id": "...", "ref_type": "message"}]` |
| `scope_id` | `str` | NO | `""` | 可选 scope 指定（空则自动解析） |
| `project_id` | `str` | NO | `""` | 可选 project 指定（空则自动解析） |
| `workspace_id` | `str` | NO | `""` | 可选 workspace 指定（空则自动解析） |

## Return Value

JSON 字符串，结构如下：

### 成功 (committed)

```json
{
  "status": "committed",
  "action": "add",
  "subject_key": "用户偏好/编程语言",
  "memory_id": "01JXYZ...",
  "version": 1,
  "scope_id": "scope-abc",
  "partition": "work"
}
```

### 更新成功 (committed, update)

```json
{
  "status": "committed",
  "action": "update",
  "subject_key": "用户偏好/编程语言",
  "memory_id": "01JXYZ...",
  "version": 2,
  "scope_id": "scope-abc",
  "partition": "work"
}
```

### 验证失败 (rejected)

```json
{
  "status": "rejected",
  "action": "add",
  "subject_key": "用户偏好/编程语言",
  "errors": ["ADD proposal 命中了已存在的 current，请改用 UPDATE"],
  "scope_id": "scope-abc"
}
```

### 参数错误

```json
{
  "error": "INVALID_PARTITION",
  "message": "无效的 partition 值 'unknown'，有效值为: core, profile, work, health, finance, chat"
}
```

### Scope 解析失败

```json
{
  "error": "SCOPE_UNRESOLVED",
  "message": "无法解析 memory scope，请确认 project 和 workspace 配置"
}
```

## Internal Flow

```
1. _resolve_runtime_project_context() -> project, workspace
2. _resolve_memory_scope_ids() -> scope_ids[0] 取第一个
3. memory_service = get_memory_service(project, workspace)
4. existing = memory_store.get_current_sor(scope_id, subject_key)
5. if existing:
     action = UPDATE, expected_version = existing.version
   else:
     action = ADD, expected_version = None
6. proposal = memory.propose_write(...)
7. validation = memory.validate_proposal(proposal.proposal_id)
8. if validation.accepted:
     result = memory.commit_memory(proposal.proposal_id)
     return committed response
   else:
     return rejected response
```

## Side Effects

- **SQLite**: 写入 WriteProposal, FragmentRecord (commit 内部), SorRecord
- **LanceDB**: 异步同步 SoR 向量索引（commit_memory 内部）
- **Vault**: 若 partition 属于 SENSITIVE_PARTITIONS，额外写入 VaultRecord

## Error Handling

| 场景 | 处理 |
|------|------|
| `subject_key` 为空 | 返回 `{"error": "MISSING_PARAM", "message": "subject_key 不能为空"}` |
| `content` 为空 | 返回 `{"error": "MISSING_PARAM", "message": "content 不能为空"}` |
| `partition` 无效 | 返回 `{"error": "INVALID_PARTITION", ...}` |
| scope 解析失败 | 返回 `{"error": "SCOPE_UNRESOLVED", ...}` |
| validate_proposal 失败 | 返回 `{"status": "rejected", "errors": [...]}` |
| commit_memory 异常 | 返回 `{"error": "COMMIT_FAILED", "message": "..."}` |
| 数据库异常 | 返回 `{"error": "INTERNAL_ERROR", "message": "记忆写入失败，请重试"}` |

## Concurrency

- **乐观锁**: UPDATE 时 `expected_version` 由工具内部自动获取，validate_proposal 检查版本一致性
- **冲突检测**: 若并发 UPDATE 导致版本不匹配，validate_proposal 返回 rejected，Agent 可重试
- **幂等性**: 同一 subject_key 的重复 ADD 被 validate_proposal 拒绝并建议改用 UPDATE
