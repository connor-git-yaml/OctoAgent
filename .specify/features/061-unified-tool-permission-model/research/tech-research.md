# 技术调研报告: 统一工具注入 + 权限 Preset 模型

**特性分支**: `claude/festive-meitner`
**调研日期**: 2026-03-17
**调研模式**: 离线（基于代码库与参考项目分析）
**产品调研基础**: 独立技术调研（tech-only 模式）

## 1. 调研目标

**核心问题**:
- 问题 1: 如何实现 Deferred Tools 懒加载，在不牺牲工具可发现性的前提下将 context 占用降低 60-70%
- 问题 2: 如何将当前 Worker Type 多模板系统收敛为统一工具集 + 三级权限 Preset（minimal/normal/full）
- 问题 3: Bootstrap 模板简化到 shared + 角色卡片（~200 tokens）后对 LLM 行为的影响
- 问题 4: Skill 声明 `tools_required` 后，如何在加载时自动提升关联工具到活跃集合

**当前状态分析**:
- 49 个 `@tool_contract` 注册在 `capability_pack.py`，每个工具的完整 JSON Schema 约 200-500 tokens
- 4 种 WorkerType（general/ops/research/dev）各自维护独立的 `default_tool_groups` 列表和 bootstrap 模板
- 5 个 bootstrap 文件（shared + general/ops/research/dev），每个 200-800 字符
- ToolIndex 已实现基于 hash embedding 的向量检索 + BM25 混合打分
- ToolProfile 三级体系（minimal/standard/privileged）已存在于 `tooling/models.py`
- Skill 系统（Feature 057）已支持 `tools_required` 声明字段

## 2. 架构方案对比

### 2.1 Deferred Tools 懒加载方案

#### 方案对比表

| 维度 | 方案 A: ToolIndex 向量检索 | 方案 B: BM25 关键词匹配 | 方案 C: 混合模式（推荐） |
|------|-------------------------|----------------------|----------------------|
| 概述 | 仅暴露名称+描述摘要列表，LLM 通过 `tool_search` 触发 ToolIndex 向量检索，命中工具加载完整 schema | 仅暴露名称列表，LLM 通过 `tool_search` 触发纯关键词/BM25 匹配 | Core Tools 始终加载 schema（~10 个）+ Deferred Tools 仅暴露 `{name, one_line_desc}` 列表，`tool_search` 使用现有 ToolIndex 混合打分 |
| 检索质量 | 高：语义理解好，但 hash embedding 维度低（96）精度有限 | 中：精确匹配好，语义泛化差 | 高：当前 ToolIndex 已实现 cosine + BM25 overlap 加权，利用现有基础设施 |
| Context 节省 | 60-70%：只传名称列表 | 70-80%：列表更精简 | 60-65%：Core Tools schema 常驻 + deferred 名称列表 |
| 首次交互延迟 | 需要额外一轮 tool call 才能使用非 core 工具 | 同上 | Core Tools 无延迟，deferred 需要一轮 |
| 实现复杂度 | 中：复用 ToolIndex.select_tools()，需新增 schema 懒加载注入逻辑 | 低：纯文本匹配，但需额外 BM25 索引 | 中：复用 ToolIndex，新增 core/deferred 分区逻辑 |
| 与现有项目兼容性 | 好：直接复用 ToolIndex + InMemoryToolIndexBackend | 需新增 BM25 专用索引 | 好：ToolIndex._score_record() 已是 cosine+BM25 混合 |
| LLM 行为风险 | 中：LLM 可能不知道何时该搜索 | 高：缺乏语义理解，LLM 可能搜不到正确工具 | 低：Core Tools 覆盖高频场景，deferred 有 one_line_desc 辅助判断 |

#### 推荐方案

**推荐**: 方案 C — 混合模式（Core Tools 常驻 + Deferred Tools 名称列表 + ToolIndex 检索）

**理由**:
1. **复用现有基础设施**：ToolIndex 的 `_score_record()` 已经是 cosine + BM25 overlap 加权，无需新建索引
2. **Core Tools 消除高频场景延迟**：`project.inspect`、`filesystem.read_text`、`filesystem.write_text`、`terminal.exec`、`memory.recall`、`tool_search`、`skills`、`subagents.spawn` 等 ~10 个高频工具始终加载完整 schema，覆盖 80% 使用场景
3. **与 Claude Code 模式对齐**：Claude Code 采用类似的 Core Tools + Deferred Tools 双层架构（见本对话的 `available-deferred-tools` 示例），LLM 对这种模式有良好的行为适应性
4. **降级友好**：ToolIndex 检索失败时，可回退到 deferred 名称列表全量加载（Constitution 原则 6: Degrade Gracefully）

**实现要点**:
- 新增 `ToolTier` 枚举：`CORE`（始终加载 schema）/ `DEFERRED`（仅名称+描述）
- `@tool_contract` 新增可选参数 `tier: ToolTier = ToolTier.DEFERRED`，高频工具标记为 `CORE`
- 新增 `tool_search` 核心工具：接收自然语言查询，调用 `ToolIndex.select_tools()`，返回命中工具的完整 schema
- Pydantic AI 集成：使用 `DynamicToolset` 在每个 run step 根据 `tool_search` 结果动态注入新工具

### 2.2 权限 Preset 方案

#### 方案对比表

| 维度 | 方案 A: 内嵌 ToolBroker | 方案 B: 独立 PermissionService | 方案 C: Policy Engine 集成（推荐） |
|------|----------------------|---------------------------|-------------------------------|
| 概述 | 将 Preset 检查逻辑直接内嵌到 ToolBroker.execute() 的 Profile 检查步骤 | 新建独立 PermissionService 层，ToolBroker 委托权限检查 | 将 Preset 作为 BeforeHook 实现，集成到现有 Hook Chain |
| 架构内聚性 | 低：ToolBroker 职责膨胀（注册+发现+执行+权限） | 高：权限逻辑独立，清晰分层 | 高：复用现有 Hook 机制，Preset 是一种特殊的 BeforeHook |
| soft deny 实现 | 需修改 execute() 返回体，增加 `ask` 状态 | PermissionService 返回 `allow/ask/deny`，execute() 转译 | Hook 返回 `BeforeHookResult(proceed=False, rejection_reason="ask:...")` + 上层捕获 |
| 运行时审批覆盖 | 需在 ToolBroker 增加运行时状态存储 | PermissionService 管理审批状态（approve/always/deny） | 审批覆盖作为 Hook chain 的高优先级前置 Hook，覆盖 Preset Hook 的决策 |
| 持久化 | 需额外存储层 | 自带存储接口 | 审批状态可存入 AgentContext / AgentSession |
| 与 Pydantic AI 兼容 | 不兼容 ApprovalRequiredToolset | 可桥接但需适配层 | 天然兼容：Hook chain 的 `ask` 可映射到 Pydantic AI `ApprovalRequired` |
| 与现有项目兼容性 | 需改造 execute() 签名和返回体 | 需新增 package | 复用 BeforeHook + FailMode.CLOSED 机制 |

#### 推荐方案

**推荐**: 方案 C — Policy Engine 集成（Preset 作为 BeforeHook）

**理由**:
1. **最小侵入性**：ToolBroker 已有完整的 Hook Chain 机制（FR-019/020），Preset 逻辑自然映射为 `BeforeHook`
2. **与 Pydantic AI ApprovalRequired 天然对齐**：`ask` 决策可直接 raise `ApprovalRequired`，复用 Pydantic AI 的 deferred tool 流程
3. **Constitution 原则 7 兼容**：soft deny（ask 而非硬拒绝）通过 Hook 的 `rejection_reason` 字段携带 `ask:` 前缀标识，上层捕获后触发审批流而非直接失败
4. **二级审批自然分层**：
   - 第一级（Preset 默认态）：`PresetBeforeHook` 检查工具是否在当前 Preset 允许列表内
   - 第二级（运行时覆盖）：`ApprovalOverrideHook`（更高优先级）检查用户是否已 approve/always/deny 过该工具

**Preset 定义**:

```python
class PermissionPreset(StrEnum):
    MINIMAL = "minimal"   # 只读工具（filesystem.read_text, project.inspect, artifact.list 等）
    NORMAL = "normal"     # 标准读写（+ filesystem.write_text, terminal.exec, web.*, memory.* 等）
    FULL = "full"         # 全部工具（+ 不可逆操作、MCP、delegation 等）
```

**映射关系（取代 ToolProfile）**:
- `MINIMAL` ≈ 当前 `ToolProfile.MINIMAL`（只读）
- `NORMAL` ≈ 当前 `ToolProfile.STANDARD`（读写）
- `FULL` ≈ 当前 `ToolProfile.PRIVILEGED`（全部）

**每个 Agent 实例独立配置**:
- Butler 默认 `FULL`
- Worker 创建时由 Butler 指定，默认 `NORMAL`
- Subagent 继承其 Worker 的 Preset

### 2.3 Bootstrap 简化方案

#### 当前状态

当前 5 个 bootstrap 文件的 token 估算：
- `bootstrap:shared`（~180 字符 ≈ ~90 tokens）：项目/环境元信息 + 治理警告
- `bootstrap:general`（~550 字符 ≈ ~250 tokens）：Butler 行为指导
- `bootstrap:ops`（~120 字符 ≈ ~60 tokens）：Ops worker 简短描述
- `bootstrap:research`（~180 字符 ≈ ~80 tokens）：Research worker 简短描述
- `bootstrap:dev`（~100 字符 ≈ ~50 tokens）：Dev worker 简短描述

**总计**：shared + 1 个角色文件 ≈ 150-340 tokens/agent

#### 目标状态

简化为：
- `bootstrap:shared`（~50 tokens）：精简为核心元信息（project/workspace/datetime/preset），去除冗余字段
- `role_card:<agent_id>`（~100-150 tokens）：从 WorkerType 多模板转为 Agent 实例级的角色卡片

**总计**：~150-200 tokens/agent

#### 方案对比表

| 维度 | 方案 A: 保留 WorkerType 模板 | 方案 B: 统一 shared + 角色卡片（推荐） |
|------|--------------------------|-----------------------------------|
| Context 开销 | ~150-340 tokens/agent | ~150-200 tokens/agent |
| 维护成本 | 4 个角色模板需分别维护 | 1 个 shared 模板 + 角色卡片自动生成 |
| 灵活性 | 受限于 4 种 WorkerType | Agent 实例级，可自定义角色描述 |
| LLM 行为一致性 | 角色提示有助于行为聚焦 | 需通过 Preset + Skill 注入替代角色引导 |
| 迁移风险 | 无需迁移 | 低：当前角色模板内容本身很简短，核心行为由 behavior 系统驱动 |

#### 推荐方案

**推荐**: 方案 B — 统一 shared + 角色卡片

**理由**:
1. **当前角色模板内容极其简短**（50-250 tokens），实际行为引导已由 `butler_behavior.py` 的 behavior pack 系统承担，bootstrap 模板的角色提示冗余
2. **与"砍掉 Worker Type 多模板系统"目标一致**：不再有 4 种 WorkerType 各自的 bootstrap，改为 Agent 实例级别
3. **角色卡片可动态生成**：根据 Agent 的 Preset + 已加载 Skill + 创建时的 objective 自动生成角色描述

**LLM 行为一致性风险评估**:
- **风险等级**：低
- **缓解措施**：
  - 角色卡片保留核心引导（"你是 Butler / 你是 research 执行者"）
  - Preset 约束已限定了可用工具集，天然约束行为范围
  - behavior 系统（`butler_behavior.py`）已承担详细行为规范的职责
  - Skill 注入时携带的 system prompt 进一步补充领域行为

## 3. 依赖库评估

### 评估矩阵

| 库名 | 用途 | 版本 | 状态 | 评级 |
|------|------|------|------|------|
| pydantic-ai (现有) | Agent 框架 + DeferredTools + ApprovalRequired | 0.1.x+ | 已集成 | 核心 |
| pydantic-ai DynamicToolset | 运行时动态工具注入 | 同上 | 已可用，待集成 | 核心 |
| pydantic-ai ApprovalRequiredToolset | 审批机制 | 同上 | 已可用，待集成 | 核心 |
| pydantic-ai PreparedToolset | 工具定义运行时修改 | 同上 | 已可用，备选 | 可选 |

### 推荐依赖集

**核心依赖**:
- `pydantic-ai DynamicToolset`：用于 Deferred Tools 的运行时按需加载。`DynamicToolset` 支持 `per_run_step=True`，每轮 LLM 交互前可重新评估工具集
- `pydantic-ai ApprovalRequiredToolset`：用于 soft deny 的审批拦截。`approval_required_func` 接收 `(ctx, tool_def, tool_args)` 三参数，可桥接到 Preset 检查

**可选依赖**:
- `pydantic-ai PreparedToolset`：用于 Deferred Tools 的 schema 注入后动态修改工具描述（如添加 context-aware 提示）

### 与现有项目的兼容性

| 现有依赖 | 兼容性 | 说明 |
|---------|--------|------|
| ToolBroker (packages/tooling) | ✅ 兼容 | Hook Chain 机制直接支持新增 PresetBeforeHook；execute() 签名无需变更 |
| ToolIndex (packages/tooling) | ✅ 兼容 | select_tools() 已返回 ToolMeta 列表，可直接用于 deferred schema 加载 |
| ToolProfile (packages/tooling) | ⚠️ 需演进 | 三级 minimal/standard/privileged 已存在，可直接重命名为 Preset 语义；`profile_allows()` 逻辑复用 |
| CapabilityPackService (gateway) | ⚠️ 需重构 | `_build_worker_profiles()` 和 `resolve_profile_first_tools()` 需重构为 Preset 驱动 |
| BehaviorPack (gateway) | ✅ 兼容 | bootstrap 简化不影响 behavior pack 系统，两者独立 |
| Skill Discovery (packages/skills) | ✅ 兼容 | `SkillMdEntry.tools_required` 字段已就绪，需新增加载时提升逻辑 |
| Pydantic AI Agent 构造 | ⚠️ 需适配 | 当前通过 `FunctionToolset` 静态注入，需改为 `CombinedToolset(core_toolset, DynamicToolset(deferred_resolver))` |

## 4. 设计模式推荐

### 推荐模式

1. **Mediator + Hook Chain（现有，扩展）**：
   - ToolBroker 作为中介者，Preset 检查作为新的 BeforeHook 插入 hook chain
   - 优先级排序：`ApprovalOverrideHook(priority=10)` > `PresetBeforeHook(priority=20)` > 其他 hooks
   - 适用场景：权限检查、审批覆盖、soft deny

2. **Two-Tier Toolset（新增）**：
   - 借鉴 Claude Code 模式：Core Toolset（FunctionToolset，静态）+ Deferred Toolset（DynamicToolset，按需）
   - `tool_search` 工具调用后，命中工具的 schema 通过 `DynamicToolset` 注入到下一轮对话
   - 适用场景：context 预算优化、工具规模扩展

3. **Capability Escalation（新增）**：
   - Skill 加载时，解析 `tools_required` 声明，将关联工具从 Deferred 提升到 Active
   - 提升操作受 Preset 约束：超出 Preset 允许范围的工具触发 soft deny（ask）
   - 适用场景：Skill-Tool 注入路径优化

### 应用案例

**Claude Code 的 Deferred Tools 模式**：
- 本对话中可直接观察到的实现：`<available-deferred-tools>` 列出 ~100 个工具名称（不含 schema），LLM 通过 `ToolSearch` 工具按需获取完整定义
- 核心价值：初始 context 仅包含 ~10 个 core tools 的 schema（Read/Write/Edit/Bash/Grep/Glob/Skill），其余工具仅占名称列表空间

**OpenClaw 的工具策略引擎**：
- `tools.allow/deny` 全局策略 + `agents.list[].tools.allow/deny` 每 agent 策略
- `group:*` 展开机制（如 `group:runtime` -> `exec, bash, process`）
- 沙箱工具策略（`tools.sandbox.tools.allow/deny`）— 类似于 Preset 的隔离层
- deny 始终优先（hard deny），与 OctoAgent 的 soft deny 设计不同

**Pydantic AI 的审批机制**：
- `ApprovalRequiredToolset`：`approval_required_func(ctx, tool_def, tool_args)` 返回 bool
- 触发 `ApprovalRequired` 异常 -> agent run 结束并返回 `DeferredToolRequests.approvals`
- 用户审批后通过 `DeferredToolResults.approvals` 恢复执行
- 天然支持 OctoAgent 的 soft deny 需求

## 5. 技术风险清单

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---------|------|------|---------|
| 1 | **LLM 不主动调用 tool_search**：Deferred Tools 名称列表不足以让 LLM 判断何时搜索 | 中 | 高 | Core Tools 覆盖 80% 高频场景；system prompt 明确引导"不确定时先搜索"；one_line_desc 提供足够线索 |
| 2 | **tool_search 检索质量不足**：hash embedding（dim=96）精度有限，语义泛化能力弱 | 中 | 中 | 当前 ToolIndex 已是 cosine + BM25 混合打分，实测足够；后续可升级到 LanceDB 真向量检索 |
| 3 | **Preset 与现有 ToolProfile 语义冲突**：两套概念并存导致混淆 | 低 | 中 | 在迁移中将 ToolProfile 重命名/映射为 PermissionPreset，不引入新枚举名，只扩展语义 |
| 4 | **soft deny 循环**：Preset 不允许 -> ask -> 用户 deny -> LLM 再次尝试 | 低 | 低 | ask 的 rejection_reason 明确告知"需要审批"，LLM 行为规范中加入"审批被拒后不再重试" |
| 5 | **Bootstrap 简化导致 LLM 行为漂移**：去除角色模板后 LLM 不再聚焦特定角色 | 低 | 中 | 角色卡片保留核心引导（~50 tokens 即可）；behavior pack 系统已承担详细行为规范 |
| 6 | **DynamicToolset per_run_step 性能开销**：每轮 LLM 交互都重新评估工具集 | 低 | 低 | ToolIndex 查询在 <1ms 量级（内存索引），49 个工具的 schema 序列化可缓存 |
| 7 | **Skill 工具提升与 Preset 冲突**：Skill 声明的 tools_required 超出 Agent Preset 范围 | 中 | 中 | soft deny（ask）：提示用户"该 Skill 需要 X 工具，当前 Preset 不允许，是否授权？" |
| 8 | **MCP 工具与 Deferred Tools 交叉**：MCP 注册的工具是否也走 deferred 模式 | 中 | 中 | MCP 工具默认 DEFERRED tier；tool_search 统一检索 builtin + MCP 工具 |

## 6. 产品-技术对齐度

### 覆盖评估

| 需求 | 技术方案覆盖 | 说明 |
|---------|-------------|------|
| Deferred Tools 懒加载 | ✅ 完全覆盖 | 方案 C 通过 Core/Deferred 分区 + ToolIndex 检索实现，预估 context 节省 60-65% |
| 统一工具可见性 + 权限 Preset | ✅ 完全覆盖 | 砍掉 WorkerType 多模板，所有 Agent 共享同一工具集，通过 PresetBeforeHook 执行权限检查 |
| 二级审批机制 | ✅ 完全覆盖 | Preset 默认态（PresetBeforeHook） + 运行时审批（ApprovalOverrideHook），soft deny 通过 ApprovalRequired 实现 |
| Bootstrap 模板最小化 | ✅ 完全覆盖 | shared + 角色卡片 ~200 tokens，behavior pack 系统承担详细行为规范 |
| Skill-Tool 注入路径优化 | ✅ 完全覆盖 | Skill 加载时解析 tools_required，通过 DynamicToolset 提升到活跃集合，受 Preset 约束 |
| Butler 默认 Full | ✅ 完全覆盖 | Agent 实例级 Preset 配置，Butler 实例创建时设为 FULL |
| Subagent 继承 Worker Preset | ✅ 完全覆盖 | 创建 Subagent 时从 parent Worker 的 AgentSession 读取 Preset |
| soft deny（ask 而非硬 deny） | ✅ 完全覆盖 | BeforeHookResult + Pydantic AI ApprovalRequired 桥接 |

### 扩展性评估

1. **Preset 自定义**：未来可支持用户定义 custom Preset（如 `network_only`），只需在 PresetBeforeHook 的允许列表中添加新条目
2. **动态 Preset 升降级**：运行中可通过审批流临时提升 Preset（如 Worker 需要 FULL 权限执行特定操作）
3. **工具数量扩展**：Deferred Tools 架构天然支持百级工具规模，ToolIndex 检索 <1ms
4. **跨进程 / A2A**：Preset 持久化在 AgentSession 中，支持进程重启后恢复

### Constitution 约束检查

| 约束 | 兼容性 | 说明 |
|------|--------|------|
| 原则 1: Durability First | ✅ 兼容 | Preset 持久化在 AgentSession/AgentRuntime，进程重启后可恢复 |
| 原则 2: Everything is an Event | ✅ 兼容 | Preset 检查结果通过 ToolBroker 的事件链记录（TOOL_CALL_STARTED/FAILED） |
| 原则 3: Tools are Contracts | ✅ 兼容 | Deferred Tools 的 schema 在加载时仍经过 reflect_tool_schema 反射，确保一致性 |
| 原则 4: Side-effect Must be Two-Phase | ✅ 兼容 | soft deny (ask) 即为 Plan -> Gate 的实现；irreversible 工具仍需 PolicyCheckpoint |
| 原则 5: Least Privilege by Default | ✅ 兼容 | 默认 Preset 为 MINIMAL（除 Butler 外），Deferred Tools 默认不加载 schema |
| 原则 6: Degrade Gracefully | ✅ 兼容 | ToolIndex 不可用时回退全量名称列表；Preset 检查失败时 fail-open（记录警告） |
| 原则 7: User-in-Control | ✅ 兼容 | 二级审批：Preset 默认态 + 运行时 approve/always/deny |
| 原则 8: Observability is a Feature | ✅ 兼容 | Preset 检查、tool_search、工具提升均生成事件，可在 UI 查看 |
| 原则 13A: 优先提供上下文 | ✅ 兼容 | Deferred Tools 名称列表 + tool_search 是上下文驱动的发现机制，非硬编码策略 |

## 7. 结论与建议

### 总结

Feature 061 的四个改进目标均有可行的技术方案，且与现有架构高度兼容：

1. **Deferred Tools**：采用混合模式（Core ~10 个 + Deferred 名称列表），复用 ToolIndex 和 Pydantic AI DynamicToolset，预估 context 节省 60-65%
2. **权限 Preset**：采用 Policy Engine 集成模式（PresetBeforeHook + ApprovalOverrideHook），复用 ToolBroker Hook Chain 和 Pydantic AI ApprovalRequired
3. **Bootstrap 简化**：统一为 shared + 角色卡片（~200 tokens），behavior pack 系统已承担详细行为规范
4. **Skill-Tool 注入**：Skill 加载时解析 `tools_required`，通过 DynamicToolset 提升到活跃集合

核心架构变更集中在：
- `capability_pack.py`：砍掉 `_build_worker_profiles()` 的 WorkerType 多模板，改为 Preset 驱动
- `broker.py`：新增 `PresetBeforeHook` 和 `ApprovalOverrideHook`
- `tool_index.py`：新增 `tool_search` 核心工具的 facade
- 新增 `ToolTier` 枚举和 Core/Deferred 分区逻辑
- Agent 构造层：从 `FunctionToolset` 改为 `CombinedToolset(core, dynamic_deferred)`

### 对产研汇总的建议

- **优先实现顺序**：权限 Preset（影响面最大但风险最低）-> Deferred Tools（context 节省最显著）-> Bootstrap 简化（低风险）-> Skill-Tool 注入（依赖前三者）
- **风险重点关注**：LLM 是否能正确使用 `tool_search`（建议在实现后进行 A/B 测试，对比全量注入 vs deferred 模式下的任务完成率）
- **迁移策略**：ToolProfile -> PermissionPreset 的重命名应渐进式进行，先在新接口使用 Preset 语义，旧接口保持兼容
- **Core Tools 清单需产品确认**：哪些工具属于 Core（始终加载）需要基于实际使用频率数据决定，建议先从 Event Store 统计 top-10 工具调用频率
