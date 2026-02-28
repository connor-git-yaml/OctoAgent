"""数据模型 -- TokenUsage + ModelCallResult

对齐 data-model.md SS2.1 / SS2.2，替代 M0 LLMResponse dataclass。
"""

from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    """Token 使用统计

    key 命名对齐 OpenAI/LiteLLM 行业标准：
    prompt_tokens / completion_tokens / total_tokens
    """

    prompt_tokens: int = Field(default=0, ge=0, description="输入 token 数")
    completion_tokens: int = Field(default=0, ge=0, description="输出 token 数")
    total_tokens: int = Field(default=0, ge=0, description="总 token 数")


class ModelCallResult(BaseModel):
    """LLM 调用结果 -- 替代 M0 LLMResponse

    包含响应内容、路由信息、成本数据、降级标记等完整信息。
    所有 provider（LiteLLM、Echo、Mock）统一返回此类型。
    """

    # 响应内容
    content: str = Field(description="LLM 响应文本内容")

    # 路由信息
    model_alias: str = Field(description="请求时使用的语义 alias 或运行时 group")
    model_name: str = Field(default="", description="实际调用的模型名称（如 gpt-4o-mini）")
    provider: str = Field(default="", description="实际 provider（如 openai/anthropic）")

    # 性能指标
    duration_ms: int = Field(ge=0, description="端到端耗时（毫秒）")

    # Token 使用
    token_usage: TokenUsage = Field(
        default_factory=TokenUsage,
        description="Token 使用详情",
    )

    # 成本数据
    cost_usd: float = Field(default=0.0, ge=0.0, description="本次调用的 USD 成本")
    cost_unavailable: bool = Field(
        default=False,
        description="成本数据是否不可用（双通道均失败时为 True）",
    )

    # 降级信息
    is_fallback: bool = Field(default=False, description="是否为降级调用")
    fallback_reason: str = Field(default="", description="降级原因说明")
