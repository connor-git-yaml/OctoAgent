"""Provider Profile 模型 -- 对齐 data-model.md SS2, SS3

ProviderProfile: 一个完整的 Provider 连接配置（元数据 + 凭证引用）。
CredentialStoreData: 持久化容器模型，对应 auth-profiles.json 文件内容。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .credentials import Credential


class ProviderProfile(BaseModel):
    """Provider 配置档 -- 关联的配置元数据和凭证

    一个 credential store 可包含多个 profile。
    """

    name: str = Field(description="Profile 名称（唯一标识）")
    provider: str = Field(description="Provider 标识")
    auth_mode: Literal["api_key", "token", "oauth"] = Field(
        description="认证模式",
    )
    credential: Credential = Field(description="关联的凭证")
    is_default: bool = Field(default=False, description="是否为默认 profile")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="最近更新时间")


class CredentialStoreData(BaseModel):
    """Credential Store 持久化数据结构

    对应 ~/.octoagent/auth-profiles.json 文件内容。
    """

    version: int = Field(default=1, description="Schema 版本号")
    profiles: dict[str, ProviderProfile] = Field(
        default_factory=dict,
        description="Profile 名称 -> Profile 映射",
    )
