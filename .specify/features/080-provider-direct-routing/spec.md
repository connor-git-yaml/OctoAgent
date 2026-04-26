# Feature 080 — Provider 直连：去 LiteLLM Proxy 化

> 作者：Connor
> 日期：2026-04-26
> 模式：spec-driver-feature（架构性变更，需完整 spec）
> 上游：Feature 078（OAuth refresh）+ 079（Setup UX）的事故复盘 + Hermes/Pydantic AI/OpenClaw 的横向调研
> 下游：plan.md / tasks.md

## 1. 背景

### 1.1 触发事件

Feature 079 推送后用户实测：保存 `main = gpt-5.5` 后，主 Agent 跑任务报 27 次连续 `Proxy returned 429: No deployments available, cooldown_list=[hash]`。

排查链路：
- Gateway 端 OAuth refresh 工作正常，`auth-profiles.json` access_token 新鲜（5/6 才过期）
- 但 LiteLLM Proxy 是 **4-18 启动的独立进程**，serve 的 `litellm-config-resolved.yaml` 是 4-18 的 frozen 快照：
  - `model: gpt-5.4`（旧）
  - `api_key: <inline JWT，12 天前已过期>`（不是 `os.environ/...` 引用）
- Proxy 拿过期 token 调 chatgpt → 401 → LiteLLM 把 deployment 拉黑（cooldown）→ 后续所有 main 调用 **返回 429**（不是 401，**绕过 Feature 078 reactive refresh 的触发条件**）

### 1.2 横向调研（4 个参考项目）

| 项目 | 中间层代理 | OAuth 支持 | 备注 |
|------|----------|-----------|------|
| OpenClaw | ❌ 无 | 原生 ChatGPT Pro Codex | Provider 走 plugin SDK，每个 provider 独立 HTTP |
| Hermes Agent | ❌ 无 | Anthropic / Codex / Qwen / Gemini CLI | `transport` 字段决定协议，凭证池轮换 |
| Pydantic AI | ❌ 无 | 仅 Vertex（httpx.Auth hook） | 35 个原生 Provider，自主实现 |
| Agent Zero | LiteLLM **库**（非 Proxy） | ❌ 无 | YAML 声明式 provider，不起独立进程 |
| **OctoAgent** | ✅ **LiteLLM Proxy（独立进程）** | Feature 078 统一 manager | 双路径（Chat 走 Proxy / Responses 直连） |

**4 个参考项目里 0 个用 LiteLLM Proxy**。OctoAgent 是唯一的异类。

### 1.3 LiteLLM Proxy 在 OctoAgent 的实际收益 vs 代价

**当初引入的设想价值**（企业级 Proxy 通用价值）：
- 多 provider 路由统一接口
- 成本追踪 / observability
- Fallback / retry 集中管理
- 多用户 / 多租户隔离（master_key）

**OctoAgent 实际场景下的价值衰减**（个人 AI OS / 单用户）：
- 多 provider 路由：Provider/Adapter 抽象已有，不依赖 Proxy
- 成本追踪：EventStore 已记录 model_call usage 事件
- Fallback：Feature 078 已实现 Phase 2 CLI adopt + Phase 3 reused recovery
- 多租户：单用户场景**完全不适用**

**实际付出的代价**：
- 独立进程启动后从 frozen yaml 读 token，**永远不感知 OAuth refresh**
- Cooldown 黑盒：401 → 拉黑 → 后续 429 拒绝，绕过 Feature 078 reactive 触发
- 配置漂移：`octoagent.yaml` / `litellm-config.yaml` / `litellm-config-resolved.yaml` 三份两两不一致
- 对 Codex 直连场景**完全无价值**（Responses API direct 已绕过 Proxy）
- Feature 078 的 1500 行救火代码大部分是为了对抗 Proxy 引入的复杂度

### 1.4 决策

**LiteLLM Proxy 是技术债，应该退役**。新方案目标：
- 所有 LLM 调用直连 provider HTTP
- OAuth refresh 链路端到端有效（无 Proxy 黑洞）
- 配置 source-of-truth 单一（`octoagent.yaml` + `auth-profiles.json`）
- 架构清晰简洁、易于扩展新 provider

## 2. 用户故事

- **US-1**（P0）：作为用户，**改 main 模型并保存后，下一个任务立即用上新 model**——不需要重启任何额外进程
- **US-2**（P0）：作为用户，**OAuth token 过期时系统自动刷新并继续工作**——不需要手动重启
- **US-3**（P0）：作为用户，**报错信息清晰反映真实原因**（"OpenAI 401 invalid_token"）而不是 LiteLLM 内部抽象（"No deployments available, cooldown_list=[hash]"）
- **US-4**（P1）：作为用户，**新增一个 provider（比如 OpenRouter / DeepSeek）只需要在 octoagent.yaml 加一段配置**，不需要改代码
- **US-5**（P1）：作为用户，**同时使用多个 OAuth provider**（ChatGPT Pro + Anthropic Claude）应该都正常工作，且互不干扰
- **US-6**（P1）：作为开发者，**调试 LLM 调用问题时栈深度最多 2 层**（Skill → Provider），不再有 LiteLLM Proxy 中间层
- **US-7**（P2）：作为开发者，**Gateway 启动时间显著缩短**（少一个 LiteLLM Proxy 子进程的启动等待）

## 3. 功能需求（FR）

### FR-1：Provider 直连
- 所有 LLM 调用直接打 provider HTTP，不经过 LiteLLM Proxy 或任何中间代理
- 移除 `LiteLLM Proxy` 进程依赖、`ProxyManager`、`litellm-config*.yaml` 生成路径

### FR-2：统一 transport 抽象
- 引入 `ProviderTransport` 枚举：`openai_chat` / `openai_responses` / `anthropic_messages`
- 后续可扩展（bedrock_converse / google_gemini / vertex 等），但本 Feature 只覆盖前 3 个

### FR-3：AuthResolver 抽象
- 统一 OAuth 和 API Key 凭证解析为 `AuthResolver` 接口
- 每次 LLM 调用前 resolve；401 时 force_refresh 一次重试
- OAuthResolver 内嵌 `PkceOAuthAdapter`（复用 Feature 078 的所有逻辑）
- StaticApiKeyResolver 从 env var 读取

### FR-4：声明式 provider 配置
- `octoagent.yaml.providers[]` 每条声明 `transport` / `api_base` / `auth` / `extra_headers` / `extra_body`
- 移除 `runtime.llm_mode` / `runtime.litellm_proxy_url` / `runtime.master_key_env` 字段

### FR-5：动态 alias 路由
- alias → provider 的解析在**每次 LLM 调用时做**（不是 Gateway 启动时 frozen）
- 改 octoagent.yaml 后下一个 task 立即生效（无需重启）

### FR-6：Migration
- 启动时检测旧 schema（含 `runtime.llm_mode`），自动迁移：
  - 备份 `octoagent.yaml` → `octoagent.yaml.bak.080`
  - 备份 `litellm-config*.yaml` → `*.bak.080`
  - 按 `provider.api_base` 推断 `transport` 字段
  - 写新 schema 到 `octoagent.yaml`
  - log.warning 告知用户迁移完成 + 备份位置

### FR-7：错误透传
- Provider 返回的错误（401 / 429 / 503 等）不再被 LiteLLM 改写
- LLMCallError 携带原始 status_code + provider 原始错误 body（脱敏后）

### FR-8：Token Usage / Cost 追踪
- 流式响应解析时提取 token usage（与现有 ChatCompletionsProvider 一致）
- Cost 计算移到 EventStore 侧（按 provider + model_name 查 cost table）—— 不再依赖 LiteLLM cost calculator

### FR-9：401 reactive refresh
- ProviderClient 内 401 → `auth_resolver.force_refresh()` → retry 1 次
- 单 call 内最多 1 次 refresh，避免风暴
- 复用 Feature 078 的 PkceOAuthAdapter.refresh() 全部逻辑

## 4. 不变量（系统级 invariants）

- **I-1**：`auth-profiles.json` schema 不变（Feature 078 / 003-b 已稳定）
- **I-2**：`PkceOAuthAdapter` / OAuth flow 不变
- **I-3**：所有 EventType 枚举（OAUTH_*, MODEL_CALL_*）保持不变
- **I-4**：Skill / Tool 系统的 SkillManifest / ToolBroker 接口不变
- **I-5**：CLI 入口的命令行接口（`octo config provider list` 等）保持不变
- **I-6**：Constitution C5（Least Privilege）—— 凭证只在 auth-profiles.json，不进 octoagent.yaml

## 5. Scope Lock（不做的事）

- ❌ 不实现 bedrock / vertex / google gemini transport（留给后续 Feature）
- ❌ 不重构 SkillRunner / Tool Broker
- ❌ 不动 EventStore schema
- ❌ 不引入凭证池轮换（Hermes 风格 —— 等真有需要再加）
- ❌ 不改前端 Settings 页面布局（仅改字段，不改 UX）—— 但 schema 字段变化前端 UI 跟随
- ❌ 不在本 Feature 加 cost calculator（仅做 token usage 透传，cost 留给后续 Feature）

## 6. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Migration 把用户配置迁错 | 中 | 用户启动后跑不通 | 必须备份原文件；迁移失败时 fallback 到旧 schema 兼容模式（保留 LiteLLM Proxy 作为 fallback） |
| 失去 LiteLLM 的某些隐性 feature（cost / auto-retry policies） | 中 | 部分功能行为变化 | Token usage / cost / retry 都是已经在 ChatCompletionsProvider 实现过的，明确 audit 一遍 |
| 多 OAuth provider 并发 refresh 竞态 | 低 | refresh 风暴 | 复用 Feature 078 的 TokenRefreshCoordinator（per-provider lock） |
| Anthropic Messages API 协议差异（消息格式 / system prompt 位置） | 中 | Claude 调用失败 | 单独 transport 实现 + 完整测试覆盖 |
| 现有 Skill 假设走 LiteLLM Proxy（hard-coded master_key 之类） | 低 | Skill 运行失败 | grep 全代码库找出所有 master_key / proxy_url 引用 |
| 前端 Settings UX 老路径残留 | 中 | 用户看到无效字段 | Phase 4 同步清理前端 |

## 7. 验收准则

### 端到端（必须通过）

- [ ] Gateway 启动后 ≤ 5 秒（无 LiteLLM Proxy 子进程等待）
- [ ] 改 octoagent.yaml 中 main 模型 → 下一个 task 立即用新 model（无需重启）
- [ ] OAuth token 过期 → 自动刷新 → 继续工作（无 cooldown 黑盒）
- [ ] `~/.octoagent/auth-profiles.json` 与 `~/.octoagent/octoagent.yaml` 是仅有的两份配置（不再有 litellm-config*.yaml）
- [ ] 同时配置 ChatGPT Pro + Anthropic Claude 两个 OAuth → 都能用，互不干扰
- [ ] 报错信息直接反映 provider 原始错误（"OpenAI 401" 而非 "cooldown_list=[hash]"）

### 兼容性（必须通过）

- [ ] 现有用户的 `octoagent.yaml`（含 `runtime.llm_mode`）能自动迁移到新 schema
- [ ] 迁移后保留 `*.bak.080` 备份
- [ ] CLI 命令 `octo config provider list` / `octo config alias set` 等照常工作
- [ ] 所有 Feature 078 / 079 现有测试（约 110 条）继续通过

### 架构（必须通过）

- [ ] LiteLLM Proxy 进程不再启动
- [ ] `ProxyManager` 类被删除
- [ ] `litellm-config*.yaml` 生成代码被删除
- [ ] `octoagent.yaml` 的 LiteLLM 相关字段（`llm_mode` / `litellm_proxy_url` / `master_key_env`）从 schema 删除
- [ ] LLM 调用栈深度 ≤ 2 层（SkillRunner → ProviderClient）
