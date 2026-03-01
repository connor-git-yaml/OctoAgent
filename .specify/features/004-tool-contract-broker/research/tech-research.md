# 技术调研报告: Feature 004 — Tool Contract + ToolBroker

**特性分支**: `feat/004-tool-contract-broker`
**调研日期**: 2026-03-01
**调研模式**: 在线（Perplexity web_search）
**产品调研基础**: [独立模式] 本次技术调研未参考产品调研结论，直接基于需求描述和代码上下文执行

## 1. 调研目标

**核心问题**:
- 问题 1: 如何设计 ToolMeta 元数据声明，使其既满足 Constitution 原则 3（Tools are Contracts）又保持对 Pydantic AI 生态的兼容性？
- 问题 2: Schema Reflection 应该基于 Pydantic AI 的 `_function_schema` 方案还是自研轻量方案？
- 问题 3: ToolBroker 的架构模式如何选型——Protocol-based Mediator vs. Registry-based Broker？
- 问题 4: Large Output Handling 的裁切策略如何做到零侵入？
- 问题 5: before/after hook 扩展点如何与 Logfire/OpenTelemetry observability 原生集成？

**需求 MVP 范围**:
- Must-have 1: ToolMeta 声明模型（name, description, schema, side_effect_level, tool_profile, tool_group）
- Must-have 2: Schema Reflection — 从函数签名 + type hints 自动生成 JSON Schema
- Must-have 3: ToolBroker — 工具发现、注册、执行中介（含 before/after hook）
- Must-have 4: Large Output Handling — >500 char 自动裁切为 artifact 引用
- Must-have 5: 至少 2 个示例工具（不同 side_effect_level）
- Must-have 6: 接口契约文档（ToolBrokerProtocol + ToolMeta + ToolResult + PolicyCheckpoint Protocol）

## 2. 架构方案对比

### 2.1 参考系统分析

在设计方案前，先提炼三个参考系统的关键设计决策：

| 维度 | Agent Zero | OpenClaw | Pydantic AI |
|------|-----------|----------|-------------|
| 工具声明 | 继承 `Tool` 基类 + 手写 description，无结构化元数据 | `AnyAgentTool` + `PluginToolMeta`（WeakMap 元数据），CORE_TOOL_GROUPS 分组 + 4 级 Profile | `Tool` dataclass + `ToolDefinition`（name, description, parameters_json_schema, strict, kind） |
| Schema 生成 | 无（依赖 LLM prompt 中手写描述） | TypeScript 泛型 `AgentTool<I,O>` 编译期保证 | `_function_schema.function_schema()` 从 `inspect.signature` + `get_type_hints` + docstring 自动生成 |
| 执行中介 | 直接调用 `tool.execute()`，无中央 broker | `resolvePluginTools()` 发现 + factory 模式创建 | Agent 内部 toolset 管理，`prepare_tool_def` 动态调整 |
| Hook 机制 | `before_execution` / `after_execution` 方法（Tool 基类） | 无显式 hook，依赖插件生命周期 | `prepare` 回调 + `requires_approval`（DeferredToolRequests） |
| 大输出处理 | `_90_save_tool_call_file.py`：>500 char 写文件 + 返回引用，零侵入 Extension | 无显式机制 | 无内置机制 |
| Observability | 自有 log 系统（`get_log_object`） | `createSubsystemLogger` | Logfire 原生 instrument |

### 2.2 方案对比表

| 维度 | 方案 A: Pydantic-Native 融合方案 | 方案 B: 自研轻量 Protocol 方案 |
|------|----------------------------------|-------------------------------|
| **概述** | 深度复用 Pydantic AI 的 `_function_schema` + `ToolDefinition`，在其基础上扩展 ToolMeta（side_effect_level, tool_profile, tool_group），ToolBroker 作为 Pydantic AI Agent 的 toolset 适配层 | 完全自研：定义 ToolMeta（Pydantic BaseModel）、用 `inspect.signature` + `get_type_hints` 自建 Schema Reflection，ToolBroker 基于 Protocol 接口独立实现 |
| **Schema 反射** | 直接调用 `pydantic_ai._function_schema.function_schema()`，获得 `FunctionSchema`（含 validator + json_schema），通过 Pydantic 的 `GenerateJsonSchema` 生成 | 自研：`inspect.signature` 提取参数 + `typing.get_type_hints` 获取类型 + `pydantic.TypeAdapter` 为每个参数生成 JSON Schema 片段并组装 |
| **性能** | 高 — 复用 Pydantic 已优化的 core_schema 构建和验证管线 | 中 — 需要自行优化 Schema 缓存和验证逻辑 |
| **可维护性** | 中 — 依赖 Pydantic AI 内部 API（`_function_schema` 带下划线前缀），升级风险存在但 Pydantic AI 是项目核心依赖 | 高 — 完全自控，无外部 private API 依赖 |
| **学习曲线** | 低 — 团队已使用 Pydantic AI，API 风格一致 | 中 — 需要理解 `inspect` 模块和 JSON Schema 组装逻辑 |
| **社区支持** | 高 — Pydantic AI 活跃（Pydantic 团队维护），schema 生成经过大量实战验证 | 低 — 自研方案无社区支持 |
| **适用规模** | 大 — 可直接复用 Pydantic AI 的工具准备、验证、重试机制 | 中 — 需自行实现验证和错误处理 |
| **与现有项目兼容性** | 极高 — 项目已依赖 Pydantic + Pydantic AI（Blueprint 明确选型）；ToolMeta 可与 Agent 的 tool 系统无缝对接 | 高 — 仅依赖 Pydantic BaseModel，但后续与 Pydantic AI Agent 集成时需要编写适配层 |
| **Constitution 对齐** | 原则 3 (Tools are Contracts): schema 自动反射保证 code=schema 单一事实源；原则 8 (Observability): Pydantic AI 原生 Logfire instrument | 原则 3: 需自行保证一致性；原则 8: 需手动集成 OpenTelemetry |
| **扩展性** | 高 — M2 阶段可直接使用 Pydantic AI 的 MCP toolset、DeferredToolRequests 等高级特性 | 中 — M2 需要自行实现 MCP 适配 |

### 2.3 方案 A 详细设计（推荐方案）

```
                    ToolBroker (中介者)
                   /        |         \
          discover()    execute()    filter()
              |             |            |
        ToolRegistry    HookChain    ProfileFilter
              |             |            |
         ToolMeta[]    before/after   profile/group
              |             |
        SchemaReflect  LargeOutputHandler
        (pydantic_ai     (after hook)
        _function_schema)
```

**核心组件**:

1. **ToolMeta** — Pydantic BaseModel，扩展 Pydantic AI `ToolDefinition`:
   ```python
   class SideEffectLevel(StrEnum):
       NONE = "none"           # 纯读取，无副作用
       REVERSIBLE = "reversible"     # 可回滚的副作用
       IRREVERSIBLE = "irreversible" # 不可逆操作

   class ToolProfile(StrEnum):
       MINIMAL = "minimal"     # 最小权限集
       STANDARD = "standard"   # 标准权限
       PRIVILEGED = "privileged" # 特权操作

   class ToolMeta(BaseModel):
       name: str
       description: str
       parameters_json_schema: dict[str, Any]
       side_effect_level: SideEffectLevel
       tool_profile: ToolProfile
       tool_group: str  # 如 "filesystem", "network", "memory"
       version: str = "1.0.0"
       timeout_seconds: float | None = None
   ```

2. **Schema Reflection** — 复用 Pydantic AI 的 `function_schema()`:
   ```python
   def reflect_tool_schema(func: Callable) -> ToolMeta:
       """从函数签名自动生成 ToolMeta（单一事实源）"""
       fs = function_schema(func, GenerateToolJsonSchema)
       # 从装饰器元数据提取 side_effect_level 等
       meta = getattr(func, '_tool_meta', {})
       return ToolMeta(
           name=func.__name__,
           description=fs.description or "",
           parameters_json_schema=fs.json_schema,
           side_effect_level=meta.get('side_effect_level', SideEffectLevel.NONE),
           ...
       )
   ```

3. **ToolBroker** — Protocol-based 中介者:
   ```python
   class ToolBrokerProtocol(Protocol):
       async def register(self, tool: ToolMeta, handler: ToolHandler) -> None: ...
       async def discover(self, profile: ToolProfile | None, group: str | None) -> list[ToolMeta]: ...
       async def execute(self, tool_name: str, args: dict[str, Any], context: ExecutionContext) -> ToolResult: ...
       def add_hook(self, hook: ToolHook) -> None: ...
   ```

4. **LargeOutputHandler** — after_hook 实现:
   ```python
   class LargeOutputHandler(ToolAfterHook):
       threshold: int = 500
       async def after_execute(self, result: ToolResult, context: ExecutionContext) -> ToolResult:
           if len(str(result.output)) > self.threshold:
               artifact = await self.store_as_artifact(result.output, context)
               return result.with_truncated(artifact_ref=artifact.artifact_id)
           return result
   ```

### 2.4 推荐方案

**推荐**: 方案 A — Pydantic-Native 融合方案

**理由**:
1. **最大化复用**: 项目已将 Pydantic AI 作为核心 Agent 框架（Blueprint 明确选型），复用其 schema 反射管线避免重复造轮，代码量减少约 60%
2. **单一事实源保证**: Pydantic AI 的 `function_schema()` 从函数签名 + type hints + docstring 一次性生成 JSON Schema + Validator，天然满足 Constitution 原则 3
3. **Observability 原生集成**: Pydantic AI 已内置 Logfire instrument，hook 层面的 span/trace 可直接对接，满足原则 8
4. **未来扩展性**: M2 阶段 Worker 使用 Pydantic AI Agent 时，ToolBroker 注册的工具可零成本转换为 Agent 的 toolset
5. **`_function_schema` 依赖风险可控**: 虽然是 private API，但 (a) Pydantic AI 由 Pydantic 团队维护且迭代稳定，(b) 可通过版本锁定 + 适配层隔离变更，(c) 该模块的核心逻辑（`inspect.signature` + `get_type_hints`）是 Python 标准库，降级自研成本低

### 2.5 方案 B 的适用场景（备选）

方案 B 适用于以下情况（当前不适用）：
- 项目不使用 Pydantic AI 作为 Agent 框架
- 需要极端轻量的独立工具系统（如嵌入式场景）
- Pydantic AI 发生重大 breaking change 导致 `_function_schema` 无法使用

**降级路径**: 若方案 A 中 `_function_schema` 在 Pydantic AI 升级后不兼容，可切换为 `pydantic.TypeAdapter.json_schema()` 逐参数生成，实现复杂度约增加 100 行代码。

## 3. 依赖库评估

### 3.1 评估矩阵

| 库名 | 用途 | 版本 | 许可证 | 最近更新 | 评级 | 备注 |
|------|------|------|--------|---------|------|------|
| pydantic | ToolMeta/ToolResult 数据模型 + JSON Schema 生成 | >=2.10,<3.0 | MIT | 活跃 | 核心 | 已有依赖，无需新增 |
| pydantic-ai-slim | Schema Reflection（`_function_schema`） | >=0.0.40 | MIT | 活跃（Pydantic 团队） | 核心 | M1 已规划引入；`_function_schema` 为 private API 需隔离 |
| structlog | Hook 层日志输出 | >=25.1,<26.0 | MIT | 活跃 | 核心 | 已有依赖 |
| python-ulid | Artifact ID / Tool 执行 trace ID 生成 | >=3.1,<4.0 | MIT | 稳定 | 核心 | 已有依赖 |
| logfire | OpenTelemetry instrument for hook spans | >=2.0 | MIT | 活跃（Pydantic 团队） | 推荐 | Blueprint 已选型 Logfire 作为 Observability 方案 |
| griffe | Docstring 解析（Google/NumPy/Sphinx 格式） | >=1.0 | ISC | 活跃 | 可选 | Pydantic AI 已内置依赖，若自研 Schema Reflection 则需要 |

### 3.2 推荐依赖集

**核心依赖（无需新增）**:
- `pydantic>=2.10,<3.0`: ToolMeta / ToolResult / ToolDefinition 等数据模型
- `structlog>=25.1,<26.0`: 结构化日志
- `python-ulid>=3.1,<4.0`: ID 生成
- `aiosqlite>=0.21,<1.0`: Artifact 存储（Large Output → ArtifactStore）

**新增核心依赖**:
- `pydantic-ai-slim` (无额外 extras): 复用 `_function_schema.function_schema()` 进行 Schema Reflection。注意：此依赖在 Feature 005（Pydantic Skill 执行层）中同样需要，004 提前引入可减少后续集成成本

**可选依赖**:
- `logfire>=2.0`: 用于 before/after hook 的 OTel span 注入。M1 已规划引入但非 004 硬性要求；004 阶段 hook 可先输出 structlog，M1 后续切换为 Logfire

### 3.3 与现有项目的兼容性

| 现有依赖 | 兼容性 | 说明 |
|---------|--------|------|
| pydantic>=2.10,<3.0 | 兼容 | ToolMeta 基于 BaseModel，与现有 Event/Task/Artifact 模型风格一致 |
| aiosqlite>=0.21,<1.0 | 兼容 | Large Output Handler 复用 ArtifactStore.put_artifact()，无需新增存储层 |
| structlog>=25.1,<26.0 | 兼容 | Hook 日志直接使用现有 structlog 配置 |
| python-ulid>=3.1,<4.0 | 兼容 | Artifact ID 生成复用现有 ULID 工具 |
| octoagent-core (workspace) | 兼容 | ToolResult 中的 artifact_ref 直接引用 core 的 Artifact 模型和 ArtifactStore Protocol |
| pydantic-ai-slim (新增) | 需注意 | 需要确认 pydantic-ai-slim 的 pydantic 版本要求与项目的 `>=2.10,<3.0` 兼容 |

### 3.4 包组织建议

Feature 004 的代码应放置在 `octoagent/packages/tooling/` 包中（Blueprint 已规划此路径）：

```
packages/tooling/
  pyproject.toml          # 依赖: octoagent-core + pydantic-ai-slim
  src/octoagent/tooling/
    __init__.py
    models.py             # ToolMeta, ToolResult, SideEffectLevel, ToolProfile 等
    schema.py             # Schema Reflection 封装（隔离 _function_schema 依赖）
    broker.py             # ToolBroker 实现
    hooks.py              # ToolHook Protocol + LargeOutputHandler + ObservabilityHook
    protocols.py          # ToolBrokerProtocol, ToolHandler Protocol
    decorators.py         # @tool_contract 装饰器
  tests/
```

## 4. 设计模式推荐

### 4.1 推荐模式

1. **Mediator 模式（ToolBroker）**: ToolBroker 作为工具注册、发现、执行的中央中介者。所有工具调用必须经过 Broker，确保 hook 链路完整执行。这与 Constitution 原则 4（Side-effect Must be Two-Phase）天然对齐——Broker 在 execute 前可插入 PolicyCheckpoint gate。

   **适用场景**: 工具与调用者（Worker/Orchestrator）解耦；集中管理工具的注册、过滤、执行、审计。
   **参考案例**: Agent Zero 的 `tool.before_execution()` / `after_execution()` 生命周期；Pydantic AI 的 `prepare_tool_def` 动态工具准备。

2. **Decorator + Metadata 模式（ToolMeta 声明）**: 使用 Python 装饰器将元数据（side_effect_level, tool_profile, tool_group）附加到工具函数上，Schema Reflection 时自动提取。

   ```python
   @tool_contract(
       side_effect_level=SideEffectLevel.IRREVERSIBLE,
       tool_profile=ToolProfile.STANDARD,
       tool_group="filesystem",
   )
   async def write_file(path: str, content: str) -> str:
       """写入文件内容。

       Args:
           path: 目标文件路径
           content: 要写入的内容
       """
       ...
   ```

   **适用场景**: 将工具元数据与函数实现紧密绑定，保证 code=schema 单一事实源。
   **参考案例**: Pydantic AI 的 `@agent.tool` 装饰器 + `Tool()` 构造器；LangChain 的 `@tool` 装饰器。

3. **Chain of Responsibility 模式（Hook 链）**: before/after hook 按优先级链式执行。每个 hook 可决定是否继续（放行）、修改（增强）或中断（拒绝）。

   ```
   before_hooks: [ObservabilityHook, PolicyCheckHook, ValidationHook]
       → execute tool
   after_hooks:  [ObservabilityHook, LargeOutputHandler, AuditHook]
   ```

   **适用场景**: 支持可插拔的横切关注点（observability、policy gate、output 裁切），不同 hook 之间互不干扰。
   **参考案例**: Agent Zero 的 Extension 系统（`_90_save_tool_call_file.py` 作为 after hook）；Django/FastAPI middleware 链。

4. **Adapter 模式（Schema Reflection 隔离层）**: 用 adapter 隔离 Pydantic AI `_function_schema` 的 private API 调用，对外暴露稳定的公共接口。当 Pydantic AI 升级导致 breaking change 时，只需修改 adapter 层。

   **适用场景**: 降低对第三方 private API 的耦合风险。
   **参考案例**: 项目中 `octoagent-provider` 对 LiteLLM SDK 的封装方式。

### 4.2 应用案例

**Agent Zero 的 Extension 系统**: Agent Zero 使用目录扫描 + 优先级编号（如 `_90_save_tool_call_file.py`）实现 hook 排序。`SaveToolCallFile` Extension 在 `hist_add_tool_result` 事件中检查输出长度，>500 char 则写入文件并将文件路径注入 `data["file"]`。这种零侵入设计值得借鉴——OctoAgent 的 LargeOutputHandler 应类似地作为 after hook 透明运行，工具实现者无需感知。

**OpenClaw 的 Plugin Tool 注册**: OpenClaw 的 `resolvePluginTools()` 展示了成熟的工具发现模式：factory 函数创建工具实例 → 名称冲突检测 → allowlist 过滤 → WeakMap 存储元数据。OctoAgent 的 ToolBroker 可借鉴其冲突检测和分组过滤机制。

**Pydantic AI 的 DeferredToolRequests**: Pydantic AI 最新版引入了 `requires_approval` 标志和 `DeferredToolRequests`/`DeferredToolResults` 机制，用于 human-in-the-loop 审批。这与 Constitution 原则 4（Two-Phase）和原则 7（User-in-Control）高度对齐。OctoAgent 的 PolicyCheckpoint 可在 M2 阶段对接此机制。

## 5. 技术风险清单

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---------|------|------|---------|
| 1 | **`_function_schema` 为 Pydantic AI private API**，未来版本可能 breaking change | 中 | 中 | (a) 版本锁定 pydantic-ai-slim；(b) 用 Adapter 模式隔离调用点（`schema.py` 单文件）；(c) 准备降级方案（`pydantic.TypeAdapter.json_schema()` 逐参数生成） |
| 2 | **Large Output 裁切阈值选择不当**：500 char 可能对某些工具（如代码执行）过于激进，导致关键信息丢失 | 中 | 中 | (a) 阈值可配置（per-tool 或全局）；(b) 裁切时保留头尾（prefix/suffix 策略，避免 lost-in-the-middle）；(c) 原始内容始终存入 Artifact，LLM 可按需检索 |
| 3 | **Hook 链执行顺序错误**导致 PolicyCheckHook 被绕过 | 低 | 高 | (a) Hook 注册时声明 priority；(b) PolicyCheckHook 强制最高优先级，不可被跳过；(c) 单元测试覆盖 hook 执行顺序 |
| 4 | **Schema Reflection 性能**：每次工具注册时调用 `function_schema()` 涉及 Pydantic 内部 schema 构建 | 低 | 低 | (a) Schema 在注册时一次性生成并缓存到 ToolMeta；(b) 工具注册是启动阶段操作，非热路径 |
| 5 | **ToolBroker 与 Pydantic AI Agent 的 toolset 集成**：M2 阶段 Worker 使用 Agent 时，需要将 Broker 的工具转换为 Agent 的 toolset | 中 | 中 | (a) ToolMeta 设计时包含 `parameters_json_schema`，可直接映射为 `ToolDefinition`；(b) 预留 `to_pydantic_tool()` 转换方法 |
| 6 | **side_effect_level 声明的准确性**依赖开发者自觉标注 | 中 | 中 | (a) 装饰器强制要求 side_effect_level 参数（无默认值）；(b) Code review 检查；(c) 未来可通过静态分析辅助检测（如检查是否调用了文件写入 API） |
| 7 | **async/sync 工具混合**：部分工具为同步函数，ToolBroker 需统一处理 | 低 | 低 | (a) `_function_schema` 已内置 `is_async` 检测；(b) Broker 执行层对同步工具使用 `asyncio.to_thread()` 包装 |

## 6. 需求-技术对齐度

### 6.1 覆盖评估

| MVP 功能 | 技术方案覆盖 | 说明 |
|---------|-------------|------|
| ToolMeta 声明模型 | 完全覆盖 | Pydantic BaseModel 扩展 ToolDefinition，包含 side_effect_level / tool_profile / tool_group |
| Schema Reflection | 完全覆盖 | 复用 `_function_schema.function_schema()`，从函数签名 + type hints + docstring 自动生成 |
| ToolBroker 执行中介 | 完全覆盖 | Protocol-based Mediator + Hook Chain，支持 discover/register/execute/filter |
| Large Output Handling | 完全覆盖 | LargeOutputHandler 作为 after hook，>500 char 调用 ArtifactStore 存储 + 返回引用 |
| 示例工具 x2 | 完全覆盖 | file_read（none）+ file_write（irreversible），使用 @tool_contract 装饰器 |
| 接口契约文档 | 完全覆盖 | ToolBrokerProtocol + ToolMeta + ToolResult + PolicyCheckpoint Protocol 定义在 protocols.py |

### 6.2 扩展性评估

| 未来需求 | 扩展性评估 | 说明 |
|---------|-----------|------|
| Feature 005 Pydantic Skill 执行层 | 高 | Skill 可通过 ToolBroker 注册为工具，共享 Schema Reflection 和 Hook 基础设施 |
| Feature 006 Policy Engine | 高 | PolicyCheckHook 作为 before hook 已预留扩展点；ToolMeta 的 side_effect_level 直接服务于 Policy 决策 |
| M2 Worker 的 Agent toolset 集成 | 高 | ToolMeta → ToolDefinition 映射可零成本完成；LargeOutputHandler 在 Agent 层面同样适用 |
| MCP 工具协议兼容 | 中 | Pydantic AI 已支持 MCP toolset，Broker 可通过 adapter 桥接 MCP 工具 |
| 工具热加载/动态注册 | 中 | Broker 的 register/unregister 接口支持运行时工具管理，但需额外考虑并发安全 |
| 工具调用审批流（Two-Phase） | 高 | Pydantic AI 的 `DeferredToolRequests` + ToolMeta 的 side_effect_level 天然支持 |

### 6.3 Constitution 约束检查

| 约束 | 兼容性 | 说明 |
|------|--------|------|
| 原则 1: Durability First | 兼容 | 工具调用事件通过 EventStore 持久化；大输出通过 ArtifactStore 落盘 |
| 原则 2: Everything is an Event | 兼容 | ToolBroker 的 before/after hook 生成 TOOL_CALL_STARTED / TOOL_CALL_COMPLETED 事件 |
| 原则 3: Tools are Contracts | 兼容 | Schema Reflection 保证 code=schema 单一事实源；ToolMeta 强制声明 side_effect_level |
| 原则 4: Side-effect Must be Two-Phase | 兼容 | irreversible 工具的 before hook 可插入 PolicyCheckpoint gate；M2 对接 DeferredToolRequests |
| 原则 5: Least Privilege by Default | 兼容 | ToolProfile（minimal/standard/privileged）支持按权限过滤工具集 |
| 原则 6: Degrade Gracefully | 兼容 | ToolBroker 的 hook 失败不阻塞工具执行（可配置策略：log-and-continue 或 fail-fast） |
| 原则 7: User-in-Control | 兼容 | side_effect_level=irreversible 的工具可在 M2 阶段接入审批流 |
| 原则 8: Observability is a Feature | 兼容 | ObservabilityHook 作为 before/after hook 生成 trace/span；structlog 日志覆盖全链路 |

## 7. 结论与建议

### 7.1 总结

Feature 004 的技术方案选型推荐 **方案 A（Pydantic-Native 融合方案）**，核心决策如下：

1. **Schema Reflection**: 复用 Pydantic AI 的 `_function_schema.function_schema()`，通过 Adapter 层隔离 private API 依赖，实现 code=schema 单一事实源
2. **ToolMeta**: 基于 Pydantic BaseModel 扩展，包含 side_effect_level / tool_profile / tool_group 等治理元数据
3. **ToolBroker**: Protocol-based Mediator 模式，支持 register / discover / execute / filter，内置 Hook Chain（before/after）
4. **Large Output Handling**: LargeOutputHandler 作为 after hook 零侵入运行，>500 char 自动存入 ArtifactStore 并返回引用
5. **Hook 扩展点**: Chain of Responsibility 模式，优先级排序，预留 PolicyCheckHook（Feature 006）和 ObservabilityHook（Logfire）

**新增依赖**: 仅需引入 `pydantic-ai-slim`（无额外 extras），该依赖在 Feature 005 中同样需要。

**技术风险**: 共识别 7 个风险点，均有明确缓解策略。最高优先级风险为 `_function_schema` private API 依赖（已通过 Adapter 隔离 + 降级方案缓解）。

### 7.2 对后续规划的建议

- **建议 1（对 Spec 阶段）**: ToolBrokerProtocol 和 ToolMeta 的接口设计应优先确认，因为 Feature 005（Pydantic Skill）和 Feature 006（Policy Engine）都依赖这些契约。建议在 spec 阶段先输出 `contracts/tooling-api.md`，供并行开发引用
- **建议 2（依赖引入时机）**: `pydantic-ai-slim` 的引入可在 004 spec review 通过后立即加入 `packages/tooling/pyproject.toml`，避免阻塞 005 的开发
- **建议 3（Hook 设计范围）**: 004 阶段 Hook 实现 ObservabilityHook（structlog 版本）和 LargeOutputHandler 即可；PolicyCheckHook 的完整实现留给 006，但 004 需预留 Protocol 接口
- **建议 4（测试策略）**: Schema Reflection 测试应覆盖：(a) 各种 type hint 组合（Optional, Union, list, dict, 嵌套 BaseModel）；(b) docstring 格式（Google/NumPy/Sphinx）；(c) async/sync 函数；(d) 无类型注解的降级处理
- **建议 5（风险监控）**: 在 CI 中添加 Pydantic AI 版本兼容性检查，当 pydantic-ai-slim 发布新版本时自动触发 schema.py 的单元测试
