# 技术研究备忘: Feature 003-b -- OAuth Authorization Code + PKCE

**Feature Branch**: `feat/003b-oauth-pkce`
**Date**: 2026-03-01
**Status**: Approved
**输入**: spec.md + research/tech-research.md

---

## Decision Log

### D1. OAuth 库选型: 纯 httpx 手写 vs OAuth 库

**Decision**: 纯 httpx + Python 标准库手写

**Rationale**:
1. Feature 003 的 Device Flow 已用纯 httpx 实现（`oauth.py` 约 160 行），团队已有相同模式的实践经验
2. PKCE 核心逻辑仅需 `secrets` + `hashlib` + `base64` 三个标准库，约 15 行代码
3. 零新增依赖，与 Constitution "Degrade Gracefully" 原则对齐（依赖链最短）
4. OpenClaw 参考实现验证：所有 OAuth 实现均为纯手写（无第三方 OAuth 库），Auth Code + PKCE 手写代码量约 100-150 行

**Alternatives Rejected**:
- **authlib**: 功能全面（自动 PKCE、token 刷新），但引入 cryptography 重依赖（~1.5MB），当前 MVP 不需要 OIDC
- **oauthlib**: 维护停滞（12+ 月无发版），仅同步，需额外封装
- **httpx-oauth**: 轻量但仍为新增依赖，当前需求手写可覆盖

**后续升级路径**: 如需 OIDC 集成或 JWT 解析，可升级到 authlib

---

### D2. 本地回调服务器: asyncio.start_server vs 其他方案

**Decision**: `asyncio.start_server` + 手动 HTTP 解析

**Rationale**:
1. 零新增依赖：仅使用 Python 标准库 `asyncio` + `urllib.parse`
2. 回调逻辑极简：仅需处理一个 GET 请求，解析 `code` 和 `state` query 参数
3. OpenClaw 使用 `node:http.createServer()` 是 Node.js 的等价方案，已验证可行
4. 服务器生命周期与 OAuth 流程绑定，收到第一个有效回调后立即关闭

**Alternatives Rejected**:
- **`http.server` (stdlib)**: 非 async，需线程包装，与异步代码集成不自然
- **`aiohttp.web`**: 全 async 且生产级，但新增约 2MB 依赖
- **`uvicorn` + 临时 ASGI app**: 项目已有 uvicorn 依赖，但启动较重，不适合临时使用

**核心实现**: 约 60 行代码，参见 tech-research.md Section 6.2

---

### D3. CSRF 防护: 独立 state vs 复用 code_verifier

**Decision**: 独立随机 state 参数，不复用 code_verifier

**Rationale**:
1. Chutes 参考实现使用独立 state，更安全
2. OpenClaw Gemini 实现复用 verifier 作为 state 被评估为"简化但安全性略低"
3. OctoAgent 作为长期项目，应采用更安全的方案
4. 额外开销极小（多一次 `secrets.token_urlsafe(32)` 调用）

**Alternatives Rejected**:
- **复用 code_verifier 作为 state**: 减少一个随机值生成，但 verifier 泄露风险扩大（同时影响 PKCE 和 CSRF 防护）

---

### D4. 架构方案: 统一 OAuthFlow 抽象 + Provider Registry

**Decision**: 方案 A -- 统一 OAuthFlow 抽象 + Provider Registry

**Rationale**:
1. 需求明确支持 OpenAI Codex（Auth Code + PKCE）和 GitHub Copilot（Device Flow）两种流程，且预留 Google Gemini 扩展
2. Provider Registry 模式：新增 Provider 仅需注册配置，无需新代码
3. 与现有 AuthAdapter/HandlerChain 架构无缝集成
4. 符合 Constitution "Tools are Contracts" 原则（OAuthProviderConfig 即合约）
5. OpenClaw `createVpsAwareOAuthHandlers` 验证了统一 OAuth 抽象的可行性

**Alternatives Rejected**:
- **方案 B (最小增量)**: 在现有 oauth.py 基础上扩展，改动量小但扩展性差。多 Provider 时需复制代码或传入大量参数

**Complexity Justification**: 初期多 5-7 个新文件的开发量，在后续 Provider 扩展时被快速摊薄

---

### D5. Provider ID 规范: 双层映射

**Decision**: canonical_id (`openai-codex`) + display_id (`openai`) 双层映射

**Rationale**:
1. 粗粒度 display_id 对用户友好（UI 中选 "OpenAI" 而非 "OpenAI Codex"）
2. 细粒度 canonical_id 对注册表精确匹配有意义（区分同一 vendor 的不同产品）
3. init_wizard 通过 `display_id -> canonical_id` 映射表实现转换，改动量最小
4. 现有 CredentialStore 中的 `provider` 值通过迁移函数统一为 canonical_id 格式

**迁移契约**:
- Feature 003 中 `provider="openai-codex"` (oauth.py) 和 `"openai"` (init_wizard.py PROVIDERS key) 保持不变
- 003-b 建立显式映射表：`{"openai": "openai-codex", "github": "github-copilot"}`
- 测试中的 `"codex"` (test_e2e_integration.py) 迁移为 `"openai-codex"`

---

### D6. Token 刷新职责: Adapter 负责 store 写入

**Decision**: 实现 refresh 的 Adapter（如 PkceOAuthAdapter）构造时注入 CredentialStore 实例

**Rationale**:
1. Adapter 最了解凭证结构和更新逻辑，由它负责写回最自然
2. HandlerChain 仅负责检测过期并调用 `refresh()`，不参与 store 写入（职责边界清晰）
3. 现有 `AuthAdapter.refresh()` 签名 `async def refresh() -> str | None` 保持不变
4. 不支持刷新的 Adapter（如现有 CodexOAuthAdapter）继续返回 None，行为不变

**接口约定**:
```python
class PkceOAuthAdapter(AuthAdapter):
    def __init__(
        self,
        credential: OAuthCredential,
        provider_config: OAuthProviderConfig,
        store: CredentialStore,
        profile_name: str,
    ) -> None: ...

    async def refresh(self) -> str | None:
        # 1. 使用 refresh_token 请求 token 端点
        # 2. 更新 credential store
        # 3. 返回新的 access_token
```

---

### D7. OAuthProviderConfig 与 DeviceFlowConfig 关系: 统一取代

**Decision**: OAuthProviderConfig 作为统一配置模型取代 DeviceFlowConfig

**Rationale**:
1. OAuthProviderConfig 覆盖所有 OAuth 流程类型（auth_code_pkce, device_flow, device_flow_pkce）
2. 现有 Device Flow 逻辑从 OAuthProviderConfig 中提取所需参数
3. 避免两套配置体系共存的维护负担
4. DeviceFlowConfig 在迁移完成后标记 `@deprecated`

**兼容策略**:
- `start_device_flow()` / `poll_for_token()` 函数签名不变（仍接受 DeviceFlowConfig）
- 新增辅助函数 `OAuthProviderConfig.to_device_flow_config()` 实现转换
- 迁移完成后，DeviceFlowConfig 标记废弃但不删除

---

### D8. OpenAI 授权端点域名

**Decision**: PKCE 使用 `auth.openai.com`，Device Flow 保留 `auth0.openai.com`

**Rationale**:
1. OpenClaw 最新实现（2025 年活跃维护）已验证 `auth.openai.com` 为 Auth Code + PKCE 的正确端点
2. Device Flow 端点 `auth0.openai.com` 不做破坏性变更，保持向后兼容
3. 两者在 OAuthProviderConfig 注册表中作为独立条目管理

**端点映射**:
| 流程 | 授权端点 | Token 端点 |
|------|---------|-----------|
| Auth Code + PKCE | `https://auth.openai.com/oauth/authorize` | `https://auth.openai.com/oauth/token` |
| Device Flow | `https://auth0.openai.com/oauth/device/code` | `https://auth0.openai.com/oauth/token` |

---

### D9. OAuthCredential 扩展字段: account_id

**Decision**: 仅从 token 端点响应 JSON body 提取，不解析 JWT

**Rationale**:
1. Token 端点响应是最可靠的来源
2. JWT 解析（即使是简单 base64 解码）与 Scope Exclusion "不引入 OIDC/JWT 解析" 存在语义冲突
3. 新增 `account_id: str | None = None` 为可选字段，保持向后兼容
4. 如后续需要 JWT 中的信息，在独立 Feature 中处理

---

### D10. asyncio event loop 集成策略

**Decision**: OAuth 流程通过 `asyncio.run()` 独立执行，与 questionary 同步 CLI 隔离

**Rationale**:
1. questionary 基于 prompt_toolkit，使用自己的 event loop，与嵌套 asyncio 不兼容
2. Feature 003 的 `_run_oauth_device_flow()` 已通过 `asyncio.run()` 独立运行，模式成熟
3. PKCE 流程沿用此模式：`asyncio.run(_run_oauth_pkce_flow())`
4. 如果未来 init_wizard 改为全异步，需要重构调用方式（当前阶段不需要）

**风险**: `asyncio.run()` 不能在已有 event loop 中嵌套调用。当前 init_wizard 为同步函数，此限制不适用。
