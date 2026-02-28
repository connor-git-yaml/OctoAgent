# 产研汇总: LiteLLM Proxy 集成 + 成本治理

**特性分支**: `feat/002-integrate-litellm-provider`
**汇总日期**: 2026-02-28
**输入**: [product-research.md](product-research.md) + [tech-research.md](tech-research.md)
**执行者**: 主编排器（非子代理）

## 1. 产品×技术交叉分析矩阵

| MVP 功能 | 产品优先级 | 技术可行性 | 实现复杂度 | 综合评分 | 建议 |
|---------|-----------|-----------|-----------|---------|------|
| LiteLLMClient（SDK 直连 Proxy） | P0 — 全局基础，Feature 003-005 硬依赖 | 高 — `acompletion()` 原生 async，M0 LLMProvider ABC 可平滑演进 | 低 — Adapter 模式封装 SDK，~200 行核心代码 | ⭐⭐⭐ | 纳入 MVP |
| AliasRegistry（语义 alias + 运行时 group 映射） | P0 — 成本分流的核心机制，行业共识 | 高 — Proxy `model_list` 支持运行时 group，应用层 Registry 维护语义映射 | 低 — 静态配置 + 查询接口 | ⭐⭐⭐ | 纳入 MVP |
| CostTracker（usage -> cost 计算） | P0 — 成本可见性 Layer 1，Feature 005 Budget 的数据源 | 高 — 公开 API 主路径 + 私有字段兼容兜底 | 低 — 无状态工具类，计算 + 解析 | ⭐⭐⭐ | 纳入 MVP |
| FallbackManager（Proxy 不可达降级 Echo） | P0 — Constitution C6 强制要求 | 高 — 两级 fallback 链路短且清晰 | 低 — Chain of Responsibility 简化版 | ⭐⭐⭐ | 纳入 MVP |
| ModelCallCompletedPayload 扩展 | P0 — Event 数据完整性，C2/C8 要求 | 高 — 新增 Optional 字段，向后兼容 | 极低 — 纯数据模型变更 | ⭐⭐⭐ | 纳入 MVP |
| LLMService 改造（Echo -> LiteLLM） | P0 — M1 智能闭环的前提 | 高 — M0 预留 `register()` 扩展点 | 低 — 初始化逻辑切换，路由层无改动 | ⭐⭐⭐ | 纳入 MVP |
| /ready?profile=llm 健康检查 | P1 — 可观测性 C8 要求 | 高 — Proxy `/health/liveliness` 直连 | 极低 — `llm/full` 做真实检查，`core` 维持 `"skipped"` | ⭐⭐⭐ | 纳入 MVP |
| LiteLLM Proxy docker-compose | P2 — 降低上手门槛（<15min 首次体验） | 高 — 官方 Docker 镜像成熟 | 极低 — 配置文件 | ⭐⭐ | 纳入 MVP（Nice-to-have） |
| Streaming 支持 | P3 — M1 Skill Runner 不依赖 | 高 — `acompletion(stream=True)` 原生 | 中 — SSE chunk 处理 + stream_chunk_builder | ⭐⭐ | 推迟至 M1.5+ |
| 运行时动态切换 alias | P3 — 重启服务切换即可 | 中 — 需 Proxy API + 热加载 | 中 | ⭐ | 推迟至 M2+ |
| per-task 预算限制 + 自动降级 | P2 — 属于 Policy Engine 领域 | 高 — CostTracker 提供数据基础 | 高 — 需策略引擎集成 | ⭐ | Feature 005 |

**评分说明**:
- ⭐⭐⭐: 高优先 + 高可行 + 低复杂度 → 纳入 MVP
- ⭐⭐: 中等匹配 → 视资源纳入 MVP 或推迟
- ⭐: 低匹配 → 推迟

## 2. 可行性评估

### 技术可行性

**总体评级：高**

LiteLLM SDK v1.81.16 与项目技术栈（Python 3.12 + FastAPI + Pydantic 2.x）完全兼容。M0 已预留良好的抽象层（`LLMProvider` ABC + `LLMService` alias 路由 + `ModelCallCompletedPayload` 事件结构），改造影响面评估为"全低"——6 个需修改文件均为新增代码或新增 Optional 字段，无重写。

技术调研验证了 3 个架构方案，**方案 A（SDK 直连 Proxy）** 在 Blueprint 对齐度、开发效率、Constitution 合规性方面全面领先，产品调研中的竞品分析也印证了 LiteLLM Proxy 作为自托管模型网关的最佳选择地位（MIT 协议、Python 原生、20k+ stars、Pydantic AI 官方推荐后端）。

### 资源评估

- **预估工作量**: 3-4 天（~20-25 tasks），与 m1-feature-split.md 估算一致
- **关键技能需求**: Python async/await、LiteLLM SDK、Docker、Pydantic BaseModel
- **外部依赖**: `litellm>=1.80,<2.0`（唯一新增核心依赖，MIT 许可证，与现有依赖无冲突）

### 约束与限制

- **LiteLLM Proxy 部署前提**: 需要至少 1 个 LLM provider 的 API key（OpenAI 或 Anthropic），否则仅能使用 Echo 模式
- **litellm 包体较大**: ~50MB+（含 openai/tiktoken 等依赖链），但仅安装 SDK 核心（不装 proxy extras）
- **MVP 不含 streaming**: `acompletion(stream=True)` 虽然技术可行，但 M1 Skill Runner 不依赖流式输出，推迟可降低 Feature 002 复杂度
- **成本数据依赖 LiteLLM pricing 数据库**: 新模型发布后 pricing 可能滞后更新；需以公开 API 为主，私有字段仅兼容兜底

## 3. 风险评估

### 综合风险矩阵

| # | 风险 | 来源 | 概率 | 影响 | 缓解策略 | 状态 |
|---|------|------|------|------|---------|------|
| 1 | LiteLLM Proxy 部署阻塞（配置复杂度超预期、API key 不可用） | 技术+产品 | 低 | 高 | 提供 docker-compose + 示例 config；EchoProvider 保底；提前准备 ≥2 provider API key | 待监控 |
| 2 | LiteLLM `usage.cost` 字段不可靠（部分 provider 不返回或数据不准） | 技术 | 中 | 中 | CostTracker 双通道：公开 API 为主，私有字段仅兼容兜底；记录 raw usage 以便事后修正 | 待监控 |
| 3 | Pydantic AI + LiteLLM Proxy 兼容性（structured output / function calling） | 技术 | 低 | 中 | Feature 002 不依赖 structured output（那是 Feature 004 的事）；技术调研已确认基础 chat completion 完全兼容 | 已缓解 |
| 4 | LiteLLM SDK 版本更新导致 breaking change | 技术 | 中 | 高 | 锁定版本 `>=1.80,<2.0`；Adapter 模式隔离 SDK 类型；CI 定期兼容性测试 | 待监控 |
| 5 | Event payload 扩展的向后兼容 | 技术 | 低 | 低 | 所有新增字段均 Optional + 默认值，M0 旧事件可正常反序列化 | 已缓解 |
| 6 | EchoProvider 降级体验差（用户从真实 LLM 降到 Echo 时感知落差大） | 产品 | 低 | 低 | 降级时 event payload 写入清晰的降级原因；个人使用场景可接受短暂降级 | 已接受 |
| 7 | async event loop 阻塞（litellm SDK 内部同步调用） | 技术 | 低 | 高 | 使用 `acompletion()`（原生 async）；设置 `request_timeout`；监控 event loop lag | 待监控 |

### 风险分布

- **产品风险**: 2 项（高:0 中:1 低:1）
- **技术风险**: 5 项（高:0 中:2 低:3）
- **综合评估**: 无高概率高影响风险，整体风险可控

## 4. 最终推荐方案

### 推荐架构

**方案 A：SDK 直连 Proxy（OpenAI-compatible 模式）**

```
OctoAgent Gateway / Workers
  └── packages/provider
        ├── LiteLLMClient
        │     └── litellm.acompletion(model=alias, api_base=proxy_url)
        ├── AliasRegistry (语义 alias → runtime group/category 映射)
        ├── CostTracker (response → cost_usd 计算)
        └── FallbackManager (LiteLLM Proxy ↔ EchoProvider)
              └── LiteLLM Proxy (Docker)
                    ├── model_list: cheap/main/fallback 运行时 group
                    ├── semantic map: router/extractor/summarizer→cheap, planner/executor→main
                    ├── router_settings: fallback chain
                    └── API keys 统一管理 (C5 合规)
```

产品调研确认 LiteLLM Proxy 是自托管场景最佳选择（100+ provider、内置 cost 追踪、alias 路由原生支持、Python 生态一致）。技术调研确认方案 A 在开发效率、Blueprint 对齐、Constitution 合规三个维度全面领先方案 B（HTTP 直连）和方案 C（无 Proxy）。

### 推荐技术栈

| 类别 | 选择 | 理由 |
|------|------|------|
| LLM SDK | litellm>=1.80,<2.0 | 唯一新增依赖；产品调研确认行业标配、技术调研确认完全兼容 |
| Proxy 部署 | Docker（官方镜像） | 零运维偏好 + 单容器足够 + <5s 启动 |
| 数据模型 | Pydantic BaseModel | 与项目统一；ModelCallResult 替代 M0 的 dataclass LLMResponse |
| 健康检查 | httpx（已有依赖） | 调用 Proxy `/health/liveliness` |
| 成本计算 | litellm.completion_cost() | 内置 pricing 数据库，免维护定价表 |
| 降级策略 | 两级 Chain of Responsibility | Proxy 内置 model fallback + 应用层 Proxy fallback → Echo |

### 推荐实施路径

1. **Phase 1 (MVP/Feature 002)**: packages/provider 4 组件 + LLMService 改造 + Event payload 扩展 + /ready 健康检查 + docker-compose 配置
2. **Phase 2 (Feature 003-004)**: ToolBroker 使用 cheap alias 做输出压缩；Skill Runner 通过 LiteLLMClient 调用 main alias
3. **Phase 3 (Feature 005+)**: Policy Engine 消费 CostTracker 数据做预算执行；前端 cost dashboard

## 5. MVP 范围界定

### 最终 MVP 范围

**纳入**:
- **LiteLLMClient**: SDK 直连 Proxy 封装 — 产品侧是全局基础（Feature 003-005 硬依赖），技术侧是 `acompletion()` 简洁封装（~200 行）
- **AliasRegistry**: 语义 alias + 运行时 group 映射管理 — 产品侧 cheap/main 分流是行业共识（节省 60-85% 成本），技术侧仅需静态配置 + Registry 模式
- **CostTracker**: usage -> cost_usd 实时计算 — 产品侧是成本可见性 Layer 1 核心，技术侧 `completion_cost()` 开箱即用
- **FallbackManager**: Proxy 不可达自动降级到 EchoProvider — 产品侧对齐 Constitution C6 要求，技术侧两级 fallback 链路短
- **ModelCallCompletedPayload 扩展**: 增加 cost_usd/cost_unavailable/provider/model_name/is_fallback — 产品侧是 Event 数据完整性（C2/C8），技术侧纯 Optional 字段新增
- **LLMService 改造**: 默认 provider 从 Echo 切换到 LiteLLM + FallbackManager — 产品侧是 M1 智能闭环前提，技术侧利用 M0 预留的 `register()` 扩展点
- **/ready?profile=llm**: LiteLLM Proxy 健康检查 — 产品侧可观测性要求，技术侧在 `llm/full` 做真实检查，`core` 维持 `"skipped"`
- **docker-compose.litellm.yml** (Nice-to-have): 即开即用的 Proxy 部署 — 产品侧将"首次体验"控制在 <15 分钟

**排除（明确不在 MVP）**:
- **Streaming 支持**: 技术可行但 M1 Skill Runner 不依赖 — 推迟可降低 Feature 002 复杂度
- **运行时动态切换 alias**: 重启服务即可切换 — 个人使用场景低频操作
- **per-task 预算限制**: 属于 Policy Engine（Feature 005）领域 — CostTracker 已预留 `get_task_total_cost()` 接口
- **成本告警**: 需要 Policy Engine + 通知渠道 — 超出 Feature 002 边界
- **前端 cost dashboard**: 不阻塞核心功能 — Event Store SQL 聚合已足够 MVP
- **智能路由（按任务复杂度选模型）**: 需要 Orchestrator 路由能力 — M3+ 规划
- **熔断器（circuit breaker）**: Blueprint 已定义但 M1 不需要 — 两级 fallback 已足够

### MVP 成功标准

- **SC-1**: `acompletion()` 通过 Proxy 成功调用至少 1 个 provider（OpenAI 或 Anthropic），返回真实 LLM 响应
- **SC-2**: MODEL_CALL_COMPLETED 事件包含完整的 `cost_usd`, `token_usage`, `provider`, `model_name` 字段；数据非负且成本不可用时有明确标记
- **SC-3**: 语义 alias 按映射正确路由到运行时 group（cheap/main/fallback），并可通过 `model_name` 字段验证差异
- **SC-4**: LiteLLM Proxy 不可达时，FallbackManager 自动降级到 EchoProvider，`is_fallback=True`
- **SC-5**: `/ready?profile=llm` 返回 Proxy 真实健康状态
- **SC-6**: 所有 M0 已有测试继续通过（向后兼容验证）
- **SC-7**: 新增 packages/provider 单元测试覆盖率 ≥80%

## 6. 结论

### 综合判断

Feature 002（LiteLLM Proxy 集成 + 成本治理）的产品需求清晰（行业共识的 cheap/main 分流 + per-task 成本可见性）、技术方案成熟（LiteLLM SDK v1.81 + Proxy Docker 部署 + M0 预留扩展点），且 Constitution 合规风险已通过方案 A 的设计充分覆盖。推荐按当前定义的 MVP 范围（7 个 Must-Have + 1 个 Nice-to-have）直接进入需求规范阶段，不需要扩大或缩小范围。技术风险整体可控（无高概率高影响项），最大关注点是 LiteLLM SDK 版本稳定性和 cost 数据可靠性，已有明确缓解策略。

### 置信度

| 维度 | 置信度 | 说明 |
|------|--------|------|
| 产品方向 | 高 | Blueprint 已确定 LiteLLM Proxy 选型；竞品分析印证 cheap/main 分流和 per-task 成本追踪是行业标配；5 个差异化机会明确 |
| 技术方案 | 高 | 方案 A（SDK 直连 Proxy）在 3 方案对比中全面领先；SDK API 已通过代码分析验证；M0 预留扩展点确认改造成本低 |
| MVP 范围 | 高 | 7 个 Must-Have 均有明确的"前后依赖理由"或"Constitution 合规理由"；排除项边界清晰无争议 |

### 后续行动建议

- 确认推荐方案后，进入需求规范阶段（specify）
- Constitution WARN-1（Secrets 注入路径）需在 spec 中明确 API key 的注入方式（环境变量 → Proxy 容器，不进 OctoAgent 应用层）
- Constitution WARN-2（ModelCallResult 数据边界）需在 spec 中明确 `response_summary` 字段的内容截断策略（避免完整 LLM 响应进入 Event payload）
