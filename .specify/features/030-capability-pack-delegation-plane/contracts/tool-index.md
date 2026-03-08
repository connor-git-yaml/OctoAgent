# Contract: ToolIndex + Dynamic Tool Selection

## 1. Query

输入：

- `query: str`
- `limit: int`
- `worker_type: str | null`
- `tool_groups: list[str]`
- `tool_profile: str | null`
- `tags: list[str]`
- `project_id: str | null`
- `workspace_id: str | null`

## 2. Output

返回：

- `selection_id`
- `backend`
- `is_fallback`
- `selected_tools[]`
- `hits[]`

其中 `hits[]` 每项至少包含：

- `tool_name`
- `score`
- `match_reason`
- `matched_filters`
- `tool_group`
- `tool_profile`
- `metadata`

## 3. Semantics

- ToolIndex 只做“选择候选工具”
- 工具真实执行仍必须通过 ToolBroker
- 动态工具注入只是收敛当前 toolset，不改变工具契约
- 命中为空或 backend 不可用时，必须返回 fallback selection

## 4. Audit

每次动态选择必须进入事件链，至少记录：

- query
- selected_tools
- top_hit_names
- backend
- is_fallback
