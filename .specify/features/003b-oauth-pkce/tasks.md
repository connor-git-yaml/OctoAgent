# Tasks: Feature 003-b -- OAuth Authorization Code + PKCE + Per-Provider Auth

**Input**: `.specify/features/003b-oauth-pkce/` (plan.md, spec.md, data-model.md, contracts/)
**Prerequisites**: Feature 003 (Auth Adapter + DX) 已交付
**Branch**: `feat/003b-oauth-pkce`

**统计**: 38 个任务 | 5 个 User Stories + 后续增强 | 8 个 Phase | ~58% 可并行

---

## 路径约定

- **源码根**: `octoagent/packages/provider/src/octoagent/provider/`
- **auth 模块**: `{源码根}/auth/`
- **dx 模块**: `{源码根}/dx/`
- **core 枚举**: `octoagent/packages/core/src/octoagent/core/models/enums.py`
- **测试根**: `octoagent/packages/provider/tests/`（扁平结构，所有测试在此目录下）

以下任务描述中的文件路径均以仓库根目录为起点。

---

## Phase 1: Foundational -- PKCE + 环境检测 + Provider 注册表

**Purpose**: 实现三个独立基础模块（无互相依赖），为后续 OAuth 流程编排提供基础设施。

- [x] T001 [P] 实现 PKCE 生成器 -- `packages/provider/src/octoagent/provider/auth/pkce.py`
  - 新增文件，实现 `PkcePair` dataclass（frozen=True, slots=True）
  - 实现 `generate_pkce()`: secrets.token_urlsafe(32) 生成 verifier，S256 计算 challenge
  - 实现 `generate_state()`: 独立 secrets.token_urlsafe(32) 生成 CSRF state
  - 仅依赖 Python 标准库（secrets, hashlib, base64）
  - 对齐 FR-001, FR-008

- [x] T002 [P] 实现环境检测模块 -- `packages/provider/src/octoagent/provider/auth/environment.py`
  - 新增文件，实现 `EnvironmentContext` dataclass（frozen=True, slots=True）
  - 实现 `detect_environment(force_manual: bool = False) -> EnvironmentContext`
  - 检测维度: SSH (SSH_CLIENT/SSH_TTY/SSH_CONNECTION), 容器 (REMOTE_CONTAINERS/CODESPACES/CLOUD_SHELL), Linux 无 GUI (无 DISPLAY/WAYLAND_DISPLAY 且非 WSL)
  - 实现 `is_remote_environment()` 和 `can_open_browser()` 便捷函数
  - `use_manual_mode` 属性: force_manual or is_remote or not can_open_browser
  - 对齐 FR-002

- [x] T003 [P] 实现 OAuth Provider 配置与注册表 -- `packages/provider/src/octoagent/provider/auth/oauth_provider.py`
  - 新增文件，实现 `OAuthProviderConfig`（Pydantic BaseModel）
  - 字段: provider_id, display_name, flow_type (Literal["auth_code_pkce", "device_flow", "device_flow_pkce"]), authorization_endpoint, token_endpoint, client_id/client_id_env, scopes, redirect_uri/redirect_port, supports_refresh, extra_auth_params, poll_interval_s, timeout_s
  - 实现 `to_device_flow_config()` 向后兼容转换
  - 实现 `OAuthProviderRegistry`: _register_builtins(), register(), get(), list_providers(), list_oauth_providers(), resolve_client_id()
  - 定义 `BUILTIN_PROVIDERS` 字典（openai-codex, github-copilot）
  - 定义 `DISPLAY_TO_CANONICAL` 映射表（openai -> openai-codex, github -> github-copilot）
  - 对齐 FR-004, FR-011

- [x] T004 [P] 扩展 OAuthCredential 新增 account_id 字段 -- `packages/provider/src/octoagent/provider/auth/credentials.py`
  - 修改现有文件，在 OAuthCredential 中新增 `account_id: str | None = Field(default=None)`
  - 保持向后兼容：现有数据反序列化不报错
  - 对齐 FR-010

**Checkpoint**: 四个基础模块互相独立，全部可并行。完成后进入 Phase 2。

---

## Phase 2: Foundational -- 回调服务器 + 事件扩展

**Purpose**: 实现回调服务器和事件类型扩展，为 OAuth 流程编排提供前置依赖。

- [x] T005 [P] 实现本地回调服务器 -- `packages/provider/src/octoagent/provider/auth/callback_server.py`
  - 新增文件，实现 `CallbackResult` dataclass（frozen=True, slots=True）
  - 实现 `async wait_for_callback(port, path, expected_state, timeout) -> CallbackResult`
  - 使用 asyncio.start_server，仅绑定 127.0.0.1
  - HTTP 路由: 非 /auth/callback -> 404，缺少 code/state -> 400，state 不匹配 -> 400，成功 -> 200 + HTML
  - 收到第一个有效回调后立即关闭，默认 300s 超时
  - 端口占用抛出 OSError（调用方捕获并降级）
  - 对齐 FR-003

- [x] T006 [P] 扩展事件类型枚举 -- `packages/core/src/octoagent/core/models/enums.py`
  - 修改现有文件，在 EventType 中新增四个枚举值:
    - OAUTH_STARTED = "OAUTH_STARTED"
    - OAUTH_SUCCEEDED = "OAUTH_SUCCEEDED"
    - OAUTH_FAILED = "OAUTH_FAILED"
    - OAUTH_REFRESHED = "OAUTH_REFRESHED"
  - 对齐 FR-012

- [x] T007 [P] 扩展事件发射函数 -- `packages/provider/src/octoagent/provider/auth/events.py`
  - 修改现有文件，新增 `async emit_oauth_event(event_store, event_type, provider_id, payload)`
  - 复用 emit_credential_event 的 Event Store 写入逻辑
  - Payload 不得包含 access_token/refresh_token/code_verifier/state 明文
  - 支持四种 payload 结构: STARTED(provider_id, flow_type, environment_mode), SUCCEEDED(provider_id, token_type, expires_in, has_refresh_token, has_account_id), FAILED(provider_id, failure_reason, failure_stage), REFRESHED(provider_id, new_expires_in)
  - 依赖 T006 的新枚举值
  - 对齐 FR-012

**Checkpoint**: 回调服务器和事件基础设施就绪，OAuth 流程编排可以开始。

---

## Phase 3: User Story 1 -- 通过 PKCE OAuth 完成 OpenAI Codex 授权（本地环境）(Priority: P1) -- MVP

**Goal**: 开发者在本地桌面环境通过 `octo init` 完成 OpenAI Codex 的 Auth Code + PKCE 授权流程，系统自动打开浏览器、启动回调服务器、完成 token 交换并持久化凭证。

**Independent Test**: 运行 `octo init` 选择 OpenAI Codex OAuth 模式 -> 浏览器自动打开 -> 授权后 token 存入 credential store -> `octo doctor --live` 验证连通性。

### Tests for User Story 1

- [x] T008 [P] [US1] PKCE 生成器单元测试 -- `packages/provider/tests/test_pkce.py`
  - 新增文件，验证: verifier 长度 43 字符、challenge 为正确的 S256 哈希、state 独立生成且每次不同
  - 对齐 FR-001

- [x] T009 [P] [US1] 回调服务器单元测试 -- `packages/provider/tests/test_callback_server.py`
  - 新增文件，验证: 成功回调返回 CallbackResult、超时抛出 OAuthFlowError、无效路径返回 404、缺少参数返回 400、state 不匹配返回 400
  - 使用 asyncio 测试客户端模拟 HTTP 请求
  - 对齐 FR-003

### Implementation for User Story 1

- [x] T010 [US1] 实现 OAuth 流程编排 -- `packages/provider/src/octoagent/provider/auth/oauth_flows.py`
  - 新增文件，实现 `OAuthTokenResponse`（Pydantic BaseModel, access_token 使用 SecretStr）
  - 实现 `build_authorize_url(config, client_id, code_challenge, state) -> str`
  - 实现 `async exchange_code_for_token(token_endpoint, code, code_verifier, client_id, redirect_uri) -> OAuthTokenResponse`
  - 实现 `async run_auth_code_pkce_flow(config, registry, env, on_auth_url, on_status) -> OAuthCredential`
    - 完整步骤: 解析 client_id -> 生成 PKCE + state -> 构建 auth URL -> 自动打开浏览器 + 启动回调服务器 -> 验证 state -> token 交换 -> 构建 OAuthCredential
    - 发射 OAUTH_STARTED/OAUTH_SUCCEEDED/OAUTH_FAILED 事件
  - 实现 `async refresh_access_token(token_endpoint, refresh_token, client_id) -> OAuthTokenResponse`
  - 依赖 T001 (pkce), T003 (oauth_provider), T005 (callback_server), T007 (events)
  - 对齐 FR-005, FR-008, FR-009

- [x] T011 [US1] OAuth 流程编排单元测试 -- `packages/provider/tests/test_oauth_flows.py`
  - 新增文件（注意：已有 test_oauth_flow.py 是 Feature 003 Device Flow 测试，本文件为 003-b PKCE 流程测试）
  - 验证: 完整 PKCE 流程（mock httpx + webbrowser + callback）、build_authorize_url 参数正确性、token 交换成功/失败、refresh_access_token 成功/invalid_grant
  - 对齐 FR-005

**Checkpoint**: 本地 PKCE OAuth 流程核心逻辑完成。Story 1 的场景 1-3 可通过 mock 测试验证。

---

## Phase 4: User Story 2 -- VPS/Remote 环境手动 OAuth + User Story 5 -- 端口冲突降级 (Priority: P1 + P2)

**Goal**: 在 SSH/VPS/容器环境中通过手动粘贴 redirect URL 完成 OAuth 授权；端口被占用时自动降级到手动模式。Story 2 和 Story 5 共享手动模式逻辑，合并实现。

**Independent Test**: 设置 `SSH_CLIENT` 环境变量 -> 运行 `octo init` -> 系统输出 auth URL 而非打开浏览器 -> 粘贴模拟的 redirect URL 完成授权。

### Tests for User Story 2 & 5

- [x] T012 [P] [US2] 环境检测单元测试 -- `packages/provider/tests/test_environment.py`
  - 新增文件，验证: SSH 环境检测（SSH_CLIENT/SSH_TTY）、容器检测（CODESPACES/CLOUD_SHELL）、Linux 无 GUI 检测（无 DISPLAY 且非 WSL）、--manual-oauth 强制手动模式、use_manual_mode 属性计算逻辑
  - mock os.environ
  - 对齐 FR-002

### Implementation for User Story 2 & 5

- [x] T013 [US2] 实现手动粘贴流程 -- `packages/provider/src/octoagent/provider/auth/oauth_flows.py`
  - 修改 T010 创建的文件，新增 `async manual_paste_flow(auth_url, expected_state) -> CallbackResult`
  - 输出 auth_url 到终端，等待用户粘贴 redirect URL，解析 code + state，验证 state 一致性
  - 在 `run_auth_code_pkce_flow` 中添加: env.use_manual_mode 判断 -> 调用 manual_paste_flow；端口冲突 (OSError) -> 降级到 manual_paste_flow
  - 对齐 FR-005 (手动模式), FR-003 (端口降级)

- [x] T014 [US2] 手动模式 + 降级场景测试 -- `packages/provider/tests/test_oauth_flows.py`
  - 修改 T011 创建的文件，新增测试用例:
  - 验证: 远程环境使用手动模式、manual_paste_flow URL 解析、端口冲突自动降级、--manual-oauth 强制手动模式
  - 对齐 FR-002, FR-003

**Checkpoint**: Story 2 和 Story 5 完成。PKCE 流程在本地和远程环境均可工作，端口冲突自动降级。

---

## Phase 5: User Story 3 -- Per-Provider OAuth 注册表管理 (Priority: P1)

**Goal**: init_wizard 展示 Provider 列表并按 flow_type 自动分发到 PKCE 或 Device Flow。开发者选择 Provider 后系统使用对应的 OAuth 配置发起认证。

**Independent Test**: 运行 `octo init` -> Provider 列表显示 "OpenAI Codex - OAuth PKCE" 和 "GitHub Copilot - Device Flow" -> 选择 OpenAI 触发 PKCE -> 选择 GitHub 触发 Device Flow。

### Tests for User Story 3

- [x] T015 [P] [US3] Provider 注册表单元测试 -- `packages/provider/tests/test_oauth_provider.py`
  - 新增文件，验证: 内置 openai-codex/github-copilot 配置正确性、register() 新增 Provider、get() 查询、resolve_client_id() 静态值/环境变量/缺失报错、DISPLAY_TO_CANONICAL 映射、to_device_flow_config() 转换
  - 对齐 FR-004

### Implementation for User Story 3

- [x] T016 [US3] 更新 init_wizard 集成 PKCE 流程 -- `packages/provider/src/octoagent/provider/dx/init_wizard.py`
  - 修改现有文件
  - 更新 `AUTH_MODE_LABELS`: "oauth" -> "OAuth PKCE（免费试用，浏览器授权）"
  - 新增 `async _run_oauth_pkce_flow(provider, force_manual) -> OAuthCredential | None`
  - 修改 OAuth 模式分支: 根据 Provider flow_type 分发 PKCE 或 Device Flow
  - 新增 `run_init_wizard()` 的 `manual_oauth: bool = False` 参数
  - 使用 DISPLAY_TO_CANONICAL 映射 UI display_id -> canonical_id
  - 对齐 FR-007

- [x] T017 [US3] 更新 CLI 添加 --manual-oauth flag -- `packages/provider/src/octoagent/provider/dx/cli.py`
  - 修改现有文件，init 命令新增 `--manual-oauth` Option
  - 传递 manual_oauth 参数到 run_init_wizard()
  - 对齐 FR-002

- [x] T018 [US3] init_wizard PKCE 流程测试 -- `packages/provider/tests/test_init_wizard.py`
  - 修改现有文件，新增测试用例:
  - 验证: 选择 OpenAI 触发 PKCE 流程、选择 GitHub 触发 Device Flow、--manual-oauth 参数传递、AUTH_MODE_LABELS 更新
  - 对齐 FR-007

**Checkpoint**: Provider 注册表通过 init_wizard 集成到用户交互流程。Story 1/2/3/5 联合验证完成。

---

## Phase 6: User Story 4 -- OAuth Token 自动刷新 (Priority: P2)

**Goal**: access_token 过期时，PkceOAuthAdapter 使用 refresh_token 自动获取新 token 并回写 CredentialStore，无需用户重新授权。

**Independent Test**: 修改 credential store 中 `expires_at` 为过去时间 -> 触发 LLM 调用 -> 系统自动刷新 token -> 新 token 写入 store。

### Tests for User Story 4

- [x] T019 [P] [US4] PkceOAuthAdapter 单元测试 -- `packages/provider/tests/test_pkce_oauth_adapter.py`
  - 新增文件，验证:
    - resolve() 返回 access_token
    - resolve() 检测过期并自动调用 refresh()
    - refresh() 成功: 请求 token 端点 + 更新内存凭证 + 回写 store + 发射 OAUTH_REFRESHED + 返回新 token
    - refresh() 失败 invalid_grant: 清除凭证、返回 None
    - refresh() 无 refresh_token: 返回 None
    - is_expired() 边界条件
  - mock httpx, CredentialStore
  - 对齐 FR-006

### Implementation for User Story 4

- [x] T020 [US4] 实现 PkceOAuthAdapter -- `packages/provider/src/octoagent/provider/auth/pkce_oauth_adapter.py`
  - 新增文件，继承 AuthAdapter
  - 构造参数: credential (OAuthCredential), provider_config (OAuthProviderConfig), store (CredentialStore), profile_name (str)
  - `async resolve() -> str`: 检测过期 -> 自动刷新 -> 返回 access_token
  - `async refresh() -> str | None`: httpx POST token 端点 (grant_type=refresh_token) -> 更新内存凭证 + store 回写 + 发射 OAUTH_REFRESHED -> 返回新 token；invalid_grant 清除凭证返回 None
  - `is_expired() -> bool`: 基于 expires_at 判断
  - 依赖 T003 (OAuthProviderConfig), T010 (refresh_access_token)
  - 对齐 FR-006

- [x] T021 [US4] 注册 PkceOAuthAdapter 到 HandlerChain -- `packages/provider/src/octoagent/provider/auth/chain.py`
  - 修改现有文件，在 adapter factory 注册中新增 PkceOAuthAdapter 的 factory
  - factory 通过闭包捕获 OAuthProviderConfig + CredentialStore + profile_name
  - _create_adapter 方法无需修改
  - 对齐 FR-011

**Checkpoint**: Token 自动刷新完成。PkceOAuthAdapter 集成到 HandlerChain，长期使用不再需要反复重新授权。

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 向后兼容标记、事件契约测试、集成测试、auth __init__.py 导出更新。

- [x] T022 [P] 标记 DeviceFlowConfig deprecated -- `packages/provider/src/octoagent/provider/auth/oauth.py`
  - 修改现有文件，在 DeviceFlowConfig 类上添加 deprecated docstring 标记
  - 引导使用 OAuthProviderConfig 替代
  - 对齐 FR-011

- [x] T023 [P] OAuth 事件契约测试 -- `packages/provider/tests/test_oauth_events.py`
  - 新增文件，验证:
    - OAUTH_STARTED/SUCCEEDED/FAILED/REFRESHED payload 结构正确
    - payload 中不含 access_token/refresh_token/code_verifier/state 明文
    - emit_oauth_event 正确调用 Event Store
  - mock EventStoreProtocol
  - 对齐 FR-012, SC-005, SC-009

- [x] T024 [P] PkceOAuthAdapter 契约测试 -- `packages/provider/tests/test_adapter_contract.py`
  - 修改现有文件，新增 PkceOAuthAdapter 的接口契约验证
  - 确认实现 resolve()/refresh() 符合 AuthAdapter ABC
  - 对齐 FR-006

- [x] T025 [P] CodexOAuthAdapter 向后兼容回归测试 -- `packages/provider/tests/test_codex_oauth_adapter.py`
  - 验证现有文件中 "refresh returns None" 测试仍然通过
  - 无需修改，仅作为回归确认点
  - 对齐 FR-011, SC-008

- [x] T026 [P] 更新 auth 模块 __init__.py 导出 -- `packages/provider/src/octoagent/provider/auth/__init__.py`
  - 修改现有文件，新增导出:
    - pkce: PkcePair, generate_pkce, generate_state
    - environment: EnvironmentContext, detect_environment
    - oauth_provider: OAuthProviderConfig, OAuthProviderRegistry, BUILTIN_PROVIDERS, DISPLAY_TO_CANONICAL
    - callback_server: CallbackResult, wait_for_callback
    - oauth_flows: OAuthTokenResponse, run_auth_code_pkce_flow, exchange_code_for_token, refresh_access_token, manual_paste_flow, build_authorize_url
    - pkce_oauth_adapter: PkceOAuthAdapter
    - events: emit_oauth_event

- [x] T027 端到端集成测试 -- `packages/provider/tests/test_oauth_e2e.py`
  - 新增文件，验证完整 PKCE 流程:
    - mock OAuth server 返回 token -> CredentialStore 验证凭证写入
    - init_wizard PKCE 流程集成（mock 浏览器 + callback）
    - Device Flow 回归（GitHub Provider 仍正常工作）
    - --manual-oauth 端到端验证
  - 覆盖 SC-001 ~ SC-009

- [x] T028 [P] OAuthCredential account_id 向后兼容测试 -- `packages/provider/tests/test_credentials.py`
  - 修改现有文件，新增测试:
  - 验证: 无 account_id 的旧数据反序列化 -> account_id=None；有 account_id 的数据正确读取
  - 对齐 FR-010

- [x] T029 现有 Device Flow 测试回归验证 -- `packages/provider/tests/test_oauth_flow.py`
  - 验证现有文件中所有 Device Flow 测试仍然通过
  - 无需修改，仅作为回归确认点
  - 对齐 SC-008

- [x] T030 现有 HandlerChain 测试回归验证 -- `packages/provider/tests/test_chain.py`
  - 验证现有文件中所有 HandlerChain 测试仍然通过
  - 无需修改，仅作为回归确认点
  - 对齐 SC-008

- [x] T031 现有 E2E 测试回归验证 -- `packages/provider/tests/test_e2e_integration.py`
  - 验证现有文件中所有集成测试仍然通过
  - 无需修改，仅作为回归确认点
  - 对齐 SC-008

- [x] T032 Provider ID 迁移：CredentialStore 数据兼容 -- `packages/provider/src/octoagent/provider/auth/credentials.py`
  - 修改现有文件，在 CredentialStore.load_profile() 中添加 provider 值规范化逻辑
  - 将旧值 `"codex"` 映射为 canonical_id `"openai-codex"`（读取时自动迁移）
  - 对齐 FR-011 迁移契约

- [x] T033 Provider ID 迁移：测试中的旧 provider 值修正
  - 修改 `packages/provider/tests/test_e2e_integration.py`: `provider="codex"` → `provider="openai-codex"`
  - 修改 `packages/provider/tests/test_adapter_contract.py`: `provider="codex"` → `provider="openai-codex"`
  - 对齐 FR-011 迁移契约

- [x] T034 更新 auth-adapter-api.md 契约文档 -- `.specify/features/003-auth-adapter-dx/contracts/auth-adapter-api.md`
  - 修改现有文件，更新 refresh() 的语义描述
  - 添加 PkceOAuthAdapter 的刷新能力说明（构造注入 CredentialStore、刷新+回写）
  - 对齐 FR-006 接口迁移契约

**Checkpoint**: 所有新增功能和回归测试通过。Feature 003-b 交付就绪。

---

## Phase 8: 集成增强 -- 多认证路由隔离 + Reasoning 配置

**Purpose**: E2E 验证阶段发现的集成需求。JWT OAuth 路径需要绕过 Proxy 直连 API，同时需要支持 Codex 思考模式配置。

- [x] T035 HandlerChainResult 路由覆盖字段 -- `packages/provider/src/octoagent/provider/auth/chain.py`
  - 修改现有文件，HandlerChainResult 新增 `api_base_url: str | None` 和 `extra_headers: dict[str, str]`
  - 新增 `_extract_routing()` 静态方法，duck-typing 检测 adapter 的 `get_api_base_url()` / `get_extra_headers()`
  - 更新 `_try_profile()` 传递 `**self._extract_routing(adapter)` 到结果
  - 对齐 spec Q9（多认证路由隔离）

- [x] T036 LiteLLMClient 路由覆盖参数 -- `packages/provider/src/octoagent/provider/client.py`
  - 修改现有文件，`complete()` 新增 keyword-only 参数: `api_base`, `api_key`, `extra_headers`
  - 路由决策: `resolved_api_base = api_base or self._proxy_base_url`
  - 新增 `TestLiteLLMClientRoutingOverrides` 测试类（5 个测试）
  - 对齐 spec Q9

- [x] T037 ReasoningConfig 模型 + LiteLLMClient 集成 -- `packages/provider/src/octoagent/provider/models.py` + `client.py`
  - models.py: 新增 `ReasoningConfig` Pydantic BaseModel（effort + summary + to_responses_api_param()）
  - client.py: `complete()` 新增 `reasoning: ReasoningConfig | None` 参数
  - __init__.py: 导出 `ReasoningConfig`
  - 新增 `TestReasoningConfig`（9 个测试）+ `TestLiteLLMClientReasoning`（5 个测试）
  - 对齐 spec Q11

- [x] T038 E2E 测试脚本增强 -- `scripts/test_codex_e2e.py`
  - 修改现有文件，新增 `--reasoning-effort` / `--reasoning-summary` CLI 参数
  - 构建 Responses API `reasoning` 对象
  - 默认模型更新为 `gpt-5.3-codex`
  - 对齐 spec Q10, Q11

**Checkpoint**: 404 个测试全部通过。多认证路由隔离 + Reasoning 配置完成。

---

## FR 覆盖映射表

| FR | 描述 | 覆盖任务 |
|----|------|---------|
| FR-001 | PKCE 支持 (RFC 7636) | T001, T008 |
| FR-002 | 环境检测与交互模式选择 | T002, T012, T017 |
| FR-003 | 本地回调服务器 | T005, T009, T013 |
| FR-004 | Per-Provider OAuth 配置注册表 | T003, T015 |
| FR-005 | OAuth 流程编排 | T010, T011, T013, T014 |
| FR-006 | OAuth Token 刷新 | T019, T020, T024, T034 |
| FR-007 | Init Wizard 更新 | T016, T017, T018 |
| FR-008 | CSRF 防护 (独立 state) | T001, T010 |
| FR-009 | 凭证安全 (脱敏) | T010, T023 |
| FR-010 | 凭证模型扩展 (account_id) | T004, T028 |
| FR-011 | Device Flow 保留 + Provider ID 规范 | T003, T021, T022, T024, T025, T029, T032, T033 |
| FR-012 | OAuth 事件记录 | T006, T007, T023 |

**FR 覆盖率**: 12/12 = **100%**

---

## Success Criteria 覆盖映射表

| SC | 描述 | 验证任务 |
|----|------|---------|
| SC-001 | 本地 PKCE 全流程 60s 内完成 | T027 |
| SC-002 | SSH/VPS 手动模式完成授权 | T014, T027 |
| SC-003 | Provider 列表正确展示流程类型 | T018, T027 |
| SC-004 | 端口冲突 2s 内降级 | T014 |
| SC-005 | 日志不含敏感明文 | T023 |
| SC-006 | refresh_token 自动刷新 | T019, T020 |
| SC-007 | --manual-oauth 强制手动模式 | T012, T014, T018 |
| SC-008 | Device Flow 无回归 | T025, T029, T030, T031 |
| SC-009 | 四种 OAuth 事件有单元测试 | T023 |

---

## Dependencies & Execution Order

### Phase 依赖关系

```
Phase 1 (Foundational: PKCE + Environment + Provider + Credential)
  |-- T001, T002, T003, T004 全部可并行
  v
Phase 2 (Foundational: Callback + Events)
  |-- T005, T006 可并行
  |-- T007 依赖 T006
  v
Phase 3 (US1: 本地 PKCE 流程) -- MVP
  |-- T008, T009 测试可并行
  |-- T010 依赖 T001, T003, T005, T007
  |-- T011 依赖 T010
  v
Phase 4 (US2+US5: 手动模式 + 降级)
  |-- T012 可与 T013 并行
  |-- T013 依赖 T010 (扩展 oauth_flows.py)
  |-- T014 依赖 T013
  v
Phase 5 (US3: Provider 注册表集成)
  |-- T015 可与 T016 并行
  |-- T016 依赖 T010, T003
  |-- T017 依赖 T016
  |-- T018 依赖 T016
  v
Phase 6 (US4: Token 自动刷新)
  |-- T019 可与 T020 并行（先写测试确认失败）
  |-- T020 依赖 T003, T010
  |-- T021 依赖 T020
  v
Phase 7 (Polish)
  |-- T022, T023, T024, T025, T026, T028 全部可并行
  |-- T027 依赖所有 Phase 3-6
  |-- T029, T030, T031 回归验证可并行
```

### User Story 间依赖

- **US1** (P1): 无依赖，仅需 Phase 1-2 基础设施
- **US2 + US5** (P1 + P2): 依赖 US1 的 oauth_flows.py（扩展手动模式）
- **US3** (P1): 依赖 US1 的 oauth_flows.py + Phase 1 的 OAuthProviderRegistry
- **US4** (P2): 依赖 Phase 1 的 OAuthProviderConfig + US1 的 refresh_access_token

### Story 内部并行机会

- Phase 1: T001, T002, T003, T004 全部并行（四个独立文件）
- Phase 2: T005, T006 并行（不同包）
- Phase 3: T008, T009 测试并行
- Phase 5: T015 测试与 T016 实现可并行
- Phase 6: T019 测试与 T020 实现可并行（TDD: 先写测试）
- Phase 7: T022, T023, T024, T025, T026, T028 六个任务全部并行

---

## Implementation Strategy

### 推荐: MVP First + Incremental

1. **Phase 1 + 2 (Foundational)**: 完成所有基础设施（约 8 个任务，4 个可并行批次）
2. **Phase 3 (US1 - MVP)**: 本地 PKCE 流程可独立工作和演示
3. **STOP & VALIDATE**: 通过 mock 测试验证 Story 1 场景
4. **Phase 4 (US2 + US5)**: 添加远程环境支持和降级能力
5. **Phase 5 (US3)**: init_wizard 集成，用户可见的完整体验
6. **Phase 6 (US4)**: Token 自动刷新，提升长期使用体验
7. **Phase 7 (Polish)**: 契约测试、回归验证、导出更新

### MVP 范围

**US1 (Story 1: 本地 PKCE 授权)** -- Phase 1-3 完成后即可独立工作。
包含: PKCE 生成、回调服务器、OAuth 流程编排、事件记录。
不包含: 手动模式降级、init_wizard 集成、Token 自动刷新。

---

## Notes

- `[P]` 标记的任务涉及不同文件且无依赖关系，可安全并行执行
- `[USN]` 标记对应 spec.md 中的 User Story 编号
- 测试文件放在 `packages/provider/tests/` 扁平目录下（沿用 Feature 003 已建立的结构）
- plan.md 中描述的 `tests/unit/auth/` 子目录结构与实际代码结构不符，本任务清单以实际结构为准
- Feature 003 的现有测试（test_oauth_flow.py, test_codex_oauth_adapter.py, test_chain.py 等）不做修改，仅作为回归确认点
- 新增测试文件命名避免与现有文件冲突（如 test_oauth_flow**s**.py vs test_oauth_flow.py）
