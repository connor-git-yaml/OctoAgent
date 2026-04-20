# Feature 078 — 任务拆解（tasks.md）

> 作者：Connor（orchestrator 生成）
> 日期：2026-04-19
> 上游：plan.md
> 模式：spec-driver-story

每一行任务都独立可验证，按 Phase 分组。Phase 之间可以分多次 commit；单 Phase 内任务顺序执行。

---

## Phase 1 — Skill 路径接通 + 主动刷新（P0，半天）

### P1.1 — PkceOAuthAdapter.resolve 增加 force_refresh 形参

**文件**：`octoagent/packages/provider/src/octoagent/provider/auth/pkce_oauth_adapter.py`

- [ ] `resolve()` 签名改为 `async def resolve(self, *, force_refresh: bool = False) -> str`
- [ ] 条件 `if self.is_expired():` 改为 `if force_refresh or self.is_expired():`
- [ ] 保留现有"refresh 失败回落现值"逻辑（仅在 `force_refresh=False and not is_expired()` 时回落）
- [ ] `force_refresh=True` 且 refresh 失败时必须抛 `CredentialExpiredError`

**测试**：`octoagent/packages/provider/tests/test_pkce_oauth_adapter_force_refresh.py`
- [ ] test: `resolve(force_refresh=True)` 在未过期时也调用 `refresh()`
- [ ] test: `resolve(force_refresh=False)` 在未过期时不调用 `refresh()`（回归既有行为）
- [ ] test: `resolve(force_refresh=True)` 且 refresh 返回 None 时抛 `CredentialExpiredError`

### P1.2 — TokenRefreshCoordinator 支持 force 透传

**文件**：`octoagent/packages/provider/src/octoagent/provider/refresh_coordinator.py`

- [ ] `refresh_if_needed()` 签名扩展：`async def refresh_if_needed(self, profile_name: str, adapter, *, force: bool = False)`
- [ ] force=True 时绕过"跳过阈值"/缓存判断，始终调 `adapter.refresh()`
- [ ] 保持 per-provider `asyncio.Lock` 语义（仍串行，但不跳过）

**测试**：`octoagent/packages/provider/tests/test_refresh_coordinator_force.py`
- [ ] test: `force=True` 且 `not is_expired()` 仍调 refresh
- [ ] test: `force=True` 在并发场景下仍受 Lock 约束（不产生风暴）

### P1.3 — auth_refresh_callback 契约扩展

**文件**：`octoagent/apps/gateway/src/octoagent/gateway/services/auth_refresh.py`

- [ ] `build_auth_refresh_callback()` 返回的 callback 签名改为 `async def callback(**kwargs) -> HandlerChainResult | None`
- [ ] 解析 `force = kwargs.get("force", False)`
- [ ] 把 `force` 透传到 `coord.refresh_if_needed(..., force=force)` 和 `adapter.resolve(force_refresh=force)`（如需 resolve 路径）
- [ ] 保持既有 `await cb()` 调用方（client.py 的 LiteLLMClient 路径）能继续工作（kwargs 默认值生效）

### P1.4 — LiteLLMSkillClient 接收 callback

**文件**：`octoagent/packages/skills/src/octoagent/skills/litellm_client.py`

- [ ] `LiteLLMSkillClient.__init__` 增加 `auth_refresh_callback: Callable[..., Awaitable[Any]] | None = None` 参数
- [ ] 保存到 `self._auth_refresh_callback`
- [ ] 构造 `ChatCompletionsProvider` / `ResponsesApiProvider` 时透传该 callback

### P1.5 — ChatCompletionsProvider / ResponsesApiProvider 401 重试

**文件**：`octoagent/packages/skills/src/octoagent/skills/providers.py`

- [ ] `ChatCompletionsProvider.__init__` 接收并保存 `auth_refresh_callback`
- [ ] `ChatCompletionsProvider.call` 拆为 `_call_once`（原逻辑）+ `call`（外包装）
- [ ] 外包装捕获 `LLMCallError` with `status_code == 401`：若有 callback → `await callback(force=True)` → 重试 1 次
- [ ] `ResponsesApiProvider` 做同样改造
- [ ] 单 `call` 内最多 1 次 reactive refresh，避免递归风暴
- [ ] 确认 `_classify_proxy_error` 或 httpx path 对 401 明确带上 `status_code=401`（必要时补齐）

**测试**：`octoagent/packages/skills/tests/test_skill_client_refresh_on_401.py`
- [ ] test: httpx mock 返回 401 → 200，callback 被调用 1 次，retry 成功
- [ ] test: callback 为 None 时，401 直接抛错（既有行为）
- [ ] test: 两次连续 401（refresh 后仍 401）不递归重试
- [ ] test: ResponsesApiProvider 同上三条

### P1.6 — main.py 组装 SkillRunner 时挂 callback

**文件**：`octoagent/apps/gateway/src/octoagent/gateway/main.py`（约 579-592 行）

- [ ] 在 `LiteLLMSkillClient(...)` 构造时添加 `auth_refresh_callback=auth_refresh_callback` 形参
- [ ] 确认 `auth_refresh_callback` 在此作用域内已正确初始化（跟 LiteLLMClient 旧路径共用一个变量）

**commit 节点**：`fix(auth+skills): Skill 路径接通 auth_refresh_callback，PkceOAuthAdapter 支持 force_refresh`

---

## Phase 2 — Codex CLI 外挂路径（P0，半天）

### P2.1 — codex_cli_bridge 新模块

**文件**：`octoagent/packages/provider/src/octoagent/provider/auth/codex_cli_bridge.py`（新建）

- [ ] `read_codex_cli_auth(home_override: Path | None = None) -> OAuthCredential | None`
- [ ] 读取 `~/.codex/auth.json`，解析 access_token / refresh_token / expires_at
- [ ] 缺文件 / 解析失败 / token 为空都返回 None（不抛）
- [ ] 读取时校验权限位不宽于 0o600（若宽 → 返回 None + log warning）

### P2.2 — _is_safe_to_adopt 身份 gate

**同上文件**

- [ ] `def _is_safe_to_adopt(*, existing: OAuthCredential | None, incoming: OAuthCredential) -> tuple[bool, str]`
- [ ] 若 existing.account_id 为空或与 incoming 一致 → (True, "match")
- [ ] 若 existing.account_id != incoming.account_id → (False, "account_mismatch")
- [ ] 若 JWT 中的 email 域名和 existing 历史记录域名不符（可放宽为 only warn）→ (True, "email_domain_diff") + log warning
- [ ] 返回值必须是 tuple，方便上层记录理由到 event

### P2.3 — CredentialStore.adopt_from_external

**文件**：`octoagent/packages/provider/src/octoagent/provider/auth/store.py`

- [ ] `def adopt_from_external(self, profile_name: str, credential: OAuthCredential) -> None`
- [ ] 只覆盖 access_token / refresh_token / expires_at / account_id（保留 name / provider / auth_mode / is_default / created_at）
- [ ] 更新 `updated_at`
- [ ] 持久化到磁盘（与 update_profile 相同的 write-through 路径）
- [ ] 禁止通过该接口改 provider（若 credential.provider 与 profile.provider 不符 → 抛错）

### P2.4 — auth_refresh 集成 adopt 作为最后一根稻草

**文件**：`octoagent/apps/gateway/src/octoagent/gateway/services/auth_refresh.py`

- [ ] 在 `build_auth_refresh_callback` 返回的 callback 中：
  - coord.refresh_if_needed(...) 返回 None 且 profile.provider == "openai-codex" → 尝试 adopt
  - 调用 `read_codex_cli_auth()`
  - 若有结果 → 调 `_is_safe_to_adopt(existing=..., incoming=...)`
  - 通过 gate → `store.adopt_from_external(...)` + emit `OAUTH_ADOPTED_FROM_EXTERNAL_CLI`（Phase 4 加，这里先埋点占位）
  - 不通过 → log warning + continue fallback
- [ ] adopt 仅在 refresh 失败分支触发，不要每次都 stat 外部文件

**测试**：`octoagent/packages/provider/tests/test_codex_cli_bridge.py`
- [ ] test: fixture 写 `~/.codex/auth.json`（tmp_path），read 成功
- [ ] test: 文件不存在返回 None
- [ ] test: 权限过宽返回 None
- [ ] test: account_id 不匹配时 _is_safe_to_adopt 返回 (False, reason)
- [ ] test: CredentialStore.adopt_from_external 不改 provider 字段

**集成测试**：`octoagent/apps/gateway/tests/test_auth_refresh_with_codex_adopt.py`
- [ ] test: mock coord.refresh_if_needed 返回 None + 模拟 Codex CLI 有效 auth.json → callback 成功返回 token 且触发 adopt
- [ ] test: 跨账号场景不触发 adopt

**commit 节点**：`feat(auth): 新增 Codex CLI 外挂路径，refresh 失败后尝试从 ~/.codex/auth.json adopt`

---

## Phase 3 — Reused Recovery + Timeout 硬护栏（P1，半天）

### P3.1 — oauth_flows.refresh_access_token 支持 timeout_s

**文件**：`octoagent/packages/provider/src/octoagent/provider/auth/oauth_flows.py`

- [ ] `refresh_access_token(..., timeout_s: float = 15.0)` 默认 15s
- [ ] curl 子进程以 `asyncio.wait_for(..., timeout=timeout_s)` 包裹
- [ ] 超时抛 `OAuthRefreshTimeoutError`（新异常类）
- [ ] curl 命令行增加 `--max-time {int(timeout_s)}` 双保险

**测试**：`octoagent/packages/provider/tests/test_oauth_refresh_timeout.py`
- [ ] test: mock subprocess 永不返回 → `asyncio.wait_for` 触发超时 → 抛 `OAuthRefreshTimeoutError`
- [ ] test: 正常 < timeout 的场景仍返回 token

### P3.2 — PkceOAuthAdapter.refresh 增加 reused recovery

**文件**：`octoagent/packages/provider/src/octoagent/provider/auth/pkce_oauth_adapter.py`

- [ ] `refresh()` 捕获 `invalid_grant` 错误后：
  - 先执行 "store reload" —— 从磁盘重新 load profile（防止是并发另一个进程/协程刚刷新了 token，当前内存态是旧的）
  - 用最新 refresh_token 再试 1 次
  - 仍失败 → 原来的 fallback（remove_profile）保持不变
- [ ] store reload 失败或 reload 后 refresh_token 与内存一致 → 不重试，直接 fallback
- [ ] 整段重试加明确的 `attempt_count ≤ 2` 上限

**测试**：`octoagent/packages/provider/tests/test_pkce_oauth_adapter_reused_recovery.py`
- [ ] test: 第一次 refresh 返回 `invalid_grant` / `refresh_token_reused`，store 磁盘有更新的 refresh_token → reload 后成功
- [ ] test: store 磁盘无变化 → 不重试，走原 fallback
- [ ] test: reload 后 refresh 仍失败 → 走原 fallback

### P3.3 — TokenRefreshCoordinator 整体 timeout

**文件**：`octoagent/packages/provider/src/octoagent/provider/refresh_coordinator.py`

- [ ] `refresh_if_needed(...)` 外层加 `asyncio.wait_for(..., timeout=30.0)`（包含 lock 等待 + 实际 refresh）
- [ ] 超时 log error + 返回 None（让上层走 fallback），不抛到业务调用方
- [ ] 超时不得持有 lock 导致后续调用全挂

**commit 节点**：`fix(auth): OAuth refresh 增加硬超时与 refresh_token_reused 的 store-reload recovery`

---

## Phase 4 — Observability + 诊断 API + 前端（P1，半天 ~ 一天）

### P4.1 — 新增 EventType 枚举

**文件**：`octoagent/packages/core/src/octoagent/core/models/enums.py`

- [ ] 添加 5 个新值：
  - `OAUTH_REFRESH_TRIGGERED = "oauth_refresh_triggered"`
  - `OAUTH_REFRESH_FAILED = "oauth_refresh_failed"`
  - `OAUTH_REFRESH_RECOVERED = "oauth_refresh_recovered"`
  - `OAUTH_REFRESH_EXHAUSTED = "oauth_refresh_exhausted"`
  - `OAUTH_ADOPTED_FROM_EXTERNAL_CLI = "oauth_adopted_from_external_cli"`
- [ ] 现有 `OAUTH_REFRESHED` 保留，payload 增加 `mode: Literal["preemptive", "reactive"]` 字段（文档 + emit 处同步）

**测试**：`octoagent/packages/core/tests/test_event_types_oauth.py`
- [ ] test: 5 个新枚举可序列化（to dict / to json）
- [ ] test: 名称遵循 lower_snake_case 约定

### P4.2 — emit_oauth_event 扩展 payload

**文件**：`octoagent/packages/provider/src/octoagent/provider/auth/events.py`

- [ ] 新增 helper：`emit_refresh_triggered(mode, profile, reason=None)`
- [ ] 新增 helper：`emit_refresh_failed(profile, error_type, retry_count)`
- [ ] 新增 helper：`emit_refresh_recovered(profile, via)` with `via: Literal["store_reload", "external_cli"]`
- [ ] 新增 helper：`emit_refresh_exhausted(profile, attempt_count, last_error)`
- [ ] 新增 helper：`emit_adopted_from_external_cli(profile, source_path, gate_reason)`
- [ ] 所有 payload 经现有"sensitive field filter"脱敏（token / refresh_token / account_id 不得入事件）

### P4.3 — 埋点注入各 refresh 路径

- [ ] `PkceOAuthAdapter.refresh()` 入口 → `emit_refresh_triggered(mode="preemptive" | "reactive")`
- [ ] `PkceOAuthAdapter.refresh()` 成功 → 既有 `emit_oauth_refreshed(mode=...)`（注意传 mode）
- [ ] 401 reactive path（providers.py）→ 记录 `mode="reactive"`
- [ ] 预检查 force_refresh path（PkceOAuthAdapter.resolve）→ 记录 `mode="preemptive"`（或新增 `mode="forced_preemptive"`，与原 mode 枚举保持一致；建议合并到 preemptive，force 字段另记）
- [ ] refresh 失败 → `emit_refresh_failed(error_type=...)`
- [ ] store reload recovery 成功 → `emit_refresh_recovered(via="store_reload")`
- [ ] Codex CLI adopt 成功 → `emit_adopted_from_external_cli(...)` + `emit_refresh_recovered(via="external_cli")`
- [ ] 所有 fallback 都失败 → `emit_refresh_exhausted(...)`

### P4.4 — Diagnostics 只读 API

**文件**：`octoagent/apps/gateway/src/octoagent/gateway/services/ops.py`（新建或现有 ops 文件追加）

- [ ] `GET /api/ops/auth/diagnostics` 路由
- [ ] 响应结构：
  ```json
  {
    "profiles": [
      {
        "name": "openai-codex-default",
        "provider": "openai-codex",
        "auth_mode": "oauth",
        "is_default": true,
        "expires_at": "2026-04-29T13:37:49+00:00",
        "expires_in_seconds": 864000,
        "is_expired": false,
        "last_refresh_at": null,
        "last_refresh_mode": null,
        "codex_cli_external_available": true
      }
    ]
  }
  ```
- [ ] **绝对不**返回 access_token / refresh_token / account_id 原值
- [ ] 路由挂到现有 ops prefix（与 /api/ops/tasks 等同级）
- [ ] 需要通过现有 auth/policy middleware（管理员/本地访问才能读）

**测试**：`octoagent/apps/gateway/tests/test_auth_diagnostics_api.py`
- [ ] test: 响应结构正确 + 字段脱敏
- [ ] test: access_token 不出现在响应任何位置（扫描 JSON）
- [ ] test: 有/无 Codex CLI 外部文件两种场景

### P4.5 — 前端凭证状态卡片（可选、最后做）

**文件**：`octoagent/apps/frontend/...`（设置页 / 诊断页，具体位置看现有 Settings 结构）

- [ ] 新增 `OAuthCredentialStatus` 组件：
  - 显示 profile name / provider
  - 过期倒计时（相对时间，如 "还有 10 天"）
  - 绿色 / 黄色（≤24h）/ 红色（已过期）徽标
  - 是否检测到 Codex CLI 外部凭证（若是 + 不是 default profile → 展示 "可接管" 按钮，调现有 API）
- [ ] 接入现有诊断页导航
- [ ] 不要加自动轮询（避免空转），用户进入页面时主动 fetch 一次

**测试**：前端单元测试（如项目有 vitest/jest）或至少 preview 验证一遍 UI

**commit 节点**：
- `feat(auth+events): 新增 5 个 OAuth refresh 事件类型与埋点`
- `feat(gateway+frontend): OAuth 凭证诊断 API 与前端状态卡片`

---

## 验收自检清单（上线前跑一遍）

### 功能自检
- [ ] 把 access_token 人为改成非法值 → Skill 路径会触发 reactive refresh + retry（Phase 1）
- [ ] 把 `~/.octoagent/auth-profiles.json` 的 access_token 置空 + `~/.codex/auth.json` 正常 → adopt 成功（Phase 2）
- [ ] 在 refresh 端点 mock 网络挂起 → 30s 后 coord 超时返回 None，不阻塞业务线程（Phase 3）
- [ ] 查看事件流（Web 任务详情）→ 能看到 OAUTH_REFRESH_TRIGGERED / RECOVERED / EXHAUSTED 事件（Phase 4）
- [ ] `GET /api/ops/auth/diagnostics` 响应中无任何 token 明文（Phase 4）

### 工具链自检
- [ ] `uv run pytest` 全量通过
- [ ] `uv run mypy` / pyright 无新 error
- [ ] `uv run ruff check` 无新 warning
- [ ] 前端 `pnpm typecheck && pnpm test` 通过
- [ ] Blueprint (`docs/blueprint/*`) 若涉及架构变更需同步更新（本 Feature 主要是修 bug，蓝图不需要动）

### 不得破坏的既有行为（回归保底）
- [ ] 未过期 + 未 force 时，resolve 不触发 refresh（零回归）
- [ ] LiteLLMClient 老路径不受影响（签名默认值向后兼容）
- [ ] siliconflow / anthropic-claude profile 不走 Codex CLI adopt 分支
- [ ] 已存在的 `test_auth_profile_credentials.py` 等相关测试保持通过

---

## 任务总览

| Phase | 核心任务数 | 预计文件变更 | 预计用时 | 优先级 |
|-------|-----------|------------|---------|--------|
| 1     | 6         | ~5         | 半天    | P0     |
| 2     | 4         | 3 新 + 1 改 | 半天    | P0     |
| 3     | 3         | 3          | 半天    | P1     |
| 4     | 5 (+前端) | 4-6        | 半天~一天 | P1   |

**最小收敛版本**：Phase 1 即解决今天的 401 风暴（Task `01KPGQGC1J447N1EV5JN9EBK9M`）
**完整闭环版本**：Phase 1-4 全做完
