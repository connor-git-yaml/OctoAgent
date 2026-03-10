# Product Research - Feature 037

## 背景

本次 review 聚焦“Agent 核心控制流程”和“Context 上下文管理”的功能性/稳定性优化空间，重点参考：

- Agent Zero 的 subordinate / memory / history hygiene 模式
- OpenClaw 的 gateway-owned session / transcript / compaction / routing 模式

## 参考结论

### OpenClaw

- Gateway 是 session state 的单一权威面，session key 在入口就冻结，后续 UI 和 runtime 都只消费这条权威边界。
- session store 记录的是当前 session 的 durable metadata，而不是让后续执行再回头重新猜 project/workspace/channel routing。
- compaction / token counters / session continuity 都围绕同一个 session authority 运作。

对 OctoAgent 的启发：

- `project/workspace/session` 必须在派发现场冻结成正式对象，再由后续 worker/runtime 继承。
- request snapshot 需要能直接说明“这次请求属于哪条 runtime lineage”，否则 operator 很难调试。

### Agent Zero

- subordinate agent 的 profile、context、memory 使用边界是显式对象，而不是散落在 prompt 拼接里。
- history hygiene 的关键不是“压缩一次”，而是让运行态明确知道自己继承了哪条上下文链。

对 OctoAgent 的启发：

- `agent_profile_id/context_frame_id/session_id` 不应只作为 metadata 旁路透传，而要进入正式 runtime contract。
- response writeback 也应回到同一条 runtime lineage，而不是在请求结束后重新推导 scope。

## 产品结论

Feature 037 不做“大重构”，只做两件事：

1. 新增 `RuntimeControlContext`，把控制流和 context lineage 收敛为正式对象。  
2. 让 `AgentContextService` 真正消费 typed resolver request，并优先使用冻结 snapshot。  
