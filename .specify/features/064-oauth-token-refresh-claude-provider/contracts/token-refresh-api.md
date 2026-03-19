# Contract: Token 自动刷新 API

**Feature**: 064-oauth-token-refresh-claude-provider
**Date**: 2026-03-19
**对齐需求**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-011, FR-012

---

## SS1: PkceOAuthAdapter.refresh() — 刷新调用契约

### 现有签名（保持不变）

```python
class PkceOAuthAdapter(AuthAdapter):
    async def refresh(self) -> str | None:
        """使用 refresh_token 刷新 access_token

        Returns:
            刷新后的 access_token 字符串；不支持刷新或刷新失败时返回 None
        """
```

### 前置条件

1. `self._credential.refresh_token` 非空
2. `self._provider_config.supports_refresh` 为 `True`
3. `self._provider_config.client_id` 可解析

### 后置条件（成功）

1. 内存中 `self._credential` 更新为新凭证
2. `CredentialStore` 通过 `set_profile()` 原子写入新凭证
3. 发射 `OAUTH_REFRESHED` 事件（payload 仅含 `new_expires_in`）
4. 返回新的 `access_token` 字符串

### 后置条件（失败）

1. `invalid_grant` 错误 -> 调用 `store.remove_profile()` 清除无效凭证，返回 `None`
2. 网络错误 -> 记录 warning 日志，返回 `None`
3. 其他 `OAuthFlowError` -> 记录 warning 日志，返回 `None`

---

## SS2: PkceOAuthAdapter.is_expired() — 过期预检扩展

### 变更：增加缓冲期预检

```python
# 常量定义
REFRESH_BUFFER_SECONDS: int = 300  # 5 分钟

class PkceOAuthAdapter(AuthAdapter):
    def is_expired(self) -> bool:
        """基于 expires_at 判断 token 是否过期或即将过期

        当距过期时间不足 REFRESH_BUFFER_SECONDS 时，视为"已过期"
        以触发提前刷新。

        Returns:
            True: token 已过期或距过期不足 5 分钟
            False: token 仍在有效期内且距过期超过 5 分钟
        """
        now = datetime.now(tz=UTC)
        buffer = timedelta(seconds=REFRESH_BUFFER_SECONDS)
        return now >= (self._credential.expires_at - buffer)
```

### 对齐需求

- FR-011: 缓冲期硬编码 5 分钟，与 OpenClaw Gemini CLI OAuth 一致

---

## SS3: LiteLLMClient.complete() — refresh-on-error 重试契约

### 新增参数

```python
class LiteLLMClient:
    def __init__(
        self,
        ...
        auth_refresh_callback: Callable[[], Awaitable[HandlerChainResult | None]] | None = None,
    ) -> None:
        """
        Args:
            auth_refresh_callback: 认证刷新回调函数。
                当 LLM 调用返回 401/403 时，调用此函数获取刷新后的凭证。
                返回 None 表示刷新失败。
        """
        self._auth_refresh_callback = auth_refresh_callback
```

### 重试逻辑伪代码

```python
async def complete(self, messages, model_alias, ...,
                   api_base=None, api_key=None, extra_headers=None, **kwargs):
    try:
        return await self._do_complete(messages, model_alias, ...,
                                       api_base=api_base, api_key=api_key,
                                       extra_headers=extra_headers, **kwargs)
    except ProviderError as e:
        if not self._is_auth_error(e) or self._auth_refresh_callback is None:
            raise
        # 触发刷新
        refreshed = await self._auth_refresh_callback()
        if refreshed is None:
            raise  # 刷新失败，抛出原始错误
        # 使用刷新后的凭证重试（最多一次）
        return await self._do_complete(
            messages, model_alias, ...,
            api_base=refreshed.api_base_url or api_base,
            api_key=refreshed.credential_value,
            extra_headers=refreshed.extra_headers or extra_headers,
            **kwargs,
        )
```

### 认证错误判定

```python
@staticmethod
def _is_auth_error(e: Exception) -> bool:
    """判断异常是否为认证类错误（401/403）

    检查方式:
    1. 异常类型为 AuthenticationError
    2. 异常消息包含 "401" 或 "403" 且包含 "auth"/"unauthorized"/"forbidden"
    3. LiteLLM SDK 的 AuthenticationError / NotFoundError 中状态码为 401/403
    """
```

### 约束

- **最多重试一次**: 防止 refresh-fail-refresh 无限循环
- **仅 OAuth Provider 触发**: 非 OAuth 调用（无 `auth_refresh_callback`）直接抛出原始错误
- **不影响非 OAuth 路径**: `api_base=None` 且 `api_key=None` 时走标准 Proxy 路径

---

## SS4: 并发刷新串行化契约

### 机制: per-provider asyncio.Lock

```python
class TokenRefreshCoordinator:
    """per-provider 刷新串行化协调器

    保证同一 Provider 同一时刻只有一个刷新操作执行。
    不同 Provider 的刷新操作互不阻塞。
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, provider_id: str) -> asyncio.Lock:
        if provider_id not in self._locks:
            self._locks[provider_id] = asyncio.Lock()
        return self._locks[provider_id]

    async def refresh_if_needed(
        self,
        provider_id: str,
        chain: HandlerChain,
        provider: str | None = None,
        profile_name: str | None = None,
    ) -> HandlerChainResult | None:
        """在 provider 锁保护下执行刷新

        如果锁已被其他协程持有，等待刷新完成后重新 resolve（使用新 token）。

        Args:
            provider_id: Provider canonical_id（锁粒度）
            chain: HandlerChain 实例
            provider: resolve 参数
            profile_name: resolve 参数

        Returns:
            刷新后的 HandlerChainResult，失败返回 None
        """
        lock = self._get_lock(provider_id)
        async with lock:
            return await chain.resolve(provider=provider, profile_name=profile_name)
```

### 锁层次

| 层级 | 锁类型 | 粒度 | 保护范围 |
|------|--------|------|---------|
| 内存 | `asyncio.Lock` | per-provider | 进程内并发刷新串行化 |
| 文件 | `filelock` | 全局单文件 | `CredentialStore` 跨进程原子写入 |

### 对齐需求

- FR-005: 同一 Provider 同一时刻只有一个刷新操作执行
- 不同 Provider 的刷新互不阻塞（per-provider 锁）

---

## SS5: refresh_access_token() — Claude 适配

### 现有函数（需修改）

`refresh_access_token()` 当前在刷新后调用 `extract_account_id_from_jwt()`。Claude 的 access_token 不是 JWT 格式（`sk-ant-oat01-*`），调用 JWT 解析会返回 `None`，这是可接受的——函数已处理此情况（回退到 `data.get("account_id")`）。

**结论**: `refresh_access_token()` 无需修改即可支持 Claude Provider。JWT 解析失败时 `account_id` 为 `None`，符合 Claude 的数据模型（DM-2: `account_id = None`）。

---

## SS6: 凭证实时读取契约

### 调用链

```
Kernel 发起 LLM 调用
  -> HandlerChain.resolve(provider=...)
     -> CredentialStore.get_profile()       # 从 JSON 文件读取最新凭证
     -> PkceOAuthAdapter(credential=...)
        -> is_expired()?
           Yes -> refresh() -> 更新 store -> 返回新 token
           No  -> 返回当前 token
  -> HandlerChainResult(credential_value=..., api_base_url=..., extra_headers=...)
  -> LiteLLMClient.complete(api_key=result.credential_value,
                            api_base=result.api_base_url,
                            extra_headers=result.extra_headers)
```

### 关键保证

1. **每次 LLM 调用都从 CredentialStore 读取最新凭证** — 不缓存 token
2. **刷新后的 token 立即生效** — `PkceOAuthAdapter.refresh()` 同步更新 `self._credential` 和 store
3. **无需重启任何组件** — 凭证在调用链内实时流转
4. **OAuth 和非 OAuth 路径隔离** — 非 OAuth Provider 不经过此链路

### 对齐需求

- FR-006: 每次 LLM 调用实时读取最新 token
- FR-007: 非 OAuth Provider 不受影响
