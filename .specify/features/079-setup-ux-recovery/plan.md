# Feature 079 — Setup UX Recovery & 架构对账

> 作者：Connor（orchestrator 生成）
> 日期：2026-04-20
> 模式：spec-driver-story（轻量 — 无 spec.md，plan 自带背景）
> 上游：Feature 078 后用户事故复盘（OAuth 授权成功但 main=gpt-5.4 保存失败 + chunk 404 截图）
> 下游：tasks.md

## 0. 背景 & 事故复盘

### 0.1 触发事件

用户在 Feature 078 推 master 之后，在 Web UI 重新授权 Codex、把 `main` 模型改成 `gpt-5.4`、点击保存。结果：
1. UI 弹出"页面刚更新，请刷新一次"卡片（chunk 404 `SettingsCenter-duGbwulz.js`）
2. 刷新后发现 `~/.octoagent/octoagent.yaml` 里 `main` 仍然是 `siliconflow / Qwen/Qwen3.5-32B`
3. `providers[]` 里根本没有 `openai-codex`（尽管 `auth-profiles.json` 里 OAuth 授权已经落盘）

### 0.2 三条独立问题链叠加

| # | 问题 | 表现 | 根因 |
|---|------|------|------|
| B1 | chunk 404 + 全页 ErrorBoundary | 截图里那张"请刷新"卡片 | 浏览器长时间开着的 HTML 指向旧 hash；`RootErrorBoundary` 覆盖整棵子树，把保存 banner 也一并吞掉 |
| B2 | OAuth 授权 ≠ provider 入 config | `auth-profiles.json` 有 codex，但 `octoagent.yaml.providers[]` 没有 | `handleOpenAIOAuthConnect` 只更新 React state（`providerDrafts`）和 `auth-profiles.json`，**不自动触发 `setup.apply`**。用户必须再点一次保存 |
| B3 | 保存失败后错误信息不可见 | 用户看到 "请刷新" 卡片就没了 | `buildSetupDraft()` 里 `fieldErrors > 0` 时 silent return；后端 `SETUP_REVIEW_BLOCKED` 的 banner 被 B1 的全页 ErrorBoundary 遮住 |

### 0.3 架构坏味道（非 bug 但助长事故）

- **Smell A**：OAuth 凭证和 provider 条目是一对孪生事实，但落盘路径不对等（一个立即，一个需显式保存），UI 语义未对齐
- **Smell B**：前端 build 版本对客户端不可观测，旧 HTML 只能等 chunk 404 炸
- **Smell C**：`RootErrorBoundary` 粒度过大（整棵 App），局部 chunk 404 等于整页宕机
- **Smell D**：`auth-profiles.json` 与 `octoagent.yaml` 缺少一致性对账（没人发现 OAuth provider 没入 config）

## 1. 用户故事

- **US-1**（P0）：作为用户，我授权 OAuth 后应立即看到该 provider 的状态（pending / saved），不再出现"授权了但系统不认"的错位
- **US-2**（P0）：保存失败时，错误信息应当显式且可读，不因其它子树崩溃而被吞掉
- **US-3**（P0）：局部页面 chunk 404 不应让整站宕机，应在 route 范围内恢复
- **US-4**（P1）：系统刚更新时，浏览器应知道"我在用旧版"，温和提示刷新
- **US-5**（P1）：`auth-profiles.json` 与 `octoagent.yaml` 漂移时，诊断 API 应暴露，并在启动日志 warn
- **US-6**（P1）：后端应对 review 的 blocking_reasons 给出结构化描述，前端渲染成可操作的 modal

## 2. 执行策略

4 个 Phase 各自独立 commit，Phase 之间无强依赖但按优先级推进：

| Phase | 内容 | 优先级 | 预计用时 |
|-------|------|-------|---------|
| P1 | 止血：per-route ErrorBoundary + 错误可视化 + pending badge | P0 | 半天 |
| P2 | 链路闭环：OAuth 授权 follow-up 保存 + 后端原子化 endpoint | P0 | 半天 |
| P3 | 版本防御：build-id 指纹 + 漂移检测 + 自动重试 | P1 | 半天 |
| P4 | 对账可观测：auth-profiles ↔ octoagent.yaml 漂移诊断 + blocking_reasons 结构化 | P1 | 半天 |

**总计 ~2 天**，与 Feature 078 量级相当。建议 P1+P2 先合并走，**把用户今天卡住的 UX 灾难立刻止血**；P3+P4 作为第二轮。

## 3. 架构改动地图

```
┌─────────────── frontend ─────────────────────────┐
│ App.tsx                                           │
│   + RouteErrorBoundary wrapping each lazy route   │ ← P1
│                                                   │
│ components/shell/                                 │
│   RootErrorBoundary.tsx         缩小作用域        │ ← P1
│   + RouteErrorBoundary.tsx (新)                   │ ← P1
│   + BuildVersionWatcher.tsx (新)                  │ ← P3
│                                                   │
│ domains/settings/SettingsPage.tsx                 │
│   handleApply: 显示 fieldErrors / blocking modal  │ ← P1
│   handleOpenAIOAuthConnect: 成功后 auto-apply     │ ← P2
│   + PendingChangesBar 组件                        │ ← P1/P2 共用 │
│                                                   │
│ platform/queries/useWorkbenchData.ts              │
│   buildActionRefreshOptions: 加 oauth_and_apply   │ ← P2
│                                                   │
│ vite.config.ts                                    │
│   define: __BUILD_ID__ = timestamp+hash           │ ← P3
└───────────────────────────────────────────────────┘
                      │
┌────────────── gateway (backend) ──────────────────┐
│ routes/ops.py                                     │
│   GET /api/ops/frontend-version (新)              │ ← P3
│                                                   │
│ services/auth_refresh.py (或新文件)                │
│   detect_provider_config_drift()                  │ ← P4
│                                                   │
│ routes/ops.py GET /api/ops/auth/diagnostics       │
│   payload 加 config_drift 字段                    │ ← P4
│                                                   │
│ services/control_plane/setup_service.py           │
│   + _handle_setup_oauth_and_apply (新 action)     │ ← P2
│   _handle_setup_apply:                            │
│     blocking_reasons 改结构化（risk_id + hint）   │ ← P4
│                                                   │
│ main.py lifespan                                  │
│   启动时调用 drift 检查 + log warn                │ ← P4
└───────────────────────────────────────────────────┘
```

## 4. Phase 1 — 止血（半天，P0）

### 4.1 目标

让下一次用户操作保存失败时**立刻看到原因**，即便某个 lazy 子页面 chunk 404。

### 4.2 任务

#### P1.1 — per-route ErrorBoundary

**文件**：
- 新增：`octoagent/frontend/src/components/shell/RouteErrorBoundary.tsx`
- 修改：`octoagent/frontend/src/App.tsx`
- 修改：`octoagent/frontend/src/components/shell/RootErrorBoundary.tsx`（缩小作用域）

**做法**：
- `RouteErrorBoundary` 只捕获**一条 Route 内部**的错误，渲染"此页面加载失败"卡片 + Retry 按钮（Retry = reset boundary + 重新 lazy import）
- App.tsx 每个 `withRouteSuspense(<X />)` 外再包一层 `<RouteErrorBoundary key={routeKey}>`
- `RootErrorBoundary` 只兜底 shell 级崩溃（WorkbenchLayout 外层），不再是唯一防线

**验收**：触发 chunk 404 → 只有该 route 的卡片出现，**WorkbenchLayout 的 shell + sidebar + banner 保持可见**

#### P1.2 — handleApply 错误可视化

**文件**：`octoagent/frontend/src/domains/settings/SettingsPage.tsx`

**做法**：
- `buildSetupDraft()` 返回 null 时，不能 silent return。新增 `setErrorModal({ title, items })`，把 `fieldErrors` 逐项列出来
- `handleApply()` 捕获 `submitAction` 抛出的 `SETUP_REVIEW_BLOCKED`，把 `blocking_reasons` 渲染到同一个 modal
- modal 位置固定在页面顶部（不受 RouteErrorBoundary 崩溃影响）

**验收**：
- 场景 A：main alias 缺 provider → modal 显示"main alias 的 provider 不在 providers 列表"
- 场景 B：secret_missing:CODEX_API_KEY → modal 显示"secret 未绑定，请先补齐"

#### P1.3 — PendingChangesBar

**文件**：
- 新增：`octoagent/frontend/src/domains/settings/PendingChangesBar.tsx`
- 修改：`octoagent/frontend/src/domains/settings/SettingsPage.tsx`

**做法**：
- 用 `draftRequiresRuntimeRefresh()` 现有逻辑反推"当前是否有未保存变更"
- 有 pending draft 时在 Settings 页顶部显示 sticky bar：`"⚠️ 你有 N 个未保存的变更：providers, model_aliases..."` + 一个"立即保存"按钮直通 `handleApply`
- OAuth 授权成功后自动亮起（因为 providerDrafts 已变）

**验收**：授权完 Codex 后立刻出现 sticky bar，点它可以直接触发保存

### 4.3 Phase 1 测试

- `RouteErrorBoundary.test.tsx`：抛 chunk 404 error，验证只渲染局部卡片 + 不穿透到 root
- `SettingsPage.test.tsx`：加 3 条 case — fieldErrors 弹 modal / blocking_reasons 弹 modal / pending bar 可见性
- 现有测试跟踪：保证不破坏 Settings 保存主路径

### 4.4 Phase 1 commit

`feat(frontend): per-route ErrorBoundary + 保存失败可视化 + pending changes bar`

## 5. Phase 2 — 链路闭环（半天，P0）

### 5.1 目标

**"授权" = "provider 真正可用"**。消除"授权成功但 provider 没入 config" 的断层。

### 5.2 任务

#### P2.1 — 后端新增 `setup.oauth_and_apply` 原子 action

**文件**：`octoagent/apps/gateway/src/octoagent/gateway/services/control_plane/setup_service.py`

**做法**：
- 新增 `_handle_setup_oauth_and_apply`，params 接受 `provider_id`、`env_name`、`profile_name`、`draft`
- 内部顺序：
  1. 调 `_handle_provider_oauth_openai_codex`（或一般化的 oauth handler）→ 获取 credential
  2. 自动把 provider 加到 draft.config.providers（如尚未）
  3. 调 `_handle_setup_apply(request with draft)`
  4. 如果 apply blocking：不回滚 OAuth（credential 已落盘无害），但明确返回 `BLOCKED_AFTER_OAUTH` 让前端知道
- 注册到 action dispatch：`"setup.oauth_and_apply": self._handle_setup_oauth_and_apply`

**测试**：`test_setup_oauth_and_apply.py`
- OAuth 成功 + apply 成功 → 一次 action 完成
- OAuth 成功 + apply blocking → 返回结构化 error，credential 保留
- OAuth 失败 → 不触达 apply

#### P2.2 — 前端改用原子 action

**文件**：`octoagent/frontend/src/domains/settings/SettingsPage.tsx`

**做法**：
- `handleOpenAIOAuthConnect` 改成先本地 mutate draft（加 provider），再调 `submitAction("setup.oauth_and_apply", { draft, env_name, profile_name })`
- 成功后自动 setReview + 清 pending state
- 失败走 P1.2 的 modal 路径

**验收**：点"连接 OpenAI Codex"一次点击即完成授权 + 保存 + 通过 review

#### P2.3 — buildActionRefreshOptions 识别新 action

**文件**：`octoagent/frontend/src/platform/queries/useWorkbenchData.ts`（及相关 action refresh 映射）

**做法**：新 action 触发的资源刷新范围 = 原两个 action 的并集（config_schema、diagnostics_summary、auth_profiles、setup_governance）

### 5.3 Phase 2 commit

`feat(setup): oauth_and_apply 原子 action — 授权即入 config`

## 6. Phase 3 — 版本防御（半天，P1）

### 6.1 目标

消除"chunk 404 灾难"的根因：让浏览器知道自己在用旧版，温和提示刷新；ErrorBoundary 作为最后兜底。

### 6.2 任务

#### P3.1 — Vite 注入 build-id

**文件**：
- 修改：`octoagent/frontend/vite.config.ts`
- 修改：`octoagent/frontend/index.html`

**做法**：
- `vite.config.ts` 里 `define: { __BUILD_ID__: JSON.stringify(...) }`，值用 `Date.now() + git rev-parse --short HEAD`
- `index.html` 里 `<meta name="app-build-id" content="%__BUILD_ID__%">`（Vite 启动/构建时替换）
- `build` 时写入 dist，`dev` 时用开发 placeholder

#### P3.2 — 后端 `/api/ops/frontend-version` endpoint

**文件**：`octoagent/apps/gateway/src/octoagent/gateway/routes/ops.py`

**做法**：
- 启动时读取 `frontend/dist/index.html`，extract `<meta name="app-build-id">`
- endpoint 返回 `{ "build_id": "...", "served_at": timestamp }`
- 缓存 `build_id`，但用 `os.stat(index.html).st_mtime` 做 invalidation（rebuild 自动刷新）

#### P3.3 — `BuildVersionWatcher` 前端组件

**文件**：新增 `octoagent/frontend/src/components/shell/BuildVersionWatcher.tsx`

**做法**：
- 启动时记下 `__BUILD_ID__`
- 每 10 分钟（或 SSE 重连时）fetch `/api/ops/frontend-version`
- mismatch → 渲染温和 toast "有新版本可用，点此刷新"（非强制）
- 点刷新 = `window.location.reload()` 加时间戳绕缓存

#### P3.4 — RootErrorBoundary 自动重试

**文件**：`octoagent/frontend/src/components/shell/RootErrorBoundary.tsx`

**做法**：
- chunk 404 触发时，不立刻渲染卡片；先 `setTimeout(3000)` 后自动一次 `window.location.reload(true)`
- 如果 3s 内用户点了 button 就跳过自动重试
- 卡片上显示"3 秒后自动刷新…"倒计时

### 6.3 Phase 3 测试

- `BuildVersionWatcher.test.tsx`：mock fetch 返回 mismatch → toast 出现
- `RootErrorBoundary.test.tsx`：chunk 404 → 3s 后自动 reload（jest 加时钟控制）
- 后端：`test_frontend_version_endpoint.py`（mtime 变动触发 build_id 刷新）

### 6.4 Phase 3 commit

`feat(frontend+ops): build-id 指纹 + 漂移检测 + ErrorBoundary 自动重试`

## 7. Phase 4 — 对账可观测（半天，P1）

### 7.1 目标

让 auth-profiles ↔ octoagent.yaml 的漂移可见、可诊断；把 blocking_reasons 从字符串改成结构化对象。

### 7.2 任务

#### P4.1 — Config Drift 检查函数

**文件**：新增 `octoagent/apps/gateway/src/octoagent/gateway/services/config/drift_check.py`

**做法**：
- `detect_auth_config_drift(project_root) -> list[DriftRecord]`
- 规则：
  - `auth-profiles.json` 里的 OAuth profile 其 `provider` 不在 `octoagent.yaml.providers[]` → `drift_type="oauth_provider_not_in_config"`
  - `octoagent.yaml.providers[]` 里 `auth_type=oauth` 但 `auth-profiles.json` 没对应 profile → `drift_type="config_provider_no_credential"`
  - `model_aliases[*].provider` 不在 enabled providers 里 → `drift_type="alias_provider_disabled"`
- 返回结构化 `DriftRecord(severity, provider, explanation, recommended_action)`

#### P4.2 — 启动时 log + diagnostics 暴露

**文件**：
- 修改：`octoagent/apps/gateway/src/octoagent/gateway/main.py`（lifespan）
- 修改：`octoagent/apps/gateway/src/octoagent/gateway/routes/ops.py`（`/api/ops/auth/diagnostics` 返回 `config_drift` 字段）

**做法**：
- lifespan 启动后 `drift = detect_auth_config_drift(...)`；非空时 `log.warning("auth_config_drift_detected", count=len(drift))`
- diagnostics endpoint response 新增 `config_drift: [...]`，前端可渲染

#### P4.3 — blocking_reasons 结构化

**文件**：`octoagent/apps/gateway/src/octoagent/gateway/services/control_plane/setup_service.py`

**做法**：
- `SetupReviewSummary.blocking_reasons` 从 `list[str]` 改成 `list[BlockingReason]`，其中 `BlockingReason(risk_id, title, summary, recommended_action, field_path?)`
- `_handle_setup_apply` 的 `SETUP_REVIEW_BLOCKED` 错误 payload 带结构化 list
- **向后兼容**：`blocking_reasons` 旧字段保留（降级为 `[str(r.title) for r in ...]`），新增字段 `blocking_reasons_detail`

#### P4.4 — 前端 blocking modal 用结构化

**文件**：`octoagent/frontend/src/domains/settings/SettingsPage.tsx`（P1.2 的 modal 升级版）

**做法**：
- 收到 `blocking_reasons_detail` 时逐项渲染 `{title, summary, recommended_action}`，每项带"跳到相关字段"链接（如果 `field_path` 非空）

### 7.3 Phase 4 测试

- `test_drift_check.py`：3 种 drift 场景
- `test_auth_diagnostics_with_drift.py`：诊断 endpoint 返回 drift 字段，脱敏仍合规
- `test_setup_review_blocking_structured.py`：blocking_reasons_detail 结构 + 向后兼容

### 7.4 Phase 4 commit

`feat(auth+setup): config drift 检测 + blocking_reasons 结构化`

## 8. 关键设计决策

### 8.1 为什么用 per-route ErrorBoundary 而不是 suspense fallback？

`Suspense fallback` 只处理**加载中**状态，不处理**加载失败**。lazy chunk 404 是异步错误，必须 ErrorBoundary 捕获。

### 8.2 为什么 blocking_reasons 保留旧字段？

现有测试和外部调用方（比如 CLI / Telegram 消息）可能依赖旧的字符串列表。加新字段 `_detail` 而不是重名是最小入侵。

### 8.3 为什么不把 OAuth 凭证也写进 octoagent.yaml？

Constitution C5（Least Privilege）：凭证只能在 auth-profiles.json（0o600 文件），不能进入任何会被 log/event 的文件。继续分离，只是加**对账**。

### 8.4 为什么 build_id 用 mtime 而不是 file hash？

`index.html` 体量小，mtime 足够区分 build。hash 能解决 file system 时钟漂移但代价更大。先 mtime，出问题再升级。

### 8.5 为什么 `setup.oauth_and_apply` 不是 `setup.apply` 的 opt-in 参数？

分开能让 action audit log 更清晰（日志里能看到"原子操作"还是"仅保存"）；前端路径也更直白。

## 9. 测试策略总览

| Phase | 新增测试文件 | 关键 case 数 |
|-------|------------|------------|
| P1 | RouteErrorBoundary.test / SettingsPage.test 扩展 | ~8 |
| P2 | test_setup_oauth_and_apply.py / SettingsPage.test 扩展 | ~6 |
| P3 | BuildVersionWatcher.test / test_frontend_version_endpoint.py / RootErrorBoundary.test 扩展 | ~7 |
| P4 | test_drift_check / test_auth_diagnostics_with_drift / test_setup_review_blocking_structured | ~9 |

**预计新增 ~30 条测试**，覆盖率策略与 Feature 078 一致（重点测失败分支和边界）。

## 10. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| per-route ErrorBoundary 改 App.tsx 破坏现有路由 | 中 | 整站白屏 | App.test.tsx 加 "所有 route 可以正常渲染" 覆盖；chunk 404 显式 e2e 测试 |
| setup.oauth_and_apply 原子性破坏现有授权流程 | 中 | 用户无法授权 | 保留旧 `provider.oauth.openai_codex` action，`setup.oauth_and_apply` 仅作为 opt-in；老路径继续工作 |
| build_id 在 dev 模式下行为不同 | 低 | dev 启动报错 | Vite define 加 placeholder `__BUILD_ID__ = "dev"`；前端 BuildVersionWatcher 在 `build_id === "dev"` 时直接 no-op |
| drift check 误报 | 低 | 用户日志噪音 | 仅 `log.warning`，不 block 启动；diagnostics 里结构化 + recommended_action 让用户能判断 |
| blocking_reasons_detail 破坏现有测试 | 中 | 现有 assert 失败 | 保留旧 `blocking_reasons` 字段；只新增 detail 字段 |

## 11. Scope Lock

**不改**：
- `auth-profiles.json` schema
- `PkceOAuthAdapter` 或任何 Feature 078 新增的代码（独立 Feature）
- `litellm-config.yaml` 生成逻辑
- Feature 078 的 `/api/ops/auth/diagnostics` response 结构（只扩字段，不改既有）
- CLI 入口（setup 所有改动仅影响 Web UI）

**不做**：
- 不引入状态管理库（Zustand 等），继续用现有 React state + useWorkbench
- 不改 `ConfigSchema` 模型字段（只改 `SetupReviewSummary` 这个响应对象）
- 不做 telemetry/上报框架（build_id mismatch 只前端弹 toast）

## 12. 分阶段验收 checklist

### Phase 1（止血后）

- [ ] 浏览器缓存失效模拟：强制一个 lazy chunk 404 → 只局部卡片 + shell 保留
- [ ] fieldErrors 非空：保存按钮点击 → modal 显示所有错误
- [ ] 授权 Codex 后：sticky bar 立即出现

### Phase 2（闭环后）

- [ ] 删除现有 `openai-codex` 配置 + 再次授权 → 一次点击完成授权 + 入 config
- [ ] 授权 + draft 有 review blocking → modal 显示"授权已完成但还有未解决 blocking"

### Phase 3（版本防御后）

- [ ] `pnpm build` 后 `<meta>` 有 build-id
- [ ] mock 后端返回 mismatch build_id → toast "有新版本"
- [ ] mock lazy chunk 404 → 3s 倒计时后自动 reload

### Phase 4（对账后）

- [ ] 手工破坏 octoagent.yaml（删 provider 但保留 auth-profiles 条目）→ 启动 log warn + diagnostics 显示 drift
- [ ] 后端返回结构化 blocking_reasons_detail → 前端 modal 渲染多条
- [ ] `blocking_reasons`（旧）字段仍能被 test 读到（向后兼容）

## 13. 全量验收（所有 Phase 完成后）

重演事故场景：
1. 浏览器长开 WebUI
2. 后端 rebuild dist
3. 授权 OAuth + 改 main = gpt-5.4 + 点保存

**预期**：
- 没有 chunk 404 全页灾难（P3 的 toast 提前告警 → 用户主动刷新 → 不再有 mismatch）
- 即便 chunk 404 发生（P3 没触发）：只影响单个 route，shell 仍在，banner 可见（P1）
- OAuth 授权一键完成 + provider 入 config（P2）
- 保存失败时原因清晰可见（P1 + P4）
- `octoagent.yaml` 与 `auth-profiles.json` 不会再漂移（P4）

---

**总结**：4 Phase / ~2 天 / 15 个任务。P1+P2 = 救火（P0，半天-1天）；P3+P4 = 防御+健康度（P1，半天-1天）。
