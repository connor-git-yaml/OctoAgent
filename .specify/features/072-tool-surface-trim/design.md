# Feature 072: 工具集按场景裁剪 — 让 Core/Deferred 分层真正生效

## Context

58 个 builtin 工具 + N 个 MCP 工具**全部以完整 schema 注入 LLM**。
小模型 function calling 不稳定。

Feature 061 已实现了完整的 Core/Deferred 基础设施（CoreToolSet、tool_search、
ToolPromotionState、format_deferred_tools_list），但实际运行时这些机制
全部断开——`mounted_tools` 构建时把所有工具都放进去了。

## Claude Code 做法（深度源码分析）

Claude Code ~47 内置工具 + N 个 MCP 工具，LLM 首轮只看到 ~20 个完整 schema。

```
1. 始终加载（~20 个）：Bash, FileRead, FileEdit, FileWrite, Glob, Grep,
   Agent, Skill, ToolSearch, TodoWrite ...
   → 完整 JSON schema 注入 API tools 参数

2. 延迟加载（~26 内置 + 全部 MCP）：WebSearch, Cron*, Task*, Plan*, Config ...
   → 仅名称列表注入为 <available-deferred-tools> 文本消息
   → LLM 需要时调用 ToolSearch
   → ToolSearch 返回 tool_reference content block → API 自动展开 schema

3. MCP 工具：默认全部 deferred，除非标记 alwaysLoad=true

4. Subagent 裁剪：Coordinator 模式只给 4 个工具，异步 agent 给 ~20 个白名单
```

关键差异：Claude Code 用了 Anthropic API 的 `tool_reference` 和 `defer_loading` 特性。
OctoAgent 走 LiteLLM，不能用这些特性。但核心思路一样：**默认少给，按需加载**。

## 三个断点修复

当前 Feature 061 的 Core/Deferred 基础设施全部已实现但未接通。需要修复三个断点：

### 断点 1：`mounted_tools` 包含全部工具（应只含 Core + promoted）

**位置**: `capability_pack.py` → `resolve_profile_first_tools()`

```
当前：遍历所有注册工具 → 全部放入 mounted_tools → 56 个完整 schema 注入 LLM
修复：只放 Core + promoted 工具 → 9 个完整 schema 注入 LLM
```

### 断点 2：Deferred 工具列表未注入 system prompt

**位置**: `agent_decision.py` → `render_behavior_system_block()`

`format_deferred_tools_list()` 已实现（`models.py:543`）但没有被调用。
需要在 system prompt 构建时追加 deferred 工具列表段落。

LLM 需要知道这些工具存在才会去搜索。对标 Claude Code 的
`<available-deferred-tools>` 列表。

### 断点 3：tool_search 返回后未调用 `process_tool_search_results()`

**位置**: `llm_service.py:598` 定义了 `process_tool_search_results()`，
但在 SkillRunner 的 tool call 执行流程中，tool_search 返回后没有触发提升。

Claude Code 用 `tool_reference` API 特性自动完成提升。我们需要在
SkillRunner 的工具执行回调中识别 tool_search 的返回并调用提升。

## Core 工具清单

对标 Claude Code 的 ~20 个 always-loaded（Bash, FileRead/Edit/Write, Glob, Grep,
Agent, Skill, ToolSearch 等），根据 OctoAgent 的场景调整：

```python
CoreToolSet.default() = [
    # 入口（必须始终可用）
    "tool_search",       # 搜索和激活 deferred 工具

    # 文件操作（日常高频）
    "filesystem.list_dir",
    "filesystem.read_text",
    "filesystem.write_text",

    # 执行
    "terminal.exec",

    # 记忆
    "memory.recall",

    # 网络（OctoAgent 作为 AI 助手，联网是高频操作）
    "web.search",
    "web.fetch",

    # 技能
    "skills",
]
```

9 个 Core（对比 Claude Code 的 ~20 个，OctoAgent 工具总量更少，Core 比例接近）。

**Deferred（49 个 builtin + 全部 MCP）**：
- project.inspect, task.inspect, artifact.list
- memory.search, memory.browse, memory.citations, memory.read, memory.write
- subagents.spawn/list/kill/steer, workers.review, work.split/merge/delete/inspect
- browser.open/status/navigate/snapshot/act/close
- mcp.servers.list/tools.list/tools.refresh/install/install_status/uninstall
- gateway.inspect, runtime.inspect, runtime.now, nodes.list, cron.list
- behavior.write_file, config.inspect/add_provider/set_model_alias/sync
- setup.review, setup.quick_connect
- pdf.inspect, image.inspect, tts.speak, canvas.write
- sessions.list, session.status, agents.list
- graph_pipeline
- 全部 MCP 工具

## 实现方案

### Step 1: 修复 `resolve_profile_first_tools()` — 只 mount Core + promoted

```python
# capability_pack.py

async def resolve_profile_first_tools(self, ...):
    core_set = CoreToolSet.default()
    promotion_state = (
        self._tool_promotion.state if self._tool_promotion else None
    )
    mounted_tools = []
    deferred_entries = []

    for meta, handler in self._tool_broker._registry.values():
        is_core = meta.name in core_set.tool_names
        is_promoted = (
            promotion_state is not None
            and promotion_state.is_promoted(meta.name)
        )
        if is_core or is_promoted:
            mounted_tools.append(...)       # 完整 schema 注入
        else:
            deferred_entries.append(...)     # 只保留名称+描述

    return DynamicToolSelection(
        mounted_tools=mounted_tools,
        deferred_entries=deferred_entries,   # 新增字段
        ...
    )
```

### Step 2: 注入 Deferred 工具列表到 system prompt

在 `render_behavior_system_block()` 末尾追加 deferred 工具段落：

```python
# agent_decision.py

def render_behavior_system_block(..., deferred_tools: list[DeferredToolEntry] | None = None):
    ...existing logic...

    # 追加 deferred 工具列表
    if deferred_tools:
        from octoagent.tooling.models import format_deferred_tools_list
        blocks.append(format_deferred_tools_list(deferred_tools))

    return "\n\n".join(blocks)
```

### Step 3: tool_search 返回后触发提升

在 SkillRunner 的工具执行链路中，识别 tool_search 返回并调用提升：

```python
# runner.py 或 llm_service.py

# 在 _execute_single_tool 之后检查
if call.tool_name == "tool_search" and not feedback.is_error:
    # 通知上层提升搜索到的工具
    await self._on_tool_search_result(feedback.output)
```

具体提升方式：通过回调或事件通知 `LLMService.process_tool_search_results()`，
将搜索到的工具名加入 `ToolPromotionState`。下一轮 LLM 调用时，
`resolve_profile_first_tools()` 会把 promoted 工具放入 `mounted_tools`。

### Step 4: MCP 工具默认 Deferred

对标 Claude Code：所有 MCP 工具默认 deferred，除非 MCP server 在 tool 的
annotations 中标记 `alwaysLoad: true`。

```python
# mcp_registry.py

# 构建 ToolMeta 时
tier = ToolTier.CORE if annotations.get("alwaysLoad") else ToolTier.DEFERRED
```

当前 MCP 工具已经是 `DEFERRED`（默认值），只需确保 `resolve_profile_first_tools()`
的过滤逻辑正确处理它们。

## 文件变更

| 文件 | 变更 | 说明 |
|------|------|------|
| `capability_pack.py` | `resolve_profile_first_tools()` 按 tier 过滤 | 核心修复 |
| `models.py` (tooling) | 更新 `CoreToolSet.default()` | 调优清单 |
| `agent_decision.py` | 注入 deferred 工具列表到 system prompt | 接通断点 2 |
| `runner.py` 或回调 | tool_search 返回后触发提升 | 接通断点 3 |
| `DynamicToolSelection` 模型 | 增加 `deferred_entries` 字段 | 传递 deferred 列表 |

## 不做

- **不引入新抽象**：不加 ToolPolicyPipeline、不加 ToolFilter 链
- **不做 subagent 裁剪**：当前 subagent 使用较少，等实际遇到问题再加
- **不做工具排序优化**：LiteLLM 不支持 prompt cache prefix 策略
- **不做 auto 阈值**：Claude Code 的 `tst-auto` 模式依赖 token 计数，复杂度高，等需要再加

## 效果

| 指标 | 改动前 | 改动后 |
|------|--------|--------|
| LLM 首轮 tools schema 数 | 56+ | 9 |
| LLM context 工具 schema 占用 | ~15K tokens | ~3K tokens |
| System prompt deferred 列表 | 无 | ~500 tokens |
| tool_search 提升后可用 | 链路断开 | 链路连通 |
| MCP 工具默认状态 | 全 mounted | deferred |

## 验证

1. 启动实例，观察 LLM 调用日志 `tools_count`（应为 9 而非 56+）
2. 检查 system prompt 中出现 "Available Tools (Deferred)" 段落
3. 对话中让 Agent 用 `tool_search("浏览器")` → 下一轮应能调用 `browser.open`
4. 用 Qwen 35B 测试 function calling 稳定性
