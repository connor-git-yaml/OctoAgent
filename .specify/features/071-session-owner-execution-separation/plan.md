# Implementation Plan

## Goal

把“先和谁说话”和“本轮给谁执行”拆成清晰的运行时语义，并让：

- 默认主会话继续保留 Butler direct execution
- direct non-main agent session 成为一等对象
- main agent 可 `self / delegate_to_worker / spawn_subagent`
- worker 可 `self / spawn_subagent`

## Design Summary

### 1. 统一语义模型

引入并贯穿 4 个核心字段：

- `session_owner_profile_id`
- `turn_executor_kind`
- `delegation_target_profile_id`
- `inherited_context_owner_profile_id`

并停止把：
- `agent_profile_id`
- `requested_worker_profile_id`

继续同时承担会话归属、上下文继承和 delegation target 三种语义。

### 2. Dispatch 决策链重排

dispatch 入口改成：

1. 读取 `session owner`
2. 判定当前 owner 的 runtime kind（main / worker）
3. 决定本轮是 `self` 还是 delegation
4. 只有发生 delegation 时才写 `delegation_target_profile_id`

### 3. 动作图硬约束

| Owner Kind | 允许动作 |
|---|---|
| Main Agent | `self`, `delegate_to_worker`, `spawn_subagent` |
| Worker Agent | `self`, `spawn_subagent` |

### 4. UI 投影更新

Web / control plane 统一展示：
- 会话 owner
- 本轮执行者
- delegation 链路

## Scope

### In

- `chat.py` 首条消息与续聊时的 metadata 语义修正
- `DelegationPlane` 的 inherited profile 提升逻辑收口
- `orchestrator` 的 Butler direct execution eligibility 重算
- direct session / default session / worker internal session 的语义清理
- Web 端聊天页与会话投影的 owner / executor 展示
- 历史兼容与最小迁移

### Out

- tool / approval / memory runtime 的大规模重构
- worker/subagent 具体技能策略重写
- 069 graph pipeline 能力本身的行为调整

## Workstreams

### Slice A - 语义对象与元数据

- 定义/引入 `session_owner_profile_id`
- 定义/引入 `turn_executor_kind`
- 定义/引入 `delegation_target_profile_id`
- 明确 `inherited_context_owner_profile_id`
- 更新 `RuntimeControlContext` / dispatch metadata / event payload 结构

### Slice B - 调度链路重构

- `chat.py` 不再把 `requested_agent_profile_id` 自动复制到 `requested_worker_profile_id`
- `DelegationPlane` 不再把 inherited profile 自动提升成 requested worker profile
- `orchestrator` Butler eligibility 只看显式 delegation target
- direct non-main agent session 走 owner-self execution，而不是 worker wrapper

### Slice C - 动作图与 guardrails

- main agent 可 `self / delegate_to_worker / spawn_subagent`
- worker 可 `self / spawn_subagent`
- 显式禁止 worker -> worker delegation
- 子任务/graph/subagent 的 metadata 不再污染 owner fields

### Slice D - UI / 投影

- `/api/control/resources/sessions` 和 task/runtime summary 输出 owner / executor / delegation target
- Chat 页面区分“正在和谁对话”与“这一轮谁在执行”
- direct session banner、会话列表、轨迹卡改用新语义

### Slice E - 兼容迁移与验证

- 历史 `BUTLER_MAIN + worker-profile-id` 会话的兼容恢复策略
- 历史 context frame 中 worker profile 继承污染的最小迁移
- direct main / direct worker / delegated worker / spawned subagent 的回归矩阵

## Risks

- 历史字段兼容不当会导致旧会话消失或恢复错误
- Butler direct execution 与 direct non-main self execution 容易再次在 eligibility 判定里互相覆盖
- UI 若仍沿用旧字段名，会继续把 owner/executor 混着展示

## Exit Criteria

- 默认主会话与 direct non-main 会话可同时稳定工作
- `requested_worker_profile_id` 只在真实 delegation 时出现
- control plane / Chat 页面可区分 owner 与 executor
- worker 不再能隐式 route 到另一个 worker
