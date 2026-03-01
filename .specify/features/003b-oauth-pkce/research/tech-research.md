# 技术调研报告: Feature 003-b -- OAuth Authorization Code + PKCE + Per-Provider Auth

> **[独立模式]** 本次技术调研未参考产品调研结论，直接基于需求描述和代码上下文执行。

| 元数据 | 值 |
|--------|-----|
| 特性编号 | 003-b |
| 调研日期 | 2026-03-01 |
| 调研模式 | 独立模式（无前序产品调研） |
| 预设 | quality-first |
| 现有基线 | Feature 003 已交付（Device Flow + AuthAdapter + DX 工具） |

---

## 1. 需求上下文与核心技术问题

### 1.1 需求概述

将 OctoAgent 现有的 RFC 8628 Device Flow OAuth 认证升级为 Authorization Code + PKCE 流程，具体包括：

1. **PKCE 支持**: code_verifier / code_challenge 生成与验证（S256）
2. **本地回调服务器**: `localhost:1455` 接收 OAuth callback
3. **Per-Provider OAuth 配置注册表**: 支持 OpenAI Codex、GitHub Copilot、Google Gemini 等多 Provider
4. **Init wizard 更新**: 支持真实 OAuth PKCE 流程
5. **VPS/Remote 降级**: 无本地浏览器时的手动模式

### 1.2 核心技术问题

| # | 问题 | 优先级 |
|---|------|--------|
| Q1 | Python 生态如何实现 PKCE？纯手写 vs OAuth 库？ | P0 |
| Q2 | 本地回调服务器选型（asyncio vs aiohttp vs stdlib）？ | P0 |
| Q3 | Per-Provider 注册表如何设计？如何扩展新 Provider？ | P0 |
| Q4 | VPS/Remote 环境检测与降级策略？ | P1 |
| Q5 | token 刷新机制如何与现有 AuthAdapter 集成？ | P1 |
| Q6 | 安全考量：CSRF、token 存储、code_verifier 生命周期？ | P1 |

### 1.3 现有技术栈约束（Constitution）

- **语言**: Python 3.12+
- **HTTP 客户端**: httpx（已在 pyproject.toml 中依赖）
- **数据模型**: Pydantic 2.x
- **日志**: structlog
- **CLI**: questionary + rich
- **存储**: JSON 文件 + filelock（CredentialStore 已实现）
- **异步优先**: IO 操作使用 async/await

---

## 2. 参考项目 OAuth 实现分析

### 2.1 OpenClaw -- 最关键参考

OpenClaw 是当前最成熟的多 Provider OAuth CLI 实现，提供了 4 种不同的 OAuth 流程：

#### 2.1.1 OpenAI Codex OAuth（Auth Code + PKCE）

**源码**: `src/commands/openai-codex-oauth.ts` + `@mariozechner/pi-ai` 内部实现

**流程**:
1. 生成 PKCE verifier/challenge + 随机 state
2. 打开 `https://auth.openai.com/oauth/authorize?...`
3. 本地回调服务器监听 `http://127.0.0.1:1455/auth/callback`
4. 远程/无头环境降级到手动粘贴 redirect URL
5. Token 交换 `https://auth.openai.com/oauth/token`
6. 从 access_token 提取 accountId 并存储

**关键设计决策**:
- 使用 `createVpsAwareOAuthHandlers` 统一封装本地/远程两种模式
- `onAuth` callback 处理浏览器打开/URL 展示
- `onPrompt` callback 处理手动输入降级
- 流程异常不中断 onboarding，允许重试或切换 API Key

#### 2.1.2 Google Gemini OAuth（Auth Code + PKCE）

**源码**: `extensions/google-gemini-cli-auth/oauth.ts`

**PKCE 生成** (第 221-225 行):
```typescript
function generatePkce(): { verifier: string; challenge: string } {
  const verifier = randomBytes(32).toString("hex");
  const challenge = createHash("sha256").update(verifier).digest("base64url");
  return { verifier, challenge };
}
```

**本地回调服务器** (第 303-394 行):
- 使用 `node:http.createServer()` 创建临时 HTTP 服务器
- 监听 `localhost:8085` 端口
- 验证 `state` 参数防 CSRF
- 返回 HTML 提示用户关闭窗口
- 超时 5 分钟自动关闭

**VPS 降级** (第 217-219 行):
```typescript
function shouldUseManualOAuthFlow(isRemote: boolean): boolean {
  return isRemote || isWSL2Sync();
}
```

**端口冲突处理** (第 710-729 行):
- 捕获 `EADDRINUSE` 错误
- 自动降级到手动粘贴模式
- 提示用户粘贴完整 redirect URL

#### 2.1.3 Qwen Portal OAuth（Device Flow + PKCE 混合）

**源码**: `extensions/qwen-portal-auth/oauth.ts`

**独特之处**: Device Flow 也使用了 PKCE
```typescript
// Device Code 请求携带 code_challenge
body: toFormUrlEncoded({
  client_id: QWEN_OAUTH_CLIENT_ID,
  scope: QWEN_OAUTH_SCOPE,
  code_challenge: params.challenge,
  code_challenge_method: "S256",
})

// Token 轮询携带 code_verifier
body: toFormUrlEncoded({
  grant_type: QWEN_OAUTH_GRANT_TYPE,
  client_id: QWEN_OAUTH_CLIENT_ID,
  device_code: params.deviceCode,
  code_verifier: params.verifier,
})
```

**启示**: PKCE 不仅适用于 Auth Code Flow，也可用于增强 Device Flow 安全性。

#### 2.1.4 GitHub Copilot（纯 Device Flow，无 PKCE）

**源码**: `src/providers/github-copilot-auth.ts`

标准 RFC 8628 Device Flow，无 PKCE。使用 `@clack/prompts` 的 spinner 提供交互反馈。

#### 2.1.5 Chutes OAuth（Auth Code + PKCE，通用模式）

**源码**: `src/commands/chutes-oauth.ts` + `src/agents/chutes-oauth.ts`

**最具参考价值的通用实现**:
- `generateChutesPkce()`: 标准 PKCE 生成
- `buildAuthorizeUrl()`: 标准 authorize URL 构建
- `waitForLocalCallback()`: 通用本地回调服务器，支持自定义 redirect URI
- `loginChutes()`: 完整 OAuth 流程编排，支持 manual/auto 双模式
- `exchangeChutesCodeForTokens()`: 标准 token 交换
- `refreshChutesTokens()`: Token 刷新

#### 2.1.6 VPS/Remote 环境检测

**源码**: `src/commands/oauth-env.ts`

```typescript
export function isRemoteEnvironment(): boolean {
  if (process.env.SSH_CLIENT || process.env.SSH_TTY || process.env.SSH_CONNECTION) return true;
  if (process.env.REMOTE_CONTAINERS || process.env.CODESPACES) return true;
  if (process.platform === "linux" && !process.env.DISPLAY && !process.env.WAYLAND_DISPLAY && !isWSLEnv()) return true;
  return false;
}
```

**检测维度**:
1. SSH 环境变量（SSH_CLIENT, SSH_TTY, SSH_CONNECTION）
2. 容器/云开发环境（REMOTE_CONTAINERS, CODESPACES）
3. Linux 无图形界面（无 DISPLAY 和 WAYLAND_DISPLAY，且非 WSL）

### 2.2 参考项目对比矩阵

| 维度 | OpenAI Codex | Gemini CLI | Qwen Portal | Chutes | GitHub Copilot |
|------|-------------|------------|-------------|--------|---------------|
| OAuth 流程 | Auth Code + PKCE | Auth Code + PKCE | Device + PKCE | Auth Code + PKCE | Device Flow |
| PKCE 方法 | S256 | S256 | S256 | S256 | N/A |
| verifier 生成 | randomBytes(32).hex | randomBytes(32).hex | randomBytes(32).base64url | randomBytes(32).hex | N/A |
| 本地回调端口 | 1455 | 8085 | N/A（Device Flow） | 自定义（默认 1456） | N/A |
| VPS 降级 | 手动粘贴 URL | 手动粘贴 URL | N/A（Device 天然支持） | 手动粘贴 URL | N/A |
| state 参数 | verifier 复用 | verifier 复用 | N/A | 独立随机值 | N/A |
| client_secret | 无（公开客户端） | 有（从 CLI 提取） | 无 | 可选 | 无 |
| Token 刷新 | 是 | 是 | 是 | 是（完整实现） | 否 |

### 2.3 从参考实现提取的关键设计模式

1. **VPS-Aware OAuth Handlers**: `createVpsAwareOAuthHandlers()` 将 onAuth/onPrompt 抽象为回调接口，上层 OAuth 流程无需关心本地/远程差异
2. **端口冲突优雅降级**: 本地回调服务器失败时自动切换到手动模式
3. **state 防 CSRF**: Chutes 使用独立 state 值（推荐），Gemini 复用 verifier 作为 state（简化但安全性略低）
4. **Token Sink 模式**: 所有 OAuth 凭证写入统一存储，避免多客户端 refresh token 冲突

---

## 3. Python OAuth 库选型分析

### 3.1 候选方案

| 方案 | 库 | 版本 | 许可证 | 下载量（月） |
|------|-----|------|--------|------------|
| A | authlib | 1.4.x | BSD-3 | ~4M |
| B | oauthlib | 3.2.x | BSD-3 | ~25M（但多为间接依赖） |
| C | httpx-oauth | 0.16.x | MIT | ~200K |
| D | 纯 httpx 手写 | N/A | N/A | N/A |

### 3.2 评估矩阵

| 评估维度 | authlib | oauthlib | httpx-oauth | 纯 httpx |
|----------|---------|----------|-------------|----------|
| **PKCE 支持** | 优秀：自动生成 challenge/verifier | 良好：需手动配置 | 良好：支持 PKCE 参数 | 手动：~20 行代码 |
| **async 支持** | 优秀：AsyncOAuth2Client | 差：仅同步 | 优秀：原生 async | 优秀：httpx 原生 |
| **回调服务器集成** | 手动 | 手动 | 手动 | 手动 |
| **维护活跃度** | 活跃（2025 持续发版） | 停滞（12+ 月无发版） | 活跃（2025 有更新） | N/A（httpx 活跃） |
| **依赖体积** | 中（~1.5MB + cryptography） | 轻（~500KB） | 极轻（~100KB） | 最小（仅 httpx） |
| **与项目兼容性** | 好（httpx 后端） | 需额外封装 | 好（httpx 原生） | 完美（已有 httpx） |
| **学习曲线** | 中等 | 较高 | 低 | 最低（但需自行保证安全） |
| **Token 刷新** | 内置 | 内置 | 内置 | 手动实现 |

### 3.3 推荐: 方案 D -- 纯 httpx 手写

**推荐理由**:

1. **与现有代码一致**: Feature 003 的 Device Flow 已用纯 httpx 实现（`oauth.py` 约 160 行），团队已有相同模式的经验
2. **零新增依赖**: 项目已依赖 httpx，PKCE 仅需 Python 标准库（`secrets`, `hashlib`, `base64`），不引入额外包
3. **Constitution 对齐**: "Degrade Gracefully" 原则要求最小依赖，纯 httpx 方案依赖链最短
4. **参考实现验证**: OpenClaw 的所有 OAuth 实现均为纯手写（无第三方 OAuth 库），实践证明 PKCE Auth Code Flow 手写代码量约 100-150 行，可控
5. **完全可控**: 回调服务器、PKCE 生成、token 交换均可针对 OctoAgent 场景定制

**PKCE 核心实现仅需 ~15 行**:
```python
import secrets
import hashlib
import base64

def generate_pkce() -> tuple[str, str]:
    """生成 PKCE code_verifier 和 code_challenge (S256)"""
    verifier = secrets.token_urlsafe(32)  # 43 chars, RFC 7636 要求 43-128
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge
```

**备选**: 如果后续需要 OIDC（OpenID Connect）集成或 JWT 解析，可升级到 authlib。当前 MVP 不需要。

---

## 4. 架构方案选型

### 4.1 方案 A: 统一 OAuthFlow 抽象 + Provider Registry（推荐）

```
                    OAuthProviderRegistry
                    /        |        \
         OpenAICodexProvider  GeminiProvider  CopilotProvider
                    \        |        /
                     OAuthFlowRunner
                    /                \
          LocalCallbackServer    ManualPasteHandler
                    \                /
                     AuthAdapter (existing)
```

**核心组件**:

1. **`OAuthProviderConfig`** (Pydantic Model): Per-Provider 配置
   ```python
   class OAuthProviderConfig(BaseModel):
       provider_id: str                    # "openai-codex"
       display_name: str                   # "OpenAI Codex"
       flow_type: Literal["auth_code_pkce", "device_flow", "device_flow_pkce"]
       authorization_endpoint: str
       token_endpoint: str
       client_id: str | None = None        # 动态生成时为 None
       client_id_env: str | None = None    # 环境变量名
       scopes: list[str]
       redirect_uri: str = "http://localhost:1455/auth/callback"
       redirect_port: int = 1455
       supports_refresh: bool = True
   ```

2. **`OAuthProviderRegistry`**: Provider 注册表
   - 内置默认 Provider 配置（OpenAI Codex, GitHub Copilot 等）
   - 支持通过配置文件或代码注册新 Provider
   - `get_provider(id)` / `list_providers()` / `register(config)`

3. **`OAuthFlowRunner`**: OAuth 流程编排器
   - `run_auth_code_pkce(provider_config, env_context)` -- Auth Code + PKCE
   - `run_device_flow(provider_config)` -- Device Flow（保留现有）
   - 自动检测环境（本地 vs VPS）选择交互模式
   - 流程: PKCE 生成 -> 构建 auth URL -> 启动回调/等待输入 -> token 交换

4. **`LocalCallbackServer`**: 异步本地回调服务器
   - 基于 `asyncio` + stdlib `http.server` 或 `aiohttp`
   - 监听指定端口，处理 OAuth callback
   - state 验证 + 超时关闭 + 端口冲突降级

5. **`EnvironmentDetector`**: 运行环境检测
   - `is_remote()`: 检测 SSH/容器/无 GUI 环境
   - `can_open_browser()`: 检测浏览器可用性

**优势**:
- Provider 注册表解耦了 OAuth 流程与具体 Provider 配置
- OAuthFlowRunner 复用了本地/远程两种交互模式
- 与现有 AuthAdapter/HandlerChain 架构无缝集成
- 扩展新 Provider 仅需注册配置，无需新代码

**劣势**:
- 需要较多新抽象层
- 初始开发量稍大

### 4.2 方案 B: 最小增量改造 -- 在现有 oauth.py 基础上扩展

```
  oauth.py (现有 Device Flow)
       |
  oauth_pkce.py (新增 Auth Code + PKCE)
       |
  codex_oauth_adapter.py (修改: 支持两种 flow)
```

**核心变更**:

1. **新增 `oauth_pkce.py`**: 实现 Auth Code + PKCE 流程
   - `generate_pkce()`: 生成 verifier/challenge
   - `build_authorize_url()`: 构建授权 URL
   - `start_callback_server()`: 启动本地回调服务器
   - `exchange_code_for_token()`: 授权码换取 token
   - `run_pkce_flow()`: 编排完整流程

2. **修改 `codex_oauth_adapter.py`**: 新增 PKCE 流程入口
3. **修改 `init_wizard.py`**: 更新 OAuth 选项为 PKCE 流程
4. **Provider 配置硬编码在各函数参数中**

**优势**:
- 改动量最小，快速交付
- 不引入新抽象层，代码路径清晰

**劣势**:
- 多 Provider 支持需要复制代码或传入大量参数
- 无统一的 Provider 注册表，扩展性差
- 与 OpenClaw 的可扩展设计差距明显

### 4.3 方案对比

| 维度 | 方案 A: 统一抽象 | 方案 B: 最小增量 |
|------|-----------------|-----------------|
| **开发量** | 中（5-7 个新文件） | 小（2-3 个新文件） |
| **可维护性** | 高：关注点分离清晰 | 中：随 Provider 增加退化 |
| **可扩展性** | 高：注册表模式，新 Provider 零代码 | 低：每个 Provider 需独立实现 |
| **学习曲线** | 中：需理解抽象层 | 低：直接读懂 |
| **与现有架构一致性** | 高：复用 AuthAdapter/HandlerChain | 中：部分绕过现有抽象 |
| **测试复杂度** | 中：需 mock Provider 注册表 | 低：直接 mock HTTP |
| **适用规模** | >= 3 Provider | 1-2 Provider |
| **Constitution 对齐** | "Tools are Contracts" -- 配置即合约 | 部分对齐 |

### 4.4 推荐: 方案 A（统一 OAuthFlow 抽象 + Provider Registry）

**推荐理由**:

1. 需求明确要支持 OpenAI Codex、GitHub Copilot、潜在的 Google Gemini 等多 Provider，方案 B 会在第 3 个 Provider 时产生大量重复代码
2. OpenClaw 的实践验证了统一 OAuth 抽象的可行性和价值（`createVpsAwareOAuthHandlers` + Provider 配置分离）
3. Provider Registry 模式符合 Constitution "Tools are Contracts" 原则
4. 初期多投入的开发量在后续 Provider 扩展时会被快速摊薄

---

## 5. 设计模式调研

### 5.1 推荐设计模式

#### 5.1.1 Strategy Pattern -- OAuth 流程策略

**用途**: 封装不同 OAuth 流程（Auth Code + PKCE、Device Flow、Device Flow + PKCE）

```python
class OAuthFlowStrategy(ABC):
    @abstractmethod
    async def authenticate(self, config: OAuthProviderConfig, env: EnvironmentContext) -> OAuthCredential: ...

class AuthCodePkceFlow(OAuthFlowStrategy): ...
class DeviceFlow(OAuthFlowStrategy): ...          # 已有实现
class DeviceFlowPkceFlow(OAuthFlowStrategy): ...  # Qwen 模式
```

**参考**: OpenClaw 的 `loginOpenAICodexOAuth` / `loginGeminiCliOAuth` / `loginQwenPortalOAuth` 虽然不是显式 Strategy，但实质上每个 Provider 对应一种流程策略。

#### 5.1.2 Registry Pattern -- Provider 配置注册表

**用途**: 管理多 Provider 的 OAuth 端点、client_id、scopes 等配置

```python
class OAuthProviderRegistry:
    _providers: dict[str, OAuthProviderConfig] = {}

    @classmethod
    def register(cls, config: OAuthProviderConfig) -> None: ...

    @classmethod
    def get(cls, provider_id: str) -> OAuthProviderConfig | None: ...

    @classmethod
    def list_all(cls) -> list[OAuthProviderConfig]: ...
```

**参考**: OpenClaw 在 `auth-choice.apply.*.ts` 文件中分散实现了类似功能，但没有集中注册表。OctoAgent 可以做得更好。

#### 5.1.3 Template Method Pattern -- OAuth 流程框架

**用途**: 定义 OAuth 流程的骨架，子类/回调实现具体步骤

```python
async def run_oauth_flow(
    config: OAuthProviderConfig,
    on_auth_url: Callable[[str], Awaitable[None]],   # 展示/打开 auth URL
    on_prompt: Callable[[str], Awaitable[str]],        # 手动输入
    on_progress: Callable[[str], None],                # 进度回调
) -> OAuthCredential:
    # 1. 生成 PKCE
    # 2. 构建 auth URL
    # 3. 启动回调服务器 / 等待手动输入
    # 4. Token 交换
    # 5. 返回凭证
```

**参考**: 这是 OpenClaw `loginChutes()` 和 `loginGeminiCliOAuth()` 的共同模式。

#### 5.1.4 Adapter Pattern（已有）-- AuthAdapter 集成

现有 `AuthAdapter` 抽象基类 + `CodexOAuthAdapter` 已实现此模式。新增 `PkceOAuthAdapter` 继承同一接口即可。

### 5.2 模式适用性评估

| 模式 | 适用性 | 风险 | 业界案例 |
|------|--------|------|---------|
| Strategy | 高：天然匹配多 OAuth 流程 | 低：接口简单 | OpenClaw 实质使用 |
| Registry | 高：Provider 数量 >= 3 时价值显著 | 低：纯数据配置 | Pydantic AI model registry |
| Template Method | 高：流程步骤固定，变化在回调 | 中：回调签名需稳定 | OpenClaw loginChutes |
| Adapter | 已有：直接复用 | 无 | 已实现 |

---

## 6. 本地回调服务器实现方案

### 6.1 选型对比

| 方案 | 库 | 优势 | 劣势 |
|------|-----|------|------|
| A | `asyncio` + `http.server` | 零依赖，stdlib 原生 | `http.server` 非 async，需线程包装 |
| B | `aiohttp.web` | 全 async，生产级 | 新增依赖 (~2MB) |
| C | `asyncio.start_server` + 手动 HTTP 解析 | 最轻量，纯 asyncio | 需手动解析 HTTP 请求 |
| D | `uvicorn` + 临时 ASGI app | 项目已有 uvicorn 依赖 | 启动较重 |

### 6.2 推荐: 方案 C -- asyncio.start_server + 手动 HTTP 解析

**理由**:

1. **零新增依赖**: 仅使用 Python 标准库 `asyncio`
2. **参考验证**: OpenClaw 使用 `node:http.createServer()` 是 Node.js 的等价方案
3. **回调服务器极其简单**: 只需处理一个 GET 请求，解析 query string 的 `code` 和 `state`
4. **可控的生命周期**: 接收到回调后立即关闭服务器

**核心实现约 60 行**:
```python
import asyncio
from urllib.parse import urlparse, parse_qs

async def wait_for_callback(
    port: int = 1455,
    path: str = "/auth/callback",
    expected_state: str = "",
    timeout: float = 300.0,
) -> tuple[str, str]:
    """启动临时 HTTP 服务器等待 OAuth callback

    Returns: (code, state)
    Raises: OAuthFlowError on timeout or invalid callback
    """
    result: asyncio.Future[tuple[str, str]] = asyncio.get_event_loop().create_future()

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        data = await reader.read(4096)
        request_line = data.decode("utf-8").split("\r\n")[0]
        # GET /auth/callback?code=xxx&state=yyy HTTP/1.1
        _, url_path, _ = request_line.split(" ", 2)
        parsed = urlparse(url_path)

        if parsed.path != path:
            writer.write(b"HTTP/1.1 404 Not Found\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]

        if not code or state != expected_state:
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        # 返回成功 HTML
        html = "<html><body><h2>OAuth 授权成功</h2><p>可以关闭此窗口。</p></body></html>"
        response = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n{html}"
        writer.write(response.encode())
        await writer.drain()
        writer.close()

        if not result.done():
            result.set_result((code, state))

    server = await asyncio.start_server(handle_client, "localhost", port)
    try:
        async with asyncio.timeout(timeout):
            return await result
    finally:
        server.close()
        await server.wait_closed()
```

### 6.3 端口冲突处理策略

参考 OpenClaw Gemini OAuth 的做法：

1. 尝试绑定默认端口 1455
2. 如果 `OSError`（EADDRINUSE），自动降级到手动粘贴模式
3. 日志记录端口冲突信息

```python
try:
    code, state = await wait_for_callback(port=config.redirect_port, ...)
except OSError as e:
    if "Address already in use" in str(e):
        log.warning("callback_port_in_use", port=config.redirect_port)
        code, state = await manual_paste_flow(auth_url, expected_state)
    else:
        raise
```

---

## 7. VPS/Remote 降级策略

### 7.1 环境检测 -- Python 实现

参考 OpenClaw `oauth-env.ts`，Python 等价实现：

```python
import os
import sys
import shutil

def is_remote_environment() -> bool:
    """检测是否处于远程/无浏览器环境"""
    # 1. SSH 环境
    if any(os.environ.get(k) for k in ("SSH_CLIENT", "SSH_TTY", "SSH_CONNECTION")):
        return True

    # 2. 容器/云开发环境
    if any(os.environ.get(k) for k in ("REMOTE_CONTAINERS", "CODESPACES", "CLOUD_SHELL")):
        return True

    # 3. Linux 无 GUI（排除 WSL）
    if sys.platform == "linux":
        has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        is_wsl = "microsoft" in (os.uname().release or "").lower()
        if not has_display and not is_wsl:
            return True

    return False

def can_open_browser() -> bool:
    """检测是否可以打开浏览器"""
    if is_remote_environment():
        return False
    # 检查 webbrowser 是否可用
    import webbrowser
    try:
        return webbrowser.get() is not None
    except webbrowser.Error:
        return False
```

### 7.2 降级交互流程

**本地模式**（`can_open_browser() == True`）:
1. 自动打开浏览器跳转 auth URL
2. 启动本地回调服务器等待 callback
3. 如果回调超时/端口冲突，降级到手动模式

**远程/VPS 模式**（`is_remote_environment() == True`）:
1. 输出 auth URL 到终端
2. 提示用户在本地浏览器打开 URL 完成授权
3. 用户粘贴 redirect URL 到终端
4. 解析 redirect URL 提取 code 和 state
5. 进行 token 交换

**手动模式交互设计**（参考 OpenClaw）:
```
[info] 您正在远程/VPS 环境中运行。
[info] 请在本地浏览器中打开以下 URL：

  https://auth.openai.com/oauth/authorize?client_id=...&response_type=code&...

[input] 授权完成后，将浏览器地址栏中的 redirect URL 粘贴到此处：
> http://localhost:1455/auth/callback?code=xxx&state=yyy

[success] OAuth 授权成功！
```

---

## 8. Per-Provider 注册表设计

### 8.1 数据模型

```python
class OAuthProviderConfig(BaseModel):
    """Per-Provider OAuth 配置"""
    provider_id: str = Field(description="Provider 唯一标识，如 openai-codex")
    display_name: str = Field(description="展示名称，如 OpenAI Codex")
    flow_type: Literal["auth_code_pkce", "device_flow", "device_flow_pkce"] = Field(
        description="OAuth 流程类型",
    )
    authorization_endpoint: str = Field(description="授权端点")
    token_endpoint: str = Field(description="Token 端点")
    client_id: str | None = Field(default=None, description="Client ID（静态值）")
    client_id_env: str | None = Field(
        default=None,
        description="Client ID 环境变量名（动态获取）",
    )
    scopes: list[str] = Field(default_factory=list, description="请求的 scopes")
    redirect_uri: str = Field(
        default="http://localhost:1455/auth/callback",
        description="回调 URI",
    )
    redirect_port: int = Field(default=1455, description="回调监听端口")
    supports_refresh: bool = Field(default=True, description="是否支持 token 刷新")
    extra_auth_params: dict[str, str] = Field(
        default_factory=dict,
        description="额外的授权请求参数",
    )
```

### 8.2 内置 Provider 配置

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
    # 预留：Google Gemini、Qwen 等
}
```

### 8.3 注册表设计

```python
class OAuthProviderRegistry:
    """OAuth Provider 注册表 -- 管理多 Provider 的 OAuth 配置"""

    def __init__(self) -> None:
        self._providers: dict[str, OAuthProviderConfig] = dict(BUILTIN_PROVIDERS)

    def register(self, config: OAuthProviderConfig) -> None:
        """注册新 Provider"""
        self._providers[config.provider_id] = config

    def get(self, provider_id: str) -> OAuthProviderConfig | None:
        """按 ID 获取 Provider 配置"""
        return self._providers.get(provider_id)

    def list_oauth_providers(self) -> list[OAuthProviderConfig]:
        """列出所有支持 OAuth 的 Provider"""
        return list(self._providers.values())

    def resolve_client_id(self, config: OAuthProviderConfig) -> str:
        """解析 Client ID（静态值或从环境变量获取）"""
        if config.client_id:
            return config.client_id
        if config.client_id_env:
            value = os.environ.get(config.client_id_env)
            if value:
                return value
        raise OAuthFlowError(
            f"无法获取 {config.display_name} 的 Client ID",
            provider=config.provider_id,
        )
```

---

## 9. 安全最佳实践

### 9.1 PKCE 安全

| 要求 | 实现方式 | 参考标准 |
|------|---------|---------|
| code_verifier 长度 | 43-128 字符 | RFC 7636 Section 4.1 |
| code_verifier 熵 | `secrets.token_urlsafe(32)` (256 bit) | RFC 7636 Section 7.1 |
| code_challenge_method | 仅 S256（不使用 plain） | RFC 7636 Section 4.2 |
| code_verifier 生命周期 | 仅在内存中存在，流程结束即丢弃 | 安全实践 |
| code_verifier 存储 | 不持久化、不写日志 | 安全实践 |

### 9.2 CSRF 防护

| 要求 | 实现方式 |
|------|---------|
| state 参数 | 独立随机值（`secrets.token_urlsafe(32)`），不复用 verifier |
| state 验证 | callback 收到 state 必须与预期值完全匹配 |
| state 生命周期 | 与 OAuth 流程绑定，超时自动失效 |

**注意**: OpenClaw Gemini 实现中将 verifier 复用为 state，这是一种简化做法但不推荐。Chutes 实现使用独立 state 更安全。OctoAgent 应采用独立 state。

### 9.3 Token 存储安全

| 要求 | 现有实现 | 需变更 |
|------|---------|--------|
| 文件权限 | `0o600`（已实现） | 无 |
| 原子写入 | 临时文件 + rename（已实现） | 无 |
| SecretStr 包裹 | access_token/refresh_token 使用 Pydantic SecretStr（已实现） | 无 |
| 日志脱敏 | masking.py 已实现 token 脱敏 | 确保新增字段也脱敏 |
| 文件锁 | filelock（已实现） | 无 |

### 9.4 回调服务器安全

| 风险 | 缓解措施 |
|------|---------|
| 端口劫持 | 绑定 `localhost` / `127.0.0.1`，不绑定 `0.0.0.0` |
| 超时 | 默认 5 分钟超时，超时后关闭服务器 |
| 重放攻击 | 收到第一个有效 callback 后立即关闭服务器 |
| 非法请求 | 验证 path 和 state，非匹配请求返回 404/400 |

---

## 10. 依赖库评估

### 10.1 需新增依赖

| 库 | 版本 | 用途 | 评估 |
|-----|------|------|------|
| 无 | - | - | 全部使用 Python 标准库 + 现有 httpx |

### 10.2 现有依赖兼容性

| 依赖 | 版本 | 与 Feature 003-b 兼容性 |
|------|------|------------------------|
| httpx | >= 0.27, < 1.0 | 完全兼容，Token 交换使用 httpx.AsyncClient |
| pydantic | >= 2.10, < 3.0 | 完全兼容，新增 OAuthProviderConfig 模型 |
| structlog | >= 25.1, < 26.0 | 完全兼容，日志记录 |
| questionary | >= 2.0, < 3.0 | 完全兼容，init wizard 交互 |
| rich | >= 13.0, < 14.0 | 完全兼容，CLI 输出 |
| filelock | >= 3.12, < 4.0 | 完全兼容，CredentialStore 已使用 |

**结论**: Feature 003-b 不需要引入任何新的第三方依赖。PKCE 生成使用 Python 标准库（`secrets`, `hashlib`, `base64`），本地回调服务器使用 `asyncio`。

---

## 11. 技术风险清单

| # | 风险 | 概率 | 影响 | 缓解策略 |
|---|------|------|------|---------|
| R1 | OpenAI Codex OAuth 端点变更或 client_id 机制不明确 | 中 | 高 | 参考 OpenClaw 最新实现；client_id 通过环境变量注入，不硬编码 |
| R2 | 本地回调服务器端口 1455 被占用 | 中 | 中 | 实现端口冲突检测 + 自动降级到手动模式（OpenClaw 已验证此策略） |
| R3 | VPS 环境检测误判（false positive/negative） | 低 | 中 | 提供 `--manual-oauth` CLI flag 手动覆盖检测结果 |
| R4 | Token 刷新与多客户端冲突（refresh token 失效） | 中 | 中 | 参考 OpenClaw Token Sink 模式，统一存储点；刷新时加 filelock |
| R5 | PKCE state 与 code_verifier 泄露 | 低 | 高 | 不写入日志/持久化；仅内存中短暂存在；structlog 已有 masking |
| R6 | asyncio 回调服务器与 questionary (prompt_toolkit) event loop 冲突 | 中 | 中 | 参考现有实现：OAuth 流程通过 `asyncio.run()` 独立执行，与 questionary 同步 CLI 隔离 |
| R7 | `webbrowser.open()` 在某些 Linux 桌面环境下静默失败 | 低 | 低 | 捕获异常 + 降级到手动模式（OpenClaw 已使用此模式） |
| R8 | OAuthCredential 模型需扩展但破坏向后兼容 | 低 | 中 | 新增可选字段（如 `account_id`），使用 Pydantic Field(default=None) |

---

## 12. 需求-技术对齐度评估

### 12.1 覆盖度检查

| 需求项 | 技术方案覆盖 | 状态 |
|--------|------------|------|
| PKCE 支持 | 方案 A -- OAuthFlowRunner + generate_pkce() | 完全覆盖 |
| 本地回调服务器 | asyncio.start_server 实现 | 完全覆盖 |
| Per-Provider OAuth 配置 | OAuthProviderRegistry + OAuthProviderConfig | 完全覆盖 |
| Init wizard 更新 | init_wizard.py 修改 _run_oauth 为 PKCE 流程 | 完全覆盖 |
| VPS/Remote 降级 | EnvironmentDetector + ManualPasteFlow | 完全覆盖 |
| Token 刷新 | CodexOAuthAdapter.refresh() 升级 | 完全覆盖 |

### 12.2 可能限制需求扩展的技术点

1. **回调端口固定**: 当前设计每个 Provider 使用固定端口。如果需要同时运行多个 Provider 的 OAuth（极端场景），需要动态端口分配。[推断] 当前阶段不需要。

2. **单 event loop 限制**: `asyncio.run()` 在 questionary 同步上下文中调用时，如果未来 init_wizard 改为全异步，需要重构 OAuth 流程的调用方式。

3. **client_id 动态获取**: OpenAI Codex 的 client_id 可能需要动态生成/发现（OpenClaw 通过 `@mariozechner/pi-ai` 内部处理）。当前设计通过环境变量注入，如果 OpenAI 改变机制可能需要适配。

### 12.3 Constitution 合规检查

| Constitution 条款 | 合规性 | 说明 |
|------------------|--------|------|
| Durability First | 合规 | Token 持久化到 auth-profiles.json |
| Everything is an Event | 合规 | 复用现有 emit_credential_event |
| Tools are Contracts | 合规 | OAuthProviderConfig 即合约 |
| Side-effect Must be Two-Phase | N/A | OAuth 流程本身是可逆的（可重试） |
| Least Privilege by Default | 合规 | token 存储权限 0o600；scopes 最小化 |
| Degrade Gracefully | 合规 | VPS 降级 + 端口冲突降级 + Device Flow 保留 |
| User-in-Control | 合规 | 浏览器授权需用户主动操作 |
| Observability is a Feature | 合规 | structlog 记录 OAuth 流程全链路 |

---

## 13. 推荐实现路径

### Phase 1: 核心 PKCE 基础设施（优先交付）
1. `pkce.py` -- PKCE verifier/challenge 生成
2. `oauth_provider.py` -- OAuthProviderConfig 模型 + Registry
3. `callback_server.py` -- asyncio 本地回调服务器
4. `environment.py` -- 远程环境检测

### Phase 2: OAuth 流程编排
5. `oauth_flows.py` -- Auth Code + PKCE 完整流程（替代 `oauth.py` 中的 Device Flow）
6. 修改 `codex_oauth_adapter.py` -- 集成 PKCE 流程 + Token 刷新
7. 修改 `init_wizard.py` -- OAuth 选项指向 PKCE 流程

### Phase 3: 多 Provider 扩展
8. 注册 OpenAI Codex、GitHub Copilot 等内置 Provider
9. 更新 `chain.py` HandlerChain 支持新 OAuth 适配器
10. 集成测试 + 手动验证

---

## 附录 A: 文件影响列表

| 文件 | 操作 | 说明 |
|------|------|------|
| `auth/pkce.py` | 新增 | PKCE 生成器 |
| `auth/oauth_provider.py` | 新增 | Provider 配置模型 + 注册表 |
| `auth/callback_server.py` | 新增 | 本地 OAuth 回调服务器 |
| `auth/environment.py` | 新增 | 运行环境检测 |
| `auth/oauth_flows.py` | 新增 | Auth Code + PKCE 流程编排 |
| `auth/oauth.py` | 保留 | 现有 Device Flow 不删除，作为备选 |
| `auth/codex_oauth_adapter.py` | 修改 | 集成 PKCE 流程 |
| `auth/credentials.py` | 小修 | OAuthCredential 可选新增 account_id 字段 |
| `dx/init_wizard.py` | 修改 | OAuth 入口改为 PKCE 流程 |
| `pyproject.toml` | 无变更 | 不需要新增依赖 |

## 附录 B: 参考源码索引

| 参考项目 | 文件 | 关注点 |
|---------|------|--------|
| OpenClaw | `src/commands/openai-codex-oauth.ts` | OpenAI Codex OAuth 封装 |
| OpenClaw | `src/commands/oauth-flow.ts` | VPS-aware OAuth handlers |
| OpenClaw | `src/commands/oauth-env.ts` | 远程环境检测 |
| OpenClaw | `src/commands/chutes-oauth.ts` | 通用 Auth Code + PKCE + 回调服务器 |
| OpenClaw | `src/agents/chutes-oauth.ts` | PKCE 生成 + Token 交换 |
| OpenClaw | `extensions/google-gemini-cli-auth/oauth.ts` | Gemini PKCE + 回调 + 端口冲突处理 |
| OpenClaw | `extensions/qwen-portal-auth/oauth.ts` | Device Flow + PKCE 混合 |
| OpenClaw | `src/providers/github-copilot-auth.ts` | GitHub Device Flow |
| OpenClaw | `src/commands/auth-choice.apply.openai.ts` | OpenAI auth 选择流程 |
| OpenClaw | `docs/concepts/oauth.md` | OAuth 概念文档 + Token Sink |
