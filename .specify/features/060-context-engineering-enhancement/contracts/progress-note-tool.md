# API Contract: progress_note Tool

**Feature**: 060 Context Engineering Enhancement
**Module**: `packages/tooling/src/octoagent/tooling/progress_note.py`
**Date**: 2026-03-17

## 概览

`progress_note` 是一个 Pydantic 工具，供 Worker 在执行长任务过程中记录结构化里程碑。记录持久化到 Artifact Store，上下文构建时自动注入最近 N 条笔记。

---

## 工具 Schema

### 基本信息

| 属性 | 值 |
|------|-----|
| `name` | `progress_note` |
| `description` | 记录任务执行的关键里程碑。每完成一个有意义的步骤后调用此工具，确保上下文压缩或进程重启后能从断点继续。 |
| `side_effect_level` | `none` |
| `category` | `agent_internal` |
| `tool_profile` | `minimal` (所有 Worker 可用) |

### 输入 Schema

```json
{
    "type": "object",
    "properties": {
        "step_id": {
            "type": "string",
            "minLength": 1,
            "description": "步骤标识（如 'step_1', 'data_collection', 'api_integration'）"
        },
        "description": {
            "type": "string",
            "minLength": 1,
            "description": "本步骤做了什么"
        },
        "status": {
            "type": "string",
            "enum": ["completed", "in_progress", "blocked"],
            "default": "completed",
            "description": "步骤状态"
        },
        "key_decisions": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
            "description": "本步骤的关键决策"
        },
        "next_steps": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
            "description": "接下来需要做什么"
        }
    },
    "required": ["step_id", "description"]
}
```

### 输出 Schema

```json
{
    "type": "object",
    "properties": {
        "note_id": {
            "type": "string",
            "description": "笔记唯一 ID"
        },
        "persisted": {
            "type": "boolean",
            "description": "是否成功持久化到 Artifact Store"
        }
    },
    "required": ["note_id", "persisted"]
}
```

---

## 行为规则

1. **写入**: 每次调用创建一个新的 Artifact（type: `progress-note`）
2. **幂等**: 相同 `step_id` 可多次调用，每次创建新笔记（代表同一步骤的状态更新），不覆盖旧笔记
3. **自动合并**: 当同一 `task_id + agent_session_id` 下笔记累积超过 50 条时，系统自动将旧笔记合并为一条汇总笔记
4. **上下文注入**: 上下文构建时，最近 5 条笔记的 `step_id + status + description` 摘要注入到 `ProgressNotes` 系统块

---

## 可见性规则

| 角色 | 可见性 | 机制 |
|------|--------|------|
| Worker（自身） | 自动注入 | ProgressNotes 系统块（最近 5 条） |
| Butler | control plane 查询 | `GET /api/artifacts?type=progress-note&task_id={task_id}` |
| Subagent | 不可见 | 独立 session + 绕过压缩机制 |

---

## 示例调用

### Worker 记录步骤完成

```json
{
    "step_id": "data_collection",
    "description": "从 GitHub API 获取了 42 个 PR 的元数据，耗时 12 秒",
    "status": "completed",
    "key_decisions": ["使用批量 API 而非逐条查询", "只拉取最近 30 天的 PR"],
    "next_steps": ["解析 PR diff", "提取变更摘要"]
}
```

### 返回

```json
{
    "note_id": "pn-01HXY-data_collection-01HXYZ123",
    "persisted": true
}
```

### 上下文注入示例

```
## Progress Notes

- [data_collection] completed: 从 GitHub API 获取了 42 个 PR 的元数据
  Next: 解析 PR diff, 提取变更摘要
- [diff_parsing] in_progress: 已解析 28/42 个 PR 的 diff
  Next: 完成剩余 14 个, 开始摘要生成
```

---

## Artifact 存储格式

| 字段 | 值 |
|------|-----|
| `artifact_id` | `pn-{task_id[:8]}-{step_id}-{ulid}` |
| `task_id` | 当前任务 ID |
| `name` | `progress-note:{step_id}` |
| `description` | `Progress note: {description[:80]}` |
| `mime_type` | `application/json` |
| `parts[0].part_type` | `json` |
| `parts[0].content` | ProgressNote JSON（见 data-model.md） |
| `metadata.type` | `progress-note` |
| `metadata.agent_session_id` | 当前 session ID |

---

## 错误处理

| 场景 | 行为 |
|------|------|
| Artifact Store 不可用 | 返回 `{"note_id": "...", "persisted": false}`，不阻断 Worker 执行 |
| step_id 为空 | 工具参数验证失败，返回 Pydantic ValidationError |
| description 为空 | 工具参数验证失败，返回 Pydantic ValidationError |
