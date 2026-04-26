"""Feature 080 Phase 1：单 provider 的运行时配置描述符。

把 ``octoagent.yaml.providers[]`` 的声明性配置和 live 的 ``AuthResolver`` 绑定
在一个 frozen dataclass 里，作为 ``ProviderClient`` 的输入。

设计要点：
- frozen=True：runtime 一旦构造就不可变；token refresh 由 ``AuthResolver`` 内部
  管理，不需要重建 runtime
- 每次 LLM 调用都从 ``AuthResolver.resolve()`` 拿现役凭证，不在 runtime 缓存
- ``provider_id`` 与 ``octoagent.yaml.providers[].id`` 严格对齐
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .auth_resolver import AuthResolver
from .transport import ProviderTransport


@dataclass(frozen=True)
class ProviderRuntime:
    """运行时单 provider 描述符。

    ``ProviderRouter.resolve_for_alias()`` 内部按 ``ProviderEntry`` 构造此对象，
    交给 ``ProviderClient.call()`` 执行实际 HTTP 请求。
    """

    provider_id: str
    """与 ``octoagent.yaml.providers[].id`` 对齐，如 ``openai-codex``。"""

    transport: ProviderTransport

    api_base: str
    """Provider HTTP 基础 URL（不带尾部 ``/``），例如：
    - ``https://api.siliconflow.cn``
    - ``https://chatgpt.com/backend-api/codex``
    - ``https://api.anthropic.com``
    """

    auth_resolver: AuthResolver
    """凭证解析器；每次 LLM 调用前 ``resolve()``。"""

    extra_headers: dict[str, str] = field(default_factory=dict)
    """与 ``ResolvedAuth.extra_headers`` 合并的静态头部，比如：
    - ``OpenAI-Beta: responses=experimental``
    - ``anthropic-version: 2023-06-01``
    - ``originator: pi``
    """

    extra_body: dict[str, Any] = field(default_factory=dict)
    """每次请求 body 自动 merge 的字段，比如：
    - ``store: false``（OpenAI Responses API 默认不留存到 chat history）
    - ``stream: true``
    """

    timeout_s: float = 60.0
    """单次请求超时（秒）。流式响应的总耗时不受此限制（httpx connect timeout 用 10s）。"""


__all__ = ["ProviderRuntime"]
