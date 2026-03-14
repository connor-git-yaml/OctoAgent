# Data Model - Feature 050 Agent Management Simplification

## 1. 目标

本 Feature 不优先引入新的持久化实体，而是在现有 `WorkerProfile + Project.default_agent_profile_id` 之上建立面向 UI 的产品对象。

## 2. UI 视图模型

### 2.1 MainAgent

| 字段 | 来源 | 说明 |
|------|------|------|
| `profileId` | `Project.default_agent_profile_id` + `WorkerProfile.profile_id` | 当前项目默认 Agent 的真实 ID |
| `name` | `WorkerProfile.name` | 主 Agent 名称 |
| `persona` | `WorkerProfile.summary` 或 persona 摘要映射 | 对用户可见的用途说明 |
| `projectId` | `WorkerProfile.project_id` | 所属项目 |
| `modelAlias` | `WorkerProfile.model_alias` | 使用的模型别名 |
| `defaultToolGroups` | `WorkerProfile.default_tool_groups` | 默认工具组 |
| `selectedTools` | `WorkerProfile.selected_tools` | 固定工具 |
| `capabilityBindings` | 现有 capability selection 映射 | MCP / Skill 能力允许范围 |
| `isEditable` | 派生 | 主 Agent 始终可编辑 |
| `isDeletable` | 固定 `false` | 主 Agent 不提供删除 |

### 2.2 ProjectAgent

| 字段 | 来源 | 说明 |
|------|------|------|
| `profileId` | `WorkerProfile.profile_id` | 已创建 Agent 的真实 ID |
| `name` | `WorkerProfile.name` | 列表显示名称 |
| `persona` | `WorkerProfile.summary` | 用途说明 |
| `projectId` | `WorkerProfile.project_id` | 所属项目 |
| `modelAlias` | `WorkerProfile.model_alias` | 当前模型别名 |
| `defaultToolGroupCount` | `default_tool_groups.length` | 摘要信息 |
| `selectedToolCount` | `selected_tools.length` | 摘要信息 |
| `isMainAgent` | 派生 | 是否等于项目默认 Agent |
| `isDeletable` | `!isMainAgent` | 非主 Agent 才可删除 |

### 2.3 BuiltinAgentTemplate

| 字段 | 来源 | 说明 |
|------|------|------|
| `templateId` | `WorkerProfile.profile_id` | 内置模板标识 |
| `name` | `WorkerProfile.name` | 模板名称 |
| `summary` | `WorkerProfile.summary` | 模板用途说明 |
| `baseArchetype` | `WorkerProfile.base_archetype` | 创建时的默认起点 |
| `suggestedModelAlias` | `WorkerProfile.model_alias` | 默认模型 |
| `suggestedToolGroups` | `WorkerProfile.default_tool_groups` | 默认工具组 |
| `suggestedTools` | `WorkerProfile.selected_tools` | 默认固定工具 |

### 2.4 AgentEditorDraft

| 字段 | 类型 | 说明 |
|------|------|------|
| `profileId` | `string` | 编辑已有 Agent 时存在；新建时为空 |
| `name` | `string` | Agent 名称 |
| `persona` | `string` | 用途 / Persona |
| `projectId` | `string` | 所属项目 |
| `modelAlias` | `string` | 单选/下拉 |
| `defaultToolGroups` | `string[]` | 多选 |
| `selectedTools` | `string[]` | 搜索多选 |
| `capabilityBindings` | `string[]` 或更细粒度结构 | 允许使用的能力 |
| `advanced.runtimeKinds` | `string[]` | 高级字段 |
| `advanced.policyRefs` | `string[]` | 高级字段 |
| `advanced.tags` | `string[]` | 高级字段 |
| `advanced.metadata` | `Record<string, unknown>` | 高级字段 |

## 3. 关键派生规则

### 3.1 当前项目 Agent 列表

从 `worker_profiles` 中筛出：

- `origin_kind != builtin`
- `scope = project`
- `project_id = currentProjectId`

按此结果构造 `ProjectAgent[]`。

### 3.2 主 Agent 解析

`MainAgent.profileId` 来自：

- `Project.default_agent_profile_id`

然后在当前 `worker_profiles` 中查找对应 profile，构造 `MainAgent`。

### 3.3 模板列表

模板只从以下对象生成：

- `origin_kind = builtin`

并且只在“新建 Agent”流中显示，不进入默认列表。

## 4. 迁移状态

### 4.1 Builtin 默认 Agent

如果 `Project.default_agent_profile_id` 指向 builtin template：

- 页面将其标记为“需要建立项目主 Agent”
- 首次编辑或点击迁移动作时：
  - 基于该 builtin template 创建 project-scoped profile
  - 绑定为新的 `default_agent_profile_id`

### 4.2 删除边界

- 主 Agent：禁止删除
- 非主 Agent：允许删除/归档
- 正在被活跃 work 使用的 Agent：先提示风险，必要时改为归档

## 5. 与现有后端模型的关系

本 Feature 首版与后端模型关系如下：

- **保持不变**：`WorkerProfile`、`Project.default_agent_profile_id`、既有 clone/apply/bind_default/archive 动作
- **新增在前端**：面向普通用户的对象映射与表单草稿模型
- **可选后续优化**：若 UI adapter 仍过重，再考虑补一个更友好的 canonical `agent-management` 资源
