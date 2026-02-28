# LiteLLM Proxy 集成 + 成本治理 -- 功能需求规范

**特性**: 002-integrate-litellm-provider
**版本**: v1.0
**状态**: Draft
**日期**: 2026-02-28
**调研基础**: [research-synthesis.md](research/research-synthesis.md)
**蓝图依据**: docs/blueprint.md SS7.4, SS8.9, SS9.10, SS14

---

## 1. 概述

### 1.1 功能名称

LiteLLM Proxy 集成 + 成本治理 -- packages/provider 包 + LLMService 改造 + 成本可见性

### 1.2 目标

替换 M0 的 Echo 模式，接入真实 LLM，建立统一模型出口和成本可见性。Feature 002 的核心价值是让 OctoAgent 具备真实的 LLM 调用能力，同时确保每次模型调用的成本和资源消耗可追踪、可归因。这是 M1 "最小智能闭环"的基础设施层，后续 Feature 003（工具治理）、Feature 004（Skill Runner）、Feature 005（Policy Engine）均依赖本特性提供的 LLM 调用和成本数据能力。

### 1.3 范围概述

Feature 002 聚焦于 **packages/provider** 包的创建和 **apps/gateway** 中 LLM 服务的改造。具体包括：

- 新建 `packages/provider` 包，包含 4 个核心组件（LiteLLMClient、AliasRegistry、CostTracker、FallbackManager）
- 改造现有 `LLMService`，将默认 provider 从 EchoProvider 切换到 LiteLLM
- 扩展 `ModelCallCompletedPayload` 事件数据，增加成本和 provider 信息
- 扩展 `/ready` 健康检查端点，支持 LiteLLM Proxy 可达性检测
- 提供 LiteLLM Proxy 的 Docker 部署配置（Nice-to-have）

本特性**不涉及**：工具调用、结构化输出、流式响应、预算策略执行、成本告警、前端成本面板、智能路由。

### 1.4 受众声明

本规范面向 OctoAgent 的所有利益相关者。核心章节（User Stories、FR、Success Criteria）使用业务语言编写。技术栈约束和架构背景见 SS8（约束），供技术团队参考。

---

## 2. 参与者（Actors）

| 参与者 | 描述 | Feature 002 交互方式 |
|--------|------|---------------------|
| **Owner（用户）** | OctoAgent 的唯一用户 | 通过发送消息触发真实 LLM 调用；在 Web UI 事件时间线中观察成本和 provider 信息；通过 `/ready` 检查 Proxy 健康状态 |
| **系统（OctoAgent 后端）** | FastAPI 服务进程 | 通过 LiteLLMClient 调用 Proxy；通过 CostTracker 解析成本；通过 FallbackManager 实现降级；将成本数据写入 Event |
| **LiteLLM Proxy** | 模型网关容器 | 接收模型调用请求，路由到正确的 provider；管理 API keys；提供 fallback 和健康检查 |
| **EchoProvider** | M0 遗留的模拟 LLM 服务 | 作为 LiteLLM Proxy 不可达时的降级后备 |

---

## 3. User Stories

### P1 -- MVP 核心（必须交付）

#### US-1: 真实 LLM 调用（Priority: P1）

**作为** Owner，**我希望** 发送一条消息后，系统能调用真实的 LLM 生成有意义的响应（而非 Echo 回声），**以便** OctoAgent 成为一个真正有智能的助手。

**优先级理由**: 这是 Feature 002 存在的根本意义，也是 M1 所有后续 Feature 的前提。没有真实 LLM 调用，系统只是一个空壳。

**独立测试**: 配置 LiteLLM Proxy 后，发送一条消息，验证返回的不是 Echo 内容而是 LLM 生成的响应，且 MODEL_CALL_COMPLETED 事件包含真实数据。

**验收场景**:

```
Given LiteLLM Proxy 已启动且至少有 1 个 provider（如 OpenAI）可用
  And OctoAgent 配置为 LiteLLM 模式（非 Echo）
When Owner 通过 POST /api/message 发送消息 "请用一句话介绍 Python"
Then 系统创建任务并调用 LiteLLM Proxy
  And MODEL_CALL_COMPLETED 事件的响应内容是 LLM 生成的相关回答（非输入回声）
  And MODEL_CALL_COMPLETED 事件包含真实的 token_usage 数据
  And 任务正常推进到 SUCCEEDED 终态
```

```
Given OctoAgent 配置为 LiteLLM 模式
When Owner 发送消息，且 LLM 调用成功返回
Then 系统记录的事件链完整：TASK_CREATED -> USER_MESSAGE -> MODEL_CALL_STARTED -> MODEL_CALL_COMPLETED -> STATE_TRANSITION(SUCCEEDED)
  And 事件链中的 trace_id 一致
```

---

#### US-2: 成本可见性（Priority: P1）

**作为** Owner，**我希望** 每次 LLM 调用后，系统记录该调用的成本（USD）、消耗的 token 数量以及使用的模型和 provider，**以便** 我能按任务追溯成本，做到"花了多少钱，心里有数"。

**优先级理由**: 成本可见性是成本治理的第一层防线（Layer 1: Visibility），也是 Constitution C8（可观测性是产品功能）的直接体现。后续 Feature 005 的预算策略执行依赖本 Story 提供的成本数据。

**独立测试**: 完成一次 LLM 调用后，查询 MODEL_CALL_COMPLETED 事件的 payload，验证包含 cost_usd、provider、model_name 等字段。

**验收场景**:

```
Given 系统完成了一次 LLM 调用
When Owner 通过 GET /api/tasks/{task_id} 查询任务详情
Then 该任务的 MODEL_CALL_COMPLETED 事件 payload 包含以下字段：
  - cost_usd: 一个非负浮点数（对 Echo 模式为 0.0）
  - token_usage: 包含 prompt_tokens 和 completion_tokens 的整数值
  - model_name: 实际调用的模型名称（如 "gpt-4o-mini"）
  - provider: 实际使用的 provider（如 "openai"）
  - model_alias: 请求时使用的 alias（如 "cheap"）
```

```
Given 同一任务执行了多次 LLM 调用
When Owner 查询该任务的事件列表
Then 每个 MODEL_CALL_COMPLETED 事件都独立包含各自的成本数据
  And 所有事件的 cost_usd 之和即为该任务的总成本
```

---

#### US-3: Alias 路由分流（Priority: P1）

**作为** Owner，**我希望** 系统根据调用场景选择不同的模型（如轻量任务走 cheap 模型、复杂任务走 main 模型），**以便** 在保证质量的同时控制成本。

**优先级理由**: cheap/main 分流是行业共识（可节省 60-85% 成本），也是 Blueprint SS8.9.1 定义的 alias 策略的核心。Feature 003 的 summarizer 依赖 cheap alias，Feature 004 的 planner 依赖 main alias。

**独立测试**: 分别使用映射到 cheap 与 main 运行时 group 的语义 alias 调用 LLM，验证实际路由到了不同的模型。

**验收场景**:

```
Given LiteLLM Proxy 配置了 cheap 运行时 group 指向轻量模型（如 gpt-4o-mini）
  And LiteLLM Proxy 配置了 main 运行时 group 指向主力模型（如 gpt-4o）
When 系统使用映射到 cheap 的语义 alias（如 summarizer）发起一次 LLM 调用
  And 系统使用映射到 main 的语义 alias（如 planner）发起另一次 LLM 调用
Then 两次调用的 MODEL_CALL_COMPLETED 事件中 model_name 字段不同
  And cheap group 调用的 model_name 对应轻量模型
  And main group 调用的 model_name 对应主力模型
```

```
Given 系统已注册 6 个语义 alias（router/extractor/planner/executor/summarizer/fallback）
When 查询 AliasRegistry 中所有 alias 的配置
Then 每个 alias 都有明确的 category 归属（cheap / main / fallback）
  And 相同 category 的 alias 在 MVP 阶段路由到相同的模型
```

---

#### US-4: Provider 故障自动降级（Priority: P1）

**作为** Owner，**我希望** 当 LiteLLM Proxy 不可达时，系统自动降级到备用方案（如 Echo 模式），而不是直接报错导致任务失败，**以便** 系统在网络或 Proxy 异常时仍可基本运行。

**优先级理由**: 对齐 Constitution C6（Degrade Gracefully），这是"不可谈判的硬规则"。调研结论确认两级 fallback（Proxy 内置 model fallback + 应用层 Proxy fallback -> Echo）是最优方案。

**独立测试**: 停止 LiteLLM Proxy 容器后发送消息，验证系统降级到 EchoProvider 而不是报错。

**验收场景**:

```
Given LiteLLM Proxy 正常运行
When LiteLLM Proxy 突然变得不可达（容器停止或网络断开）
  And Owner 发送一条消息
Then 系统自动降级到 EchoProvider 处理该消息
  And MODEL_CALL_COMPLETED 事件中 is_fallback 字段为 true
  And 事件中包含降级原因说明
  And 任务正常推进到 SUCCEEDED（而非 FAILED）
```

```
Given 系统当前处于降级状态（使用 EchoProvider）
When LiteLLM Proxy 恢复可达
  And Owner 发送新的消息
Then 系统恢复使用 LiteLLM Proxy 处理消息
  And 新的 MODEL_CALL_COMPLETED 事件中 is_fallback 字段为 false
```

---

#### US-5: LLMService 平滑切换（Priority: P1）

**作为** Owner（也作为开发者），**我希望** LLM 服务从 Echo 模式切换到 LiteLLM 模式后，M0 的所有功能（任务创建、事件记录、SSE 推送、Web UI 展示）仍然正常工作，**以便** 我确信升级不会破坏已有能力。

**优先级理由**: 向后兼容是增量开发的基石。M0 已有 105 个通过的测试，Feature 002 不得导致任何回归。

**独立测试**: 在 LiteLLM 模式下运行 M0 的完整测试套件，所有测试通过。

**验收场景**:

```
Given OctoAgent 从 M0 升级到 Feature 002 版本
  And 系统配置为 LiteLLM 模式
When 运行 M0 的完整测试套件
Then 所有 M0 已有测试继续通过（向后兼容）
  And M0 的旧事件（不含 cost_usd、provider 等新字段）可正常反序列化
```

```
Given 系统配置为 Echo 模式（通过环境变量 OCTOAGENT_LLM_MODE=echo）
When 启动系统并发送消息
Then 系统行为与 M0 完全一致（EchoProvider 处理，事件结构兼容）
```

---

### P2 -- 重要补充（应当交付）

#### US-6: LiteLLM Proxy 健康检查（Priority: P2）

**作为** Owner（运维角色），**我希望** 通过一个简单的 HTTP 请求就能知道 LiteLLM Proxy 是否正常工作，**以便** 在部署或排障时快速定位问题。

**优先级理由**: 对齐 Constitution C8（可观测性是产品功能）。M0 已预留 `litellm_proxy: "skipped"` 占位，Feature 002 需要替换为真实检查。

**独立测试**: 分别在 Proxy 可达和不可达时请求 `/ready?profile=llm`，验证返回正确的状态。

**验收场景**:

```
Given LiteLLM Proxy 正常运行
When 请求 GET /ready?profile=llm
Then 返回 200 状态码
  And checks 中 litellm_proxy 字段值为 "ok"

Given LiteLLM Proxy 不可达
When 请求 GET /ready?profile=llm
Then 返回非 200 状态码
  And checks 中 litellm_proxy 字段标明不可达状态

Given 系统配置为 Echo 模式
When 请求 GET /ready（无 profile 参数或 profile=core）
Then litellm_proxy 检查返回 "skipped"
  And 不影响整体 ready 判定
```

---

#### US-7: Proxy 部署即开即用（Priority: P2）

**作为** Owner（开发者角色），**我希望** 有一份即开即用的 LiteLLM Proxy 部署配置，让我从零到"看到真实 LLM 响应"只需不到 15 分钟，**以便** 降低首次使用门槛。

**优先级理由**: 产研调研确认"首次体验 < 15 分钟"是关键体验指标。docker-compose 配置是实现此目标的关键交付物。

**独立测试**: 按照配置文件和文档，从零启动 Proxy 并完成一次真实 LLM 调用。

**验收场景**:

```
Given Owner 拥有至少 1 个 LLM provider 的 API key（如 OPENAI_API_KEY）
When 按照项目提供的 docker-compose 配置启动 LiteLLM Proxy
  And 启动 OctoAgent（LiteLLM 模式）
  And 发送一条消息
Then 整个过程耗时不超过 15 分钟（不含依赖下载时间）
  And 消息得到真实 LLM 响应
```

---

## 4. Functional Requirements（功能需求）

### 4.1 LLM 客户端

#### FR-002-CL-1: LiteLLM Proxy 调用封装 [MUST]

系统必须提供 LiteLLMClient 组件，封装对 LiteLLM Proxy 的调用。该组件必须支持以 messages 格式（chat completion）发送请求，通过 model alias 指定目标模型，并返回包含完整成本和 usage 数据的调用结果。

**追踪**: US-1, US-2, US-3

#### FR-002-CL-2: 异步优先 [MUST]

LiteLLMClient 的所有网络调用必须使用异步非阻塞方式，不得阻塞主事件循环。请求超时必须可配置，默认值为 30 秒，可通过环境变量覆盖。[AUTO-CLARIFIED: 默认超时 30 秒 -- 行业惯例，为大模型响应留有充分余量]

**追踪**: US-1

#### FR-002-CL-3: 调用结果数据模型 [MUST]

LLM 调用的返回结果必须包含以下字段：响应内容（content）、请求时使用的语义 alias（model_alias）、实际调用的模型名称（model_name）、实际 provider 名称（provider）、端到端耗时（duration_ms）、token 使用详情（prompt_tokens、completion_tokens、total_tokens）、USD 成本（cost_usd）、成本是否不可用（cost_unavailable）、是否降级调用（is_fallback）。调用结果必须使用强类型数据模型。

**追踪**: US-2, US-4

#### FR-002-CL-4: 响应摘要与截断 [MUST]

当 LLM 响应内容超过截断阈值时，写入 Event payload 的 `response_summary` 必须截断并附带截断标记。完整响应内容必须通过 Artifact 引用存储。截断阈值应当沿用 M0 已有的 8KB 默认值，且为可配置常量。[AUTO-RESOLVED: research-synthesis.md 未明确截断策略，沿用 M0 spec AC-3 的 8KB 阈值 + Artifact 引用模式，与 Constitution C8 最小化原则和 C11 上下文卫生一致]

**追踪**: US-2, WARN-2

### 4.2 Alias 管理

#### FR-002-AL-1: Alias 注册表 [MUST]

系统必须提供 AliasRegistry 组件，管理语义 alias 到 category（cheap / main / fallback）以及到运行时 group（cheap / main / fallback）的映射关系。MVP 阶段支持 6 个语义 alias：router、extractor、planner、executor、summarizer、fallback。

**追踪**: US-3

#### FR-002-AL-2: Alias 静态配置 [MUST]

Alias 映射必须从配置文件或环境变量加载（静态配置），启动时初始化，运行期间不变。配置至少包括：语义 alias 映射（6 个）和运行时 group 映射（cheap/main/fallback）。MVP 阶段不支持运行时动态切换映射。

**追踪**: US-3

#### FR-002-AL-3: Alias 查询接口 [SHOULD]

AliasRegistry 应当提供按语义 alias 查询配置、按 category 查询 alias 列表、按运行时 group 查询语义 alias 列表、列出所有 alias 的查询能力，供后续 Feature 使用。

**追踪**: US-3

### 4.3 成本追踪

#### FR-002-CT-1: 实时成本计算 [MUST]

系统必须在每次 LLM 调用返回后，立即计算该调用的 USD 成本。成本计算必须采用双通道策略：优先从模型网关公开响应字段获取成本值，若不可用则通过内置 pricing 数据库计算。若双通道均不可用，系统必须记录 `cost_usd=0.0` 且 `cost_unavailable=true`，并在事件 payload 中保留不可用标记。

**追踪**: US-2

#### FR-002-CT-2: Token Usage 解析 [MUST]

系统必须从 LLM 响应中解析完整的 token 使用数据，包括输入 token 数、输出 token 数和总 token 数。解析结果必须写入调用结果数据模型。token_usage 字段的 key 命名统一对齐行业标准（prompt_tokens/completion_tokens/total_tokens），替代 M0 的旧命名。M0 旧事件的反序列化不受影响。[AUTO-CLARIFIED: 对齐行业标准 key 命名]

**追踪**: US-2

#### FR-002-CT-3: 成本数据零依赖查询 [SHOULD]

成本数据通过已有的 Event Store 天然支持按任务聚合查询。CostTracker 应当提供辅助方法供后续 Feature（如 Policy Engine）便捷查询单个任务的总成本。

**追踪**: US-2

### 4.4 降级管理

#### FR-002-FM-1: 应用层降级策略 [MUST]

系统必须提供 FallbackManager 组件，实现两级降级：第一级由 LiteLLM Proxy 内部处理（model-level fallback，如 cheap 失败切换到 fallback 模型）；第二级由 FallbackManager 处理（Proxy 本身不可达时，降级到 EchoProvider）。

**追踪**: US-4

#### FR-002-FM-2: 降级标记 [MUST]

当发生应用层降级时，调用结果的 is_fallback 字段必须为 true。降级事件必须包含降级原因的描述信息。

**追踪**: US-4

#### FR-002-FM-3: 自动恢复 [SHOULD]

当 LiteLLM Proxy 从不可达状态恢复时，系统应当自动恢复使用 Proxy 处理后续请求，无需手动干预或重启。恢复探测采用 lazy probe 策略：每次 LLM 调用时先尝试 Proxy，若 Proxy 返回成功则恢复正常路径，若仍失败则继续 fallback 到 EchoProvider。MVP 阶段不引入后台定时探测，以避免额外的复杂度。[AUTO-CLARIFIED: Lazy probe 策略 -- 每次请求时尝试 Proxy，单用户场景调用频率低，lazy probe 开销可忽略，避免引入后台定时任务]

**追踪**: US-4

### 4.5 Event Payload 扩展

#### FR-002-EP-1: ModelCallCompletedPayload 扩展 [MUST]

ModelCallCompletedPayload 必须新增以下字段：cost_usd（USD 成本，浮点数，默认 0.0）、cost_unavailable（成本是否不可用，布尔值，默认 false）、model_name（实际模型名称，字符串，默认空）、provider（实际 provider 名称，字符串，默认空）、is_fallback（是否降级调用，布尔值，默认 false）。所有新增字段必须为 Optional 或有默认值，确保 M0 旧事件可正常反序列化。

**追踪**: US-2, US-5

#### FR-002-EP-2: 向后兼容 [MUST]

Event payload 的扩展不得破坏 M0 已有事件的反序列化。M0 产生的不含新字段的旧事件，在 Feature 002 版本的代码中必须可正常读取和展示。

**追踪**: US-5

#### FR-002-EP-3: ModelCallFailedPayload 扩展 [MUST]

ModelCallFailedPayload 必须新增以下字段：model_name（实际模型名称，字符串，默认空）、provider（实际 provider 名称，字符串，默认空）、is_fallback（是否降级调用，布尔值，默认 false）。所有新增字段必须为 Optional 或有默认值，确保 M0 旧事件可正常反序列化。MODEL_CALL_FAILED 事件须记录失败原因和超时信息（如适用）。

**追踪**: US-4, US-5, EC-5

### 4.6 LLMService 改造

#### FR-002-LS-1: 默认 Provider 切换 [MUST]

LLMService 的默认 provider 必须从 EchoProvider 切换为通过 FallbackManager 包装的 LiteLLMClient。EchoProvider 保留为 FallbackManager 的降级后备。切换行为通过配置控制（如环境变量），支持显式选择 Echo 模式。

**追踪**: US-1, US-5

#### FR-002-LS-2: Messages 格式支持 [MUST]

LLMService 的调用接口必须支持结构化消息格式（chat completion 多轮对话格式）。同时保留对 M0 简单文本 prompt 的向后兼容（自动转换为消息格式）。LiteLLMClient 仅接受消息格式；LLMService 负责文本 prompt 到消息格式的兼容转换；EchoProvider 通过适配层消费同一 messages 接口并保持 M0 行为兼容。[AUTO-CLARIFIED: LLMService 层适配 -- 各层职责分明，改动集中]

**追踪**: US-1, US-5

#### FR-002-LS-3: LLM 模式可配置 [MUST]

系统必须支持通过配置选择 LLM 运行模式：litellm（通过 Proxy 调用真实 LLM）、echo（Echo 模式，与 M0 行为一致）。默认模式为 litellm。模式切换不需要修改业务代码。

**追踪**: US-5

### 4.7 健康检查

#### FR-002-HC-1: Proxy 健康检查 [MUST]

系统必须提供 LiteLLM Proxy 健康检查能力，通过调用 Proxy 的健康检查端点判断其存活状态。

**追踪**: US-6

#### FR-002-HC-2: /ready 端点扩展 [MUST]

`GET /ready` 端点必须支持 `profile` 查询参数。当 `profile=llm` 时，除 M0 已有的核心检查外，必须额外检查 LiteLLM Proxy 的可达性。Proxy 不可达时，`profile=llm` 的整体结果应为非 ready。当未指定 profile 或 profile=core 时，LiteLLM Proxy 检查项返回 "skipped"（沿用 M0 行为），不影响整体 ready 判定。

**追踪**: US-6

### 4.8 Secrets 注入

#### FR-002-SK-1: API Key 不进应用层 [MUST]

LLM provider 的 API key（如 OPENAI_API_KEY、ANTHROPIC_API_KEY）必须仅注入到 LiteLLM Proxy 容器的环境变量中。OctoAgent 应用层代码不得持有、读取或传递任何 LLM provider 的 API key。OctoAgent 仅持有 Proxy 的访问密钥（如 LITELLM_PROXY_KEY），该密钥用于身份验证而非直接访问 LLM provider。[AUTO-RESOLVED: 遵循 Constitution C5（Least Privilege），产研调研一致推荐 Proxy 统一管理密钥，应用层不持有 provider API key]

**追踪**: US-1, WARN-1

#### FR-002-SK-2: Secrets 环境变量分层 [SHOULD]

系统应当支持 `.env` 分层：通用配置（`.env`）+ LiteLLM 专用 API keys（`.env.litellm`），两者均受 `.gitignore` 保护。

**追踪**: WARN-1

### 4.9 部署配置

#### FR-002-DC-1: Docker Compose 配置 [SHOULD]

系统应当提供 LiteLLM Proxy 的 Docker Compose 配置文件（如 `docker-compose.litellm.yml`），包含 Proxy 服务定义、端口映射、配置文件挂载、环境变量注入和健康检查配置。

**追踪**: US-7

#### FR-002-DC-2: Proxy 配置模板 [SHOULD]

系统应当提供 LiteLLM Proxy 的 YAML 配置模板（如 `litellm-config.yaml`），预配置 cheap/main/fallback 三个运行时 group，并提供语义 alias 到运行时 group 的映射示例，包含 fallback 策略和基础调优参数。

**追踪**: US-7

---

## 5. Key Entities（关键实体）

| 实体 | 描述 | 关键属性 |
|------|------|---------|
| **ModelCallResult** | 单次 LLM 调用的完整结果（Pydantic BaseModel，替代 M0 的 `LLMResponse` dataclass）[AUTO-CLARIFIED] | content, model_alias, model_name, provider, duration_ms, token_usage, cost_usd, cost_unavailable, is_fallback, fallback_reason |
| **TokenUsage** | Token 使用统计（key 统一为 `prompt_tokens`/`completion_tokens`/`total_tokens`，对齐 OpenAI 标准）[AUTO-CLARIFIED] | prompt_tokens, completion_tokens, total_tokens |
| **AliasConfig** | 单个语义 alias 的配置 | name, description, category（cheap/main/fallback）, runtime_group（cheap/main/fallback） |
| **AliasRegistry** | 所有 alias 的注册表 | aliases 集合, 查询接口 |
| **ModelCallCompletedPayload（扩展）** | MODEL_CALL_COMPLETED 事件的 payload | 继承 M0 字段 + cost_usd, cost_unavailable, model_name, provider, is_fallback |

---

## 6. Success Criteria（成功标准）

| 编号 | 标准 | 验证方式 |
|------|------|---------|
| SC-1 | 系统可通过模型网关成功调用至少 1 个 LLM provider，返回真实 LLM 响应（非 Echo 回声） | 端到端测试 |
| SC-2 | 每次 LLM 调用的成本（USD）、token 用量、provider 和模型名称均被完整记录到事件系统中；数据必须非负且在不可用场景带有明确标记 | 集成测试 |
| SC-3 | 语义 alias 按映射路由到运行时 group（cheap/main/fallback），事件中记录的模型名称符合映射预期 | 端到端测试 |
| SC-4 | 模型网关不可达时，系统自动降级到备用模式，降级标记被正确记录 | 集成测试 |
| SC-5 | 健康检查端点可返回模型网关的真实可达状态 | 集成测试 |
| SC-6 | 所有 M0 已有测试继续通过（向后兼容验证） | 回归测试 |
| SC-7 | 新增的 provider 模块单元测试覆盖率不低于 80% | 覆盖率分析 |

---

## 7. Edge Cases（边界场景）

### EC-1: LiteLLM Proxy 启动中尚未就绪

**场景**: OctoAgent 启动时，LiteLLM Proxy 容器正在启动但尚未完成初始化。
**关联**: FR-002-FM-1, FR-002-HC-1, US-4
**处理策略**: FallbackManager 检测到 Proxy 不可达，降级到 EchoProvider。健康检查 `/ready?profile=llm` 返回 Proxy 未就绪状态。Proxy 就绪后自动恢复。

### EC-2: LiteLLM Proxy 内部 model fallback 全部失败

**场景**: Proxy 配置的 cheap/main/fallback 三个运行时 group 对应模型全部不可用（如 API key 过期或 provider 全部宕机）。
**关联**: FR-002-FM-1, US-4
**处理策略**: Proxy 返回错误后，FallbackManager 将请求降级到 EchoProvider。MODEL_CALL_COMPLETED 事件记录 is_fallback=true 和降级原因。

### EC-3: 成本数据不可用

**场景**: LLM provider 返回的响应中不包含 usage 信息，或 LiteLLM 的 pricing 数据库中没有对应模型的定价。
**关联**: FR-002-CT-1, US-2
**处理策略**: CostTracker 双通道均失败时，cost_usd 记录为 0.0，`cost_unavailable=true`，并在事件 payload 中保留成本不可用标记。token_usage 字段如不可用则填充为全零。系统不因成本计算失败而中断正常流程。

### EC-4: Proxy 地址配置错误

**场景**: 环境变量 LITELLM_PROXY_URL 配置了错误的地址（如端口不对或域名不存在）。
**关联**: FR-002-CL-1, FR-002-FM-1, US-4
**处理策略**: 与 Proxy 不可达的处理方式一致 -- FallbackManager 降级到 EchoProvider。启动日志中应当输出明确的连接错误信息和配置提示。`/ready?profile=llm` 返回错误详情。

### EC-5: LLM 调用超时

**场景**: LLM 调用因模型响应慢或网络问题超时。
**关联**: FR-002-CL-2, US-1
**处理策略**: 超时后记录 MODEL_CALL_FAILED 事件（包含超时时长和配置的超时阈值）。FallbackManager 可尝试降级到 EchoProvider（视超时是否被归类为 Proxy 不可达而定）。

### EC-6: M0 旧事件反序列化

**场景**: 数据库中存在 M0 产生的 MODEL_CALL_COMPLETED 事件，不包含 cost_usd、provider 等新字段。
**关联**: FR-002-EP-2, US-5
**处理策略**: 所有新增字段均有默认值（cost_usd=0.0, provider="", model_name="", is_fallback=false），M0 旧事件反序列化时自动使用默认值，不报错。

### EC-7: 并发 LLM 调用

**场景**: 多个任务同时触发 LLM 调用，竞争 Proxy 资源。
**关联**: FR-002-CL-1, US-1
**处理策略**: LiteLLMClient 使用异步调用，天然支持并发。Proxy 的 router_settings 配置了限流和重试策略。每个调用独立产生事件，互不影响。M0 为单用户场景，极端并发不是核心关注点。

---

## 8. Constraints（约束）

### 8.1 Constitution 约束

| 宪法原则 | Feature 002 中的体现 |
|----------|---------------------|
| C1: Durability First | MODEL_CALL_STARTED/COMPLETED/FAILED 事件持久化到 Event Store，成本数据随事件落盘 |
| C2: Everything is an Event | 每次 LLM 调用生成 STARTED/COMPLETED/FAILED 事件，包含完整成本数据 |
| C5: Least Privilege | API keys 仅在 Proxy 容器，不进 OctoAgent 应用层（FR-002-SK-1） |
| C6: Degrade Gracefully | FallbackManager 实现 Proxy 不可达时自动降级到 EchoProvider |
| C8: Observability is a Feature | 成本/usage/provider/model 全量记录 + `/ready?profile=llm` 健康检查 |
| C8 补充: 日志最小化 | response_summary 截断策略对齐 M0 的 8KB 阈值 + Artifact 引用（WARN-2） |
| C8 补充: 敏感数据脱敏 | API keys 不进日志/Event；Proxy 访问密钥不写入 Event payload |

### 8.2 技术栈约束（来自 Blueprint 已锁定决策，非规范性参考）

> 以下技术选型来自 Blueprint SS7 和 Constitution SSIII 的已锁定决策，列出它们是为了约束实现边界，不属于本规范的功能定义。详见 Blueprint SS7 和 SS8.9。

### 8.3 架构约束

- Feature 002 在 M0 的单进程合并架构上构建，不引入新的独立进程
- LiteLLM Proxy 是唯一的外部容器依赖
- packages/provider 作为 workspace 的独立包，通过 pyproject.toml 管理依赖
- packages/provider 依赖 packages/core（共享数据模型和 Event 定义）

### 8.4 MVP 范围约束

以下功能明确不在 Feature 002 范围内，以确保边界清晰：

| 排除功能 | 归属 | 理由 |
|----------|------|------|
| Streaming 支持 | M1.5+ | M1 Skill Runner 不依赖流式输出 |
| 运行时动态切换 alias | M2+ | 重启服务切换即可，个人使用场景低频操作 |
| per-task 预算限制 + 自动降级 | Feature 005 (Policy Engine) | CostTracker 已预留数据接口 |
| 成本告警 | Feature 005 + M2（渠道通知） | 需 Policy Engine + 通知渠道 |
| 前端 cost dashboard | 前端迭代 | Event Store SQL 聚合已足够 MVP |
| 智能路由 | M3+ | 需 Orchestrator 路由能力 |
| 熔断器（circuit breaker） | M2+ | 两级 fallback 已足够 M1 |

---

## 9. Clarifications

### Session 2026-02-28 (Specify)

#### AC-1: response_summary 的截断策略（WARN-2）

**问题**: Constitution WARN-2 要求明确 `response_summary` 字段的内容截断策略，避免完整 LLM 响应进入 Event payload。

**解决**: [AUTO-RESOLVED: 沿用 M0 spec AC-3 的 8KB（8192 字节）截断阈值。LLM 完整响应超过阈值时，response_summary 存储截断内容（附截断标记），完整响应通过 Artifact 引用存储。理由：(1) M0 已验证此阈值的合理性；(2) 与 Blueprint SS8.5.2 的 max_inline_chars 一致；(3) Constitution C8 最小化原则和 C11 上下文卫生明确要求不将大文本写入 Event payload]

#### AC-2: API key 注入路径（WARN-1）

**问题**: Constitution WARN-1 要求明确 API key 的注入方式。

**解决**: [AUTO-RESOLVED: API key 通过环境变量注入到 LiteLLM Proxy Docker 容器（`.env.litellm` -> docker-compose environment），不进 OctoAgent 应用层。OctoAgent 仅持有 Proxy 访问密钥（LITELLM_PROXY_KEY）。理由：(1) Constitution C5 要求最小权限；(2) 产研调研和技术调研一致推荐 Proxy 统一管理密钥；(3) Blueprint SS16.3 明确规定 `.env` 分层方案]

### Session 2026-02-28 (Clarify)

#### AC-3: LLMProvider 接口演进 -- messages 格式支持

**问题**: M0 的 `LLMProvider.call(prompt: str)` 仅接受纯字符串，而 FR-002-LS-2 要求支持 messages 格式（`list[dict]`）。接口演进路径未明确。

**解决**: [AUTO-CLARIFIED: LLMService 层适配 -- LiteLLMClient 内部仅接受 messages 格式；LLMService 层负责 prompt-string -> messages 的向后兼容转换（`[{"role": "user", "content": prompt}]`）；EchoProvider 通过 EchoMessageAdapter 消费同一 messages 接口并保持 M0 输出行为一致。理由：(1) FallbackManager 统一调用契约，避免 prompt/messages 双轨接口分叉；(2) 各层职责分明——LiteLLMClient 对齐 LiteLLM SDK 原生接口，LLM 兼容逻辑在服务层/适配层完成；(3) 对业务调用方保持无感，改动边界可控]

#### AC-4: token_usage 字段 key 命名标准化

**问题**: M0 代码中 `token_usage` 的 key 使用 `prompt`/`completion`/`total`，与 spec 定义的 `prompt_tokens`/`completion_tokens`/`total_tokens`（OpenAI API 标准）不一致。

**解决**: [AUTO-CLARIFIED: 对齐 OpenAI/LiteLLM 行业标准 -- Feature 002 起统一使用 `prompt_tokens`/`completion_tokens`/`total_tokens` 命名。M0 旧事件的 token_usage 是 `dict[str, int]` 类型，key 差异不影响 Pydantic 反序列化（只影响查询时的 key 访问），向后兼容无风险。理由：(1) LiteLLM SDK 返回的 usage 对象原生使用此命名；(2) 与 OpenAI API 标准一致；(3) 避免后续在 CostTracker 和 Policy Engine 中做 key 映射]

#### AC-5: LLMResponse -> ModelCallResult 数据模型迁移

**问题**: M0 使用 `@dataclass class LLMResponse`（4 个字段），spec 定义 `ModelCallResult` 为 Pydantic BaseModel（10+ 字段）。两者并存还是替换？

**解决**: [AUTO-CLARIFIED: ModelCallResult 直接替换 LLMResponse -- packages/provider 定义 `ModelCallResult(BaseModel)` 作为统一返回类型，EchoProvider 也返回 ModelCallResult（新增字段使用默认值：cost_usd=0.0, cost_unavailable=false, provider="echo", model_name="echo", is_fallback=按实际情况设置）。M0 的 `LLMResponse` dataclass 废弃。理由：(1) ModelCallResult 是 LLMResponse 的超集，无信息丢失；(2) FallbackManager 需要统一的返回类型（含 is_fallback 标记）；(3) Pydantic BaseModel 对齐项目数据模型规范]

#### AC-6: FallbackManager 自动恢复探测策略

**问题**: FR-002-FM-3 要求 Proxy 恢复后自动恢复使用，但未定义探测机制。Lazy probe（每次请求时尝试）vs Active probe（后台定时探测）？

**解决**: [AUTO-CLARIFIED: Lazy probe 策略 -- 每次 LLM 调用时，FallbackManager 先尝试通过 LiteLLMClient 调用 Proxy，成功则正常返回，失败则 fallback 到 EchoProvider。不维护显式的"降级状态"标记，避免状态同步问题。理由：(1) 单用户场景调用频率低，lazy probe 开销可忽略；(2) 避免引入后台定时任务和状态机的复杂度；(3) 实现简洁，首次 Proxy 恢复时即可自动感知]

#### AC-7: LLM 调用默认超时值

**问题**: FR-002-CL-2 要求"请求超时必须可配置"但未定义默认值。

**解决**: [AUTO-CLARIFIED: 默认 30 秒 -- 沿用 LiteLLM SDK 的默认 `request_timeout` 值（30 秒），可通过环境变量 `OCTOAGENT_LLM_TIMEOUT_S` 覆盖。理由：(1) 30 秒是 LiteLLM SDK 默认值，行业惯例；(2) GPT-4 级模型通常 5-15 秒内响应，30 秒留有充分余量；(3) 与 M0 不设超时相比，增加超时保护更安全]

---

## 附录 A: User Story 与 FR 追踪矩阵

| FR 编号 | US 追踪 | 级别 |
|---------|---------|------|
| FR-002-CL-1 | US-1, US-2, US-3 | MUST |
| FR-002-CL-2 | US-1 | MUST |
| FR-002-CL-3 | US-2, US-4 | MUST |
| FR-002-CL-4 | US-2, WARN-2 | MUST |
| FR-002-AL-1 | US-3 | MUST |
| FR-002-AL-2 | US-3 | MUST |
| FR-002-AL-3 | US-3 | SHOULD |
| FR-002-CT-1 | US-2 | MUST |
| FR-002-CT-2 | US-2 | MUST |
| FR-002-CT-3 | US-2 | SHOULD |
| FR-002-FM-1 | US-4 | MUST |
| FR-002-FM-2 | US-4 | MUST |
| FR-002-FM-3 | US-4 | SHOULD |
| FR-002-EP-1 | US-2, US-5 | MUST |
| FR-002-EP-2 | US-5 | MUST |
| FR-002-EP-3 | US-4, US-5, EC-5 | MUST |
| FR-002-LS-1 | US-1, US-5 | MUST |
| FR-002-LS-2 | US-1, US-5 | MUST |
| FR-002-LS-3 | US-5 | MUST |
| FR-002-HC-1 | US-6 | MUST |
| FR-002-HC-2 | US-6 | MUST |
| FR-002-SK-1 | US-1, WARN-1 | MUST |
| FR-002-SK-2 | WARN-1 | SHOULD |
| FR-002-DC-1 | US-7 | SHOULD |
| FR-002-DC-2 | US-7 | SHOULD |

## 附录 B: Constitution WARN 处置记录

| WARN 编号 | 问题 | 处置 | 对应 FR |
|-----------|------|------|---------|
| WARN-1 | Secrets 注入路径 -- API key 的注入方式需明确 | API key 仅注入 Proxy 容器环境变量，应用层不持有 | FR-002-SK-1, FR-002-SK-2 |
| WARN-2 | ModelCallResult 数据边界 -- response_summary 截断策略需明确 | 沿用 M0 的 8KB 阈值 + Artifact 引用模式 | FR-002-CL-4 |

## 附录 C: 与 Research Synthesis 的对齐验证

| 调研推荐项 | 综合评分 | spec 对齐 | 对应 FR |
|-----------|---------|-----------|---------|
| LiteLLMClient（SDK 直连 Proxy） | 三星 | 纳入 MVP | FR-002-CL-1, CL-2, CL-3 |
| AliasRegistry（语义 alias + 运行时 group 映射） | 三星 | 纳入 MVP | FR-002-AL-1, AL-2, AL-3 |
| CostTracker（usage -> cost 计算） | 三星 | 纳入 MVP | FR-002-CT-1, CT-2, CT-3 |
| FallbackManager（Proxy 不可达降级 Echo） | 三星 | 纳入 MVP | FR-002-FM-1, FM-2, FM-3 |
| ModelCallCompletedPayload 扩展 | 三星 | 纳入 MVP | FR-002-EP-1, EP-2 |
| LLMService 改造（Echo -> LiteLLM） | 三星 | 纳入 MVP | FR-002-LS-1, LS-2, LS-3 |
| /ready?profile=llm 健康检查 | 三星 | 纳入 MVP | FR-002-HC-1, HC-2 |
| LiteLLM Proxy docker-compose | 二星 | 纳入（SHOULD） | FR-002-DC-1, DC-2 |
| Streaming 支持 | 二星 | 排除（M1.5+） | -- |
| 运行时动态切换 alias | 一星 | 排除（M2+） | -- |
| per-task 预算限制 | 一星 | 排除（Feature 005） | -- |
