# Agent 端到端可用性改进计划

> 版本：v1.0 | 日期：2026-04-05 | 基于代码审计 + 竞品对比

## 1. 核心发现

### 1.1 当前消息处理链路过长

```
用户消息 → chat.py → TaskRunner.enqueue → OrchestratorService.dispatch
  → PolicyGate → SingleWorkerRouter → _resolve_owner_self_worker_execution
  → AgentContextService.build_task_context（构建 system prompt，7+ 来源）
  → TaskService.process_task_with_llm → LLMService.call
  → SkillRunner.run → LiteLLMSkillClient.generate
  → _build_initial_history → _merge_system_messages_to_front
  → _call_proxy / _call_proxy_responses → LLM API
  → 工具调用循环（最多 30 步）→ SSE 推送
```

**对比竞品：**

| 产品 | 层数 | 核心代码量 | 最简路径 |
|------|------|----------|---------|
| Pydantic AI | 3 层 | ~800 行 | `Agent().run()` → while loop → LLM call |
| Agent Zero | 2 层 | ~900 行 | `agent.monologue()` → process_tools() |
| OctoAgent | 8+ 层 | ~5000 行 | chat.py → 6 个中间服务 → LLM call |

### 1.2 已确认的 Bug（按严重性）

| # | 问题 | 严重性 | 根因 |
|---|------|--------|------|
| 1 | Responses API call_id 不匹配 | 🔴 | `output_item.done` 覆盖 `output_item.added` 的 call_id |
| 2 | System Message 过度注入（15K+ chars） | 🔴 | 7+ 来源无 token 预算，Qwen 对长 system message 不稳定 |
| 3 | MCP 工具不在 selected_tools | 🔴 | `manifest.tools_allowed` 是静态的，MCP 动态注册后不同步 |
| 4 | 工具结果回填降级为自然语言 | 🟠 | tool_call_id 不匹配时 fallback 到 user message 格式 |
| 5 | ToolBroker discover 沉默失败 | 🟠 | `except Exception: return []` 吞掉所有错误 |
| 6 | 对话历史内存无上限 | 🟡 | `_MAX_HISTORY_ENTRIES = 100` 但每条可达 1MB+ |

### 1.3 架构简化机会

**从竞品学到的经验：**

- **Pydantic AI**：Agent 内部就是一个简单 while loop，不需要 Orchestrator/DelegationPlane/TaskRunner 三层编排
- **Agent Zero**：Tool 执行用 `process_tools()` 单方法处理，不需要 SkillRunner + LiteLLMSkillClient 两层
- **Claude Code**：System Prompt 用一个函数构建（`buildSystemPrompt()`），不需要 7 个来源分别注入

## 2. 改进方案

### Phase 1：修复 Critical Bug（2-3 天）

#### P1.1 修复 Responses API call_id 不匹配
**文件**：`packages/skills/src/octoagent/skills/litellm_client.py`

**问题**：L437-468 中 `response.output_item.added` 和 `response.output_item.done` 都修改 `tool_calls_raw[item_id]`，后者覆盖前者。

**修复**：
- `output_item.added` 初始化 tool_call 记录
- `output_item.done` 只更新参数（arguments），不覆盖 call_id
- 添加 call_id 一致性校验日志

#### P1.2 System Message Token 预算
**文件**：`apps/gateway/src/octoagent/gateway/services/agent_context.py`

**问题**：7+ 来源的 system message 无限制注入，总量 15K+ chars。

**修复**：
- 建立 System Message 优先级：`Core（AGENTS.md）> Agent（IDENTITY/SOUL）> Tool Guide > Runtime Hints > Bootstrap`
- 添加全局 token 预算（如 4000 tokens），贪心装箱
- cheap 模型自动降低预算（如 2000 tokens）

#### P1.3 MCP 工具注入修复
**文件**：`packages/skills/src/octoagent/skills/litellm_client.py`

**问题**：`resolve_effective_tool_allowlist()` 基于静态 `manifest.tools_allowed`，不包含动态 MCP 工具。

**修复**：
- 在 `_get_tool_schemas()` 中，白名单之外额外包含 `tool_meta.source_kind == "mcp"` 的工具
- 或在 `manifest.tools_allowed` 构建时包含所有已注册的 MCP 工具名

### Phase 2：改善工具调用稳定性（2-3 天）

#### P2.1 工具结果回填统一
**文件**：`packages/skills/src/octoagent/skills/litellm_client.py`

**问题**：L697-727 的三条分支（Chat Completions / Responses API / 自然语言 fallback）导致非 OpenAI 模型行为不稳定。

**修复**：
- 统一为 Chat Completions 标准格式（`role: "tool", tool_call_id: "xxx"`）
- Responses API 发送前做格式转换
- 去掉自然语言 fallback（总是使用 tool role）

#### P2.2 添加诊断日志
**关键位置**：
- 工具被过滤时记录原因
- System message 逐条来源和 token 估算
- tool_call_id 匹配/不匹配时的详细日志
- MCP 工具发现/加载的完整流程

#### P2.3 沉默错误修复
**文件**：`litellm_client.py` L145
```python
# Before:
except Exception:
    return []
# After:
except Exception:
    log.warning("tool_discovery_failed", exc_info=True)
    return []
```

### Phase 3：架构简化（5-7 天，可选）

#### P3.1 简化 System Prompt 构建
**目标**：从 7 个来源 + 分散注入 → 1 个函数 + 统一构建

参考 Claude Code 的 `buildSystemPrompt()` 模式：
```python
def build_system_prompt(
    agent_profile: AgentProfile,
    project: Project | None,
    tools: list[ToolSpec],
    memory_context: str = "",
) -> str:
    """单一函数构建完整 system prompt。"""
    parts = []
    parts.append(load_behavior_content("AGENTS.md"))
    parts.append(load_behavior_content("IDENTITY.md", agent_profile))
    if tools:
        parts.append(build_tool_guide(tools))
    if memory_context:
        parts.append(memory_context)
    return "\n\n".join(parts)
```

#### P3.2 减少中间层
**目标**：消除不必要的编排层，简化调用链

当前 8+ 层中可以合并的：
- `TaskRunner` + `OrchestratorService` → 直接在 chat route 中调用 LLMService
- `AgentContextService.build_task_context` → 简化为 `build_system_prompt()` 函数
- `TaskService.process_task_with_llm` → 合并到 LLMService

**注意**：这是大改动，需要渐进式进行。先修 Bug（Phase 1-2），架构简化后续做。

## 3. 验证标准

### 端到端测试场景

| 场景 | 主 Agent（GPT 5.4） | 小 A（Qwen/cheap） |
|------|---------------------|-------------------|
| 简单问答 | "你好" → 正常回复 | "你好" → 正常回复 |
| 工具调用 | "查深圳天气" → 调用 web.search | "查深圳天气" → 调用 web.search |
| 多步工具 | "安装 MCP" → mcp.install | "记住我叫 Connor" → memory.store |
| 行为文件 | USER.md 中的信息在上下文中 | USER.md 在上下文中 |
| MCP 工具 | openrouter-perplexity 可调用 | openrouter-perplexity 可调用 |
| 错误恢复 | 工具失败后能继续对话 | 工具失败后能继续对话 |

### 性能指标

| 指标 | 当前 | 目标 |
|------|------|------|
| System Prompt tokens | ~15K chars | < 6K chars |
| 工具 Schema tokens | ~50K chars（60+ 工具） | < 20K chars（核心 + deferred） |
| 首次响应延迟 | 3-8 秒 | < 3 秒 |
| 工具调用成功率（Qwen） | ~60% | > 90% |

## 4. 优先级总结

| 优先级 | 任务 | 预估 | 影响 |
|--------|------|------|------|
| **P0** | 修复 Responses API call_id | 1 天 | 主 Agent 工具调用 |
| **P0** | System Message token 预算 | 1 天 | 所有模型推理质量 |
| **P0** | MCP 工具注入修复 | 0.5 天 | 动态工具能力 |
| **P1** | 工具结果回填统一 | 1 天 | Qwen 工具调用稳定性 |
| **P1** | 添加诊断日志 | 0.5 天 | 问题排查效率 |
| **P2** | System Prompt 构建简化 | 3 天 | 代码可维护性 |
| **P2** | 减少中间层 | 5 天 | 架构简洁度 |
