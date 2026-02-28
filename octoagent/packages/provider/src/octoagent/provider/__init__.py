"""OctoAgent Provider -- LLM 调用抽象层

packages/provider 的公开接口导出。
对齐 contracts/provider-api.md SS1。
"""

# 数据模型
from .alias import AliasConfig, AliasRegistry

# 核心组件
from .client import LiteLLMClient

# 配置
from .config import ProviderConfig, load_provider_config
from .cost import CostTracker
from .echo_adapter import EchoMessageAdapter

# 异常
from .exceptions import CostCalculationError, ProviderError, ProxyUnreachableError
from .fallback import FallbackManager
from .models import ModelCallResult, TokenUsage

__all__ = [
    "ModelCallResult",
    "TokenUsage",
    "LiteLLMClient",
    "AliasConfig",
    "AliasRegistry",
    "CostTracker",
    "FallbackManager",
    "EchoMessageAdapter",
    "ProviderConfig",
    "load_provider_config",
    "ProviderError",
    "ProxyUnreachableError",
    "CostCalculationError",
]
