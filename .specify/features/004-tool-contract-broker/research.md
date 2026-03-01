# 技术决策研究: Feature 004 — Tool Contract + ToolBroker

**Feature Branch**: `feat/004-tool-contract-broker`
**日期**: 2026-03-01
**输入**: spec.md, research/tech-research.md, constitution.md

---

## Decision 1: Schema Reflection 方案选型

**决策**: 复用 Pydantic AI `_function_schema.function_schema()` + Adapter 隔离层

**理由**:
1. 项目已将 Pydantic AI 作为核心 Agent 框架（Blueprint 明确选型），`function_schema()` 从 `inspect.signature` + `get_type_hints` + docstring 一次性生成 JSON Schema + Validator，天然满足 Constitution C3（code = schema 单一事实源）
2. 代码量减少约 60%（对比自研方案），且经过 Pydantic AI 社区大量实战验证
3. Feature 005 Skill Runner 同样依赖 `pydantic-ai-slim`，提前引入减少后续集成成本
4. Pydantic AI 原生 Logfire instrument，Hook 层 span/trace 可直接对接（Constitution C8）

**替代方案**:
- **方案 B: 自研轻量方案** — 使用 `inspect.signature` + `typing.get_type_hints` + `pydantic.TypeAdapter.json_schema()` 逐参数组装。优点：无 private API 依赖。缺点：代码量大、缺少社区验证、后续与 Pydantic AI Agent 集成需要额外适配层
- **降级路径**: 若 `_function_schema` 在 Pydantic AI 升级后不兼容，切换为 `pydantic.TypeAdapter.json_schema()` 逐参数生成，改动范围限制在 `schema.py` 单文件（Adapter 模式隔离）

**风险缓解**:
- 版本锁定 `pydantic-ai-slim>=0.0.40`
- Adapter 模式隔离调用点（`schema.py` 单文件）
- CI 中添加 Pydantic AI 版本兼容性监控

---

## Decision 2: ToolBroker 架构模式

**决策**: Protocol-based Mediator + Hook Chain（Chain of Responsibility）

**理由**:
1. **Mediator 模式**: 所有工具调用必须经过 Broker，确保 hook 链路完整执行——直接对齐 Constitution C4（Two-Phase 强制）。Broker 在 execute 前可插入 PolicyCheckpoint gate
2. **Protocol-based**: 使用 Python `typing.Protocol` 定义 `ToolBrokerProtocol`，Feature 005/006 可基于 Protocol 编写 mock 实现，三轨并行开发无阻塞
3. **Hook Chain**: Chain of Responsibility 模式，before/after hook 按优先级链式执行。支持可插拔的横切关注点（observability、policy gate、output 裁切），不同 hook 互不干扰

**替代方案**:
- **Registry-based Broker（无 Hook）**: 仅提供注册/发现/执行，横切关注点硬编码在 Broker 核心逻辑中。缺点：违反单一职责、Feature 006 PolicyCheckpoint 接入需修改 Broker 代码
- **Event-driven Broker**: 工具执行通过事件总线广播，hook 作为事件订阅者。缺点：引入额外复杂度，M1 阶段不需要异步解耦

---

## Decision 3: 大输出裁切实现方式

**决策**: LargeOutputHandler 作为 after hook 实现，阈值 500 字符，零侵入

**理由**:
1. **零侵入**: 工具开发者无需在工具代码中处理裁切逻辑，ToolBroker 后处理自动运行（对齐 Agent Zero `_90_save_tool_call_file.py` 模式）
2. **after hook 实现**: 裁切逻辑与 Broker 核心解耦，可独立配置/禁用/替换
3. **500 字符阈值**: 用户已确认采用 Agent Zero 验证过的 500 字符阈值（CLR-004），激进裁切有利于保持 LLM 上下文精简（Constitution C11）
4. **可配置**: FR-017 要求全局默认 + 工具级自定义阈值，after hook 实现天然支持

**替代方案**:
- **Broker 核心内置裁切**: 裁切逻辑硬编码在 execute() 方法中。缺点：违反单一职责，未来难以扩展
- **4000 字符阈值**: Blueprint §8.5.5 原始建议。缺点：已被用户在 CLR-004 中否决，500 字符更精简

**降级策略**: ArtifactStore 不可用时保留原始输出（不裁切），记录降级警告事件（FR-018, Constitution C6）

---

## Decision 4: Hook fail_mode 策略

**决策**: 每个 Hook 声明 `fail_mode`（"closed" 或 "open"），按类型区分

**理由**:
1. **安全类 hook**（如 PolicyCheckpoint）声明 `fail_mode="closed"`，超时/异常即拒绝执行，确保不可逆操作不被放行（Constitution C4）
2. **可观测类 hook**（如 ObservabilityHook）声明 `fail_mode="open"`，超时/异常仅记录警告并继续执行（Constitution C6）
3. 兼顾安全性和可用性——用户在 CLR-005 中已确认此策略

**替代方案**:
- **全局 fail-open**: 所有 hook 异常均继续执行。缺点：安全类 hook 被绕过时违反 C4
- **全局 fail-closed**: 所有 hook 异常均拒绝执行。缺点：可观测类 hook 故障导致工具系统不可用，违反 C6

---

## Decision 5: irreversible 工具在无 PolicyCheckpoint 时的行为

**决策**: 强制拒绝执行（FR-010a）

**理由**:
1. Constitution C4 要求"不可逆操作必须拆成 Plan -> Gate -> Execute"，C7 要求"默认启用门禁（safe by default）"
2. 若没有任何注册的 PolicyCheckpoint hook，irreversible 工具的 Gate 环节缺失，直接执行违反 Two-Phase 原则
3. 返回明确的拒绝原因（"no policy checkpoint registered for irreversible tool"），引导开发者注册 PolicyCheckpoint
4. `side_effect_level=none` 和 `reversible` 的工具不受此限制（它们不需要 Two-Phase）

**替代方案**:
- **默认放行 + 警告**: 记录警告但允许执行。缺点：直接违反 Constitution C4 和 C7
- **所有工具都需要 PolicyCheckpoint**: 过于严格，none/reversible 工具无需门禁，影响开发效率

---

## Decision 6: 新包 packages/tooling 的依赖策略

**决策**: `octoagent-tooling` 依赖 `octoagent-core` + `pydantic-ai-slim`

**理由**:
1. 依赖 `octoagent-core` 获取 Event/Artifact/EventType 等共享模型和 Store Protocol
2. 依赖 `pydantic-ai-slim`（无额外 extras）获取 `_function_schema.function_schema()` 用于 Schema Reflection
3. 不依赖 `octoagent-provider`——tooling 包是独立的工具治理基础设施，不需要 LLM 调用能力
4. `pydantic-ai-slim` 是 Feature 005 的共享依赖，提前引入减少重复

**替代方案**:
- **将 tooling 代码放入 core 包**: 缺点：core 包应保持精简（仅 domain models + stores），工具治理属于独立技术域
- **不引入 pydantic-ai-slim**: 缺点：需自研 Schema Reflection（Decision 1 已否决）

---

## Decision 7: EventType 枚举扩展方式

**决策**: 直接在 core 包的 `EventType` 枚举中添加 TOOL_CALL_STARTED / TOOL_CALL_COMPLETED / TOOL_CALL_FAILED

**理由**:
1. 与现有 MODEL_CALL_STARTED / COMPLETED / FAILED 模式保持一致
2. 向前兼容的枚举扩展——新增枚举值不影响现有事件消费者
3. core 包作为共享基础设施包，承载所有事件类型定义是其设计职责
4. 同时新增对应的 Payload 类型（ToolCallStartedPayload / ToolCallCompletedPayload / ToolCallFailedPayload）

**替代方案**:
- **在 tooling 包中定义独立的事件类型**: 缺点：EventStore 消费端需要同时导入 core + tooling 的事件类型，增加耦合
- **使用字符串常量（非枚举）**: 缺点：无类型安全，不符合项目使用 StrEnum 的惯例

---

## Decision 8: 装饰器设计 — @tool_contract

**决策**: 使用 `@tool_contract()` 装饰器附加元数据，Schema Reflection 时自动提取

**理由**:
1. 声明性标注——工具元数据与函数实现紧密绑定，代码即文档（Constitution C3）
2. `side_effect_level` 作为必填参数（无默认值），强制声明（FR-002）
3. 装饰器将元数据存储在函数对象的 `_tool_meta` 属性上，Schema Reflection 时提取合并
4. 对齐 Pydantic AI 的 `@agent.tool` 装饰器风格，团队学习成本低

**替代方案**:
- **基类继承**: 工具定义为类继承 `BaseTool`（Agent Zero 模式）。缺点：函数式工具更轻量，M1 阶段不需要类级别的组织
- **独立 manifest 文件**: 工具元数据写在 YAML/JSON 文件中。缺点：违反 C3（双事实源），需手动同步

---

## Decision 9: 敏感数据脱敏策略

**决策**: ToolBroker 在事件生成前统一执行脱敏，工具开发者无需在工具代码中处理

**理由**:
1. Constitution C8 要求"敏感原文不得直接写入 Event payload"
2. 集中处理避免遗漏——每个工具单独处理脱敏容易出现不一致
3. 脱敏规则（FR-015）：(a) `$HOME` -> `~`，(b) 环境变量值 -> `[ENV:VAR_NAME]`，(c) 凭证模式 -> `[REDACTED]`

**替代方案**:
- **工具级脱敏**: 每个工具自行处理。缺点：分散、易遗漏、增加工具开发者负担
- **Event Store 层脱敏**: 在 EventStore.append_event() 时处理。缺点：EventStore 不应承载业务逻辑
