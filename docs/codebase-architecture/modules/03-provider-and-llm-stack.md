# Provider / LLM Stack 模块

本模块对应当前代码里的 [`octoagent/packages/provider`](../../../octoagent/packages/provider)。  
它解决的不是单纯“换个模型厂商”，而是把下面几件事收成一条链：

- `octoagent.yaml` 配置建模
- alias 解析
- LiteLLM Proxy 调用
- CLI / setup / doctor / runtime activation

如果只看当前实现，可以把它理解为：**OctoAgent 的模型配置平面 + 运行时模型接入平面。**

## 1. 模块职责

当前 `packages/provider` 主要包含：

1. **配置事实源**
   - `octoagent.yaml` schema
   - provider / alias / runtime / memory / channels 配置

2. **运行时 alias 与模型调用**
   - `AliasRegistry`
   - `LiteLLMClient`
   - fallback / cost / reasoning 相关支持

3. **开发者体验（DX）**
   - `octo setup`
   - `octo config *`
   - wizard
   - doctor
   - runtime activation / sync

## 2. 关键文件与角色

| 文件 | 当前角色 |
| --- | --- |
| [`dx/config_schema.py`](../../../octoagent/packages/provider/src/octoagent/provider/dx/config_schema.py) | `octoagent.yaml` 的 Pydantic schema |
| [`alias.py`](../../../octoagent/packages/provider/src/octoagent/provider/alias.py) | 运行时 alias 注册与兼容映射 |
| [`client.py`](../../../octoagent/packages/provider/src/octoagent/provider/client.py) | LiteLLM Proxy 调用封装 |
| [`dx/config_wizard.py`](../../../octoagent/packages/provider/src/octoagent/provider/dx/config_wizard.py) | 配置加载与保存 |
| [`dx/config_commands.py`](../../../octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py) | CLI config 命令与运行态同步 |
| `dx/config_bootstrap.py` | `octo setup` / quick bootstrap 路径 |

## 3. `config_schema.py`: 主配置事实源

位置：[`config_schema.py`](../../../octoagent/packages/provider/src/octoagent/provider/dx/config_schema.py)

这是当前 Provider 栈最重要的文件，因为它决定了：  
**什么配置是系统正式承认的、应该写进 `octoagent.yaml` 的。**

### 3.1 `ProviderEntry`

职责：

- 描述一个 provider 条目
- 包含 `id`、`name`、`auth_type`、`api_key_env`、`base_url`、`enabled`

设计要点：

- 不存 API Key 明文
- `base_url` 支持自定义网关或兼容层
- 只要 schema 承认，Web / CLI / runtime 才可能真正统一

### 3.2 `ModelAlias`

职责：

- 把用户可见 alias 绑定到 `provider + model`
- 可选携带 `thinking_level`

实现要点：

- `model_validator` 会调用 `normalize_provider_model_string()`
- 对需要显式 provider 前缀的路由型 provider 做规范化，例如 `openrouter/<model>`

### 3.3 `RuntimeConfig`

职责：

- 定义 `llm_mode`
- 定义 LiteLLM Proxy URL
- 定义 master key env

它决定了当前实例是在 `echo` 还是真实 `litellm` 模式下运行。

### 3.4 `MemoryConfig`

职责：

- 定义记忆系统依赖的四类 alias：
  - `reasoning_model_alias`
  - `expand_model_alias`
  - `embedding_model_alias`
  - `rerank_model_alias`

这说明 memory 已经是配置体系里的一级对象，而不是外围 patch。

### 3.5 `OctoAgentConfig`

位置：`config_schema.py` 中的总配置模型。

它把 provider、alias、runtime、memory、front door、channels 等块收进同一个主配置对象中，并在 validator 里承担当前配置层的强校验。

### 3.6 `build_config_schema_document()`

职责：

- 把 Pydantic schema 转成控制面/UI 可消费的配置 schema 文档
- 输出 UI hints、分组、顺序、字段说明

这解释了为什么 Web Settings 不直接硬编码 schema，而是消费控制面提供的配置文档。

## 4. `AliasRegistry`: 运行时 alias 注册表

位置：[`alias.py`](../../../octoagent/packages/provider/src/octoagent/provider/alias.py)

`AliasRegistry` 解决的问题是：  
配置里定义的 alias，到运行时应该怎么解析？

### 4.1 当前设计原则

当前实现强调三点：

1. `octoagent.yaml.model_aliases` 是主事实源
2. legacy 语义 alias 只做兼容 fallback
3. 显式配置 alias 优先级高于隐式兼容映射

### 4.2 `resolve()`

实现逻辑：

1. 如果 alias 已在运行时 alias 集合中，直接透传
2. 否则检查是不是 legacy alias，例如 `planner`、`summarizer`
3. 如果 legacy alias 对应的 runtime_group 在当前可用 alias 中，则映射过去
4. 都不满足时回退默认 alias，并记录告警

这意味着当前 alias 层已经从“固定 main/cheap/fallback”转向“配置优先，兼容保底”。

### 4.3 `from_runtime_aliases()`

职责：

- 从当前配置中的 alias key 集构造 registry

这一步通常在 gateway 启动时发生，把配置驱动 alias 接到运行时。

## 5. `LiteLLMClient`: 统一 Proxy 调用封装

位置：[`client.py`](../../../octoagent/packages/provider/src/octoagent/provider/client.py)

`LiteLLMClient` 当前是 runtime 调用真实模型的关键桥梁。

### 5.1 它不只是调用 SDK

当前它还负责：

- 认证错误识别
- 连接错误识别
- 轻量脱敏
- Responses API 直连
- reasoning 参数适配
- 流式响应聚合
- token / cost 解析

### 5.2 `_is_auth_error()`

职责：

- 统一判断 401/403 及各种 SDK auth 异常
- 为 auth refresh 和错误分类提供基础

### 5.3 `_resolve_reasoning_for_alias()`

职责：

- 根据 alias 和 reasoning 支持矩阵，决定是否真的向下传 reasoning 配置

### 5.4 `_collect_stream_response()`

职责：

- 消费 LiteLLM 流式 chunk
- 用 `stream_chunk_builder` 聚合成完整 completion 对象

### 5.5 `_complete_via_responses_api()`

职责：

- 当某些 alias 需要直连 Responses API 时，构造请求体并调用后端

这说明当前 Provider 层已经不再只对应传统 chat completions。

### 5.6 `_build_result()`

职责：

- 把底层 SDK / proxy 响应统一收口成 `ModelCallResult`
- 同时解析 token usage、cost、provider、model name

这让上层 `TaskService`、`OrchestratorService` 只消费统一结果对象。

## 6. `config_wizard.py`: 配置的磁盘读写入口

位置：[`config_wizard.py`](../../../octoagent/packages/provider/src/octoagent/provider/dx/config_wizard.py)

### 6.1 `load_config()`

职责：

- 从 project root 读取 `octoagent.yaml`
- 解析成 `OctoAgentConfig`

### 6.2 `save_config()`

职责：

- 把 `OctoAgentConfig` 序列化写回磁盘

这两个函数的重要性在于：  
CLI、setup、gateway 启动、control plane review/apply，本质上都在围绕同一个主配置文件工作。

## 7. `config_commands.py`: CLI 和运行态衔接层

位置：[`config_commands.py`](../../../octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py)

这个文件当前承担的是“配置命令 + 运行态同步”的桥梁。

最值得关注的不是每个命令名，而是两类核心逻辑。

### 7.1 配置编辑命令

例如：

- provider add/list/disable
- alias set/list
- memory show

这些命令最终都应该以 `OctoAgentConfig` 为中心做增量修改，而不是直接手改衍生配置。

### 7.2 `_auto_sync()` / `_activate_runtime_for_config()`

职责：

- 在配置变更后决定是否同步衍生文件
- 在必要时触发 runtime activation / verify 路径

这两个函数解释了“配置变更”和“运行时真正生效”之间的桥接点在哪里。

## 8. 当前模块的架构要点

### 8.1 `octoagent.yaml` 已经是单一事实源

这一点当前应该作为事实来理解：

- Web、CLI、Agent、gateway runtime 都围绕 `octoagent.yaml`
- `litellm-config.yaml` 是衍生物

### 8.2 Provider 模块不仅是 SDK 包装

它还承担：

- schema
- setup 流程
- doctor
- alias 运行时桥接
- 配置到运行时的 activation

### 8.3 当前最重要的边界

如果你要继续演进这一层，最关键的边界是：

1. `config_schema.py` 定义“什么是正式配置”
2. `AliasRegistry` 定义“配置 alias 如何进入运行时”
3. `LiteLLMClient` 定义“运行时如何调用真实模型”

只要这三层不混，Provider 栈的后续演进就会稳定很多。
