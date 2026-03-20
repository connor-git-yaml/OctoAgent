# Tasks

## Slice A - 语义建模

- [x] 为会话/任务/事件定义 `session_owner_profile_id`
- [x] 为运行时定义 `turn_executor_kind`
- [x] 为显式委派定义 `delegation_target_profile_id`
- [x] 为上下文 continuity 定义 `inherited_context_owner_profile_id`
- [x] 收口旧字段含义，明确 `agent_profile_id / requested_worker_profile_id` 的兼容角色

## Slice B - 发送链与继承链

- [x] 修改 `chat.py`，让 `Profile + Project` 只决定 session owner，不自动写 requested worker profile
- [x] 修改 `DelegationPlane.prepare_dispatch()`，移除 inherited profile -> requested worker profile 的自动提升
- [x] 为 direct non-main session 建立 owner-self execution 主链
- [x] 补齐直聊首条消息与续聊的 metadata 回归测试

## Slice C - Orchestrator 与动作图

- [x] 重构 Butler direct execution eligibility，只看显式 delegation target
- [x] 为 main agent 建立 `self / delegate_to_worker / spawn_subagent` 正式动作图
- [x] 为 worker 建立 `self / spawn_subagent` 正式动作图
- [x] 明确禁止 worker -> worker delegation
- [x] 检查 `single_loop_executor / spawned_by / worker_internal` 等 metadata 是否仍打穿新语义

## Slice D - Session / 投影 / UI

- [x] control plane session projection 输出 owner / executor / delegation target
- [x] Chat 页面区分“正在和谁对话”与“这一轮谁在执行”
- [x] direct session banner、会话列表、轨迹卡改用新语义
- [x] 清理旧的 `requested_worker_profile_id` UI 依赖

## Slice E - 兼容与迁移

- [x] 为历史 `BUTLER_MAIN + worker-profile-id` 会话定义兼容恢复策略
- [x] 为历史 context frame 中的 worker profile 继承污染定义最小迁移
- [x] 确保旧事件链仍可在 UI 中正确解释

## Slice F - 验证与事实源

- [x] 新增 main session / direct worker session / delegated worker / spawned subagent 的端到端回归
- [x] 对照 Feature 064 与 070 验证不回归
- [x] 回写 `docs/m4-feature-split.md`
- [x] 回写相关 blueprint 边界说明（若实现语义改变）
