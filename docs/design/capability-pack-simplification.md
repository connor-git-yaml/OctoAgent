# Capability Pack Simplification Refactor Plan

## 1. 目标

收口 `capability_pack.py` 的结构性坏味道，并把 OctoAgent 的默认工具暴露面从“过宽、混层、易让模型分心”调整为“少而宽、按场景分层、易治理”。

这个方案不主张简单删除大量工具实现，而是主张：

- 保留 contract 级工具定义
- 缩窄默认注入给模型的工具面
- 把解题、编排、管理三类能力拆成不同 surface
- 把巨型 `CapabilityPackService` 拆成职责清晰的服务和 domain registry

## 2. 源码级外部对照

本节不只看 README，而是直接看两个项目的源码与实现结构。

### 2.1 Agent Zero：少数宽工具 + 文件化工具目录 + subordinate / MCP 动态接入

#### 2.1.1 工具加载方式

Agent Zero 的核心调度在：

- [_references/opensource/agent-zero/agent.py](/Users/connorlu/.codex/worktrees/47dc/OctoAgent/_references/opensource/agent-zero/agent.py)

关键实现：

- `process_tools()` 先从模型输出里解析 `tool_name / tool_args`
- 先尝试 MCP 工具查找
- MCP 找不到时，再 fallback 到本地工具
- 本地工具通过 `get_tool()` 到 `python/tools/<name>.py` 动态加载

对应代码位置：

- `process_tools()`：`agent.py:855`
- MCP 优先查找：`agent.py:872-890`
- 本地 fallback：`agent.py:891-900`
- `get_tool()`：`agent.py:974-1001`

这说明 Agent Zero 的设计重点是：

1. 工具入口少，执行入口集中在 `agent.py`
2. 工具实现分散在 `python/tools/`
3. MCP 是动态并入，不需要把所有 MCP 管理逻辑混进默认工具面

#### 2.1.2 subordinate / subagent 的实现方式

Agent Zero 的 subordinate 工具在：

- [_references/opensource/agent-zero/python/tools/call_subordinate.py](/Users/connorlu/.codex/worktrees/47dc/OctoAgent/_references/opensource/agent-zero/python/tools/call_subordinate.py)

它的实现非常直接：

- 当前 agent 的 `data` 上维护一个 subordinate 实例
- 第一次需要时才初始化 subordinate
- subordinate 复用同一条 message loop
- 工具本身就是“通信 / 委派”的一部分，而不是一个大型 control-plane surface

另外，Agent Zero 的 agent/profile 组织在：

- [_references/opensource/agent-zero/python/helpers/subagents.py](/Users/connorlu/.codex/worktrees/47dc/OctoAgent/_references/opensource/agent-zero/python/helpers/subagents.py)

这里可以看到：

- default / user / project 三层 agent 配置目录
- agent profile 的覆盖与合并是文件系统层完成的
- subordinate 的 prompt/profile/tool 覆盖是 agent 目录结构的一部分

#### 2.1.3 Search / MCP 在源码里的位置

Search 工具：

- [_references/opensource/agent-zero/python/tools/search_engine.py](/Users/connorlu/.codex/worktrees/47dc/OctoAgent/_references/opensource/agent-zero/python/tools/search_engine.py)

实现上它是一个单一宽工具：

- 对外叫 `search_engine`
- 内部用 `searxng`
- 返回标题、链接、摘要拼接结果

MCP 动态接入：

- [_references/opensource/agent-zero/python/helpers/mcp_handler.py](/Users/connorlu/.codex/worktrees/47dc/OctoAgent/_references/opensource/agent-zero/python/helpers/mcp_handler.py)
- [_references/opensource/agent-zero/docs/developer/mcp-configuration.md](/Users/connorlu/.codex/worktrees/47dc/OctoAgent/_references/opensource/agent-zero/docs/developer/mcp-configuration.md)

源码上很清楚：

- MCP server 配置是 settings 中的独立项
- tool discovery 后，把工具信息注入 prompt
- 运行时由 `mcp_handler` 把 tool call 转发给 MCP server

#### 2.1.4 对我们的启发

从源码上看，Agent Zero 不是“能力少”，而是：

1. 默认工具入口少
2. 工具实现按文件目录拆开
3. subordinate / MCP 是能力扩展层，不和普通解题工具混在一个超大注册器里
4. 它允许 agent 自己通过 terminal/code/自定义工具扩展能力，而不是一开始把所有治理/管理面都默认挂出来

### 2.2 OpenClaw：工具很多，但默认暴露面强依赖 profile / allow-deny / session-tool 分层

#### 2.2.1 profile / allow / deny / subagent override 是配置状态机的一部分

OpenClaw 的这层不是文档描述，而是源码结构里已经建模出来了：

- [_references/opensource/openclaw/src/plugins/config-state.ts](/Users/connorlu/.codex/worktrees/47dc/OctoAgent/_references/opensource/openclaw/src/plugins/config-state.ts)

这里直接定义了：

- `allow`
- `deny`
- `entries`
- `entries[*].subagent.allowModelOverride`
- `entries[*].subagent.allowedModels`

也就是说，OpenClaw 的能力分层不是 prompt 上的软约定，而是配置状态模型里的正式字段。

#### 2.2.2 session / subagent 工具是显式的一组，不混在普通求解面里

OpenClaw 对 session 工具的设计在：

- [_references/opensource/openclaw/docs/concepts/session-tool.md](/Users/connorlu/.codex/worktrees/47dc/OctoAgent/_references/opensource/openclaw/docs/concepts/session-tool.md)
- [_references/opensource/openclaw/docs/tools/subagents.md](/Users/connorlu/.codex/worktrees/47dc/OctoAgent/_references/opensource/openclaw/docs/tools/subagents.md)

关键实现语义非常清楚：

- `sessions_send`
- `sessions_spawn`
- `session_status`

并且：

- sub-agent 默认是 **full tool set minus session tools**
- sub-agent 不允许继续 `sessions_spawn`
- depth-1 orchestrator 和 depth-2 leaf worker 的权限不同
- session tools visibility 也可以再限制

这说明 OpenClaw 的关键点不是“完全没有 session tools”，而是：

**它把 session/subagent 能力当成显式独立 surface，而不是默认解题工具的一部分。**

#### 2.2.3 tools profile / group 的使用方式

虽然 OpenClaw 的完整核心实现散落在多个模块，但从源码与文档组合起来已经很明确：

- sandbox 默认 allow/deny 在 `README.md` 中给出
- channel/group 里直接用 `allow: ["group:messaging", "group:sessions"]` / `deny: [...]`
- UI 也把 `sessions_send / sessions_spawn / session_status` 当成明确的独立工具项

关键证据：

- [_references/opensource/openclaw/README.md](/Users/connorlu/.codex/worktrees/47dc/OctoAgent/_references/opensource/openclaw/README.md#L336)
- [_references/opensource/openclaw/docs/channels/groups.md](/Users/connorlu/.codex/worktrees/47dc/OctoAgent/_references/opensource/openclaw/docs/channels/groups.md#L87)
- [_references/opensource/openclaw/ui/src/ui/views/agents-utils.ts](/Users/connorlu/.codex/worktrees/47dc/OctoAgent/_references/opensource/openclaw/ui/src/ui/views/agents-utils.ts#L74)

#### 2.2.4 prompt 也按 run mode 减法

OpenClaw 在：

- [_references/opensource/openclaw/docs/concepts/system-prompt.md](/Users/connorlu/.codex/worktrees/47dc/OctoAgent/_references/opensource/openclaw/docs/concepts/system-prompt.md#L80)

直接说明了：

- sub-agent 只注入 `AGENTS.md` 和 `TOOLS.md`
- 其它 bootstrap 文件会被过滤掉
- `Current Date & Time` 只放 timezone，精确时间走 `session_status`

这和它的 tool surface 分层是配套的：

- prompt 变小
- subagent surface 变小
- session tool 单独成组

#### 2.2.5 对我们的启发

OpenClaw 不是“工具少”，而是：

1. 工具有分组
2. 暴露面靠 allow/deny/profile 决定
3. session / subagent 工具单独成组
4. subagent 的 prompt 和 tool surface 都做减法

### 2.3 对我们真正有价值的启发

Agent Zero 和 OpenClaw 的共同点，不是“少工具”这四个字，而是：

1. 默认工具面更克制
2. 工具实现与工具暴露是两层
3. subordinate / subagent 不和主会话共享同一套宽 surface
4. prompt / context 也会按 run mode 分层
5. MCP / session / admin 能力存在，但不是默认 conversational surface 的常驻成员

## 3. OctoAgent 当前的问题

当前文件：

- [`octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`](../../octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py)

### 3.1 一个类承担了过多职责

当前 `CapabilityPackService` 同时在做：

- pack lifecycle
- builtin tool 注册
- worker profile 构造
- bootstrap template 构造
- tool context 装配
- profile-first 解析
- builtin tool 实现
- scope / profile 过滤

典型入口函数：

- `startup()`
- `refresh()`
- `build_tool_context()`
- `resolve_profile_first_tools()`
- `_register_builtin_tools()`
- `_build_worker_profiles()`
- `_build_bootstrap_templates()`
- `_filter_pack_for_scope()`

这已经不是“单个 service 大了一点”，而是多个子系统被揉进了一个类。

### 3.2 默认 `general` profile 的工具组过宽

当前 `_build_worker_profiles()` 把 `"general"` 的默认工具组定义成：

- `project`
- `artifact`
- `document`
- `session`
- `filesystem`
- `terminal`
- `network`
- `browser`
- `memory`
- `supervision`
- `delegation`
- `mcp`
- `skills`
- `runtime`
- `automation`
- `media`
- `config`
- `setup`
- `behavior`

这意味着默认 conversational agent 一开始就能看到：

- 解题工具
- 编排工具
- 控制台/管理工具
- 配置工具
- setup 工具
- MCP 工具

这会直接增加模型的选择熵。

### 3.3 profile-first 的“核心工具”其实已经把编排偏好注入进来了

当前 `_profile_first_core_tool_names()` 包含：

- `project.inspect`
- `task.inspect`
- `artifact.list`
- `sessions.list`
- `session.status`
- `workers.review`
- `subagents.spawn`
- `subagents.list`
- `subagents.steer`
- `mcp.tools.list`

这里的问题不是这些工具不该存在，而是：

- 这些工具很多都不是“当前问题直接求解”必需工具
- 但它们会在一个很早的阶段就进入模型视野
- 于是模型更容易先编排、先检查、先管理，而不是先做题

### 3.4 `_register_builtin_tools()` 是一个巨型闭包注册器

这意味着：

- 工具实现无法按 domain 独立测试
- 很难只替换某一组工具的注册逻辑
- 很难让不同 surface 复用不同 registry
- 任何重构都会先被这个函数阻塞

## 4. 重构原则

### 4.1 “少而宽”发生在 surface 层，不发生在 contract 层

这是本方案最重要的原则。

不建议为了“少工具”把这些合并成超级工具：

- `filesystem.list_dir`
- `filesystem.read_text`
- `filesystem.write_text`
- `terminal.exec`
- `web.search`
- `web.fetch`

因为它们在 contract / side-effect / governance / audit 层面是不同的。

正确做法是：

- contract 级工具继续保持清晰分离
- 模型默认看到的工具面变少
- 同一回合只挂载当前真正必要的那一小层 surface

### 4.2 区分“求解工具面”和“管理工具面”

OctoAgent 现在最大的坏味道不是工具多，而是：

- 解题能力
- 编排能力
- 配置/管理能力

同时暴露给了默认 conversational run。

后续必须拆开。

### 4.3 让 surface 由 turn semantics 决定，而不是由一个宽 profile 一步到位决定

现在的思路更接近：

`worker profile -> default tool groups -> big pack`

建议改成：

`turn semantics -> surface kind -> group subset -> policy/profile gate -> mounted tools`

这里的 `turn semantics` 至少应看：

- `session_owner_profile_id`
- `turn_executor_kind`
- `delegation_target_profile_id`
- 当前 objective
- 当前 UI surface（chat / settings / advanced / work）
- 当前是不是 setup/config flow

## 5. 建议的新架构

### 5.1 顶层类设计

#### A. `CapabilityRegistryService`

职责：

- 管理 builtin / MCP / plugin tool registry
- 生成 pack snapshot
- 维护工具定义与 availability
- 不负责 turn-specific surface 决策

建议函数：

- `refresh_registry() -> CapabilityRegistrySnapshot`
- `list_builtin_tools() -> list[BundledToolDefinition]`
- `list_plugin_tools() -> list[BundledToolDefinition]`
- `list_worker_profiles() -> list[WorkerCapabilityProfile]`
- `list_bootstrap_templates() -> list[WorkerBootstrapFile]`

#### B. `CapabilitySurfaceResolver`

职责：

- 根据当前 turn 决定模型应该看到哪些工具
- 把 registry snapshot 解析成 `EffectiveToolSurface`
- 负责默认 surface 的收敛

建议函数：

- `resolve_surface(context) -> ToolSurfaceKind`
- `build_conversation_core_surface(context, registry) -> EffectiveToolSurface`
- `build_delegation_runtime_surface(context, registry) -> EffectiveToolSurface`
- `build_admin_control_surface(context, registry) -> EffectiveToolSurface`
- `apply_profile_and_policy(surface, context) -> EffectiveToolSurface`

#### C. `WorkerBindingService`

职责：

- 解析 owner / worker / profile binding
- 解析 bootstrap template
- 解析 profile-first 的 profile 约束
- 不负责 builtin tool 实现

建议函数：

- `resolve_owner_binding(...)`
- `resolve_worker_binding(...)`
- `resolve_bootstrap_templates(...)`
- `resolve_profile_constraints(...)`

#### D. `BuiltinToolRegistrar`

职责：

- 只负责把 builtin tool 按 domain 注册进 registry
- 每个 domain 独立文件

建议拆分文件：

- `filesystem_tools.py`
- `runtime_tools.py`
- `web_tools.py`
- `browser_tools.py`
- `memory_tools.py`
- `delegation_tools.py`
- `session_tools.py`
- `config_tools.py`
- `setup_tools.py`
- `mcp_tools.py`
- `media_tools.py`

### 5.2 新的 surface 切分

#### Surface 1：`conversation-core`

默认对话求解面。

建议默认包含：

- `project.inspect`
- `filesystem.list_dir`
- `filesystem.read_text`
- `filesystem.write_text`
- `terminal.exec`
- `runtime.now`
- `web.search`
- `web.fetch`
- `memory.search`
- `memory.read`
- `memory.write`

默认不包含：

- `subagents.spawn`
- `workers.review`
- `sessions.list`
- `session.status`
- `config.*`
- `setup.*`
- `mcp.*`
- `browser.*`

`browser.*` 只在明确网页交互任务下额外挂入。

#### Surface 2：`delegation-runtime`

只在当前回合真的进入编排或分工时出现。

建议包含：

- `workers.review`
- `subagents.spawn`
- `subagents.list`
- `subagents.steer`
- `work.split`
- `work.merge`

注意：

- 主 Agent 可以拿到这层
- worker 可以拿到 `subagent` 相关子集
- worker 不应再拿到“转交另一个 worker”的能力

#### Surface 3：`admin-control`

只用于 setup / settings / advanced / operator flow。

建议包含：

- `agents.list`
- `sessions.list`
- `session.status`
- `config.inspect`
- `config.add_provider`
- `config.set_model_alias`
- `config.sync`
- `setup.review`
- `setup.quick_connect`
- `mcp.*`

这层不应该默认出现在普通 Chat 求解里。

### 5.3 profile-first 的改造建议

当前 `profile-first` 的问题是：

- 它太早注入了编排和管理工具
- 把“当前 owner/profile 的选择”放大成了“默认就该先管理系统”

建议改成：

#### 旧逻辑

`profile-first core = inspect + sessions + workers + subagents + mcp`

#### 新逻辑

`profile-first core = conversation-core 中最小必要子集`

建议最小集合：

- `project.inspect`
- `filesystem.list_dir`
- `filesystem.read_text`
- `runtime.now`
- `web.search`
- `memory.search`

然后：

- 只有当 objective 明显需要 delegation 时，才升级到 `delegation-runtime`
- 只有在设置/治理页面或 setup flow 中，才升级到 `admin-control`

## 6. 对现有工具的处理建议

### 6.1 不建议砍掉实现，只建议移出默认面

保留在 registry 里，但从默认 conversational surface 移出的工具包括：

- `agents.list`
- `sessions.list`
- `session.status`
- `workers.review`
- `subagents.spawn`
- `subagents.list`
- `subagents.steer`
- `config.*`
- `setup.*`
- `mcp.*`
- `automation.*`
- `media.*`
- `behavior.*`
- 大多数 `browser.*`

### 6.2 哪些继续保留在默认面

优先保留：

- `project.inspect`
- `filesystem.*`
- `terminal.exec`
- `runtime.now`
- `web.search`
- `web.fetch`
- `memory.search/read/write`

这组已经足够覆盖大量真实求解场景：

- README / 配置 / 代码 / 文档读取
- 轻量命令验证
- 时间感知
- 实时网页查询
- 简单事实召回与写回

## 7. 与 Agent Zero / OpenClaw 的异同

### 7.1 和 Agent Zero / OpenClaw 的共同点

- 默认面都应该克制
- subordinate / sub-agent 不该拿和主会话一样宽的 surface
- project / memory / secrets / files 应该有明确边界
- prompt/context 也要按 run mode 做减法
- 工具实现目录和工具暴露逻辑不该揉成一个单体类

### 7.2 和 Agent Zero 的差异

Agent Zero 更依赖：

- terminal
- code generation
- “自己造工具”
- 动态从 `python/tools/` 和 MCP runtime 拿能力

OctoAgent 则更强调：

- control plane
- approvals
- event / artifact / work truth
- governed tool contracts

所以我们不适合完全复制 Agent Zero 的“默认极少工具 + 其它全靠代码制造”模式。我们仍需要清晰的治理工具体系和 contract 级 schema。

### 7.3 和 OpenClaw 的差异

OpenClaw 已经把 profile / group / allow-deny / subagent visibility 做得很强，而且这些都已经进入配置状态模型，不只是 prompt 文案。

OctoAgent 当前更强的是：

- durability
- 审批与两阶段副作用
- work / event / artifact 真相链

但我们现在更弱的是：

- surface 分层
- session / subagent 工具与普通解题工具的隔离
- prompt mode 与 tool surface 的协同减法

我们的目标不应是复制 OpenClaw 的具体工具名字，而是学习它的三个结构点：

1. profile 是 base allowlist
2. group / surface 是能力分层单位
3. sub-agent 有单独的 prompt 和 tool surface

## 8. 推荐的迁移顺序

### Phase 1：抽注册器，不改行为

目标：

- 先把 `_register_builtin_tools()` 拆到 domain files
- 保持现有行为不变

输出：

- `BuiltinToolRegistrar`
- `*_tools.py` 系列模块

### Phase 2：引入 `CapabilitySurfaceResolver`

目标：

- 保留现有 registry
- 但开始用显式 surface 解析替代“宽 profile -> 大 pack”

输出：

- `ToolSurfaceKind`
- `EffectiveToolSurface`
- `CapabilitySurfaceResolver`

### Phase 3：收窄默认 conversational surface

目标：

- 默认只挂 conversation-core
- 浏览器、委派、session、config、setup、mcp 不再默认注入

输出：

- 新版默认 `general` profile
- 新的 profile-first core

### Phase 4：把 admin / operator 工具彻底剥离

目标：

- `settings / advanced / setup` 才能看到管理面工具
- 普通聊天求解面完全不再混入管理动作

### Phase 5：删旧兼容层

目标：

- 移除旧的 `_profile_first_core_tool_names()` 宽注入逻辑
- 清理旧的 “default_tool_groups = everything” 心智

## 9. 我建议的第一批落地范围

如果要先做一版低风险改造，我建议先只做：

1. 把 builtin tool 注册按 domain 拆文件
2. 把默认 `general` conversational surface 收窄为：
   - `project`
   - `filesystem`
   - `terminal`
   - `network`
   - `runtime`
   - `memory`
3. 把 `browser / delegation / session / mcp / config / setup / behavior / automation / media` 从默认 conversational surface 拿掉

这一步就已经能明显减少：

- 模型选择困难
- 简单问题过度编排
- profile-first 过早进入管理态

## 10. 一句话结论

`capability_pack.py` 的问题不是“文件太长”，而是：

- registry
- surface 解析
- worker/profile 绑定
- builtin tool 实现
- setup/config/admin 能力

全都混在一个类里了。

正确方向不是粗暴删工具，而是：

- 保留 contract 级工具定义
- 把默认暴露面收窄
- 显式分离 `conversation-core / delegation-runtime / admin-control`
- 学 Agent Zero 的“默认面克制”
- 学 OpenClaw 的“profile/group/allow-deny/subagent-surface 分层”
