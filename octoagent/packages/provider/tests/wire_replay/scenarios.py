"""录制/回放共享场景输入（单一事实源，spec D6）。

录制脚本（record_cassettes.py）与回放测试用**同一份**输入驱动 ProviderClient：
matcher 只松匹配 method/host/path，但请求构造路径（history 转换 / tools 翻译 /
tool_choice 注入 / URL 拼接）真实跑一遍是本套件价值的一半。

全部内容为**中性合成文本**（spec §7 风险表：不含宿主配置 / 个人信息 / 凭证）。
"""

from __future__ import annotations

from typing import Any

#: 演示工具（三 transport 通用 OpenAI Chat 嵌套格式；ProviderClient 内部各自翻译）。
WEATHER_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "demo.weather",
        "description": "查询指定城市当天天气（演示用假工具）",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名"},
            },
            "required": ["city"],
        },
    },
}

#: 强制选中演示工具（消除模型自主决策的不确定性；OpenAI Chat 格式，
#: responses/anthropic 由 ProviderClient 内部翻译）。
WEATHER_TOOL_CHOICE: dict[str, Any] = {
    "type": "function",
    "function": {"name": "demo.weather"},
}

# ---------------------------------------------------------------- openai_chat
CHAT_SIMPLE: dict[str, Any] = {
    "instructions": "你是一个极简助手，用一句话回答。",
    "history": [{"role": "user", "content": "用一句话说明什么是 SSE（Server-Sent Events）。"}],
    "tools": [],
}

CHAT_TOOL_CALL: dict[str, Any] = {
    "instructions": "你是一个会用工具的助手。",
    "history": [{"role": "user", "content": "上海今天天气怎么样？"}],
    "tools": [WEATHER_TOOL],
    "tool_choice": WEATHER_TOOL_CHOICE,
}

#: U+2028 探针（spec §5）：请模型原样复读含 LINE SEPARATOR 的串。
#: wire 上若以未转义原始字符回流 → LineDecoder 切行 → delta 静默丢（F142 钉住面）。
U2028_PROBE_TEXT = "A\u2028B"  # 中间是 U+2028 LINE SEPARATOR（显式转义防编辑器吞字符）
CHAT_U2028_PROBE: dict[str, Any] = {
    "instructions": (
        "你是一个复读机：把用户消息中反引号内的内容原样输出，不加任何解释、引号或空白改写。"
    ),
    "history": [{"role": "user", "content": f"请原样输出反引号内的内容：`{U2028_PROBE_TEXT}`"}],
    "tools": [],
}

EMBED_MODEL = "Qwen/Qwen3-Embedding-0.6B"
EMBED_TEXTS: list[str] = ["OctoAgent wire replay probe"]

# ---------------------------------------------------------------- openai_responses
RESPONSES_SIMPLE: dict[str, Any] = {
    "instructions": "You are a terse assistant. Answer in one short sentence.",
    "history": [{"role": "user", "content": "What does SSE stand for in web APIs?"}],
    "tools": [],
}

RESPONSES_TOOL_CALL: dict[str, Any] = {
    "instructions": "You are an assistant that uses tools when asked about weather.",
    "history": [{"role": "user", "content": "What's the weather in Shanghai today?"}],
    "tools": [WEATHER_TOOL],
    "tool_choice": WEATHER_TOOL_CHOICE,
}

# ---------------------------------------------------------------- anthropic_messages
# 注意：anthropic cassette 为手写 golden（宿主无可用凭证，spec §2）；场景输入
# 仍是回放测试驱动请求构造路径的单一事实源。
ANTHROPIC_SIMPLE: dict[str, Any] = {
    "instructions": "You are a terse assistant. Answer in one short sentence.",
    "history": [{"role": "user", "content": "What does SSE stand for in web APIs?"}],
    "tools": [],
}

ANTHROPIC_TOOL_CALL: dict[str, Any] = {
    "instructions": "You are an assistant that uses tools when asked about weather.",
    "history": [{"role": "user", "content": "What's the weather in Shanghai today?"}],
    "tools": [WEATHER_TOOL],
    "tool_choice": WEATHER_TOOL_CHOICE,
}
