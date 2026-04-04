# Agent Zero 技术架构深度分析

> 源码版本：基于 `_references/opensource/agent-zero/` 目录快照
> 分析日期：2026-03-22

---

## 1. 系统概览

### 1.1 技术栈

| 层         | 技术选型                                          |
| ---------- | ------------------------------------------------- |
| 语言       | Python 3.12+                                      |
| LLM 调用   | LiteLLM（统一多 provider）                        |
| 向量存储   | FAISS（langchain_community 封装）                  |
| Embedding  | LiteLLM / 本地 SentenceTransformer                |
| Web UI     | 自研前端 + WebSocket                               |
| 代码执行   | SSH（Docker 容器） / 本地 Shell                    |
| 消息格式   | LangChain BaseMessage                              |
| 浏览器代理 | browser-use 库                                     |
| 模板系统   | 自研 Markdown + `{{placeholder}}` + `{{include}}` |
| 调度       | APScheduler（scheduler 工具）                      |

### 1.2 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                         Web UI                              │
│                   (WebSocket + REST API)                     │
└──────────────┬──────────────────────────────┬───────────────┘
               │ communicate()                │
               ▼                              │
┌──────────────────────────┐                  │
│      AgentContext         │  管理会话状态     │
│  ┌────────────────────┐   │                  │
│  │  Agent (agent0)     │   │  DeferredTask   │
│  │  ┌──────────────┐   │   │  (线程池执行)    │
│  │  │  monologue() │   │   │                  │
│  │  │  ┌─────────┐ │   │   │                  │
│  │  │  │ Loop:   │ │   │   │                  │
│  │  │  │ prompt  │ │   │   │                  │
│  │  │  │ → LLM   │ │   │   │                  │
│  │  │  │ → parse │ │   │   │                  │
│  │  │  │ → tool  │ │   │   │                  │
│  │  │  │ → loop  │ │   │   │                  │
│  │  │  └─────────┘ │   │   │                  │
│  │  └──────────────┘   │   │                  │
│  │                      │   │                  │
│  │  Subordinate Agent   │   │                  │
│  │  (number = N+1)      │   │                  │
│  └────────────────────┘   │                  │
└──────────────────────────┘                  │
               │                              │
               ▼                              ▼
┌──────────────────────┐    ┌──────────────────────────┐
│   LiteLLM Wrapper    │    │    Extension System      │
│  (models.py)         │    │  python/extensions/      │
│  ┌────────────────┐  │    │  - system_prompt         │
│  │ Chat Model     │  │    │  - message_loop_*        │
│  │ Utility Model  │  │    │  - monologue_*           │
│  │ Browser Model  │  │    │  - tool_execute_*        │
│  │ Embedding      │  │    │  - response_stream       │
│  └────────────────┘  │    └──────────────────────────┘
└──────────────────────┘
               │                              │
               ▼                              ▼
┌──────────────────────┐    ┌──────────────────────────┐
│   Tool System        │    │    Memory (FAISS)        │
│  python/tools/       │    │  - main / fragments      │
│  - code_execution    │    │  - solutions             │
│  - memory_*          │    │  - knowledge preload     │
│  - call_subordinate  │    │  per-agent subdir        │
│  - behaviour_adjust  │    └──────────────────────────┘
│  - response          │
│  - browser_agent     │
└──────────────────────┘
```

### 1.3 核心设计哲学

1. **Free Loop（自由循环）**：Agent 没有预定义的状态机。`monologue()` 是一个无限循环，LLM 每轮输出一个 JSON 工具调用，框架执行后把结果喂回 LLM，直到 LLM 调用 `response` 工具主动退出。
2. **Extension Point 驱动**：核心流程的几乎所有阶段（system prompt 组装、消息循环前后、工具执行前后、monologue 结束等）都通过 Extension 机制注入逻辑，而非硬编码。
3. **Prompt-as-Code**：所有系统提示、工具描述、框架消息都以 `.md` 模板文件存放在 `prompts/` 目录，支持 `{{include}}` 嵌套和 `{{if}}` 条件。
4. **Subordinate 递归委派**：多 Agent 通过 `call_subordinate` 工具递归嵌套——subordinate 的 `monologue()` 在 superior 的工具调用循环内同步执行。
5. **Utility Model 分离**：辅助任务（记忆提取、历史压缩、行为合并、聊天重命名等）使用独立的小模型，与主聊天模型解耦。

---

## 2. 用户消息完整执行路径

以下是从用户在 Web UI 发送消息到最终响应返回的完整调用链：

```
用户消息
  │
  ▼
AgentContext.communicate(UserMessage)          # agent.py:246
  │
  ├─ 如果 task 正在运行 → 设置 intervention    # agent.py:253-259
  │   (intervention 会在下次 handle_intervention() 时被处理)
  │
  └─ 否则 → run_task(_process_chain)           # agent.py:261
       │
       ▼
     _process_chain(agent, msg)                # agent.py:276
       │
       ├─ agent.hist_add_user_message(msg)     # agent.py:279
       │    └─ history.new_topic()             # 新消息开启新 Topic
       │    └─ parse_prompt("fw.user_message.md")
       │    └─ hist_add_message(ai=False, content)
       │
       ├─ agent.monologue()                    # agent.py:285 ← 核心主循环
       │    │
       │    ├─ call_extensions("monologue_start")
       │    │    ├─ _10_memory_init.py          # 初始化 memory search
       │    │    └─ _60_rename_chat.py          # 异步重命名聊天
       │    │
       │    └─ while True:  ← 消息循环
       │         │
       │         ├─ call_extensions("message_loop_start")
       │         │    └─ _10_iteration_no.py     # 记录迭代号
       │         │
       │         ├─ handle_intervention()        # 检查用户干预
       │         │
       │         ├─ prepare_prompt(loop_data)     # agent.py:535
       │         │    ├─ call_extensions("message_loop_prompts_before")
       │         │    │    └─ _90_organize_history_wait.py  # 等待压缩完成
       │         │    │
       │         │    ├─ get_system_prompt()       # agent.py:639
       │         │    │    └─ call_extensions("system_prompt")
       │         │    │         ├─ _10_system_prompt.py  # 主 prompt + tools + secrets + project
       │         │    │         └─ _20_behaviour_prompt.py  # behaviour 规则（插入到 index 0）
       │         │    │
       │         │    ├─ history.output()           # 输出消息历史
       │         │    │
       │         │    ├─ call_extensions("message_loop_prompts_after")
       │         │    │    ├─ _50_recall_memories.py    # 向量检索相关记忆
       │         │    │    ├─ _60_include_current_datetime.py
       │         │    │    ├─ _65_include_loaded_skills.py
       │         │    │    ├─ _70_include_agent_info.py
       │         │    │    ├─ _75_include_workdir_extras.py  # 项目文件结构
       │         │    │    └─ _91_recall_wait.py          # 等待 memory recall 完成
       │         │    │
       │         │    ├─ 拼接 system_text = "\n\n".join(system)
       │         │    ├─ 拼接 extras（persistent + temporary）
       │         │    └─ 构建 [SystemMessage, ...history, ...extras]
       │         │
       │         ├─ call_chat_model(prompt)          # agent.py:454
       │         │    └─ model.unified_call()         # models.py:465
       │         │         └─ litellm.acompletion()    # 流式调用
       │         │              ├─ reasoning_callback   # 推理流
       │         │              └─ stream_callback      # 响应流
       │         │
       │         ├─ 重复检测：if last_response == response → 警告
       │         │
       │         ├─ hist_add_ai_response(response)
       │         │
       │         ├─ process_tools(response)             # agent.py:855
       │         │    ├─ json_parse_dirty(msg)           # 提取 JSON 工具调用
       │         │    ├─ get_tool(name, args)            # 按名查找工具
       │         │    │    └─ subagents.get_paths() → extract_tools.load_classes_from_file()
       │         │    ├─ tool.before_execution()
       │         │    ├─ call_extensions("tool_execute_before")
       │         │    ├─ tool.execute(**args)
       │         │    ├─ call_extensions("tool_execute_after")
       │         │    ├─ tool.after_execution(response)
       │         │    └─ if response.break_loop → return  # response 工具终止循环
       │         │
       │         └─ call_extensions("message_loop_end")
       │              ├─ _10_organize_history.py  # 异步压缩历史
       │              └─ _90_save_chat.py          # 持久化聊天
       │
       ├─ call_extensions("monologue_end")
       │    ├─ _50_memorize_fragments.py     # 提取事实记忆
       │    ├─ _51_memorize_solutions.py     # 提取解决方案
       │    └─ _90_waiting_for_input_msg.py
       │
       └─ 如果有 superior → _process_chain(superior, response)  # 递归回溯
```

**关键路径特征**：
- 主循环没有固定迭代上限，完全由 LLM 自主决定何时调用 `response` 工具退出
- `handle_intervention()` 在每个关键节点都被调用，允许用户随时打断
- 历史压缩和记忆提取在后台线程异步执行，不阻塞主循环

---

## 3. 编排层详解

### 3.1 Agent 主循环

Agent 的核心逻辑在 `monologue()` 方法（`agent.py:383-533`）中。它是一个双层 `while True` 循环：

**外层循环**（`agent.py:385`）：处理 `monologue_start` / `monologue_end` 生命周期。当 `InterventionException` 发生时重启整个 monologue，当其他异常发生时进行重试。

**内层循环**（`agent.py:395`）：消息循环。每次迭代：
1. 调用 `prepare_prompt()` 组装完整的 LLM 输入
2. 调用 `call_chat_model()` 获取 LLM 响应
3. 检测重复响应（连续两次相同响应会发出警告）
4. 调用 `process_tools()` 解析并执行工具
5. 如果工具返回 `break_loop=True`（即 `response` 工具），退出循环返回结果

**错误处理策略**（`agent.py:586-637`）：
- `RepairableException`：转发给 LLM 尝试自修复，作为 warning 加入历史
- `InterventionException`：用户干预，重置迭代
- 一般异常：`retry_critical_exception()` 最多重试 1 次（`max_retries=1`），之后抛出 `HandledException` 终止循环

### 3.2 工具调用循环

`process_tools()` 方法（`agent.py:855-947`）负责从 LLM 响应中提取工具调用：

```python
# agent.py:857 - 从 LLM 文本响应中提取 JSON
tool_request = extract_tools.json_parse_dirty(msg)
```

Agent Zero 的工具调用不使用 LLM 原生的 function calling，而是要求 LLM 在文本中输出 JSON 格式：

```json
{
  "tool_name": "code_execution",
  "tool_args": {
    "runtime": "terminal",
    "code": "ls -la"
  }
}
```

`json_parse_dirty()`（`extract_tools.py:9`）使用 `DirtyJson` 解析器，能容忍不规范的 JSON 格式（缺少引号、多余逗号等）。

工具查找优先级（`agent.py:872-898`）：
1. 先尝试 MCP 工具（`mcp_handler.MCPConfig.get_tool()`）
2. 回退到本地文件系统工具（`get_tool()` → `subagents.get_paths()` 搜索 `python/tools/{name}.py`）

工具名支持 `:` 分隔的方法调用：`"tool_name": "code_execution:terminal"` 会拆分为 `tool_name="code_execution"`, `tool_method="terminal"`。

如果 LLM 响应中找不到有效的 JSON 工具调用，框架会发送 `fw.msg_misformat.md` 警告，要求 LLM 重新格式化。

### 3.3 Subordinate Agent 委派

多 Agent 通过 `call_subordinate` 工具（`python/tools/call_subordinate.py`）实现递归委派：

```python
# call_subordinate.py:24 - 创建 subordinate
sub = Agent(self.agent.number + 1, config, self.agent.context)
sub.set_data(Agent.DATA_NAME_SUPERIOR, self.agent)    # 注册 superior
self.agent.set_data(Agent.DATA_NAME_SUBORDINATE, sub)  # 注册 subordinate
```

**关键设计细节**：
- Subordinate 与 Superior 共享同一个 `AgentContext`，因此共享同一个 `DeferredTask` 线程
- Subordinate 可以指定不同的 `profile`（Agent 配置 profile），从而使用不同的 prompt 和行为规则
- Subordinate 的 `monologue()` 在 superior 的 `process_tools()` 内同步 `await` 执行，是阻塞式嵌套
- Agent number 递增（A0 → A1 → A2...），没有硬性深度限制
- 当 subordinate 完成后，其结果通过 `_process_chain()` 递归回传给 superior（`agent.py:286-288`），superior 将结果作为 `call_subordinate` 工具的返回值继续处理

**回溯机制**（`agent.py:276-293`）：`_process_chain` 方法在 subordinate 完成后检查是否有 superior，如果有则递归将结果向上传递。这保证即使聊天从文件恢复（原始调用栈丢失），回溯链依然完整。

---

## 4. Context 管理详解

### 4.1 System Prompt 组装

System prompt 的组装分为两个阶段，由 Extension 机制驱动：

**阶段一：`system_prompt` Extension Point**

`_10_system_prompt.py`（`python/extensions/system_prompt/_10_system_prompt.py`）组装以下片段：

| 片段               | 来源                                    | 说明                          |
| ------------------ | --------------------------------------- | ----------------------------- |
| main prompt        | `agent.system.main.md` → 包含 role / environment / communication / solving / tips | 基础角色定义 |
| tools prompt       | `agent.system.tools.md` + 动态工具列表   | 可用工具描述                  |
| vision prompt      | `agent.system.tools_vision.md`           | 仅在 vision=true 时追加       |
| MCP tools          | `MCPConfig.get_tools_prompt()`           | MCP 服务器提供的工具描述      |
| skills             | `agent.system.skills.md`                 | 可用 skills 列表              |
| secrets            | `agent.system.secrets.md`                | 已注册的 secrets 变量         |
| project prompt     | `agent.system.projects.*.md`             | 项目信息（活跃/非活跃）      |

`_20_behaviour_prompt.py`（`python/extensions/system_prompt/_20_behaviour_prompt.py`）将行为规则 **插入到 system_prompt 列表的 index 0**（最高优先级位置）。行为规则来自 `behaviour.md` 文件，如果不存在则使用默认规则。

**阶段二：`message_loop_prompts_after` Extension Point**

在 system prompt 和 history 组装完成后，以下 Extension 向 `extras_temporary` / `extras_persistent` 注入上下文：

| 优先级 | Extension | 注入内容 |
| ------ | --------- | -------- |
| _50 | `recall_memories.py` | 从 FAISS 检索的相关记忆和解决方案 |
| _60 | `include_current_datetime.py` | 当前日期时间 |
| _65 | `include_loaded_skills.py` | 已加载的 skill 内容 |
| _70 | `include_agent_info.py` | Agent 编号、profile、LLM 型号 |
| _75 | `include_workdir_extras.py` | 项目文件目录树 |
| _91 | `recall_wait.py` | 等待 memory recall 异步任务完成 |

**最终 prompt 拼接**（`agent.py:535-584`）：

```python
# 1. system_text = 所有 system 片段用 \n\n 连接
system_text = "\n\n".join(loop_data.system)

# 2. extras 作为最后一条 user message 附加
extras = history.Message(False, content=agent.read_prompt(
    "agent.context.extras.md",
    extras=dirty_json.stringify({**extras_persistent, **extras_temporary})
))

# 3. 最终 prompt = [SystemMessage(system_text), ...history_langchain, ...extras]
full_prompt = [SystemMessage(content=system_text), *history_langchain]
```

### 4.2 Prompt 模板系统

Agent Zero 使用自研的 Markdown 模板系统（`python/helpers/files.py`）：

**变量替换**（`files.py:269-275`）：`{{variable_name}}` 在模板内被直接替换为传入的 kwargs 值。

**Include 指令**（`files.py:317-334`）：`{{ include "path/to/file.md" }}` 递归包含其他模板文件。主 prompt `agent.system.main.md` 就是用 include 组装的：

```markdown
# Agent Zero System Manual
{{ include "agent.system.main.role.md" }}
{{ include "agent.system.main.environment.md" }}
{{ include "agent.system.main.communication.md" }}
{{ include "agent.system.main.solving.md" }}
{{ include "agent.system.main.tips.md" }}
```

**条件语句**（`files.py:159-203`）：`{{if condition}}...{{endif}}` 支持嵌套，使用 `simpleeval` 安全求值。

**Python 插件变量**（`files.py:22-78`）：如果 `.md` 模板有同名的 `.py` 文件，该 Python 文件可以实现 `VariablesPlugin` 类，动态注入模板变量。例如 `agent.system.tools.py` 为 `agent.system.tools.md` 提供工具列表。

**JSON 模板**（`files.py:84-116`）：如果模板被 ` ```json ``` ` 围栏包裹，则作为 JSON 模板解析，变量替换使用 `json.dumps()` 保持类型安全。

### 4.3 上下文压缩策略

Agent Zero 实现了多层渐进式上下文压缩（`python/helpers/history.py`）：

**History 数据结构**：

```
History
  ├── bulks: list[Bulk]      # 最旧的消息，已被摘要
  ├── topics: list[Topic]    # 历史话题，可能部分压缩
  └── current: Topic         # 当前话题（最新，未压缩）
       └── messages: list[Message]
```

**空间分配比例**（`history.py:13-15`）：

```python
CURRENT_TOPIC_RATIO = 0.5    # 当前话题占 50%
HISTORY_TOPIC_RATIO = 0.3    # 历史话题占 30%
HISTORY_BULK_RATIO  = 0.2    # 已归档 bulk 占 20%
```

**压缩触发**：在每次 `message_loop_end` 时，`_10_organize_history.py` 在后台线程启动 `history.compress()` 方法。

**压缩策略层次**（`history.py:368-417`）：

1. **大消息截断**（`Topic.compress_large_messages()`，`history.py:161-196`）：
   - 计算单条消息允许的最大 token 数（上下文长度 * 历史比例 * 消息比例）
   - 对超限消息：raw message 直接替换为 "Message content replaced to save space"；普通消息使用 `truncate_dict_by_ratio()` 按比例截断

2. **注意力窗口压缩**（`Topic.compress_attention()`，`history.py:205-220`）：
   - 保留话题的第一条和最后两条消息（请求和最终响应）
   - 中间消息通过 utility model 摘要压缩
   - `CURRENT_TOPIC_ATTENTION_COMPRESSION = 0.65`（保留 65% 的中间消息）
   - `HISTORY_TOPIC_ATTENTION_COMPRESSION = 0`（历史话题中间消息全部摘要）

3. **话题合并到 Bulk**（`History.compress_topics()`，`history.py:419-441`）：
   - 每次将最旧的 `TOPICS_MERGE_COUNT=3` 个话题合并为一个 Bulk，通过 utility model 生成摘要

4. **Bulk 合并**（`History.compress_bulks()`，`history.py:443-448`）：
   - 将 Bulk 分组合并（每 `BULK_MERGE_COUNT=3` 个合并为一个）
   - 如果合并后仍然超限，丢弃最旧的 Bulk

**压缩目标比**：`COMPRESSION_TARGET_RATIO = 0.8`，即压缩到上限的 80%，留出余量。

### 4.4 Agent 配置继承

Agent Zero 通过 `subagents.py` 的路径搜索机制实现配置继承（`subagents.py:300-359`）。`get_paths()` 按优先级返回文件搜索路径：

```
1. project/agents/<profile>/...    # 项目级 agent 配置（最高优先级）
2. project/.a0proj/...              # 项目元数据
3. usr/agents/<profile>/...         # 用户自定义 agent 配置
4. agents/<profile>/...             # 默认 agent 配置
5. usr/...                          # 用户全局配置
6. <default_root>/...               # 系统默认（如 python/）
```

这意味着用户可以通过在 `usr/agents/<profile>/prompts/` 放置同名文件来覆盖默认 prompt，而不修改源码。

Agent profile 配置存储在 `agents/<profile>/agent.json`，包含 `title`、`description`、`context` 字段。Profile 之间的继承通过 `_merge_agents()` 实现：子 profile 的字段覆盖父 profile，prompts 字典合并。

---

## 5. Tool 系统详解

### 5.1 工具发现和注册

Agent Zero 使用文件系统自动发现工具，无需显式注册：

```python
# agent.py:988-989 - 在 agent 的路径层级中搜索工具文件
paths = subagents.get_paths(self, "tools", name + ".py", default_root="python")
for path in paths:
    classes = extract_tools.load_classes_from_file(path, Tool)
```

`load_classes_from_file()`（`extract_tools.py:104-120`）使用 `importlib.util.spec_from_file_location()` 动态加载 Python 模块，通过 `inspect.getmembers()` 找到所有 `Tool` 子类。

**工具基类** `Tool`（`python/helpers/tool.py:16-67`）定义了工具接口：

```python
class Tool:
    def __init__(self, agent, name, method, args, message, loop_data, **kwargs)
    async def execute(self, **kwargs) -> Response    # 抽象方法
    async def before_execution(self, **kwargs)       # 日志 + 打印参数
    async def after_execution(self, response)        # 将结果加入历史
```

`Response` 数据类包含两个关键字段：
- `message: str` — 工具返回的文本
- `break_loop: bool` — 是否终止消息循环

### 5.2 代码执行工具（Docker 隔离）

`CodeExecution`（`python/tools/code_execution_tool.py`）是最复杂的工具，支持多种运行时：

**运行时类型**（`code_execution_tool.py:60-96`）：
- `python`：通过 ipython 执行（`code_execution_tool.py:163-167`）
- `nodejs`：通过 `node /exe/node_eval.js` 执行（`code_execution_tool.py:169-173`）
- `terminal`：直接执行 shell 命令（`code_execution_tool.py:175-179`）
- `output`：获取正在运行的 session 的输出（`code_execution_tool.py:82`）
- `reset`：重置 terminal session（`code_execution_tool.py:84`）

**会话管理**（`code_execution_tool.py:117-161`）：
- 支持多 session（`session` 参数指定编号），每个 session 独立管理
- SSH 模式（Docker）：通过 `SSHInteractiveSession` 连接到 Docker 容器
- 本地模式：通过 `LocalInteractiveSession` 在宿主机执行

**超时机制**（`code_execution_tool.py:16-29`）：

```python
CODE_EXEC_TIMEOUTS = {
    "first_output_timeout": 30,     # 首次输出等待
    "between_output_timeout": 15,   # 输出间隔等待
    "max_exec_timeout": 180,        # 总执行时间上限
    "dialog_timeout": 5,            # 交互对话检测
}
```

**输出处理**（`code_execution_tool.py:236-373`）：
- 使用 prompt 正则检测 shell 提示符（如 `root@container:~#`），检测到则提前返回
- 使用 dialog 正则检测交互提示（如 `Y/N`、`yes/no`），检测到则返回并通知 LLM
- 输出截断：`fix_full_output()` 调用 `truncate_text_agent()` 限制输出最大约 1MB（`code_execution_tool.py:473`）

**工作目录**（`code_execution_tool.py:476-489`）：如果有活跃项目，cwd 设置为项目目录；否则使用配置的 `workdir_path`。

### 5.3 行为调整工具

`UpdateBehaviour`（`python/tools/behaviour_adjustment.py`）允许 LLM 在运行时自我调整行为规则：

```python
# behaviour_adjustment.py:27-51
async def update_behaviour(agent, log_item, adjustments):
    # 1. 读取当前规则和 system prompt
    system = agent.read_prompt("behaviour.merge.sys.md")
    current_rules = read_rules(agent)

    # 2. 调用 utility model 合并新旧规则
    adjustments_merge = await agent.call_utility_model(
        system=system,
        message=agent.read_prompt("behaviour.merge.msg.md",
                                   current_rules=current_rules,
                                   adjustments=adjustments),
    )

    # 3. 写入行为文件
    rules_file = get_custom_rules_file(agent)  # → usr/memory/<subdir>/behaviour.md
    files.write_file(rules_file, adjustments_merge)
```

行为文件存储在 memory 子目录中（`memory/<subdir>/behaviour.md`），与 memory 分区绑定。每次 system prompt 组装时，`_20_behaviour_prompt.py` 会读取此文件并插入到 system prompt 最前面。

### 5.4 工具输出截断策略

Agent Zero 有两层截断机制：

**层一：文本截断**（`python/helpers/messages.py:6-21`）：

```python
def truncate_text(agent, output, threshold=1000):
    if len(output) <= threshold:
        return output
    placeholder = agent.read_prompt("fw.msg_truncated.md", length=(len(output) - threshold))
    # 占位符格式：<< {length} CHARACTERS REMOVED TO SAVE SPACE >>
    start_len = (threshold - len(placeholder)) // 2
    end_len = threshold - len(placeholder) - start_len
    return output[:start_len] + placeholder + output[-end_len:]
```

截断保留首尾各一半，中间替换为占位符。这在工具输出和代码执行输出中广泛使用。

**层二：字典/列表按比例截断**（`python/helpers/messages.py:24-75`）：

`truncate_dict_by_ratio()` 递归遍历嵌套数据结构，当累计大小超过阈值时开始截断后续字段。用于历史消息压缩中对大型结构化消息的处理。

**层三：代码执行输出截断**（`code_execution_tool.py:468-474`）：

```python
def fix_full_output(self, output):
    output = re.sub(r"(?<!\\)\\x[0-9A-Fa-f]{2}", "", output)  # 清理转义
    output = truncate_text_agent(agent=self.agent, output=output, threshold=1000000)  # ~1MB 上限
    return output
```

### 5.5 完整工具列表

| 工具名 | 文件 | 功能 | break_loop |
| --- | --- | --- | --- |
| `response` | `response.py` | 返回最终响应，终止循环 | True |
| `code_execution` | `code_execution_tool.py` | 代码/命令执行（Docker/本地） | False |
| `call_subordinate` | `call_subordinate.py` | 委派任务给 subordinate agent | False |
| `memory_save` | `memory_save.py` | 手动保存记忆到向量数据库 | False |
| `memory_load` | `memory_load.py` | 手动查询向量数据库 | False |
| `memory_forget` | `memory_forget.py` | 从向量数据库删除记忆 | False |
| `memory_delete` | `memory_delete.py` | 按 ID 删除记忆 | False |
| `behaviour_adjustment` | `behaviour_adjustment.py` | 自我调整行为规则 | False |
| `search_engine` | `search_engine.py` | 网络搜索 | False |
| `browser_agent` | `browser_agent.py` | 浏览器自动化 | False |
| `document_query` | `document_query.py` | 文档查询 | False |
| `notify_user` | `notify_user.py` | 发送通知给用户 | False |
| `scheduler` | `scheduler.py` | 定时任务调度 | False |
| `wait` | `wait.py` | 等待指定时间 | False |
| `input` | `input.py` | 请求用户输入 | False |
| `skills_tool` | `skills_tool.py` | 技能管理 | False |
| `a2a_chat` | `a2a_chat.py` | Agent-to-Agent 通信 | False |
| `vision_load` | `vision_load.py` | 图片加载 | False |

---

## 6. Memory 系统详解

### 6.1 向量存储

Memory 系统基于 FAISS（`python/helpers/memory.py`），使用 LangChain 封装：

**初始化**（`memory.py:127-239`）：
- 使用 `CacheBackedEmbeddings` 缓存 embedding 结果到本地文件系统（`tmp/memory/embeddings/`）
- FAISS 索引使用 `IndexFlatIP`（内积），距离策略为 `COSINE`
- 自定义归一化函数 `_cosine_normalizer`：`(1 + val) / 2`
- 支持 embedding 模型变更时自动重建索引

**搜索接口**（`memory.py:330-341`）：

```python
async def search_similarity_threshold(self, query, limit, threshold, filter=""):
    comparator = Memory._get_comparator(filter) if filter else None
    return await self.db.asearch(
        query,
        search_type="similarity_score_threshold",
        k=limit,
        score_threshold=threshold,
        filter=comparator,
    )
```

`filter` 参数支持 Python 表达式字符串（如 `"area == 'main'"`），通过 `simpleeval` 安全求值。

### 6.2 Memory 分区

Memory 按 `area` 元数据字段分为三个区域（`memory.py:56-59`）：

```python
class Area(Enum):
    MAIN = "main"          # 用户手动保存的记忆（memory_save 工具）
    FRAGMENTS = "fragments" # 自动提取的事实碎片（monologue_end 扩展）
    SOLUTIONS = "solutions" # 自动提取的解决方案（monologue_end 扩展）
```

**分区存储路径**：

```
usr/memory/<subdir>/          # 默认记忆目录
  ├── index.faiss             # FAISS 索引
  ├── index.pkl               # docstore 序列化
  ├── embedding.json          # 当前 embedding 模型信息
  ├── behaviour.md            # 行为规则文件
  └── knowledge_import.json   # 知识预加载索引

usr/projects/<name>/.a0proj/memory/  # 项目级记忆
```

**per-agent 隔离**（`memory.py:517-533`）：Memory 子目录由 `AgentConfig.memory_subdir` 决定。如果有活跃项目且项目设置 `memory="own"`，则使用 `projects/<name>` 作为子目录，与全局记忆隔离。

### 6.3 记忆读写工具

**手动写入**（`memory_save.py`）：直接调用 `db.insert_text()`，支持指定 area 参数。

**手动查询**（`memory_load.py`）：默认阈值 0.7，限制 10 条。

**手动删除**（`memory_forget.py`）：按语义相似度查找并删除。

**自动记忆提取**：在 `monologue_end` 时由两个 Extension 触发：

- `_50_memorize_fragments.py`：调用 utility model 从对话中提取事实信息，存入 `FRAGMENTS` 区
- `_51_memorize_solutions.py`：调用 utility model 提取成功的问题/解决方案对，存入 `SOLUTIONS` 区

两者都支持智能合并（`memory_memorize_consolidation` 设置），使用 `memory_consolidation` 模块对相似记忆进行去重和合并。如果关闭合并，则使用 `memory_memorize_replace_threshold` 阈值直接替换相似记忆。

**自动记忆召回**：在 `message_loop_prompts_after` 时由 `_50_recall_memories.py` 触发：

1. 可选使用 utility model 从对话生成搜索 query（`memory_recall_query_prep` 设置）
2. 分别搜索 MAIN+FRAGMENTS 和 SOLUTIONS 两个区域
3. 可选通过 utility model 对结果进行相关性过滤（`memory_recall_post_filter` 设置）
4. 最终结果注入到 `extras_persistent["memories"]` 和 `extras_persistent["solutions"]`

### 6.4 Knowledge 预加载

`Memory.preload_knowledge()`（`memory.py:249-325`）支持从文件系统批量导入知识文档到向量数据库：
- 知识目录结构：`knowledge/<subdir>/` 根目录文件进入 MAIN 区，子目录按 Area 名称分区
- 使用 `knowledge_import.json` 跟踪文件变更状态（changed/removed）
- 支持项目级知识（`project/.a0proj/knowledge/`）

---

## 7. 项目和文件系统

### 7.1 多项目支持

Agent Zero 的项目系统（`python/helpers/projects.py`）提供工作空间隔离：

**目录结构**：
```
usr/projects/
  └── <project-name>/
       ├── .a0proj/                    # 项目元数据
       │    ├── project.json           # 项目头信息（title, description, instructions, color, memory）
       │    ├── instructions/          # 额外指令文件
       │    ├── knowledge/             # 项目知识库
       │    │    ├── main/
       │    │    ├── fragments/
       │    │    └── solutions/
       │    ├── agents/                # 项目级 agent 配置覆盖
       │    ├── variables.env          # 项目变量
       │    ├── agents.json            # 项目级 subagent 启用/禁用
       │    └── memory/                # 项目独立记忆（memory="own" 时）
       └── ... (项目工作文件)
```

**项目激活**（`projects.py:305-326`）：通过 `AgentContext.set_data("project", name)` 将项目绑定到会话。激活后影响：
- Memory 子目录切换到项目级（`projects/<name>`）
- System prompt 追加项目指令和描述
- 工具搜索路径增加项目级 `agents/` 目录
- 代码执行 cwd 切换到项目目录
- 文件结构展示为项目目录树

**项目 prompt 注入**（`projects.py:369-385`）：

```python
def build_system_prompt_vars(name):
    return {
        "project_name": ...,
        "project_description": ...,
        "project_instructions": ...,   # 主指令 + 额外指令文件
        "project_path": ...,
        "project_git_url": ...,
    }
```

### 7.2 工作目录隔离

**文件路径系统**（`python/helpers/files.py`）：
- `get_base_dir()`（`files.py:554-557`）：以 `agent.py` 所在目录为基准
- `get_abs_path(*relative_paths)`（`files.py:508-510`）：所有路径操作基于 base_dir 的相对路径
- `normalize_a0_path(path)`（`files.py:541-546`）：将绝对路径转为 `/a0/...` 格式（Docker 容器内路径）

### 7.3 Docker Sandbox

代码执行通过 SSH 连接到 Docker 容器，实现与宿主机的隔离：

**连接配置**（`agent.py:311-315`）：

```python
@dataclass
class AgentConfig:
    code_exec_ssh_enabled: bool = True
    code_exec_ssh_addr: str = "localhost"
    code_exec_ssh_port: int = 55022
    code_exec_ssh_user: str = "root"
    code_exec_ssh_pass: str = ""
```

**执行模式切换**（`code_execution_tool.py:139-154`）：
- `code_exec_ssh_enabled=True`：使用 `SSHInteractiveSession` 连接 Docker 容器
- `code_exec_ssh_enabled=False`：使用 `LocalInteractiveSession` 在宿主机执行

**Docker 容器管理**：通过 `DockerContainerManager`（`python/helpers/docker.py`）管理容器生命周期，容器配置在 `docker/run/` 目录中。

---

## 8. LLM 调用链详解

### 8.1 模型调用方式

Agent Zero 通过 LiteLLM 统一所有 LLM provider 的调用（`models.py`）：

**模型类型**（通过 `AgentConfig` 配置）：

| 模型     | 用途                                           | 获取方法           |
| -------- | ---------------------------------------------- | ------------------ |
| Chat     | 主对话 LLM                                     | `get_chat_model()` |
| Utility  | 辅助任务（摘要、记忆提取、行为合并等）         | `get_utility_model()` |
| Browser  | 浏览器自动化                                   | `get_browser_model()` |
| Embedding| 向量 embedding                                 | `get_embedding_model()` |

**LiteLLMChatWrapper**（`models.py:292-463`）：
- 继承 `langchain_core.SimpleChatModel`
- 将 LangChain `BaseMessage` 转换为 LiteLLM 格式
- 支持 Anthropic 的 `cache_control`（显式缓存 system message 和最后一条 assistant message）
- 支持 API key 逗号分隔的 round-robin（`models.py:201-213`）

**Rate Limiter**（`models.py:217-225`）：per-model 的速率限制，支持 requests/input/output 三个维度，60 秒滑动窗口。

### 8.2 Utility Model

Utility model 是 Agent Zero 的重要设计——使用小/便宜模型处理辅助任务：

```python
# agent.py:762-795
async def call_utility_model(self, system, message, callback=None, background=False):
    model = self.get_utility_model()
    # ... Extension 钩子（util_model_call_before）
    response, _reasoning = await model.unified_call(
        system_message=system, user_message=message,
        response_callback=stream_callback if callback else None,
    )
    return response
```

Utility model 使用场景：
- 历史消息摘要（`history.py:222-229`）
- 记忆查询 query 生成（`_50_recall_memories.py`）
- 记忆/解决方案提取（`_50_memorize_fragments.py` / `_51_memorize_solutions.py`）
- 记忆相关性过滤（`_50_recall_memories.py`）
- 行为规则合并（`behaviour_adjustment.py`）
- 聊天重命名（`_60_rename_chat.py`）
- 记忆合并决策（`memory_consolidation.py`）

### 8.3 流式响应

`unified_call()` 方法（`models.py:465-572`）是统一的 LLM 调用入口：

```python
async def unified_call(self, ..., response_callback, reasoning_callback, ...):
    # 判断是否流式
    stream = reasoning_callback or response_callback or tokens_callback
    _completion = await acompletion(model=..., messages=..., stream=stream)

    if stream:
        async for chunk in _completion:
            parsed = _parse_chunk(chunk)           # 提取 delta
            output = result.add_chunk(parsed)       # 处理 thinking tags
            if output["reasoning_delta"]:
                await reasoning_callback(delta, full_reasoning)
            if output["response_delta"]:
                await response_callback(delta, full_response)
```

**Thinking tag 处理**（`models.py:107-194`）：`ChatGenerationResult` 类自动检测并分离 `<think>` / `<reasoning>` 标签内的推理内容。如果 LLM 原生支持 reasoning（如 `reasoning_content` 字段），则使用原生分离。

**重试机制**（`models.py:508-572`）：
- 可配置的重试次数（`a0_retry_attempts`，默认 2 次）和重试延迟（`a0_retry_delay_seconds`，默认 1.5 秒）
- 只在未收到任何 chunk 且错误为瞬时错误（408/429/5xx）时重试
- 一旦收到了部分数据，不会重试（避免重复内容）

---

## 9. Extension 系统详解

### 9.1 Extension 机制

Extension 系统（`python/helpers/extension.py`）是 Agent Zero 的核心插件架构：

**Extension 基类**：

```python
class Extension:
    def __init__(self, agent, **kwargs):
        self.agent = agent
    async def execute(self, **kwargs) -> Any:
        pass  # 抽象方法
```

**调用流程**（`extension.py:27-48`）：

```python
async def call_extensions(extension_point, agent=None, **kwargs):
    # 1. 搜索所有路径层级中的 extension 文件夹
    paths = subagents.get_paths(agent, "extensions", extension_point, default_root="python")
    # 2. 从每个路径加载 Extension 子类
    all_exts = [cls for path in paths for cls in _get_extensions(path)]
    # 3. 按文件名去重（优先级高的路径覆盖低的）
    unique = {}  # filename → class
    for cls in all_exts:
        file = cls.__module__.split(".")[-1]
        if file not in unique:
            unique[file] = cls
    # 4. 按文件名排序执行
    classes = sorted(unique.values(), key=lambda cls: ...)
    for cls in classes:
        await cls(agent=agent).execute(**kwargs)
```

**命名约定**：Extension 文件名格式为 `_XX_description.py`，其中 `XX` 是两位数字优先级（越小越先执行）。

**Extension Point 全列表**：

| Extension Point | 触发时机 | 已知扩展 |
| --- | --- | --- |
| `agent_init` | Agent 构造时 | 初始消息、加载 profile 设置 |
| `system_prompt` | 每次 prepare_prompt | 主 prompt、行为规则 |
| `monologue_start` | monologue 开始 | 记忆初始化、重命名聊天 |
| `monologue_end` | monologue 结束 | 记忆提取、等待输入消息 |
| `message_loop_start` | 每次循环开始 | 迭代计数 |
| `message_loop_end` | 每次循环结束 | 历史压缩、保存聊天 |
| `message_loop_prompts_before` | prompt 组装前 | 等待历史压缩 |
| `message_loop_prompts_after` | prompt 组装后 | 记忆召回、日期时间、工作目录 |
| `before_main_llm_call` | LLM 调用前 | 流式日志 |
| `reasoning_stream` / `_chunk` / `_end` | 推理流处理 | 日志、masking |
| `response_stream` / `_chunk` / `_end` | 响应流处理 | 日志、masking、live_response |
| `tool_execute_before` | 工具执行前 | unmask secrets、替换上次输出 |
| `tool_execute_after` | 工具执行后 | mask secrets |
| `hist_add_before` | 添加历史前 | mask content |
| `hist_add_tool_result` | 工具结果入历史 | 保存工具调用文件 |
| `error_format` | 错误格式化 | mask errors |
| `util_model_call_before` | utility model 调用前 | mask secrets |
| `process_chain_end` | 整个处理链结束 | 处理队列 |
| `banners` | UI 横幅 | 安全警告、缺少 API key、系统资源 |
| `user_message_ui` | 用户消息 UI | 更新检查 |

---

## 10. 与 OctoAgent 的关键差异

### 10.1 架构差异

| 维度 | Agent Zero | OctoAgent |
| --- | --- | --- |
| 整体架构 | 单体应用（Agent + Web UI 一体） | 分层架构（Gateway → Kernel → Workers） |
| 编排模型 | 递归嵌套 monologue（superior ← subordinate 同步调用） | Orchestrator Free Loop + Worker 自治 + Skill Pipeline |
| 多 Agent | 同进程递归 Agent（共享 Context） | 独立 Worker 进程 + A2A-Lite 通信 |
| 状态管理 | 全内存（History/Context/Memory 对象） | Event Sourcing + SQLite WAL |
| 任务模型 | 无显式任务状态机 | Task 状态机（SUCCEEDED/FAILED/CANCELLED/REJECTED） |
| 门禁 | 无审批机制 | Policy Engine 双维度（PolicyAction × ApprovalDecision） |

### 10.2 工具系统差异

| 维度 | Agent Zero | OctoAgent |
| --- | --- | --- |
| 工具调用方式 | LLM 输出 JSON 文本 → DirtyJson 解析 | Pydantic AI 原生 tool_use |
| 工具发现 | 文件系统扫描 + 动态 import | Tool Broker + Schema 反射 |
| 工具类型 | 扁平 Tool 类继承 | Pydantic Skill（强类型 Input/Output） |
| 执行隔离 | SSH 到 Docker 容器 | Docker 执行隔离 |
| 工具审批 | 无 | Policy Engine 控制（safe/confirm/deny） |

### 10.3 Memory 模型差异

| 维度 | Agent Zero | OctoAgent |
| --- | --- | --- |
| 存储后端 | FAISS（文件持久化） | LanceDB（嵌入式向量数据库） |
| 分区模型 | 三区域（main / fragments / solutions） | 多层 SoR（System of Record）+ 敏感分区 |
| 写入方式 | 直接 insert + utility model 提取 | propose → validate → commit 工作流 |
| 行为文件 | 单文件 `behaviour.md`（utility model 合并） | 四层 BehaviorWorkspaceScope |
| 事实记忆 | 向量数据库中的 Document | 结构化 Memory Fragment + ARCHIVED 状态 |

### 10.4 上下文管理差异

| 维度 | Agent Zero | OctoAgent |
| --- | --- | --- |
| System prompt | Extension 动态组装 + Markdown 模板 | Behavior 文件分层 + Bootstrap |
| 历史压缩 | 三级渐进压缩（Topic → Bulk → 丢弃） | 待实现（blueprint 中有规划） |
| 上下文窗口 | 按比例分配（current 50% / history 30% / bulk 20%） | 按 ctx_length 动态管理 |
| 模板系统 | 自研 `{{}}` + `{{include}}` + `{{if}}` | Behavior 文件 + Jinja2 |

### 10.5 项目隔离差异

| 维度 | Agent Zero | OctoAgent |
| --- | --- | --- |
| 项目模型 | `.a0proj/` 元数据目录 + 记忆/知识分区 | project_path_manifest + storage_boundary_hints |
| 配置继承 | 路径优先级搜索（project > user > default） | 四层 scope（system_shared → project_agent） |
| 文件系统注入 | `file_tree` 模块扫描目录树 → extras | project_shared 工作材料 |
| 执行环境 | 共享 Docker 容器 + cwd 切换 | 独立 Docker 容器（计划中） |
