---
feature_id: "071"
title: "Align LLM Config Flow"
milestone: "M4"
status: "partially-implemented"
created: "2026-03-20"
updated: "2026-03-20"
predecessor: "Feature 014 / Feature 036 / Feature 044"
blueprint_ref: "docs/blueprint.md §8.9.4 §12.9.1"
---

# Feature Specification: Align LLM Config Flow

**Feature Branch**: `071-align-llm-config-flow`  
**Created**: 2026-03-20  
**Status**: In Progress  
**Input**: 用户要求“先放一放 secret，把 LLM Provider / alias / Memory 模型依赖 / Web CLI Agent 配置流先做成一个能跑通的 feature”

## Problem Statement

当前 LLM 配置已经在底层收敛到 `octoagent.yaml`，但面向用户和 Agent 的主路径仍有几处关键断点：

1. **Web 无法完整表达自定义 Provider**
   `ProviderEntry.base_url` 已在 schema 与 LiteLLM 生成器中支持，但 Settings 页没有输入项，provider 草稿 round-trip 也会丢失该字段，导致 SiliconFlow / DeepSeek / vLLM / Ollama 这类依赖自定义 API Base 的 Provider 难以稳定配置。

2. **Memory 模型依赖没有前置校验**
   `memory.reasoning_model_alias`、`expand_model_alias`、`embedding_model_alias`、`rerank_model_alias` 目前主要在运行时 fallback，保存前不会明确拦截拼写错误或不存在 alias，用户与 Agent 很容易以为配置成功，但实际只是在静默降级。

3. **CLI 自定义 Provider 路径仍然不完整**
   Web 已支持自定义 `base_url`，但 CLI 的 `octo config provider add`、CLI wizard 仍无法完整表达 `ProviderEntry.base_url`，并且更新已有 Provider 时还会因为整条覆盖而丢失已有 `base_url` 等字段，导致 SiliconFlow / DeepSeek / vLLM / Ollama 这类场景在 CLI 里仍然容易失败。

4. **运行时 alias 消费仍停留在历史 MVP 语义映射**
   `octoagent.yaml.model_aliases` 已经允许任意 alias，LiteLLM 生成器也会为每个 alias 生成 `model_name`，但 Gateway `LLMService` 仍通过 `AliasRegistry` 把大量 alias 压缩回 `main/cheap/fallback`。这会让 `compaction` / `summarizer` / 自定义 alias 出现“配置成功但运行时没真正使用”的假象，已经不只是 UX 问题，而是实际功能错误。

5. **人类与 Agent 对“怎么配、怎么生效”的认知不一致**
   当前真正的一键入口是 Web/CLI 的 `setup.quick_connect` / `octo setup`，但 Agent runtime 仍只有低层 `config.*` 工具，不能直接走 canonical setup 启用路径；仓库内也残留“手改 `litellm-config.yaml`”“`config.sync` 等于热重载”“Memory 还有 memu/local 模式切换”等过期叙事，Agent skill 也没有同步更新。

6. **产品对外叙事与内部实现边界没有完全拉齐**
   目标应是“所有可见配置从 `octoagent.yaml` 出发，`litellm-config.yaml` 是衍生文件，LiteLLM 是内部实现细节”。当前 README / blueprint / skill 仍有历史路径残留，容易继续误导 Web 用户、CLI 用户和 Agent。

## User Scenarios & Testing

### User Story 1 - Web 可以完整配置自定义 Provider (Priority: P1)

用户在 Web `Settings` 中添加 SiliconFlow / DeepSeek / 本地 vLLM 等 Provider 时，必须能填写并保留自定义 `base_url`，保存后该字段要进入 `octoagent.yaml` 并生成到 `litellm-config.yaml`。

**Why this priority**: 这是“先跑通真实模型接入”最直接的主路径。当前很多第三方 Provider 在 Web 上根本无法正确录入。

**Independent Test**: 在 Settings 页添加一个自定义 Provider，填写 `base_url` 后点击检查/保存，提交草稿与持久化配置都保留该字段。

**Acceptance Scenarios**:

1. **Given** 用户在 Settings 中新增一个自定义 Provider，**When** 填写 `base_url` 并执行检查配置，**Then** 提交给 `setup.review` 的 draft 必须包含该 `base_url`
2. **Given** 已保存的 Provider 带有 `base_url`，**When** 页面重新加载并再次编辑保存，**Then** `base_url` 不得被静默丢失

---

### User Story 2 - Memory alias 错配在保存前就能发现 (Priority: P1)

用户或 Agent 配置 Memory 依赖模型时，如果填写了不存在的 alias，系统应在 `setup.review` / schema 校验阶段直接指出问题，而不是运行后再 fallback。

**Why this priority**: 这直接影响 Memory 是否真的按预期使用 Embedding / Rerank / Expand / Reasoning 模型；当前问题非常隐蔽。

**Independent Test**: 构造带错误 Memory alias 的配置草稿，`setup.review` 返回阻塞或明确风险；构造正确 alias 时不误报。

**Acceptance Scenarios**:

1. **Given** draft 中 `memory.embedding_model_alias=mem-embed`，但 `model_aliases` 不存在该 key，**When** 执行 `setup.review`，**Then** review 必须返回配置未通过或明确指出 `memory.embedding_model_alias` 错误
2. **Given** draft 中四个 Memory alias 都引用存在的 alias，**When** 执行 `setup.review`，**Then** review 不得因为 Memory alias 自身而误报阻塞

---

### User Story 3 - Web / CLI / Agent 对配置入口和生效语义一致 (Priority: P2)

用户从 Web、CLI 或 Agent 路径查看配置说明时，应得到同一条叙事：配置写入 `octoagent.yaml`，`litellm-config.yaml` 是自动生成的衍生物；需要“一键保存并启用真实模型”时走 `setup.quick_connect` / `octo setup`；`config.sync` 只负责重新生成衍生配置，不承诺重启 runtime。

**Why this priority**: 当前配置失败不只是代码问题，也来自入口叙事混乱。先把产品面向用户的路径统一，后续再做 secret 安全治理会顺畅很多。

**Independent Test**: 检查 skill / CLI 文案 / README / blueprint 中相关描述，确认不再要求手工编辑 `litellm-config.yaml`，也不再把 `config.sync` 描述成热重载。

**Acceptance Scenarios**:

1. **Given** Agent 读取 `skills/llm-config/SKILL.md`，**When** 它为用户配置 Provider，**Then** skill 应明确以 `octoagent.yaml` 为起点，并区分 `octo setup` 与 `octo config sync` 的职责
2. **Given** 用户阅读 README / blueprint 配置说明，**When** 寻找 Provider / Memory 模型配置入口，**Then** 文档必须指向 `octoagent.yaml`、Web Settings、`octo setup` / `octo config` 的当前路径，而不是历史 `litellm-config.yaml` / MemU 模式

---

### User Story 4 - CLI 与 Agent 也能完整跑通自定义 Provider (Priority: P1)

CLI 用户配置 SiliconFlow / 本地 vLLM 等自定义 Provider 时，必须能填写并保留 `base_url`；Agent 需要时也应有 canonical `setup.review` / `setup.quick_connect` 高层工具，而不是只能停留在 `config.sync`。

**Why this priority**: Web 主路径修好后，如果 CLI 与 Agent 仍不能完整配置和启用，自定义 Provider 依然会在真实使用中卡住。

**Independent Test**: 使用 CLI wizard 或 `octo config provider add` 配置自定义 Provider，保存后 `octoagent.yaml` 保留 `base_url`；Agent runtime 暴露 `setup.review` / `setup.quick_connect` 工具并能调用 canonical setup。

**Acceptance Scenarios**:

1. **Given** 已有 Provider 带 `base_url`，**When** 用户通过 `octo config provider add <id>` 或 Agent `config.add_provider` 更新同一个 Provider 且未显式改 `base_url`，**Then** 已有 `base_url` 不得被静默清空
2. **Given** CLI 用户进入统一 wizard，**When** 配置自定义 Provider 和 Memory alias，**Then** wizard draft 必须能同时表达 `providers[].base_url` 和 `memory.*_model_alias`
3. **Given** Agent 需要帮用户完成“保存并启用真实模型”，**When** 当前 surface 暴露 capability pack，**Then** Agent 必须能使用高层 `setup.review` / `setup.quick_connect` 工具，而不是只能提示人类改走 Web/CLI

---

### User Story 5 - Alias 从配置到运行时消费必须一致 (Priority: P1)

用户在 `octoagent.yaml` / Web Settings 中定义的 alias，必须在 Gateway 运行时被原样消费；历史语义 alias 只能作为兼容层，不能覆盖当前配置，也不能把未知 alias 静默降回 `main`。

**Why this priority**: 这是“配置成功但调用不对”的核心根因。只修入口不修运行时，用户仍会遇到 alias 配好了但 Butler / compaction / Agent 实际没按预期使用的情况。

**Independent Test**: 构造包含 `compaction`、`summarizer` 或任意自定义 alias 的配置，验证 Gateway 运行时解析后仍使用对应 alias，而不是被强制折叠为 `main` / `cheap`。

**Acceptance Scenarios**:

1. **Given** `octoagent.yaml.model_aliases` 存在 `compaction` 和 `summarizer`，**When** Context Compaction 调用 LLMService，**Then** 运行时必须继续使用这些 alias，而不是静默改写成 `cheap`
2. **Given** `octoagent.yaml.model_aliases` 存在自定义 alias `reasoning` 或 `research-main`，**When** Root Agent / Worker profile 绑定该 alias 并发起调用，**Then** LLMService 必须按该 alias 调 LiteLLM Proxy
3. **Given** 某个 legacy 语义 alias 未在 `model_aliases` 中显式定义，**When** 运行时代码请求该 alias，**Then** 系统只能按兼容映射解析到现有 alias，不得无条件吞成 `main`

## Edge Cases

- Provider 为 OAuth 类型时，`base_url` 字段应允许留空，不强迫用户填写
- 用户修改 Provider `id` 后，已有 alias 绑定仍应正确跟随更新，不得因为新增 `base_url` 字段破坏原有迁移逻辑
- Memory alias 留空时应继续保持既有 fallback 行为，不应因为新增校验而把默认空值判为错误
- `config.sync` 文案改正后，不应影响现有 CLI/Agent 调用结果结构或已有测试假设
- 运行时对 legacy 语义 alias 的兼容必须“显式优先于隐式”：若 `model_aliases` 已有同名 alias，应优先使用配置值；只有缺失时才允许语义兼容映射
- Root Agent / Worker profile 的 alias 选择既要校验存在性，也要兼容当前 builtin 模板与历史测试数据，不得引入“保存时通过、运行时失效”的新断层

## Requirements

### Functional Requirements

- **FR-001**: Web Settings 必须支持查看、编辑并提交 `providers[].base_url`
- **FR-002**: Provider 草稿解析与序列化必须无损保留 `base_url`，避免页面 round-trip 丢字段
- **FR-003**: `OctoAgentConfig` 必须校验非空的 `memory.*_model_alias` 引用存在于 `model_aliases`
- **FR-004**: `setup.review` 必须把 Memory alias 相关问题纳入保存前检查结果，避免只在运行时 fallback
- **FR-005**: Agent / CLI / 文档侧必须统一说明 `octoagent.yaml` 是单一事实源，`litellm-config.yaml` 是衍生文件
- **FR-006**: `config.sync` 与相关 Agent 能力文案必须诚实描述其职责为“重新生成衍生配置”，不得继续宣称其会热重载 runtime
- **FR-007**: `skills/llm-config/SKILL.md` 必须覆盖 Provider、模型别名、Memory alias、Web/CLI/Agent 入口与生效方式的当前正确路径
- **FR-008**: CLI `octo config provider add` 与 CLI wizard 必须支持查看、编辑并提交 `providers[].base_url`
- **FR-009**: 更新已有 Provider 时，CLI 与 Agent 的增量修改不得因为未显式提供可选字段而静默丢失已有 `base_url`
- **FR-010**: CLI wizard 必须覆盖 `memory.*_model_alias` 输入，确保 CLI-first 用户能一次性完成 Memory 模型绑定
- **FR-011**: Agent runtime capability pack 必须暴露高层 `setup.review` / `setup.quick_connect` 工具，复用 canonical setup 语义
- **FR-012**: Gateway 运行时的 alias 解析必须以 `octoagent.yaml.model_aliases` 为主事实源，不能再把任意 alias 默认折叠为 `main`
- **FR-013**: `AliasRegistry` 的 legacy 语义 alias 兼容必须只作为 fallback 层，且不得覆盖同名的显式配置 alias
- **FR-014**: Root Agent / Worker profile 的保存与 review 必须校验 `model_alias` 存在于当前可用 alias 集
- **FR-015**: Web AgentCenter 的 alias 选项不得再硬编码不存在于 `model_aliases` 的值；UI 只能展示当前配置中真实可用的 alias，并兼容默认 `main/cheap`
- **FR-016**: CLI `octo setup` 主入口必须支持 custom provider + `base_url` 路径，避免 CLI-first 用户被迫切回低层命令或 Web

### Key Entities

- **Provider Draft**: Settings 页中对 `providers[]` 的可编辑草稿项，需完整映射 `ProviderEntry`
- **Memory Alias Binding**: `memory.*_model_alias -> model_aliases` 的引用关系
- **Quick Connect Flow**: `setup.review -> setup.quick_connect -> runtime activation` 的一键配置与启用主路径
- **Derived LiteLLM Config**: 从 `octoagent.yaml` 自动生成的 `litellm-config.yaml`
- **Runtime Alias Registry**: Gateway 在调用 LiteLLM 前用于解析 alias 的运行时注册表，必须与 `octoagent.yaml.model_aliases` 保持一致

## Success Criteria

### Measurable Outcomes

- **SC-001**: Settings 页提交包含自定义 Provider 时，`base_url` 字段在检查配置、保存配置、页面刷新后的 round-trip 中都不丢失
- **SC-002**: 对错误的 Memory alias 配置执行 `setup.review` 时，100% 能在保存前返回明确错误或阻塞原因
- **SC-003**: `skills/llm-config/SKILL.md`、README、blueprint 中不再要求用户手工编辑 `litellm-config.yaml` 来完成主配置
- **SC-004**: Agent / CLI 的 `config.sync` 文案统一为“同步衍生配置”，不再把它描述成 runtime 热重载
- **SC-005**: CLI 更新已有自定义 Provider 后，`base_url` 不会因为字段省略而被清空
- **SC-006**: Agent capability pack 中可见并可调用 `setup.review` / `setup.quick_connect`
- **SC-007**: Gateway 对 `compaction`、`summarizer` 和任意显式配置 alias 的实际调用，100% 保持 alias 原样或按兼容映射显式解析，不再无条件静默降回 `main`
- **SC-008**: Agent / Root Agent 保存不存在的 alias 时，review 或 save 阶段 100% 返回明确错误
- **SC-009**: `octo setup` 能直接完成一个 custom provider + `base_url` 的 CLI 主路径接入，不需要再回退到低层 `config provider add`
