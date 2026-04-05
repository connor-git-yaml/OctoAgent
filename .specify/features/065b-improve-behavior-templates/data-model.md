# Data Model: Feature 065 - 全面改进 Behavior 默认模板内容

**Branch**: `claude/bold-aryabhata` | **Date**: 2026-03-19

## 概述

Feature 065 **无数据模型变更**。本文档记录相关实体的现状供实现参考。

## 涉及实体（只读引用，不修改）

### BEHAVIOR_FILE_BUDGETS

定义于 `behavior_workspace.py` 模块级常量，是每个行为文件的字符预算上限映射表。

```python
BEHAVIOR_FILE_BUDGETS = {
    "AGENTS.md": 3200,
    "USER.md": 1800,
    "PROJECT.md": 2400,
    "KNOWLEDGE.md": 2200,
    "TOOLS.md": 3200,
    "BOOTSTRAP.md": 2200,
    "SOUL.md": 1600,
    "IDENTITY.md": 1600,
    "HEARTBEAT.md": 1600,
}
```

**Feature 065 约束**: 所有模板内容 `len()` MUST <= 对应预算值。不修改预算值本身。

### BehaviorWorkspaceFile

行为文件的运行时表示（Pydantic model），定义于 `models/behavior.py`。

关键字段：
- `file_id: str` -- 文件标识（如 `"AGENTS.md"`）
- `content: str` -- 文件内容（Feature 065 影响此字段的默认值）
- `budget_chars: int` -- 字符预算上限
- `source_kind: str` -- 来源类型（`"default_template"` / `"system_file"` / `"agent_file"` 等）

**Feature 065 影响**: 当 `source_kind="default_template"` 时，`content` 的值由 `_default_content_for_file` 生成。本 Feature 扩展该生成逻辑。

### BehaviorPackFile

行为文件打包后的传输表示，用于 system prompt 注入。

**Feature 065 影响**: 无。PackFile 从 WorkspaceFile 打包生成，透传 content。

### _default_content_for_file 函数签名

```python
def _default_content_for_file(
    *,
    file_id: str,           # 行为文件标识
    is_worker_profile: bool, # Butler(False) vs Worker(True)
    agent_name: str,         # Agent 名称（用于模板插值）
    project_label: str,      # Project 标签（用于模板插值）
) -> str:
```

**Feature 065 约束**: 函数签名不变（FR-036）。仅修改函数体中的返回字符串。

## Schema 变更

无。

## 数据迁移

无。已有用户自定义过的行为文件不受影响（自定义内容优先于默认模板）。新模板仅在首次创建行为文件时生效。
