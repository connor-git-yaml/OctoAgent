---
name: llm-config
description: "配置 OctoAgent 的 LLM Provider、模型别名和 Memory 模型绑定。适用于新增 SiliconFlow / OpenRouter / OpenAI 等 Provider、设置 main / cheap / memory aliases，以及说明 Web / CLI / Agent 的正确配置入口与生效方式。"
version: 2.0.0
author: OctoAgent
tags:
  - llm
  - provider
  - config
  - alias
  - memory
  - setup
---

# LLM Config

用于统一配置 OctoAgent 的模型接入路径。

## 核心原则

1. **所有用户可见配置都从 `octoagent.yaml` 出发**
2. **`litellm-config.yaml` 是自动生成的衍生文件，不手工编辑**
3. **普通用户优先走 Web Settings 或 `octo setup`**
4. **`octo config sync` / `config.sync` 只负责重新生成衍生配置，不负责自动重启 runtime**

如果用户问“LiteLLM 怎么配”，你的回答应该是：

- 用户主配置写在 `octoagent.yaml`
- LiteLLM 是内部实现层
- 人只需要关心 Provider、alias、Memory alias 和是否已经启用真实模型

## 什么时候使用

- 新增或修改 Provider（如 SiliconFlow、OpenRouter、OpenAI、Anthropic、本地 vLLM / Ollama）
- 配置 `main` / `cheap` 或自定义模型别名
- 配置 Memory 依赖模型：
  - `reasoning_model_alias`
  - `expand_model_alias`
  - `embedding_model_alias`
  - `rerank_model_alias`
- 解释 Web、CLI、Agent 三种路径下应该怎么配、怎么生效

## 不要这样做

- 不要让用户手工编辑 `litellm-config.yaml` 作为主配置路径
- 不要再使用 `memory.backend_mode`、`memu-command`、`memu-http` 之类历史字段
- 不要把 `config.sync` 描述成“热重载”或“自动启用真实模型”

## 配置模型

### 1. Provider 配置位置

`octoagent.yaml`:

```yaml
providers:
  - id: siliconflow
    name: SiliconFlow
    auth_type: api_key
    api_key_env: SILICONFLOW_API_KEY
    base_url: https://api.siliconflow.cn/v1
    enabled: true
```

字段说明：

- `id`: Provider 标识
- `name`: 显示名称
- `auth_type`: `api_key` 或 `oauth`
- `api_key_env`: 凭证环境变量名
- `base_url`: 自定义 API Base URL。SiliconFlow、DeepSeek、本地 vLLM / Ollama 常需要填写
- `enabled`: 是否启用

### 2. 模型别名配置位置

```yaml
model_aliases:
  main:
    provider: siliconflow
    model: Qwen/Qwen3.5-32B
    description: 主力模型
  cheap:
    provider: siliconflow
    model: Qwen/Qwen3.5-14B
    description: 低成本模型
```

建议：

- `main` 是主 Agent 默认模型
- `cheap` 用于降级和低成本路径
- 自定义 alias 可以继续扩展给特定能力使用

### 3. Memory 模型绑定位置

```yaml
memory:
  reasoning_model_alias: mem-reasoning
  expand_model_alias: mem-expand
  embedding_model_alias: mem-embedding
  rerank_model_alias: mem-rerank
```

这些字段**引用的是 `model_aliases` 的 key**，不是直接写模型名。

对应 alias 需要同时存在：

```yaml
model_aliases:
  mem-reasoning:
    provider: siliconflow
    model: Qwen/Qwen3.5-32B
    description: 记忆加工
  mem-expand:
    provider: siliconflow
    model: Qwen/Qwen3.5-14B
    description: 查询扩写
  mem-embedding:
    provider: siliconflow
    model: Qwen/Qwen3-Embedding-8B
    description: 语义检索
  mem-rerank:
    provider: siliconflow
    model: Qwen/Qwen3-Reranker-0.6B
    description: 结果重排
```

默认行为：

- `reasoning_model_alias` 留空 -> fallback 到 `main`
- `expand_model_alias` 留空 -> fallback 到 `main`
- `embedding_model_alias` 留空 -> 使用内建 embedding
- `rerank_model_alias` 留空 -> 使用 heuristic

## 三种使用路径

### Web 用户

推荐路径：

1. 打开 `Settings`
2. 在 `Model Providers 配置` 中添加 Provider
3. 需要自定义网关时填写 `API Base URL`
4. 配置 `main` / `cheap` 和 Memory 相关 alias
5. 点击“检查配置”
6. 点击“连接真实模型”

说明：

- “连接真实模型”走的是 `setup.quick_connect`
- 这条路径会同时保存配置、生成衍生配置、并处理 runtime activation

### CLI 用户

推荐路径：

```bash
octo setup
```

这是一键主路径，适合普通用户。

自定义 Provider 现在也可以直接走：

```bash
octo setup --provider custom --provider-id siliconflow --base-url https://api.siliconflow.cn/v1 --main-model Qwen/Qwen3-32B --cheap-model Qwen/Qwen3-14B
```

高级手动路径：

```bash
octo config provider add siliconflow --name "SiliconFlow" --api-key-env SILICONFLOW_API_KEY --base-url https://api.siliconflow.cn/v1
octo config alias set main --provider siliconflow --model Qwen/Qwen3.5-32B
octo config alias set cheap --provider siliconflow --model Qwen/Qwen3.5-14B
octo config sync
```

如果需要一次性把 Provider、alias、Memory alias 都走交互式路径录进去：

```bash
octo project edit --wizard
```

说明：

- `octo setup` 现在支持 `--provider custom` + `--base-url` 主路径；如果想做更细粒度的分步修改，再使用 Web Settings、`octo project edit --wizard` 或低层 `octo config *`
- `octo config provider add` / `alias set` 负责修改 `octoagent.yaml`
- `octo config sync` 只重新生成 `litellm-config.yaml`
- 如果要“一次保存并启用真实模型”，仍应优先使用 `octo setup`

### Agent

如果当前 Agent 需要帮助用户配置模型，遵循以下顺序：

1. 读取当前 `octoagent.yaml`
2. 优先使用高层 `setup.review` / `setup.quick_connect` 工具；需要细粒度编辑时再退回 `config.*`
3. 写入 Provider
4. 写入 `main` / `cheap` 等模型别名
5. 如用户要求 Memory 专用模型，再写入 `memory.*_model_alias`
6. 如果当前只是做了低层配置修改，再执行 `config.sync` 重新生成衍生配置
7. 明确告知用户：
   - 高层 `setup.quick_connect` 才负责保存并启用真实模型
   - `config.sync` 只同步衍生配置，不代表 runtime 已启用真实模型
   - 定义 alias 不等于已经被主 Agent 使用；主 Agent / Worker 绑定 alias 需要到 Agents 页面或对应 profile 配置里选择

Agent 输出时必须避免以下误导：

- 不要说“我已经热重载 LiteLLM”
- 不要说“只改 litellm-config.yaml 就算配置完成”
- 不要把 `base_url` 说成只能去内部文件里手工写

## 生效语义

### `octoagent.yaml`

- 用户主配置
- Provider、模型别名、Memory alias 都写这里

### `litellm-config.yaml`

- 自动生成
- 给 LiteLLM Proxy 使用
- 不作为用户主配置入口

### `octo config sync` / `config.sync`

- 作用：重新生成 `litellm-config.yaml`
- 不保证：自动重启 runtime、自动切换到真实模型、自动完成 live verify

### `setup.quick_connect` / `octo setup`

- 作用：高层一键入口
- 负责：保存配置、同步衍生配置、处理 runtime activation
- 面向：普通用户和“直接跑通”场景

## 示例：SiliconFlow + Memory

```yaml
providers:
  - id: siliconflow
    name: SiliconFlow
    auth_type: api_key
    api_key_env: SILICONFLOW_API_KEY
    base_url: https://api.siliconflow.cn/v1
    enabled: true

model_aliases:
  main:
    provider: siliconflow
    model: Qwen/Qwen3.5-32B
    description: 主力模型
  cheap:
    provider: siliconflow
    model: Qwen/Qwen3.5-14B
    description: 低成本模型
  mem-embedding:
    provider: siliconflow
    model: Qwen/Qwen3-Embedding-8B
    description: 语义检索
  mem-rerank:
    provider: siliconflow
    model: Qwen/Qwen3-Reranker-0.6B
    description: 结果重排

memory:
  embedding_model_alias: mem-embedding
  rerank_model_alias: mem-rerank
```

## 排错提示

- `setup.review` 提示缺少 `main`：先补 `model_aliases.main`
- `setup.review` 提示 Memory alias 不存在：检查 `memory.*_model_alias` 是否引用了真实存在的 alias key
- 自定义 Provider 调不通：先检查 `providers[].base_url`
- 运行时没切到真实模型：确认是否走了 `setup.quick_connect` / `octo setup`，不要只停留在 `config.sync`
