# Feature Specification: OAuth Authorization Code + PKCE + Per-Provider Auth

**Feature Branch**: `feat/003b-oauth-pkce`
**Created**: 2026-03-01
**Status**: Draft
**Input**: User description: "将 OctoAgent 现有的 OAuth Device Flow 认证改为 Authorization Code + PKCE 流程，支持 PKCE 生成、本地回调服务器、Per-Provider OAuth 注册表、init wizard 更新、VPS/Remote 降级。"
**前序依赖**: Feature 003（Auth Adapter + DX 工具）已交付
**调研基础**: `research/tech-research.md`（技术调研，独立模式）

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 通过 PKCE OAuth 完成 OpenAI Codex 授权（本地环境） (Priority: P1)

作为在本地开发环境工作的开发者，我希望通过浏览器完成 OpenAI Codex 的 OAuth 授权（Authorization Code + PKCE 流程），使得系统自动接收回调并完成 token 交换，整个过程无需我手动复制粘贴任何授权码或 URL。

**Why this priority**: 这是 Feature 003-b 的核心场景。Auth Code + PKCE 替换 Device Flow 是本次特性的首要目标。OpenAI Codex 是 OctoAgent 的主力免费认证通道，必须优先保证其 OAuth 流程可用。本地环境覆盖了大部分开发者的日常使用场景。

**Independent Test**: 可通过运行 `octo init` 选择 OpenAI Codex OAuth 模式，验证系统自动打开浏览器、启动本地回调服务器（localhost:1455）、完成授权后 token 被存入 credential store，再通过 `octo doctor --live` 验证 LLM 调用连通性。

**Acceptance Scenarios**:

1. **Given** 开发者在本地桌面环境中运行 `octo init` 并选择 OpenAI Codex OAuth 模式, **When** 系统触发 Authorization Code + PKCE 流程, **Then** 系统生成 PKCE code_verifier/code_challenge，自动打开浏览器跳转到 OpenAI 授权页面，同时启动本地回调服务器监听 `localhost:1455`。
2. **Given** 用户在浏览器中完成 OpenAI 授权, **When** OpenAI 将授权码通过回调 URL 返回到本地服务器, **Then** 系统自动提取授权码和 state、验证 state 一致性、使用授权码 + code_verifier 换取 access_token 和 refresh_token，并将凭证持久化到 credential store。
3. **Given** 用户完成授权后浏览器显示回调页面, **When** token 交换成功, **Then** 浏览器页面显示"授权成功，可以关闭此窗口"的提示，CLI 终端输出授权成功的确认信息。

---

### User Story 2 - 在 VPS/Remote 环境中通过手动模式完成 OAuth 授权 (Priority: P1)

作为在 VPS、SSH 远程服务器或容器环境中工作的开发者，我希望系统检测到无浏览器环境后，自动提供手动 OAuth 授权模式（输出 URL 并接受粘贴的 redirect URL），使得我在没有本地浏览器的环境中也能完成 OAuth 认证。

**Why this priority**: VPS/Remote 是 OctoAgent 的重要使用场景。如果 OAuth 流程仅支持本地浏览器，远程开发者将完全无法使用 OAuth 认证通道，只能退回到 API Key 模式。降级能力直接影响产品的可用性覆盖面。与 Story 1 共同构成完整的 PKCE OAuth 能力，缺一不可。

**Independent Test**: 可通过设置 `SSH_CLIENT` 环境变量模拟远程环境，运行 `octo init` 选择 OAuth 模式，验证系统输出 auth URL 而非打开浏览器，然后粘贴模拟的 redirect URL 完成授权流程。也可通过 `--manual-oauth` CLI flag 强制触发手动模式。

**Acceptance Scenarios**:

1. **Given** 开发者在 SSH 远程服务器上运行 `octo init` 并选择 OAuth 模式, **When** 系统检测到远程环境（SSH_CLIENT/SSH_TTY 环境变量存在）, **Then** 系统输出完整的授权 URL 到终端，提示用户在本地浏览器中打开该 URL 完成授权。
2. **Given** 用户在本地浏览器完成授权后被重定向到 `localhost:1455/auth/callback?code=xxx&state=yyy`, **When** 用户将浏览器地址栏中的 redirect URL 粘贴到终端, **Then** 系统从粘贴的 URL 中解析出 code 和 state，验证 state 一致性，完成 token 交换并存入 credential store。
3. **Given** 开发者使用 `--manual-oauth` CLI flag, **When** 运行 `octo init`, **Then** 系统无论当前环境是否有浏览器，均强制使用手动粘贴模式。
4. **Given** 开发者在 Docker 容器或 GitHub Codespaces 中运行, **When** 系统检测到容器/云开发环境, **Then** 系统自动降级到手动模式，行为与 SSH 远程环境一致。

---

### User Story 3 - 使用 Per-Provider OAuth 注册表管理多 Provider 配置 (Priority: P1)

作为需要对接多个 LLM Provider 的开发者，我希望系统内置多个 Provider 的 OAuth 配置（端点、client_id、scopes 等），使得我只需选择 Provider 名称即可触发对应的 OAuth 流程，无需手动配置 OAuth 端点信息。

**Why this priority**: Per-Provider 注册表是 Auth Code + PKCE 流程的基础设施。没有统一的 Provider 配置管理，每新增一个 Provider 都需要硬编码端点信息，不可扩展。注册表模式让"选择 Provider → 自动匹配 OAuth 配置 → 执行流程"成为流畅的用户体验。

**Independent Test**: 可通过 `octo init` 查看 Provider 列表，验证每个 OAuth Provider 显示正确的名称和认证模式。选择不同 Provider 后，验证系统使用对应的 OAuth 端点发起授权请求。

**Acceptance Scenarios**:

1. **Given** 开发者运行 `octo init` 并进入 Provider 选择步骤, **When** 系统列出所有可用 Provider, **Then** 每个支持 OAuth 的 Provider 显示其名称和 OAuth 流程类型（如"OpenAI Codex - OAuth PKCE"、"GitHub Copilot - Device Flow"），用户可以清晰区分。
2. **Given** 开发者选择 OpenAI Codex Provider, **When** 系统从注册表获取该 Provider 的 OAuth 配置, **Then** 系统使用正确的授权端点（`auth.openai.com`，Auth Code + PKCE 流程使用此域名；Feature 003 Device Flow 使用的 `auth0.openai.com` 端点仅用于 Device Flow 兼容）、token 端点、scopes 和 redirect URI 发起 PKCE 流程。[AUTO-CLARIFIED: PKCE 使用 auth.openai.com，Device Flow 保留 auth0.openai.com]
3. **Given** 开发者选择 GitHub Copilot Provider, **When** 系统从注册表获取该 Provider 的 OAuth 配置, **Then** 系统使用 Device Flow（GitHub 不支持 Auth Code + PKCE）而非 PKCE 流程，保持与原有行为兼容。
4. **Given** 系统需要获取 Provider 的 Client ID, **When** Client ID 未硬编码在注册表中而是配置为环境变量, **Then** 系统从对应的环境变量（如 `OCTOAGENT_CODEX_CLIENT_ID`）中读取 Client ID，找不到时明确报错并提示用户设置。

---

### User Story 4 - OAuth Token 自动刷新 (Priority: P2)

作为长期使用 OctoAgent 的开发者，我希望 OAuth access_token 过期时系统能使用 refresh_token 自动获取新的 access_token，使得我不需要每次 token 过期都重新走一遍完整的 OAuth 授权流程。

**Why this priority**: Token 刷新提升了长期使用体验。Feature 003 的 `CodexOAuthAdapter.refresh()` 返回 None（未实现刷新），这意味着 token 过期后用户必须重新授权。PKCE 流程获取的 refresh_token 可以被用于实现自动刷新。归为 P2 是因为即使没有自动刷新，用户仍可通过重新授权解决问题。

**Independent Test**: 可通过手动修改 credential store 中的 `expires_at` 为过去时间来模拟 token 过期，然后触发一次 LLM 调用，验证系统自动使用 refresh_token 换取新 access_token 而非抛出过期错误。

**Acceptance Scenarios**:

1. **Given** OAuth access_token 已过期但 refresh_token 仍有效, **When** 系统尝试解析凭证, **Then** 系统自动使用 refresh_token 向 token 端点请求新的 access_token，更新 credential store 中的凭证。
2. **Given** refresh_token 也已失效或不存在, **When** 系统尝试自动刷新, **Then** 系统返回刷新失败，提示用户重新进行 OAuth 授权。
3. **Given** 自动刷新成功, **When** 新 token 写入 credential store, **Then** 系统保证写入的并发安全性和原子性，新凭证的 expires_at 被正确更新。

---

### User Story 5 - 端口冲突时自动降级到手动模式 (Priority: P2)

作为开发者，当本地回调服务器的监听端口已被其他程序占用时，我希望系统自动降级到手动粘贴模式而非报错退出，使得 OAuth 流程不会因为端口冲突而完全不可用。

**Why this priority**: 端口冲突是本地回调服务器的常见故障场景。如果端口被占用就导致 OAuth 流程失败，用户体验将严重受损。自动降级到手动模式是 Constitution C6（Degrade Gracefully）的直接体现。归为 P2 是因为端口冲突不是常态。

**Independent Test**: 可通过在端口 1455 上启动一个占位进程，然后运行 `octo init` 选择 OAuth 模式，验证系统检测到端口冲突后自动切换到手动粘贴模式并给出提示。

**Acceptance Scenarios**:

1. **Given** 开发者在本地运行 `octo init` 触发 OAuth 流程, **When** 本地回调服务器尝试绑定端口 1455 但端口已被占用, **Then** 系统记录端口冲突警告日志，自动降级到手动粘贴模式，告知用户端口被占用并引导手动完成授权。
2. **Given** 系统降级到手动模式, **When** 用户按手动模式流程完成授权, **Then** token 交换和凭证存储的后续流程与正常手动模式完全一致。

---

### Edge Cases

- **EC-1** (关联 FR-001, Story 1): PKCE code_verifier 生成后 OAuth 流程中断（用户关闭浏览器/取消授权） -- code_verifier 仅在内存中存在，流程中断后自动丢弃，不会残留在任何持久化存储中。系统提示用户可以重试或切换到 API Key 模式。
- **EC-2** (关联 FR-003, Story 1): 本地回调服务器超时（用户在 5 分钟内未完成浏览器端授权） -- 回调服务器自动关闭，系统提示授权超时，建议用户重试。
- **EC-3** (关联 FR-002, Story 1): state 参数验证失败（回调中的 state 与预期不匹配） -- 系统拒绝该回调，返回 HTTP 400，在终端提示可能存在 CSRF 风险，建议用户重新发起授权。
- **EC-4** (关联 FR-004, Story 3): Provider 的 Client ID 无法解析（环境变量未设置且注册表无静态值） -- 系统明确报错，提示用户通过环境变量设置 Client ID，或切换到 API Key 模式。
- **EC-5** (关联 FR-005, Story 2): 用户粘贴的 redirect URL 格式错误或不包含 code 参数 -- 系统解析失败后明确提示 URL 格式要求，允许用户重新粘贴。
- **EC-6** (关联 FR-006, Story 4): Token 刷新时 token 端点返回 invalid_grant（refresh_token 已被吊销） -- 系统清除过期凭证，提示用户重新进行完整 OAuth 授权。
- **EC-7** (关联 FR-002, Story 2): 远程环境检测误判（本地 Linux 无 DISPLAY 变量但有浏览器） -- 用户可通过不使用 `--manual-oauth` flag 并手动设置 `DISPLAY` 环境变量来覆盖检测结果，或系统在检测到 Linux 无 GUI 时提供确认提示。[AUTO-RESOLVED: 提供 `--manual-oauth` CLI flag 作为手动覆盖机制，与 OpenClaw 实践一致]
- **EC-8** (关联 FR-003, Story 1): 回调服务器收到非 OAuth 回调的 HTTP 请求（如端口扫描） -- 非 `/auth/callback` 路径的请求返回 HTTP 404；缺少 code/state 参数的请求返回 HTTP 400。仅第一个有效回调被处理，之后服务器立即关闭。
- **EC-9** (关联 FR-001, Story 1): OpenAI 授权端点返回错误（服务暂时不可用） -- 系统报告 OAuth 服务不可用，建议用户稍后重试或切换到 API Key 模式。与 Feature 003 EC-8 行为一致。

---

## Requirements *(mandatory)*

### Functional Requirements

**PKCE 支持**

- **FR-001**: 系统 MUST 支持 RFC 7636 PKCE (Proof Key for Code Exchange) 流程。PKCE 实现包括：(1) 生成满足 RFC 7636 要求的 code_verifier（43-128 字符，256 bit 熵），(2) 使用 S256 方法计算 code_challenge，(3) 在授权请求中携带 code_challenge 和 code_challenge_method=S256，(4) 在 token 交换请求中携带 code_verifier。code_verifier MUST NOT 被持久化到任何存储或写入日志。
  *Traces to: Story 1, Story 2*

**环境检测与交互模式选择**

- **FR-002**: 系统 MUST 提供运行环境检测能力，判断当前环境是否支持本地浏览器交互。检测维度包括：(1) SSH 环境（SSH_CLIENT、SSH_TTY、SSH_CONNECTION 环境变量），(2) 容器/云开发环境（REMOTE_CONTAINERS、CODESPACES、CLOUD_SHELL 环境变量），(3) Linux 无图形界面（无 DISPLAY 和 WAYLAND_DISPLAY 环境变量，且非 WSL）。系统 MUST 支持 `--manual-oauth` CLI flag 允许用户手动覆盖检测结果。
  *Traces to: Story 2; Constitution C7 合规*

**本地回调服务器**

- **FR-003**: 系统 MUST 提供本地 OAuth 回调服务器，监听指定端口（默认 localhost:1455）接收 OAuth 授权回调。回调服务器 MUST 满足：(1) 仅绑定 localhost/127.0.0.1（不绑定 0.0.0.0），(2) 验证回调中的 state 参数与预期值一致，(3) 收到第一个有效回调后立即关闭服务器，(4) 默认超时 5 分钟后自动关闭，(5) 返回 HTML 页面告知用户授权结果。端口被占用时 MUST 自动降级到手动粘贴模式。
  *Traces to: Story 1, Story 5*

**Per-Provider OAuth 配置注册表**

- **FR-004**: 系统 MUST 提供 Per-Provider 的 OAuth 配置注册表，管理每个 Provider 的 OAuth 端点信息。每个 Provider 配置 MUST 包含：(1) Provider 唯一标识和展示名称，(2) OAuth 流程类型（Auth Code + PKCE、Device Flow、Device Flow + PKCE），(3) 授权端点和 Token 端点，(4) Client ID（静态值或环境变量名），(5) 请求的 scopes，(6) 回调 URI 和监听端口，(7) 是否支持 token 刷新。注册表 MUST 内置 OpenAI Codex 和 GitHub Copilot 的默认配置。注册表 SHOULD 支持通过代码注册新 Provider。新引入的 `OAuthProviderConfig` 取代 Feature 003 中的 `DeviceFlowConfig`，成为所有 OAuth Provider 配置的统一数据模型；现有 `DeviceFlowConfig` 在迁移完成后废弃。[AUTO-CLARIFIED: OAuthProviderConfig 统一取代 DeviceFlowConfig]
  *Traces to: Story 3*

**OAuth 流程编排**

- **FR-005**: 系统 MUST 提供 OAuth 流程编排能力，根据 Provider 配置的流程类型自动选择正确的 OAuth 流程。Auth Code + PKCE 流程 MUST 包含以下步骤：(1) 生成 PKCE verifier/challenge 和独立的 state 随机值，(2) 构建授权 URL（包含 client_id、redirect_uri、response_type=code、scope、code_challenge、code_challenge_method、state），(3) 根据环境检测结果选择自动打开浏览器或输出 URL，(4) 启动回调服务器等待回调或接受手动粘贴的 redirect URL，(5) 使用授权码 + code_verifier 向 token 端点请求 access_token/refresh_token，(6) 将凭证持久化到 credential store。现有的 Device Flow 流程 MUST 保留，作为不支持 Auth Code + PKCE 的 Provider 的备选。
  *Traces to: Story 1, Story 2, Story 3*

**OAuth Token 刷新**

- **FR-006**: 对于支持 refresh_token 的 Provider，系统 SHOULD 在 access_token 过期时使用 refresh_token 自动获取新 token。刷新操作 MUST 保证并发安全和写入原子性。刷新成功后 MUST 更新 credential store 中的 access_token 和 expires_at。刷新失败时 MUST 提示用户重新进行 OAuth 授权。
  **接口迁移契约**:
  - 现有 `AuthAdapter.refresh()` 签名为 `async def refresh() -> str | None`，返回 None 表示不支持刷新（Feature 003 基线）。
  - 003-b MUST 扩展 refresh 能力：支持刷新的 Adapter（如 PkceOAuthAdapter）构造时接受 `CredentialStore` 注入，refresh() 内部完成 token 端点请求 + store 写回 + 返回新凭证值。
  - 不支持刷新的 Adapter（如现有 CodexOAuthAdapter）继续返回 None，行为不变。
  - `auth-adapter-api.md` 契约文档 MUST 同步更新 refresh() 的语义描述。
  - 现有 `test_codex_oauth_adapter.py` 和 `test_adapter_contract.py` 中 "refresh returns None" 的测试 MUST 保留（验证向后兼容），同时新增 PkceOAuthAdapter 的刷新成功/失败测试。
  [CLARIFIED: 接口迁移契约 + 向后兼容策略]
  *Traces to: Story 4*

**Init Wizard 更新**

- **FR-007**: `octo init` 交互式引导工具 MUST 更新以支持 PKCE OAuth 流程。当用户选择支持 Auth Code + PKCE 的 Provider 时，`octo init` MUST 触发 PKCE 流程而非 Device Flow。Provider 选择列表 MUST 显示每个 Provider 的 OAuth 流程类型。`octo init` MUST 支持 `--manual-oauth` flag。
  *Traces to: Story 1, Story 2, Story 3; 对齐 Feature 003 FR-007*

**CSRF 防护**

- **FR-008**: OAuth 流程 MUST 使用独立的随机 state 参数（不复用 code_verifier）进行 CSRF 防护。state 参数 MUST 在回调中被严格验证。state 参数与 OAuth 流程生命周期绑定，超时后自动失效。[AUTO-RESOLVED: 采用独立 state 而非复用 verifier，原因是 Chutes 实现验证了独立 state 更安全，且调研结论明确推荐此方案]
  *Traces to: Story 1, Story 2*

**凭证安全**

- **FR-009**: PKCE 相关的安全敏感值（code_verifier、state）MUST NOT 出现在系统日志、Event Store 事件记录或任何持久化存储中。OAuth 流程中的 token 交换请求和响应 MUST 在日志中脱敏处理（使用现有 masking 机制）。
  *Traces to: Story 1; Constitution C5 合规; 对齐 Feature 003 FR-011*

**凭证模型扩展**

- **FR-010**: 现有 OAuthCredential 数据模型 SHOULD 扩展以支持 PKCE 流程特有的信息（如 account_id），新增字段 MUST 为可选字段以保持向后兼容。凭证类型的 Discriminated Union 结构不变。account_id 从 JWT access_token 的 `https://api.openai.com/auth` claim 中提取 `chatgpt_account_id`（与 OpenClaw/pi-ai 一致）；若 JWT 解析失败或 claim 不存在，fallback 到 token 端点响应 JSON 中的 account_id 字段；两者均无则为 None。[UPDATED: JWT 方案对齐 OpenClaw，从 JWT 提取 account_id]
  *Traces to: Story 1; 对齐 Feature 003 FR-001*

**OAuth 事件记录（Constitution C2/C8 合规）**

- **FR-012**: 系统 MUST 为 OAuth 流程的关键步骤生成结构化事件记录。MUST 记录的事件类型包括：(1) `OAUTH_STARTED` — 用户发起 OAuth 流程时（含 provider_id、flow_type、environment_mode），(2) `OAUTH_SUCCEEDED` — token 交换成功时（含 provider_id、token_type、expires_in；token 值脱敏），(3) `OAUTH_FAILED` — OAuth 流程失败时（含 provider_id、failure_reason、failure_stage），(4) `OAUTH_REFRESHED` — token 自动刷新成功时（含 provider_id、new_expires_in）。事件 MUST 复用现有凭证事件记录机制。事件 payload 中 MUST NOT 包含 access_token、refresh_token、code_verifier、state 的明文值。
  *Traces to: Story 1, Story 2, Story 4; Constitution C2, C8 合规*

**Device Flow 保留**

- **FR-011**: 系统 MUST 保留现有的 Device Flow OAuth 实现，作为不支持 Auth Code + PKCE 的 Provider（如 GitHub Copilot）的认证流程。Handler Chain 和 AuthAdapter 接口 MUST 同时支持 PKCE 流程和 Device Flow 流程的 OAuth 凭证。
  **Provider ID 规范（双层映射）**:
  - **规范 ID (canonical_id)**: OAuthProviderRegistry 和 CredentialStore 统一使用，格式 `{vendor}-{product}`，如 `openai-codex`、`github-copilot`。这是系统内部的唯一标识。
  - **显示 ID (display_id)**: init_wizard UI 展示用，格式 `{vendor}`，如 `openai`、`github`。仅在 UI 层使用。
  - **迁移契约**: Feature 003 中现有的 `provider="openai-codex"`（oauth.py）和 PROVIDERS dict 中的 `"openai"` key（init_wizard.py）保持不变；003-b 实现时 MUST 建立 `display_id -> canonical_id` 的显式映射表；现有 CredentialStore 中已存储的 `provider` 值 MUST 通过迁移函数统一为 canonical_id 格式；测试中的 `"codex"`（test_e2e_integration.py）MUST 迁移为 `"openai-codex"`。
  [CLARIFIED: 固化双层 ID 规范 + 迁移契约]
  *Traces to: Story 3; Constitution C6 合规*

### Key Entities

- **OAuthProviderConfig（OAuth Provider 配置）**: 描述一个 LLM Provider 的 OAuth 认证参数的配置对象。包含 Provider 标识、展示名称、OAuth 流程类型、授权端点、Token 端点、Client ID 来源、scopes、回调 URI 等。每个配置代表一种 Provider 的 OAuth 连接方式。
- **OAuthProviderRegistry（OAuth Provider 注册表）**: Provider 配置的集中管理容器。内置多个默认 Provider 配置，支持运行时注册新 Provider。提供按 ID 查询、列举、Client ID 解析等能力。
- **PKCE Pair（PKCE 密钥对）**: 由 code_verifier 和 code_challenge 组成的临时密钥对。仅在 OAuth 流程执行期间存在于内存中，流程结束后立即丢弃。code_challenge 发送给授权服务器，code_verifier 在 token 交换时提交用于验证。
- **LocalCallbackServer（本地回调服务器）**: 临时 HTTP 服务器，在 OAuth 流程期间监听本地端口，接收授权服务器的回调请求。生命周期与 OAuth 流程绑定——流程开始时创建，收到有效回调或超时后销毁。
- **EnvironmentContext（环境上下文）**: 描述当前运行环境特征的信息对象。包含是否为远程环境、是否可打开浏览器、是否强制手动模式等属性。用于决定 OAuth 流程的交互模式。

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 在本地桌面环境中，开发者通过 `octo init` 选择 OpenAI Codex OAuth 后，从浏览器授权到 token 写入 credential store 的全流程可在 60 秒内完成（不含用户在浏览器中的操作时间）。
- **SC-002**: 在 SSH 远程/VPS 环境中，开发者通过手动粘贴 redirect URL 的方式可以成功完成 OAuth 授权流程，token 被正确存入 credential store。
- **SC-003**: `octo init` 的 Provider 选择列表正确展示所有内置 OAuth Provider 及其流程类型，选择任一 Provider 后系统使用对应的 OAuth 端点发起认证。
- **SC-004**: 本地回调服务器端口被占用时，系统在 2 秒内检测到冲突并自动降级到手动模式，不抛出未处理的异常。
- **SC-005**: OAuth 流程执行过程中，系统日志中不出现 code_verifier、state、access_token、refresh_token 的明文值。
- **SC-006**: 支持 refresh_token 的 Provider，access_token 过期后系统可通过 refresh_token 自动获取新 token，无需用户重新授权。
- **SC-007**: 使用 `--manual-oauth` CLI flag 时，无论当前环境检测结果如何，系统均进入手动粘贴模式。
- **SC-008**: 现有的 Device Flow 认证流程（如 GitHub Copilot）在 Feature 003-b 交付后继续正常工作，无回归。
- **SC-009**: OAuth 流程的 OAUTH_STARTED / OAUTH_SUCCEEDED / OAUTH_FAILED / OAUTH_REFRESHED 四种事件类型均有对应的单元测试验证事件生成，且事件 payload 中不包含敏感值明文。

---

## Scope Exclusions

以下内容明确不在 Feature 003-b 范围内：

- **OIDC (OpenID Connect) 完整集成** -- 当前 MVP 不需要完整 OIDC 功能（如 id_token 验证签名、nonce 校验等）。仅做最小化 JWT payload 解码以提取 `chatgpt_account_id`（base64 解码，无签名验证），对齐 OpenClaw/pi-ai 方案。调研结论建议"如后续需要完整 OIDC 集成，可升级到 authlib"。
- **Token Exchange (id_token → API Key)** -- 个人 ChatGPT 账户不支持 Token Exchange（缺少 organization_id）。采用 JWT 方案直连 `chatgpt.com/backend-api`，对齐 OpenClaw/pi-ai。
- **动态端口分配（同时运行多 Provider OAuth）** -- 当前阶段每个 Provider 使用固定端口，极端场景（同时多 Provider OAuth）暂不支持。
- **OAuth client_id 动态发现** -- 当前通过环境变量或硬编码注入 client_id，不实现自动发现机制。
- **GUI 配置界面** -- M1 仅提供 CLI，与 Feature 003 一致。
- **OAuth token 后台自动刷新任务** -- FR-006 实现的是按需刷新（resolve 时检测过期并刷新），不包含后台定时刷新任务（属 M2 范畴）。

---

## Appendix: Constitution Compliance Notes

| Constitution 条款 | 合规要求 | 对应 FR |
| --- | --- | --- |
| C2 (Everything is an Event) | OAuth 流程的关键步骤（授权发起、token 获取、token 刷新、授权失败）记录为事件，复用现有凭证事件记录机制 | FR-005, FR-006（对齐 Feature 003 FR-012） |
| C5 (Least Privilege) | code_verifier 不持久化、不写日志；token 存储使用安全封装类型 + 受限文件权限；OAuth 流程日志中 token 脱敏 | FR-001, FR-009 |
| C6 (Degrade Gracefully) | VPS 降级到手动模式；端口冲突降级到手动模式；Device Flow 保留作为备选 | FR-002, FR-003, FR-011 |
| C7 (User-in-Control) | 浏览器授权需用户主动操作；`--manual-oauth` flag 覆盖自动检测；Provider 选择交互式确认 | FR-002, FR-007 |
| C8 (Observability) | OAuth 流程全链路结构化日志（含脱敏）；成功/失败事件记录到 Event Store | FR-009（对齐 Feature 003 FR-011, FR-012） |

---

## Clarifications

### Session 2026-03-01

**Q1 - state 参数应独立生成还是复用 code_verifier?**

- **状态**: [AUTO-RESOLVED: 独立生成]
- **理由**: 调研结论明确推荐独立 state（参考 Chutes 实现），OpenClaw Gemini 实现中复用 verifier 作为 state 被评估为"简化但安全性略低"。OctoAgent 作为长期项目应采用更安全的方案。

**Q2 - 回调服务器实现方案选择?**

- **状态**: [AUTO-RESOLVED: 零新增依赖的轻量回调服务器]
- **理由**: 调研结论推荐零新增依赖方案（参考 OpenClaw 验证、回调逻辑极简），与项目"最小依赖"原则一致。具体实现技术在 plan 阶段确定。

### Session 2026-03-01 Clarify

**Q3 - Provider 标识命名冲突: "openai" vs "openai-codex"?**

- **状态**: [AUTO-CLARIFIED: 双层 ID 映射]
- **上下文**: Feature 003 的 init_wizard PROVIDERS dict 使用 `"openai"` 作为 key，而 003-b OAuthProviderRegistry 使用 `"openai-codex"` 作为 provider_id。现有 oauth.py poll_for_token 硬编码 `provider="openai-codex"`。
- **选择**: init_wizard 维持粗粒度 UI 标识（`"openai"`），通过内部映射关联到 OAuthProviderConfig 的细粒度 ID（`"openai-codex"`）。CredentialStore 中 profile.provider 统一使用细粒度 ID。
- **理由**: 粗粒度 ID 对用户友好（UI 中选"OpenAI"而非"OpenAI Codex"），细粒度 ID 对注册表精确匹配有意义。双层映射在 init_wizard 中通过 auth_mode -> provider_id 转换即可实现，改动量最小。

**Q4 - Token 刷新后 credential store 更新职责?**

- **状态**: [AUTO-CLARIFIED: Adapter 负责 store 写入]
- **上下文**: FR-006 要求刷新后更新 store，但 AuthAdapter.refresh() 签名仅返回 `str | None`，不涉及 store 写入。HandlerChain._try_profile 调用 refresh() 后不写 store。
- **选择**: 实现 refresh 的 Adapter（如 PkceOAuthAdapter）构造时注入 CredentialStore 实例，refresh() 内部完成 token 端点请求 + store 写回 + 返回新凭证值。HandlerChain 仅负责检测过期并调用 refresh()，不参与 store 写入。
- **理由**: Adapter 最了解凭证结构和更新逻辑，由它负责写回最自然。这也与现有 HandlerChain 的职责边界一致（chain 只做调度，不做 IO）。

**Q5 - OAuthProviderConfig 与现有 DeviceFlowConfig 的关系?**

- **状态**: [AUTO-CLARIFIED: OAuthProviderConfig 统一取代 DeviceFlowConfig]
- **上下文**: Feature 003 有 DeviceFlowConfig（仅含 Device Flow 参数），003-b 引入 OAuthProviderConfig（覆盖所有 OAuth 流程类型）。两者字段有重叠但不完全一致。
- **选择**: OAuthProviderConfig 作为统一配置模型取代 DeviceFlowConfig。现有 Device Flow 逻辑（oauth.py 中的 start_device_flow / poll_for_token）从 OAuthProviderConfig 中提取所需参数。DeviceFlowConfig 在迁移完成后标记废弃。
- **理由**: 统一配置模型避免了两套配置体系共存的维护负担，符合方案 A（统一抽象）的设计方向。OAuthProviderConfig 已包含 DeviceFlowConfig 的所有关键字段。

**Q6 - OpenAI 授权端点域名不一致: auth0.openai.com vs auth.openai.com?**

- **状态**: [AUTO-CLARIFIED: PKCE 使用 auth.openai.com，Device Flow 保留 auth0.openai.com]
- **上下文**: Feature 003 DeviceFlowConfig 默认使用 `auth0.openai.com`；tech-research 和 OpenClaw 参考实现中 Auth Code + PKCE 流程使用 `auth.openai.com`。
- **选择**: Auth Code + PKCE 流程使用 `https://auth.openai.com/oauth/authorize` 和 `https://auth.openai.com/oauth/token`；现有 Device Flow 保留 `auth0.openai.com` 端点不变。两者在 OAuthProviderConfig 中分别注册为不同的 Provider 条目（如需要）。
- **理由**: OpenClaw 的最新实现（2025年活跃维护）已验证 auth.openai.com 为 Auth Code + PKCE 的正确端点。Device Flow 端点不做破坏性变更，保持向后兼容。

**Q7 - OAuthCredential.account_id 字段来源?**

- **状态**: [UPDATED: JWT 方案对齐 OpenClaw/pi-ai]
- **上下文**: FR-010 提到新增 account_id 但未定义来源。Tech research 引用 OpenClaw "从 access_token 提取 accountId"。实际验证发现 Token Exchange 对个人账户不可用（缺少 organization_id），需要 JWT 方案。
- **选择**: 优先从 JWT access_token 的 `https://api.openai.com/auth` claim 中提取 `chatgpt_account_id`（与 OpenClaw/pi-ai `extractAccountId()` 逻辑一致）。JWT 解析仅做 base64url 解码（无签名验证），不引入任何依赖。若 JWT 解析失败，fallback 到 token 端点响应 JSON 中的 account_id 字段。
- **理由**: 实际测试验证，OpenAI token 端点响应不一定包含 account_id 字段，但 JWT access_token 的 payload 中总是包含 `chatgpt_account_id`。OpenClaw/pi-ai 已验证此方案可行。最小化 JWT 解码（不验证签名）不构成完整 OIDC 集成。

**Q8 - Token Exchange vs JWT 直连方案?**

- **状态**: [DECIDED: JWT 直连方案]
- **上下文**: 最初实现了 Token Exchange 流程（id_token → sk-... API Key），但发现个人 ChatGPT Pro/Max 账户的 id_token 中没有 organization_id，导致 Token Exchange 返回 HTTP 401 "Invalid ID token: missing organization_id"。
- **选择**: 放弃 Token Exchange，改用 JWT 方案：OAuth access_token (JWT) 直接作为 Bearer token 调用 `chatgpt.com/backend-api/codex/responses`，附带 `chatgpt-account-id`、`OpenAI-Beta` 等特殊 headers。此方案对齐 OpenClaw/pi-ai 的实现。
- **理由**: (1) 个人账户无 organization_id 是 OpenAI 平台限制，非代码 bug; (2) OpenClaw/pi-ai 已在生产环境验证 JWT 直连方案可行; (3) JWT 方案无需额外的 Token Exchange 步骤，流程更简洁。OAuthProviderConfig 新增 `api_base_url` 和 `extra_api_headers` 字段支持此模式。

### Session 2026-03-01 实现阶段补充

**Q9 - 多认证路由隔离: JWT 路径如何绕过 LiteLLM Proxy 而不影响其他认证方案?**

- **状态**: [IMPLEMENTED]
- **上下文**: JWT OAuth 路径需要直连 `chatgpt.com/backend-api`（Responses API），而 API Key 路径继续走 LiteLLM Proxy。用户未来可能绑定多个账号/票据，不同调用使用不同认证方式，必须确保路径间互不干扰。
- **选择**: 三层路由隔离设计：
  - (1) `HandlerChainResult` 扩展 `api_base_url: str | None` 和 `extra_headers: dict[str, str]`，JWT 路径填充路由覆盖信息，API Key 路径保持默认值 (None / {})
  - (2) `LiteLLMClient.complete()` 新增 keyword-only 参数 `api_base`、`api_key`、`extra_headers`，支持按调用覆盖路由
  - (3) `HandlerChain._extract_routing()` 通过 duck-typing 从 adapter 提取路由信息（仅 `PkceOAuthAdapter` 实现了 `get_api_base_url()` / `get_extra_headers()`）
- **理由**: 覆盖参数优先于实例默认值（`resolved_api_base = api_base or self._proxy_base_url`），非 OAuth adapter 不产生路由覆盖，完全零影响。

**Q10 - ChatGPT Backend API（Codex Responses API）的请求约束?**

- **状态**: [DISCOVERED: E2E 验证]
- **上下文**: E2E 测试中发现 `chatgpt.com/backend-api/codex/responses` 有以下硬约束。
- **发现**:
  - 模型名为 `gpt-5.3-codex`（非 codex-mini / o4-mini / codex-mini-latest）
  - 必须 `"stream": true`（否则返回 HTTP 400 "Stream must be set to true"）
  - 必须 `"store": false`（否则返回 HTTP 400 "Store must be set to false"）
  - 请求格式为 Responses API: `{model, instructions, input: [{type: "message", role, content: [{type: "input_text", text}]}]}`，非 Chat Completions 格式
  - Reasoning 配置通过 `reasoning: {effort: "high", summary: "auto"}` 对象传递

**Q11 - Codex Reasoning/Thinking 模式如何配置?**

- **状态**: [IMPLEMENTED]
- **上下文**: Codex 模型支持 reasoning effort 级别从 none 到 xhigh。
- **选择**:
  - 新增 `ReasoningConfig` Pydantic 模型（`effort: Literal["none", "low", "medium", "high", "xhigh"]` + `summary: Literal["auto", "concise", "detailed"] | None`）
  - `LiteLLMClient.complete()` 新增 `reasoning: ReasoningConfig | None` 参数，Chat Completions API 路径传递 `reasoning_effort` 顶层参数
  - `ReasoningConfig.to_responses_api_param()` 返回 Responses API 格式的 `reasoning` 对象
  - E2E 脚本 `--reasoning-effort` / `--reasoning-summary` CLI 参数
- **理由**: 双路径适配——LiteLLM SDK (Chat Completions) 使用 `reasoning_effort` 字符串，直连 API (Responses API) 使用 `reasoning` 嵌套对象。统一配置模型避免调用方关心 API 差异。
