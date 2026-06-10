"""F105: ConversationBinding 领域模型（OC-2）。

(platform, account_id, conversation_id) → scope/project 的渠道会话路由绑定，
带 last_active_at（OC-6 last-route 出站解析的状态底座）。

H1 不变量（docs/blueprint/agent-collaboration-philosophy.md）：
所有平台 binding 默认收敛唯一主 Agent——``agent_profile_id`` 默认空字符串
即"主 Agent"；v0.1 运行时写入面不暴露该字段（构造性保证，spec D5），
字段本身保留供 v0.2 显式配置面（届时绑定写入单点做 H1 校验）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class ConversationBindingKind(StrEnum):
    """绑定来源（OC-2 configured/runtime 二分）。

    - CONFIGURED: 用户显式配置的绑定（v0.2 配置面写入）
    - RUNTIME: inbound 消息自动登记的绑定（v0.1 唯一写入路径）
    """

    CONFIGURED = "configured"
    RUNTIME = "runtime"


class ConversationBinding(BaseModel):
    """渠道会话路由绑定。"""

    binding_id: str = Field(min_length=1)
    platform: str = Field(min_length=1, description="平台标识（如 telegram / web）")
    account_id: str = Field(
        default="default",
        min_length=1,
        description="平台账号标识（OC-7 字段位预留，v0.1 恒 'default'）",
    )
    conversation_id: str = Field(
        min_length=1,
        description="平台会话标识（telegram chat_id / web thread_id）",
    )
    scope_id: str = Field(default="", description="关联的 scope_id")
    project_id: str = Field(default="", description="project 维度（H1：区分仅在 project）")
    agent_profile_id: str = Field(
        default="",
        description="''=主 Agent（H1 默认）；v0.1 写入面不暴露此字段",
    )
    binding_kind: ConversationBindingKind = ConversationBindingKind.RUNTIME
    last_active_at: datetime = Field(
        default_factory=_utc_now,
        description="最近 inbound 活跃时间（OC-6 last-route）",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
