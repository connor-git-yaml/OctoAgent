"""凭证格式校验 -- 对齐 contracts/auth-adapter-api.md SS5, FR-003, FR-004

提供 API Key 和 Setup Token 的前缀格式校验。
"""

from __future__ import annotations

# 各 Provider 的 API Key 前缀映射
_API_KEY_PREFIXES: dict[str, str] = {
    "openai": "sk-",
    "openrouter": "sk-or-",
    "anthropic": "sk-ant-api",
}

# Anthropic Setup Token 固定前缀
_SETUP_TOKEN_PREFIX = "sk-ant-oat01-"


def validate_api_key(key: str, provider: str) -> bool:
    """校验 API Key 格式

    校验规则:
    - 非空
    - OpenAI: 以 "sk-" 开头
    - OpenRouter: 以 "sk-or-" 开头
    - Anthropic: 以 "sk-ant-api" 开头
    - 其他 Provider: 非空即可

    Args:
        key: API Key 值
        provider: Provider 标识

    Returns:
        True 表示格式有效
    """
    if not key or not key.strip():
        return False
    prefix = _API_KEY_PREFIXES.get(provider.lower())
    if prefix is None:
        # 未知 Provider，非空即可
        return True
    return key.startswith(prefix)


def validate_setup_token(token: str) -> bool:
    """校验 Anthropic Setup Token 格式

    校验规则:
    - 非空
    - 以 "sk-ant-oat01-" 开头

    Args:
        token: Setup Token 值

    Returns:
        True 表示格式有效
    """
    if not token or not token.strip():
        return False
    return token.startswith(_SETUP_TOKEN_PREFIX)
