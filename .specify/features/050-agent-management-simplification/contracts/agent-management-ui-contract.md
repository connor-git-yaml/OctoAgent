# Contract - Agent Management UI Contract

## 1. 目标

定义 050 在不新造平行 backend truth 的前提下，如何把现有 canonical 资源映射成普通用户可理解的 Agent 管理 UI。

## 2. 输入资源

### 2.1 `worker_profiles`

用途：

- 内置模板来源
- 已创建 Agent 来源
- 主 Agent 编辑数据来源

关键字段：

- `profile_id`
- `origin_kind`
- `scope`
- `project_id`
- `name`
- `summary`
- `model_alias`
- `default_tool_groups`
- `selected_tools`

### 2.2 `projects`

用途：

- 当前项目识别
- 主 Agent 绑定来源

关键字段：

- `project_id`
- `name`
- `default_agent_profile_id`

### 2.3 capability / provider 相关资源

用途：

- 生成“能力绑定”编辑区
- 将 MCP / Skill 绑定转成普通用户语言

## 3. 视图映射规则

### 3.1 BuiltinAgentTemplate

判定条件：

- `origin_kind = builtin`

使用场景：

- 仅在“新建 Agent”流程中显示

### 3.2 ProjectAgent

判定条件：

- `origin_kind != builtin`
- `scope = project`
- `project_id = currentProjectId`

使用场景：

- 当前项目默认列表

### 3.3 MainAgent

判定条件：

- `profile_id = currentProject.default_agent_profile_id`

使用场景：

- 当前项目主 Agent 卡片
- 编辑页入口
- 删除动作禁用

## 4. 写操作与现有动作的关系

### 4.1 新建 Agent（从模板）

首选动作链：

1. 从 builtin template 生成 `AgentEditorDraft`
2. 用户填写最少必要字段
3. 使用现有 `worker_profile.apply` 保存为 project-scoped profile
4. 如用户指定为主 Agent，再调用 `worker_profile.bind_default`

### 4.2 编辑已有 Agent

动作：

- `worker_profile.apply`

### 4.3 设为主 Agent

动作：

- `worker_profile.bind_default`

### 4.4 删除普通 Agent

动作：

- 继续复用 `worker_profile.archive` 或等价删除/归档语义

约束：

- 主 Agent 禁止删除

## 5. 控件约定

### 5.1 默认编辑区

| 字段 | 控件 | 说明 |
|------|------|------|
| 名称 | 文本输入 | 必填 |
| Persona / 用途 | 文本域 | 必填 |
| 所属项目 | 单选 / 下拉 | 必填 |
| 使用的 LLM | 单选 / 下拉 | 必填 |
| 默认工具组 | 多选 | 推荐 chips / checkbox |
| 固定工具 | 搜索多选 | 推荐 searchable list |
| MCP / Skill 能力 | 多选 | 分组显示 |

### 5.2 高级区

以下字段默认折叠：

- `runtime kinds`
- `policy refs`
- `tags`
- `metadata`

## 6. 迁移规则

### 6.1 Builtin 默认 Agent 迁移

如果当前项目默认 Agent 仍指向 builtin template：

1. 页面提示“先建立项目主 Agent”
2. 基于 builtin template 创建 project-scoped profile
3. 调用 `bind_default`
4. 后续再进入正常编辑流

### 6.2 项目切换

项目切换时：

- 重新解析 `MainAgent`
- 重新构建 `ProjectAgent[]`
- 清空上一个项目的编辑草稿或提示保存

## 7. 非目标

- 不在本 Feature 内设计新的 revision browser contract
- 不在本 Feature 内新增系统级模板 marketplace
- 不在普通用户页面直接暴露 raw `worker_profile` 字段表
