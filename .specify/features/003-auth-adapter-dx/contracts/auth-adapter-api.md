# Auth Adapter API 契约

**Feature**: 003 - Auth Adapter + DX 工具
**Date**: 2026-03-01
**Status**: Draft
**对齐**: Blueprint SS8.9.4 + spec FR-001 ~ FR-006, FR-010 ~ FR-013

---

## SS1. AuthAdapter 抽象接口

### 1.1 接口定义

```python
# packages/provider/src/octoagent/provider/auth/adapter.py

from abc import ABC, abstractmethod


class AuthAdapter(ABC):
    """认证适配器抽象基类"""

    @abstractmethod
    async def resolve(self) -> str:
        """解析当前可用的凭证值

        Returns:
            可直接用于 API 调用的凭证字符串（API key / access token）

        Raises:
            CredentialNotFoundError: 无可用凭证
            CredentialExpiredError: 凭证已过期
        """

    @abstractmethod
    async def refresh(self) -> str | None:
        """刷新过期凭证

        Returns:
            刷新后的凭证字符串；不支持刷新时返回 None

        Raises:
            OAuthFlowError: OAuth 刷新失败
        """

    @abstractmethod
    def is_expired(self) -> bool:
        """检查凭证是否已过期

        Returns:
            True 表示已过期或即将过期
        """
```

### 1.2 具体 Adapter 签名

```python
# ApiKeyAuthAdapter -- FR-003
class ApiKeyAuthAdapter(AuthAdapter):
    def __init__(self, credential: ApiKeyCredential) -> None: ...
    async def resolve(self) -> str: ...       # 返回 key.get_secret_value()
    async def refresh(self) -> str | None: ... # 永远返回 None
    def is_expired(self) -> bool: ...          # 永远返回 False

# SetupTokenAuthAdapter -- FR-004
class SetupTokenAuthAdapter(AuthAdapter):
    def __init__(self, credential: TokenCredential, ttl_hours: int = 24) -> None: ...
    async def resolve(self) -> str: ...       # 检查过期后返回 token
    async def refresh(self) -> str | None: ... # 返回 None（不支持自动刷新）
    def is_expired(self) -> bool: ...          # 基于 acquired_at + TTL 计算

# CodexOAuthAdapter -- FR-005 (Device Flow, 已废弃)
class CodexOAuthAdapter(AuthAdapter):
    def __init__(self, credential: OAuthCredential) -> None: ...
    async def resolve(self) -> str: ...       # 返回 access_token
    async def refresh(self) -> str | None: ... # 返回 None（不支持刷新）
    def is_expired(self) -> bool: ...          # 基于 expires_at 判断

# PkceOAuthAdapter -- FR-006 (003-b: Auth Code + PKCE, 推荐)
class PkceOAuthAdapter(AuthAdapter):
    def __init__(
        self,
        credential: OAuthCredential,
        provider_config: OAuthProviderConfig,  # 注入 Provider 配置
        store: CredentialStore,                # 注入 CredentialStore（刷新后回写）
        profile_name: str,                     # 对应的 profile 名称
        event_store: EventStoreProtocol | None = None,  # 可选事件存储
    ) -> None: ...
    async def resolve(self) -> str: ...       # 检测过期 -> 自动刷新 -> 返回 access_token
    async def refresh(self) -> str | None: ... # httpx POST token 端点 (grant_type=refresh_token)
                                               # -> 更新内存凭证 + CredentialStore 回写
                                               # -> 发射 OAUTH_REFRESHED 事件
                                               # -> 返回新 access_token
                                               # invalid_grant: 清除凭证返回 None
    def is_expired(self) -> bool: ...          # 基于 expires_at 判断
```

> **003-b 说明**: `PkceOAuthAdapter` 是推荐的 OAuth Adapter，支持自动 token 刷新。
> `CodexOAuthAdapter` 仍保留以兼容旧 Device Flow 凭证，但新流程应使用 `PkceOAuthAdapter`。
> `PkceOAuthAdapter.refresh()` 通过构造注入的 `CredentialStore` 实现刷新后自动回写，
> 确保凭证持久化与内存状态一致。

---

## SS2. Credential Store API

```python
# packages/provider/src/octoagent/provider/auth/store.py

class CredentialStore:
    """凭证存储管理器

    文件位置: ~/.octoagent/auth-profiles.json
    文件权限: 0o600（仅当前用户可读写）
    并发安全: filelock
    """

    def __init__(self, store_path: Path | None = None) -> None:
        """初始化

        Args:
            store_path: 存储文件路径，None 时使用默认路径
        """

    def load(self) -> CredentialStoreData:
        """加载 credential store

        Returns:
            CredentialStoreData 实例

        Raises:
            CredentialError: 文件损坏（备份原文件并返回空 store）
        """

    def save(self, data: CredentialStoreData) -> None:
        """持久化 credential store

        使用 filelock 保证并发安全。
        写入时先写临时文件再 rename（原子性）。
        自动设置文件权限为 0o600。
        """

    def get_profile(self, name: str) -> ProviderProfile | None:
        """按名称获取 profile"""

    def set_profile(self, profile: ProviderProfile) -> None:
        """创建或更新 profile"""

    def remove_profile(self, name: str) -> bool:
        """删除 profile，返回是否成功"""

    def get_default_profile(self) -> ProviderProfile | None:
        """获取默认 profile"""

    def list_profiles(self) -> list[ProviderProfile]:
        """列出所有 profile"""
```

---

## SS3. Handler Chain API

```python
# packages/provider/src/octoagent/provider/auth/chain.py

class HandlerChain:
    """处理器链 -- 按优先级解析凭证

    解析优先级: 显式 profile > credential store > 环境变量 > 默认值
    """

    def __init__(
        self,
        store: CredentialStore,
        env_prefix: str = "",
    ) -> None:
        """初始化

        Args:
            store: Credential Store 实例
            env_prefix: 环境变量前缀（如空字符串则使用标准命名）
        """

    def register_adapter_factory(
        self,
        provider: str,
        factory: Callable[[Credential], AuthAdapter],
    ) -> None:
        """注册 adapter 工厂函数

        Args:
            provider: Provider 标识
            factory: 给定 Credential 返回对应 AuthAdapter 的工厂函数
        """

    async def resolve(
        self,
        provider: str | None = None,
        profile_name: str | None = None,
    ) -> HandlerChainResult:
        """解析凭证

        解析链:
        1. 如果指定 profile_name -> 从 store 获取指定 profile
        2. 如果指定 provider -> 从 store 获取该 provider 的默认 profile
        3. 尝试从环境变量解析（如 OPENAI_API_KEY、OPENROUTER_API_KEY）
        4. 返回默认值或抛出 CredentialNotFoundError

        Args:
            provider: 目标 Provider 标识
            profile_name: 显式指定的 profile 名称

        Returns:
            HandlerChainResult

        Raises:
            CredentialNotFoundError: 所有来源均无有效凭证
        """
```

---

## SS4. 凭证脱敏 API

```python
# packages/provider/src/octoagent/provider/auth/masking.py

def mask_secret(value: str, prefix_len: int = 10, suffix_len: int = 3) -> str:
    """脱敏凭证值

    规则:
    - 长度 <= prefix_len + suffix_len: 返回 "***"
    - 否则: 保留前 prefix_len 字符 + "***" + 末尾 suffix_len 字符

    示例:
    - "sk-or-v1-abc123xyz" -> "sk-or-v1-a***xyz"
    - "sk-ant-oat01-longtoken" -> "sk-ant-oat***ken"
    - "short" -> "***"

    Args:
        value: 原始凭证值
        prefix_len: 保留前缀长度
        suffix_len: 保留后缀长度

    Returns:
        脱敏后的字符串
    """
```

---

## SS5. 凭证格式校验 API

```python
# packages/provider/src/octoagent/provider/auth/validators.py

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
```

---

## SS6. 凭证事件发射 API

```python
# packages/provider/src/octoagent/provider/auth/events.py

async def emit_credential_event(
    event_store,       # EventStore 实例（Protocol 类型）
    event_type: EventType,
    provider: str,
    credential_type: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """发射凭证生命周期事件到 Event Store

    注意: payload 中不包含凭证值本身。

    Args:
        event_store: Event Store 实例
        event_type: 事件类型（CREDENTIAL_LOADED / CREDENTIAL_EXPIRED / CREDENTIAL_FAILED）
        provider: Provider 标识
        credential_type: 凭证类型（api_key / token / oauth）
        extra: 额外元信息
    """
```

---

## SS7. Codex OAuth Device Flow API

```python
# packages/provider/src/octoagent/provider/auth/oauth.py

class DeviceFlowConfig(BaseModel):
    """Device Flow 配置"""
    authorization_endpoint: str = Field(
        default="https://auth0.openai.com/oauth/device/code",
        description="设备授权端点"
    )
    token_endpoint: str = Field(
        default="https://auth0.openai.com/oauth/token",
        description="Token 端点"
    )
    client_id: str = Field(description="OAuth Client ID")
    scope: str = Field(default="openid profile email offline_access", description="请求 scope")
    poll_interval_s: int = Field(default=5, ge=1, description="轮询间隔（秒）")
    timeout_s: int = Field(default=300, ge=30, description="授权超时（秒）")

class DeviceAuthResponse(BaseModel):
    """设备授权响应"""
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    expires_in: int
    interval: int

async def start_device_flow(config: DeviceFlowConfig) -> DeviceAuthResponse:
    """发起 Device Flow 授权请求

    Returns:
        DeviceAuthResponse，包含 user_code 和 verification_uri

    Raises:
        OAuthFlowError: 授权端点不可达
    """

async def poll_for_token(
    config: DeviceFlowConfig,
    device_code: str,
    interval: int = 5,
    timeout: int = 300,
) -> OAuthCredential:
    """轮询 Token 端点等待用户授权

    Returns:
        OAuthCredential，包含 access_token 和 expires_at

    Raises:
        OAuthFlowError: 授权超时或被拒绝
    """
```
