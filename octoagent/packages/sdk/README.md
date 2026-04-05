# OctoAgent SDK

轻量级 Agent 框架 — 3 行代码创建智能 Agent。

```python
from octoagent_sdk import Agent

agent = Agent(model="gpt-4o")
result = await agent.run("帮我查深圳今天天气")
print(result.text)
```

## 安装

```bash
pip install octoagent-sdk
```

## 快速上手

### 基础用法

```python
from octoagent_sdk import Agent

agent = Agent(model="gpt-4o", api_key="sk-...")
result = await agent.run("用一句话解释量子计算")
print(result.text)

# 同步用法（CLI / 脚本）
result = agent.run_sync("用一句话解释量子计算")
```

### 带工具

```python
from octoagent_sdk import Agent, tool

@tool
async def search_weather(city: str) -> str:
    """查询城市天气"""
    # 你的天气 API 调用
    return f"{city} 今天 26°C 多云"

@tool
async def calculate(expression: str) -> str:
    """计算数学表达式"""
    return str(eval(expression))

agent = Agent(
    model="gpt-4o",
    tools=[search_weather, calculate],
    system_prompt="你是一个实用助手，善用工具解决问题。",
)

result = await agent.run("深圳今天温度的华氏度是多少？")
# Agent 会先调 search_weather("深圳")，再调 calculate("26 * 9/5 + 32")
```

### 多轮对话

```python
agent = Agent(model="gpt-4o")
conv = agent.chat("你是一个翻译助手，用户说什么你就翻译成英文。")

r1 = await conv.send("你好世界")
print(r1.text)  # "Hello World"

r2 = await conv.send("再翻译成日语")
print(r2.text)  # "こんにちは世界"

# 对话历史自动保持
print(len(conv.messages))  # 5 (system + 2x user + 2x assistant)

# 清空历史开始新话题
conv.clear()
```

### 流式输出

```python
async for chunk in agent.run_stream("写一首关于春天的诗"):
    print(chunk.text, end="", flush=True)
```

### 自定义 LLM 端点

```python
# 硅基流动
agent = Agent(
    model="Qwen/Qwen3.5-35B-A3B",
    api_key="sk-xxx",
    api_base="https://api.siliconflow.cn/v1",
)

# 本地 Ollama
agent = Agent(
    model="llama3",
    api_base="http://localhost:11434/v1",
)

# LiteLLM Proxy
agent = Agent(
    model="gpt-4o",
    api_base="http://localhost:4000",
    api_key="sk-proxy-key",
)
```

### Policy 审批

```python
def my_policy(tool_name: str, arguments: dict) -> bool:
    """危险命令需要确认。"""
    if tool_name == "exec_command":
        cmd = arguments.get("command", "")
        if any(kw in cmd for kw in ["rm", "drop", "delete"]):
            return input(f"允许执行 {cmd}？(y/n) ") == "y"
    return True

agent = Agent(model="gpt-4o", tools=[exec_command], policy=my_policy)
```

## API 参考

### Agent

```python
Agent(
    model: str = "gpt-4o",        # 模型名称
    tools: list = None,            # 工具列表
    system_prompt: str = "",       # 系统提示
    api_key: str = "",             # API 密钥
    api_base: str = "",            # API 基础 URL
    max_steps: int = 30,           # 最大工具调用步数
    timeout_s: float = 120,        # 超时秒数
    temperature: float = 0.7,      # 温度
    memory: Any = None,            # Memory 存储（可选）
    policy: Any = None,            # Policy 审批（可选）
)
```

**方法：**
- `await agent.run(prompt)` → `AgentResult` — 异步执行
- `agent.run_sync(prompt)` → `AgentResult` — 同步执行
- `async for chunk in agent.run_stream(prompt)` — 流式执行
- `agent.chat(system_prompt)` → `Conversation` — 创建多轮对话

### @tool

```python
@tool
async def my_tool(arg1: str, arg2: int = 10) -> str:
    """工具描述（会自动提取为 LLM 的工具描述）"""
    return "结果"
```

自动从函数签名生成 JSON Schema，支持 `str/int/float/bool/list/dict` 类型。

### AgentResult

```python
result.text           # str — LLM 最终输出文本
result.tool_calls_count  # int — 工具调用次数
result.total_tokens   # int — 总 token 消耗
result.duration_ms    # int — 执行耗时（毫秒）
result.metadata       # dict — 附加元数据
```

### Conversation

```python
conv = agent.chat("system prompt")
result = await conv.send("用户消息")  # 发送并保持历史
conv.messages                         # 当前对话历史
conv.add_message("user", "手动追加")  # 手动追加消息
conv.clear()                          # 清空（保留 system）
```

## 与 OctoAgent 全栈的关系

`octoagent-sdk` 是 OctoAgent 生态的轻量入口：

```
octoagent-sdk (本包)     ← 3 行代码，纯 Python
    ↓ 可选集成
octoagent-memory         ← 长期记忆
octoagent-policy         ← 审批与权限
octoagent-gateway        ← Web UI + Telegram + 完整 Agent OS
```

SDK 不依赖 FastAPI / Web 服务，可以在任何 Python 环境中使用。

## License

MIT
