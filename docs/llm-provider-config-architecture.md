# LLM Provider 配置到调用：当前架构、市场协议与 OpenClaw / Agent Zero 对标

_更新日期：2026-03-20_

## 1. 执行摘要

这篇文档回答四个问题：

1. OctoAgent 现在如何从 Provider 配置一路走到真实 LLM 调用。
2. Web、CLI、Agent 三条入口各自应该怎么走，哪些动作只是“改配置”，哪些动作才算“真正生效”。
3. 市面上常见 LLM Provider 的协议到底统一到了什么程度。
4. OpenClaw 和 Agent Zero 在这条链路上分别做得怎样，哪些点值得继续吸收。

当前主结论：

- `octoagent.yaml` 已经是 OctoAgent 的 LLM 主配置事实源。
- `litellm-config.yaml` 是衍生物，不应该再被当成人工主编辑入口。
- OctoAgent 当前的“推荐生效路径”已经收敛到 canonical setup 流：`setup.review` / `setup.quick_connect` / `octo setup`。
- 低层 `octo config *` 和 Agent `config.*` 工具仍然保留，但它们的语义是“编辑主配置”和“重建衍生配置”，不是“自动启用真实模型”。
- 运行时 alias 现在以 `octoagent.yaml.model_aliases` 为主事实源，`main` / `cheap` 不再是唯一允许的运行时 alias。
- 市场上确实存在强烈的 “OpenAI-compatible” 收敛趋势，但它只统一了核心推理接口，没有统一完整控制面、能力矩阵和计费面。
- OpenClaw 在“统一 onboarding / config protocol + 控制台 + secret lifecycle”上更成熟；Agent Zero 在“Settings / Projects / Memory Dashboard 作为产品对象”上更产品化。

## 2. 当前 OctoAgent 的主设计

### 2.1 单一事实源

当前主线已经明确：

- 用户主配置：`octoagent.yaml`
- 衍生运行配置：`litellm-config.yaml`
- 运行时环境变量：`.env` / `.env.litellm`

这套约束在以下位置已经一致：

- 蓝图：`docs/blueprint.md`
- README：`README.md`
- Skill：`skills/llm-config/SKILL.md`
- DX schema / bootstrap / sync 代码：
  - `octoagent/packages/provider/src/octoagent/provider/dx/config_schema.py`
  - `octoagent/packages/provider/src/octoagent/provider/dx/config_bootstrap.py`
  - `octoagent/packages/provider/src/octoagent/provider/dx/litellm_generator.py`
  - `octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py`

这意味着：

- 用户和 Agent 应该理解的是 Provider、模型别名、Memory 绑定、是否启用真实模型。
- LiteLLM 的 `model_list`、内部 `api_base` 拼装、Headers 注入等应被视为内部实现层。

### 2.2 核心对象模型

当前配置主模型可概括为三层：

1. `providers[]`
2. `model_aliases{}`
3. `memory.*_model_alias`

`providers[]` 负责声明“有哪些上游网关/厂商可用”，典型字段包括：

- `id`
- `name`
- `auth_type`
- `api_key_env`
- `base_url`
- `enabled`

`model_aliases{}` 负责把一个稳定的业务 alias 绑定到具体模型：

- `main`
- `cheap`
- 任意自定义 alias，例如 `summarizer`、`compaction`、`mem-reasoning`

`memory.*_model_alias` 则不直接写模型名，而是引用 alias：

- `memory.reasoning_model_alias`
- `memory.expand_model_alias`
- `memory.embedding_model_alias`
- `memory.rerank_model_alias`

这套设计把“真实模型名”与“业务消费方”解耦了。

### 2.3 校验边界

当前 schema 和 setup review 已经补上了两层关键校验：

1. `model_aliases.<alias>.provider` 必须引用一个真实存在的 provider。
2. `memory.*_model_alias` 必须引用一个真实存在的 alias。

对应代码：

- `octoagent/packages/provider/src/octoagent/provider/dx/config_schema.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`

这意味着 Memory 的 alias 错配已经从“运行时才发现”前移到了“保存前检查”。

## 3. 从配置到调用的完整链路

### 3.1 编辑阶段

当前有三类入口：

- Web：`Settings`
- CLI：`octo setup`、`octo project edit --wizard`、`octo config *`
- Agent：高层 `setup.review` / `setup.quick_connect`，低层 `config.*`

推荐程度从高到低：

1. `setup.quick_connect` / `octo setup`
2. Web Settings
3. CLI wizard
4. 低层 `octo config *`

原因很简单：高层入口不仅改主配置，还会处理 activation；低层入口只负责配置本身。

### 3.2 审查阶段

当前 canonical 审查动作是：

- `setup.review`

它会检查至少以下问题：

- 是否有启用的 Provider
- `main` / `cheap` 是否存在
- alias 所引用的 Provider 是否可用
- Memory 所需 alias 是否完整
- runtime / proxy / env 状态是否具备基本连通条件

这一步本质上是“保存前的配置治理层”。

### 3.3 保存与激活阶段

当前真正面向“让真实模型跑起来”的动作是：

- Web：连接真实模型
- CLI：`octo setup`
- Agent：`setup.quick_connect`

它们最终都会走 canonical quick connect 流，完成：

1. 保存 `octoagent.yaml`
2. 重新生成 `litellm-config.yaml`
3. 做 runtime activation
4. 在托管 runtime 下尝试 restart / scheduled restart
5. 返回 review / activation 结果

关键代码：

- `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
- `octoagent/packages/provider/src/octoagent/provider/dx/cli.py`

### 3.4 衍生配置同步阶段

低层动作：

- `octo config sync`
- Agent `config.sync`

它们现在的正确语义只有一个：

- 从 `octoagent.yaml` 重新生成 `litellm-config.yaml`

它们不保证：

- 自动重启 runtime
- 自动切到真实模型
- 自动 live verify

这点在 CLI、Agent tool 文案和 skill 中都已经对齐。

### 3.5 运行时调用阶段

当前运行时 alias 解析由 `AliasRegistry` 负责：

- 显式配置 alias 优先
- legacy 语义 alias 仅做兼容 fallback
- 未知 alias 才回退到默认 alias（通常是 `main`）

关键代码：

- `octoagent/packages/provider/src/octoagent/provider/alias.py`
- `octoagent/apps/gateway/src/octoagent/gateway/main.py`

这次重构后的关键变化是：

- `octoagent.yaml.model_aliases` 已经成为运行时 alias 的主事实源。
- `summarizer`、`compaction` 之类自定义 alias 不再被静默折回 `main` / `cheap`。

## 4. 三条用户路径如何正确使用

### 4.1 Web

当前 Web 主路径是：

1. 在 `Settings` 添加 Provider
2. 如需自定义网关，填写 `API Base URL`
3. 配置 `main` / `cheap`
4. 如有需要，配置 Memory 的四个 alias
5. 点击“检查配置”
6. 点击“连接真实模型”
7. 去 `Agents` 页面把具体 Agent 绑定到某个 alias

这里要特别注意一件事：

- 配 alias，不等于所有 Agent 都自动改用它。

`Settings` 负责定义 alias 本身；
`Agents` 负责决定哪个 Agent 实际使用哪个 alias。

相关实现：

- `octoagent/frontend/src/domains/settings/SettingsPage.tsx`
- `octoagent/frontend/src/domains/settings/SettingsProviderSection.tsx`
- `octoagent/frontend/src/domains/agents/agentManagementData.ts`
- `octoagent/frontend/src/domains/agents/AgentEditorSection.tsx`

### 4.2 CLI

当前 CLI 分两层：

高层一键入口：

```bash
octo setup
```

对自定义 Provider，当前也已经支持：

```bash
octo setup \
  --provider custom \
  --provider-id siliconflow \
  --provider-name SiliconFlow \
  --api-key-env SILICONFLOW_API_KEY \
  --base-url https://api.siliconflow.cn/v1 \
  --main-model Qwen/Qwen3-32B \
  --cheap-model Qwen/Qwen3-14B
```

低层编辑入口：

```bash
octo config provider add siliconflow --name SiliconFlow --api-key-env SILICONFLOW_API_KEY --base-url https://api.siliconflow.cn/v1
octo config alias set main --provider siliconflow --model Qwen/Qwen3-32B
octo config alias set cheap --provider siliconflow --model Qwen/Qwen3-14B
octo config sync
octo restart
octo doctor --live
```

建议：

- 普通用户优先用 `octo setup`
- 只有在需要精细化修改时，再使用 `octo config *`

### 4.3 Agent

Agent 路径也分高低两层。

推荐 Agent 工具流：

1. 读取当前配置
2. 调 `setup.review`
3. 调 `setup.quick_connect`
4. 如用户要细粒度 patch，再退回 `config.*`

不推荐的旧路径：

- 只调用 `config.add_provider`
- 只调用 `config.set_model_alias`
- 最后停在 `config.sync`

因为这条路只会完成“改配置”和“重建衍生文件”，不保证真实 activation。

## 5. 当前 UI 里几个容易误解的语义

### 5.1 “设为默认”

Web 里“设为默认”的真实语义，不是“所有调用都会走这个 Provider”。

它做的事情是：

- 把当前 Provider 移到列表第一位
- 让它成为 Settings 页内部的默认 Provider
- 后续“恢复 main / cheap”这类默认动作优先基于它生成模板

它不会自动做的事情：

- 不会改写现有 `model_aliases`
- 不会让已有 Agent / Worker 自动改用它
- 不会让所有运行时请求直接切过去

真正决定调用走谁的，仍然是：

- `model_aliases.<alias>.provider`
- 以及 Agent / Worker 当前绑定的是哪个 alias

### 5.2 “恢复 main / cheap”

这颗按钮的真实语义不是“修复一下缺失项”，而是：

- 用当前默认 Provider 重新生成一套推荐 alias 模板
- 覆盖当前 alias 草稿

它一般会把 alias 列表重置为：

- `main`
- `cheap`

对自定义 alias 的影响：

- `summarizer`
- `compaction`
- `embedding`
- `rerank`

这些自定义 alias 可能会在草稿里被清掉。

所以它更接近“恢复默认 alias 模板”，不是“温和修复 main / cheap”。

## 6. Memory 与 alias 的关系

当前 Memory 不直接配置模型名，而是绑定 alias。

这是一个正确方向，因为它允许：

- reasoning / expand 跟随主模型策略演进
- embedding / rerank 绑定到专用模型
- 在不改 Memory 业务逻辑的前提下替换底层 Provider

当前默认 fallback 语义：

- `reasoning_model_alias` 留空：回退到 `main`
- `expand_model_alias` 留空：回退到 `main`
- `embedding_model_alias` 留空：使用内建 embedding
- `rerank_model_alias` 留空：使用 heuristic

这套语义已经体现在：

- schema
- Settings 页面
- memory retrieval profile
- README / skill

## 7. 当前测试覆盖应该如何理解

这条链路当前已有三类测试：

### 7.1 配置与 schema 层

- `octoagent/packages/provider/tests/dx/test_config_schema.py`
- `octoagent/packages/provider/tests/dx/test_config_wizard.py`
- `octoagent/packages/provider/tests/dx/test_wizard_session.py`
- `octoagent/packages/provider/tests/dx/test_project_commands.py`

重点验证：

- YAML 往返
- provider / alias / memory alias 约束
- wizard 顺序与字段持久化

### 7.2 bootstrap / runtime / doctor 层

- `octoagent/packages/provider/tests/test_config_bootstrap.py`
- `octoagent/packages/provider/tests/test_alias.py`
- `octoagent/packages/provider/tests/test_doctor.py`
- `octoagent/packages/provider/tests/test_setup_command.py`

重点验证：

- `octo setup` / bootstrap
- alias runtime 解析
- `doctor` 与 sync 状态
- custom provider + `base_url`

### 7.3 集成层

- `octoagent/packages/provider/tests/integration/test_live_llm.py`

这组测试已经覆盖：

- 构造 `octoagent.yaml`
- 生成 `litellm-config.yaml`
- 检查 alias → provider 的路由映射
- 在有真实 API Key 时进行有限 live validation

它还不是“托管实例真人全链路 E2E”的完全替代，但已经是源码侧最接近主链的集成验证。

## 8. 市场上的 Provider 协议，实际上统一了吗

截至 2026-03-20，答案是：

- 核心推理接口层，越来越统一。
- 完整控制面、能力面、计费面，没有统一。

### 8.1 已经形成的事实标准

今天很多 Provider 都声称自己“支持 OpenAI-compatible API”。

这通常意味着至少一部分接口相近：

- `base_url`
- `Bearer API key`
- `chat/completions`
- 流式 SSE
- 一部分 `embeddings`

这是一种很有价值的收敛，因为它降低了接入门槛，也让 LiteLLM 这类网关有了统一抽象空间。

### 8.2 仍然没有统一的部分

但这并不意味着“协议完全统一”。当前最典型的差异包括：

- `Responses API` 是否支持
- reasoning / thinking 参数名和语义
- tool calling 的边角行为
- vision / audio / image / rerank / embedding 是否兼容
- `/models` 目录是否存在、返回字段是否稳定
- 价格、余额、订阅制 / 充值制这些控制面接口
- 模型命名方式
- Azure 这类 deployment name 语义

### 8.3 常见 Provider 的现状

| Provider | 核心调用协议 | 模型目录 | 价格 / 余额信息 | 备注 |
| --- | --- | --- | --- | --- |
| OpenAI | 原生基准协议 | 有官方 API / models 能力 | 有公开定价文档，但账户计费与组织控制面是另一层 | 最接近“标准源头” |
| Azure OpenAI / Foundry | 大体兼容 OpenAI SDK，但不完全相同 | 目录与部署管理走 Azure 控制面 | 价格、配额、部署走 Azure | `model` 常常实际填写 deployment name |
| Anthropic | 原生 `Messages API`，不是 OpenAI 原生协议 | 官方有 models overview | 价格体系独立 | 需要 native adapter 思维 |
| Gemini | 有 OpenAI compatibility layer，也有 native API | 目录和能力更多通过 Gemini 自己的文档 / API 暴露 | 价格与配额体系独立 | Google 官方明确建议新系统优先考虑 native API |
| OpenRouter | 强调统一聚合，接口非常接近 OpenAI Chat API | 有 Models API | 有 credits / catalog / pricing 体系 | 适合作为“模型聚合层”而不是单一厂商 |
| Groq | “mostly compatible” | 有 `/openai/v1/models` | 能力和限制写得较明确 | 明确列出不支持项，比很多兼容层更诚实 |
| Together | 强调 OpenAI compatibility | 有 `/models`，且返回较多元数据 | 定价元数据可在模型目录里看到 | 是比较适合做 catalog 接入的 Provider |
| Fireworks | 兼容 OpenAI，但行为细节存在差异 | 有自身平台接口 | 控制面独立 | 兼容不等于完全同语义 |
| DeepSeek | 明确支持 OpenAI-compatible | 模型与价格更多通过官方文档呈现 | 价格文档独立 | thinking / 模型命名是自己的一套 |
| SiliconFlow | 官方说明大模型部分可用 OpenAI 库调用，支持大多数参数 | 模型详情主要在平台模型广场 | 价格 / 限速信息走平台文档 | 典型“兼容核心推理，但控制面不统一”的 Provider |

### 8.4 对 OctoAgent 的架构含义

这对 OctoAgent 有三个直接含义：

1. 不能把 “OpenAI-compatible” 误当成 “完整统一协议”。
2. `ProviderConfig` 与 `ProviderCatalog` 应该是两层对象，不要混在一起。
3. “模型元数据平台”不应试图从 `chat/completions` 协议里反推。

换句话说：

- 当前 `octoagent.yaml` 适合承载“我准备用哪个 Provider + 哪个模型字符串”。
- 但它并不适合承载“这个 Provider 支持全部哪些模型、余额多少、是否订阅制、是否支持多模态”。

后者需要单独的 catalog 层。

## 9. OpenClaw 怎么做

OpenClaw 在这条链路上的最大特点，不是“某个字段设计得更复杂”，而是它更早把“配置”提升成了一套跨界面的协议对象。

### 9.1 它把 onboarding / config 先做成协议

OpenClaw 官方把 onboarding + config 定义成共享协议，而不是某个页面的内部状态：

- `wizard.start`
- `wizard.next`
- `wizard.cancel`
- `wizard.status`
- `config.schema`

而且响应对象已经稳定到：

- `sessionId`
- `schema`
- `uiHints`
- `generatedAt`

这意味着：

- CLI、Web、macOS app 共享同一套 onboarding 逻辑
- 前端不是自己发明一套 form 状态机

参考：

- https://docs.openclaw.ai/wizard
- `docs/blueprint.md` 中既有的 OpenClaw onboarding/config protocol 研究结论

### 9.2 它把控制面集中到一个 Control UI

OpenClaw 的 Control UI 不是单纯的聊天页，而是把以下对象放进同一个 operator surface：

- chat
- sessions
- channels
- cron jobs
- config
- dashboard

这对用户的好处是：

- “聊天、配置、生效、诊断” 是一条连续路径
- 用户不需要猜“下一步去哪个页面”

参考：

- https://docs.openclaw.ai/web/control-ui
- https://docs.openclaw.ai/web/dashboard
- https://docs.openclaw.ai/quickstart

### 9.3 它的 secret lifecycle 更完整

OpenClaw 官方文档已经把 secret 管理做成一套清晰生命周期：

- `openclaw secrets audit`
- `openclaw secrets configure`
- `openclaw secrets apply`
- `openclaw secrets reload`

并且 runtime model 讲得很清楚：

- secret 先解析到内存快照
- activation 时一次性 resolve
- reload 走原子 swap
- 失败保留 last-known-good

这比“把明文塞进 config draft，再顺手写入 env”要强很多。

参考：

- https://docs.openclaw.ai/cli
- https://docs.openclaw.ai/gateway/secrets

### 9.4 对 OctoAgent 最值得继续吸收的点

OpenClaw 最值得继续吸收的是三件事：

1. 把 onboarding / config / wizard 彻底协议化。
2. 把 control plane 动作做成跨表面复用，而不是 Web 独享。
3. 把 secret lifecycle 和 config lifecycle 分开。

OctoAgent 在第一件事上已经明显靠近了；
在第二件事上已经建立了 `control_plane + capability_pack` 的骨架；
第三件事还需要后续 feature 继续补完。

## 10. Agent Zero 怎么做

Agent Zero 在 LLM 配置这件事上没有 OpenClaw 那么“协议化”，但它在产品对象层面的组织方式很值得参考。

### 10.1 Settings 是正式产品入口，不是运维附属页

从代码和文档都能看出，Agent Zero 把 Settings 做成了正式的数据模型和 UI 配置中心。

它的设置对象直接覆盖多类模型与行为：

- `chat_model_*`
- `util_model_*`
- `embed_model_*`
- `browser_model_*`
- memory / workdir / shell / auth / MCP / A2A 等

参考：

- `python/helpers/settings.py`
- https://github.com/frdel/agent-zero

这意味着它的思路更像：

- 先有一个统一设置中心
- 再从设置中心向聊天、Project、Memory、Scheduler 分发

### 10.2 Projects 是统一隔离单位

Agent Zero 的 Project 不是“一个文件夹别名”，而是统一隔离单位，内含：

- instructions
- memory
- secrets
- files
- subagent config
- knowledge
- git workspace

参考：

- https://www.agent-zero.ai/p/docs/projects/
- https://raw.githubusercontent.com/frdel/agent-zero/main/docs/guides/projects.md

这点非常重要，因为它把“模型配置”和“项目上下文”关联到了同一个 operator object 上。

### 10.3 Memory Dashboard 是用户可见产品对象

Agent Zero 没有把 memory 只留在内部 helper 层，而是直接提供了 Memory Dashboard：

- 搜索
- 过滤
- 编辑
- 删除
- 多 memory subdir

参考：

- https://www.agent-zero.ai/p/docs/memory/
- `python/api/memory_dashboard.py`

这说明它在产品上更强调：

- 让用户看得见 memory
- 让用户能主动校正 memory

### 10.4 它对 OctoAgent 的真正启发

Agent Zero 最值得 OctoAgent 吸收的，不是它某个具体 LLM provider 字段，而是这套产品组织方式：

1. Settings 是产品对象，不是脚手架。
2. Projects 是配置、上下文、记忆、密钥、工作区的统一边界。
3. Memory / backup / scheduler 都应该是 UI 一等公民。

这也是为什么 OctoAgent 后续不应把 LLM Provider 配置只当成一个 `provider` 子模块问题，而应把它视作：

- Settings
- Projects
- Agents
- Memory
- Runtime activation

共同参与的一条主链。

## 11. OpenClaw 与 Agent Zero 的差异，对 OctoAgent 的意义

| 维度 | OpenClaw | Agent Zero | OctoAgent 当前更适合吸收什么 |
| --- | --- | --- | --- |
| 配置协议 | 很强，wizard + schema + uiHints | 相对弱，更多是 settings center + UI | 继续走 OpenClaw 风格的 control-plane contract |
| 控制台组织 | 很强，Control UI 集中 operator path | 很强，Dashboard / Settings / Projects 更产品化 | 两者结合 |
| Secret lifecycle | 很成熟，audit / configure / apply / reload | 有 secrets，但以 Settings / Projects 入口为主 | 吸收 OpenClaw 生命周期设计 |
| Project 组织 | 有 workspace / sessions / channels / control 面 | 很强，project 是完整隔离单位 | 吸收 Agent Zero 的 project object 思维 |
| Memory 用户化 | 有，但更偏平台组织 | 很强，直接 Memory Dashboard | 吸收 Agent Zero 的用户可见性 |
| LLM 入口 | 强调 onboarding / configure / dashboard 连续路径 | 强调 settings / projects / provider surface | OctoAgent 应保留 canonical setup 流，同时补 catalog 与 project integration |

一句话总结：

- OpenClaw 更像“协议先行的 operator platform”
- Agent Zero 更像“产品对象先行的 personal AI workspace”
- OctoAgent 最好的方向不是二选一，而是“OpenClaw 的控制面协议化 + Agent Zero 的对象级产品化”

## 12. 对 OctoAgent 的后续建议

### 12.1 保持不变的正确方向

以下方向当前是对的，不应该回退：

- `octoagent.yaml` 作为主事实源
- `litellm-config.yaml` 作为衍生物
- `setup.review` / `setup.quick_connect` 作为 canonical 生效流
- `model_aliases` 作为运行时 alias 主事实源
- Memory 通过 alias 引用模型，而不是直接绑模型名

### 12.2 下一层应补的是 catalog，不是再堆一套 config

如果下一步要提升用户体验，最值得补的不是“再多一个配置入口”，而是单独引入：

- `ProviderCatalog`
- `ModelCatalog`

建议至少包含：

- Provider 级：
  - `supports_openai_compat`
  - `supports_models_listing`
  - `supports_pricing_lookup`
  - `supports_balance_lookup`
  - `plan_kind`
- Model 级：
  - `modalities`
  - `reasoning_support`
  - `input_price`
  - `output_price`
  - `context_window`
  - `tool_calling`
  - `json_mode`
  - `embedding`
  - `rerank`

这层能力应该服务于：

- Web model picker
- CLI 自动补全 / 建议
- Agent 配置技能
- doctor / review 提示

而不应继续塞回 `octoagent.yaml` 本体。

### 12.3 让“使用哪个 alias”更显式

当前 alias 定义已经清楚，但使用方绑定仍然分散在：

- Settings
- Agents
- Worker profile
- Memory

后续可以继续做一层“alias 消费图”，明确展示：

- 哪些 Agent 绑定了哪个 alias
- Memory 的四个槽位各绑定了什么
- 哪些 alias 已定义但无人消费
- 哪些消费方引用了失效 alias

这会显著减少“我明明配了模型，为什么没用上”的困惑。

### 12.4 把真人 E2E 继续制度化

源码侧集成测试已经不错，但这条链路最终仍然强依赖托管实例的运行态。

建议把以下三套真人 E2E 固定成 release checklist：

1. Web：Provider → alias → quick connect → Agent 绑定 → 真调用
2. CLI：`octo setup` → `octo doctor --live`
3. Agent：让 Agent 自己完成 provider 配置与 alias 绑定

## 13. 参考资料

### 13.1 OctoAgent 内部资料

- `docs/blueprint.md`
- `README.md`
- `skills/llm-config/SKILL.md`
- `octoagent/packages/provider/src/octoagent/provider/dx/config_schema.py`
- `octoagent/packages/provider/src/octoagent/provider/dx/config_bootstrap.py`
- `octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py`
- `octoagent/packages/provider/src/octoagent/provider/alias.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
- `octoagent/frontend/src/domains/settings/SettingsPage.tsx`
- `octoagent/frontend/src/domains/settings/SettingsProviderSection.tsx`
- `octoagent/frontend/src/domains/agents/agentManagementData.ts`
- `octoagent/frontend/src/domains/agents/AgentEditorSection.tsx`

### 13.2 外部资料

#### OpenClaw

- https://docs.openclaw.ai/quickstart
- https://docs.openclaw.ai/wizard
- https://docs.openclaw.ai/web/control-ui
- https://docs.openclaw.ai/web/dashboard
- https://docs.openclaw.ai/cli
- https://docs.openclaw.ai/gateway/secrets

#### Agent Zero

- https://github.com/frdel/agent-zero
- https://www.agent-zero.ai/p/docs/projects/
- https://www.agent-zero.ai/p/docs/memory/
- https://www.agent-zero.ai/p/docs/secrets/
- https://www.agent-zero.ai/p/docs/task-scheduler/
- https://www.agent-zero.ai/p/docs/changelog/0.9.6/
- https://www.agent-zero.ai/p/docs/changelog/0.9.7/
- https://www.agent-zero.ai/p/docs/changelog/0.9.8/

#### Provider 官方资料

- OpenAI API: https://developers.openai.com/api/reference/overview
- Azure OpenAI / Foundry: https://learn.microsoft.com/en-us/azure/foundry-classic/openai/how-to/switching-endpoints
- Anthropic Messages: https://docs.anthropic.com/en/api/messages-examples
- Anthropic Models Overview: https://docs.anthropic.com/en/docs/models-overview
- Gemini OpenAI compatibility: https://ai.google.dev/gemini-api/docs/openai
- OpenRouter API: https://openrouter.ai/docs/api/reference/overview
- OpenRouter Models: https://openrouter.ai/docs/docs/overview/models
- Groq OpenAI compatibility: https://console.groq.com/docs/openai
- Groq Models: https://console.groq.com/docs/models
- Together OpenAI compatibility: https://docs.together.ai/docs/openai-api-compatibility
- Together Models: https://docs.together.ai/reference/models
- Fireworks OpenAI compatibility: https://docs.fireworks.ai/tools-sdks/openai-compatibility
- DeepSeek API: https://api-docs.deepseek.com/
- DeepSeek Pricing: https://api-docs.deepseek.com/quick_start/pricing/
- SiliconFlow Quickstart: https://docs.siliconflow.cn/cn/userguide/quickstart
- SiliconFlow Rate Limits: https://docs.siliconflow.cn/en/userguide/rate-limits/rate-limit-and-upgradation
