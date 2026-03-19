# 调度架构对比研究：Claude Code / OpenClaw / Agent Zero / OctoAgent

> Feature 064 调研产出。基于源码级分析。

## 1. 核心模式对比

| 维度 | Claude Code | OpenClaw | Agent Zero | OctoAgent (当前) |
|------|------------|----------|------------|----------------|
| **预路由 LLM 调用** | 无 | 无 | 无 | **有**（butler_decision） |
| **主 LLM 调用次数（简单问题）** | 1 | 1 | 1 | **2-3** |
| **委派决策者** | LLM via Agent tool | LLM via sessions_spawn tool | LLM via call_subordinate tool | 代码预判 + model decision |
| **工具注入** | 核心全量 + Tool Search 按需 | 全量注入 | 全量注入 system prompt | tool_profile 分级 + Deferred Tools |
| **子 agent 触发** | LLM 自主调 Agent tool | LLM 自主调 sessions_spawn | LLM 自主调 call_subordinate | orchestrator 代码路由 |
| **上下文管理** | auto-compaction ~75% | context-window-guard + compact | 历史压缩 + topic 密封 | Event Store + _fit_prompt_budget |
| **错误恢复** | 重试 + 降级 | failover 候选模型链 | 三层异常（干预/修复/致命） | 事件记录但无自动重试 |

## 2. Claude Code 架构

### 2.1 消息处理流程

```
用户消息 → [可选: Router 代理层, ~5-10ms] → Claude LLM (单次调用)
  ├─ System Prompt + CLAUDE.md + Skills
  ├─ Message History (auto-compacted if > ~75% tokens)
  ├─ Allowed Tools (whitelisted, partial schema)
  └─ Tool Search Tool (for large libraries)

  ↓ [Inference]

  Outputs: text + tool_use blocks (if needed)
  ├─ Tool execution (parallel for read-only, sequential for mutations)
  ├─ Observation feedback → next iteration
  └─ Repeat until stop condition
```

### 2.2 关键设计决策

1. **零预路由**：没有任何 classifier 或 router LLM 调用。主模型在推理过程中动态判断是否需要工具。
2. **Tool Search Tool**：当工具库 >30-50 个时，用 regex/BM25 索引按需发现，返回 3-5 个工具定义。节省 ~85% token。
3. **Agent tool 委派**：子 agent 定义在 YAML 文件中，主 LLM 通过 Agent tool 调用匹配任务。子 agent 运行在隔离上下文，不能再派发子 agent。
4. **权限分层**：allowedTools / disallowedTools 预过滤 → LLM 在可见范围内自主选择。

### 2.3 对 OctoAgent 的启示

- 取消预路由 LLM 调用，让主 LLM 自主决策
- Tool Search 机制与 OctoAgent 的 Deferred Tools 理念一致，可借鉴 BM25 索引
- Agent tool 的"隔离上下文 + 无递归"模式适合 OctoAgent 的 Subagent

## 3. OpenClaw 架构

### 3.1 消息处理流程

```
HTTP 请求 → Gateway (server.impl.ts)
  → ChatRunRegistry 队列 (server-chat.ts, 以 sessionId 分桶)
  → parseAgentSessionKey() 解析 agent 绑定
  → runEmbeddedPiAgent() (pi-embedded-runner/run.ts:266)
    → buildEmbeddedRunPayloads() 构建 prompt
    → enqueueSession/enqueueGlobal() 入队
    → 直接调 LLM（无预路由）
```

### 3.2 关键设计决策

1. **Session 键编码 Agent 绑定**：`agent:${agentId}:${channel}:${peerId}`，关系由键本身表达，无动态关系表。
2. **全量工具注入**：所有工具在 agent 启动时注入，LLM 自主选择。无动态发现。
3. **sessions_spawn 双模式**：
   - `mode="run"` — 一次性执行，完成后关闭（类似 OctoAgent Subagent）
   - `mode="session"` — 持久会话，绑定 thread（类似 OctoAgent Worker）
4. **Failover 候选链**：API 调用失败时，从 fallback 模型列表轮转。支持 timeout/overload/auth/billing/context-overflow 等原因。
5. **Cron 隔离会话**：cron 任务使用 `cron:${job.id}` 作为独立 session key，与普通对话隔离。

### 3.3 代码级关键路径

| 组件 | 文件 | 关键函数 |
|------|------|---------|
| Session 路由 | routing/session-key.ts:40-174 | parseAgentSessionKey() |
| LLM 执行 | pi-embedded-runner/run.ts:266+ | runEmbeddedPiAgent() |
| Spawn 主逻辑 | agents/acp-spawn.ts:408-777 | spawnAcpDirect() |
| 上下文管理 | context-window-guard.ts | evaluateContextWindowGuard() |
| Failover | pi-embedded-runner/run.ts:850-920 | failover 重试循环 |
| Cron 集成 | gateway/server-cron.ts:144-315 | buildGatewayCronService() |

### 3.4 对 OctoAgent 的启示

- Session Key 编码 Agent 绑定——简化路由，避免查表
- Spawn 的 run/session 双模式与 OctoAgent 的 Subagent/Worker 语义完全对齐
- Failover 候选链值得引入——当前 OctoAgent 无自动模型降级
- Cron 隔离会话与 OctoAgent 的 HEARTBEAT.md 机制可结合

## 4. Agent Zero 架构

### 4.1 消息处理流程

```
用户消息 → Agent.monologue() (agent.py:383)
  → 初始化 LoopData (迭代计数、提示、历史)
  → while True:  # monologue loop
      → prepare_prompt() → get_system_prompt() + history.output()
      → call_chat_model() → unified_call() (单次 LLM 调用)
      → process_tools() → extract_tools.json_parse_dirty()
        → 优先 MCP 工具，回退本地工具
        → tool.execute(**tool_args)
        → 若 break_loop=True: 返回结果
        → 否则: 继续循环
```

### 4.2 关键设计决策

1. **双层 while 循环**：外层处理致命异常恢复，内层处理消息-工具循环。
2. **全量工具注入**：所有工具说明在 system prompt 中全量呈现。无按需发现。
3. **call_subordinate 共享上下文**：子 agent 与父 agent 共享 AgentContext，独立 History。支持重用（不 reset 则保留子 agent 状态）。
4. **三层异常处理**：
   - InterventionException：用户干预→重新开始循环
   - RepairableException：转发给 LLM 尝试修复
   - 致命异常：3s 延迟后重试，最多 1 次
5. **Memory 三区域**：main（主记忆）/ fragments（片段）/ solutions（解决方案），FAISS 向量搜索，工具接口操作。
6. **Topic 密封**：子 agent 完成后 `history.new_topic()` 密封当前主题，便于后续上下文压缩。

### 4.3 代码级关键路径

| 组件 | 文件 | 关键行 |
|------|------|--------|
| Monologue 主循环 | agent.py:383-533 | monologue() |
| System Prompt 构建 | agent.py:639-644 | get_system_prompt() |
| 工具解析 | agent.py:855-948 | process_tools() |
| 子 agent 委派 | tools/call_subordinate.py | Delegation.execute() |
| Memory | helpers/memory.py:56-174 | Memory.get(), search(), save() |
| 错误恢复 | agent.py:586-637 | retry_critical_exception() |

### 4.4 对 OctoAgent 的启示

- Monologue Loop + break_loop 信号是最简洁的 agent 循环模式
- RepairableException 转发给 LLM 修复——比简单重试更智能
- Topic 密封对 OctoAgent 的 Memory 整理有参考价值
- 子 agent 共享上下文但独立历史——平衡了效率和隔离

## 5. OctoAgent 当前架构问题

### 5.1 调度链路（当前）

```
消息 → dispatch() [orchestrator.py:514]
  ├─ Policy Gate [行 550]                    # 无 LLM，纯规则
  ├─ Butler Decision [行 571-607]            # ⭐ LLM Call #1（model_alias="main"）
  │  ├─ 规则决策 decide_butler_decision()    # 仅天气/位置检测
  │  └─ 模型决策 _resolve_model_butler_decision()  # 用 main 模型判断委派
  │     └─ record_auxiliary_model_call()     # tool_profile="minimal"
  │        → 10-30s（gpt-5.4 xhigh）
  │
  ├─ Inline Butler Decision [行 618]         # ⭐ LLM Call #2（ASK_ONCE/BEST_EFFORT）
  │  └─ process_task_with_llm() + _InlineReplyLLMService
  │
  ├─ Delegation Plane [行 633]               # 无 LLM，路由规则
  │  └─ prepare_dispatch() → DelegationPlan
  │
  └─ Worker Dispatch [行 676+]               # ⭐ LLM Call #3
     └─ WorkerRuntime.execute()
        └─ process_task_with_llm()
           → 10-30s（main 模型生成回复）
```

**简单问题总耗时：20-60s，2-3 次 LLM 调用。**

### 5.2 核心问题

| 问题 | 描述 | 行业对比 |
|------|------|---------|
| **预路由 LLM 调用** | _resolve_model_butler_decision() 用 main 模型判断"委派 vs 直答"，仅此一步就 10-30s | 三家均无此步骤 |
| **DIRECT_ANSWER fallthrough** | 决策返回 DIRECT_ANSWER 时 None → fallthrough 到 delegation_plane → Worker，触发第二次完整 LLM 调用 | 三家直答就是直答 |
| **Worker 必经路径** | 所有回复都经过 Worker（即使是"你好"），增加 A2A 开销 | 三家简单问题不经子 agent |
| **规则决策过窄** | decide_butler_decision() 仅检测天气/位置，其他全走模型决策 | 三家不需要规则预判 |
| **无模型降级** | LLM 调用失败无自动 fallback | OpenClaw 有完整 failover 链 |

## 6. 结论与设计方向

### 6.1 行业共识

**所有主流框架的核心模式一致：主 LLM 单次推理，自主决定回答/用工具/委派。不做独立预路由。**

### 6.2 OctoAgent 应对齐的方向

1. **消除 _resolve_model_butler_decision()**：Butler 直接用主 LLM 回答，工具和委派通过 tool calling 表达
2. **Butler 作为 Free Loop 主执行者**：对齐蓝图"Butler 是主执行者 + 监督者"的设计意图
3. **委派通过工具触发**：新增 `delegate_to_worker` tool，LLM 自主判断是否需要委派
4. **保留 A2A 协议**：Worker 委派后的 A2A 交互不变，但触发方式从代码预判改为 LLM 工具调用
5. **引入 Failover**：借鉴 OpenClaw 的 fallback 候选链

### 6.3 保留 OctoAgent 的差异化优势

| 优势 | 保留方式 |
|------|---------|
| A2A 协议 | 委派后 Butler↔Worker 仍通过 A2A 通信 |
| Event Sourcing | 每个 LLM 调用仍生成完整事件记录 |
| Policy Gate | 保持不变，策略拒绝在 LLM 调用前 |
| Deferred Tools | 保持 Feature 061 的按需工具发现 |
| Behavior Workspace | IDENTITY/SOUL/HEARTBEAT 继续注入 system prompt |
| 持久化 Worker | Worker 的持久化和 Project 绑定不变 |
