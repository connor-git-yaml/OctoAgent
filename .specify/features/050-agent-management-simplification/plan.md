---
feature_id: "050"
title: "Agent Management Simplification"
created: "2026-03-14"
updated: "2026-03-14"
status: "Draft"
---

# Plan - Feature 050

## 1. 目标

把 `Agents` 页面从“模板/运行态/技术字段混合工作台”重构成普通用户可理解的 Agent 管理中心：

- 当前项目有一个清晰的主 Agent
- 当前项目其他 Agent 以列表管理
- 内置 Agent 只作为创建模板出现
- 编辑体验以结构化控件为主
- 高级技术字段退到高级区

## 2. 非目标

- 不重做整个 `worker_profiles` 持久化体系
- 不把 revision/runtime rail 全部移出系统
- 不在本 Feature 内引入模板 marketplace
- 不在普通用户路径中保留完整 control-plane 术语

## 3. 设计原则

### 3.1 Project 优先

用户先理解“当前项目有哪些 Agent”，再理解模板和运行态。  
`Agents` 页面默认只看当前项目，不做系统级 profile 控制台。

### 3.2 主 Agent / 已创建 Agent / 模板严格分层

- 主 Agent 是项目默认 Agent
- 已创建 Agent 是用户拥有对象
- 模板只是创建起点

这三类对象不能继续混在一个列表或工作台里。

### 3.3 结构化选择优先于自由文本

普通用户不应被迫输入原始 tool group 或 tool name。  
除名称与 Persona 外，核心配置优先采用：

- 单选
- 多选
- 搜索选择器
- checkbox / chips

### 3.4 高级信息必须降级

`runtime kinds / policy refs / tags / metadata` 等内容只在高级区出现，不占普通用户默认路径。

### 3.5 不新造平行后端真相

本 Feature 首版优先复用现有：

- `worker_profiles`
- `projects.default_agent_profile_id`
- 既有 capability binding

如需更清晰的前端对象，则在 domain adapter/view-model 层收口。

## 4. 参考证据

- `docs/blueprint.md`
- `octoagent/frontend/src/pages/AgentCenter.tsx`
- `octoagent/frontend/src/domains/agents/TemplateWorkspaceSection.tsx`
- `octoagent/frontend/src/pages/ChatWorkbench.tsx`
- `octoagent/packages/core/src/octoagent/core/models/agent_context.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- `_references/opensource/agent-zero/docs/guides/projects.md`
- `_references/opensource/openclaw/docs/channels/channel-routing.md`

## 5. 实施切片

### Slice A - 对象模型与文案冻结

- 在 `domains/agents` 建立 `MainAgent / ProjectAgent / BuiltinAgentTemplate` 视图模型
- 冻结用户语言：主 Agent、已创建 Agent、模板、能力绑定
- 清理首屏技术术语

### Slice B - 首页改成 Agent 列表

- `Agents` 默认页显示当前项目主 Agent + 已创建 Agent 列表
- 模板从默认首屏移除
- 每个 Agent 卡片提供基础摘要和动作

### Slice C - 新建 Agent 流程

- 新建时再展示模板库
- 支持模板起点与空白起点
- 创建成功后回到当前项目 Agent 列表

### Slice D - 编辑页结构化控件

- 模型别名改为单选/下拉
- 默认工具组改为多选
- 固定工具改为搜索多选
- MCP / Skill 绑定改为能力选择区
- 高级字段折叠收纳

### Slice E - 默认主 Agent 与迁移边界

- 处理 builtin 默认 Agent 的迁移
- 处理当前项目主 Agent 的绑定和删除限制
- 处理项目切换后的列表过滤与默认 Agent 解析

### Slice F - 测试与回归

- 首页列表、模板创建、编辑页结构化控件、删除边界、项目切换
- 保持当前 chat/work 默认 Agent 解析链不回归

## 6. 风险

- 如果不新增前端视图模型，只改 DOM 和文案，后续仍会继续泄漏底层字段。
- 如果不处理 builtin 默认 Agent 的迁移，就无法兑现“主 Agent 可编辑”的产品承诺。
- 如果把 Provider / MCP / Skill 绑定独立成另一个控制台，用户仍会继续在多个页面间来回跳。

## 7. 验证方式

- `Agents` 首页渲染与交互测试
- 创建流测试
- 编辑页表单测试
- 项目切换 / 默认 Agent 绑定测试
- 关键黄金路径 smoke：`/agents` + `/chat`
