"""Gateway 向 Provider 注入的最小、secret-safe 路由 DTO。"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProviderAuthRoute(BaseModel):
    """只保存凭证引用，不保存凭证值。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["api_key", "oauth"]
    env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    profile: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]*$")

    @model_validator(mode="after")
    def validate_reference(self) -> ProviderAuthRoute:
        if self.kind == "api_key" and self.env and self.profile is None:
            return self
        if self.kind == "oauth" and self.profile and self.env is None:
            return self
        raise ValueError("auth route must contain exactly one canonical reference")


class ProviderRoute(BaseModel):
    """ProviderRouter 消费的完整、不可变路由事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    alias: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    transport: Literal["openai_chat", "openai_responses", "anthropic_messages"]
    api_base: str = Field(min_length=1)
    auth: ProviderAuthRoute

    @model_validator(mode="after")
    def validate_api_base(self) -> ProviderRoute:
        value = self.api_base
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("api_base contains control characters")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("api_base must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("api_base must not contain userinfo")
        if parsed.query or parsed.fragment:
            raise ValueError("api_base must not contain query or fragment")
        return self


__all__ = ["ProviderAuthRoute", "ProviderRoute"]
