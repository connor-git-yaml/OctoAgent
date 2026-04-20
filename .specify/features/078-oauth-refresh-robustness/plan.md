# Feature 078 — 实施计划（plan.md）

> 作者：Connor（orchestrator 生成）
> 日期：2026-04-19
> 模式：spec-driver-story
> 上游：spec.md
> 下游：tasks.md

## 0. 执行策略

Phase 1 ~ 4 都是独立可落地的 commit。**建议分 4 次 commit、每次独立验证**；commit 粒度和 spec.md 的分阶段对齐，避免一次 PR 过大。

## 1. 架构改动地图

```
┌─────────────────── gateway ──────────────────┐
│ main.py:579-592                               │
│   SkillRunner(model_client=LiteLLMSkillClient(│
│     ...                                       │
│     + auth_refresh_callback=...  ← Phase 1    │
│   ))                                          │
│                                               │
│ services/auth_refresh.py                      │
│   build_auth_refresh_callback(...)            │
│     + 支持 force=True 透传         ← Phase 1  │
│                                               │
│ services/ops.py (新增诊断路由)                │
│   GET /api/ops/auth/diagnostics   ← Phase 4   │
└───────────────────────────────────────────────┘
                      │
┌──────────────── provider/auth ────────────────┐
│ pkce_oauth_adapter.py                          │
│   resolve(force_refresh: bool) ← Phase 1      │
│   refresh(): 新增 reused recovery ← Phase 3   │
│                                                │
│ codex_cli_bridge.py (新增)       ← Phase 2    │
│   read_codex_cli_auth(env)                    │
│                                                │
│ store.py                                       │
│   adopt_from_external(...)       ← Phase 2    │
│                                                │
│ oauth_flows.py                                │
│   refresh_access_token(timeout_s) ← Phase 3   │
│                                                │
│ events.py + core/enums.py                     │
│   新增 6 个 OAuth 事件类型       ← Phase 4    │
└───────────────────────────────────────────────┘
                      │
┌──────────────── skills ──────────────────────┐
│ litellm_client.py                              │
│   __init__(auth_refresh_callback)  ← Phase 1  │
│                                                │
│ providers.py                                   │
│   ChatCompletionsProvider.call:                │
│     401 → callback(force=True) → retry 1 次   │
│   ResponsesApiProvider.call: 同上 ← Phase 1   │
└───────────────────────────────────────────────┘
                      │
┌──────────────── frontend (可选) ─────────────┐
│ Settings 页 / 诊断页                          │
│   OAuth 凭证状态卡片             ← Phase 4    │
└───────────────────────────────────────────────┘
```

## 2. 关键设计决策

### 2.1 `auth_refresh_callback` 契约扩展

现状（单参数）：`Callable[[], Awaitable[HandlerChainResult | None]]`

扩展后（兼容式）：`Callable[..., Awaitable[HandlerChainResult | None]]` —— 实现用 `**kwargs` 吞掉未来扩展。本次先加 `force: bool = False`。

**为什么用 kwargs 而非 positional**：
- 多个调用点（client.py, providers.py）混用，kwargs 让"不关心 force 的旧调用点"继续调用 `await cb()`，向后兼容
- 未来可能再加 `profile_hint: str | None` 做精准刷新，也走 kwargs

### 2.2 `PkceOAuthAdapter.resolve(force_refresh=False)`

```python
async def resolve(self, *, force_refresh: bool = False) -> str:
    value = self._credential.access_token.get_secret_value()
    if not value:
        raise CredentialNotFoundError(...)

    if force_refresh or self.is_expired():
        refreshed = await self.refresh()
        if refreshed is not None:
            return refreshed
        # 保留既有行为：refresh 失败回落现值（force 模式下拿到 401 就立即告警）
        if not force_refresh and not self.is_expired():
            return value  # 预检查 failed 但当前 token 还在有效期
        raise CredentialExpiredError(...)

    return value
```

### 2.3 401 重试路径（providers.py）

**每个 provider call() 方法添加一层外包装**：

```python
async def call(self, *, ..., auth_refresh_callback=None) -> (content, tool_calls, metadata):
    try:
        return await self._call_once(...)
    except LLMCallError as exc:
        if exc.status_code != 401 or auth_refresh_callback is None:
            raise
        # 401 且有 callback：调用 force refresh
        refreshed = await auth_refresh_callback(force=True)
        if refreshed is None:
            raise  # refresh 失败，抛原 401
        # 重试一次（仅一次，避免风暴）
        return await self._call_once(...)  # 用刷新后的 token（通过 env var / os.environ 同步）
```

**约束**：每个 provider call 内最多 1 次 reactive refresh；不做递归重试。

### 2.4 Codex CLI 外挂路径（Phase 2）

**关键抉择**：是 `CredentialStore.get_profile(prefer_external=True)` 入侵式改造，还是独立 `CodexCliBridge` 模块？

选择**独立模块** + 在 `auth_refresh_callback` 调用 `PkceOAuthAdapter.refresh()` 失败后作为**最后一根稻草**触发：

```python
# auth_refresh.py 内
token = await coord.refresh_if_needed(...)
if token is None and profile.provider == "openai-codex":
    # 尝试从 Codex CLI adopt
    external = read_codex_cli_auth()
    if external and _is_safe_to_adopt(existing=profile.credential, incoming=external):
        store.adopt_from_external(profile.name, external)
        token = external.access_token.get_secret_value()
```

**理由**：
- 不改 `CredentialStore` 的默认行为（其他 provider 用户无感）
- adopt 只在 refresh 失败时触发，避免每次调用都 stat 外部文件
- `_is_safe_to_adopt` gate 挡住跨账号拷贝

### 2.5 Events 扩展（Phase 4）

新增 `EventType`：
- `OAUTH_REFRESH_TRIGGERED`（mode=preemptive|reactive）
- `OAUTH_REFRESH_FAILED`（含 error_type）
- `OAUTH_REFRESH_RECOVERED`（via=store_reload|external_cli）
- `OAUTH_REFRESH_EXHAUSTED`
- `OAUTH_ADOPTED_FROM_EXTERNAL_CLI`

既有 `OAUTH_REFRESHED` 继续用，payload 加 `mode` 字段。

## 3. 测试策略

| Phase | 核心测试 | 策略 |
|-------|---------|------|
| 1 | `test_litellm_skill_client_refresh_on_401.py` | mock httpx 返回 401 → 200，验证 callback 被调用一次 |
| 1 | `test_pkce_oauth_adapter_force_refresh.py` | force=True 绕开 is_expired gate |
| 2 | `test_codex_cli_bridge.py` | fixture 伪造 `~/.codex/auth.json`，验证 read / adopt / identity gate |
| 3 | `test_pkce_oauth_adapter_reused_recovery.py` | mock `invalid_grant` + store reload 场景 |
| 3 | `test_oauth_refresh_timeout.py` | mock curl 卡住 > timeout_s |
| 4 | `test_auth_diagnostics_api.py` | 端点响应结构 + 脱敏 |
| 4 | `test_events_oauth_new_types.py` | 6 个新事件类型枚举 + payload 合规 |

## 4. 风险与缓解（已在 spec §6 覆盖，此处补实施期提醒）

- **测试期污染真实凭证**：所有测试用 `tmp_path` + fixture-scoped CredentialStore，绝不读 `~/.octoagent/auth-profiles.json`
- **providers.py 的 401 分类要准**：`_classify_proxy_error` 已经把 4xx 归类，但要确认 401 明确返回 `status_code=401` 供 retry 判断
- **向后兼容**：所有 callback 签名改动用 `**kwargs`；不动已有测试用例的调用形式

## 5. 不改动的文件（scope lock）

- `LiteLLMClient`（老路径）的 callback 签名不扩展，继续走 `await cb()`
- `auth-profiles.json` schema 不变
- `octoagent.yaml` 无新字段
- `providers.py` 的 `_classify_proxy_error` / `_merge_system_messages_to_front` 等辅助函数不动
