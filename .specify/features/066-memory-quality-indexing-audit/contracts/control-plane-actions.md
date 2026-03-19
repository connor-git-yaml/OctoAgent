# Control Plane Action 契约: Feature 066

---

## 1. memory.sor.edit（新增）

**功能**: 用户通过 UI 编辑 SoR 记忆内容和/或 subject_key
**Category**: `memory`
**Risk Hint**: `medium`（不可逆：旧版本变为 superseded）

### 请求参数

```json
{
  "scope_id": "scope_xxx",
  "subject_key": "用户偏好/编程语言",
  "content": "Connor 偏好 Python 3.12+，使用 uv 管理依赖...",
  "new_subject_key": "",
  "expected_version": 3,
  "edit_summary": "更新 Python 版本偏好"
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `scope_id` | `str` | 是 | 记忆所属 scope |
| `subject_key` | `str` | 是 | 当前 subject_key（用于定位） |
| `content` | `str` | 是 | 新内容 |
| `new_subject_key` | `str` | 否 | 修改后的 subject_key（空则不变） |
| `expected_version` | `int` | 是 | 乐观锁——期望当前版本号 |
| `edit_summary` | `str` | 否 | 编辑摘要 |

### 执行流程

1. 查找 `scope_id` + `subject_key` 下 status=current 的 SoR
2. 检查 `expected_version` 是否匹配当前 SoR 版本（不匹配返回冲突错误）
3. 调用 `memory.propose_write(action=UPDATE, source="user_edit")`
4. 调用 `memory.validate_proposal()`
5. 调用 `memory.commit_memory()`——旧版本自动 superseded
6. 返回新版本 SoR 摘要

### 响应

成功：
```json
{
  "status": "ok",
  "message": "记忆已更新",
  "data": {
    "memory_id": "01JSOR_NEW",
    "subject_key": "用户偏好/编程语言",
    "version": 4,
    "updated_at": "2026-03-19T12:00:00Z"
  }
}
```

冲突：
```json
{
  "status": "error",
  "message": "版本冲突：期望版本 3，当前版本 4。请刷新后重试。",
  "error_code": "VERSION_CONFLICT"
}
```

---

## 2. memory.sor.archive（新增）

**功能**: 用户将 SoR 记忆归档（从 recall 排除，可恢复）
**Category**: `memory`
**Risk Hint**: `medium`

### 请求参数

```json
{
  "scope_id": "scope_xxx",
  "memory_id": "01JSOR_xxx",
  "expected_version": 3
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `scope_id` | `str` | 是 | 记忆所属 scope |
| `memory_id` | `str` | 是 | SoR memory_id |
| `expected_version` | `int` | 是 | 乐观锁 |

### 执行流程

1. 查找 SoR 记录，确认 status=current
2. 检查 `expected_version` 匹配
3. 如果是 Vault 层记忆（`SENSITIVE_PARTITIONS`），要求额外授权确认
4. 调用 `update_sor_status(memory_id, status="archived")`
5. 生成审计事件（包含操作人、时间、被归档的 subject_key）

### 响应

```json
{
  "status": "ok",
  "message": "记忆已归档",
  "data": {
    "memory_id": "01JSOR_xxx",
    "subject_key": "过时信息/旧项目",
    "new_status": "archived"
  }
}
```

---

## 3. memory.sor.restore（新增）

**功能**: 恢复已归档的 SoR 记忆为 current
**Category**: `memory`
**Risk Hint**: `low`

### 请求参数

```json
{
  "scope_id": "scope_xxx",
  "memory_id": "01JSOR_xxx"
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `scope_id` | `str` | 是 | 记忆所属 scope |
| `memory_id` | `str` | 是 | SoR memory_id |

### 执行流程

1. 查找 SoR 记录，确认 status=archived
2. 检查同 subject_key 下是否已有 status=current 的记录（若有，提示冲突）
3. 调用 `update_sor_status(memory_id, status="current")`
4. 生成审计事件

### 响应

```json
{
  "status": "ok",
  "message": "记忆已恢复",
  "data": {
    "memory_id": "01JSOR_xxx",
    "subject_key": "用户偏好/编程语言",
    "new_status": "current"
  }
}
```

---

## 4. memory.browse（新增）

**功能**: 前端 Memory UI 的 browse 查询接口
**Category**: `memory`
**Risk Hint**: `none`（只读）

### 请求参数

```json
{
  "prefix": "家庭/",
  "partition": "",
  "group_by": "partition",
  "offset": 0,
  "limit": 20
}
```

### 响应

与 Agent 工具 `memory.browse` 返回格式一致（参见 agent-tools.md）。

---

## 5. Action 定义清单

```python
# 新增到 _build_action_definitions()
definition("memory.sor.edit", "编辑记忆内容", category="memory", risk_hint="medium",
           params_schema={
               "type": "object",
               "required": ["scope_id", "subject_key", "content", "expected_version"],
           }),
definition("memory.sor.archive", "归档记忆", category="memory", risk_hint="medium",
           params_schema={
               "type": "object",
               "required": ["scope_id", "memory_id", "expected_version"],
           }),
definition("memory.sor.restore", "恢复已归档记忆", category="memory", risk_hint="low",
           params_schema={
               "type": "object",
               "required": ["scope_id", "memory_id"],
           }),
definition("memory.browse", "浏览记忆目录", category="memory"),
```
