# Tasks: OAuth Token 自动刷新 + Claude 订阅 Provider 支持

**Input**: Design documents from `.specify/features/064-oauth-token-refresh-claude-provider/`
**Prerequisites**: plan.md, spec.md, data-model.md, contracts/token-refresh-api.md, contracts/claude-provider-api.md, research/tech-research.md

**Tests**: 包含测试任务。spec 要求完整的测试覆盖（SC-001 至 SC-006），plan.md 在每个 Phase 都列出了端到端验证任务。

**Organization**: 任务按 User Story 组织，支持增量交付。US1/US2/US3 为 P1 核心刷新机制，US4 为 P2 Claude 订阅支持，US5 为 P3 体验优化。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行执行（不同文件、无依赖）
- **[Story]**: 所属 User Story（US1, US2, US3, US4, US5）
- 包含精确文件路径

## 路径约定

```
源码:  octoagent/packages/provider/src/octoagent/provider/
测试:  octoagent/packages/provider/tests/
CLI:   octoagent/packages/provider/src/octoagent/provider/dx/
```

---

## Phase 1: Foundational (阻塞性前置依赖)

**Purpose**: 新增异常类型、并发协调器、常量定义 -- 所有 User Story 的共享基础设施

- [x] T001 [P] 新增 `AuthenticationError` 异常类型 -- 在 `octoagent/packages/provider/src/octoagent/provider/exceptions.py` 中添加 `AuthenticationError(ProviderError)` 子类，含 `status_code: int` 和 `provider: str` 字段。对齐 data-model.md DM-3。
- [x] T002 [P] 新增 `TokenRefreshCoordinator` 并发刷新协调器 -- 新建 `octoagent/packages/provider/src/octoagent/provider/refresh_coordinator.py`，实现 per-provider `asyncio.Lock` 的 `refresh_if_needed()` 方法。对齐 contracts/token-refresh-api.md SS4、FR-005。
- [x] T003 [P] 新增 `REFRESH_BUFFER_SECONDS` 常量 -- 在 `octoagent/packages/provider/src/octoagent/provider/auth/pkce_oauth_adapter.py` 顶部添加 `REFRESH_BUFFER_SECONDS: int = 300` 常量定义（5 分钟缓冲期）。对齐 data-model.md DM-4、FR-011。

**Checkpoint**: 基础设施就绪，User Story 实现可以开始。

---

## Phase 2: User Story 1 - OpenAI Codex Token 过期后无感续期 (Priority: P1) -- MVP

**Goal**: 当 Codex 的 access_token 过期时，系统自动使用 refresh_token 获取新 token，用户无感知。

**Independent Test**: 使用一个即将过期的 Codex access_token 发起 LLM 请求，验证系统自动刷新 token 并成功返回结果。

### Tests for User Story 1

> **NOTE: 先写测试，确认测试失败，再实现**

- [x] T004 [P] [US1] 扩展 `is_expired()` 缓冲期预检测试 -- 在 `octoagent/packages/provider/tests/test_pkce_oauth_adapter.py` 中新增测试用例：(a) token 距过期 > 5min 返回 False；(b) token 距过期 < 5min 返回 True；(c) token 已过期返回 True。
- [x] T005 [P] [US1] 扩展 `refresh()` 刷新成功测试 -- 在 `octoagent/packages/provider/tests/test_pkce_oauth_adapter.py` 中新增测试用例：当 `supports_refresh=True` 且 token 过期时，`refresh()` 成功返回新 access_token，`CredentialStore` 被更新。
- [x] T006 [P] [US1] 扩展 `refresh()` 刷新失败测试 -- 在 `octoagent/packages/provider/tests/test_pkce_oauth_adapter.py` 中新增测试用例：(a) `invalid_grant` 错误导致 profile 被清除，返回 None；(b) 网络错误返回 None 并记录 warning。
- [x] T007 [P] [US1] 新增 `TokenRefreshCoordinator` 并发刷新测试 -- 新建 `octoagent/packages/provider/tests/test_refresh_coordinator.py`，测试：(a) 多个并发刷新只执行一次实际刷新；(b) 不同 provider 的刷新互不阻塞；(c) 刷新失败返回 None。

### Implementation for User Story 1

- [x] T008 [US1] 启用 `openai-codex` 的 `supports_refresh=True` -- 在 `octoagent/packages/provider/src/octoagent/provider/auth/oauth_provider.py` 中将 `BUILTIN_PROVIDERS["openai-codex"]` 的 `supports_refresh` 从 `False` 改为 `True`。对齐 data-model.md DM-1.1。
- [x] T009 [US1] 修改 `is_expired()` 增加缓冲期预检 -- 在 `octoagent/packages/provider/src/octoagent/provider/auth/pkce_oauth_adapter.py` 中修改 `is_expired()` 方法，将过期判定从 `now >= expires_at` 改为 `now >= (expires_at - timedelta(seconds=REFRESH_BUFFER_SECONDS))`。对齐 contracts/token-refresh-api.md SS2、FR-011。
- [x] T010 [US1] 刷新流程集成 `TokenRefreshCoordinator` -- 在 `octoagent/packages/provider/src/octoagent/provider/auth/pkce_oauth_adapter.py` 中，在 `refresh()` 方法内通过 `TokenRefreshCoordinator` 串行化刷新操作（或在调用方注入 coordinator 实例）。对齐 FR-005。

**Checkpoint**: Codex token 过期后自动刷新功能就绪。可通过 `test_pkce_oauth_adapter.py` 和 `test_refresh_coordinator.py` 独立验证。

---

## Phase 3: User Story 2 - API 错误触发主动刷新重试 (Priority: P1)

**Goal**: 当 Provider API 返回 401/403 认证失败时，系统自动刷新 token 并重试请求（最多一次），而非直接返回错误。

**Independent Test**: 模拟 Provider API 返回 401 状态码，验证系统触发刷新并成功重试。

### Tests for User Story 2

- [x] T011 [P] [US2] 新增 `_is_auth_error()` 认证错误判定测试 -- 新建 `octoagent/packages/provider/tests/test_client_auth_retry.py`，测试：(a) `AuthenticationError` 被正确识别；(b) LiteLLM 的 401/403 异常被正确识别；(c) 非认证错误（500、连接超时）不被误判。
- [x] T012 [P] [US2] 新增 refresh-on-401 重试逻辑测试 -- 在 `octoagent/packages/provider/tests/test_client_auth_retry.py` 中新增测试用例：(a) 401 触发回调 + 重试成功；(b) 回调返回 None 时抛出原始错误；(c) 无 callback 时直接抛出；(d) 重试后仍 401 不再循环。
- [x] T013 [P] [US2] 新增 refresh_token 失效时的用户提示测试 -- 在 `octoagent/packages/provider/tests/test_client_auth_retry.py` 中测试：刷新失败后错误消息包含"重新授权"建议文案。对齐 FR-003。

### Implementation for User Story 2

- [x] T014 [US2] 新增 `_is_auth_error()` 静态方法 -- 在 `octoagent/packages/provider/src/octoagent/provider/client.py` 中的 `LiteLLMClient` 类添加 `_is_auth_error(e: Exception) -> bool` 方法，判定 401/403 认证类错误。对齐 contracts/token-refresh-api.md SS3。
- [x] T015 [US2] 新增 `auth_refresh_callback` 参数 -- 在 `octoagent/packages/provider/src/octoagent/provider/client.py` 中的 `LiteLLMClient.__init__()` 添加 `auth_refresh_callback` 可选参数，类型为 `Callable[[], Awaitable[HandlerChainResult | None]] | None`。
- [x] T016 [US2] 实现 `complete()` 中的 refresh-on-error 重试逻辑 -- 在 `octoagent/packages/provider/src/octoagent/provider/client.py` 中修改 `complete()` 方法：捕获认证错误 -> 调用 callback -> 使用新凭证重试一次。对齐 contracts/token-refresh-api.md SS3、FR-002。

**Checkpoint**: 401/403 自动刷新重试功能就绪。可通过 `test_client_auth_retry.py` 独立验证。

---

## Phase 4: User Story 3 - Token 刷新后无需重启即刻生效 (Priority: P1)

**Goal**: 刷新后的 token 立即在后续请求中生效，无需重启服务或手动操作。

**Independent Test**: 刷新 token 后立即发起多次请求，验证每次请求都使用最新的 access_token。

### Tests for User Story 3

- [x] T017 [P] [US3] 新增凭证实时生效集成测试 -- 在 `octoagent/packages/provider/tests/test_pkce_oauth_adapter.py` 中新增测试用例：模拟 `refresh()` 后，下一次 `resolve()` 返回新 token（非旧 token）。对齐 contracts/token-refresh-api.md SS6。
- [x] T018 [P] [US3] 新增并发请求刷新后凭证一致性测试 -- 在 `octoagent/packages/provider/tests/test_refresh_coordinator.py` 中新增测试用例：多个并发请求同时检测到 token 过期，刷新完成后所有请求都使用同一个新 token。对齐 FR-005、US3 场景 2。

### Implementation for User Story 3

- [x] T019 [US3] 验证 `HandlerChain.resolve()` 的实时凭证读取路径 -- 审查 `octoagent/packages/provider/src/octoagent/provider/auth/chain.py` 中的 `resolve()` 方法，确认每次调用都从 `CredentialStore` 读取最新凭证（非缓存）。如有缓存行为则修正。对齐 FR-006。
- [x] T020 [US3] 验证 `PkceOAuthAdapter.refresh()` 的内存 + 持久化双写 -- 审查 `octoagent/packages/provider/src/octoagent/provider/auth/pkce_oauth_adapter.py` 中的 `refresh()` 方法，确认刷新后同时更新 `self._credential`（内存）和 `CredentialStore`（文件）。对齐 FR-004。

**Checkpoint**: 凭证实时生效机制验证完成。US1+US2+US3 共同构成完整的核心刷新闭环。

---

## Phase 5: User Story 4 - Claude 订阅用户通过 Setup Token 接入 (Priority: P2)

**Goal**: 拥有 Claude 订阅的用户通过 CLI 导入 setup-token，复用核心刷新机制调用 Claude 模型。

**Independent Test**: 用户粘贴 Claude setup-token，验证系统正确存储凭证并能成功调用 Claude API，且 8 小时后 access_token 自动刷新。

### Tests for User Story 4

- [x] T021 [P] [US4] 新增 Claude setup-token 格式校验测试 -- 在 `octoagent/packages/provider/tests/test_validators.py` 中新增测试用例：(a) 有效的 `sk-ant-oat01-*` + `sk-ant-ort01-*` 通过校验；(b) 前缀错误、长度不足、空值被拒绝。
- [x] T022 [P] [US4] 新增 Claude Provider 注册测试 -- 在 `octoagent/packages/provider/tests/test_oauth_provider.py` 中新增测试用例：(a) `BUILTIN_PROVIDERS` 包含 `anthropic-claude` 配置；(b) `supports_refresh=True`；(c) `DISPLAY_TO_CANONICAL` 包含映射。
- [x] T023 [P] [US4] 新增 `paste-token` CLI 命令测试 -- 新建 `octoagent/packages/provider/tests/test_paste_token_command.py`，测试：(a) 有效 token 导入后存储为 OAuthCredential；(b) 无效格式被拒绝并显示错误；(c) 导入前显示政策风险提示。
- [x] T024 [P] [US4] 新增 Claude Provider 刷新适配测试 -- 新建 `octoagent/packages/provider/tests/test_claude_provider.py`，测试：(a) Claude OAuthCredential 的 `refresh()` 成功（mock Anthropic token 端点）；(b) `account_id` 为 None 不影响刷新流程；(c) Anthropic 403 政策拒绝返回友好错误消息。

### Implementation for User Story 4

- [x] T025 [US4] 新增 `anthropic-claude` Provider 注册 -- 在 `octoagent/packages/provider/src/octoagent/provider/auth/oauth_provider.py` 中向 `BUILTIN_PROVIDERS` 添加 `anthropic-claude` 配置（token_endpoint、client_id、supports_refresh=True），并扩展 `DISPLAY_TO_CANONICAL` 映射。对齐 data-model.md DM-1.2、DM-1.3。
- [x] T026 [US4] 新增 `validate_claude_setup_token()` 校验函数 -- 在 `octoagent/packages/provider/src/octoagent/provider/auth/validators.py` 中添加 `validate_claude_setup_token(access_token, refresh_token) -> tuple[bool, str]`，校验 `sk-ant-oat01-*` 和 `sk-ant-ort01-*` 前缀及最小长度。对齐 contracts/claude-provider-api.md SS1。
- [x] T027 [US4] 新增 `paste-token` CLI 子命令 -- 在 `octoagent/packages/provider/src/octoagent/provider/dx/cli.py` 中（或新建 `auth_commands.py`）添加 `octo auth paste-token --provider anthropic-claude` 命令：显示政策风险提示、接收 access_token + refresh_token、校验、存储为 OAuthCredential profile。对齐 contracts/claude-provider-api.md SS1、FR-008、FR-010。
- [x] T028 [US4] 更新 `TokenCredential` 文档注释 -- 在 `octoagent/packages/provider/src/octoagent/provider/auth/credentials.py` 中更新 `TokenCredential` 类的文档注释，说明 Anthropic setup-token 已迁移至 `OAuthCredential` 存储，不再使用 `TokenCredential`。对齐 data-model.md DM-2 遗留清理。

**Checkpoint**: Claude 订阅 Provider 功能就绪。可通过 `test_claude_provider.py` + `test_paste_token_command.py` 独立验证。

---

## Phase 6: User Story 5 - Token 过期预检（提前刷新缓冲） (Priority: P3)

**Goal**: 在 token 即将过期时（过期前 5 分钟）主动刷新，减少用户请求被延迟的概率。

**Independent Test**: 设置一个 5 分钟后过期的 token，在过期前 5 分钟内发起请求，验证系统提前刷新。

> **NOTE**: 此 User Story 的核心实现（`REFRESH_BUFFER_SECONDS` 常量 + `is_expired()` 缓冲期逻辑）已在 Phase 1 (T003) 和 Phase 2 (T009) 中完成。本 Phase 仅需补充端到端集成验证。

- [x] T029 [US5] 端到端预检刷新集成测试 -- 在 `octoagent/packages/provider/tests/test_pkce_oauth_adapter.py` 中新增测试用例：构造一个距过期 4 分钟的 token，发起 `resolve()` 调用，验证触发刷新并返回新 token。对齐 FR-011、US5 场景 1。

**Checkpoint**: 预检刷新体验优化验证完成。

---

## Phase 7: 可观测性与回归 (Cross-Cutting)

**Purpose**: 确保事件完整性、非 OAuth Provider 不受影响、代码清理

- [x] T030 [P] 验证 `OAUTH_REFRESHED` / `OAUTH_FAILED` 事件完整性 -- 在 `octoagent/packages/provider/tests/test_oauth_events.py` 中扩展或新增测试用例，覆盖：(a) 正常刷新成功发射 `OAUTH_REFRESHED`；(b) `invalid_grant` 发射 `OAUTH_FAILED`；(c) 网络错误发射 `OAUTH_FAILED`；(d) 并发刷新只发一次事件。对齐 FR-012、SC-006。
- [x] T031 [P] 非 OAuth Provider 回归测试 -- 在 `octoagent/packages/provider/tests/test_client.py` 或 `test_api_key_adapter.py` 中新增测试用例：验证 API Key Provider（无 `auth_refresh_callback`）的 LLM 调用路径不受 refresh-on-error 逻辑影响。对齐 FR-007。
- [x] T032 [P] `__init__.py` 导出更新 -- 在 `octoagent/packages/provider/src/octoagent/provider/__init__.py` 中导出 `AuthenticationError` 和 `TokenRefreshCoordinator`。
- [x] T033 Anthropic 403 政策拒绝友好错误消息处理 -- 在 `octoagent/packages/provider/src/octoagent/provider/client.py` 的 retry 失败分支中，检测 Anthropic 的 "permission_error" 响应并构建用户友好的错误消息（建议使用 API Key 替代）。对齐 contracts/claude-provider-api.md SS3、FR-010。

---

## Phase 8: Polish & Documentation

**Purpose**: 文档更新、代码审查、最终验证

- [x] T034 [P] `refresh_coordinator.py` 的 `__init__.py` 注册 -- 确认新文件 `refresh_coordinator.py` 在包初始化中被正确导入和注册。
- [x] T035 [P] 更新 Feature 文档 -- 在 `.specify/features/064-oauth-token-refresh-claude-provider/` 下更新 spec.md 状态为 Implemented，确认所有 SC 已覆盖。
- [x] T036 全量测试运行验证 -- 在 `octoagent/packages/provider/` 目录下运行 `uv run pytest tests/` 确保所有新增和既有测试通过，无回归。

---

## FR 覆盖映射表

| FR | 描述 | 覆盖任务 |
|----|------|---------|
| FR-001 | OAuth access_token 过期时自动刷新 | T008, T009, T010, T004, T005, T006 |
| FR-002 | 401/403 响应触发刷新重试（最多一次） | T014, T015, T016, T011, T012 |
| FR-003 | 刷新失败时返回明确错误信息 | T006, T013, T033 |
| FR-004 | 刷新后新凭证持久化存储 | T020, T005 |
| FR-005 | 并发刷新串行化（per-provider 锁） | T002, T010, T007, T018 |
| FR-006 | 每次 LLM 调用实时读取最新 token | T019, T017 |
| FR-007 | 非 OAuth Provider 不受影响 | T031 |
| FR-008 | CLI 导入 Claude setup-token 为 OAuthCredential | T027, T023 |
| FR-009 | Claude 凭证复用 Codex 刷新逻辑 | T025, T024 |
| FR-010 | Claude 凭证被拒时展示友好错误提示 | T027, T033, T024 |
| FR-011 | token 过期预检（5 分钟缓冲期） | T003, T009, T004, T029 |
| FR-012 | 刷新事件结构化记录 | T030 |

**FR 覆盖率**: 12/12 = **100%**

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Foundational) ── 无依赖，立即开始
  |
  v
Phase 2 (US1: Codex 自动刷新) ── 依赖 Phase 1 中的 T001, T002, T003
  |
  v
Phase 3 (US2: 401 重试) ── 依赖 Phase 1 中的 T001（AuthenticationError）
  |                          可与 Phase 2 并行（不同文件: client.py vs pkce_oauth_adapter.py）
  v
Phase 4 (US3: 凭证即时生效) ── 依赖 Phase 2 + Phase 3 完成
  |
  v
Phase 5 (US4: Claude 订阅) ── 依赖 Phase 1 完成（复用刷新基础设施）
  |                            可与 Phase 2/3/4 并行（不同文件: oauth_provider.py, validators.py, cli.py）
  v
Phase 6 (US5: 预检优化) ── 实现已内嵌在 Phase 1/2，仅需集成测试
  |
  v
Phase 7 (可观测性 & 回归) ── 依赖所有实现 Phase 完成
  |
  v
Phase 8 (Polish) ── 最终阶段
```

### User Story 间依赖

| Story | 依赖 | 可否并行 |
|-------|------|---------|
| US1 (Codex 自动刷新) | Phase 1 | 最优先实现 |
| US2 (401 重试) | Phase 1 (T001) | 可与 US1 并行（client.py vs pkce_oauth_adapter.py） |
| US3 (凭证即时生效) | US1 + US2 | 需要刷新 + 重试都就绪后验证 |
| US4 (Claude 订阅) | Phase 1 | 可与 US1/US2 并行（全新文件） |
| US5 (预检优化) | US1 (T003, T009) | 实现已内嵌，仅补测试 |

### Story 内部并行机会

- **Phase 1**: T001, T002, T003 三个任务完全独立，可并行
- **Phase 2**: T004, T005, T006, T007 四个测试任务可并行
- **Phase 3**: T011, T012, T013 三个测试任务可并行
- **Phase 5**: T021, T022, T023, T024 四个测试任务可并行；T025, T026, T027 三个实现任务部分可并行（不同文件）

### 推荐实现策略

**MVP First**: 完成 Phase 1 -> Phase 2 -> Phase 3 -> Phase 4 即可交付核心价值（Codex token 自动刷新 + 401 重试 + 凭证即时生效）。MVP 范围为 US1 + US2 + US3。

**Incremental Delivery**:
1. Phase 1 + Phase 2 (US1) -> 验证 Codex 刷新 -> 可用
2. + Phase 3 (US2) -> 验证 401 重试 -> 更可靠
3. + Phase 4 (US3) -> 验证凭证即时生效 -> 闭环
4. + Phase 5 (US4) -> Claude 订阅支持 -> 扩展价值
5. + Phase 6/7/8 -> 完整交付

---

## Summary

| 维度 | 数值 |
|------|------|
| 总任务数 | 36 |
| User Stories | 5 (US1-US5) |
| 可并行任务 | 22 (61%) |
| 新增文件 | 4 (`refresh_coordinator.py`, `test_client_auth_retry.py`, `test_paste_token_command.py`, `test_claude_provider.py`) |
| 修改文件 | 7 (`exceptions.py`, `oauth_provider.py`, `pkce_oauth_adapter.py`, `client.py`, `validators.py`, `credentials.py`, `cli.py`) |
| FR 覆盖率 | 100% (12/12) |

---

## Notes

- [P] 任务 = 不同文件、无依赖，可并行执行
- [USN] 标记映射任务到具体 User Story，支持追溯
- 每个 Phase Checkpoint 后可独立验证该 Story
- Commit 粒度建议：每完成一个 Phase 提交一次
- US5 (预检优化) 的实现已内嵌在 US1 的 T003 + T009 中，Phase 6 仅补充端到端测试
