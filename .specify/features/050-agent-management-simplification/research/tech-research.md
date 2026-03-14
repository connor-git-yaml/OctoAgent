# 技术调研：Agent Management Simplification

**特性分支**: `codex/050-agent-management-simplification`  
**日期**: 2026-03-14  
**范围**: `Agents` 页面前端组织、现有 `worker_profiles` 模型能力、项目默认 Agent 绑定、模板/实例分层改造路径  
**输入**: 当前 `AgentCenter / TemplateWorkspaceSection / ChatWorkbench`、`WorkerProfile / Project` 模型、`control_plane` 的 clone/apply/bind_default 逻辑

## 1. 当前实现现状

### 1.1 前端页面结构

当前 `AgentCenter` 主要围绕四个 workspace 组织：

- `Butler 设置`
- `Worker 模板`
- `运行中的 Worker`
- `Providers`

默认入口是 `templates`，而不是“当前项目已有 Agent 列表”。  
这意味着用户进入 `Agents` 时，首先看到的是模板工作台，而不是自己拥有的 Agent。

### 1.2 模板浏览与编辑被塞进同一页

`TemplateWorkspaceSection` 目前在同一屏里同时承载：

- 内置模板列表
- 已保存模板列表
- 模板编辑器
- 版本/运行时轨道

其结果是：

- 对象层级不清晰
- 页面对普通用户过重
- 运行态与编辑态耦合过深

### 1.3 编辑表单暴露了过多底层字段

当前编辑区域直接暴露了大量底层字段：

- `scope`
- `base_archetype`
- `tool_profile`
- `default_tool_groups`
- `selected_tools`
- `runtime_kinds`
- `policy_refs`
- `tags`

其中不少字段仍以 textarea + 每行一个值 的形式输入，这在技术上灵活，但在产品上不适合普通用户。

## 2. 现有后端模型已经具备哪些能力

### 2.1 `WorkerProfile` 已经支持项目归属

`WorkerProfile` 当前已经有：

- `profile_id`
- `scope`
- `project_id`
- `name`
- `summary`
- `model_alias`
- `default_tool_groups`
- `selected_tools`
- `origin_kind`
- `active_revision / draft_revision`

这说明“每个 Agent 归属某个项目”在数据层已经成立，不需要重新发明持久化对象。

### 2.2 `Project` 已经有默认 Agent 绑定

`Project.default_agent_profile_id` 已经表达了“当前项目默认 Agent 是谁”。  
`control_plane._bind_worker_profile_as_default()` 也已经负责把某个 project-scoped profile 绑定为该项目默认 Agent。

因此，“每个项目有一个主 Agent”在技术上是可表达的。

### 2.3 内置模板与用户创建对象目前共享同一资源集合

当前 `worker_profiles` 资源里同时包含：

- 内置对象：`origin_kind = builtin`
- 用户创建对象：`origin_kind = custom / cloned / extracted`

这也是当前 UI 容易混淆的根源之一。  
虽然这在存储层可以接受，但在 UI 层必须做显式分层。

### 2.4 Clone 流程会自然产生“近似重复对象”

`worker_profile.clone` 会基于现有 profile 复制出一个新草稿：

- 内置模板默认会变成一个新的 `cloned` profile
- `profile_id` 会按名字 slug 自动生成
- 若冲突则追加后缀

这保证了存储唯一性，但不会阻止“显示名称几乎一样、描述几乎一样、只差少量工具”的对象继续出现。

## 3. 技术结论

### 3.1 第一阶段不必强制新增新的持久化实体

从实现成本和兼容性看，050 第一阶段不必立刻发明新的后端存储模型。  
现有 `worker_profiles + projects.default_agent_profile_id` 已足够表达：

- 当前项目主 Agent
- 当前项目其他 Agent
- 内置模板

真正缺的是：

- 前端 view-model 分层
- 更友好的信息架构
- 更克制的字段暴露策略

### 3.2 需要新增明确的前端视图模型

建议在 `domains/agents` 新增明确的 adapter / view-model：

- `BuiltinAgentTemplate`
- `ProjectAgent`
- `MainAgentSummary`
- `AgentEditorDraft`

而不是让页面继续直接消费原始 `WorkerProfileItem` 并到处判断 `origin_kind / scope / default_profile_id`。

### 3.3 主 Agent 应定义为“当前项目默认 Agent”

从现有模型出发，最稳定的产品定义是：

- **主 Agent** = `currentProject.default_agent_profile_id`
- **其他 Agent** = 当前项目内其他非内置 `worker_profiles`
- **模板** = 内置 `worker_profiles`

这比“整个系统只有一个主 Agent”更符合已有 `Project` 模型，也更符合用户的项目隔离心智。

### 3.4 需要处理“默认 Agent 仍是内置模板”的迁移边界

当前部分项目或测试数据中，默认 Agent 可能仍然指向内置对象，例如 `singleton:general`。  
如果 050 要把主 Agent 定义成“用户可直接编辑保存、不可删除的真实对象”，就必须补一条迁移路径：

- 当当前项目默认 Agent 仍是内置模板时
- 首次编辑或显式点击“建立主 Agent”时
- 系统应基于该内置模板创建一个 project-scoped 的自有 profile
- 然后自动绑定为 `default_agent_profile_id`

否则“主 Agent 可编辑”这条产品语义会被现有 builtin 只读规则打断。

### 3.5 编辑体验可以复用现有字段，但应换输入控件

现有模型字段足够承接大多数编辑需求：

- `name` -> 文本输入
- `summary` -> Persona / 用途说明
- `model_alias` -> 单选 / 下拉
- `default_tool_groups` -> 多选
- `selected_tools` -> 搜索多选
- `project_id` -> 单选

而以下字段应默认降级到高级区：

- `runtime_kinds`
- `policy_refs`
- `tags`
- `metadata`

### 3.6 Provider / MCP / Skill 绑定不应再单独占据技术面板语义

当前 capability 绑定能力已经存在，因此不需要重造数据模型。  
更合理的方向是把它改成“这个 Agent 允许使用哪些能力”的普通编辑区块，而不是继续使用“Provider 白名单”“Capability pack”等技术术语作为用户语言。

## 4. 推荐的代码切片

### Slice A：对象层适配

- 在 `domains/agents` 内引入 `agentManagementData.ts` 一类的 adapter
- 把原始 `worker_profiles` 转成主 Agent / 已创建 Agent / 模板三类视图对象

### Slice B：列表首页

- 新的 `Agents` 首页默认显示当前项目主 Agent + 已创建 Agent 列表
- 模板不再占据首屏主体

### Slice C：创建流

- 新建 Agent 时才打开模板选择器
- 从模板创建不再直接暴露为“复制模板”

### Slice D：编辑页重构

- 用结构化控件替换 textarea
- Provider/MCP/Skill 绑定纳入普通编辑流
- 高级字段折叠收纳

### Slice E：迁移与默认绑定

- 处理 builtin 默认 Agent 的迁移
- 处理项目切换后的主 Agent / 列表刷新
- 处理非主 Agent 的删除/归档边界

## 5. 风险

- 如果只改前端文案，不改对象分层，页面还是会继续混乱。
- 如果不处理 builtin 默认 Agent 的迁移，就无法真正兑现“主 Agent 可直接编辑”的产品承诺。
- 如果仍让页面直接消费原始 `worker_profiles` 结构，后续功能继续增长时会再次把底层语义泄漏到 UI。

## 6. 结论

050 在技术上是可行的，而且不需要先做大规模 backend 重构。  
现有数据模型已经足够支撑“项目内主 Agent + 已创建 Agent + 模板创建流”这套产品心智；需要重整的是：

1. 前端 view-model
2. 页面信息架构
3. 编辑控件表达
4. builtin 默认 Agent 的迁移路径
