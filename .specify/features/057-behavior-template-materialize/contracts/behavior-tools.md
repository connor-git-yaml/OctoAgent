# API Contract: behavior.read_file / behavior.write_file LLM Tools

**Feature**: 057-behavior-template-materialize
**Date**: 2026-03-16

---

## Tool: behavior.read_file

### Metadata

| Field | Value |
|-------|-------|
| name | `behavior.read_file` |
| side_effect_level | `none` |
| tool_profile | `ToolProfile.MINIMAL` |
| tool_group | `behavior` |
| tags | `["behavior", "file", "read", "context"]` |
| worker_types | `["ops", "research", "dev", "general"]` |
| manifest_ref | `builtin://behavior.read_file` |

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_path` | `str` | Yes | 行为文件相对路径（相对于 project_root），如 `behavior/system/USER.md` |

### Return (JSON string)

```json
{
  "file_path": "behavior/system/USER.md",
  "content": "...",
  "exists": true,
  "budget_chars": 1800,
  "current_chars": 156
}
```

如果文件不存在（尚未 materialize），返回默认模板内容：

```json
{
  "file_path": "behavior/system/USER.md",
  "content": "...(default template content)...",
  "exists": false,
  "budget_chars": 1800,
  "current_chars": 156,
  "source": "default_template"
}
```

### Error Cases

| Error | Condition |
|-------|-----------|
| `MISSING_PARAM` | `file_path` 为空 |
| `INVALID_PATH` | 路径包含 `..` 或超出 project_root 边界 |
| `FILE_READ_ERROR` | 磁盘 IO 异常 |

---

## Tool: behavior.write_file

### Metadata

| Field | Value |
|-------|-------|
| name | `behavior.write_file` |
| side_effect_level | `reversible` |
| tool_profile | `ToolProfile.STANDARD` |
| tool_group | `behavior` |
| tags | `["behavior", "file", "write", "context"]` |
| worker_types | `["ops", "research", "dev", "general"]` |
| manifest_ref | `builtin://behavior.write_file` |

### Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | `str` | Yes | - | 行为文件相对路径 |
| `content` | `str` | Yes | - | 新的文件内容 |
| `confirmed` | `bool` | No | `false` | 当 review_mode=REVIEW_REQUIRED 时，是否已经过用户确认 |

### Return (JSON string)

#### Case 1: Proposal 模式（confirmed=false 且 review_mode=REVIEW_REQUIRED）

```json
{
  "file_path": "behavior/system/USER.md",
  "proposal": true,
  "review_mode": "review_required",
  "current_content": "...(current content)...",
  "proposed_content": "...(proposed content)...",
  "current_chars": 156,
  "proposed_chars": 280,
  "budget_chars": 1800,
  "message": "请向用户展示修改摘要并请求确认，确认后再次调用并设置 confirmed=true"
}
```

#### Case 2: 写入成功（confirmed=true 或 review_mode=none）

```json
{
  "file_path": "behavior/system/USER.md",
  "written": true,
  "chars_written": 280,
  "budget_chars": 1800
}
```

#### Case 3: 预算超出

```json
{
  "file_path": "behavior/system/USER.md",
  "written": false,
  "error": "BUDGET_EXCEEDED",
  "current_chars": 2100,
  "budget_chars": 1800,
  "exceeded_by": 300,
  "message": "内容超出字符预算 300 字符，请精简后重试"
}
```

### Error Cases

| Error | Condition |
|-------|-----------|
| `MISSING_PARAM` | `file_path` 或 `content` 为空 |
| `INVALID_PATH` | 路径包含 `..` 或超出 behavior 目录边界 |
| `BUDGET_EXCEEDED` | 内容字符数超出 BEHAVIOR_FILE_BUDGETS |
| `FILE_WRITE_ERROR` | 磁盘 IO 异常 |

---

## Path Validation Rules

1. `file_path` 必须是相对路径（不以 `/` 开头）
2. 路径 resolve 后必须在 `project_root` 内（防止 path traversal）
3. 路径必须在 behavior 目录体系内（`behavior/` 或 `projects/*/behavior/`）
4. 不接受 `..` 路径组件

## Budget Reference

| file_id | budget_chars |
|---------|-------------|
| AGENTS.md | 3200 |
| USER.md | 1800 |
| PROJECT.md | 2400 |
| KNOWLEDGE.md | 2200 |
| TOOLS.md | 3200 |
| BOOTSTRAP.md | 2200 |
| SOUL.md | 1600 |
| IDENTITY.md | 1600 |
| HEARTBEAT.md | 1600 |
