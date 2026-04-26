# Provider 直连架构（Feature 080 + 081）

> 作者：Connor
> 引入版本：Feature 080（Phase 1-5a，2026-04-26）+ Feature 081（5 Phase，2026-04-26）
> 状态：✅ 完成（LiteLLM Proxy 完全退役）

## 1. 历史演进

| 阶段 | 主路径 | 状态 |
|------|-------|------|
| Feature 014 | LiteLLM Proxy（子进程 + Docker Compose） | 已退役 |
| Feature 080 | Provider 直连抽象（与 LiteLLM Proxy 并存） | 已合并 |
| Feature 081 | LiteLLM Proxy 完全退役 | ✅ |

## 2. 当前架构

```
┌─ Channel ────────┐
│ Telegram / Web   │
└────────┬─────────┘
         │
┌────────▼─────────┐
│  OctoGateway     │  FastAPI + SSE
└────────┬─────────┘
         │
┌────────▼─────────┐
│  OctoKernel      │  Task/Event/Artifact 中枢
└────────┬─────────┘
         │
┌────────▼─────────┐
│  Workers         │  Free Loop 自治智能体
└────────┬─────────┘
         │
┌────────▼─────────┐
│  SkillRunner     │  Skill Pipeline + 工具调用循环
└────────┬─────────┘
         │
┌────────▼─────────┐
│  ProviderRouter  │  alias → ProviderClient 解析（task scope 缓存）
└────────┬─────────┘
         │
┌────────▼─────────┐
│  ProviderClient  │  按 transport 派发：
│                  │  - openai_chat       → POST /v1/chat/completions
│                  │  - openai_responses  → POST /v1/responses
│                  │  - anthropic_messages → POST /v1/messages
└────────┬─────────┘
         │
┌────────▼─────────┐
│  Provider HTTP   │  OpenAI / Anthropic / SiliconFlow / OpenRouter / ...
└──────────────────┘
```

## 3. 调用栈深度

LLM 调用栈深度从历史 4 层缩短到 2 层：

| 历史（LiteLLM Proxy） | 当前（Provider 直连） |
|--------|--------|
| Skill → LiteLLMSkillClient → ChatCompletionsProvider → LiteLLM Proxy → Provider | Skill → ProviderClient → Provider |

## 4. 核心组件

### 4.1 ProviderRouter（packages/provider/.../provider_router.py）

- alias → ProviderClient 解析（按 task_scope 缓存，避免 mid-task provider 切换）
- 凭证管理（API key / OAuth）
- HTTP client 共享（同一 provider/transport 跨 task 复用）

### 4.2 ProviderClient（packages/provider/.../provider_client.py）

- 按 ``ProviderTransport`` 枚举派发到 3 种 transport 实现
- 401/403 反应式 OAuth 刷新（Feature 078 OAuth Token Refresh Robustness）
- 统一 ``LLMCallError`` 错误分类

### 4.3 ProviderRouterMessageAdapter（packages/provider/.../router_message_adapter.py）

- 把 ProviderRouter 包装成 ``LLMProviderProtocol``（``complete(messages, alias)`` 接口）
- 替代 LiteLLMClient 作为 ``FallbackManager.primary``
- 关键作用：让 ``LLMService.call()`` 在没有 SkillRunner 路径时（如 ``context_compaction``）也能直连 provider

### 4.4 ProviderModelClient（packages/skills/.../provider_model_client.py）

- 替代 LiteLLMSkillClient，对接 SkillRunner
- per-(task_id, trace_id) history 缓存 + idle eviction
- 工具调用循环编排

### 4.5 AuthResolver（packages/provider/.../auth_resolver.py）

- ``StaticApiKeyResolver``：从环境变量解析 API key
- ``OAuthResolver``：从 ``auth-profiles.json`` 读取 OAuth credential，带 401/403 反应式刷新

## 5. 配置 schema（v2）

### 5.1 ProviderEntry

```yaml
providers:
  - id: openai-codex
    name: OpenAI Codex
    enabled: true
    transport: openai_responses          # openai_chat / openai_responses / anthropic_messages
    api_base: https://chatgpt.com/backend-api/codex
    auth:
      kind: oauth
      profile: openai-codex-default
  - id: openrouter
    name: OpenRouter
    enabled: true
    transport: openai_chat
    api_base: https://openrouter.ai/api/v1
    auth:
      kind: api_key
      env: OPENROUTER_API_KEY
```

### 5.2 RuntimeConfig

```yaml
runtime:
  # Feature 081 后保留为 deprecated 字段（运行时被忽略，仅供 legacy yaml 兼容）：
  # llm_mode / litellm_proxy_url / master_key_env
  # 用户跑 `octo config migrate-080` 可一键升级到 v2 schema
```

## 6. 历史 yaml 兼容性

老 v1 yaml（含 ``runtime.llm_mode`` / ``litellm_proxy_url`` / ``master_key_env``）启动时：

1. ``load_config`` 在 raw YAML 层调用 ``detect_legacy_yaml_keys``，命中 legacy keys 时
   log warning 引导用户跑 ``octo config migrate-080``
2. Pydantic 解析仍然成功（deprecated 字段保留默认值）
3. 运行时不再消费这些字段（ProviderRouter 直连）

`octo config migrate-080`：

- yaml 迁移：v1 → v2（推断 transport，转 ``auth_type+api_key_env`` → ``auth.kind+env``）
- 凭证迁移：``.env.litellm`` → ``.env``（合并已存在键不覆盖）
- 自动备份原文件 → ``*.bak.080-{kind}-{timestamp}``
- ``--dry-run`` 仅打印 diff，不写文件
- 幂等：v2 yaml + 缺 ``.env.litellm`` → 重复跑直接 skip

## 7. 已删除文件清单（Feature 081 P4）

| 文件 | 替代方案 |
|------|---------|
| `octoagent/skills/litellm_client.py` | `octoagent/skills/provider_model_client.py` |
| `octoagent/skills/providers.py` | `octoagent/provider/provider_client.py`（按 transport 派发） |
| `octoagent/skills/compactor.py` | `gateway/services/context_compaction.py`（主线 + 三级 fallback）|
| `octoagent/provider/client.py`（``LiteLLMClient``）| `octoagent/provider/provider_client.py`（``ProviderClient``）|
| `gateway/services/proxy_process_manager.py` | 不再需要（Provider 直连无子进程）|
| `gateway/services/config/litellm_generator.py` | 不再需要（无衍生配置） |
| `gateway/services/config/litellm_runtime.py` | `ProviderEntry.transport` 直接表达 |
| `octoagent/docker-compose.litellm.yml` | 不再需要（无 Docker 容器）|
| `octoagent/provider/dx/docker_daemon.py` | 不再需要（无 Docker daemon 依赖）|

## 8. 性能改进

- Gateway 启动时间：~10s → **~5s**（无 LiteLLM Proxy 子进程等待 + 无 docker-compose 启动）
- LLM 调用栈：4 层 → **2 层**
- 配置 source-of-truth：3 份（octoagent.yaml + auth-profiles.json + litellm-config.yaml）→ **2 份**（前两份）
- 用户首次 setup 步骤：5 步 → **3 步**

## 9. 相关 Feature 文档

- `.specify/features/078-oauth-token-refresh-robustness/`
- `.specify/features/079-setup-ux-recovery/`
- `.specify/features/080-provider-direct-routing/`
- `.specify/features/081-litellm-full-retirement/`
