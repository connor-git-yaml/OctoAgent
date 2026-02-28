# Provider 包 API 契约

**特性**: 002-integrate-litellm-provider
**日期**: 2026-02-28
**包**: `packages/provider` (`octoagent-provider`)
**追踪**: FR-002-CL-1 ~ CL-4, FR-002-AL-1 ~ AL-3, FR-002-CT-1 ~ CT-3, FR-002-FM-1 ~ FM-3

---

## 1. 公开接口总览

```python
# packages/provider/src/octoagent/provider/__init__.py

# 数据模型
from .models import ModelCallResult, TokenUsage

# 核心组件
from .client import LiteLLMClient
from .alias import AliasConfig, AliasRegistry
from .cost import CostTracker
from .fallback import FallbackManager
from .echo_adapter import EchoMessageAdapter

# 配置
from .config import ProviderConfig, load_provider_config

# 异常
from .exceptions import ProviderError, ProxyUnreachableError, CostCalculationError

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
```

---

## 2. LiteLLMClient 契约

**文件**: `packages/provider/src/octoagent/provider/client.py`
**职责**: 封装 LiteLLM SDK `acompletion()` 调用 Proxy

### 2.1 构造器

```python
class LiteLLMClient:
    def __init__(
        self,
        proxy_base_url: str = "http://localhost:4000",
        proxy_api_key: str = "",
        timeout_s: int = 30,
    ) -> None:
        """初始化 LiteLLM Proxy 客户端

        Args:
            proxy_base_url: Proxy 基础 URL
            proxy_api_key: Proxy 访问密钥（LITELLM_PROXY_KEY）
            timeout_s: 请求超时（秒）

        注意: proxy_api_key 是 Proxy 管理密钥，不是 LLM provider API key。
              LLM provider API key 仅存在于 Proxy 容器环境变量中。
        """
```

### 2.2 complete() 方法

```python
    async def complete(
        self,
        messages: list[dict[str, str]],
        model_alias: str = "main",
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> ModelCallResult:
        """发送 chat completion 请求到 LiteLLM Proxy

        Args:
            messages: 消息列表，格式 [{"role": "user", "content": "..."}]
            model_alias: 运行时 group 名称（由 AliasRegistry.resolve() 提供）
            temperature: 采样温度
            max_tokens: 最大生成 token 数，None 使用模型默认
            **kwargs: 其他 LiteLLM 支持的参数

        Returns:
            ModelCallResult，包含完整的响应、成本、路由信息

        Raises:
            ProxyUnreachableError: Proxy 连接失败或超时
            ProviderError: Proxy 返回错误（如模型不可用、配额耗尽）

        实现要点:
            1. 调用 litellm.acompletion(model=model_alias, api_base=proxy_base_url, ...)
            2. 通过 CostTracker.calculate_cost() 计算成本
            3. 通过 CostTracker.parse_usage() 解析 token 使用
            4. 从 response 中提取 model_name 和 provider
            5. 计算 duration_ms
            6. 构建并返回 ModelCallResult
        """
```

### 2.3 health_check() 方法

```python
    async def health_check(self) -> bool:
        """检查 LiteLLM Proxy 可达性

        发送 GET {proxy_base_url}/health/liveliness 请求。

        Returns:
            True 如果 Proxy 活跃，False 如果不可达或异常

        注意: 此方法不抛出异常，所有异常内部捕获并返回 False。
              超时设置为 5 秒（硬编码，健康检查应快速响应）。
        """
```

---

## 3. AliasRegistry 契约

**文件**: `packages/provider/src/octoagent/provider/alias.py`

### 3.1 resolve() 方法

```python
class AliasRegistry:
    def resolve(self, alias: str) -> str:
        """将语义 alias 解析为运行时 group

        映射链: 语义 alias -> AliasConfig -> runtime_group

        Args:
            alias: 语义 alias 名称（如 "planner", "summarizer"）
                   或运行时 group 名称（如 "cheap", "main"）

        Returns:
            运行时 group 名称（对应 Proxy model_name）

        行为规则:
            1. 如果 alias 在注册表中 -> 返回对应 runtime_group
            2. 如果 alias 不在注册表中但是已知运行时 group（cheap/main/fallback）
               -> 直接返回 alias（透传）
            3. 如果都不匹配 -> 返回 "main"（安全默认值）
               并记录 warning 日志
        """
```

### 3.2 查询接口

```python
    def get_alias(self, alias: str) -> AliasConfig | None:
        """按名称查询单个 alias 配置

        Args:
            alias: alias 名称

        Returns:
            AliasConfig 或 None（不存在时）
        """

    def get_aliases_by_category(self, category: str) -> list[AliasConfig]:
        """按 category 查询 alias 列表

        Args:
            category: "cheap" | "main" | "fallback"

        Returns:
            属于该 category 的所有 alias 配置
        """

    def get_aliases_by_runtime_group(self, group: str) -> list[AliasConfig]:
        """按运行时 group 查询语义 alias 列表

        Args:
            group: "cheap" | "main" | "fallback"

        Returns:
            映射到该 group 的所有 alias 配置
        """

    def list_all(self) -> list[AliasConfig]:
        """列出所有已注册的 alias

        Returns:
            所有 AliasConfig 列表（按 name 排序）
        """
```

---

## 4. CostTracker 契约

**文件**: `packages/provider/src/octoagent/provider/cost.py`

### 4.1 calculate_cost() 方法

```python
class CostTracker:
    @staticmethod
    def calculate_cost(response) -> tuple[float, bool]:
        """从 LiteLLM 响应计算 USD 成本

        双通道策略：
        1. 主路径: litellm.completion_cost(completion_response=response)
        2. 兜底路径: response._hidden_params.get("response_cost", 0.0)
        3. 全失败: (0.0, True)

        Args:
            response: LiteLLM ModelResponse 对象

        Returns:
            (cost_usd, cost_unavailable) 元组
            - cost_usd: USD 成本，float >= 0.0
            - cost_unavailable: True 表示双通道均失败

        注意: 此方法不抛出异常，所有异常内部捕获并返回 (0.0, True)。
        """
```

### 4.2 parse_usage() 方法

```python
    @staticmethod
    def parse_usage(response) -> TokenUsage:
        """从 LiteLLM 响应解析 token 使用数据

        Args:
            response: LiteLLM ModelResponse 对象

        Returns:
            TokenUsage 实例

        行为规则:
            - 如果 response.usage 存在 -> 提取 prompt_tokens/completion_tokens/total_tokens
            - 如果 response.usage 不存在 -> 返回全零 TokenUsage
            - 不抛出异常
        """
```

### 4.3 extract_model_info() 方法

```python
    @staticmethod
    def extract_model_info(response) -> tuple[str, str]:
        """从 LiteLLM 响应提取模型和 provider 信息

        Args:
            response: LiteLLM ModelResponse 对象

        Returns:
            (model_name, provider) 元组
            - model_name: 实际模型名称，如 "gpt-4o-mini"
            - provider: provider 名称，如 "openai"

        行为规则:
            - model_name 从 response.model 提取
            - provider 从 response._hidden_params 或推断获取
            - 任一字段不可用时返回空字符串
        """
```

---

## 5. FallbackManager 契约

**文件**: `packages/provider/src/octoagent/provider/fallback.py`

### 5.1 构造器

```python
class FallbackManager:
    def __init__(
        self,
        primary: LiteLLMClient,
        fallback: EchoMessageAdapter | None = None,
    ) -> None:
        """初始化降级管理器

        Args:
            primary: 主 LLM 客户端（LiteLLMClient）
            fallback: 降级客户端（默认 EchoMessageAdapter）

        降级链:
            LiteLLMClient -> EchoMessageAdapter
            Proxy 内部的 model fallback 由 Proxy 自行处理，对本组件透明。
        """
```

### 5.2 call_with_fallback() 方法

```python
    async def call_with_fallback(
        self,
        messages: list[dict[str, str]],
        model_alias: str = "main",
        **kwargs,
    ) -> ModelCallResult:
        """带降级的 LLM 调用

        Lazy probe 策略：每次调用时先尝试 primary，失败则切换到 fallback。
        不维护显式的"降级状态"标记。

        Args:
            messages: 消息列表
            model_alias: 运行时 group 名称
            **kwargs: 传递给 primary.complete() 的额外参数

        Returns:
            ModelCallResult
            - primary 成功: is_fallback=False
            - fallback 成功: is_fallback=True, fallback_reason=<错误描述>
            - 全部失败: 抛出 ProviderError

        异常处理:
            - ProxyUnreachableError -> 触发 fallback
            - ProviderError(可恢复) -> 触发 fallback
            - 其他异常 -> 触发 fallback
            - fallback 也失败 -> 抛出 ProviderError（包含原始错误和 fallback 错误）
        """
```

---

## 6. EchoMessageAdapter 契约

**文件**: `packages/provider/src/octoagent/provider/echo_adapter.py`

```python
class EchoMessageAdapter:
    """EchoProvider 的 messages 接口适配层

    将 EchoProvider 的 call(prompt: str) 接口适配为
    complete(messages: list[dict]) -> ModelCallResult 接口。

    FallbackManager 的降级后备统一使用此适配器。
    """

    async def complete(
        self,
        messages: list[dict[str, str]],
        model_alias: str = "echo",
        **kwargs,
    ) -> ModelCallResult:
        """通过 EchoProvider 处理 messages

        行为:
            1. 从 messages 中提取最后一条 user message 的 content
            2. 调用 EchoProvider.call(content)
            3. 将 LLMResponse 转换为 ModelCallResult:
               - model_name = "echo"
               - provider = "echo"
               - cost_usd = 0.0
               - cost_unavailable = False
               - is_fallback 由调用方设置（FallbackManager 设为 True）
            4. token_usage 使用 prompt_tokens/completion_tokens/total_tokens 命名

        Args:
            messages: 消息列表
            model_alias: 模型别名（传入 EchoProvider）
            **kwargs: 忽略

        Returns:
            ModelCallResult
        """
```

---

## 7. 异常体系

**文件**: `packages/provider/src/octoagent/provider/exceptions.py`

```python
class ProviderError(Exception):
    """Provider 包基础异常"""

    def __init__(self, message: str, recoverable: bool = True) -> None:
        """
        Args:
            message: 错误描述
            recoverable: 是否可通过重试或降级恢复
        """


class ProxyUnreachableError(ProviderError):
    """LiteLLM Proxy 不可达（连接失败、超时、DNS 解析失败等）

    此异常触发 FallbackManager 的降级逻辑。
    """

    def __init__(self, proxy_url: str, original_error: Exception) -> None:
        """
        Args:
            proxy_url: 尝试连接的 Proxy 地址
            original_error: 原始异常
        """


class CostCalculationError(ProviderError):
    """成本计算失败

    此异常不中断正常流程，仅标记 cost_unavailable=True。
    """
```

---

## 8. 配置加载函数

**文件**: `packages/provider/src/octoagent/provider/config.py`

```python
def load_provider_config() -> ProviderConfig:
    """从环境变量加载 Provider 配置

    环境变量映射:
        LITELLM_PROXY_URL -> proxy_base_url (默认 "http://localhost:4000")
        LITELLM_PROXY_KEY -> proxy_api_key (默认 "")
        OCTOAGENT_LLM_MODE -> llm_mode (默认 "litellm")
        OCTOAGENT_LLM_TIMEOUT_S -> timeout_s (默认 30)

    Returns:
        ProviderConfig 实例
    """
```

---

## 9. 契约不变量

### 9.1 ModelCallResult 不变量

- `content` 不为 None（可以为空字符串）
- `duration_ms >= 0`
- `cost_usd >= 0.0`
- 如果 `cost_unavailable == True`，则 `cost_usd == 0.0`
- 如果 `is_fallback == True`，则 `fallback_reason` 非空
- `token_usage.total_tokens == token_usage.prompt_tokens + token_usage.completion_tokens`（在数据可靠时）

### 9.2 AliasRegistry 不变量

- 初始化后至少包含 6 个 MVP 默认 alias
- `resolve()` 永远返回非空字符串（不抛异常，未知 alias 降级到 "main"）
- 同一个 `name` 不会重复注册

### 9.3 FallbackManager 不变量

- `call_with_fallback()` 至少尝试一个 client（primary 或 fallback）
- 如果 primary 成功，不调用 fallback
- 如果 primary 和 fallback 均失败，抛出 `ProviderError`（不会返回无内容的 ModelCallResult）
