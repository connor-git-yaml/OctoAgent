# Agent Tool 契约: Feature 066

---

## 1. memory.browse（新增工具）

**工具类型**: Agent 可调用工具
**注册位置**: `capability_pack.py`
**副作用等级**: `none`（只读）

### 参数 Schema

| 参数 | 类型 | 必需 | 默认值 | 说明 |
|------|------|------|--------|------|
| `prefix` | `str` | 否 | `""` | subject_key 前缀匹配（如 `"家庭/"` `"用户偏好/"` ） |
| `partition` | `str` | 否 | `""` | partition 筛选（如 `"core"` `"solution"` ） |
| `scope_id` | `str` | 否 | `""` | scope 筛选（空则使用上下文 scope） |
| `group_by` | `str` | 否 | `"partition"` | 分组维度：`"partition"` / `"scope"` / `"prefix"` |
| `offset` | `int` | 否 | `0` | 分页偏移 |
| `limit` | `int` | 否 | `20` | 返回条数上限，最大 100 |
| `project_id` | `str` | 否 | `""` | 项目 ID |
| `workspace_id` | `str` | 否 | `""` | 工作区 ID |

### 返回格式

```json
{
  "groups": [
    {
      "key": "core",
      "count": 12,
      "items": [
        {
          "subject_key": "用户偏好/编程语言",
          "summary": "Connor 偏好 Python 和 TypeScript...",
          "status": "current",
          "version": 3,
          "updated_at": "2026-03-15T10:30:00Z"
        }
      ],
      "latest_updated_at": "2026-03-18T15:00:00Z"
    }
  ],
  "total_count": 42,
  "has_more": true,
  "offset": 0,
  "limit": 20
}
```

### 降级行为

- 向量后端不可用时：正常运行（browse 不走向量检索）
- 指定 scope 无记忆时：返回 `{"groups": [], "total_count": 0, "has_more": false}`

---

## 2. memory.search（参数扩展）

**变更类型**: 新增可选参数（向后兼容）

### 新增参数

| 参数 | 类型 | 必需 | 默认值 | 说明 |
|------|------|------|--------|------|
| `derived_type` | `str` | 否 | `""` | 按派生类型筛选：`"profile"` / `"tom"` / `"entity"` / `"relation"` |
| `status` | `str` | 否 | `""` | 按 SoR 状态筛选：`"current"` / `"archived"` / `"superseded"` |
| `updated_after` | `str` | 否 | `""` | 更新时间下界（ISO 8601） |
| `updated_before` | `str` | 否 | `""` | 更新时间上界（ISO 8601） |

### 向后兼容保证

- 所有新参数均为可选，默认值为空字符串
- 未提供时行为与当前版本完全一致
- 现有的 `query` / `scope_id` / `partition` / `layer` / `limit` 参数不变

---

## 3. memory.write（metadata 扩展约定）

**变更类型**: metadata 字段约定（不改签名）

### 用户编辑场景

当 Control Plane `memory.sor.edit` action 转发为 `propose_write` 时：

```python
metadata = {
    "source": "user_edit",
    "edit_summary": "<用户编辑摘要>",
}
```

### Consolidation MERGE 场景

```python
metadata = {
    "source": "consolidate",
    "merge_source_ids": ["mem_id_1", "mem_id_2", "mem_id_3"],
}
```

### Consolidation REPLACE 场景

```python
metadata = {
    "source": "consolidate",
    "reason": "replace",
}
```

### Solution 提取场景

```python
metadata = {
    "source": "solution_extract",
}
```
