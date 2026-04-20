# Feature 078 — OAuth Token Refresh 链路补齐与健壮性加固

> 状态：Draft
> 作者：Connor
> 创建时间：2026-04-19
> 模式：spec-driver-feature（含调研，跨多个改动面）
> 分支：`078-oauth-refresh-robustness`
> 前置 Feature：064c（OAuth refresh 初版实现，遗留本 Feature 要修的缺口）

## 0. TL;DR

Feature 064c 引入了 `PkceOAuthAdapter` + `TokenRefreshCoordinator` + `auth_refresh_callback`，但**只接到了老的 `LiteLLMClient` 调用路径**。现网所有 chat/skill 调用走 `SkillRunner → LiteLLMSkillClient`，**这条路径完全没接 refresh 回调**。叠加 `is_expired()` 只看 `expires_at` 字段的乐观判断，结果：

- access_token 真过期时，Skill 路径不会触发 refresh
- OpenAI 提前 revoke 时，`is_expired()` 判为"未过期"，即使接上回调也不刷新
- 连环 401 → LiteLLM cooldown → 12 次失败 + 用户看到英文技术栈错误

本 Feature 一次性：**(P1) 把 Skill 接上 refresh + (P2) 撞 401 强制 refresh + (P3) Codex 专项外挂 `~/.codex/auth.json` + (P4) 诊断与观测**。做完后这条链路能经受 Codex CLI 登过别的端、refresh_token 被烧、access_token 被服务端提前 revoke 等真实场景。

## 1. 背景与问题

### 1.1 现状架构（Feature 064c 落地后）

```
┌─────────────────────────┐
│ User / Channel          │
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐      ┌─ LiteLLMClient ──────┐
│ LLMService              │─────▶│  auth_refresh_callback✅│ ← 064c 接进来的
│ ._fallback_manager      │      └───────────────────────┘
└──────────┬──────────────┘
           ▼ (新路径，现网 99% chat 走这条)
┌─────────────────────────┐      ┌─ LiteLLMSkillClient ─┐
│ SkillRunner             │─────▶│  ❌ 无 callback      │ ← 本 Feature 要补
└─────────────────────────┘      └───────────────────────┘
           │
           ▼
┌─────────────────────────┐
│ PkceOAuthAdapter        │
│ ├ resolve()             │ ─┐
│ │   if is_expired():    │  │ ❌ 只看 expires_at 字段；
│ │     refresh()         │  │    服务端提前 revoke 不触发
│ └ refresh()             │  │
└─────────────────────────┘  │
                             ▼
           ┌────────────────────────────┐
           │ ~/.octoagent/              │ ❌ 不感知 Codex CLI 的 auth.json
           │   auth-profiles.json       │
           └────────────────────────────┘
```

### 1.2 现网事件证据（2026-04-19）

Task `01KPGQGC1J447N1EV5JN9EBK9M` 场景：

```
13:13:13 MODEL_CALL_FAILED Proxy 429: No deployments available（被 401 踩冷）
13:13:16 MODEL_CALL_FAILED Proxy 401: Provided authentication token is expired
13:13:17 MODEL_CALL_FAILED Proxy 429: ...
13:13:20 MODEL_CALL_FAILED Proxy 429: ...
13:13:23 MODEL_CALL_FAILED Proxy 401: Provided authentication token is expired
[共 12 次连发失败，0 次 OAUTH_REFRESHED 事件]
```

用户凭证状态：
- `openai-codex-default.updated_at = 2026-04-18 05:43:59 UTC`（32 小时前）
- `expires_at = 2026-04-28 05:43:59 UTC`（本地认为 10 天后才过期）
- 服务端实际已 revoke（ChatGPT Pro 的服务端策略，非 JWT exp 决定）

用户本地有 `codex` CLI（Claude Code 生态用户基本都装了），但 OctoAgent 不知道 `~/.codex/auth.json` 的存在。

### 1.3 Root Cause（两个独立 bug + 一个架构缺失）

- **Bug A（Skill 路径无回调）**：`LiteLLMSkillClient.__init__` 没有 `auth_refresh_callback` 参数，[main.py:580](octoagent/apps/gateway/src/octoagent/gateway/main.py:580) 构造它时也没传。064c 只接到 [main.py:465](octoagent/apps/gateway/src/octoagent/gateway/main.py:465) 的 `LiteLLMClient`（现在很少用）
- **Bug B（resolve 只认 expires_at）**：[pkce_oauth_adapter.py:80](octoagent/packages/provider/src/octoagent/provider/auth/pkce_oauth_adapter.py:80) 的 `if self.is_expired(): refresh()` 逻辑，遇到服务端提前 revoke 场景完全不触发
- **缺失 C（不感知外部 CLI）**：Codex CLI 自己有一套完整的 OAuth refresh（包括用户在 Claude Code 里跑 Codex 时的自动续期），`~/.codex/auth.json` 是它的真理来源。我们自己又独立做一份，既重复又容易漂移

### 1.4 参考实现（OpenClaw）

[_references/opensource/openclaw/src/agents/auth-profiles/oauth.ts](_references/opensource/openclaw/src/agents/auth-profiles/oauth.ts) 的 1045 行 OAuth 代码是工业级参考。关键做法：

| 能力 | 位置 | 我们是否需要 |
|---|---|---|
| 外挂 Codex CLI auth.json | `readOpenAICodexCliOAuthProfile` + `resolveExternalAuthProfiles` hook | ✅ 核心借鉴 |
| 跨进程文件锁（refresh_token 是 single-use） | `withFileLock(globalRefreshLockPath, ...)` | ⏳ 当前单实例暂不需要，留 Phase 4 |
| `refresh_token_reused` 专用恢复路径 | `isRefreshTokenReusedError` + reload-from-store | ✅ 核心借鉴 |
| Identity binding（跨 agent 凭证拷贝安全） | `isSameOAuthIdentity` / `isSafeToCopyOAuthIdentity` | ⏳ Phase 4 |
| `hasUsableOAuthCredential` 真实可用性（不只看 expires） | 使用前实时判 | ✅ 核心借鉴 |
| 硬超时（refresh 卡住时释放锁） | `withRefreshCallTimeout` | ✅ 核心借鉴 |
| 插件化 `refreshOAuth` hook（每 provider 自己实现） | `ProviderPlugin.refreshOAuth` | 📝 记为后续重构点 |

## 2. User Stories

### US-1：access_token 真过期时 Skill 路径能自动续（P0）

**As** 一个在 Web UI 连续对话的用户
**I want** 当 Codex access_token 自然到期（约 24h）时 Skill 调用自动 refresh，无感续期
**So that** 不需要我手动重登

**验收**：
- Given Codex access_token 已过 `expires_at - 5min` 阈值
- When 发起一次 Chat / Skill 调用
- Then `OAUTH_REFRESHED` 事件出现一次，后续调用用新 token，无 `MODEL_CALL_FAILED`
- 并发发 5 个请求时，仍只有 1 次真实 refresh（在 coordinator 串行化下）

### US-2：服务端提前 revoke 时也能自动 recover（P0）

**As** 在 ChatGPT 其他端（网页 / 移动端）刚登录/切换账号的用户
**I want** OctoAgent 第一次撞 401 就触发一次 refresh 重试，而不是 12 连 401
**So that** 跨端操作不会让我的 OctoAgent 失效

**验收**：
- Given `expires_at` 在未来，但服务端已 revoke token
- When 发起一次 LLM 调用，收到 401
- Then 自动调 `auth_refresh_callback(force_refresh=True)`，成功后重试**最多一次**，重试仍失败就抛出人类可读错误并提示重登
- 不出现 12 连 401 + 429 的风暴

### US-3：Codex CLI 已登录时直接 adopt（P0）

**As** 一个已经在终端用 `codex` CLI 登录过 ChatGPT Pro 的用户
**I want** OctoAgent 自动检测 `~/.codex/auth.json` 并 adopt 里面的最新 token
**So that** 我不需要在 OctoAgent 里单独再走一遍浏览器 OAuth

**验收**：
- Given `~/.codex/auth.json` 存在且 `auth_mode="chatgpt"`
- When OctoAgent 启动或每次调用前的 token 解析
- Then 若文件里的 token 比我们 store 里的新（按 exp / iat 判），自动 adopt 到 `auth-profiles.json.openai-codex-default`，并写入 `OAUTH_ADOPTED_FROM_EXTERNAL_CLI` 事件
- 若 `~/.codex/auth.json` 不存在，不影响正常流程（best-effort）

### US-4：refresh 失败时给出可操作提示（P1）

**As** 一个 refresh_token 也失效或服务端永久拒绝的用户
**I want** OctoAgent 明确告诉我"凭证已失效，请运行 `octo setup --provider openai-codex` 重登"
**So that** 我不需要自己看日志猜原因

**验收**：
- 连续两次 refresh 失败（一次乐观 pre-emptive + 一次 reactive force）
- 抛出 `ProviderError(recoverable=True)`，`error_message` 包含具体命令：`octo setup --provider openai-codex`
- 同时发射 `OAUTH_REFRESH_EXHAUSTED` 事件（包含 provider / profile / 最后错误类型）
- 前端 chat UI 显示中文提示：**"ChatGPT Pro 授权已失效，请在终端运行 `octo setup --provider openai-codex` 重新登录"**

### US-5：refresh_token_reused 不清除凭证（P1）

**As** 在多个窗口 / 后台任务并发调用的用户
**I want** 偶发的 `refresh_token_reused`（上一次 refresh 调用已成功但我们这里超时了）能自动 recover
**So that** 不会因为一次超时就把整个凭证废掉

**验收**：
- Given 一次 refresh 调用抛 `invalid_grant` 且错误体含 "refresh_token" / "already been used"
- Then 不 `remove_profile`；先 reload store（可能被其他进程更新了），如果凭证变了就用新的；没变才走"重新登录"引导
- 发射 `OAUTH_REFRESH_RECOVERED` 事件区分"真失效 vs 虚惊一场"

### US-6：可观测（P1）

**As** 运维 / 开发者
**I want** 事件流里能看到所有 refresh 状态
**So that** 下次出现类似风暴时 60 秒内定位到根因

**验收**：新增事件类型并落盘：
- `OAUTH_REFRESH_TRIGGERED(mode=preemptive|reactive, provider, profile)`
- `OAUTH_REFRESHED(provider, profile, new_expires_at)`（已有，保留并加 mode 字段）
- `OAUTH_REFRESH_FAILED(provider, profile, error_type, error_message)`
- `OAUTH_REFRESH_RECOVERED(via=store_reload|external_cli, provider, profile)`
- `OAUTH_REFRESH_EXHAUSTED(provider, profile, last_error)`
- `OAUTH_ADOPTED_FROM_EXTERNAL_CLI(provider, profile, source_path, new_expires_at)`

`/api/ops/auth/diagnostics` 新增端点，列出每个 profile：最近一次 refresh 时间 / 状态 / 错误类型。

## 3. 不做的事（Non-goals）

- **跨进程文件锁**：当前 OctoAgent 是单进程单 Gateway，暂不引入 `fcntl.flock`；写进 [Open Questions](#7-open-questions) 待未来多 agent 场景
- **Identity binding**：auth-profiles.json 目前不存在跨账号污染风险（单用户单端）；Phase 4 再加
- **插件化 ProviderPlugin 重构**：不动 `PkceOAuthAdapter` 的层级结构，只在其上加功能
- **Anthropic Claude 的类似路径**：Claude 今天你在 Web UI 刚授权的流程工作正常，不在本 Feature 范围。但 Phase 1 的 Skill callback hookup 会同时受益（因为 callback 会遍历所有 OAuth profile）
- **`setup` CLI 改造**：`octo setup --provider openai-codex` 目前依赖 Gateway 在线是另一个已知问题（今天你也撞上了），记为相邻但独立的 [follow-up task](#8-相邻问题-followup)

## 4. 分阶段交付

### Phase 1 — Skill 路径补回调 + reactive force refresh（P0，独立可落）

**目标**：解决"access_token 真到期时不 refresh"和"服务端提前 revoke 时不 refresh"两个 Bug。

**变更面**：
1. `LiteLLMSkillClient.__init__` 新增 `auth_refresh_callback` 参数，透传给 `ChatCompletionsProvider` / `ResponsesApiProvider`
2. `providers.py` 的两个 provider `call()` 方法：`resp.status_code == 401` 时调 `auth_refresh_callback()` 后重试最多一次
3. `main.py:580` 把已构造好的 `auth_refresh_callback` 传进去
4. `PkceOAuthAdapter.resolve(force_refresh: bool = False)`：force 模式绕开 `is_expired()` gate 直接 refresh
5. `auth_refresh.py` 的 `_resolve` 在"由 401 触发"时 `force_refresh=True`（通过 callback 上下文参数传递）
6. 测试：`test_litellm_skill_client_refresh_on_401.py` 覆盖 401→refresh→retry→200 完整链路

**验收**：US-1 + US-2 的 acceptance scenarios 全部通过；`grep OAUTH_REFRESHED events.db`  能看到事件；人工压测：手动把 `auth-profiles.json` 的 access_token 改错 1 字符，发起一次对话，能看到一次 `OAUTH_REFRESH_TRIGGERED(mode=reactive)` → `OAUTH_REFRESHED` → 对话成功。

### Phase 2 — Codex CLI 外挂 adapter（P0）

**目标**：装了 `codex` CLI 的用户完全不需要我们自己的 refresh，直接吃 CLI 的成果。

**变更面**：

1. 新增 `octoagent/packages/provider/src/octoagent/provider/auth/codex_cli_bridge.py`：
   - `read_codex_cli_auth(env) -> OAuthCredential | None`：读 `~/.codex/auth.json`，解析 `auth_mode=chatgpt` 下的 `tokens.{access_token,refresh_token,account_id}`，转为我们的 `OAuthCredential`
   - `external_cli_path(env) -> Path`：支持 `CODEX_HOME` env 覆盖
2. `CredentialStore.get_profile(name, *, prefer_external=True)`：获取 openai-codex-default 前先查外部 CLI；若外部比 store 新（按 JWT exp 比），写一份快照到 store 并发射 `OAUTH_ADOPTED_FROM_EXTERNAL_CLI`
3. `PkceOAuthAdapter.refresh()`：撞 `invalid_grant` 时也先尝试 reload external CLI（可能用户刚在 CLI 重登了）
4. 测试：`test_codex_cli_bridge.py` 用 fixture 伪造 `~/.codex/auth.json`，验证 adopt / 忽略不存在 / identity 冲突时拒绝

**验收**：US-3 全部通过。终极 smoke test：把 `auth-profiles.json.openai-codex-default.credential.access_token` 故意改坏（不动 `~/.codex/auth.json`），重启 Gateway，发起对话能自动从 CLI 拿到可用 token（事件里能看到 `OAUTH_ADOPTED_FROM_EXTERNAL_CLI`）。

### Phase 3 — refresh_token_reused recovery + 硬超时（P1）

**目标**：偶发的并发 refresh 或超时不清除凭证；refresh HTTP 卡住能释放给别人。

**变更面**：

1. `PkceOAuthAdapter.refresh()`：
   - 识别 `invalid_grant` + 消息含 `refresh_token` / `already been used` 为 `_is_refresh_token_reused_error`
   - 触发时**不 remove_profile**，改为 reload store，若 credential 真变了就用新的，发 `OAUTH_REFRESH_RECOVERED`
   - 仍没变就走 US-4 的"重新登录"提示路径
2. `oauth_flows.refresh_access_token` 加 `timeout_s` 硬上限（默认 15s），超时抛独立异常类型 `OAuthRefreshTimeout`，不污染 `OAuthFlowError`
3. `TokenRefreshCoordinator.refresh_if_needed`：超时时释放锁、不缓存失败态（让下次请求能重试）
4. 测试：`test_pkce_oauth_adapter_reused_recovery.py` 覆盖 3 种 recovery 路径

**验收**：US-5 通过；人工场景：模拟在 refresh 过程中另起一个进程直接写 store 更新凭证，主流程撞 `invalid_grant` 后不弹重登、而是用 store 里已更新的凭证继续。

### Phase 4 — 观测与诊断（P1）

**目标**：让下次踩坑时在事件流里直接看到因果。

**变更面**：

1. `core/models/enums.py EventType` 新增 6 个事件
2. `PkceOAuthAdapter` 的每个分支补 `emit_oauth_event` 调用
3. 新增 API `GET /api/ops/auth/diagnostics`：列出所有 OAuth profile 最近一次 refresh 状态、external CLI 文件状态、下次预计 refresh 时间
4. 前端 Settings / 诊断页加 "OAuth 凭证状态" 卡片，展示 `OAUTH_REFRESH_EXHAUSTED` 时的引导命令
5. 测试：`test_auth_diagnostics_api.py`

**验收**：US-6 全部通过；`curl http://localhost:8000/api/ops/auth/diagnostics` 返回结构化 JSON；前端在 Refresh 耗尽时不再让用户看到"Proxy returned 401..."英文栈。

## 5. 设计细节

### 5.1 `auth_refresh_callback` 的契约扩展（Phase 1）

现状：`Callable[[], Awaitable[HandlerChainResult | None]]`

扩展：`Callable[[*, force: bool = False], Awaitable[HandlerChainResult | None]]`

- 向后兼容：老调用点不传 `force`，行为等价于 pre-emptive 刷新（受 `is_expired()` gate）
- 新调用点（reactive 401 retry 路径）传 `force=True`，绕开 `is_expired()` gate
- 多个 OAuth profile 轮询时，`force` 只对"当前请求所属 provider"生效，不是全局强刷

### 5.2 外部 CLI adopt 的 identity 门控（Phase 2）

借鉴 OpenClaw `isSafeToCopyOAuthIdentity` 精神，最小版本：

```python
def _is_safe_to_adopt_from_cli(
    existing: OAuthCredential | None,
    incoming: OAuthCredential,
) -> bool:
    # 没 existing → 任意 adopt
    if existing is None:
        return True
    # existing 和 incoming 都有 account_id → 必须相等
    if existing.account_id and incoming.account_id:
        return existing.account_id == incoming.account_id
    # existing 有但 incoming 没有 → 拒绝（防止 downgrade）
    if existing.account_id and not incoming.account_id:
        return False
    # 其他情况 → 允许（纯 upgrade 或都没 id）
    return True
```

refuse 时写 `OAUTH_ADOPTED_FROM_EXTERNAL_CLI` 但 `status=refused_identity_mismatch`，便于排查"用户是不是在 codex 里登错了账号"。

### 5.3 事件 schema

所有 OAuth 事件共享 payload 基础字段：
```
{
  "provider": str,          // e.g. "openai-codex"
  "profile": str,            // e.g. "openai-codex-default"
  "mode": "preemptive" | "reactive" | null,  // refresh 触发来源
  "source": "store" | "external_cli" | null,
  "error_type": str | null,  // LLMCallError.error_type 或 "refresh_token_reused" 等
  "timestamp_local": str,    // ISO 8601 便于人读
}
```

### 5.4 不改动的文件（scope lock）

- `octoagent.yaml` schema 不动
- `auth-profiles.json` schema 不动（新增 adopt 路径写的是既有字段）
- `SkillRunner` 主循环不动（只扩展 `LiteLLMSkillClient` 构造器）
- CLI 不加新命令（诊断走 `octo doctor` 既有入口或 `curl /api/ops/auth/diagnostics`）

## 6. 风险与回退

| 风险 | 严重度 | 缓解 |
|---|---|---|
| Phase 1 的 401 retry 在真 401（凭证彻底失效）场景下会多发一次 refresh 请求，冲击 token endpoint | 低 | 每个 provider+profile 单次请求周期内最多 1 次 reactive refresh，配合 `TokenRefreshCoordinator` 的 in-memory 锁 |
| Phase 2 读 `~/.codex/auth.json` 时被恶意软件写了坏文件 | 低 | 只读 + JSON schema 严格校验，任何异常都 fallback 到自管 store；不执行文件内容 |
| Phase 2 用户在 OctoAgent 和 Codex CLI 登了不同账号 | 中 | identity gate 拒绝 adopt，写 refused 事件，用户在诊断页能看到 |
| Phase 3 `_is_refresh_token_reused_error` 误伤真失效 case | 低 | reload store 后 credential 没变才走"需重登"路径；未命中不影响既有行为 |
| Phase 4 新事件类型让 events 表膨胀 | 低 | 一次对话最多 +1~2 条 OAuth 事件，相对现有 SKILL_COMPLETED 等已有事件量可忽略 |

**回退策略**：每个 Phase 独立一个 commit（或 2~3 个）；如发现严重 regression 直接 `git revert` 对应 Phase 的 commit，不影响其他 Phase。

## 7. Open Questions

1. **多实例并发**：如果未来同时跑 Gateway + 独立 Worker 进程都用同一 `auth-profiles.json`，需要引入跨进程文件锁（OpenClaw 的 `withFileLock` 模式）。当前单进程不需要，但要在 Phase 2 的 external CLI adopt 路径先预留锁的位置（写入 profile 时用 `fcntl.LOCK_EX`），避免未来接入时被破坏。
2. **Codex CLI 不存在但 CLI 名字叫别的**：用户可能装了类似工具用别的路径。Phase 2 留 env `OCTOAGENT_CODEX_AUTH_PATH` 让用户自定义。
3. **Responses API 直连路径的 401**：`ResponsesApiProvider` 走 direct_params 时绕开 Proxy 直连 Codex Backend，这条路径的 401 返回格式与 Proxy 不同，需要单独识别。Phase 1 要覆盖两条路径。
4. **未来 refresh 失败的用户体验**：现在 US-4 的提示是中文 chat bubble，但 Telegram 渠道怎么呈现？先复用现有"系统消息"机制，但 Phase 4 可能需要专门的 channel-aware 提示。

## 8. 相邻问题（follow-up）

本 Feature 不涉及但已知相关的问题，留作独立 fix：

- **`octo setup` 依赖 Gateway 在线**：用户在 Gateway 挂了之后没法 setup，形成死锁。改为 setup 可独立运行（读配置、本地起临时 httpd 做 OAuth callback、写 `auth-profiles.json`）即可。
- **Web UI 只能授权 Anthropic Claude，不支持 Codex / SiliconFlow**：今天你实际遇到的 UX 坑。独立的前端 Feature。
- **`MODEL_CALL_FAILED` 空 error_message**：已在 commit `db9c081` (Feature 079 旁支) 修复，但底层 `providers.py` 可能还有类似空异常的路径，Phase 4 诊断页实际运行时若发现再补。

## 9. 成功指标

- **功能**：US-1 ~ US-6 所有 acceptance scenarios 通过（单元 + 集成）
- **可靠性**：新造 4 种场景（token 自然过期 / 服务端提前 revoke / CLI 有新 token / refresh_token 虚误 reused）能被正确处理
- **观测**：`events.db` 里出现预期的 6 类新事件；`/api/ops/auth/diagnostics` 返回完整状态
- **对照**：人工重现今天 task `01KPGQGC1J447N1EV5JN9EBK9M` 的场景（改坏 access_token），应看到 **1 次** `OAUTH_REFRESH_TRIGGERED(reactive)` + **1 次** `OAUTH_REFRESHED`，然后对话正常返回（不再有 12 连失败）

## 10. 任务分解概览（详见 tasks.md）

- T1. Phase 1：Skill hookup + force refresh（~6 任务，包含测试）
- T2. Phase 2：Codex CLI bridge（~7 任务，包含 identity gate）
- T3. Phase 3：reused recovery + 硬超时（~5 任务）
- T4. Phase 4：观测 + 诊断 API + 前端卡片（~6 任务）
- T5. 收尾：CLAUDE.md 的 Milestone 更新 + blueprint 同步 + M4/M5 check-in

预估工时：Phase 1 半天，Phase 2 半天，Phase 3 半天，Phase 4 半天到一天，总计 **2~2.5 天**。Phase 1 单独落地即可解决今天所有 401 风暴问题。
