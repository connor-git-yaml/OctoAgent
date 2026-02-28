# Feature 002 技术决策研究

**特性**: 002-integrate-litellm-provider
**日期**: 2026-02-28
**输入**: spec.md + research-synthesis.md + tech-research.md + constitution.md
**阶段**: Phase 0 -- 技术决策研究

---

## 1. 决策总览

本文档记录 Feature 002 技术规划阶段的所有关键技术决策，每个决策包含结论、理由和被否决的替代方案。

---

## 2. 技术决策清单

### TD-1: LLM SDK 集成方式

**决策**: 使用 LiteLLM SDK `acompletion()` 通过 Proxy OpenAI-compatible endpoint 调用

**理由**:
1. `acompletion()` 原生 async，与 FastAPI event loop 零摩擦
2. `completion_cost()` 内置 pricing 数据库，免维护定价表
3. SDK 自动处理 OpenAI 兼容格式转换，减少样板代码
4. 完全对齐 Blueprint SS8.9（LiteLLM Proxy 作为统一模型出口）

**替代方案**:
| 方案 | 否决理由 |
|------|---------|
| httpx 直连 Proxy（方案 B） | 需手动实现成本计算、streaming、错误分类；开发量显著增加 |
| SDK 直连无 Proxy（方案 C） | 违反 Blueprint SS8.9；密钥管理分散，违反 Constitution C5 |

---

### TD-2: LLMResponse 数据模型演进

**决策**: `ModelCallResult(BaseModel)` 直接替换 M0 的 `LLMResponse(dataclass)`

**理由**:
1. `ModelCallResult` 是 `LLMResponse` 的严格超集，无信息丢失
2. FallbackManager 需要统一的返回类型（含 `is_fallback`、`cost_usd` 等标记）
3. Pydantic BaseModel 对齐项目全局数据模型规范
4. 定义在 `packages/provider` 包中，供 gateway 和未来 workers 共享

**替代方案**:
| 方案 | 否决理由 |
|------|---------|
| 保留 LLMResponse + 新增 ModelCallResult | 两种返回类型并存增加维护复杂度；FallbackManager 接口分叉 |
| 在 LLMResponse 上扩展字段 | dataclass 不支持 Pydantic 验证；与项目规范不一致 |

---

### TD-3: Alias 双层映射架构

**决策**: AliasRegistry 维护 6 个语义 alias -> 3 个 category -> 3 个运行时 group 的双层映射

**理由**:
1. 语义 alias（router/extractor/planner/executor/summarizer/fallback）表达业务意图，后续 Feature 003-005 直接按意图调用
2. category 维度（cheap/main/fallback）用于成本归因和预算策略
3. runtime_group 维度（cheap/main/fallback）对应 Proxy `model_list` 中的 `model_name`
4. MVP 阶段 category 与 runtime_group 一一对齐，但架构上保留独立演进能力

**映射表**:
| 语义 alias | category | runtime_group | 示例模型 |
|-----------|----------|---------------|---------|
| router | cheap | cheap | gpt-4o-mini |
| extractor | cheap | cheap | gpt-4o-mini |
| summarizer | cheap | cheap | gpt-4o-mini |
| planner | main | main | gpt-4o |
| executor | main | main | gpt-4o |
| fallback | fallback | fallback | claude-3.5-haiku |

**替代方案**:
| 方案 | 否决理由 |
|------|---------|
| 仅 3 个运行时 group，不要语义 alias | Feature 003 的 summarizer 和 Feature 004 的 planner 需要语义粒度的调用意图 |
| 每个语义 alias 独立配置模型 | MVP 阶段过度设计；6 个独立模型配置增加运维复杂度 |

---

### TD-4: CostTracker 双通道策略

**决策**: 公开 API（`response.usage` + `completion_cost()`）为主，私有字段（`_hidden_params`）仅兼容兜底

**理由**:
1. `completion_cost(completion_response=response)` 是 LiteLLM 官方推荐的公开 API
2. 私有字段 `_hidden_params["response_cost"]` 是 SDK 内部实现，不保证跨版本稳定
3. 双通道均失败时记录 `cost_usd=0.0` + `cost_unavailable=true`，不中断正常流程
4. 对齐 Constitution C8（可观测性），成本数据不可用时有明确标记

**实现优先级**:
```
通道 1（主）: completion_cost(completion_response=response)
通道 2（兜底）: response._hidden_params.get("response_cost", 0.0)
全失败: cost_usd=0.0, cost_unavailable=True
```

**替代方案**:
| 方案 | 否决理由 |
|------|---------|
| 仅依赖公开 API | 部分新模型可能暂时不在 pricing 数据库中，缺少兜底 |
| 仅依赖私有字段 | 私有 API 不保证跨版本稳定，风险过高 |
| 自维护 pricing 表 | 增加维护成本，LiteLLM pricing DB 已覆盖主流模型 |

---

### TD-5: FallbackManager 降级与恢复策略

**决策**: Lazy probe -- 每次 LLM 调用时先尝试 Proxy，失败则 fallback 到 EchoProvider，不维护显式降级状态

**理由**:
1. 单用户场景调用频率低，lazy probe 开销可忽略（每次额外 1-5ms 连接检测）
2. 无需后台定时任务和状态机的额外复杂度
3. Proxy 恢复后首次调用即自动感知，恢复延迟 = 下一次用户请求间隔
4. 对齐 Constitution C6（Degrade Gracefully）-- 降级无感，恢复自动

**降级链路**:
```
请求 -> LiteLLMClient.complete() -> 成功 -> 返回 ModelCallResult(is_fallback=False)
                                   -> 失败 -> EchoMessageAdapter.complete() -> 返回 ModelCallResult(is_fallback=True)
```

**替代方案**:
| 方案 | 否决理由 |
|------|---------|
| Active probe（后台定时探测） | 引入后台任务 + 状态标记的复杂度；单用户场景收益不大 |
| Circuit breaker（熔断器） | Blueprint 已定义但 M1 不需要；两级 fallback 已足够 |

---

### TD-6: EchoProvider 适配层（EchoMessageAdapter）

**决策**: 新增 `EchoMessageAdapter` 适配层，让 EchoProvider 消费统一的 `messages: list[dict]` 接口

**理由**:
1. FallbackManager 需要统一的调用契约（`complete(messages, model_alias)` -> `ModelCallResult`）
2. EchoProvider 的核心逻辑（提取 prompt、生成回声）不变，仅包装接口
3. 适配层隔离了 M0 遗留接口和 M1 新接口，改动边界可控
4. 测试中可直接用 EchoMessageAdapter 替代 LiteLLMClient，无需修改 FallbackManager

**替代方案**:
| 方案 | 否决理由 |
|------|---------|
| 修改 EchoProvider.call() 签名为 messages | 破坏 M0 测试；EchoProvider 在 M0 中还被直接引用 |
| FallbackManager 内部做 prompt/messages 双轨 | 双轨接口分叉，增加 FallbackManager 复杂度 |

---

### TD-7: 健康检查 Profile 机制

**决策**: `/ready` 端点支持 `profile` 查询参数，`llm`/`full` 做真实 Proxy 检查，`core`/无参数维持 `"skipped"`

**理由**:
1. M0 已预留 `litellm_proxy: "skipped"` 占位，平滑扩展
2. Echo 模式下不需要 Proxy 检查，`core` profile 维持现有行为
3. `llm`/`full` profile 触发真实的 `/health/liveliness` 检查
4. Proxy 不可达不影响 `core` profile 的整体 ready 判定

**替代方案**:
| 方案 | 否决理由 |
|------|---------|
| 始终检查 Proxy | Echo 模式下 Proxy 不存在，会导致误报 not_ready |
| 新增独立的 /ready/llm 端点 | 违反现有 API 设计惯例；增加端点数量 |

---

### TD-8: token_usage 字段命名标准化

**决策**: Feature 002 起统一使用 `prompt_tokens`/`completion_tokens`/`total_tokens`（OpenAI API 标准）

**理由**:
1. LiteLLM SDK 返回的 `usage` 对象原生使用此命名
2. 与 OpenAI API 标准一致，减少后续 CostTracker 和 Policy Engine 的 key 映射
3. M0 旧事件的 `token_usage` 是 `dict[str, int]` 类型，key 差异不影响 Pydantic 反序列化
4. 新旧数据在 Event Store 中共存无冲突

**替代方案**:
| 方案 | 否决理由 |
|------|---------|
| 保留 M0 的 prompt/completion/total 命名 | 需要在 CostTracker 中做 key 映射；与 LiteLLM SDK 不一致 |
| 同时支持新旧命名 | 增加 TokenUsage 模型复杂度；双命名维护成本高 |

---

### TD-9: packages/provider 包定位

**决策**: `packages/provider` 作为独立 workspace 包，包含 4 个核心组件 + 配置 + 异常体系

**包结构**:
```
packages/provider/
  pyproject.toml
  src/octoagent/provider/
    __init__.py            # 公开接口导出
    client.py              # LiteLLMClient
    alias.py               # AliasRegistry + AliasConfig
    cost.py                # CostTracker + TokenUsage
    fallback.py            # FallbackManager
    models.py              # ModelCallResult
    config.py              # ProviderConfig（环境变量）
    exceptions.py          # ProviderError 异常体系
    echo_adapter.py        # EchoMessageAdapter
```

**理由**:
1. 对齐 Blueprint SS9.10 的包职责定义
2. 独立包可被 gateway、workers 等多个 app 依赖
3. 与 `packages/core` 平级，通过 uv workspace 管理
4. 依赖 `packages/core` 共享 Event payload 定义

**替代方案**:
| 方案 | 否决理由 |
|------|---------|
| 放在 gateway 内部 | 未来 workers 需要相同的 LLM 客户端，无法跨 app 共享 |
| 放在 core 包中 | core 包聚焦 domain models 和 event store，provider 是基础设施层 |

---

### TD-10: LLM 调用超时策略

**决策**: 默认 30 秒超时，可通过 `OCTOAGENT_LLM_TIMEOUT_S` 环境变量覆盖

**理由**:
1. 30 秒是 LiteLLM SDK 的默认 `request_timeout` 值，行业惯例
2. GPT-4 级模型通常 5-15 秒内响应，30 秒留有充分余量
3. 与 M0 不设超时相比，增加超时保护更安全
4. 超时后触发 FallbackManager 降级路径

**替代方案**:
| 方案 | 否决理由 |
|------|---------|
| 不设超时 | 模型调用可能无限挂起，阻塞事件循环 |
| 60 秒超时 | 对于单用户交互场景，60 秒等待体验过差 |

---

## 3. 未决事项

本轮技术决策中无 `NEEDS CLARIFICATION` 项。所有关键技术选型均已在调研阶段（tech-research.md）和需求规范阶段（spec.md AC-1 ~ AC-7）中明确解决。
