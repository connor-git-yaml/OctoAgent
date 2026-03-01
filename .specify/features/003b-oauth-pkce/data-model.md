# 数据模型设计: Feature 003-b -- OAuth Authorization Code + PKCE

**Feature Branch**: `feat/003b-oauth-pkce`
**Date**: 2026-03-01
**Status**: Draft
**对齐**: spec.md FR-001, FR-004, FR-010, FR-011

---

## 概览

Feature 003-b 在 Feature 003 数据模型基础上进行增量扩展，新增 OAuth Provider 配置注册表相关模型，并扩展现有 OAuthCredential。所有新增字段为可选字段，保持向后兼容。

### 变更摘要

| 模型 | 操作 | 文件 |
|------|------|------|
| OAuthProviderConfig | 新增 | `auth/oauth_provider.py` |
| OAuthProviderRegistry | 新增 | `auth/oauth_provider.py` |
| PkcePair | 新增 | `auth/pkce.py` |
| EnvironmentContext | 新增 | `auth/environment.py` |
| CallbackResult | 新增 | `auth/callback_server.py` |
| OAuthFlowResult | 新增 | `auth/oauth_flows.py` |
| OAuthEventPayload | 新增 | `auth/events.py` |
| OAuthCredential | 扩展 | `auth/credentials.py` |
| DeviceFlowConfig | 标记废弃 | `auth/oauth.py` |

---

## SS1. OAuthProviderConfig -- OAuth Provider 配置

**文件**: `packages/provider/src/octoagent/provider/auth/oauth_provider.py`
**对齐**: spec FR-004

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class OAuthProviderConfig(BaseModel):
    """Per-Provider OAuth 配置

    描述一个 LLM Provider 的 OAuth 认证参数。
    取代 Feature 003 中的 DeviceFlowConfig，统一管理所有 OAuth 流程类型。
    """

    # === 标识 ===
    provider_id: str = Field(
        description="Provider 规范 ID (canonical_id)，格式 {vendor}-{product}，如 openai-codex",
    )
    display_name: str = Field(
        description="展示名称，如 OpenAI Codex",
    )

    # === 流程类型 ===
    flow_type: Literal["auth_code_pkce", "device_flow", "device_flow_pkce"] = Field(
        description="OAuth 流程类型",
    )

    # === 端点 ===
    authorization_endpoint: str = Field(
        description="授权端点 URL",
    )
    token_endpoint: str = Field(
        description="Token 端点 URL",
    )

    # === Client 配置 ===
    client_id: str | None = Field(
        default=None,
        description="Client ID 静态值（与 client_id_env 二选一）",
    )
    client_id_env: str | None = Field(
        default=None,
        description="Client ID 环境变量名（动态获取）",
    )

    # === 请求参数 ===
    scopes: list[str] = Field(
        default_factory=list,
        description="请求的 OAuth scopes",
    )
    redirect_uri: str = Field(
        default="http://localhost:1455/auth/callback",
        description="OAuth 回调 URI",
    )
    redirect_port: int = Field(
        default=1455,
        description="本地回调服务器监听端口",
    )

    # === 能力 ===
    supports_refresh: bool = Field(
        default=True,
        description="是否支持 token 刷新",
    )

    # === 扩展 ===
    extra_auth_params: dict[str, str] = Field(
        default_factory=dict,
        description="额外的授权请求参数（如 audience 等）",
    )

    # === Device Flow 专用（flow_type 为 device_flow 时使用） ===
    poll_interval_s: int = Field(
        default=5,
        ge=1,
        description="Device Flow 轮询间隔（秒）",
    )
    timeout_s: int = Field(
        default=300,
        ge=30,
        description="OAuth 流程超时（秒）",
    )
```

### 约束

- `client_id` 和 `client_id_env` 至少有一个非 None
- `flow_type` 决定运行时使用的 OAuth 流程策略
- `redirect_uri` 和 `redirect_port` 仅在 `auth_code_pkce` 流程中使用

---

## SS2. OAuthProviderRegistry -- Provider 注册表

**文件**: `packages/provider/src/octoagent/provider/auth/oauth_provider.py`
**对齐**: spec FR-004

```python
import os
from ..exceptions import OAuthFlowError


class OAuthProviderRegistry:
    """OAuth Provider 注册表 -- 管理多 Provider 的 OAuth 配置

    内置 OpenAI Codex 和 GitHub Copilot 的默认配置。
    支持通过 register() 方法运行时注册新 Provider。
    """

    def __init__(self) -> None:
        self._providers: dict[str, OAuthProviderConfig] = {}
        self._register_builtins()

    def _register_builtins(self) -> None:
        """注册内置 Provider 配置"""
        for config in BUILTIN_PROVIDERS.values():
            self._providers[config.provider_id] = config

    def register(self, config: OAuthProviderConfig) -> None:
        """注册新 Provider 配置"""
        self._providers[config.provider_id] = config

    def get(self, provider_id: str) -> OAuthProviderConfig | None:
        """按 canonical_id 获取 Provider 配置"""
        return self._providers.get(provider_id)

    def list_providers(self) -> list[OAuthProviderConfig]:
        """列出所有已注册的 Provider 配置"""
        return list(self._providers.values())

    def list_oauth_providers(self) -> list[OAuthProviderConfig]:
        """列出所有支持 OAuth 的 Provider（排除纯 API Key 模式）"""
        return [p for p in self._providers.values()]

    def resolve_client_id(self, config: OAuthProviderConfig) -> str:
        """解析 Provider 的 Client ID

        优先使用静态值，其次从环境变量获取。

        Raises:
            OAuthFlowError: 无法解析 Client ID
        """
        if config.client_id:
            return config.client_id
        if config.client_id_env:
            value = os.environ.get(config.client_id_env)
            if value:
                return value
        raise OAuthFlowError(
            f"无法获取 {config.display_name} 的 Client ID。"
            f"请设置环境变量 {config.client_id_env or 'CLIENT_ID'} 或使用 API Key 模式。",
            provider=config.provider_id,
        )
```

### 内置 Provider 配置

```python
BUILTIN_PROVIDERS: dict[str, OAuthProviderConfig] = {
    "openai-codex": OAuthProviderConfig(
        provider_id="openai-codex",
        display_name="OpenAI Codex",
        flow_type="auth_code_pkce",
        authorization_endpoint="https://auth.openai.com/oauth/authorize",
        token_endpoint="https://auth.openai.com/oauth/token",
        client_id_env="OCTOAGENT_CODEX_CLIENT_ID",
        scopes=["openid", "profile", "email", "offline_access"],
        redirect_uri="http://localhost:1455/auth/callback",
        redirect_port=1455,
        supports_refresh=True,
        extra_auth_params={"audience": "https://api.openai.com/v1"},
    ),
    "github-copilot": OAuthProviderConfig(
        provider_id="github-copilot",
        display_name="GitHub Copilot",
        flow_type="device_flow",
        authorization_endpoint="https://github.com/login/device/code",
        token_endpoint="https://github.com/login/oauth/access_token",
        client_id="Iv1.b507a08c87ecfe98",
        scopes=["read:user"],
        supports_refresh=False,
    ),
}
```

### display_id -> canonical_id 映射表

```python
# init_wizard 使用此映射表将 UI 展示 ID 转换为注册表 canonical_id
DISPLAY_TO_CANONICAL: dict[str, str] = {
    "openai": "openai-codex",
    "github": "github-copilot",
}
```

---

## SS3. PkcePair -- PKCE 密钥对

**文件**: `packages/provider/src/octoagent/provider/auth/pkce.py`
**对齐**: spec FR-001

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PkcePair:
    """PKCE 密钥对 -- 临时值，仅在 OAuth 流程期间存在于内存中

    code_verifier: 43-128 字符，256 bit 熵
    code_challenge: SHA256(code_verifier) 的 base64url 编码
    """

    code_verifier: str
    code_challenge: str
```

**设计说明**: 使用 `dataclass(frozen=True)` 而非 Pydantic BaseModel，因为 PkcePair 是纯内存临时值，不需要序列化/反序列化能力。`frozen=True` 防止意外修改。

### 生成函数

```python
import secrets
import hashlib
import base64


def generate_pkce() -> PkcePair:
    """生成 PKCE code_verifier 和 code_challenge (S256)

    符合 RFC 7636:
    - code_verifier: 43 字符, 256 bit 熵 (secrets.token_urlsafe(32))
    - code_challenge: SHA256(verifier) -> base64url 编码（无 padding）
    - code_challenge_method: S256
    """
    verifier = secrets.token_urlsafe(32)  # 43 chars
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return PkcePair(code_verifier=verifier, code_challenge=challenge)


def generate_state() -> str:
    """生成独立的 OAuth state 参数（CSRF 防护）

    使用独立随机值，不复用 code_verifier。
    """
    return secrets.token_urlsafe(32)
```

---

## SS4. EnvironmentContext -- 环境上下文

**文件**: `packages/provider/src/octoagent/provider/auth/environment.py`
**对齐**: spec FR-002

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EnvironmentContext:
    """运行环境上下文 -- 描述当前环境的 OAuth 交互能力

    用于决定 OAuth 流程的交互模式（自动打开浏览器 vs 手动粘贴 URL）。
    """

    is_remote: bool           # 是否为远程环境（SSH/容器/云开发）
    can_open_browser: bool    # 是否可以自动打开浏览器
    force_manual: bool        # 是否强制手动模式（--manual-oauth flag）
    detection_details: str    # 检测详情（用于日志/诊断）

    @property
    def use_manual_mode(self) -> bool:
        """是否应使用手动粘贴模式"""
        return self.force_manual or self.is_remote or not self.can_open_browser
```

### 检测函数

```python
import os
import sys


def detect_environment(force_manual: bool = False) -> EnvironmentContext:
    """检测当前运行环境

    检测维度:
    1. SSH 环境（SSH_CLIENT, SSH_TTY, SSH_CONNECTION）
    2. 容器/云开发环境（REMOTE_CONTAINERS, CODESPACES, CLOUD_SHELL）
    3. Linux 无图形界面（无 DISPLAY 和 WAYLAND_DISPLAY，且非 WSL）

    Args:
        force_manual: 是否强制手动模式（--manual-oauth CLI flag）
    """
```

---

## SS5. CallbackResult -- 回调服务器结果

**文件**: `packages/provider/src/octoagent/provider/auth/callback_server.py`
**对齐**: spec FR-003

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CallbackResult:
    """OAuth 回调结果"""

    code: str     # 授权码
    state: str    # state 参数（用于 CSRF 验证）
```

---

## SS6. OAuthFlowResult -- OAuth 流程结果

**文件**: `packages/provider/src/octoagent/provider/auth/oauth_flows.py`
**对齐**: spec FR-005

```python
from pydantic import BaseModel, Field, SecretStr
from datetime import datetime


class OAuthTokenResponse(BaseModel):
    """OAuth Token 端点响应"""

    access_token: SecretStr = Field(description="访问令牌")
    refresh_token: SecretStr = Field(
        default=SecretStr(""),
        description="刷新令牌",
    )
    token_type: str = Field(default="Bearer", description="Token 类型")
    expires_in: int = Field(default=3600, description="过期时间（秒）")
    scope: str = Field(default="", description="授予的 scopes")
    account_id: str | None = Field(
        default=None,
        description="账户 ID（从响应 JSON 提取，若无则为 None）",
    )
```

---

## SS7. OAuthCredential 扩展 -- 新增 account_id

**文件**: `packages/provider/src/octoagent/provider/auth/credentials.py`
**对齐**: spec FR-010

```python
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
```

### 向后兼容性

- `account_id` 字段使用 `default=None`，已有的 OAuthCredential 实例自动获得 `account_id=None`
- CredentialStore 中的现有数据（Feature 003 存储的 Device Flow 凭证）无需迁移即可正常加载
- Discriminated Union (`Credential` type alias) 无需修改

---

## SS8. OAuth 事件类型扩展

**文件**: `packages/provider/src/octoagent/provider/auth/events.py` (扩展)
**对齐**: spec FR-012

需要在 `octoagent.core.models.enums.EventType` 中新增以下枚举值：

```python
class EventType(str, Enum):
    # ... 现有值 ...

    # --- 003-b 新增 OAuth 事件 ---
    OAUTH_STARTED = "OAUTH_STARTED"
    OAUTH_SUCCEEDED = "OAUTH_SUCCEEDED"
    OAUTH_FAILED = "OAUTH_FAILED"
    OAUTH_REFRESHED = "OAUTH_REFRESHED"
```

### OAuth 事件 Payload 约定

所有 OAuth 事件的 payload 中 **MUST NOT** 包含以下字段的明文值：
- `access_token`
- `refresh_token`
- `code_verifier`
- `state`

**OAUTH_STARTED payload**:
```python
{
    "provider_id": str,         # canonical_id
    "flow_type": str,           # "auth_code_pkce" | "device_flow"
    "environment_mode": str,    # "auto" | "manual"
}
```

**OAUTH_SUCCEEDED payload**:
```python
{
    "provider_id": str,
    "token_type": str,          # "Bearer"
    "expires_in": int,          # 秒
    "has_refresh_token": bool,  # 是否包含 refresh_token
    "has_account_id": bool,     # 是否包含 account_id
}
```

**OAUTH_FAILED payload**:
```python
{
    "provider_id": str,
    "failure_reason": str,      # 错误描述
    "failure_stage": str,       # "authorization" | "callback" | "token_exchange" | "state_validation"
}
```

**OAUTH_REFRESHED payload**:
```python
{
    "provider_id": str,
    "new_expires_in": int,      # 新 token 的过期时间（秒）
}
```

---

## SS9. DeviceFlowConfig 废弃标记

**文件**: `packages/provider/src/octoagent/provider/auth/oauth.py`
**对齐**: spec FR-011, research D7

```python
import warnings


class DeviceFlowConfig(BaseModel):
    """Device Flow 配置

    .. deprecated:: 003-b
        使用 OAuthProviderConfig 替代。
        现有 Device Flow 逻辑通过 OAuthProviderConfig.to_device_flow_config() 获取兼容配置。
    """
    # ... 现有字段不变 ...
```

### 兼容转换

```python
class OAuthProviderConfig(BaseModel):
    # ... 已有字段 ...

    def to_device_flow_config(self) -> "DeviceFlowConfig":
        """转换为 DeviceFlowConfig（向后兼容）

        仅在 flow_type 为 device_flow 或 device_flow_pkce 时使用。
        """
        from .oauth import DeviceFlowConfig
        return DeviceFlowConfig(
            authorization_endpoint=self.authorization_endpoint,
            token_endpoint=self.token_endpoint,
            client_id=self.resolve_client_id_value(),  # 需要先解析
            scope=" ".join(self.scopes),
            poll_interval_s=self.poll_interval_s,
            timeout_s=self.timeout_s,
        )
```

---

## SS10. HandlerChainResult 路由覆盖扩展

**文件**: `packages/provider/src/octoagent/provider/auth/chain.py`
**对齐**: 多认证路由隔离（JWT 直连 vs API Key 走 Proxy）

```python
class HandlerChainResult(BaseModel):
    """认证解析结果 -- Handler Chain 返回值

    003-b 扩展: 新增路由覆盖字段，支持 JWT OAuth 路径绕过 LiteLLM Proxy。
    """

    provider: str
    api_key: str
    model_name: str | None = None
    # --- 003-b 路由隔离新增 ---
    api_base_url: str | None = Field(
        default=None,
        description="路由覆盖: JWT 路径填充 chatgpt.com/backend-api，API Key 路径为 None（走默认 Proxy）",
    )
    extra_headers: dict[str, str] = Field(
        default_factory=dict,
        description="额外请求头: JWT 路径携带 Authorization: Bearer {jwt}，API Key 路径为空 dict",
    )
```

### 路由隔离策略

| 认证路径 | api_base_url | extra_headers | 说明 |
|---------|-------------|---------------|------|
| API Key（走 Proxy） | `None` | `{}` | 使用 LiteLLM Proxy 默认路由 |
| JWT OAuth（直连） | `https://chatgpt.com/backend-api` | `{"Authorization": "Bearer {jwt}"}` | 绕过 Proxy 直连 ChatGPT Backend API |

### HandlerChain._extract_routing()

```python
def _extract_routing(self, adapter: AuthAdapter) -> tuple[str | None, dict[str, str]]:
    """从适配器提取路由覆盖信息

    仅 PkceOAuthAdapter 实现了 get_api_base_url() / get_extra_headers()，
    其他适配器返回 (None, {})。
    """
```

---

## SS11. ReasoningConfig 模型

**文件**: `packages/provider/src/octoagent/provider/models.py`
**对齐**: Codex Reasoning/Thinking 模式配置

```python
from typing import Literal
from pydantic import BaseModel, Field

ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh"]
ReasoningSummary = Literal["auto", "concise", "detailed"]


class ReasoningConfig(BaseModel):
    """Codex 推理/思考模式配置

    支持双路径适配:
    - Responses API: 使用 to_responses_api_param() 生成嵌套 reasoning 对象
    - Chat Completions API (LiteLLM): 使用 effort 字段作为顶层 reasoning_effort 字符串
    """

    effort: ReasoningEffort = Field(
        default="medium",
        description="推理深度级别: none / low / medium / high / xhigh",
    )
    summary: ReasoningSummary | None = Field(
        default=None,
        description="推理摘要模式（仅 Responses API）: auto / concise / detailed；None 表示不传",
    )

    def to_responses_api_param(self) -> dict:
        """转换为 Responses API reasoning 参数"""
        param: dict = {"effort": self.effort}
        if self.summary is not None:
            param["summary"] = self.summary
        return param
```

### 双路径适配

| API 路径 | 传参方式 | 使用场景 |
|---------|---------|---------|
| Responses API | `{"reasoning": {"effort": "high", "summary": "auto"}}` | E2E 脚本直连 ChatGPT Backend API |
| Chat Completions API | `reasoning_effort="high"` (顶层字符串) | LiteLLMClient.complete() 经 LiteLLM SDK |

---

## 实体关系图

```mermaid
erDiagram
    OAuthProviderRegistry ||--o{ OAuthProviderConfig : "manages"
    OAuthProviderConfig ||--o| PkcePair : "generates (runtime)"
    OAuthProviderConfig ||--o| EnvironmentContext : "determines mode"
    OAuthProviderConfig }|--|| OAuthCredential : "produces"
    OAuthCredential }|--|| ProviderProfile : "stored in"
    ProviderProfile }|--|| CredentialStoreData : "contained in"
    OAuthProviderConfig ..> DeviceFlowConfig : "replaces (deprecated)"
    HandlerChainResult ||--o| ReasoningConfig : "paired with (runtime)"
    LiteLLMClient ..> ReasoningConfig : "accepts (optional)"
    LiteLLMClient ..> HandlerChainResult : "uses routing overrides"
```
