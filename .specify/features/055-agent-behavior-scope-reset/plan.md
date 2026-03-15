---
feature_id: "055"
title: "Agent Behavior Scope Reset & Behavior Center"
status: "Implemented"
created: "2026-03-15"
updated: "2026-03-15"
---

# Implementation Plan

当前状态：**Implemented**

说明：

- Slice A-F 已完成并进入代码主线
- 本文档保留为实现计划与边界回写，不再代表未开始的草案状态

## 1. 目标

把当前 `BehaviorWorkspace` 从 `system/project` 双层模型升级成统一的多 Agent 行为工作区，并把 Web 入口从 `Settings` 迁到 `Agents`。同时明确 behavior / memory / secrets / workspace 的边界，把当前 project 与关键 md 路径显式注入运行时上下文，并把初始化模板与默认会话 Agent 的 bootstrap 访谈纳入正式主链。模板正文骨架应参考 Agent Zero / OpenClaw，但最终按 OctoAgent 的 behavior / memory / secrets 边界落地。

## 2. 实施切片

### Slice A - 行为作用域与目录重构

目标：

- 扩展行为作用域枚举与模型
- 支持全局行为目录 + project-centered 目录：
  - `behavior/system`
  - `behavior/agents/<agent-slug>`
  - `projects/<project-slug>/behavior`
  - `projects/<project-slug>/behavior/agents/<agent-slug>`

输出：

- 新的 `BehaviorWorkspaceScope`
- 新的路径解析器
- 文件归属矩阵与默认模板映射
- `project path manifest` 模型
- bootstrap 模板脚手架 contract
- 默认模板正文骨架合同

### Slice B - 运行时装配统一化

目标：

- 所有 agent runtime 共用同一条 behavior 装配链
- Butler 不再在行为文件层面成为特例
- 明确 `system -> agent -> project -> project-agent -> project-path-manifest -> runtime hints` 的 effective chain

输出：

- 统一装配函数
- agent session owner 驱动的行为包构造
- source chain / truncation / override metadata
- project/workspace/behavior roots 的显式注入
- handoff / subordinate continuation 可重用的 `project_path_manifest + source_chain` capsule
- facts / secrets / workspace roots 的显式访问提示

### Slice C - Agents 页升级为 Behavior Center

目标：

- 在 `Agents` 页中展示：
  - Shared Files
  - Agent Private
  - Project Shared
  - Project-Agent Override
  - Effective View

输出：

- `Agents` 页新信息架构
- 针对某个 agent 的文件归属说明
- 打开文件、查看来源链、后续 edit/proposal 的入口预留

### Slice D - Settings 页移除无效 behavior 管理区

目标：

- 删除 `Settings` 里当前只读、无操作闭环的 behavior section
- 保留真正的 runtime/provider/memory/channel 配置

输出：

- `Settings` 结构收口
- 迁移提示：行为文件请去 `Agents`

### Slice E - CLI 与治理配套

目标：

- 让 CLI 也能按 agent/project scope 查看行为文件
- 为后续 proposal/apply 铺 scope-aware contract

输出：

- `octo behavior ls/show` 的 scope 扩展
- project / agent / project-agent 的路径说明
- 与 secret bindings metadata 的衔接说明
- proposal/apply 所需的 editability / review mode 元数据输出

### Slice F - Bootstrap 与用户画像初始化

目标：

- 为 shared / agent-private / project-shared 生成最小模板脚手架
- 让默认会话 Agent 在首次进入 project 时执行 bootstrap 访谈
- 明确用户事实、Agent 名称/性格、敏感信息分别落到 Memory / behavior proposal / secret bindings

输出：

- bootstrap 模板映射
- 默认会话 Agent 的 bootstrap questionnaire contract
- onboarding 与 behavior workspace 的衔接点
- bootstrap 结果到 Memory / behavior proposal / secret bindings 的路由规则

## 3. 技术设计要点

### 3.1 作用域模型

建议把当前：

- `SYSTEM`
- `PROJECT`

升级为：

- `SYSTEM_SHARED`
- `AGENT_PRIVATE`
- `PROJECT_SHARED`
- `PROJECT_AGENT`

同时保留 `visibility / share_with_workers / source_kind / truncation` 元数据。

### 3.2 文件归属策略

首批正式文件建议如下：

#### Shared

- `AGENTS.md`
- `USER.md`
- `TOOLS.md`
- `BOOTSTRAP.md`

#### Agent Private

- `IDENTITY.md`
- `SOUL.md`
- `HEARTBEAT.md`

#### Project Shared

- `PROJECT.md`
- `KNOWLEDGE.md`
- `USER.md`（project override）
- `TOOLS.md`（project override）
- `instructions/*.md`

说明：

- `MEMORY.md` 本轮移除，不作为默认 behavior 文件
- facts 必须通过 Memory 访问与存储，不得通过 markdown 文件伪装
- secrets 必须通过 project secret bindings 访问与配置，不得通过 markdown 文件保存
- 若后续需要“记忆规则文件”，必须以 `MEMORY_POLICY.md` 之类形式单独回归，而不是承担事实存储
- Behavior 文件负责规则，不负责事实仓库存储
- secrets 逻辑上 project-scoped，但不以明文 md 保存

#### Project-Agent

- `IDENTITY.md`
- `SOUL.md`
- `TOOLS.md`
- `PROJECT.md`

### 3.3 UI 信息架构

`Agents` 页建议采用：

1. Agent 列表
2. 选中 Agent 的 overview
3. Behavior 分栏：
   - 共享层
   - 私有层
   - 当前项目层
   - 当前项目内 override
   - Effective 合成视图
   - Project Path Manifest
   - Editability / Review Mode（可直接编辑、建议 proposal/apply、只读）

`Settings` 页中当前 behavior 卡片与 CLI snippet 需要删除。

### 3.4 上下文装配与 handoff

Agent 的运行时上下文不只需要 effective behavior 文本，还需要结构化的路径感知。

因此 Slice B 必须同时产出：

- `project_path_manifest`
- `effective_behavior_source_chain`
- `shared/project instructions summary`
- `storage_boundary_hints`

并让这些对象：

- 进入当前 agent runtime context
- 在需要继续委派时作为 handoff capsule 的一部分复用

这样 subordinate/worker 才不会继续靠猜测去定位 project 根目录和关键 md 文件。

### 3.5 Bootstrap 初始化约束

初始化必须同时完成两件事：

1. 生成最小可解释的 behavior 模板骨架  
2. 为默认会话 Agent 准备一轮 bootstrap 访谈

bootstrap 访谈需要覆盖：

- 用户如何称呼自己
- 默认会话 Agent 应该叫什么
- 默认会话 Agent 应该呈现什么性格/语气
- 用户的时区、地点、语言偏好
- 哪些长期信息应进入 Memory
- 哪些信息应被识别为敏感信息并导向 secret bindings

这意味着 055 不能只改目录和 UI，还要把现有 onboarding 的 provider/runtime 主链扩展成可以衔接 Agent bootstrap 的设计。

## 4. 风险与迁移注意事项

### 4.1 与 049 的兼容

Feature 049 目前把核心默认文件定义成：

- `AGENTS.md`
- `USER.md`
- `PROJECT.md`
- `TOOLS.md`

055 不是推翻 049，而是：

- 补充正式的 agent-private 层
- 把 project 层和共享层重新分开
- 把 UI 入口从 Settings 迁到 Agents
- 把 `MEMORY.md` 从默认 behavior 文件集合中拿掉
- 把 project 目录升级为真正的工作根，而不只是 behavior overlay 容器

### 4.2 迁移策略

建议迁移顺序：

1. 先扩作用域与路径解析，不改 UI
2. 再让 runtime 统一按新 scope 构造 effective chain
3. 再重做 Agents 页
4. 最后移除 Settings 里的 behavior 区

### 4.3 不要一次性把自动自改行为也塞进来

本轮重点是：

- 目录与所有权
- 运行时装配
- Web 入口迁移
- bootstrap 模板与问答路由
- 默认模板骨架合同
- proposal/apply 所需的元数据与入口预留

不是直接做 fully autonomous self-edit loop。

## 5. 完成判据

- `BehaviorWorkspace` 已支持四层 scope
- 任意 Agent 都能产出统一的 effective source chain
- 任意 Agent 都能拿到当前 project/workspace/behavior roots
- handoff 可以复用 project path manifest 与 effective source chain
- `Agents` 页可解释文件归属与影响范围
- `Settings` 页不再显示无效 behavior 管理区
