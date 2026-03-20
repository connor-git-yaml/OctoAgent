# Research Synthesis

## 本地代码审计结论

### 1. Profile 选择与执行路由混线

当前 `chat.py` 在发送消息时，一旦存在 `requested_agent_profile_id`，就会同时写：

- `agent_profile_id`
- `requested_worker_profile_id`

这使得“先和谁说话”直接升级成“本轮交给谁执行”。

### 2. DelegationPlane 继续放大混线

当前 `DelegationPlane.prepare_dispatch()` 会：

- 先读 `inherited_agent_profile_id`
- 再用它补 `agent_profile_id`
- 最后在 `requested_worker_profile_id` 为空时，直接把 `agent_profile_id` 补进去

因此只要某条 task/context 曾被写成某个 worker profile，后续就越来越像显式 worker route。

### 3. Butler Direct Execution 被错误门禁挡住

Feature 064 的 Butler direct execution 还在，但 `_is_butler_decision_eligible()` 当前只要看到：

- `requested_worker_profile_id`
- 或 `agent_profile_id`

非空且非 `singleton:*`，就直接判定 Butler 不可直解。  
这让 session owner / inherited profile 的残留也能持续打穿 Butler direct path。

### 4. direct non-main session 目前仍偏向“伪 direct”

Feature 070 修好了 direct session 的很多接缝，但更底层的语义还没完全拆开。  
现在 direct session 能工作，但：

- owner profile
- executor
- delegation target

仍然没有被一等建模，因此和 064 容易继续冲突。

## 参考框架启发

### Agent Zero

Agent Zero 的关键启发不是“路由技巧”，而是：

- 先有清晰的 agent identity / project / subordinate 边界
- subordinate handoff 是显式任务分派，而不是把会话 owner 偷偷当成执行 target

这意味着：
- 先和谁说话
- 之后由谁执行

必须是两层不同语义。

### OpenClaw / Codex / Claude Code

这些交互形态的一致经验是：

- 用户可以显式选择当前在跟哪个角色/会话对话
- 但“是否继续委派别人干活”是当前 Agent 的运行时决策
- UI 通常会把“会话对象”与“内部执行链”分开展示

## 设计推导

因此 OctoAgent 后续应采用：

1. **用户会话语义**
   - `session owner`
   - 由用户通过 `Profile + Project` 决定

2. **执行语义**
   - `turn executor`
   - 由当前 owner 在本轮运行时决定

3. **委派语义**
   - `delegation target`
   - 只在显式 delegation 时出现

4. **continuity 语义**
   - `inherited context owner`
   - 只负责 recall / context continuity

## 最重要的架构结论

当前坏味道不是“调度太复杂”，而是：

**`session owner / inherited profile / execution target / delegation target` 被压进了同一组 metadata 字段。**

只要不先把这层拆开，后面继续优化 direct session、worker chat、subagent orchestration，都会反复出现新的接缝。
