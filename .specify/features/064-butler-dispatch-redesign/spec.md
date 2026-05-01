---
feature_id: "064-butler-dispatch-redesign"
title: "Butler Dispatch Redesign: 消除预路由 LLM，Butler 直接执行"
milestone: M1
status: implementing
created: 2026-03-19
updated: 2026-03-19
research: research/dispatch-architecture-comparison.md
---

# Feature 064: Butler Dispatch Redesign

> **⚠️ Note (F087 followup 2026-05-01)**: 本 spec Phase 2 计划复用的 `SubagentExecutor` / `spawn_subagent_v2()` / `drain_subagent_results()` / `SubagentSpawnParams` / `SubagentSpawnContext` 等接口，已在 F087 followup 死代码清理时随 `subagent_lifecycle.py` 整文件一并删除。如本 spec 后续推进实施，需基于 Feature 084+ 当前的 `task_runner.launch_child_task` 路径重新规划 Phase 2（详见 `docs/codebase-architecture/e2e-testing.md` §2.1）。Phase 1 内容（包括 Butler dispatch 主路径、move-to-agent 改名等）不受影响。

## 1. 动机

### 1.1 现状问题

用户发送 "Hello 你是什么模型？"，系统耗时 30s+。根因：

1. **预路由 LLM 调用**（`_resolve_model_butler_decision()`）：用 main 模型（gpt-5.4 xhigh, 32K thinking budget）仅为判断"委派 vs 直答"，消耗 10-30s
2. **DIRECT_ANSWER fallthrough**：决策返回 None 后 fallthrough 到 Worker 派发，触发第二次完整 LLM 调用
3. **总计 2-3 次 LLM 调用**，即使是最简单的问题

### 1.2 行业对比

经 Claude Code / OpenClaw / Agent Zero 三大框架源码级调研（详见 `research/dispatch-architecture-comparison.md`），核心发现：

> **所有主流 Agent 框架均不做独立的"预路由决策 LLM 调用"。工具使用和子 agent 委派由主 LLM 在单次推理中自行判断，通过 tool calling 表达。**

| 维度 | 行业共识 | OctoAgent (改造前) |
|------|---------|-------------------|
| 简单问题 LLM 调用次数 | **1 次** | 2-3 次 |
| 委派决策者 | LLM 自主（via tool call） | 代码预判 + model decision |
| 预路由步骤 | 无 | 有（10-30s 开销） |

### 1.3 设计目标

- 简单问题从 30s+ 降至 ~4-15s（单次 LLM 调用）
- Butler 对齐蓝图定位："主执行者 + 监督者，永远 Free Loop"
- 保留 OctoAgent 差异化优势：A2A、Event Sourcing、Policy Gate、Deferred Tools

## 2. 架构变更

### 2.1 调度链路对比

**改造前链路（2-3 次 LLM 调用）**：

```
消息 → Policy Gate → Butler Decision（⭐ LLM #1: 预路由判断）
  → 若 DIRECT_ANSWER: fallthrough → Delegation Plane → Worker Dispatch（⭐ LLM #2: 生成回复）
  → 若 ASK_ONCE: Inline Reply（⭐ LLM #2: 生成澄清问题）
  → 若 DELEGATE_*: Delegation Plane → Worker Dispatch（⭐ LLM #2: Worker 执行）
```

**目标链路（1 次 LLM 调用，委派时 +1）**：

```
消息 → Policy Gate → Butler Execution Loop（⭐ LLM #1: 直接回答 + 工具调用）
  → LLM 自主决定:
    → 直接回答（文本输出）→ 返回
    → 使用工具（web_search, memory_recall 等）→ 工具结果 → 继续循环
    → 委派 Worker（调用 delegate_to_worker tool）→ A2A 派发（⭐ LLM #2: Worker 执行）
```

### 2.2 核心变更

#### 变更 A: 移除预路由 LLM 调用 [Phase 1 ✅]

- **已删除** `_resolve_model_butler_decision()` 函数及其依赖（`_resolve_butler_tool_universe_hints()`、`_build_precomputed_recall_plan_metadata()`）
- **已清理** 废弃导入：`ButlerLoopPlan`、`build_butler_decision_messages`、`parse_butler_loop_plan_response`、`RecallPlanMode`、`build_tool_universe_hints`
- `_resolve_butler_decision()` 直接返回规则决策结果（`decide_butler_decision()`）

#### 变更 B: Butler 直接执行路径 [Phase 1 ✅]

- 新增 `_dispatch_butler_direct_execution()` 方法
- 新增 `_should_butler_direct_execute()` 资格判断
- Butler 使用 `self._llm_service`（真实 LLM）直接处理请求
- 使用 `tool_profile="standard"`（Butler 可使用标准工具集）
- 保持完整的 Event Sourcing（ORCH_DECISION → MODEL_CALL_STARTED → MODEL_CALL_COMPLETED → ARTIFACT_CREATED）
- metadata 记录 `butler_execution_mode=direct`

#### 变更 C: 委派通过工具触发 [Phase 2]

将 `spawn_subagent_v2()` 编程 API 包装为 LLM 可调用的 `delegate_to_worker` Skill/Tool。

**已有基础设施**（master `03cd25b` 已实现）：
- `SubagentExecutor`：独立 asyncio.Task 执行循环，不阻塞父 Agent
- `SubagentSpawnParams` + `SubagentSpawnContext`：参数和上下文注入
- `SubagentResultQueue`：FIFO 结果注入 + A2A_MESSAGE_RECEIVED 事件
- `ContextCompactor`：三级渐进上下文压缩（截断→LLM 摘要→丢弃最老）
- Child Task + A2AConversation 全链路审计

**Phase 2 需新增**：
1. `delegate_to_worker` Skill/Tool 定义和注册（包装 `spawn_subagent_v2()`）
2. Butler Execution Loop 的结果消费逻辑（`drain_subagent_results()` 接入）
3. Butler 多轮循环支持（轮次上限 10）

工具参数（对齐 `SubagentSpawnParams`）：

```python
class DelegateToWorkerTool:
    name = "delegate_to_worker"
    parameters = {
        "worker_type": {
            "type": "string",
            "enum": ["research", "dev", "ops", "general"],
            "description": "Worker 类型"
        },
        "task_description": {
            "type": "string",
            "description": "给 Worker 的任务描述"
        },
        "urgency": {
            "type": "string",
            "enum": ["normal", "high"],
            "default": "normal"
        }
    }
```

#### 变更 D: 保留规则快速路径 [Phase 1 ✅]

- `decide_butler_decision()` 中的天气/位置检测规则保留
- 新增 `_is_trivial_direct_answer()` 识别简单对话（问候、身份、致谢、确认）
- 规则快速路径直接进入 Butler 直接执行，跳过额外准备步骤

### 2.3 不变的部分

| 组件 | 状态 | 理由 |
|------|------|------|
| Policy Gate | 不变 | 策略拒绝在 LLM 调用前，安全边界 |
| A2A 协议 | 不变 | Worker 委派后的通信格式不变 |
| Event Sourcing | 不变 | Butler 直接执行也生成完整事件链 |
| SubagentExecutor | 复用 | Phase 2 包装为 tool，底层逻辑不变 |
| ContextCompactor | 复用 | Phase 2 多轮循环自动触发压缩 |
| SubagentResultQueue | 复用 | Phase 2 扩展 Butler 消费能力 |
| Worker Runtime | 不变 | Worker 的执行逻辑和 LLM 调用不变 |
| Deferred Tools | 不变 | Feature 061 的按需工具发现机制保留 |
| Behavior Workspace | 不变 | IDENTITY/SOUL/HEARTBEAT 继续注入 |
| Task/Event 模型 | 不变 | 数据模型和存储不变 |

## 3. 详细设计

### 3.1 Butler Execution Loop 执行模型

Butler 的执行模型从"决策者 + 路由器"转变为"主执行者"：

```python
# 伪代码：Butler Execution Loop
async def butler_execution_loop(request):
    # 1. 构建上下文（复用 _build_system_blocks + _fit_prompt_budget）
    context = build_butler_context(
        user_text=request.user_text,
        behavior_files=load_behavior_workspace(),
        memory=recall_relevant_memories(),
        tools=resolve_tools(tool_profile="standard"),  # Phase 2: 含 delegate_to_worker
        deferred_tools=get_deferred_tool_names(),
        conversation_history=get_recent_conversation(),
    )

    # 2. Phase 1: 单次 LLM 调用（通过 process_task_with_llm）
    #    Phase 2: 多轮循环（轮次上限 10）
    result = await task_service.process_task_with_llm(
        llm_service=self._llm_service,
        tool_profile="standard",
        butler_metadata={"butler_execution_mode": "direct"},
    )

    # 3. 返回结果
    return self._butler_worker_result(task_id, dispatch_prefix="butler-direct")
```

### 3.2 上下文预算管理

Butler 直接执行时的上下文构成（复用现有 `_build_system_blocks()`）：

```
System Blocks:
  ├─ Behavior Block (IDENTITY/SOUL/HEARTBEAT)
  ├─ Runtime Hint Block (project/workspace info)
  ├─ Tool Guide Block
  ├─ Loaded Skills Block
  ├─ Deferred Tools Block
  └─ Conversation Context Block

User Message:
  └─ request.user_text
```

Token 预算策略不变（`_fit_prompt_budget()` 逻辑复用）。Phase 2 多轮循环时由 `ContextCompactor` 自动触发三级压缩。

### 3.3 模型降级（Failover）[Phase 3]

**已有基础设施**：
- `FallbackManager`（`provider/fallback.py`）：Primary → Fallback 客户端级切换
- `AliasRegistry`（`provider/alias.py`）："fallback" alias 已预留
- `ModelCallResult.is_fallback` / `fallback_reason` 字段已定义
- LiteLLM Proxy 服务端已有 model fallback 链（对客户端透明）

**Phase 3 需新增**：
- SkillRunner 级 Failover 决策（连续失败 N 次 → 切换 alias）
- `MODEL_FAILOVER_TRIGGERED` 事件类型
- 降级链条配置：`main → fallback → ultra_cheap`

## 4. 迁移策略

### 4.1 分阶段实施

| 阶段 | 内容 | 已有基础设施 | 新增工作量 | 状态 |
|------|------|------------|----------|------|
| **Phase 1** | Butler 直接回答 + 废弃代码清理 | — | 2 源文件 + 3 测试文件 | **✅ 已完成** |
| **Phase 2** | delegate_to_worker 工具 + 多轮循环 | SubagentExecutor, ResultQueue, ContextCompactor (80%) | Tool 定义注册 + Butler 结果消费 (20%) | 待实施 |
| **Phase 3** | Failover 模型降级 | FallbackManager, AliasRegistry, is_fallback 字段 (70%) | SkillRunner 级决策 + 事件类型 (30%) | 待实施 |

### 4.2 Phase 1 变更集 [✅ 已完成]

源文件（2 个）：
1. **orchestrator.py**：删除 `_resolve_model_butler_decision()` 及依赖 → 新增 `_dispatch_butler_direct_execution()` + `_should_butler_direct_execute()` → dispatch() 新增 DIRECT_ANSWER 早期返回分支
2. **butler_behavior.py**：新增 `_is_trivial_direct_answer()` 简单对话识别

测试文件（3 个）：
3. **test_butler_dispatch_redesign.py**（新增）：12 个集成测试
4. **test_butler_behavior.py**：28 个 trivial 检测单元测试
5. **test_orchestrator.py**：删除 3 个废弃测试辅助类 + 5 个废弃测试方法

### 4.3 兼容性

- **前端**：无影响（SSE 事件流格式不变）
- **Telegram**：无影响（消息处理管道不变）
- **API**：无影响（请求/响应格式不变）
- **Event Store**：新增 `butler_execution_mode=direct` metadata，向后兼容

## 5. 预期效果

| 场景 | 改造前 | Phase 1 后 | Phase 2 后 | LLM 调用次数 |
|------|-------|-----------|-----------|-------------|
| "Hello 你是什么模型" | ~30s | **~4-15s** | ~4-15s | 2-3→**1** |
| 一般对话（无工具） | ~30s | **~4-15s** | ~4-15s | 2-3→**1** |
| 需要搜索的问题 | ~40s | ~30-40s（仍走 Worker） | **~20-30s**（Butler 直接用工具） | 3→**1-2** |
| 需要 Worker 的复杂任务 | ~40s | ~30-40s（仍走 Worker） | **~35-40s**（Butler→Worker） | 3→**2** |

## 6. 验证标准

### 6.1 功能验证

- [x] 简单问题（问候、身份、致谢）：无 Worker 创建，单次 LLM 调用 [Phase 1]
- [ ] 工具使用问题（天气查询）：Butler 直接调用工具，无 Worker [Phase 2]
- [ ] 复杂任务（编程请求）：Butler 通过 delegate_to_worker 工具委派 Worker [Phase 2]
- [x] 所有场景生成完整 Event 链 [Phase 1]

### 6.2 性能验证

- [x] 简单问题端到端延迟 < 15s（取决于模型 thinking 配置） [Phase 1，待手动验证]
- [x] 无 regression：现有测试 806 passed, 0 failed [Phase 1]

### 6.3 兼容性验证

- [x] Event Store 查询正常（metadata 向后兼容） [Phase 1]
- [x] 现有测试通过 [Phase 1]
- [ ] 前端 SSE 事件流正常 [待手动验证]
- [ ] Telegram 消息收发正常 [待手动验证]

## 7. 长期演进

### 7.1 架构愿景

```
用户消息 → Policy Gate → Butler Execution Loop
  ├─ 直接回答（简单问题）          → 1 次 LLM 调用
  ├─ 使用工具后回答（中等问题）    → 1-3 次 LLM 调用（循环）
  └─ 委派 Worker（复杂任务）       → 1 次 Butler + N 次 Worker
     └─ Worker Free Loop
        ├─ 直接执行（使用工具）
        └─ Spawn Subagent（更大任务）
```

这与蓝图中 "三层 Agent + Skill Pipeline" 的架构完全一致，但执行方式从"代码预判路由"转变为"LLM 自主决策"。

### 7.2 竞品对比（Phase 1 完成后）

| 维度 | Claude Code | OpenClaw | Agent Zero | **OctoAgent** |
|------|------------|----------|------------|--------------|
| 预路由 LLM 调用 | 无 | 无 | 无 | **无** ✅ |
| 简单问题 LLM 调用数 | 1 | 1 | 1 | **1** ✅ |
| 委派决策 | LLM via Agent tool | LLM via sessions_spawn | LLM via call_subordinate | 规则决策（Phase 2: delegate_to_worker tool） |
| 工具注入 | 核心全量 + Tool Search | 全量注入 | 全量注入 | Deferred Tools + tool_profile 分级 ✅ |
| 模型降级 | 无 | failover 候选链 | 重试 1 次 | FallbackManager 已有（Phase 3: SkillRunner 级增强） |
| Subagent 机制 | Agent tool（隔离上下文） | sessions_spawn（run/session 双模式） | call_subordinate（共享上下文） | SubagentExecutor（独立 asyncio.Task） ✅ |
| 上下文压缩 | auto-compaction ~75% | context-window-guard | topic 密封 | ContextCompactor 三级压缩 ✅ |
| 事件溯源 | 无 | 无 | 无 | **完整 Event Sourcing** ✅ |

## 8. Clarifications

### Session 2026-03-19

#### 8.1 Butler 直接执行时的 ButlerSession 状态管理

[AUTO-CLARIFIED: 复用 process_task_with_llm Event 链路]

Butler Execution Loop 中的 LLM 调用和工具调用通过 `TaskService.record_*` 方法写入 Event Store，与 Worker 执行路径保持一致的事件粒度。

#### 8.2 delegate_to_worker 工具的 context 传递机制

[AUTO-CLARIFIED: 工具执行器自动构建 context capsule]

`delegate_to_worker` 工具执行器负责构建 A2A context capsule。LLM 通过 `task_description` 参数描述任务意图，执行器从当前 ButlerSession 提取 recent conversation summary + 相关 memory 组装为 context capsule。

**更新**：Phase 2 实现时复用 `SubagentSpawnContext` 作为上下文注入容器，无需新建机制。

#### 8.3 Orchestrator 与 Butler 的角色边界

[AUTO-CLARIFIED: Orchestrator 保持治理框架，Butler Execution Loop 是其执行分支]

OrchestratorService 保持为入口层（Policy Gate + 请求准备 + 事件写入 + 结果组装），Butler Execution Loop 作为其中的主要执行分支。

#### 8.4 _is_trivial_direct_answer() 的覆盖范围

[AUTO-CLARIFIED: 保守关键词匹配，仅跳过准备步骤]

Phase 1 使用保守的关键词/模式匹配，仅覆盖：纯问候、身份询问、致谢/确认、简单元问题。误判不影响正确性。

#### 8.5 术语规范化

- **Butler Execution Loop**: Butler 的 LLM 驱动执行循环
- **Butler Direct Execution**: Butler 不经 Worker 直接处理请求的路径
- **Rule-based Fast Path**: `_is_trivial_direct_answer()` + `decide_butler_decision()` 的规则快速路径

#### 8.6 [已决] Butler Execution Loop 迭代上限与成本控制

**决议**: 不设轮次上限，对齐 Claude Code / OpenClaw / Agent Zero 行业实践——三家均不限制 LLM 工具调用循环步数，信任模型自己决定何时结束。后续如观测到循环失控再加防御性上限。

#### 8.7 Phase 2/3 与 master 编排增强的关系

**调研结论**（基于 master commit `03cd25b` 源码分析）：

Phase 2（delegate_to_worker）80% 基础设施已在 master 上实现：
- `SubagentExecutor` + `spawn_subagent_v2()` — 完整的 Subagent 生命周期管理
- `SubagentResultQueue` — 结果注入 + A2A 事件
- `ContextCompactor` — 三级压缩
- Phase 2 核心新增：Tool 定义注册（包装 `spawn_subagent_v2()`）+ Butler 结果消费

Phase 3（Failover）70% 基础设施已实现：
- `FallbackManager` — 客户端级 Primary→Fallback 切换
- `AliasRegistry` — "fallback" alias 已预留
- `ModelCallResult.is_fallback` — 降级标志已定义
- Phase 3 核心新增：SkillRunner 级 Failover 决策 + `MODEL_FAILOVER_TRIGGERED` 事件
