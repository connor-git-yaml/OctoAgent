# Contract: Project CLI Main Path

## 1. Command Surface

### `octo project create`

**Input**

- `--name`
- `--slug`
- `--set-active/--no-set-active`
- `--description`

**Output**

- `project_id`
- `slug`
- `active_project_changed: bool`
- redacted summary

**Behavior**

- 基于 025-A canonical `Project` / `Workspace` 创建新 project
- 默认创建 primary workspace
- 可选立即设为 active project

### `octo project select`

**Input**

- `project_id | slug`

**Output**

- `ProjectSelectorDocument` 语义兼容 summary

**Behavior**

- 更新 active project selection state
- 若目标 project 缺失或 readiness 有 warning，仍返回结构化结果

### `octo project edit`

**Input**

- `--wizard`
- `--name`
- `--description`
- 最小 project metadata patch

**Behavior**

- 非 `--wizard` 路径仅更新轻量 metadata
- `--wizard` 路径进入统一 wizard session

### `octo project inspect`

**Output**

- active project
- primary workspace
- readiness / warnings
- binding summary
- secret/runtime sync summary

**Security**

- 所有 secret 相关字段只允许 redacted 输出

## 2. Active Project Selection

- canonical 状态由 `ProjectSelectorState` 持久化
- CLI 是当前唯一 writer，但 contract 语义必须兼容 026-A `ProjectSelectorDocument`
- 同一 surface 只能有一个 active selection

## 3. Failure Semantics

- project 不存在：exit 2 + 结构化错误
- readiness warning：exit 0，但 summary 带 warning
- secret/runtime 未同步：`inspect` 返回 `action_required`
