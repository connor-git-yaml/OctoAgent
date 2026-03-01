# Auth OAuth PKCE API 契约

**Feature**: 003-b -- OAuth Authorization Code + PKCE + Per-Provider Auth
**Date**: 2026-03-01
**Status**: Draft
**对齐**: spec FR-001 ~ FR-012
**前序契约**: `003-auth-adapter-dx/contracts/auth-adapter-api.md`（SS1 AuthAdapter 接口保持不变）

---

## SS1. PKCE 生成 API

**文件**: `packages/provider/src/octoagent/provider/auth/pkce.py`
**对齐**: spec FR-001

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PkcePair:
    """PKCE 密钥对（临时值，仅内存中存在）"""
    code_verifier: str   # 43-128 字符, 256 bit 熵
    code_challenge: str  # SHA256(verifier) -> base64url 编码


def generate_pkce() -> PkcePair:
    """生成 PKCE code_verifier 和 code_challenge (S256)

    实现要求:
    - code_verifier: secrets.token_urlsafe(32), 生成 43 字符
    - code_challenge: SHA256(verifier) -> base64url 编码（无 padding）
    - code_challenge_method 始终为 "S256"

    安全约束:
    - PkcePair 实例 MUST NOT 被序列化、持久化或写入日志
    - 使用后应尽快释放引用

    Returns:
        PkcePair 实例
    """


def generate_state() -> str:
    """生成 OAuth state 参数（CSRF 防护）

    实现要求:
    - 使用 secrets.token_urlsafe(32) 生成独立随机值
    - 不复用 code_verifier
    - 与 OAuth 流程生命周期绑定，超时后自动失效

    Returns:
        32 字节的 URL-safe base64 编码字符串
    """
```

### 约束

- `code_verifier` 和 `state` 生成后 **MUST NOT** 出现在：日志输出、Event Store payload、任何持久化存储
- `PkcePair` 使用 `frozen=True` 防止意外修改
- 本模块不依赖任何外部库（仅 Python 标准库）

---

## SS2. OAuth Provider 配置 API

**文件**: `packages/provider/src/octoagent/provider/auth/oauth_provider.py`
**对齐**: spec FR-004, FR-011

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class OAuthProviderConfig(BaseModel):
    """Per-Provider OAuth 配置

    统一管理所有 OAuth 流程类型的 Provider 配置。
    取代 Feature 003 中的 DeviceFlowConfig。
    """

    # 标识
    provider_id: str = Field(description="规范 ID (canonical_id)")
    display_name: str = Field(description="展示名称")

    # 流程
    flow_type: Literal["auth_code_pkce", "device_flow", "device_flow_pkce"]

    # 端点
    authorization_endpoint: str
    token_endpoint: str

    # Client
    client_id: str | None = Field(default=None)
    client_id_env: str | None = Field(default=None)

    # 请求参数
    scopes: list[str] = Field(default_factory=list)
    redirect_uri: str = Field(default="http://localhost:1455/auth/callback")
    redirect_port: int = Field(default=1455)

    # 能力
    supports_refresh: bool = Field(default=True)

    # 扩展
    extra_auth_params: dict[str, str] = Field(default_factory=dict)

    # JWT 方案配置（对齐 OpenClaw/pi-ai）
    api_base_url: str | None = Field(
        default=None,
        description="LLM API base URL（JWT 方案下为 chatgpt.com/backend-api）",
    )
    extra_api_headers: dict[str, str] = Field(
        default_factory=dict,
        description="API 调用附加 headers（支持 {account_id} 占位符）",
    )

    # Device Flow 专用
    poll_interval_s: int = Field(default=5, ge=1)
    timeout_s: int = Field(default=300, ge=30)

    def to_device_flow_config(self) -> "DeviceFlowConfig":
        """转换为 DeviceFlowConfig（向后兼容）

        Raises:
            ValueError: flow_type 不是 device_flow 或 device_flow_pkce
        """


class OAuthProviderRegistry:
    """OAuth Provider 注册表

    管理多 Provider 的 OAuth 配置。
    内置 OpenAI Codex 和 GitHub Copilot 默认配置。
    """

    def __init__(self) -> None:
        """初始化注册表，自动加载内置 Provider 配置"""

    def register(self, config: OAuthProviderConfig) -> None:
        """注册新 Provider 配置

        Args:
            config: Provider OAuth 配置
        """

    def get(self, provider_id: str) -> OAuthProviderConfig | None:
        """按 canonical_id 获取 Provider 配置

        Args:
            provider_id: Provider 规范 ID

        Returns:
            匹配的配置，未找到返回 None
        """

    def list_providers(self) -> list[OAuthProviderConfig]:
        """列出所有已注册的 Provider"""

    def list_oauth_providers(self) -> list[OAuthProviderConfig]:
        """列出所有支持 OAuth 的 Provider"""

    def resolve_client_id(self, config: OAuthProviderConfig) -> str:
        """解析 Provider 的 Client ID

        优先级: config.client_id > os.environ[config.client_id_env]

        Args:
            config: Provider 配置

        Returns:
            Client ID 字符串

        Raises:
            OAuthFlowError: 无法解析 Client ID
        """


# display_id -> canonical_id 映射表
DISPLAY_TO_CANONICAL: dict[str, str] = {
    "openai": "openai-codex",
    "github": "github-copilot",
}
```

---

## SS3. 环境检测 API

**文件**: `packages/provider/src/octoagent/provider/auth/environment.py`
**对齐**: spec FR-002

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EnvironmentContext:
    """运行环境上下文"""

    is_remote: bool
    can_open_browser: bool
    force_manual: bool
    detection_details: str

    @property
    def use_manual_mode(self) -> bool:
        """是否应使用手动粘贴模式"""


def detect_environment(force_manual: bool = False) -> EnvironmentContext:
    """检测当前运行环境

    检测维度:
    1. SSH 环境（SSH_CLIENT, SSH_TTY, SSH_CONNECTION 环境变量）
    2. 容器/云开发环境（REMOTE_CONTAINERS, CODESPACES, CLOUD_SHELL）
    3. Linux 无图形界面（无 DISPLAY 和 WAYLAND_DISPLAY，且非 WSL）

    Args:
        force_manual: --manual-oauth CLI flag 的值

    Returns:
        EnvironmentContext 实例
    """


def is_remote_environment() -> bool:
    """检测是否处于远程/无浏览器环境

    等价于 detect_environment().is_remote
    """


def can_open_browser() -> bool:
    """检测是否可以自动打开浏览器

    使用 webbrowser 模块检测，捕获异常返回 False。
    """
```

---

## SS4. 本地回调服务器 API

**文件**: `packages/provider/src/octoagent/provider/auth/callback_server.py`
**对齐**: spec FR-003

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CallbackResult:
    """OAuth 回调结果"""
    code: str
    state: str


async def wait_for_callback(
    port: int = 1455,
    path: str = "/auth/callback",
    expected_state: str = "",
    timeout: float = 300.0,
) -> CallbackResult:
    """启动临时 HTTP 服务器等待 OAuth callback

    实现要求:
    - 仅绑定 localhost/127.0.0.1（不绑定 0.0.0.0）
    - 验证 callback 中的 state 参数与 expected_state 一致
    - 收到第一个有效 callback 后立即关闭服务器
    - 超时（默认 5 分钟）后自动关闭
    - 返回 HTML 页面告知用户授权结果

    HTTP 响应规则:
    - 非 /auth/callback 路径 -> HTTP 404
    - 缺少 code 或 state 参数 -> HTTP 400
    - state 不匹配 -> HTTP 400
    - 成功 -> HTTP 200 + 成功提示 HTML

    Args:
        port: 监听端口（默认 1455）
        path: 回调路径
        expected_state: 预期的 state 参数值
        timeout: 超时时间（秒）

    Returns:
        CallbackResult 包含 code 和 state

    Raises:
        OAuthFlowError: 超时或端口被占用
        OSError: 端口绑定失败（EADDRINUSE）
    """
```

### 端口冲突处理约定

调用方（OAuthFlowRunner）负责捕获 `OSError` 并降级到手动模式：

```python
# 调用方示例
try:
    result = await wait_for_callback(port=config.redirect_port, ...)
except OSError:
    # 端口被占用 -> 降级到手动粘贴模式
    result = await manual_paste_flow(auth_url, expected_state)
```

---

## SS5. OAuth 流程编排 API

**文件**: `packages/provider/src/octoagent/provider/auth/oauth_flows.py`
**对齐**: spec FR-005

```python
from pydantic import BaseModel, Field, SecretStr
from datetime import datetime


class OAuthTokenResponse(BaseModel):
    """OAuth Token 端点响应"""

    access_token: SecretStr
    refresh_token: SecretStr = SecretStr("")
    token_type: str = "Bearer"
    expires_in: int = 3600
    scope: str = ""
    account_id: str | None = None


async def run_auth_code_pkce_flow(
    config: OAuthProviderConfig,
    registry: OAuthProviderRegistry,
    env: EnvironmentContext,
    on_auth_url: Callable[[str], Awaitable[None]] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> OAuthCredential:
    """执行 Auth Code + PKCE OAuth 流程

    完整步骤（JWT 方案，对齐 OpenClaw/pi-ai）:
    1. 解析 client_id（从 config 或环境变量）
    2. 生成 PKCE verifier/challenge + 独立 state
    3. 构建授权 URL
    4. 根据环境上下文选择交互模式:
       - 本地: 自动打开浏览器 + 启动回调服务器
       - 远程/VPS: 输出 URL + 等待手动粘贴 redirect URL
       - 端口冲突: 自动降级到手动模式
    5. 验证 state 参数一致性
    6. 使用授权码 + code_verifier 向 token 端点请求 JWT access_token
    6.5 从 JWT access_token 提取 chatgpt_account_id（无 Token Exchange）
    7. 构建 OAuthCredential 并返回（access_token 为 JWT）

    事件发射:
    - 流程开始: OAUTH_STARTED
    - 成功: OAUTH_SUCCEEDED
    - 失败: OAUTH_FAILED

    Args:
        config: Provider OAuth 配置
        registry: Provider 注册表（用于解析 client_id）
        env: 环境上下文
        on_auth_url: 自定义授权 URL 处理回调（默认使用 webbrowser.open）
        on_status: 状态更新回调（用于 CLI 输出）

    Returns:
        OAuthCredential 实例

    Raises:
        OAuthFlowError: 授权失败、超时或 state 验证失败
    """


def extract_account_id_from_jwt(access_token: str) -> str | None:
    """从 JWT access_token 中提取 chatgpt_account_id

    对齐 OpenClaw/pi-ai 的 extractAccountId() 逻辑。
    JWT payload 路径: payload["https://api.openai.com/auth"]["chatgpt_account_id"]

    仅做 base64url 解码（无签名验证），不引入任何依赖。

    Args:
        access_token: JWT 格式的 access_token

    Returns:
        chatgpt_account_id 字符串；解析失败、claim 缺失、非 JWT 格式时返回 None
    """


async def exchange_code_for_token(
    token_endpoint: str,
    code: str,
    code_verifier: str,
    client_id: str,
    redirect_uri: str,
) -> OAuthTokenResponse:
    """授权码 + PKCE verifier 交换 Token

    发送 POST 请求到 token 端点:
    - grant_type: authorization_code
    - code: 授权码
    - code_verifier: PKCE verifier
    - client_id: Client ID
    - redirect_uri: 回调 URI

    Args:
        token_endpoint: Token 端点 URL
        code: 授权码
        code_verifier: PKCE code_verifier
        client_id: OAuth Client ID
        redirect_uri: 回调 URI

    Returns:
        OAuthTokenResponse 实例

    Raises:
        OAuthFlowError: Token 交换失败
    """


async def refresh_access_token(
    token_endpoint: str,
    refresh_token: str,
    client_id: str,
) -> OAuthTokenResponse:
    """使用 refresh_token 获取新的 access_token

    发送 POST 请求到 token 端点:
    - grant_type: refresh_token
    - refresh_token: 刷新令牌
    - client_id: Client ID

    Args:
        token_endpoint: Token 端点 URL
        refresh_token: 刷新令牌值
        client_id: OAuth Client ID

    Returns:
        OAuthTokenResponse 实例

    Raises:
        OAuthFlowError: 刷新失败（如 invalid_grant）
    """


async def manual_paste_flow(
    auth_url: str,
    expected_state: str,
) -> CallbackResult:
    """手动粘贴模式 -- 用户粘贴 redirect URL

    流程:
    1. 输出 auth_url 到终端
    2. 等待用户粘贴 redirect URL
    3. 解析 URL 提取 code 和 state
    4. 验证 state 一致性

    Args:
        auth_url: 完整的授权 URL
        expected_state: 预期的 state 参数值

    Returns:
        CallbackResult 包含 code 和 state

    Raises:
        OAuthFlowError: URL 解析失败或 state 不匹配
    """


def build_authorize_url(
    config: OAuthProviderConfig,
    client_id: str,
    code_challenge: str,
    state: str,
) -> str:
    """构建 OAuth 授权 URL

    URL 参数:
    - client_id
    - redirect_uri
    - response_type=code
    - scope (空格分隔)
    - code_challenge
    - code_challenge_method=S256
    - state
    - extra_auth_params (来自 config)

    Args:
        config: Provider 配置
        client_id: 解析后的 Client ID
        code_challenge: PKCE code_challenge
        state: CSRF state 参数

    Returns:
        完整的授权 URL 字符串
    """
```

---

## SS6. PkceOAuthAdapter -- PKCE OAuth 认证适配器

**文件**: `packages/provider/src/octoagent/provider/auth/pkce_oauth_adapter.py`
**对齐**: spec FR-006, 继承 auth-adapter-api.md SS1

```python
class PkceOAuthAdapter(AuthAdapter):
    """PKCE OAuth 认证适配器

    适用 Provider: 支持 Auth Code + PKCE 流程的 Provider（如 OpenAI Codex）
    特征: 支持 token 自动刷新（通过注入的 CredentialStore 回写）
    """

    def __init__(
        self,
        credential: OAuthCredential,
        provider_config: OAuthProviderConfig,
        store: CredentialStore,
        profile_name: str,
    ) -> None:
        """初始化

        Args:
            credential: OAuth 凭证
            provider_config: Provider OAuth 配置（用于 token 端点等信息）
            store: Credential Store 实例（用于刷新后回写）
            profile_name: 当前 profile 名称（用于 store 更新）
        """

    async def resolve(self) -> str:
        """返回 access_token

        如果 token 已过期且有 refresh_token，自动尝试刷新。

        Returns:
            可直接用于 API 调用的 access_token

        Raises:
            CredentialNotFoundError: access_token 为空
            CredentialExpiredError: Token 已过期且刷新失败
        """

    async def refresh(self) -> str | None:
        """使用 refresh_token 刷新 access_token

        刷新成功后:
        1. 更新内存中的 credential 实例
        2. 写回 credential store（并发安全、原子写入）
        3. 发射 OAUTH_REFRESHED 事件

        刷新失败时:
        - invalid_grant: 清除过期凭证，返回 None
        - 网络错误: 抛出 OAuthFlowError

        Returns:
            刷新后的 access_token；不支持刷新或刷新失败时返回 None
        """

    def is_expired(self) -> bool:
        """基于 expires_at 判断 token 是否过期"""

    def get_api_base_url(self) -> str | None:
        """返回 LLM API base URL

        JWT 方案下返回 chatgpt.com/backend-api；未配置返回 None。
        """

    def get_extra_headers(self) -> dict[str, str]:
        """返回 API 调用附加 headers

        JWT 方案需要: chatgpt-account-id, OpenAI-Beta, originator。
        模板中的 {account_id} 占位符被替换为实际值。
        """
```

### 与 CodexOAuthAdapter 的关系

| 特性 | CodexOAuthAdapter (003) | PkceOAuthAdapter (003-b) |
|------|------------------------|--------------------------|
| 流程来源 | Device Flow | Auth Code + PKCE |
| refresh() | 返回 None | 实现自动刷新 |
| 构造参数 | credential only | credential + config + store + profile |
| store 写入 | 无 | 刷新后自动回写 |
| 向后兼容 | 保留不变 | 新增 Adapter |

---

## SS7. OAuth 事件发射扩展

**文件**: `packages/provider/src/octoagent/provider/auth/events.py` (扩展)
**对齐**: spec FR-012

```python
async def emit_oauth_event(
    event_store: EventStoreProtocol | None,
    event_type: EventType,
    provider_id: str,
    payload: dict[str, Any],
) -> None:
    """发射 OAuth 流程事件

    复用 emit_credential_event 的 Event Store 写入逻辑。
    payload 中 MUST NOT 包含 access_token, refresh_token, code_verifier, state 的明文值。

    Args:
        event_store: Event Store 实例（None 时仅记录日志）
        event_type: OAUTH_STARTED / OAUTH_SUCCEEDED / OAUTH_FAILED / OAUTH_REFRESHED
        provider_id: Provider canonical_id
        payload: 事件负载（已脱敏）
    """
```

---

## SS8. Init Wizard 更新

**文件**: `packages/provider/src/octoagent/provider/dx/init_wizard.py` (修改)
**对齐**: spec FR-007

### 变更点

1. **AUTH_MODE_LABELS 更新**:
```python
AUTH_MODE_LABELS: dict[str, str] = {
    "api_key": "API Key（标准密钥）",
    "token": "Setup Token（免费试用）",
    "oauth": "OAuth PKCE（免费试用，浏览器授权）",  # 从 "Device Flow" 改为 "PKCE"
}
```

2. **新增 `_run_oauth_pkce_flow()` 函数**:
```python
async def _run_oauth_pkce_flow(
    provider: str,
    force_manual: bool = False,
) -> OAuthCredential | None:
    """执行 OAuth PKCE 流程

    根据 provider display_id 从注册表获取 canonical_id，
    然后调用 run_auth_code_pkce_flow() 完成授权。

    Args:
        provider: Provider display_id（如 "openai"）
        force_manual: 是否强制手动模式

    Returns:
        OAuthCredential 实例，失败返回 None
    """
```

3. **OAuth 模式分支更新**:
```python
elif auth_mode == "oauth":
    # 003-b: 根据 Provider 的 flow_type 选择 PKCE 或 Device Flow
    provider_config = registry.get(DISPLAY_TO_CANONICAL.get(provider, ""))
    if provider_config and provider_config.flow_type == "auth_code_pkce":
        credential = asyncio.run(_run_oauth_pkce_flow(provider, force_manual))
    else:
        credential = asyncio.run(_run_oauth_device_flow())  # 保留 Device Flow
```

4. **CLI flag 支持**:
```python
def run_init_wizard(
    project_root: Path | None = None,
    store: CredentialStore | None = None,
    manual_oauth: bool = False,  # 003-b 新增
) -> InitConfig:
```

### CLI 入口更新

**文件**: `packages/provider/src/octoagent/provider/dx/cli.py` (修改)

```python
@main.command()
@click.option(
    "--manual-oauth",
    is_flag=True,
    default=False,
    help="强制使用手动 OAuth 模式（粘贴 redirect URL）",
)
def init(manual_oauth: bool) -> None:
    """初始化 OctoAgent 配置"""
    run_init_wizard(manual_oauth=manual_oauth)
```

---

## SS9. HandlerChain 适配

**文件**: `packages/provider/src/octoagent/provider/auth/chain.py` (修改)
**对齐**: spec FR-011

HandlerChain 的 adapter factory 注册需要支持 PkceOAuthAdapter:

```python
# 注册示例
chain.register_adapter_factory(
    provider="openai-codex",
    factory=lambda cred: PkceOAuthAdapter(
        credential=cred,
        provider_config=registry.get("openai-codex"),
        store=store,
        profile_name=f"openai-codex-default",
    ),
)
```

**注意**: `_create_adapter` 方法无需修改，factory 签名 `Callable[[Credential], AuthAdapter]` 通过闭包捕获额外参数。

---

## SS10. HandlerChainResult 路由覆盖（实现阶段增补）

**文件**: `packages/provider/src/octoagent/provider/auth/chain.py` (修改)
**对齐**: spec Q9 — 多认证路由隔离

```python
class HandlerChainResult(BaseModel):
    """Handler Chain 解析结果"""

    provider: str = Field(description="匹配的 Provider")
    credential_value: str = Field(description="解析到的凭证值")
    source: Literal["profile", "store", "env", "default"]
    adapter: str = Field(description="匹配的 AuthAdapter 类名")

    # --- 路由覆盖（003-b JWT 方案） ---
    api_base_url: str | None = Field(
        default=None,
        description="LLM API base URL 覆盖；None 表示使用调用方默认值（如 Proxy URL）",
    )
    extra_headers: dict[str, str] = Field(
        default_factory=dict,
        description="LLM API 调用附加 headers（如 chatgpt-account-id）",
    )
```

### 路由提取机制

```python
@staticmethod
def _extract_routing(adapter: AuthAdapter) -> dict:
    """从 adapter 提取路由覆盖信息（duck-typing）

    仅当 adapter 提供 get_api_base_url() / get_extra_headers() 时填充。
    非 OAuth adapter 返回空 dict，不影响 HandlerChainResult 默认值。
    """
    routing: dict = {}
    if hasattr(adapter, "get_api_base_url"):
        api_base = adapter.get_api_base_url()
        if api_base is not None:
            routing["api_base_url"] = api_base
    if hasattr(adapter, "get_extra_headers"):
        headers = adapter.get_extra_headers()
        if headers:
            routing["extra_headers"] = headers
    return routing
```

---

## SS11. LiteLLMClient 路由覆盖 + Reasoning 配置（实现阶段增补）

**文件**: `packages/provider/src/octoagent/provider/client.py` (修改)
**对齐**: spec Q9（路由覆盖）, Q11（Reasoning 配置）

```python
async def complete(
    self,
    messages: list[dict[str, str]],
    model_alias: str = "main",
    temperature: float = 0.7,
    max_tokens: int | None = None,
    *,
    api_base: str | None = None,
    api_key: str | None = None,
    extra_headers: dict[str, str] | None = None,
    reasoning: ReasoningConfig | None = None,
    **kwargs,
) -> ModelCallResult:
    """发送 chat completion 请求

    路由决策: 覆盖参数优先于实例默认值
    - api_base 覆盖 self._proxy_base_url
    - api_key 覆盖 self._proxy_api_key
    - extra_headers 附加到请求
    - reasoning.effort 作为 reasoning_effort 传递给 LiteLLM SDK
    """
```

---

## SS12. ReasoningConfig 模型（实现阶段增补）

**文件**: `packages/provider/src/octoagent/provider/models.py`
**对齐**: spec Q11 — Codex Reasoning/Thinking 模式

```python
from typing import Literal

ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh"]
ReasoningSummary = Literal["auto", "concise", "detailed"]


class ReasoningConfig(BaseModel):
    """Reasoning / Thinking 模式配置

    Responses API: reasoning 对象 {"effort": "high", "summary": "auto"}
    Chat Completions API: 顶层 reasoning_effort: "high"
    """

    effort: ReasoningEffort = Field(default="medium")
    summary: ReasoningSummary | None = Field(default=None)

    def to_responses_api_param(self) -> dict:
        """转换为 Responses API 的 reasoning 参数对象"""
        param: dict = {"effort": self.effort}
        if self.summary is not None:
            param["summary"] = self.summary
        return param
```

### 双路径适配约定

| API 路径 | 参数格式 | 使用场景 |
|----------|---------|---------|
| Chat Completions (LiteLLM SDK) | `reasoning_effort: "high"` (顶层字符串) | LiteLLM Proxy 路由 |
| Responses API (直连) | `reasoning: {"effort": "high", "summary": "auto"}` (嵌套对象) | JWT 直连 chatgpt.com |
