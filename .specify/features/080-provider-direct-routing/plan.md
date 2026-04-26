# Feature 080 — Provider 直连：架构设计 + 实施计划

> 作者：Connor
> 日期：2026-04-26（v2，吸收 Codex adversarial review）
> 上游：spec.md
> 下游：tasks.md
> 模式：spec-driver-feature

## 0.0 v2 修订说明（Codex review 后）

Codex adversarial review 提出 5 个 high finding，全部证实：
- **F1**：每次现读 alias 会让同 task 跨 provider，触发非幂等工具重跑风险
- **F2**：ProviderClient 缓存会固化 OAuth credential，账号切换不感知
- **F3**：401-only reactive refresh 比现有 401/403 双触发窄，丢掉 403 自愈能力
- **F4**：Phase 1 feature flag 只切 LLM 主链路，memory bridge / embedding 仍走 Proxy → rollout 假象
- **F5**：启动时自动改写 yaml + 旧读者 warn-continue → 回滚不可靠

修正方案见 §5（关键设计决策 v2）和 §4（Phase 划分 v2）。

---

## 0. 总览

```
现状：
┌─ Skill ─┐    ┌─ LiteLLM ─┐    ┌─ Provider ─┐
│ Runner  │ ─→ │  Proxy    │ ─→ │   API      │
│         │    │ (独立进程) │    │            │
└─────────┘    └───────────┘    └────────────┘
                  │
                  └─→ frozen yaml + cooldown 黑盒 + 配置漂移

目标：
┌─ Skill ─┐    ┌─ Provider ─┐
│ Runner  │ ─→ │   API      │
└─────────┘    └────────────┘
   │
   └─→ ProviderRouter 每次调用解析 alias → ProviderClient
```

**核心变化**：
- 删除 LiteLLM Proxy 这一层
- 引入 `ProviderTransport` 枚举（3 种）+ `AuthResolver` 抽象（2 种）
- alias → provider 解析从启动时 frozen 改为每次调用时动态
- 配置 source-of-truth 收敛到 `octoagent.yaml` + `auth-profiles.json`

---

## 1. 核心抽象设计

### 1.1 ProviderTransport（协议层）

```python
# octoagent/packages/provider/src/octoagent/provider/transport.py
class ProviderTransport(str, Enum):
    """LLM 调用协议类型。每个 transport 对应一个 HTTP 请求构建/响应解析路径。"""

    OPENAI_CHAT = "openai_chat"
    """OpenAI Chat Completions API（/v1/chat/completions）。
    覆盖：OpenAI、SiliconFlow、DeepSeek、Groq、OpenRouter、Together AI、Mistral 等
    所有声明 OpenAI 兼容的 provider。这是最广的 transport。"""

    OPENAI_RESPONSES = "openai_responses"
    """OpenAI Responses API（/v1/responses 或 chatgpt.com/backend-api/codex/responses）。
    覆盖：OpenAI 原生 Responses + ChatGPT Pro Codex OAuth。"""

    ANTHROPIC_MESSAGES = "anthropic_messages"
    """Anthropic Messages API（/v1/messages）。
    覆盖：Anthropic Claude API + Claude Pro/Max OAuth。"""
```

**设计理由**：
- 用 `transport` 字段而不是 `provider` 字段做协议路由，因为 SiliconFlow / DeepSeek / Groq 都用同一个 OpenAI Chat 协议；用 transport 抽象一次实现，N 个 provider 复用
- 不引入 `bedrock_converse` / `google_gemini` 等：本 Feature scope 锁定 3 个，覆盖 95% 的实际使用

### 1.2 AuthResolver（凭证层）

```python
# octoagent/packages/provider/src/octoagent/provider/auth_resolver.py

@dataclass(frozen=True)
class ResolvedAuth:
    """LLM 调用前 resolve 出来的"现役凭证"，含 token + 动态 headers。"""
    bearer_token: str
    extra_headers: dict[str, str] = field(default_factory=dict)
    """会被 merge 到请求 headers，比如 OAuth 的 chatgpt-account-id（动态）。"""


class AuthResolver(Protocol):
    """每次 LLM 调用前 resolve 出 bearer token；401 时 force_refresh 并重试。"""

    async def resolve(self) -> ResolvedAuth: ...
    async def force_refresh(self) -> ResolvedAuth | None:
        """force=True 路径。失败返回 None，调用方应回退到原 401。"""


class StaticApiKeyResolver:
    """API key 类（SiliconFlow、DeepSeek、OpenAI raw key 等）。"""

    def __init__(self, env_var: str, extra_headers: dict[str, str] | None = None):
        self._env_var = env_var
        self._extra_headers = dict(extra_headers or {})

    async def resolve(self) -> ResolvedAuth:
        token = os.environ.get(self._env_var, "")
        if not token:
            raise CredentialNotFoundError(f"env {self._env_var} 为空")
        return ResolvedAuth(bearer_token=token, extra_headers=dict(self._extra_headers))

    async def force_refresh(self) -> ResolvedAuth | None:
        # API key 没法 refresh —— 直接重新读 env（用户可能刚改过 .env）
        token = os.environ.get(self._env_var, "")
        return ResolvedAuth(bearer_token=token, ...) if token else None


class OAuthResolver:
    """OAuth 类（ChatGPT Pro Codex、Anthropic Claude OAuth）。

    内嵌 PkceOAuthAdapter，复用 Feature 078 的所有逻辑：
    - is_expired() preemptive 检查
    - force_refresh 强制刷新
    - reused_recovery / store_reload
    - emit_oauth_event 事件埋点
    """

    def __init__(
        self,
        adapter: PkceOAuthAdapter,
        coordinator: TokenRefreshCoordinator,
        provider_id: str,
        extra_headers_template: dict[str, str] | None = None,
    ):
        self._adapter = adapter
        self._coord = coordinator
        self._provider_id = provider_id
        self._tmpl = dict(extra_headers_template or {})

    async def resolve(self, *, force: bool = False) -> ResolvedAuth:
        token = await self._coord.refresh_if_needed(
            provider_id=self._provider_id,
            refresh_fn=lambda: self._adapter.resolve(force_refresh=force),
        )
        if not token:
            raise CredentialExpiredError(f"OAuth refresh 失败：{self._provider_id}")
        # 模板替换 {account_id}
        account_id = self._adapter.credential.account_id or ""
        headers = {k: v.replace("{account_id}", account_id) for k, v in self._tmpl.items()}
        return ResolvedAuth(bearer_token=token, extra_headers=headers)

    async def force_refresh(self) -> ResolvedAuth | None:
        try:
            return await self.resolve(force=True)
        except (CredentialExpiredError, CredentialNotFoundError):
            return None
```

**设计理由**：
- 把"凭证管理"和"协议层"完全解耦：同一个 `OAuthResolver(provider_id="openai-codex")` 既可以配 `OPENAI_RESPONSES` transport，也可以理论上配 `OPENAI_CHAT`（如果 ChatGPT Pro 支持）
- StaticApiKeyResolver 也保持一致接口（force_refresh 重读 env）—— 调用方不需要分类型处理
- OAuthResolver 内嵌 `PkceOAuthAdapter` + `TokenRefreshCoordinator` —— **完整复用 Feature 078 的 1500 行 OAuth 逻辑**，零浪费

### 1.3 ProviderRuntime（运行时配置）

```python
# octoagent/packages/provider/src/octoagent/provider/runtime.py

@dataclass(frozen=True)
class ProviderRuntime:
    """运行时单 provider 的完整描述符。

    既包含 octoagent.yaml 的 static 配置，也持有 live 的 AuthResolver。
    在 ProviderRouter.resolve_for_alias 时按需构造，不长生命周期持有
    （token 刷新由 AuthResolver 内部管理，无需 runtime 重建）。
    """

    provider_id: str
    """与 octoagent.yaml.providers[].id 对齐，如 'openai-codex'。"""

    transport: ProviderTransport

    api_base: str
    """Provider 的 HTTP 基础 URL，如 https://api.siliconflow.cn 或
    https://chatgpt.com/backend-api/codex"""

    auth_resolver: AuthResolver
    """凭证解析器。每次 LLM 调用前 resolve()。"""

    extra_headers: dict[str, str] = field(default_factory=dict)
    """与 auth_resolver.resolve() 返回的 extra_headers merge，
    包括静态字段（OpenAI-Beta、originator 等）。"""

    extra_body: dict[str, Any] = field(default_factory=dict)
    """每次请求 body 自动 merge 的字段（如 store=False / stream=True）。"""

    timeout_s: float = 60.0
```

### 1.4 ProviderClient（HTTP 调用层）

```python
# octoagent/packages/provider/src/octoagent/provider/client.py

class ProviderClient:
    """单 provider 的 LLM 调用 client。

    所有 transport 通过这一个类处理（按 transport 字段路由到内部 _call_xxx 方法），
    避免 N 个 provider × M 个 transport 的笛卡尔爆炸。
    """

    def __init__(self, runtime: ProviderRuntime, http_client: httpx.AsyncClient):
        self._runtime = runtime
        self._http = http_client

    async def call(
        self,
        *,
        manifest: SkillManifest,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model_name: str,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        """按 transport 路由到具体协议实现。401 时统一 force_refresh + retry 一次。"""
        try:
            return await self._dispatch(
                manifest=manifest,
                history=history,
                tools=tools,
                model_name=model_name,
                auth=await self._runtime.auth_resolver.resolve(),
            )
        except LLMCallError as exc:
            if exc.status_code != 401:
                raise
            log.info(
                "provider_401_force_refresh",
                provider_id=self._runtime.provider_id,
                transport=self._runtime.transport.value,
            )
            fresh = await self._runtime.auth_resolver.force_refresh()
            if fresh is None:
                raise
            return await self._dispatch(
                manifest=manifest,
                history=history,
                tools=tools,
                model_name=model_name,
                auth=fresh,
            )

    async def _dispatch(self, *, auth, manifest, history, tools, model_name):
        if self._runtime.transport == ProviderTransport.OPENAI_CHAT:
            return await self._call_openai_chat(auth, manifest, history, tools, model_name)
        if self._runtime.transport == ProviderTransport.OPENAI_RESPONSES:
            return await self._call_openai_responses(auth, manifest, history, tools, model_name)
        if self._runtime.transport == ProviderTransport.ANTHROPIC_MESSAGES:
            return await self._call_anthropic_messages(auth, manifest, history, tools, model_name)
        raise NotImplementedError(f"unsupported transport: {self._runtime.transport}")

    # _call_openai_chat / _call_openai_responses / _call_anthropic_messages 内部实现
    # 大量复用现有 ChatCompletionsProvider / ResponsesApiProvider 的代码（流式解析、tool_call
    # 累积、usage 提取等），只是 base_url + auth 来源换成 runtime
```

### 1.5 ProviderRouter（alias 路由层）

```python
# octoagent/packages/provider/src/octoagent/provider/router.py

class ProviderRouter:
    """alias → ProviderClient + model_name 的解析。

    设计要点：
    - 每次 resolve 都重新读 octoagent.yaml（不 frozen）—— 改 alias 后下个 task 立即生效
    - ProviderRuntime 缓存复用（auth_resolver / extra_headers 都是 stateless），
      避免每次都构造 PkceOAuthAdapter
    - http_client 共享一个长生命周期实例
    """

    def __init__(
        self,
        project_root: Path,
        credential_store: CredentialStore,
        coordinator: TokenRefreshCoordinator,
        event_store: EventStoreProtocol | None = None,
        timeout_s: float = 60.0,
    ):
        self._project_root = project_root
        self._store = credential_store
        self._coord = coordinator
        self._event_store = event_store
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=10.0))
        self._client_cache: dict[str, ProviderClient] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    def resolve_for_alias(self, model_alias: str) -> tuple[ProviderClient, str]:
        """每次调用都从 octoagent.yaml 现读，自动感知改动。

        Returns:
            (client, model_name) tuple
        """
        cfg = load_config(self._project_root)
        if cfg is None:
            raise CredentialError(f"octoagent.yaml 不可用，无法 resolve alias {model_alias}")
        alias_cfg = cfg.model_aliases.get(model_alias)
        if alias_cfg is None:
            raise CredentialError(f"model alias {model_alias!r} 未在 config 中定义")
        provider_cfg = cfg.get_provider(alias_cfg.provider)
        if provider_cfg is None or not provider_cfg.enabled:
            raise CredentialError(
                f"alias {model_alias!r} 的 provider {alias_cfg.provider!r} 不存在或未启用"
            )
        client = self._client_cache.get(provider_cfg.id)
        if client is None or self._needs_rebuild(client, provider_cfg):
            client = self._build_client(provider_cfg)
            self._client_cache[provider_cfg.id] = client
        return client, alias_cfg.model

    def _build_client(self, provider_cfg: ProviderEntry) -> ProviderClient:
        """从 ProviderEntry 构造 ProviderRuntime + ProviderClient。"""
        auth_resolver = self._build_auth_resolver(provider_cfg)
        runtime = ProviderRuntime(
            provider_id=provider_cfg.id,
            transport=ProviderTransport(provider_cfg.transport),
            api_base=provider_cfg.api_base.rstrip("/"),
            auth_resolver=auth_resolver,
            extra_headers=dict(provider_cfg.extra_headers or {}),
            extra_body=dict(provider_cfg.extra_body or {}),
        )
        return ProviderClient(runtime, self._http)

    def _build_auth_resolver(self, provider_cfg: ProviderEntry) -> AuthResolver:
        if provider_cfg.auth.kind == "api_key":
            return StaticApiKeyResolver(env_var=provider_cfg.auth.env)
        if provider_cfg.auth.kind == "oauth":
            profile = self._store.get_profile(provider_cfg.auth.profile)
            if profile is None or not isinstance(profile.credential, OAuthCredential):
                raise CredentialNotFoundError(
                    f"OAuth profile {provider_cfg.auth.profile!r} 不存在",
                )
            adapter_config = BUILTIN_PROVIDERS.get(provider_cfg.id)
            if adapter_config is None:
                raise CredentialError(
                    f"provider {provider_cfg.id!r} 没有 OAuth provider config",
                )
            adapter = PkceOAuthAdapter(
                credential=profile.credential,
                provider_config=adapter_config,
                store=self._store,
                profile_name=provider_cfg.auth.profile,
                event_store=self._event_store,
            )
            return OAuthResolver(
                adapter=adapter,
                coordinator=self._coord,
                provider_id=provider_cfg.id,
                extra_headers_template=adapter_config.extra_api_headers,
            )
        raise CredentialError(f"unknown auth kind: {provider_cfg.auth.kind}")
```

### 1.6 整体调用链

```
LLMService.process_message()
  └─ SkillRunner.run_skill(manifest)
      └─ ProviderModelClient.generate(manifest, history)   # 替代 LiteLLMSkillClient
          └─ ProviderRouter.resolve_for_alias("main")      # 现读 octoagent.yaml
              └─ (client, model_name)
          └─ client.call(manifest, history, tools, model_name)
              └─ auth_resolver.resolve()                    # 复用 Feature 078 PkceOAuthAdapter
              └─ POST <api_base>/v1/chat/completions       # 直连，无中间代理
              └─ on 401: auth_resolver.force_refresh() + retry 1 次
```

**栈深度**：LLMService → SkillRunner → ProviderModelClient → ProviderRouter → ProviderClient → HTTP。其中 Router 只是查表，Client 只是协议适配。**逻辑上 2 层**（Skill / Provider），不再有 LiteLLM Proxy 这一层。

---

## 2. 配置 schema（新版 octoagent.yaml）

### 2.1 新 schema 示例

```yaml
config_version: 2  # ← 升一个版本号方便 migration 检测
updated_at: "2026-04-26"

# 不再有 runtime: { llm_mode, litellm_proxy_url, master_key_env }

providers:
  # ChatGPT Pro Codex（OAuth + Responses API）
  - id: openai-codex
    name: OpenAI Codex (ChatGPT Pro)
    enabled: true
    transport: openai_responses
    api_base: https://chatgpt.com/backend-api/codex
    auth:
      kind: oauth
      profile: openai-codex-default
    extra_headers:
      OpenAI-Beta: responses=experimental
      originator: pi
      User-Agent: pi (darwin; arm64)
      # chatgpt-account-id 由 OAuthResolver 动态注入（{account_id} 模板）
    extra_body:
      store: false

  # Anthropic Claude OAuth
  - id: anthropic-claude
    name: Anthropic Claude (Pro/Max OAuth)
    enabled: true
    transport: anthropic_messages
    api_base: https://api.anthropic.com
    auth:
      kind: oauth
      profile: anthropic-claude-default
    extra_headers:
      anthropic-version: "2023-06-01"

  # SiliconFlow（API Key + OpenAI-compatible Chat）
  - id: siliconflow
    name: SiliconFlow
    enabled: true
    transport: openai_chat
    api_base: https://api.siliconflow.cn
    auth:
      kind: api_key
      env: SILICONFLOW_API_KEY

  # OpenRouter（API Key + OpenAI-compatible Chat，可加更多 provider）
  - id: openrouter
    name: OpenRouter
    enabled: false
    transport: openai_chat
    api_base: https://openrouter.ai/api
    auth:
      kind: api_key
      env: OPENROUTER_API_KEY

model_aliases:
  main:
    provider: openai-codex
    model: gpt-5.5
    thinking_level: medium
  cheap:
    provider: siliconflow
    model: Qwen/Qwen3.5-14B
  rerank:
    provider: siliconflow
    model: Qwen/Qwen3-Reranker-0.6B

memory:
  reasoning_model_alias: main
  expand_model_alias: cheap
  # ...
```

**对比旧 schema 的差异**：
- 删除 `runtime: { llm_mode, litellm_proxy_url, master_key_env }`
- providers[] 每条新增 `transport` / `api_base` / `auth: {kind, env|profile}` / `extra_headers` / `extra_body`
- 删除 `providers[].api_key_env`（迁到 `auth.env`）
- 删除 `providers[].auth_type`（迁到 `auth.kind`）
- 删除 `providers[].base_url`（迁到 `api_base`，名字更准）
- `config_version: 1 → 2` 触发自动迁移

### 2.2 新 ProviderEntry pydantic 模型

```python
# octoagent/.../config_schema.py

class AuthApiKey(BaseModel):
    kind: Literal["api_key"]
    env: str = Field(min_length=1, pattern=r"^[A-Z][A-Z0-9_]*$")

class AuthOAuth(BaseModel):
    kind: Literal["oauth"]
    profile: str = Field(min_length=1)

ProviderAuth = Annotated[Union[AuthApiKey, AuthOAuth], Field(discriminator="kind")]

class ProviderEntry(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=1)
    enabled: bool = True
    transport: Literal["openai_chat", "openai_responses", "anthropic_messages"]
    api_base: str = Field(min_length=1)
    auth: ProviderAuth
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
```

---

## 3. 各 transport 的实现要点

### 3.1 OPENAI_CHAT

**端点**：`POST {api_base}/v1/chat/completions`

**复用现有代码**：从 `octoagent/packages/skills/src/octoagent/skills/providers.py::ChatCompletionsProvider._call_once` 95% 直接搬过来：
- 流式 SSE 解析
- tool_call 按 index 累积
- usage 从最后一个 chunk 提取
- system message merge to front（Qwen / Gemma 兼容）
- 错误分类 `_classify_proxy_error` → 改名 `_classify_provider_error`

**唯一差异**：
- `Authorization: Bearer {auth.bearer_token}` 而不是 master_key
- `extra_headers` merge 进请求

### 3.2 OPENAI_RESPONSES

**端点**：
- 标准 OpenAI：`POST {api_base}/v1/responses`
- ChatGPT Pro Codex：`POST chatgpt.com/backend-api/codex/responses`

**复用现有代码**：从 `ResponsesApiProvider._call_once` 95% 搬过来：
- `_history_to_responses_input` 转换
- `_build_responses_instructions`
- `response.output_text.delta` / `response.output_item.added` 等事件解析
- function_call_output 配对

**关键差异**：
- 不再需要 `responses_direct_params` 启动快照（router 现读）
- `Authorization` + `extra_headers`（含 `chatgpt-account-id`）由 ResolvedAuth 提供

### 3.3 ANTHROPIC_MESSAGES（**新增**，本 Feature 第一次实现）

**端点**：`POST {api_base}/v1/messages`

**协议要点**（与 OpenAI Chat 的差异）：
- `messages: [...]` 不能含 `role: "system"`，system 单独走 `system: "..."` 字段（顶层）
- `tools` 字段格式：`{name, description, input_schema}`（不是 `{type: function, function: {...}}`）
- 流式事件：`message_start` / `content_block_start` / `content_block_delta` / `content_block_stop` / `message_delta` / `message_stop`
- tool_use 在 `content_block_delta` 的 `input_json_delta` 累积
- usage 在 `message_delta` 的 `usage` 字段
- thinking：通过 `thinking: {type: enabled, budget_tokens: N}`（Claude 4 / opus / sonnet 4 支持）

**OAuth 头**：Anthropic OAuth 走 `Authorization: Bearer {oauth_token}` + `anthropic-beta: oauth-2025-04-20`（用户 OAuth 专用）

**实现策略**：新写一个 `_call_anthropic_messages` 方法，~300 行（流式 + tool_call + thinking 加起来）

---

## 4. Phase 划分（v2，吸收 Codex review F4）

**v1 设计**：Phase 1 引入 feature flag 让用户立即换上 → **被 Codex F4 打回**（memory bridge / embedding 仍走 Proxy，rollout 假象）

**v2 设计**：Phase 1 不接入主链路，纯抽象 + 单测；P3 一次性切线（含所有 Proxy 依赖点）

### Phase 1（**v2**）— 抽象层 + OPENAI_RESPONSES + 单测（不接主链路）

**目标**：建立设计骨架，**仅作为新模块存在**，不暴露给用户。Phase 2/3/4 在此基础上扩展。

**任务（v2）**：
1. `provider/transport.py` —— `ProviderTransport` 枚举
2. `provider/auth_resolver.py` —— `AuthResolver` / `StaticApiKeyResolver` / `OAuthResolver` / `ResolvedAuth`
   - **F2 修复**：`OAuthResolver.resolve()` 每次从 store 重读 profile，不持有 credential 快照
3. `provider/provider_runtime.py` —— `ProviderRuntime` dataclass（frozen）
4. `provider/provider_client.py` —— `ProviderClient` 类
   - 仅实现 `_call_openai_responses`（复用 ResponsesApiProvider 现有代码 95%）
   - **F3 修复**：401 **和** 403 都触发 force_refresh + retry 1 次
5. `provider/provider_router.py` —— `ProviderRouter`
   - **F1 修复**：``resolve_for_alias(alias, task_scope=...)`` 同 task scope 内钉死
6. `provider/__init__.py` 导出新 API
7. ~~feature flag~~（**F4 修复**：v2 不引入 flag，Phase 1 完全不接主链路）

**新增文件**：6 个（4 个核心 + 2 个测试）
**修改文件**：1 个（`__init__.py` 导出 + 给 PkceOAuthAdapter 加 `credential` / `profile_name` 两个 property）

**测试**：
- `test_provider_router_resolve.py`（4 条：alias 找到 / alias 不存在 / provider 不存在 / provider 禁用）
- `test_oauth_resolver.py`（3 条：resolve 走 PkceOAuthAdapter / force_refresh 重试 / refresh 失败返回 None）
- `test_provider_client_responses.py`（5 条：调通 / 401 触发 force_refresh / 二次 401 抛错 / extra_headers 注入 / extra_body merge）

**Phase 1 commit**：`feat(provider): Feature 080 Phase 1 — Provider 直连抽象层 + OpenAI Responses transport`

---

### Phase 2 — OPENAI_CHAT transport + 多 alias 路由

**目标**：覆盖 SiliconFlow / DeepSeek / Groq 等所有 OpenAI Chat 兼容 provider。

**任务**：
1. `ProviderClient._call_openai_chat`（复用 ChatCompletionsProvider 现有代码 95%）
2. `StaticApiKeyResolver`（已有，不需要改）
3. `ProviderModelClient` 支持多 alias 并发（cheap / rerank 等）
4. config_schema.py 接受 `transport: openai_chat` + `auth.kind: api_key`
5. 测试：cheap (siliconflow) + rerank (siliconflow) 都走新链路

**测试**：
- `test_provider_client_chat.py`（5 条：调通 / 401 retry / 流式 tool_call / system msg merge / usage 提取）
- `test_static_api_key_resolver.py`（3 条：env 有值 / env 缺失 / refresh 重读 env）

**Phase 2 commit**：`feat(provider): Feature 080 Phase 2 — OpenAI Chat transport + 多 alias 路由`

---

### Phase 3 — ANTHROPIC_MESSAGES transport + Anthropic OAuth

**目标**：覆盖 Claude Pro / Max OAuth + Anthropic 直接 API key。

**任务**：
1. `ProviderClient._call_anthropic_messages`（**新增** ~300 行）：
   - 请求 body 转换（messages 格式不同 / system 单独字段）
   - 流式事件解析（`message_start` / `content_block_*` / `message_delta`）
   - tool_use 累积
   - thinking 支持
2. `octoagent/packages/provider/src/octoagent/provider/auth/oauth_provider.py` 新增 `anthropic-claude` 的 OAuthProviderConfig（认证端点 / scopes / extra_api_headers）
3. 复用 PkceOAuthAdapter（不动）
4. 测试：Anthropic Claude OAuth 调通 + Anthropic API key 调通

**测试**：
- `test_provider_client_anthropic.py`（6 条：调通 / 401 retry / 流式 tool_use / system 顶层字段 / thinking / usage）
- `test_anthropic_oauth_resolver.py`（2 条：resolve 包含 anthropic-beta header / refresh）
- 完整跑 003-b 系列的 anthropic_messages provider 测试

**Phase 3 commit**：`feat(provider): Feature 080 Phase 3 — Anthropic Messages transport + Claude OAuth`

---

### Phase 4 — LiteLLM Proxy 退役 + Migration + 前端清理

**目标**：完全删除 LiteLLM Proxy 相关代码和配置。

**任务**：

#### 后端
1. **删除**：
   - `octoagent.gateway.services.config.litellm_generator`（整个文件）
   - `octoagent.gateway.services.config.litellm_runtime`（整个文件）
   - `octoagent.gateway.services.runtime_activation.RuntimeActivationService`（部分 —— 只保留非 Proxy 部分）
   - `ProxyManager` 类（在 main.py 等处的引用）
   - `LiteLLMClient`（旧路径）— gateway/main.py 不再创建它
2. **重命名**：`LiteLLMSkillClient` → `ProviderModelClient`（保留旧名作 deprecation alias）
3. **删除**：`docker-compose.litellm.yml`
4. config_schema.py 删除 `RuntimeConfig` 类（或简化保留 `RuntimeConfig` 但不含 LiteLLM 字段）

#### Migration
5. `octoagent/.../config/migration_080.py`：
   - 检测 `config_version == 1`
   - 备份 `octoagent.yaml` → `octoagent.yaml.bak.080-{timestamp}`
   - 备份 `litellm-config*.yaml` → `*.bak.080-{timestamp}`
   - 按 provider.api_base / id 推断 transport：
     - `chatgpt.com/backend-api` → `openai_responses`
     - `api.siliconflow.cn` / `api.openai.com` 等 → `openai_chat`
     - `api.anthropic.com` → `anthropic_messages`
   - 把 `auth_type` + `api_key_env` 转成 `auth: {kind, env|profile}`
   - 写新 schema，`config_version: 2`
   - log.warning 报告迁移完成 + 备份位置

#### 前端
6. `octoagent/frontend/src/types/index.ts` 更新 ProviderEntry 类型
7. `octoagent/frontend/src/domains/settings/SettingsProviderSection.tsx` 增加 transport 选择 + auth.kind 切换
8. 删除 LiteLLM 相关的字段输入（`runtime.litellm_proxy_url` / `runtime.master_key_env`）
9. 默认 provider preset 模板更新（buildProviderPreset）

#### 文档 + 清理
10. `docs/blueprint/*` 同步更新模型调用层架构描述
11. `CLAUDE.md` 移除 LiteLLM Proxy 相关说明
12. 删除 `~/.octoagent/.env.litellm` 的依赖（用户已生成的可以不管，但代码不再读）

#### 测试
13. `test_migration_080.py`（5 条：检测旧版本 / 迁移成功 / api_base 推断 / OAuth profile 关联 / 备份创建）
14. 所有 Feature 078 / 079 现有测试继续通过（约 110 条）
15. 完整 e2e：起新 Gateway，三种 provider（codex OAuth / siliconflow API key / anthropic OAuth）都能调通

**Phase 4 commit**：`feat(provider): Feature 080 Phase 4 — LiteLLM Proxy 退役 + 配置自动迁移 + 前端清理`

---

## 5. 关键设计决策（与备选对比）

### 5.1 为什么 `transport` 字段而不是 N 个 ProviderClient 子类

**备选**：每个 transport 一个 client 类（`OpenAIChatClient` / `OpenAIResponsesClient` / `AnthropicMessagesClient`）。

**选择 transport 字段**理由：
- 后续加 transport 不需要新增类，只需新增方法 + 枚举值
- 测试只用一组 fixture（一个 ProviderClient + 不同 runtime）
- 协议层逻辑（流式 / 错误分类）天然内聚

**代价**：单类长度会比较大（~700 行）—— 但 `ChatCompletionsProvider` 也是 ~400 行，可控。

### 5.2 为什么 alias 解析每次都现读 yaml

**备选 A**：启动时 frozen，改 yaml 后必须重启 Gateway。
**备选 B**：用 watcher（inotify）监听 yaml 变化。

**选择"每次现读"**理由：
- 用户痛点："改了 main 没生效"是 Feature 079 P3 的核心场景
- yaml 体量小（< 2KB），现读成本可忽略（每次任务 1 次解析 ~ 1ms）
- 不引入 watcher 依赖（跨平台 / 容器场景麻烦）
- ProviderClient 缓存复用避免每次都构造 PkceOAuthAdapter

**代价**：极端情况下用户改 yaml 时正在调用，可能跨配置（罕见，可接受）。

### 5.3 为什么 ResolvedAuth 不是 Optional

**备选**：`resolve()` 失败返回 `None`，调用方 if 判断。

**选择 raise 异常**理由：
- 401 / refresh 失败是真异常，不是正常 None
- 调用方代码更线性，少一层 if
- 与 PkceOAuthAdapter.resolve() 现有契约一致

**代价**：force_refresh 失败时还是返回 None（不是 raise），因为 401 retry 路径下"失败"是预期分支（回退到原始 401）。

### 5.4 为什么 OAuthResolver 内嵌 PkceOAuthAdapter（不是组合）

**备选**：OAuthResolver 接受 `Callable[[], Awaitable[str]]` refresh 函数（更松耦合）。

**选择内嵌**理由：
- PkceOAuthAdapter 已经是 OAuth 标准适配，没必要再包一层
- Feature 078 的 reused_recovery / store_reload 等逻辑都在 adapter 里，复用价值最大
- 类型安全：OAuthResolver 可以直接访问 `adapter.credential.account_id`

**代价**：OAuthResolver 强依赖 PkceOAuthAdapter；如果将来要支持非 PKCE OAuth 需要新 Resolver 类（可接受）。

### 5.5 为什么 transport 名字用 `openai_chat` 而不是 `chat_completions`

**备选**：用 OpenAI API 名字（`chat_completions` / `responses_api` / `messages`）。

**选择 `openai_chat` 风格**理由：
- transport 可被多个 provider 复用（SiliconFlow 用 openai_chat 不代表它是 OpenAI），但**协议是 OpenAI Chat 协议**，名字必须包含来源
- 与 Hermes 的 `transport: openai_chat | anthropic_messages | codex_responses` 命名一致，便于参考交叉
- 未来加 `cohere_chat` / `mistral_chat` 等也清晰

### 5.6 为什么 `auth.profile` 引用 `auth-profiles.json` 的 profile 名

**备选**：在 `auth` 里 inline OAuth credential（重复）。

**选择 `profile` 引用**理由：
- 凭证在 auth-profiles.json 是 Constitution C5（Least Privilege）+ Feature 003-b 的契约
- 一个 OAuth profile 可以被多个 provider entry 引用（理论上罕见，但保留可能性）
- Migration 后 profile 名不变（`openai-codex-default`），最小入侵

---

## 6. Phase 之间的依赖

```
Phase 1（抽象层 + Responses）
    ↓ 验证设计正确性 + 用户日常路径不挂
Phase 2（OpenAI Chat + 多 alias）
    ↓ 覆盖所有 API key provider
Phase 3（Anthropic Messages）
    ↓ 覆盖 Claude OAuth
Phase 4（退役 LiteLLM + Migration + 前端）
    ↓ 删旧代码 + 上线
```

**Phase 1-3 和 LiteLLM Proxy 共存**：通过 feature flag `OCTOAGENT_USE_DIRECT_PROVIDER=true` 切换。开发期可以反复对比验证。

**Phase 4 才删除 LiteLLM Proxy**：在 Phase 1-3 充分验证后才动刀。

每个 Phase 独立可 commit，Phase 1 即可立即缓解今天的问题（用户 main → gpt-5.5 不再走 Proxy）。

---

## 7. 测试策略

| Phase | 新增测试文件 | 关键 case 数 |
|-------|------------|------------|
| P1 | provider_router / oauth_resolver / provider_client_responses | ~12 |
| P2 | provider_client_chat / static_api_key_resolver | ~8 |
| P3 | provider_client_anthropic / anthropic_oauth_resolver | ~8 |
| P4 | migration_080 + e2e（3 provider 链路） | ~10 |

**预计新增 ~38 条测试**，覆盖率策略：每个 transport 都覆盖（happy path / 401 retry / 错误分类 / extra_headers 注入 / 流式 tool_call）。

**回归保底**：所有 Feature 078 / 079 测试（约 110 条）必须继续通过。

---

## 8. 风险与缓解（实施期补充）

| 风险 | 缓解 |
|------|------|
| Phase 1-3 期间 feature flag 切换出 bug | 双链路并存，可一键 rollback |
| Migration 把用户 yaml 弄坏 | 必须备份；迁移失败时保留旧 schema 兼容模式（用户可手工修复） |
| Anthropic Messages API 协议细节遗漏（thinking / tool_use 边界 case） | 单独测试覆盖 + 用 Anthropic 官方 SDK 文档对照 |
| 不同 provider 的错误响应格式不一致（OpenAI vs Anthropic vs SiliconFlow） | `_classify_provider_error` 按 transport 分别处理 |
| Token usage 字段名不一致（OpenAI `usage`, Anthropic `usage` 但字段名不同） | 各 transport 内部归一化为 `{prompt_tokens, completion_tokens, total_tokens}` |

---

## 9. Scope Lock（不改的东西）

- `auth-profiles.json` schema 不变
- `PkceOAuthAdapter` / OAuth flow / `oauth_flows.py` 不变
- `TokenRefreshCoordinator` 不变（OAuthResolver 复用它）
- 所有 EventType 枚举不变（OAUTH_*, MODEL_CALL_*）
- Skill / Tool / SkillRunner 接口不变
- `octoagent.yaml` 的 model_aliases / memory / channels / front_door 等字段不变
- CLI 命令接口（`octo config provider list` 等）不变
- 不引入 bedrock / vertex / google_gemini transport（留给后续）
- 不引入凭证池轮换（Hermes 风格）
- 不在本 Feature 加 cost calculator（仅 token usage 透传）

---

## 10. 全量验收 checklist（Phase 4 完成后）

### 功能
- [ ] Gateway 启动 ≤ 5 秒（无 LiteLLM Proxy 子进程）
- [ ] 改 octoagent.yaml `main` 模型 → 下个 task 立即生效（无重启）
- [ ] OAuth token 过期 → 自动 refresh → 继续工作
- [ ] 三种 provider（Codex OAuth / SiliconFlow API key / Anthropic OAuth）都能调通
- [ ] 报错信息直接反映 provider 原始错误（不再 cooldown_list）

### 配置
- [ ] `~/.octoagent/octoagent.yaml` + `~/.octoagent/auth-profiles.json` 是仅有的两份配置
- [ ] `litellm-config*.yaml` / `.env.litellm` 不再被代码读取
- [ ] config_version: 2

### 架构
- [ ] LiteLLM Proxy 进程不再启动
- [ ] `ProxyManager` 类不存在
- [ ] LLM 调用栈深度 ≤ 2 层（Skill → Provider）
- [ ] `octoagent.yaml` schema 移除 `runtime.llm_mode` / `litellm_proxy_url` / `master_key_env`

### 兼容性
- [ ] 现有用户旧 yaml 自动迁移到新 schema（带备份）
- [ ] CLI 命令照常工作
- [ ] 所有 Feature 078 / 079 现有测试通过

### 文档
- [ ] `docs/blueprint/` 模型调用层章节同步
- [ ] `CLAUDE.md` 移除 LiteLLM 相关描述
- [ ] 新增 `docs/codebase-architecture/provider-direct-routing.md`

---

## 11. 总结

| 维度 | 现状 (有 LiteLLM Proxy) | Feature 080 后 |
|------|------------------------|----------------|
| 配置 source of truth | 3 份 yaml 互相漂移 | 仅 `octoagent.yaml` + `auth-profiles.json` |
| OAuth refresh 端到端有效性 | ❌ 被 Proxy 黑洞吞掉 | ✅ Provider 层直接生效 |
| Feature 078 1500 行救火代码价值 | 大部分在对抗 Proxy | 全部都在 ProviderClient 直接生效 |
| Gateway 启动时间 | Gateway + LiteLLM Proxy 两进程 | 仅 Gateway 一进程 |
| LLM 调用栈深度 | 3 层（Skill → Proxy → Provider） | 2 层（Skill → Provider） |
| 用户报错可读性 | "No deployments available, cooldown_list=[hash]" | "OpenAI 401 invalid_token" |
| 新增 provider 的成本 | 改 yaml + LiteLLM 配置 + 重启 Proxy | 改 yaml 一处 |
| 代码行数（净变化） | — | -800 (删 LiteLLM 相关) +500 (Provider 直连) = **-300 净减** |

**预计总用时**：3 天（Phase 1=1 天 / Phase 2=半天 / Phase 3=1 天 / Phase 4=半天）。
