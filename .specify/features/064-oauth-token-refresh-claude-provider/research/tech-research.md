# 技术调研报告: OAuth Token 自动刷新 + Claude 订阅 Provider 支持

**特性分支**: `claude/competent-pike`
**调研日期**: 2026-03-19
**调研模式**: 在线（Perplexity Web Search + 本地代码分析）
**产品调研基础**: 无（tech-only 独立模式）

> [独立模式] 本次技术调研未参考产品调研结论，直接基于需求描述和代码上下文执行。

## 1. 调研目标

**核心问题**:
- OpenClaw 如何实现 OAuth token 自动刷新？关键机制是什么？
- OpenClaw 如何支持 Claude 订阅用户（Pro/Max/Team）使用？
- OctoAgent 当前 OAuth 实现与理想状态的差距有多大？如何弥补？
- LiteLLM Proxy 的静态配置如何与动态 token 刷新协同工作？

**需求 MVP 范围**:
- Must-have: OpenAI Codex OAuth token 到期后自动刷新（`supports_refresh` 启用）
- Must-have: LiteLLM Proxy 层面的 token 动态传播（刷新后新 token 生效）
- Must-have: API 返回 401/403 时触发主动刷新重试
- Nice-to-have: Claude 订阅用户（通过 setup-token）接入 OctoAgent
- Nice-to-have: 多 Provider profile 并行管理和自动切换

## 2. OpenClaw 刷新机制分析

### 2.1 OAuth 存储模型

OpenClaw 使用 `auth-profiles.json` 作为统一的凭证存储，位于 `~/.openclaw/agents/<agentId>/agent/auth-profiles.json`。该文件支持两种凭证类型：

- `type: "api_key"`: `{ provider, key }` -- 静态 API Key
- `type: "oauth"`: `{ provider, access, refresh, expires, email? }` -- OAuth 凭证含 refresh_token

每个 profile 带有 `expires` 时间戳和 `usageStats`（包含 lastUsed、cooldownUntil、errorCount）。

### 2.2 Token 过期检测与自动刷新

OpenClaw 的刷新策略（来自 `docs/concepts/oauth.md`）：

**运行时刷新逻辑**：
1. 检查 profile 的 `expires` 时间戳
2. 如果 `expires` 在未来 -> 直接使用已存储的 access_token
3. 如果已过期 -> 在 file lock 保护下执行 refresh 并覆写存储的凭证

**关键设计**：刷新流程是自动的，用户通常不需要手动管理 token。这与 OctoAgent 的 PkceOAuthAdapter.refresh() 设计意图一致。

### 2.3 Auth Profile 轮转与 Cooldown

OpenClaw 在 profile 级别实现了 failover 机制（来自 `docs/concepts/model-failover.md`）：

**轮转优先级**：
1. 显式配置的 `auth.order[provider]`
2. 按 profile 类型排序（OAuth 优先于 API Key）
3. 按 `usageStats.lastUsed` 排序（最久未用优先）

**Cooldown 机制**（认证失败时）：
- 指数退避：1min -> 5min -> 25min -> 1h（上限）
- Billing 失败：5h 起步，翻倍增长，24h 上限
- 状态持久化在 `auth-profiles.json` 的 `usageStats` 中

**Session 粘性**：选定的 auth profile 在会话内固定，仅在 session reset、compaction 完成、或 profile 进入 cooldown 时才切换。

### 2.4 Token Sink 设计

OpenClaw 的一个关键设计称为 "token sink"：由于 OAuth Provider 在 login/refresh 时常常会签发新的 refresh_token 并使旧的失效，如果用户同时在 OpenClaw 和 Claude Code / Codex CLI 中登录同一账号，可能导致其中一个被"登出"。`auth-profiles.json` 作为 token sink，确保运行时从唯一位置读取凭证，并通过 profile 路由机制保证确定性。

### 2.5 Gemini CLI OAuth 刷新参考

OpenClaw 的 `google-gemini-cli-auth` 扩展（`extensions/google-gemini-cli-auth/oauth.ts`）展示了完整的 PKCE OAuth 流程：
- `exchangeCodeForTokens()`: 使用 PKCE verifier 交换 token，**明确要求 refresh_token**（缺失时抛出异常）
- 过期时间计算：`Date.now() + expires_in * 1000 - 5 * 60 * 1000`（提前 5 分钟过期）
- 凭证结构：`{ access, refresh, expires, email, projectId }`

## 3. OpenClaw Claude 订阅支持分析

### 3.1 Anthropic 认证方式

OpenClaw 对 Anthropic 支持两种认证路径：

**路径 A: API Key（推荐）**
- 环境变量 `ANTHROPIC_API_KEY`
- 支持 key 轮转：`ANTHROPIC_API_KEYS`（逗号分隔列表）
- 仅在 rate limit（429）时尝试下一个 key

**路径 B: Setup Token（订阅用户）**
- 通过 `claude setup-token` 命令生成 token
- 粘贴到 OpenClaw：`openclaw models auth setup-token --provider anthropic`
- 存储为 token auth profile，**不支持 refresh**
- 模型引用：`anthropic/claude-opus-4-6`

### 3.2 Setup Token 技术细节

根据 Web 搜索和 OpenClaw 文档：

- **Token 格式**: `sk-ant-oat01-*`（access token）和 `sk-ant-ort01-*`（refresh token）
- **Access token 有效期**: 8 小时（`expires_in: 28800`）
- **Refresh 端点**: `https://console.anthropic.com/api/oauth/token`
- **Client ID**: `9d1c250a-e61b-44d9-88ed-5944d1962f5e`
- **Refresh 请求**: `grant_type=refresh_token` + refresh_token + client_id
- **Refresh token 生命周期**: 长寿命，可能无限期（但可能在 refresh 时被轮换）

### 3.3 政策风险

OpenClaw 文档多次强调：

> Anthropic setup-token support is technical compatibility, not a policy guarantee. Anthropic has blocked some subscription usage outside Claude Code in the past.

具体来说，某些 API 调用可能返回：

```
This credential is only authorized for use with Claude Code and cannot be used for other API requests.
```

因此 OpenClaw 推荐 Anthropic 用户优先使用 API Key 而非 setup-token。

### 3.4 架构实现

在 OpenClaw 中，Anthropic 是一个内置的 pi-ai catalog provider，不需要 `models.providers` 配置：
- Provider ID: `anthropic`
- 认证存储在 `auth-profiles.json`
- Setup token flow 是 "paste-token" 模式（无 OAuth 交互流程）

## 4. OctoAgent 现状与差距分析

### 4.1 当前实现概览

OctoAgent 的 auth 包位于 `octoagent/packages/provider/src/octoagent/provider/auth/`，架构如下：

```
AuthAdapter (抽象基类)
  +-- PkceOAuthAdapter (Auth Code + PKCE)

HandlerChain (Chain of Responsibility)
  +-- profile -> store -> env -> fallback(echo)

CredentialStore (JSON 文件持久化)
  +-- auth-profiles.json (filelock + 原子写入)

OAuthProviderRegistry -> OAuthProviderConfig
  +-- BUILTIN_PROVIDERS: openai-codex, github-copilot
```

### 4.2 差距一览表

| 维度 | 当前状态 | 目标状态 | 差距评级 |
|------|---------|---------|---------|
| `supports_refresh` | openai-codex 设为 `False` | 应为 `True` | **关键** |
| Token 刷新逻辑 | `refresh_access_token()` 已实现但未激活 | 激活并验证 | 中等 |
| LiteLLM 静态 token | `.env.litellm` 写入后不更新 | 刷新后动态传播到 Proxy | **关键** |
| API 401/403 重试 | `doctor.py` 检测到 401/403 但不触发刷新 | 拦截错误并触发刷新重试 | **关键** |
| Anthropic Provider | BUILTIN_PROVIDERS 未注册 | 新增 anthropic-claude Provider | 中等 |
| 多 profile 管理 | 支持单 default profile | 支持多 profile + 轮转 | 低（Nice-to-have） |
| Token 过期预检 | `is_expired()` 基于 `expires_at` | 提前 N 分钟刷新（buffer） | 低 |

### 4.3 差距详细分析

#### 差距 1: `supports_refresh=False`（关键）

**位置**: `oauth_provider.py` 第 158 行

```python
BUILTIN_PROVIDERS = {
    "openai-codex": OAuthProviderConfig(
        ...
        supports_refresh=False,  # <-- 阻塞了整个刷新链路
        ...
    ),
}
```

**影响**: `PkceOAuthAdapter.refresh()` 在第 111 行检查此标志，如果为 False 直接返回 None，导致 token 过期后无法自动刷新，用户必须重新授权。

**修复**: 将 `supports_refresh` 改为 `True`。`refresh_access_token()` 函数已完整实现（`oauth_flows.py` 第 323-370 行），包含 curl 调用、JWT 解析、新 refresh_token 回传，只是从未被生产路径触发。

#### 差距 2: LiteLLM Proxy 静态 Token 传播缺失（关键）

**当前流程**:
1. `litellm_generator.py` 生成 `litellm-config.yaml`，api_key 格式为 `os.environ/{env_var}`
2. `.env.litellm` 在首次 OAuth 授权后写入 JWT access_token
3. LiteLLM Proxy 通过 Docker Compose 启动，读取 `.env.litellm` 中的环境变量
4. **问题**: token 刷新后，`.env.litellm` 可更新，但 Proxy 容器内的环境变量不会自动更新

**关键代码路径** (`litellm_generator.py` 第 86-111 行):
```python
if provider_entry.auth_type == "oauth":
    jwt = os.environ.get(provider_entry.api_key_env, "")
    account_id = extract_account_id_from_jwt(jwt) if jwt else ""
    litellm_params["headers"] = {
        k: v.replace("{account_id}", account_id or "")
        for k, v in oauth_cfg.extra_api_headers.items()
    }
```

这段代码在**生成时**提取 account_id，但 token 刷新后需要重新生成 headers。

**OpenClaw 的解决方式**: OpenClaw 不使用 LiteLLM Proxy 作为中间层。它的 pi-ai runtime 直接调用 Provider API，从 `auth-profiles.json` 实时读取凭证。

#### 差距 3: API 错误不触发刷新（关键）

**当前行为**: `client.py` 在 `complete()` 方法中捕获 LLM 调用异常，但只区分连接错误（ProxyUnreachableError）和业务错误（ProviderError），不检测 401/403 并触发 token 刷新。

**期望行为**: 当 Provider API 返回 401/403 时，应该：
1. 识别为 token 过期信号
2. 触发 `PkceOAuthAdapter.refresh()`
3. 更新 LiteLLM 的运行时凭证
4. 重试原始请求

## 5. 架构方案对比

### 方案对比表

| 维度 | 方案 A: Proxy 侧刷新（Hook 模式） | 方案 B: 应用层刷新 + Proxy 重启/热更新 | 方案 C: 绕过 Proxy 直连（OpenClaw 模式） |
|------|----------------------------------|--------------------------------------|----------------------------------------|
| 概述 | 利用 LiteLLM 的 `async_pre_call_hook` 在每次请求前检查 token 有效性 | 在 OctoAgent Kernel 层刷新 token，更新 `.env.litellm` 后重载 Proxy | 对 OAuth Provider 不经过 LiteLLM Proxy，由 client.py 直接调用 Provider API |
| 性能 | 高（无额外网络跳转） | 中（重载 Proxy 有短暂不可用） | 高（减少一跳） |
| 可维护性 | 中（需维护 LiteLLM 自定义 Hook） | 低（Proxy 重载逻辑复杂，容器 env 更新困难） | 高（与 OpenClaw 方案一致，已验证） |
| 学习曲线 | 中（需理解 LiteLLM Hook 机制） | 低（标准 Docker 操作） | 低（复用现有 `_complete_via_responses_api` 路径） |
| 社区支持 | 中（LiteLLM Hook 文档有限） | 低（非标准用法） | 高（OpenClaw 已验证的成熟模式） |
| 适用规模 | 适合多用户共享 Proxy | 适合单用户低频场景 | 适合单用户/个人 OS 场景 |
| 与现有项目兼容性 | 需要自定义 LiteLLM Docker 镜像 | 与现有 Docker Compose 兼容但需要 restart 逻辑 | **完全兼容** -- 现有 Codex 路径已是直连模式 |

### 推荐方案

**推荐**: 方案 C -- 绕过 Proxy 直连（OpenClaw 模式）

**理由**:
1. **OctoAgent 已有直连路径**: `client.py` 的 `_complete_via_responses_api()` 和 `complete()` 方法已支持 `api_base` 和 `api_key` 覆盖参数，OpenAI Codex 已经使用 Responses API 直连 `chatgpt.com/backend-api`
2. **与 OpenClaw 架构一致**: OpenClaw 对 OAuth Provider（OpenAI Codex）同样不经过 LiteLLM 类的 proxy，而是由 runtime 直接读取 `auth-profiles.json` 中的凭证
3. **避免 Proxy 环境变量更新难题**: Docker 容器内的环境变量在运行时无法热更新，方案 A/B 都需要额外机制绕过此限制
4. **复用现有基础设施**: `PkceOAuthAdapter` + `CredentialStore` + `HandlerChain` 的链路已经完整，只需启用 `supports_refresh=True` 并在 `complete()` 调用链中注入 refresh-on-401 逻辑
5. **Constitution 对齐**: "Degrade Gracefully" 原则要求 token 刷新失败时不影响其他 Provider，直连模式天然隔离

**方案 C 的具体技术路径**:

```
调用链: Kernel -> HandlerChain.resolve() -> PkceOAuthAdapter.resolve()
         |                                      |
         | 获取 access_token + routing info       | 过期时自动 refresh()
         v                                      v
LiteLLMClient.complete(api_base=..., api_key=...)
         |
         | 如果 401/403
         v
HandlerChain.resolve(force_refresh=True)
         |
         | 重试
         v
LiteLLMClient.complete(新 token)
```

## 6. Claude 订阅 Provider 实现方案

### 6.1 新增 `anthropic-claude` Provider

在 `BUILTIN_PROVIDERS` 中新增配置：

```python
"anthropic-claude": OAuthProviderConfig(
    provider_id="anthropic-claude",
    display_name="Claude (Subscription)",
    flow_type="auth_code_pkce",  # 或 "setup_token"（新增）
    authorization_endpoint="",  # setup-token 无需此端点
    token_endpoint="https://console.anthropic.com/api/oauth/token",
    client_id="9d1c250a-e61b-44d9-88ed-5944d1962f5e",
    scopes=[],
    supports_refresh=True,
    api_base_url=None,  # 使用标准 Anthropic API 端点
)
```

### 6.2 Setup Token 导入流程

参照 OpenClaw 的 `paste-token` 模式：

1. 用户在本机运行 `claude setup-token`（需要 Claude Code CLI）
2. OctoAgent 提供 `octo auth paste-token --provider anthropic-claude` 命令
3. Token 解析后存入 `auth-profiles.json` 作为 `OAuthCredential`（含 access_token + refresh_token）
4. 自动刷新逻辑复用 `PkceOAuthAdapter.refresh()`

### 6.3 Token 刷新适配

Claude 的 refresh 端点使用标准 OAuth2 `grant_type=refresh_token` 请求。OctoAgent 的 `refresh_access_token()` 已实现此逻辑（通过 curl POST），无需额外修改。唯一差异：

- Claude 的 access_token 不是 JWT（是 `sk-ant-oat01-*` 格式），无法提取 account_id
- 需要跳过 `extract_account_id_from_jwt()` 调用（仅适用于 OpenAI）

### 6.4 LiteLLM 配置集成

Claude 订阅不需要 Codex-style 的 Responses API 直连。可以通过标准 LiteLLM Proxy 路径：

- `litellm_params.model`: `anthropic/claude-sonnet-4-5`（标准 LiteLLM Anthropic provider）
- `litellm_params.api_key`: `os.environ/ANTHROPIC_OAUTH_TOKEN`
- 刷新后需要更新 `.env.litellm` 中的 `ANTHROPIC_OAUTH_TOKEN` 并通知 Proxy

或者（推荐）：采用方案 C 的直连模式，在 `LiteLLMClient.complete()` 的 `api_key` 参数中实时注入最新 token。

## 7. 依赖库评估

### 评估矩阵

| 库名 | 用途 | 当前状态 | 需要新增? | 说明 |
|------|------|---------|----------|------|
| `filelock` | CredentialStore 并发安全 | 已安装 | 否 | 刷新时 store 写入已使用 filelock |
| `httpx` | HTTP 客户端 | 已安装 | 否 | 可替代 curl subprocess 用于 token 刷新 |
| `pydantic` | 数据模型 | 已安装 | 否 | OAuthCredential 等已定义 |
| `structlog` | 结构化日志 | 已安装 | 否 | 事件追踪 |
| `litellm` | LLM 调用 | 已安装 | 否 | 已集成 |
| `python-dotenv` | .env 加载 | 已安装 | 否 | RuntimeActivation 使用 |

**核心结论**: 无需引入新依赖。所有必要的基础设施已存在于项目中。

### 与现有项目的兼容性

| 现有依赖/组件 | 兼容性 | 说明 |
|--------------|--------|------|
| PkceOAuthAdapter | 完全兼容 | 刷新逻辑已实现，只需启用 `supports_refresh` |
| HandlerChain | 完全兼容 | 已支持 adapter refresh，无需修改 |
| CredentialStore | 完全兼容 | 已有 filelock + 原子写入 |
| LiteLLMClient | 需扩展 | 需在 401/403 时触发刷新重试 |
| litellm_generator | 需扩展 | Claude Provider 的配置生成 |
| init_wizard | 需扩展 | 新增 Claude setup-token 引导 |
| OAuthProviderConfig | 需扩展 | 新增 `setup_token` flow_type（可选） |

## 8. 设计模式推荐

### 推荐模式

1. **Chain of Responsibility (已有)**: HandlerChain 已实现凭证解析链，刷新逻辑自然嵌入 PkceOAuthAdapter 的 `resolve()` 方法中。无需修改 Chain 本身。

2. **Retry with Refresh (新增)**: 在 `LiteLLMClient.complete()` 层面引入 refresh-on-error 重试逻辑：
   - 捕获 401/403 响应
   - 调用 HandlerChain 重新 resolve（触发 adapter refresh）
   - 使用新凭证重试原始请求
   - 最多重试 1 次（避免无限循环）

3. **Observer Pattern (已有)**: 通过 `emit_oauth_event()` 发射 `OAUTH_REFRESHED` 事件，其他组件可订阅此事件更新状态（如前端 UI 显示 token 状态）。

### 应用案例

- **OpenClaw 的刷新模式**: 在 profile `expires` 检查中直接执行 refresh，与 OctoAgent 的 `PkceOAuthAdapter.resolve()` 设计一致
- **Azure AD Token Refresh**: LiteLLM SDK 内置的 `AzureADCredential` 使用类似的 pre-call refresh 模式
- **Claude Code CLI**: 在 401 错误时通过 `apiKeyHelper` 刷新，TTL 默认 5 分钟

## 9. 技术风险清单

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---------|------|------|---------|
| 1 | OpenAI Codex OAuth refresh_token 被轮换，导致同时使用 Codex CLI 的用户被登出 | 高 | 中 | 参照 OpenClaw token sink 设计，文档警告用户；刷新后保存新 refresh_token |
| 2 | Anthropic 限制 setup-token 用于非 Claude Code 应用 | 中 | 高 | 明确标注此功能为"技术兼容性"，非官方支持；优先推荐 API Key；提供降级方案 |
| 3 | LiteLLM Proxy 容器内环境变量无法热更新 | 高 | 中 | 采用方案 C 直连模式绕过；或将 token 写入 Proxy 的 virtual key 系统 |
| 4 | curl subprocess 在某些环境下不可用 | 低 | 中 | 迁移 `_curl_post()` 到 httpx（已安装），消除对外部命令的依赖 |
| 5 | Token 刷新期间的并发请求导致多次刷新 | 中 | 低 | 使用 filelock（已有）+ 内存级 asyncio.Lock 确保单次刷新 |
| 6 | JWT access_token 中 account_id 提取失败影响 Codex API 调用 | 低 | 高 | 刷新后重新提取 account_id；失败时保留旧值并记录警告 |

## 10. 需求-技术对齐度

### 覆盖评估

| 需求功能 | 技术方案覆盖 | 说明 |
|---------|-------------|------|
| OpenAI Codex token 自动刷新 | 完全覆盖 | `supports_refresh=True` + 现有 `PkceOAuthAdapter.refresh()` |
| LiteLLM token 动态传播 | 完全覆盖 | 方案 C 直连模式，每次请求从 CredentialStore 实时读取 |
| 401/403 主动刷新重试 | 完全覆盖 | 在 `LiteLLMClient.complete()` 中添加 retry-with-refresh |
| Claude 订阅 setup-token 接入 | 完全覆盖 | 新增 `anthropic-claude` Provider + paste-token 流程 |
| Claude token 自动刷新 | 完全覆盖 | 复用 `refresh_access_token()` + Anthropic OAuth 端点 |
| 多 Provider profile 管理 | 部分覆盖 | HandlerChain 已支持多 profile，但缺少 OpenClaw 级别的轮转/cooldown |

### 扩展性评估

当前技术方案为以下 Nice-to-have 功能预留了扩展空间：

- **Cooldown/退避机制**: 可在 ProviderProfile 中增加 `usageStats` 字段，参照 OpenClaw 实现
- **多账户并行**: CredentialStore 已支持多 profile，只需在 HandlerChain 中增加轮转策略
- **Google Gemini CLI OAuth**: 可复用 PKCE 流程基础设施，新增 Provider 配置即可
- **前端 Token 状态展示**: OAUTH_REFRESHED 事件已有，前端订阅即可

### Constitution 约束检查

| 约束 | 兼容性 | 说明 |
|------|--------|------|
| Durability First | 兼容 | 刷新后的 token 通过 CredentialStore 原子写入持久化 |
| Everything is an Event | 兼容 | OAUTH_REFRESHED / OAUTH_FAILED 事件已定义 |
| Tools are Contracts | 兼容 | OAuthProviderConfig 类型即 contract |
| Side-effect Must be Two-Phase | 部分相关 | token 刷新本身不可逆但可重试，无需 Plan-Gate-Execute |
| Least Privilege by Default | 兼容 | 凭证按 profile 分区，不进 LLM 上下文 |
| Degrade Gracefully | 兼容 | 刷新失败时降级到 echo 模式（已实现） |
| User-in-Control | 兼容 | Anthropic setup-token 需用户主动导入 |
| Observability is a Feature | 兼容 | structlog + Event Store 追踪刷新事件 |

## 11. 结论与建议

### 总结

1. **OctoAgent 的 OAuth 刷新基础设施已经基本完整**，核心差距是配置层面的 `supports_refresh=False` 阻塞了整个链路。修复此标志并验证 `refresh_access_token()` 在生产环境下的行为是最高优先级。

2. **LiteLLM Proxy 的静态 token 问题**可以通过沿用现有的直连模式（方案 C）优雅绕过，无需引入复杂的 Proxy Hook 或容器重启机制。OctoAgent 已有的 Codex Responses API 直连路径为此提供了验证过的基础。

3. **Claude 订阅用户支持**在技术上完全可行：Anthropic 的 OAuth token 端点遵循标准 OAuth2 refresh 流程，`refresh_access_token()` 可直接复用。但存在政策风险（Anthropic 可能限制非 Claude Code 使用），应明确告知用户。

4. **无需引入新依赖**，所有必要的库和基础设施已在项目中。工作量主要在：启用 refresh 标志、401/403 重试逻辑、新 Provider 注册、wizard/CLI 扩展。

### 实现优先级建议

| 优先级 | 任务 | 复杂度 | 预估工作量 |
|-------|------|--------|----------|
| P0 | 将 openai-codex 的 `supports_refresh` 改为 `True` | 极低 | 0.5h |
| P0 | 验证 `refresh_access_token()` + `PkceOAuthAdapter.refresh()` 端到端 | 中 | 2h |
| P0 | 在 `LiteLLMClient.complete()` 中添加 401/403 -> refresh -> retry 逻辑 | 中 | 3h |
| P1 | 新增 `anthropic-claude` Provider 配置 + paste-token 命令 | 中 | 4h |
| P1 | 刷新后更新 `.env.litellm` 并通知 Proxy（非直连路径的兼容方案） | 中 | 2h |
| P2 | 添加 token 过期预检（提前 5 分钟刷新 buffer） | 低 | 1h |
| P2 | 迁移 `_curl_post()` 到 httpx（消除外部依赖） | 低 | 2h |
| P3 | 实现 OpenClaw-style cooldown/退避机制 | 中 | 4h |

### 对后续 Spec 编写的建议

- Spec 应明确区分 "OpenAI Codex 刷新" 和 "Claude 订阅接入" 两个子目标，可分阶段交付
- 重点验证场景：token 过期 -> 自动刷新 -> 重试成功、refresh_token 失效 -> 提示重新授权、并发刷新竞争
- 测试策略：mock token 端点验证刷新逻辑，集成测试验证 LiteLLMClient -> HandlerChain -> PkceOAuthAdapter 的完整调用链
