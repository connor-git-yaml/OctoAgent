# Feature Specification: OAuth Token 自动刷新 + Claude 订阅 Provider 支持

**Feature Branch**: `claude/competent-pike`
**Feature ID**: 064
**Created**: 2026-03-19
**Status**: Draft
**Input**: 实现 OAuth token 自动刷新机制，修复 OpenAI Codex token 失效后不刷新的问题。同时支持 Claude 订阅用户（通过 setup-token）接入 OctoAgent。

---

## User Scenarios & Testing

### User Story 1 - OpenAI Codex Token 过期后无感续期 (Priority: P1)

用户正在使用 OctoAgent 进行日常对话或任务执行，底层 Provider 为 OpenAI Codex（通过 OAuth 授权）。当 Codex 的 access_token 过期时，系统应自动使用 refresh_token 获取新的 access_token，用户无需手动重新授权，对话/任务不中断。

**Why this priority**: 这是当前系统最直接的痛点 -- token 过期后用户被迫重新走 OAuth 授权流程，严重影响持续使用体验。修复此问题是整个 Feature 的核心价值。

**Independent Test**: 使用一个即将过期的 Codex access_token 发起 LLM 请求，验证系统自动刷新 token 并成功返回结果，用户全程无感知。

**Acceptance Scenarios**:

1. **Given** 用户已通过 OAuth 授权 OpenAI Codex，access_token 已过期但 refresh_token 仍有效，**When** 用户发起一次对话请求，**Then** 系统自动刷新 access_token，请求成功返回结果，用户无需任何额外操作。
2. **Given** 用户已通过 OAuth 授权 OpenAI Codex，access_token 已过期且 refresh_token 也已失效，**When** 用户发起一次对话请求，**Then** 系统提示用户需要重新授权（而非静默失败或返回不明确的错误信息）。
3. **Given** 系统正在处理一个长时间运行的任务（如 coding-agent），**When** 任务执行期间 access_token 过期，**Then** 后续的 LLM 调用自动刷新 token，任务继续执行不中断。

---

### User Story 2 - API 错误触发主动刷新重试 (Priority: P1)

在某些场景下，token 可能在过期时间之前被 Provider 侧吊销（例如用户在其他客户端重新授权导致 refresh_token 轮换）。当 Provider API 返回认证失败错误时，系统应自动尝试刷新 token 并重试请求，而非直接返回错误给用户。

**Why this priority**: 仅依赖本地 expires_at 判断不够可靠。API 层面的认证失败是 token 无效的最终确认信号，必须作为刷新触发条件。这是可靠刷新机制的必要补充。

**Independent Test**: 模拟 Provider API 返回 401 状态码，验证系统触发刷新并成功重试。

**Acceptance Scenarios**:

1. **Given** 用户的 access_token 尚未到本地记录的过期时间，但 Provider 侧已吊销该 token，**When** LLM 请求返回 401 认证失败，**Then** 系统自动刷新 token 并使用新 token 重试请求，最多重试一次。
2. **Given** LLM 请求返回 403 权限不足错误，**When** 该错误可能由 token 失效引起，**Then** 系统尝试刷新 token 并重试一次；如果重试后仍然 403，则将错误信息返回给用户。
3. **Given** LLM 请求返回 401，且刷新 token 也失败（refresh_token 无效），**When** 重试机制触发，**Then** 系统不进入无限重试循环，而是在一次刷新-重试失败后将错误传达给用户，并建议重新授权。

---

### User Story 3 - Token 刷新后无需重启即刻生效 (Priority: P1)

刷新后的 token 必须立即在后续请求中生效。用户无需执行任何手动操作（如重启服务或重新配置），刷新后的凭证自动应用于所有后续请求。

**Why this priority**: 如果刷新后的 token 无法及时生效，刷新机制本身就毫无意义。凭证即时生效是自动刷新体验闭环的关键保障。

**Independent Test**: 刷新 token 后立即发起多次请求，验证每次请求都使用最新的 access_token。

**Acceptance Scenarios**:

1. **Given** token 刚刚完成刷新，**When** 紧接着发起下一次 LLM 请求，**Then** 请求使用新的 access_token，而非缓存中的旧 token。
2. **Given** 多个并发请求同时检测到 token 过期，**When** 它们同时尝试刷新，**Then** 只有一个刷新操作实际执行，其余请求等待刷新完成后使用新 token。
3. **Given** OAuth Provider 的 token 刚刚完成刷新，**When** 用户在刷新后立即发起请求，**Then** 新 token 无需重启任何系统组件即可在后续请求中生效。

---

### User Story 4 - Claude 订阅用户通过 Setup Token 接入 (Priority: P2)

拥有 Claude 订阅（Pro/Max/Team）的用户可以通过 Claude Code CLI 生成 setup-token，将其导入 OctoAgent，从而使用自己的 Claude 订阅额度调用 Claude 模型。导入后，token 的刷新和管理由系统自动处理。

**Why this priority**: 扩展了 OctoAgent 的 Provider 支持范围，让拥有 Claude 订阅的用户无需额外购买 API 额度即可使用。但此功能存在政策风险（Anthropic 可能限制 setup-token 用于非 Claude Code 应用），因此优先级低于核心刷新机制。

**Independent Test**: 用户粘贴 Claude setup-token，验证系统正确存储凭证并能成功调用 Claude API，且 8 小时后 access_token 自动刷新。

**Acceptance Scenarios**:

1. **Given** 用户已在本机安装 Claude Code CLI 并生成 setup-token，**When** 用户通过 OctoAgent 的 token 导入功能粘贴 token（包含 access_token 和 refresh_token），**Then** 系统将凭证存储为 Claude Provider 的 OAuth profile，并提示用户导入成功。
2. **Given** 用户已导入 Claude setup-token 且 access_token 已过期（有效期约 8 小时），**When** 用户发起使用 Claude 模型的请求，**Then** 系统自动通过 Anthropic 的 OAuth 端点刷新 token，请求成功返回。
3. **Given** 用户导入 Claude setup-token 后尝试调用 Claude 模型，**When** Anthropic 返回"此凭证仅授权用于 Claude Code"的错误，**Then** 系统向用户展示清晰的错误提示，说明订阅凭证可能不支持第三方应用调用，并建议使用 API Key 作为替代方案。

---

### User Story 5 - Token 过期预检（提前刷新缓冲） (Priority: P3)

系统在 token 即将过期时（例如过期前 5 分钟）主动刷新，而非等到 token 真正过期或被 API 拒绝后才触发刷新。这进一步减少用户请求被延迟的概率。

**Why this priority**: 属于体验优化。核心刷新机制（过期后刷新 + 401 重试）已能保证功能正确性，预检刷新是减少刷新延迟的"锦上添花"。

**Independent Test**: 设置一个 5 分钟后过期的 token，在过期前 5 分钟内发起请求，验证系统提前刷新。

**Acceptance Scenarios**:

1. **Given** 用户的 access_token 将在 5 分钟内过期，**When** 用户发起一次 LLM 请求，**Then** 系统在处理请求前先刷新 token，使用新 token 完成请求，避免因 token 过期导致的重试延迟。

---

### Edge Cases

- **并发刷新竞争**: 多个请求同时检测到 token 过期并尝试刷新时，系统如何保证只执行一次刷新操作？（关联 FR-005）
- **Refresh token 轮换冲突**: 用户同时在 Codex CLI 和 OctoAgent 中使用同一 OAuth 账号，Provider 在刷新时签发新 refresh_token 并使旧的失效，可能导致其中一方被"登出"。（关联 US-1 场景 2）
- **网络中断期间的刷新**: 刷新请求因网络问题失败时，系统是否会在下次请求时重新尝试？（关联 FR-003）
- **无效的 setup-token 格式**: 用户粘贴了格式错误或不完整的 token 时，系统如何提示？（关联 US-4）
- **非 OAuth Provider 的请求不受影响**: OAuth token 刷新机制不得影响非 OAuth Provider（如使用 API Key 的 Provider）的正常调用。（关联 FR-007）
- **刷新成功但新 token 立即无效**: Provider 侧异常导致刷新返回的 access_token 无法使用，系统不应陷入"刷新-失败-刷新"循环。（关联 US-2 场景 3）

---

## Requirements

### Functional Requirements

**Token 自动刷新核心**

- **FR-001**: 系统 MUST 在 OAuth access_token 过期时，自动使用 refresh_token 向 Provider 端点请求新的 access_token，无需用户介入。
- **FR-002**: 系统 MUST 在 LLM 调用收到 401（认证失败）或 403（权限不足）响应时，触发 token 刷新并使用新 token 重试原始请求，最多重试一次。
- **FR-003**: 系统 MUST 在刷新操作失败时（如 refresh_token 无效、网络错误），向用户返回明确的错误信息并建议重新授权，而非静默失败或返回不明确的技术错误。
- **FR-004**: 系统 MUST 在刷新成功后，将新的 access_token（及可能更新的 refresh_token 和过期时间）持久化存储，确保进程重启后使用最新凭证。
- **FR-005**: 系统 MUST 保证并发场景下同一 Provider 的 token 刷新请求被串行化——同一时刻只能有一个刷新操作实际执行，其他并发请求等待刷新完成后使用新 token。不同 Provider 的刷新操作互不阻塞。

**凭证实时生效**

- **FR-006**: 对于 OAuth Provider，系统 MUST 在每次 LLM 调用时从凭证存储实时读取最新 token 并传递给 Provider API。刷新后的 token 无需重启任何系统组件即可在后续请求中生效。Claude 订阅 Provider 与 OpenAI Codex 采用相同的凭证传递方式。
- **FR-007**: 非 OAuth Provider 的 LLM 调用 MUST 继续通过现有路径正常路由，OAuth 凭证实时读取机制的引入不得影响非 OAuth Provider 的功能。

**Claude 订阅 Provider 支持**

- **FR-008**: 系统 SHOULD 支持通过"粘贴 token"方式导入 Claude 订阅凭证（access_token + refresh_token），并将其存储为 `OAuthCredential` 类型的 Provider profile（而非 `TokenCredential`），以复用 `PkceOAuthAdapter` 的自动刷新链路。导入入口为 CLI 命令 `octo auth paste-token --provider anthropic-claude`，MVP 不提供 Web UI 导入入口。[AUTO-CLARIFIED: 使用 OAuthCredential 而非 TokenCredential -- setup-token 包含 refresh_token 且需要自动刷新，OAuthCredential 的字段集和 PkceOAuthAdapter 链路完全匹配；TokenCredential 虽然在注释中提到 Anthropic Setup Token，但其模型不含 refresh_token 字段，不适合此场景]
- **FR-009**: 系统 SHOULD 对 Claude 订阅凭证复用与 OpenAI Codex 相同的自动刷新逻辑（FR-001 至 FR-005），使用 Anthropic 的 OAuth token 端点进行刷新。
- **FR-010**: 系统 SHOULD 在 Claude 订阅凭证被 Anthropic 拒绝（如返回"仅授权用于 Claude Code"）时，向用户展示清晰的错误提示并建议使用 API Key 替代。[AUTO-RESOLVED: 调研报告明确指出此政策风险存在且 OpenClaw 已有先例，决定在 UI 提示中标注"技术兼容性"并推荐 API Key 作为主要方案]

**体验优化**

- **FR-011**: 系统 MAY 支持 token 过期预检 -- 在 access_token 距过期时间不足缓冲期时提前触发刷新。缓冲期硬编码为 5 分钟（`REFRESH_BUFFER_SECONDS = 300`），与 OpenClaw Gemini CLI OAuth 的 5 分钟提前量一致。[AUTO-CLARIFIED: 硬编码 5 分钟 -- MVP 阶段无需可配，5 分钟是业界通用实践（OpenClaw、Claude Code CLI 均采用此值），后续如有需求可轻松提取为配置项]

**可观测性**

- **FR-012**: 系统 MUST 在 token 刷新成功、刷新失败、重试触发等关键节点生成结构化事件记录，支持后续问题排查和状态展示。

### Key Entities

- **OAuthCredential（OAuth 凭证）**: 代表一组 OAuth 凭证，包含 access_token、refresh_token、过期时间、关联的 Provider 标识。是 token 刷新操作的直接对象。
- **OAuthProviderConfig（Provider 配置）**: 描述一个 OAuth Provider 的连接参数，包含 token 端点、client ID、是否支持刷新等属性。决定了系统对该 Provider 的行为方式。
- **ProviderProfile（Provider 档案）**: 用户与特定 Provider 的绑定关系，包含凭证引用、使用状态。是凭证存储和检索的单位。

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 当 OpenAI Codex 的 access_token 过期时，用户的下一次请求在无需任何手动操作的情况下成功返回结果（刷新 + 重试对用户透明）。
- **SC-002**: 当 Provider API 返回 401/403 认证失败时，系统在一次自动刷新-重试后成功完成请求，或在刷新失败后向用户返回可理解的错误提示（而非原始技术错误码）。
- **SC-003**: Token 刷新成功后，刷新后的凭证在下次请求中立即使用，无需用户手动操作或系统重启。
- **SC-004**: 多个请求同时触发刷新时，只执行一次刷新操作，不出现重复刷新或因刷新冲突导致请求失败。
- **SC-005**: （如 Claude 订阅支持落地）用户导入 Claude setup-token 后，能够在至少一个完整的 token 生命周期（约 8 小时）内持续使用 Claude 模型而无需手动干预。
- **SC-006**: 所有 token 刷新事件（成功和失败）都有结构化事件记录，可在系统日志或事件存储中查询。

---

## Constraints & Assumptions

### 约束

- 本特性不引入任何新的外部依赖，所有必要的基础设施已存在于项目中。
- OAuth Provider 的实时凭证读取机制仅适用于 OAuth 类型的 Provider；非 OAuth Provider 的 LLM 调用路径不受影响。
- Anthropic setup-token 支持属于"技术兼容性"范畴，非 Anthropic 官方支持的用法。系统应在用户面前透明地传达这一点。

### 假设

- OpenAI Codex 的 OAuth refresh 端点遵循标准 OAuth2 refresh_token 流程，且 refresh_token 的有效期长于 access_token。
- Anthropic 的 OAuth token 端点在可预见的未来保持可用。（具体端点 URL 见部署配置文档）
- 用户同一时间只在一个客户端使用同一 OAuth 账号（如果用户同时在 Codex CLI 和 OctoAgent 中使用同一账号，可能因 refresh_token 轮换而导致其中一方被登出，这是已知的限制而非 bug）。[AUTO-RESOLVED: 调研报告将此识别为已知风险（Token Sink 问题），决定通过文档告知用户而非在 MVP 中实现 OpenClaw 级别的 token sink 设计]

---

## Clarifications

### Session 2026-03-19

| # | 问题 | 自动选择 | 理由 | 关联需求 |
|---|------|---------|------|---------|
| 1 | Claude 订阅的 LLM 调用应走直连模式还是 LiteLLM Proxy？调研 6.4 节提到两种路径（Proxy 或直连），而 FR-006 要求所有 OAuth Provider 走直连。 | 统一走直连模式 | Claude 订阅使用 OAuth 凭证（OAuthCredential），凭证需要动态刷新，走 Proxy 会遇到与 Codex 相同的环境变量热更新难题。统一直连也简化了架构 -- 所有 OAuth Provider 一条路径。 | FR-006, FR-008 |
| 2 | Claude setup-token 导入后应存储为 OAuthCredential 还是 TokenCredential？credentials.py 中 TokenCredential 注释提到是给 Anthropic Setup Token 的，但 OAuthCredential 包含 refresh_token 字段。 | OAuthCredential | setup-token 包含 access_token + refresh_token 且需要自动刷新（8h 有效期），这与 OAuthCredential 的字段集完全匹配。TokenCredential 不含 refresh_token，无法复用 PkceOAuthAdapter 的刷新链路。注释中 TokenCredential 对 Anthropic 的描述是早期设计遗留，应在实现时更新注释。 | FR-008, FR-009 |
| 3 | FR-005 中"文件锁 + 内存锁"的锁粒度 -- 是全局单一锁还是 per-provider 锁？ | per-provider asyncio.Lock + 全局 filelock | per-provider 锁避免不同 Provider 的刷新操作互相阻塞（如 Codex 刷新时不应阻塞 Claude 请求）。filelock 已在 CredentialStore 层面是单文件锁，保证跨进程原子写入。 | FR-005 |
| 4 | FR-011 预检缓冲时间是硬编码 5 分钟还是可配置？ | 硬编码 5 分钟 | 5 分钟是 OpenClaw 和 Claude Code CLI 的通用实践，MVP 阶段无需增加配置复杂度。常量 `REFRESH_BUFFER_SECONDS = 300` 便于后续提取为配置项。 | FR-011 |
| 5 | "直连模式"是复用现有 `complete()` 的覆盖参数（api_base/api_key/extra_headers），还是创建独立 HTTP 客户端路径？ | 复用现有 complete() 覆盖参数机制 | OpenAI Codex 已通过 `_complete_via_responses_api` 路径验证了此模式（`api_base` 指向 chatgpt.com，`api_key` 传入 JWT）。Claude 订阅可直接复用 `complete(api_base=..., api_key=...)` 调用 Anthropic API。无需创建独立 HTTP 客户端，减少代码路径分散。 | FR-006 |
