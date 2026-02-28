# Feature 002 产品调研报告：LiteLLM Proxy 集成 + 成本治理

**特性**: 002-integrate-litellm-provider
**阶段**: Research -- 产品调研
**日期**: 2026-02-28
**调研模式**: full (quality-first)
**前序制品**: constitution.md, blueprint.md, m1-feature-split.md

---

## 1. 执行摘要

### 1.1 调研范围

本报告从产品角度调研 Feature 002（LiteLLM Proxy 集成 + 成本治理）的市场需求、竞品方案、用户痛点和最佳实践，为后续 spec 编写提供产品依据。

### 1.2 关键发现

1. **模型网关已成为 Agent 框架标配**：所有主流 Agent 框架（LangChain、CrewAI、Agent Zero）均通过某种形式实现了多模型管理，但成熟度差异显著。LangChain 最为成熟（内置 middleware 路由 + 成本追踪），Agent Zero 与 OctoAgent 定位最接近（chat/utility/embedding 三模型分离）。
2. **LiteLLM Proxy 是自托管场景的最佳选择**：对比 LiteLLM Proxy、Portkey AI Gateway、OpenRouter、LLM Gateway 四个方案，LiteLLM Proxy 在自托管易用性、成本追踪粒度、model alias 路由、与 Python 生态集成方面均领先，且与 Blueprint 已确定的技术选型（Pydantic AI + LiteLLM）完美对齐。
3. **cheap/main 模型分离是行业共识**：Agent Zero 的 chat_model/utility_model 分离、CrewAI 的 per-agent model 配置、LangChain 的 cost-optimized fallback 均印证了这一模式。业界数据显示，合理的 cheap/main 分流可节省 60-85% 成本。
4. **成本治理的核心是"可见性 + 预算执行"**：per-task 成本追踪、预算阈值告警、自动降级（expensive -> cheap -> pause）构成三层防线，是所有成熟方案的标配。
5. **Feature 002 的 MVP 范围合理**：当前定义的范围（LiteLLMClient + AliasRegistry + CostTracker + FallbackManager + Readiness 检查）恰好覆盖了作为 M1 后续 Feature 依赖的最小必要能力。

### 1.3 关键建议

- **不需扩大范围**：Feature 002 的当前定义已充分，不应加入预算策略执行（属于 Policy Engine 领域）或高级 cost dashboard（属于前端迭代）。
- **技术调研应关注**：LiteLLM Proxy + Pydantic AI 的 structured output 兼容性验证、LiteLLM usage callback 的 cost 字段可靠性、Docker 部署方案细节。

---

## 2. 竞品分析

### 2.1 竞品选择逻辑

选择与 OctoAgent 在"个人 AI OS / Agent 框架"定位上有交集的 5 个系统，重点关注其模型网关层的设计。

| 竞品 | 定位 | 与 OctoAgent 关联 |
|------|------|-------------------|
| **Agent Zero** | 个人 AI 助手框架 | 定位最接近，同为个人使用的长期运行 Agent |
| **AutoGPT** | 自主 AI Agent 平台 | 先驱项目，开源 Agent 生态参考 |
| **CrewAI** | 多 Agent 协作框架 | per-agent 模型配置参考 |
| **LangChain** | LLM 应用开发框架 | 生态最成熟，middleware 模式参考 |
| **Semantic Kernel** | 微软 AI 编排 SDK | 企业级可观测性集成参考 |

### 2.2 模型管理方案对比

| 维度 | Agent Zero | AutoGPT | CrewAI | LangChain | OctoAgent (Feature 002) |
|------|-----------|---------|--------|-----------|-------------------------|
| **模型分层** | chat/utility/embedding/browser 四类模型 | 单一主模型 | per-agent 自定义 LLM | per-chain 自定义 | cheap/main 双 alias + 6 个语义别名 |
| **Provider 抽象** | 统一 ModelConfig 接口，支持 OpenAI/Ollama/Anthropic 等 | 依赖外部路由（如 Requesty） | LLM class 封装，支持 100+ 模型 | ChatModel 接口 + middleware | LLMProvider ABC + LiteLLM Proxy |
| **Alias 路由** | 无显式 alias，按角色配置模型 | 无 | 无显式 alias，per-agent 绑定 | 支持 model pricing maps | 6 个语义 alias: router/extractor/planner/executor/summarizer/fallback |
| **Fallback 机制** | 社区 feature request 阶段 | 外部依赖 | 通过 Portkey 集成 | 内置 fallback middleware | FallbackManager（LiteLLM 原生 + 应用层） |
| **成本追踪** | token 计数，无 cost 换算 | 基础 usage 统计 | 通过 Langtrace/AgentOps 集成 | 内置 per-run cost 计算 | CostTracker（per-event 粒度写入 Event Store） |
| **成本可见性** | 无 dashboard | 有限 | 第三方 dashboard | LangSmith dashboard | Event Store SQL 聚合（M0 已有基础） |

### 2.3 关键竞品深度分析

#### 2.3.1 Agent Zero -- 最接近的对标

Agent Zero 的模型管理架构是与 OctoAgent 最相关的参考：

- **三层配置优先级**：硬编码默认值 -> JSON 配置文件 -> 环境变量。OctoAgent 的 config 设计可参考此模式。
- **四类模型分工**：chat_model（主推理）、utility_model（内部轻任务）、embedding_model（向量检索）、browser_model（网页浏览）。这与 OctoAgent 的 6 alias 体系有映射关系：
  - chat_model ≈ planner/executor
  - utility_model ≈ router/extractor/summarizer
  - 但 Agent Zero 没有 fallback 概念
- **痛点**：Agent Zero 社区多次提出 feature request 希望增加智能路由（按任务类型自动选模型）和 fallback（provider 故障自动切换），但至今未实现。这正是 OctoAgent Feature 002 的交付重点。

#### 2.3.2 LangChain -- 生态最成熟

LangChain 的 middleware 模式是行业标杆：

- **内置 fallback**：`model.with_fallbacks([backup_model])` 一行代码实现故障切换
- **cost 追踪**：从 token 计数 + 厂商定价表自动计算美元成本，per-run 可见
- **LangSmith 集成**：全链路 trace 含 cost/latency/tokens，支持 per-chain 聚合

**OctoAgent 的差异化**：LangChain 的 cost 追踪在应用层实现，依赖手动维护的定价表；OctoAgent 通过 LiteLLM Proxy 直接获取 usage.cost，数据源更可靠。此外 LangChain 不提供 per-task 预算概念，而 OctoAgent 的 Event Store 天然支持 per-task cost 聚合。

#### 2.3.3 CrewAI -- per-Agent 模型配置参考

CrewAI 允许每个 Agent 绑定不同的 LLM 实例：

```python
agent = Agent(
    role="Researcher",
    llm=LLM(model="openai/gpt-4o", temperature=0.7),
)
```

这启发了 OctoAgent 的设计：在 M1.5（Worker 阶段），每个 Worker 可以通过 model_alias 绑定不同的模型策略，而 Feature 002 的 AliasRegistry 正是这一能力的基础。

### 2.4 差异化机会

基于竞品分析，OctoAgent 在模型网关层的 **5 个差异化机会**：

| # | 差异化点 | 说明 | 竞品现状 |
|---|---------|------|---------|
| 1 | **per-task 成本追踪** | 每个 task 可聚合所有 MODEL_CALL 事件的 cost/tokens | Agent Zero/AutoGPT 无此能力；LangChain per-run 但不 per-task |
| 2 | **语义化 alias 体系** | 6 个语义别名（router/planner/...）而非 cheap/expensive 二分 | 竞品普遍只有 2-3 层模型分类 |
| 3 | **事件溯源的成本可见性** | cost 数据作为 Event payload 持久化，支持历史分析 | 竞品依赖外部 dashboard（LangSmith、Langtrace） |
| 4 | **优雅降级内置** | LiteLLM Proxy 不可达 -> EchoProvider fallback，对齐 Constitution C6 | Agent Zero 直接报错；AutoGPT 无降级 |
| 5 | **预算执行闭环**（M1+） | per-task budget -> 超限降级 -> pause -> 用户审批 | 业界仅有 AgentBudget 等库提供类似能力 |

---

## 3. 用户需求分析

### 3.1 目标用户画像

OctoAgent 的目标用户是 **技术型个人用户**（Connor 自用 + 扩展到 1-3 人小团队），Feature 002 的用户需求从两个角色切入：

#### Persona A: 开发者 Connor（系统构建者）

- **需求**：在 OctoAgent 内部统一管理所有 LLM 调用，不在业务代码中硬编码 provider/model
- **痛点**：M0 的 Echo 模式无法测试真实 LLM 交互链路；切换 provider 需改代码
- **期望**：改一行配置即可切换整个系统的模型供应商

#### Persona B: 日常使用者 Connor（Agent 使用者）

- **需求**：知道每次 Agent 操作花了多少钱、用了多少 token
- **痛点**：多个 LLM 供应商的计费不透明，无法按 task 归因成本
- **期望**：在 Task 详情页看到成本数据；异常高消耗时被告知

### 3.2 用户旅程分析

#### 旅程 1: 首次配置 LiteLLM Proxy

```
1. 开发者配置 LiteLLM Proxy（docker-compose）
2. 在 config 中配置 alias -> model 映射
3. 启动 OctoAgent，访问 /ready?profile=llm 验证连通性
4. 发送一条消息，观察 MODEL_CALL 事件包含真实 cost/tokens
```

**关键体验指标**：从零到"看到真实 LLM 响应" < 15 分钟

#### 旅程 2: Provider 故障时的降级体验

```
1. 用户正在使用 OctoAgent，LiteLLM Proxy 变得不可达
2. 系统自动切换到 fallback provider（或 Echo 模式）
3. 用户看到降级提示（事件/通知）
4. LiteLLM Proxy 恢复后，系统自动恢复正常路由
```

**关键体验指标**：降级切换 < 5 秒；用户可感知降级状态

#### 旅程 3: 查看 Task 成本

```
1. 用户完成一个需要多次 LLM 调用的 Task
2. 在 Task 详情页看到总 cost、token 用量、各次调用明细
3. 通过 model_alias 了解 cheap vs main 的成本占比
```

**关键体验指标**：cost 数据完整且准确；数据延迟 < 1 秒（同步写入）

### 3.3 用户痛点与需求缺口

| 痛点 | 当前状态 (M0) | Feature 002 解决方案 | 剩余缺口（后续 Feature） |
|------|--------------|---------------------|------------------------|
| Echo 模式无法测试真实链路 | EchoProvider 仅返回输入回声 | LiteLLMProvider 接入真实模型 | -- |
| 不知道每次操作花多少钱 | token_usage 为模拟数据 | 真实 cost/tokens 写入 Event | cost dashboard（前端迭代） |
| 切换 provider 需改代码 | 硬编码 EchoProvider | alias -> model 配置映射 | 运行时动态切换 alias |
| Provider 故障无降级 | N/A（无外部依赖） | FallbackManager + EchoProvider 保底 | 熔断策略（circuit breaker） |
| 模型选择不合理导致高成本 | N/A | cheap/main alias 分流 | 智能路由（按任务复杂度自动选模型） |

### 3.4 OctoAgent "个人 AI OS" 对模型网关的独特需求

与企业级 Agent 平台不同，OctoAgent 作为"个人 AI OS"有独特需求：

1. **零运维偏好**：模型网关必须"配置后忘记"，不需要 DBA 或 SRE 维护。LiteLLM Proxy 的 Docker + YAML 配置模式满足此需求。
2. **成本敏感但非极致**：个人用户关心月度总支出（如 < $50/月），但不需要企业级的 chargeback 或 department 分账。per-task 粒度的成本追踪已足够。
3. **本地优先**：模型网关必须可自托管，不依赖 SaaS 服务。LiteLLM Proxy 的开源 + Docker 方案完美匹配。
4. **降级容忍**：个人用户可以接受短暂降级（如 Echo 模式提示"当前为离线模式"），不需要 99.99% SLA。
5. **多供应商灵活性**：个人用户可能使用多个 API key（OpenAI + Anthropic + 本地 Ollama），需要统一出口。

---

## 4. 成本治理最佳实践

### 4.1 行业成本治理三层模型

```
Layer 1: 可见性（Visibility）    -- 知道花了多少钱
Layer 2: 告警（Alerting）         -- 异常时被通知
Layer 3: 执行（Enforcement）      -- 自动降级/暂停
```

**Feature 002 应覆盖 Layer 1（可见性）**，Layer 2-3 的预算策略执行属于 Policy Engine（Feature 005）领域。

### 4.2 可见性层最佳实践

| 实践 | 说明 | Feature 002 对齐 |
|------|------|-----------------|
| **per-request 成本记录** | 每次 LLM 调用记录 input_tokens、output_tokens、cost_usd、model、provider、latency | ModelCallCompletedPayload 扩展 cost/provider 字段 |
| **per-task 成本聚合** | 按 task_id 聚合所有 MODEL_CALL 事件的 cost | Event Store SQL 查询天然支持 |
| **alias 级成本归因** | 区分 cheap/main 的成本占比 | AliasRegistry + model_alias 字段 |
| **成本换算统一** | 不同 provider 的定价统一为 USD | LiteLLM 内置 completion_cost() 函数 |

### 4.3 cheap/main 分流的成本优化效果

业界数据（基于 Perplexity 调研结果）：

| 策略 | 节省比例 | 适用场景 |
|------|---------|---------|
| 简单任务走 cheap 模型（如 summarizer, router） | 60-78% | OctoAgent 的 router/extractor/summarizer alias |
| 复杂任务走 main 模型（如 planner, executor） | 基线 | OctoAgent 的 planner/executor alias |
| 失败重试升级模型（cheap -> main） | 视失败率 | Skill Runner 的 retry_policy.upgrade_model_on_fail |

**OctoAgent 的 6 alias 体系**（router/extractor/planner/executor/summarizer/fallback）比行业常见的 2 层分级（cheap/expensive）更精细，理论上可实现更优的成本控制。但 MVP 阶段建议先实现 cheap/main 两类，6 个别名映射到这两类即可。

### 4.4 成本降级策略参考

虽然 Feature 002 不实现预算执行，但需要为后续 Feature 留好接口：

```
预算充足 → 正常路由（main alias）
接近预算 → 告警 + 降级到 cheap alias  [Future: Feature 005]
超出预算 → 暂停任务 + 等待用户确认    [Future: Feature 005]
```

Feature 002 的 CostTracker 需要提供 `get_task_total_cost(task_id)` 接口，供后续 Policy Engine 查询。

### 4.5 业界工具参考

| 工具 | 类型 | 核心能力 | 与 OctoAgent 关系 |
|------|------|---------|-------------------|
| **AgentBudget** | Python 库 | per-session 预算限制、auto-stop | 理念参考；但 OctoAgent 通过 Event Store + Policy Engine 自研 |
| **Portkey** | SaaS/OSS Gateway | 实时 cost dashboard、budget limits | 功能对标；但 OctoAgent 选择 LiteLLM（更轻量、更 Python 原生） |
| **LangSmith** | SaaS | per-run cost 追踪、trace 关联 | 可观测性参考；OctoAgent 通过 Logfire + Event Store 实现类似能力 |
| **Helicone** | SaaS Gateway | 请求级 cost 记录、异常检测 | 功能参考；OctoAgent 的 Event Store 已覆盖核心能力 |

---

## 5. MVP 范围建议

### 5.1 Feature 002 Must-Have（不能少）

以下能力是 Feature 003-005 的硬依赖，Feature 002 必须交付：

| # | 功能 | 依赖方 | 理由 |
|---|------|--------|------|
| 1 | **LiteLLMClient** -- 封装 litellm SDK 调用 | Feature 003（ToolBroker 需要 summarizer alias 做输出压缩）、Feature 004（Skill Runner 需要通过 alias 调用模型） | 所有需要真实 LLM 的 Feature 都依赖此 |
| 2 | **AliasRegistry** -- cheap/main alias 映射 | Feature 003（summarizer alias）、Feature 004（planner alias） | 别名路由是统一模型出口的核心 |
| 3 | **CostTracker** -- 解析 usage -> cost 计算 | Feature 005（Policy Engine 需要查询 per-task cost 做预算判断） | 成本可见性的数据基础 |
| 4 | **FallbackManager** -- provider 故障自动切换 | 全局（Constitution C6: Degrade Gracefully） | 降级能力是宪法要求 |
| 5 | **ModelCallCompletedPayload 扩展** -- 增加 cost_usd/provider 字段 | Feature 005（成本告警）、前端（成本展示） | Event 数据完整性 |
| 6 | **LLMService 改造** -- EchoProvider -> LiteLLMProvider | 全局 | M1 智能闭环的前提 |
| 7 | **/ready?profile=llm** -- LiteLLM Proxy 健康检查 | 运维/部署 | 可观测性宪法要求 |

### 5.2 Nice-to-Have（可选，不阻塞后续）

| # | 功能 | 说明 | 建议 |
|---|------|------|------|
| 1 | 运行时动态切换 alias 映射 | 不重启服务即可修改 alias -> model 映射 | 推迟到 M1.5；当前通过重启服务切换配置即可 |
| 2 | LiteLLM Proxy docker-compose 配置 | 提供即开即用的 docker-compose.yml | 可作为 Feature 002 的一部分或独立文档；建议纳入（降低上手门槛） |
| 3 | cost 聚合查询 API | `GET /api/tasks/{id}/cost` 返回 per-task 成本 | 推迟到前端迭代；当前 Event Store 可直接 SQL 查询 |
| 4 | streaming 支持 | LiteLLMClient 支持 SSE streaming 响应 | 推迟；M1 的 Skill Runner 不依赖 streaming |

### 5.3 Future（明确不在 Feature 002 范围）

| # | 功能 | 属于 | 理由 |
|---|------|------|------|
| 1 | per-task 预算限制 + 自动降级 | Feature 005 (Policy Engine) | 预算执行是策略层能力 |
| 2 | 成本告警（超阈值通知） | Feature 005 / M2（渠道通知） | 需要 Policy Engine + 通知渠道 |
| 3 | cost dashboard（前端页面） | 前端迭代 | 不阻塞核心功能 |
| 4 | 智能路由（按任务复杂度自动选模型） | M3+ | 需要 Orchestrator 具备路由能力 |
| 5 | 多 provider 的统一定价管理 | M2+ | LiteLLM 内置定价已足够 MVP |
| 6 | 熔断器（circuit breaker） | M2+ | Blueprint 已定义（5min 3 次失败），但 M1 不需要 |

### 5.4 范围边界总结

```
Feature 002 交付范围:
+---------------------------------------------+
| packages/provider                           |
|   - LiteLLMClient (alias-aware SDK wrapper) |
|   - AliasRegistry (cheap/main + 6 semantic) |
|   - CostTracker (usage -> cost_usd)         |
|   - FallbackManager (provider failover)     |
+---------------------------------------------+
| apps/gateway 改造                            |
|   - LLMService: Echo -> LiteLLM             |
|   - 保留 EchoProvider 作为 fallback          |
|   - /ready?profile=llm 健康检查             |
+---------------------------------------------+
| packages/core 扩展                           |
|   - ModelCallCompletedPayload + cost/provider|
|   - MODEL_CALL 事件写入真实数据              |
+---------------------------------------------+
| 配置                                         |
|   - LiteLLM Proxy 连接配置                   |
|   - alias -> model 映射配置                  |
|   - LiteLLM Proxy docker-compose (Nice-to-have)|
+---------------------------------------------+

不在范围内:
- 预算限制 / 成本告警 / 自动降级策略执行
- 前端 cost dashboard
- streaming 支持
- 智能路由
- 熔断器
```

---

## 6. 关键风险与机会

### 6.1 风险

| # | 风险 | 影响 | 缓解建议 |
|---|------|------|---------|
| R1 | **LiteLLM Proxy 部署阻塞** -- 配置复杂度超出预期、provider API key 不可用 | 高：阻塞整个 M1 | 技术调研阶段做 PoC 验证；提前准备至少 2 个 provider 的 API key |
| R2 | **LiteLLM usage.cost 字段不可靠** -- 部分 provider 不返回 usage 或 cost 数据不准确 | 中：成本数据不完整 | CostTracker 实现 fallback 逻辑：优先用 LiteLLM 返回的 cost，否则用内置定价表计算 |
| R3 | **Pydantic AI + LiteLLM Proxy 兼容性** -- structured output / function calling 在 Proxy 中间层可能有兼容问题 | 中：影响 Feature 004 | Feature 002 技术调研阶段验证兼容性；这是给 Feature 004 的前期投资 |
| R4 | **Event payload 扩展的向后兼容** -- ModelCallCompletedPayload 增加字段可能影响 M0 的事件消费者 | 低：M0 事件消费者有限 | 新字段使用 Optional + 默认值，确保旧事件可反序列化 |
| R5 | **EchoProvider 降级体验差** -- 降级到 Echo 模式后用户体验落差大 | 低：个人使用场景可接受 | 降级时在事件中写入清晰的降级原因和恢复时间预估 |

### 6.2 机会

| # | 机会 | 价值 | 实现时机 |
|---|------|------|---------|
| O1 | **Event Store 天然的成本分析能力** | M0 已有 append-only Event Store，per-task cost 聚合仅需 SQL `SUM()`，无额外基础设施 | Feature 002 |
| O2 | **alias 体系为智能路由预留扩展点** | 6 alias 体系比竞品更精细，未来可接入 RouteLLM 等智能路由 | M3+ |
| O3 | **Logfire 自动 instrument LLM 调用** | Blueprint 已选定 Logfire，它原生支持 LiteLLM/Pydantic AI 的 trace/cost 追踪，几乎零成本获得可观测性 | Feature 002 |
| O4 | **成本数据可驱动模型选型决策** | 积累真实成本数据后，可数据驱动地优化 alias -> model 映射 | M2+ |
| O5 | **EchoProvider 作为"离线模式"的产品化** | 竞品普遍没有优雅的离线模式；OctoAgent 可将 Echo/Mock 包装为"离线模式"产品特性 | Feature 002 |

---

## 7. 补充调研：模型网关方案对比矩阵

### 7.1 自托管 LLM 网关方案对比

| 维度 | LiteLLM Proxy | Portkey AI Gateway | LLM Gateway | OpenRouter |
|------|--------------|-------------------|-------------|-----------|
| **开源协议** | MIT | MIT | MIT | N/A（SaaS） |
| **自托管** | Docker（简单） | Docker/Node.js | Docker Compose | 仅 SaaS；社区有非官方 proxy |
| **支持 Provider 数** | 100+ | 250+ | 20+ | 200+（云端） |
| **cost 追踪粒度** | per-request（含 budget/virtual key） | per-request（analytics dashboard） | per-request（via API） | 无原生追踪 |
| **alias 路由** | model_group_alias 原生支持 | conditional routing | 统一 API | provider 选择 |
| **fallback 机制** | 通用/context window/content policy 三类 | fallback + retry(5x) + load balance | health-aware + key blacklist | key rotation |
| **Python 集成** | 原生 Python SDK + OpenAI 兼容 | SDK + LangChain/CrewAI 集成 | REST API | OpenAI 兼容 |
| **与 Pydantic AI 兼容** | 原生支持（LiteLLM 是 Pydantic AI 推荐后端） | 需要 OpenAI 兼容层 | 需要 OpenAI 兼容层 | OpenAI 兼容 |
| **运维复杂度** | 低（YAML 配置 + Docker） | 中（Node.js 运行时） | 低 | 零（SaaS） |
| **适合 OctoAgent** | 最佳 | 备选 | 不推荐（provider 少） | 不推荐（不可自托管） |

### 7.2 选型结论

**LiteLLM Proxy 是 OctoAgent 的最佳选择**，理由：

1. **Blueprint 已确定**：Blueprint 17 处提及 LiteLLM，是架构层面的既定选型
2. **Pydantic AI 原生支持**：Pydantic AI 文档推荐 LiteLLM 作为多 provider 后端
3. **Python 生态一致**：LiteLLM 是 Python 库，与 OctoAgent 技术栈完美对齐
4. **自托管简单**：Docker + YAML 配置，符合"零运维偏好"
5. **cost 追踪内置**：`completion_cost()` + `usage` 回调，无需手动维护定价表
6. **社区活跃**：GitHub 20k+ stars，持续更新，风险可控

---

## 附录 A: 术语映射

| 本报告术语 | Blueprint 术语 | 代码术语 |
|-----------|---------------|---------|
| 模型网关 | Provider Plane / LiteLLM Proxy | `packages/provider` |
| cheap 模型 | utility alias（router/extractor/summarizer） | `model_alias="cheap"` |
| main 模型 | primary alias（planner/executor） | `model_alias="main"` |
| 成本追踪 | 统一成本治理（SS6） | `CostTracker` |
| 降级 | Degrade Gracefully（C6） | `FallbackManager` |
| 事件成本 | per-task cost/tokens（SS6） | `ModelCallCompletedPayload.cost_usd` |

## 附录 B: 调研信息源

- Web 搜索（Perplexity research 模式）：Agent 框架竞品分析、LLM 网关对比、成本治理最佳实践
- 本地代码库：M0 的 `llm_service.py`、`payloads.py`、Blueprint `docs/blueprint.md`
- 前序制品：`constitution.md`、`m1-feature-split.md`

---

> **调研模式声明**: 本报告基于 Perplexity 实时搜索（2026-02-28）+ 本地代码库分析 + Blueprint/Constitution 约束生成。竞品功能描述基于公开文档和社区信息，可能存在时效性偏差。标注 `[推断]` 的内容为基于现有信息的合理推测。
