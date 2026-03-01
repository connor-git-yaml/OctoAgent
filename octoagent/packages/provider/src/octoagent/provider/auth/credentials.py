"""凭证类型体系 -- 对齐 data-model.md SS1, FR-001

三种凭证类型通过 Discriminated Union 区分：
- ApiKeyCredential: 标准 API Key（永不过期）
- TokenCredential: 临时 Token（Anthropic Setup Token，有过期时间）
- OAuthCredential: OAuth 凭证（Codex Device Flow）
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Discriminator, Field, SecretStr


class ApiKeyCredential(BaseModel):
    """API Key 凭证 -- 标准 Provider 密钥

    适用 Provider: OpenAI、OpenRouter、Anthropic（标准模式）
    特征: 永不过期（除非用户主动吊销）
    """

    type: Literal["api_key"] = "api_key"
    provider: str = Field(description="Provider 标识（如 openai、openrouter、anthropic）")
    key: SecretStr = Field(description="API Key 值")


class TokenCredential(BaseModel):
    """Token 凭证 -- 带过期时间的临时令牌

    适用 Provider: Anthropic Setup Token（sk-ant-oat01-* 格式）
    特征: 有过期时间，需要过期检测
    """

    type: Literal["token"] = "token"
    provider: str = Field(description="Provider 标识")
    token: SecretStr = Field(description="Token 值")
    acquired_at: datetime = Field(description="Token 获取时间")
    expires_at: datetime | None = Field(
        default=None,
        description="过期时间（基于 acquired_at + TTL 计算）",
    )


class OAuthCredential(BaseModel):
    """OAuth 凭证 -- 访问令牌 + 刷新令牌

    适用 Provider: OpenAI Codex（Device Flow / Auth Code + PKCE）
    特征: 有过期时间，支持 token 刷新

    003-b 扩展: 新增 account_id 可选字段
    """

    type: Literal["oauth"] = "oauth"
    provider: str = Field(description="Provider 标识 (canonical_id)")
    access_token: SecretStr = Field(description="访问令牌")
    refresh_token: SecretStr = Field(
        default=SecretStr(""),
        description="刷新令牌（部分 Provider 可能不提供）",
    )
    expires_at: datetime = Field(description="访问令牌过期时间")
    # --- 003-b 新增 ---
    account_id: str | None = Field(
        default=None,
        description="账户 ID（从 token 端点响应提取，可选）",
    )


# Discriminated Union -- 通过 type 字段自动区分凭证类型
Credential = Annotated[
    ApiKeyCredential | TokenCredential | OAuthCredential,
    Discriminator("type"),
]
