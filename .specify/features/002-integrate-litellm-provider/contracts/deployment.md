# 部署配置契约

**特性**: 002-integrate-litellm-provider
**日期**: 2026-02-28
**追踪**: FR-002-DC-1, FR-002-DC-2, FR-002-SK-1, FR-002-SK-2

---

## 1. LiteLLM Proxy Docker Compose

**文件**: `octoagent/docker-compose.litellm.yml`

```yaml
services:
  litellm:
    image: ghcr.io/berriai/litellm:main-latest
    container_name: octoagent-litellm
    restart: unless-stopped
    ports:
      - "${LITELLM_PORT:-4000}:4000"
    volumes:
      - ./litellm-config.yaml:/app/config.yaml:ro
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    env_file:
      - .env.litellm
    environment:
      - LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY:-sk-octoagent-dev}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:4000/health/liveliness"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
```

---

## 2. LiteLLM Proxy 配置模板

**文件**: `octoagent/litellm-config.yaml`

```yaml
# OctoAgent LiteLLM Proxy 配置
# 文档: https://docs.litellm.ai/docs/proxy/configs

model_list:
  # cheap 运行时 group -- 轻量模型
  # 语义 alias 映射: router, extractor, summarizer -> cheap
  - model_name: "cheap"
    litellm_params:
      model: "gpt-4o-mini"
      api_key: "os.environ/OPENAI_API_KEY"

  # main 运行时 group -- 主力模型
  # 语义 alias 映射: planner, executor -> main
  - model_name: "main"
    litellm_params:
      model: "gpt-4o"
      api_key: "os.environ/OPENAI_API_KEY"

  # fallback 运行时 group -- 备选 provider
  # 语义 alias 映射: fallback
  - model_name: "fallback"
    litellm_params:
      model: "claude-3-5-haiku-20241022"
      api_key: "os.environ/ANTHROPIC_API_KEY"

litellm_settings:
  drop_params: true        # 忽略 provider 不支持的参数
  num_retries: 2           # 每个模型重试次数
  request_timeout: 60      # Proxy 侧请求超时（秒）

router_settings:
  routing_strategy: "simple-shuffle"  # MVP 不需要复杂路由
  fallbacks:
    - {"cheap": ["fallback"]}
    - {"main": ["fallback"]}

general_settings:
  master_key: "os.environ/LITELLM_MASTER_KEY"
```

---

## 3. 环境变量分层

### 3.1 .env（通用配置）

**文件**: `octoagent/.env`

```bash
# OctoAgent 通用配置
OCTOAGENT_LLM_MODE=litellm          # litellm / echo
OCTOAGENT_LLM_TIMEOUT_S=30          # LLM 调用超时（秒）

# LiteLLM Proxy 连接配置（OctoAgent 应用侧）
LITELLM_PROXY_URL=http://localhost:4000
LITELLM_PROXY_KEY=sk-octoagent-dev   # Proxy 访问密钥

# LiteLLM Proxy 管理密钥（Proxy 容器侧）
LITELLM_MASTER_KEY=sk-octoagent-dev
```

### 3.2 .env.litellm（LLM Provider API Keys）

**文件**: `octoagent/.env.litellm`

```bash
# LLM Provider API Keys -- 仅注入到 LiteLLM Proxy 容器
# 此文件必须在 .gitignore 中
OPENAI_API_KEY=sk-your-openai-key
ANTHROPIC_API_KEY=sk-ant-your-anthropic-key
```

### 3.3 .gitignore 更新

```
# Secrets
.env
.env.*
!.env.example
!.env.litellm.example
```

---

## 4. 环境变量完整清单

| 变量 | 归属 | 默认值 | 必须 | 说明 |
|------|------|--------|------|------|
| `OCTOAGENT_LLM_MODE` | 应用 | `litellm` | 否 | LLM 运行模式 |
| `OCTOAGENT_LLM_TIMEOUT_S` | 应用 | `30` | 否 | 调用超时（秒） |
| `LITELLM_PROXY_URL` | 应用 | `http://localhost:4000` | 否 | Proxy 地址 |
| `LITELLM_PROXY_KEY` | 应用 | `""` | 否 | Proxy 访问密钥 |
| `LITELLM_MASTER_KEY` | Proxy | `sk-octoagent-dev` | 是 | Proxy 管理密钥 |
| `LITELLM_PORT` | Proxy | `4000` | 否 | Proxy 端口映射 |
| `OPENAI_API_KEY` | Proxy | -- | 是* | OpenAI API Key |
| `ANTHROPIC_API_KEY` | Proxy | -- | 否 | Anthropic API Key（fallback） |

*至少需要 1 个 LLM provider API key。

---

## 5. Secrets 安全约束

### 5.1 分层隔离（对齐 Constitution C5）

```
应用层（OctoAgent 进程）:
  持有: LITELLM_PROXY_URL, LITELLM_PROXY_KEY, OCTOAGENT_LLM_MODE
  不持有: OPENAI_API_KEY, ANTHROPIC_API_KEY

Proxy 层（Docker 容器）:
  持有: OPENAI_API_KEY, ANTHROPIC_API_KEY, LITELLM_MASTER_KEY
  不暴露给: 应用层代码、Event payload、日志
```

### 5.2 禁止事项

- OPENAI_API_KEY / ANTHROPIC_API_KEY 不得出现在：
  - 应用层代码中
  - Event payload 中
  - structlog 日志中
  - 任何非 Proxy 容器的进程环境中
- LITELLM_PROXY_KEY 不得写入 Event payload
