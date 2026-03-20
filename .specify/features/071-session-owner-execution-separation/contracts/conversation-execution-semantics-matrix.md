# Conversation / Execution Semantics Matrix

## 1. 概念边界

| 概念 | 含义 | 是否由用户决定 | 是否进入运行时调度 |
|---|---|---|---|
| `session_owner_profile_id` | 当前这条会话默认在和谁对话 | 是 | 是，但仅作为 owner |
| `turn_executor_kind` | 这一轮当前是谁在执行（self / worker / subagent） | 否 | 是 |
| `delegation_target_profile_id` | 当前 Agent 显式委派给谁 | 否 | 是 |
| `inherited_context_owner_profile_id` | 上下文 continuity 的归属线索 | 否 | 否（不能直接变 delegation target） |

## 2. 动作图

| Owner Kind | self | delegate_to_worker | spawn_subagent |
|---|---:|---:|---:|
| Main Agent | ✅ | ✅ | ✅ |
| Worker Agent | ✅ | ❌ | ✅ |

## 3. 运行语义

### 默认主会话

- `session_owner_profile_id = default root agent`
- 默认优先尝试 `turn_executor_kind = self`
- 只有当前 Agent 显式决定委派时，才创建 worker work / A2AConversation

### direct non-main agent 会话

- `session_owner_profile_id = selected non-main agent`
- 默认优先尝试 `turn_executor_kind = self`
- 不应自动包装成 `requested_worker_profile_id`
- 若需要进一步拆分，仅允许 `spawn_subagent`

### delegated worker

- 只在主 Agent 或允许 delegation 的 owner 显式决策后出现
- 此时才写：
  - `turn_executor_kind = worker`
  - `delegation_target_profile_id = ...`

### spawned subagent

- 由当前 executor 显式 spawn
- `turn_executor_kind = subagent`

## 4. 明确禁止

- 禁止把 `session_owner_profile_id` 自动复制为 `delegation_target_profile_id`
- 禁止把 `inherited_context_owner_profile_id` 自动提升成 `requested_worker_profile_id`
- 禁止 worker 再 delegation 到另一个 worker
- 禁止 UI 把“会话 owner”和“当前执行者”混写成一个标签
