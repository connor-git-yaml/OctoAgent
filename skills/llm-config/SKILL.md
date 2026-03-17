---
name: llm-config
description: "Configure LLM providers and model aliases for OctoAgent. Use when: (1) adding a new provider (SiliconFlow, OpenRouter, Anthropic, etc.), (2) setting up model aliases (main, cheap, memory models), (3) binding memory models (embedding, rerank, query rewrite), (4) syncing litellm-config.yaml after config changes. NOT for: checking model usage/cost (use model-usage skill), runtime LLM calls."
version: 1.0.0
author: OctoAgent
tags:
  - llm
  - provider
  - config
  - model
  - alias
  - litellm
tools_required:
  - terminal.exec
  - filesystem.read_text
  - filesystem.list_dir
---

# LLM Config

配置 OctoAgent 的 LLM Provider 和模型别名。

## When to Use

- 添加新的 LLM Provider（SiliconFlow、OpenRouter、Anthropic 等）
- 配置模型别名（main、cheap、fallback 或自定义别名）
- 绑定 Memory 系统的专用模型（embedding、rerank、query rewrite）
- 同步 octoagent.yaml → litellm-config.yaml
- 诊断 Provider 配置问题

## When NOT to Use

- 查看模型用量和花费 -> 使用 `model-usage` skill
- 直接调用 LLM -> 由 OctoAgent 运行时自动处理
- 配置 MCP server -> 使用 `mcp-builder` skill

## 配置架构

```
octoagent.yaml          （声明式配置，安全纳入 Git）
  ├── providers[]        Provider 列表（id, auth_type, api_key_env）
  ├── model_aliases{}    模型别名映射（alias -> provider + model）
  ├── memory             Memory 模型绑定
  └── runtime            LiteLLM Proxy 连接信息
        ↓
    octo config sync
        ↓
litellm-config.yaml     （自动生成，LiteLLM Proxy 读取）
.env.litellm            （API Key 明文，不进 Git）
```

**核心原则**：`octoagent.yaml` 只存环境变量名（如 `SILICONFLOW_API_KEY`），不存 API Key 明文。明文凭证写入 `.env.litellm`。

## 配置操作

### 1. 添加 Provider

**CLI 方式**：

```bash
cd octoagent

# 添加 Provider（交互式）
uv run octo config provider add siliconflow

# 添加 Provider（非交互式）
uv run octo config provider add siliconflow \
  --name "SiliconFlow" \
  --auth-type api_key \
  --api-key-env SILICONFLOW_API_KEY
```

**直接编辑 octoagent.yaml**：

```yaml
providers:
  - id: siliconflow
    name: SiliconFlow
    auth_type: api_key
    api_key_env: SILICONFLOW_API_KEY
    enabled: true
```

**写入 API Key 到 .env.litellm**：

```bash
# 追加到 .env.litellm（不进 Git）
echo 'SILICONFLOW_API_KEY=sk-iosziklfffskjxciexbvykpxeaadmvjdirfasgducvorovhr' >> .env.litellm
```

### 2. 配置模型别名

**CLI 方式**：

```bash
# 设置 main 别名
uv run octo config alias set main --provider siliconflow --model Qwen/Qwen3.5-35B-A3B

# 设置 cheap 别名
uv run octo config alias set cheap --provider siliconflow --model Qwen/Qwen3.5-9B

# 带 thinking_level
uv run octo config alias set main --provider openai-codex --model gpt-5.4 --thinking-level xhigh
```

**直接编辑 octoagent.yaml**：

```yaml
model_aliases:
  main:
    provider: siliconflow
    model: Qwen/Qwen3.5-35B-A3B
    description: 主力模型
  cheap:
    provider: siliconflow
    model: Qwen/Qwen3.5-9B
    description: 轻量模型
```

### 3. 配置 Memory 专用模型

Memory 系统需要 4 种模型，通过 `octoagent.yaml` 的 `memory` 段配置：

```yaml
memory:
  backend_mode: local_only    # local_only | memu

  # 模型别名绑定（引用 model_aliases 中的 key，留空回退到 main）
  reasoning_model_alias: mem-reasoning    # 实时提取/批处理/ToM/关系抽取
  expand_model_alias: mem-expand          # 查询改写
  embedding_model_alias: mem-embedding    # 向量化
  rerank_model_alias: mem-rerank          # 精排
```

对应的 model_aliases 需要一并配置：

```yaml
model_aliases:
  # --- 主力模型 ---
  main:
    provider: openai-codex
    model: gpt-5.4
    description: 主力模型
    thinking_level: xhigh

  # --- Memory 专用模型 ---
  mem-reasoning:
    provider: siliconflow
    model: Qwen/Qwen3.5-35B-A3B
    description: Memory 加工/ToM/关系抽取
  mem-expand:
    provider: siliconflow
    model: Qwen/Qwen3.5-9B
    description: Memory 查询改写
  mem-embedding:
    provider: siliconflow
    model: Qwen/Qwen3-Embedding-8B
    description: Memory 向量化
  mem-rerank:
    provider: siliconflow
    model: Qwen/Qwen3-Reranker-0.6B
    description: Memory 精排
```

### 4. 同步配置

修改 `octoagent.yaml` 后，必须同步生成 `litellm-config.yaml`：

```bash
# 同步（生成 litellm-config.yaml）
uv run octo config sync

# 预览（不写文件）
uv run octo config sync --dry-run
```

### 5. 非标准 Base URL 的 Provider

SiliconFlow 等第三方 Provider 需要自定义 Base URL。有两种方式：

**方式 A：在 LiteLLM 模型名中指定**（推荐）

LiteLLM 支持 `openai/` 前缀 + `api_base` 参数。直接在 `litellm-config.yaml` 中通过 model_list 条目指定：

```yaml
# litellm-config.yaml（手动修改或通过扩展模板生成）
model_list:
  - model_name: mem-reasoning
    litellm_params:
      model: openai/Qwen/Qwen3.5-35B-A3B
      api_key: os.environ/SILICONFLOW_API_KEY
      api_base: https://api.siliconflow.cn/v1
```

**方式 B：通过环境变量**

```bash
# .env.litellm
SILICONFLOW_API_KEY=sk-...
SILICONFLOW_API_BASE=https://api.siliconflow.cn/v1
```

## 完整案例：SiliconFlow + Memory 模型

以下是完整配置 SiliconFlow Provider 并绑定 Memory 模型的步骤。

### Step 1: 编辑 octoagent.yaml

```yaml
config_version: 1
updated_at: '2026-03-17'

providers:
  - id: openai-codex
    name: OpenAI Codex (ChatGPT Pro OAuth)
    auth_type: oauth
    api_key_env: OPENAI_API_KEY
    enabled: true
  - id: siliconflow
    name: SiliconFlow
    auth_type: api_key
    api_key_env: SILICONFLOW_API_KEY
    enabled: true

model_aliases:
  # 主力模型
  main:
    provider: openai-codex
    model: gpt-5.4
    description: 主力模型（GPT-5.4，深度推理）
    thinking_level: xhigh
  cheap:
    provider: openai-codex
    model: gpt-5.4
    description: 低成本模型（GPT-5.4，轻量推理）
    thinking_level: low

  # Memory 专用模型（SiliconFlow）
  mem-reasoning:
    provider: siliconflow
    model: Qwen/Qwen3.5-35B-A3B
    description: Memory 实时提取/批处理/ToM/关系抽取
  mem-expand:
    provider: siliconflow
    model: Qwen/Qwen3.5-9B
    description: Memory 查询改写
  mem-embedding:
    provider: siliconflow
    model: Qwen/Qwen3-Embedding-8B
    description: Memory 向量化
  mem-rerank:
    provider: siliconflow
    model: Qwen/Qwen3-Reranker-0.6B
    description: Memory 精排

memory:
  backend_mode: local_only
  reasoning_model_alias: mem-reasoning
  expand_model_alias: mem-expand
  embedding_model_alias: mem-embedding
  rerank_model_alias: mem-rerank

runtime:
  llm_mode: litellm
  litellm_proxy_url: http://localhost:4000
  master_key_env: LITELLM_MASTER_KEY
```

### Step 2: 写入 API Key

```bash
echo 'SILICONFLOW_API_KEY=sk-iosziklfffskjxciexbvykpxeaadmvjdirfasgducvorovhr' >> octoagent/.env.litellm
```

### Step 3: 同步并重启

```bash
cd octoagent
uv run octo config sync
docker compose -f docker-compose.litellm.yml restart litellm
```

### Step 4: 验证

```bash
# 检查 litellm-config.yaml 是否包含新别名
grep -A 3 'mem-' litellm-config.yaml

# 健康检查
curl -s http://localhost:4000/health/liveliness

# 测试模型调用
curl -s http://localhost:4000/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "mem-expand", "messages": [{"role": "user", "content": "hello"}]}' | jq .model
```

## Provider 模板速查

### SiliconFlow

```yaml
- id: siliconflow
  name: SiliconFlow
  auth_type: api_key
  api_key_env: SILICONFLOW_API_KEY
  enabled: true
```
Base URL: `https://api.siliconflow.cn/v1`

### OpenRouter

```yaml
- id: openrouter
  name: OpenRouter
  auth_type: api_key
  api_key_env: OPENROUTER_API_KEY
  enabled: true
```
模型名需要 `openrouter/` 前缀（自动补全）。

### Anthropic

```yaml
- id: anthropic
  name: Anthropic
  auth_type: api_key
  api_key_env: ANTHROPIC_API_KEY
  enabled: true
```

### DeepSeek

```yaml
- id: deepseek
  name: DeepSeek
  auth_type: api_key
  api_key_env: DEEPSEEK_API_KEY
  enabled: true
```
Base URL: `https://api.deepseek.com/v1`

## 别名系统说明

### 语义别名（AliasRegistry 内置）

| 别名 | 运行时组 | 用途 |
|------|----------|------|
| `router` | cheap | 意图分类、风险分级 |
| `extractor` | cheap | 结构化提取 |
| `summarizer` | cheap | 摘要生成 |
| `planner` | main | 多约束规划 |
| `executor` | main | 高风险操作确认 |
| `fallback` | fallback | 降级 Provider |

### 运行时组

- **cheap**: 小模型，低成本
- **main**: 大模型，高质量
- **fallback**: 备用 Provider

### thinking_level 预算

| 级别 | budget_tokens | 适用场景 |
|------|--------------|----------|
| `xhigh` | 32000 | 深度推理、复杂规划 |
| `high` | 16000 | 中等推理 |
| `medium` | 8000 | 一般推理 |
| `low` | 1024 | 简单分类/提取 |

## 诊断命令

```bash
cd octoagent

# 检查配置健康
uv run octo doctor

# 检查同步状态
uv run octo config sync --dry-run

# 查看当前配置
cat octoagent.yaml

# 查看生成的 LiteLLM 配置
cat litellm-config.yaml

# 查看已配置的 API Key（脱敏）
grep -v '^#' .env.litellm | sed 's/=.*/=***/'
```
