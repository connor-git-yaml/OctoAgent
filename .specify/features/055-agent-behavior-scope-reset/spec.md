---
feature_id: "055"
title: "Agent Behavior Scope Reset & Behavior Center"
milestone: "M4"
status: "Implemented"
created: "2026-03-15"
updated: "2026-03-15"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §BehaviorWorkspace；Feature 049/050/051/053；Agent Zero projects/prompts/agents official repo"
predecessor: "Feature 049、050、051"
parallel_dependency: "后续 Web 实现可与 Agent 会话入口增强并行，但 Settings 行为入口移除必须与本 Feature 同步。"
---

# Feature Specification: Agent Behavior Scope Reset & Behavior Center

**Feature Branch**: `codex/055-agent-behavior-scope-reset`  
**Created**: 2026-03-15  
**Updated**: 2026-03-15  
**Status**: Implemented  
**Input**: 参考 Agent Zero 的 prompt / agent / project 组织方式，重新定义 OctoAgent 行为文件的共享层、agent 私有层、project 层与 Web 管理入口。目标不是继续围绕 Butler 特殊化，而是把“任何一个 Agent 都是完整运行体”的原则落到文件系统、运行时装配和 Web 界面上，同时明确 behavior / memory / secrets / project workspace 的边界。

## Implementation Status（2026-03-15）

本 Feature 已完成以下主线：

- 四层 `BehaviorWorkspaceScope`
- project-centered 目录解析
- `project_path_manifest + storage_boundary_hints`
- bootstrap 模板与默认会话 Agent 用户画像/名字/性格引导 contract
- `octo behavior --agent ...`
- `Agents` 页 `Behavior Center`
- `Settings` 行为入口迁移

本规格当前用于承载这套实现的正式边界说明，而不再只是待实现草案。

## Problem Statement

当前 OctoAgent 的 behavior workspace 已经存在，但它仍然停留在“system + project”两层模型，不能支撑真正的多 Agent 行为系统：

1. **当前没有正式的 agent 私有行为文件层**
   现有 `BehaviorWorkspace` 只支持 `system` 和 `project` 目录。`AGENTS.md / USER.md / PROJECT.md / TOOLS.md` 被当成当前 project 的默认行为文件入口，但没有一层正式的 `agent-private` 目录来承载：
   - 角色身份
   - 专长边界
   - 行为语气
   - 节奏与协作方式

2. **Butler / Worker 的文件边界仍然带有历史特殊化**
   虽然当前实现已经约束 Worker 默认只读 `AGENTS.md + PROJECT.md + TOOLS.md`，但整体设计仍然围绕 Butler 展开。用户现在要求的方向更清晰：
   - 主 Agent 只是“众多 Agent 中的一个”
   - 它只是额外拥有管理其它 Agent 的能力
   - 文件分层不应再围绕 Butler 例外来定义

3. **Settings 中的 behavior 管理入口位置错了，而且几乎无效**
   当前 `Settings` 页里挂了一个 behavior system 只读面板，但：
   - 不能直接看清哪个文件影响哪个 Agent
   - 不能直接编辑
   - 不能解释来源链
   - 不符合普通用户或维护者的操作心智
   这块能力天然应该归到 `Agents` 页，而不是 `Settings`。

4. **项目级行为文件和 project workspace 的关系还不够正式**
   现在虽然已有 project 级 behavior overlay 的雏形，但缺少清晰规则去回答：
   - 哪些文件是 project 共享的
   - 哪些文件是 project 内特定 agent 的 override
   - 当前 project 根目录到底在哪里
   - 代码、数据、文档、notes、artifacts 和 behavior 文件之间是什么关系
   - 哪些内容应该进 Memory，哪些只应该作为文件存在

5. **Memory / Secrets / Behavior 的边界还不够清楚**
   最新 Memory 实现已经是正式的写入仲裁与读取平台，`SecretService` 也已经是 project-scoped lifecycle。如果继续把越来越多的长期配置和事实都塞进 md 文件，就会和现有：
   - Memory facts / recall / vault
   - secrets bindings / secret service
   - project workspace 文档
   发生冲突。系统需要明确回答：
   - 什么属于行为规范，应该放 md
   - 什么属于事实，应该进 Memory
   - 什么属于敏感值，应该进 secret store
   - 什么属于项目材料，应该留在 project workspace 文件系统

6. **初始化与 bootstrap 还停留在 provider/runtime 级别**
   当前 onboarding 只覆盖 provider/runtime/channel/doctor/first message，没有覆盖：
   - Agent 的默认名字
   - Agent 的默认性格/语气
   - 用户的基本资料、偏好、时区/地点
   - 这些信息到底该落到 behavior files、Memory 还是 secret bindings
   这会让后续“Agent 自己知道该改哪个文件、该把事实写到哪”失去正式入口。

7. **Web 端没有“Behavior Center”**
   Agent Center 现在已经是最接近行为配置的入口，但它还没有变成：
   - 查看所有 agent 共享文件
   - 查看单个 agent 私有文件
   - 查看当前 project 覆盖层
   - 查看 effective source chain
   - 发起 edit / proposal / apply
   的正式产品面。

因此，这个 Feature 的目标不是继续补几张页面卡片，而是：

> 把 OctoAgent 的行为文件系统从“system/project 两层 + Butler 特殊化视角”升级成“共享层 + agent 私有层 + project 层 + project-agent overlay”的统一模型；同时把 Project 作为真正的工作根目录来组织代码、数据、文档、记录和行为文件；并把管理入口从 Settings 收回到 Agents；再把 bootstrap 模板、用户画像采集与存储边界一起收成正式主链。

## Product Goal

交付一套统一的 Agent 行为文件架构，满足以下目标：

- 所有 Agent 都走同一套行为文件装配规则，不再围绕 Butler 特殊化
- 用户和维护者能明确知道：
  - 哪些文件是所有 Agent 共享的
  - 哪些文件是单个 Agent 私有的
  - 哪些文件是当前 Project 共享的
  - 哪些文件是“当前 Project 下某个 Agent 的局部 override”
- `Agents` 页成为行为系统的正式 Web 管理入口
- `Settings` 页移除当前无效的 behavior 管理区块，只保留真正的系统配置
- 运行时能对任何 Agent 构建一致的 effective behavior source chain
- 运行时能明确告诉任何 Agent：
  - 当前 project 根目录在哪里
  - workspace 根目录在哪里
  - 关键 behavior md 文件在哪里
  - 哪些文件可读、可直接编辑、或应先走 proposal/apply
- 运行时和 delegation handoff 都能携带同一份 `project_path_manifest + effective behavior source chain`，避免 subordinate/worker 失去当前项目结构感知
- 运行时明确告诉任何 Agent：
  - 事实通过 Memory 访问与写入
  - 敏感值通过 project secret bindings 访问与配置
  - behavior files 只负责规则和人格，不负责存事实或密钥
- 为后续“Agent 自己提案修改行为文件，再由用户 review/apply”铺好正式目录与来源链
- 为初始化阶段正式提供 behavior 模板脚手架，以及“默认会话 Agent 的用户画像 / 名称 / 性格 bootstrap 访谈”
- 为每个默认行为文件提供初始化骨架，明确“该写什么 / 不该写什么 / facts 与 secrets 应该去哪里”

## Design Direction

### 055-A 行为文件不再围绕 Butler 特殊化

从这一轮开始，OctoAgent 的行为文件系统必须满足：

- **任何 Agent 都是完整运行体**
- “主 Agent”只是其中一个 agent profile / session owner
- “可以管理其它 Agent”是 capability / governance 的区别，不是文件层级的区别

因此：

- `BehaviorWorkspace` 必须面向 **agent runtime** 而不是 Butler 单独设计
- Web 上对任何 Agent 都要能展示相同维度：
  - 共享文件
  - 私有文件
  - 当前 project 文件
  - 当前 project-agent override

### 055-B 目录与作用域模型

统一采用 **project-centered** 目录模型。全局共享和 agent 通用人格仍放在 `behavior/` 下；某个 project 自己的行为文件、代码、数据、文档、记录等，都归到 `projects/<project-slug>/` 之下。

```text
behavior/
  system/
    AGENTS.md
    USER.md
    TOOLS.md
    BOOTSTRAP.md

  agents/
    <agent-slug>/
      IDENTITY.md
      SOUL.md
      HEARTBEAT.md

projects/
  <project-slug>/
    behavior/
      PROJECT.md
      KNOWLEDGE.md
      USER.md
      TOOLS.md
      instructions/
        *.md
      agents/
        <agent-slug>/
          IDENTITY.md
          SOUL.md
          TOOLS.md
          PROJECT.md
    workspace/
      ...
    data/
      ...
    notes/
      ...
    artifacts/
      ...
    project.secret-bindings.json
```

其中：

1. `behavior/system/`
   所有 Agent 共享的全局基础行为层。

2. `behavior/agents/<agent-slug>/`
   单个 Agent 的长期私有行为层。  
   这是“这个 Agent 是谁、擅长什么、怎么表达、怎么协作”的主要来源。

3. `projects/<project-slug>/behavior/`
   当前 Project 内所有 Agent 共享的项目上下文层。  
   这是“这个项目是什么、术语是什么、有哪些工作约束”的正式来源。

4. `projects/<project-slug>/behavior/agents/<agent-slug>/`
   高级局部 overlay。  
   只在某个 Agent 在当前项目中需要特殊人格/工具边界/项目内角色时使用。

同时：

- `projects/<project-slug>/workspace/` 承载代码、脚本、仓库 checkout、文档等真实工作材料
- `projects/<project-slug>/data/` 承载项目数据
- `projects/<project-slug>/notes/` 承载人工笔记、草稿和记录
- `projects/<project-slug>/artifacts/` 承载运行产物和导出结果
- `project.secret-bindings.json` 只记录“这个项目依赖哪些 secret 名称和用途”，不保存 secret 明文

### 055-C Behavior / Memory / Secrets / Workspace 边界

本 Feature 明确规定四类信息源各自负责什么：

1. **Behavior Files**
   - 负责规则、人格、协作方式、工具治理、项目工作约束
   - 不负责存动态事实
   - 不负责存敏感值

2. **Memory**
   - 负责事实、持续上下文、可召回信息
   - 必须复用现有 `MemoryService` 的 `propose -> validate -> commit` 与 recall/vault 流程
   - 不得被 markdown 文件替代

3. **Secrets**
   - 负责敏感值
   - 逻辑上属于 project-scoped 资源
   - 必须复用现有 `SecretService` / `ProjectSecretBinding`
   - 不得把 secret 明文写入 markdown

4. **Project Workspace**
   - 负责代码、数据、文档、notes、artifacts
   - 可以被 behavior files 引用，但不能被 behavior files 替代

因此：

- `MEMORY.md` 从默认 behavior 文件集合中移除
- 若未来需要“记忆策略文件”，只能以 `MEMORY_POLICY.md` 之类规则文件回归
- `project.secret-bindings.json` 只保存 bindings metadata，不保存 secret 值

### 055-D 初始化模板与 bootstrap 设计

系统初始化时必须为以下层级提供模板脚手架：

#### Shared templates

- `AGENTS.md`
- `USER.md`
- `TOOLS.md`
- `BOOTSTRAP.md`

#### Agent private templates

- `IDENTITY.md`
- `SOUL.md`
- `HEARTBEAT.md`

#### Project shared templates

- `PROJECT.md`
- `KNOWLEDGE.md`
- `USER.md`
- `TOOLS.md`
- `instructions/README.md`

初始化目标不是一次性写很多内容，而是生成**最小但可解释**的骨架，让任何 Agent 都知道：

- 这些文件的 canonical 路径
- 它们分别影响谁
- 哪些文件应该改规则
- 哪些内容应该写进 Memory
- 哪些内容应该走 Secret bindings

具体模板结构见：`contracts/behavior-template-skeletons.md`

#### 默认会话 Agent 的 bootstrap 访谈

虽然不对 Butler 做文件层级特例，但系统仍有一个“默认会话 Agent / root session owner”。它在用户首次真正进入某个 project 时，应触发一轮 bootstrap 访谈，采集：

- 用户希望 Agent 怎么称呼自己
- 用户希望默认会话 Agent 叫什么
- 用户偏好的语气、性格、协作方式
- 用户的时区、地点、语言偏好
- 需要优先记住的长期个人信息

这些信息的落点必须明确：

- **用户事实**：进入 Memory（如 profile/core 分区）
- **Agent 名称 / 性格偏好**：生成 behavior proposal，目标通常是当前 project 内默认会话 Agent 的 `IDENTITY.md / SOUL.md`
- **敏感信息**：引导进入 Secret bindings / `octo secrets` 主链

bootstrap 访谈本身由 `BOOTSTRAP.md` 规范“问什么、怎么问、何时停止追问、哪些信息不能进 markdown”。

### 055-E 文件归属矩阵

默认文件归属按以下原则划分：

#### 所有 Agent 共享

- `AGENTS.md`
  - 全平台操作原则、委派原则、澄清原则、输出契约
- `USER.md`
  - 用户长期偏好、协作习惯、默认时区/地点等共享事实
- `TOOLS.md`
  - 全局工具治理、默认工具边界、审批原则、路径约定
- `BOOTSTRAP.md`
  - 首轮装配、会话启动与上下文注入的共享规则

#### 单个 Agent 私有

- `IDENTITY.md`
  - 角色身份、边界、专长、角色使命
- `SOUL.md`
  - 语气、价值排序、思考习惯、表达风格
- `HEARTBEAT.md`
  - 节奏、等待/打断/协作时机、长任务沟通习惯

#### Project 共享

- `PROJECT.md`
  - 项目目标、工作边界、术语、验收口径、成功标准
- `KNOWLEDGE.md`
  - 项目级知识入口、术语表、关键文档说明
- `USER.md`（project override）
  - 该项目里的协作偏好、特殊默认值
- `TOOLS.md`（project override）
  - 该项目里的路径、环境、工具提示
- `instructions/*.md`
  - 项目内更细的说明文件，按主题拆分而不是塞进一个大文件

#### Project-Agent 高级 Overlay

- `IDENTITY.md / SOUL.md / TOOLS.md / PROJECT.md`
  - 只有当某个 Agent 在某个 Project 内需要明显不同的行为时才使用
  - 例如：Research Agent 在 A 项目里需要更保守的来源要求，但在 B 项目里不需要

### 055-F 运行时装配规则

运行时不再把文件简单拼成一个“system/project 文本包”，而是按统一顺序解析：

1. `system shared`
2. `agent private`
3. `project shared`
4. `project-agent overlay`
5. `project path manifest`
6. `runtime hints / session facts / recall capsule`

关键原则：

- 所有 agent session owner 都按同一算法装配
- 主 Agent 只是默认 session owner，不享有文件层级特权
- project 级共享信息不能再依赖 Butler 特判才能进入上下文
- project-agent overlay 是高优先级，但必须显式标记来源链

其中 `project path manifest` 是本轮新增的正式上下文对象，至少包含：

- `project_root`
- `project_behavior_root`
- `project_workspace_root`
- `project_data_root`
- `project_notes_root`
- `project_artifacts_root`
- `shared_behavior_root`
- `agent_behavior_root`
- `effective_behavior_files`
  - 文件 id
  - 实际路径
  - 作用域
  - 是否可编辑
  - 是否建议提案后再编辑

它必须进入当前 agent 的运行时上下文，保证 Agent 不只是“知道有这些 md”，还知道：

- 当前 project 在哪里
- 代码和数据该去哪个根目录找
- 哪些 md 是 canonical behavior files
- 它若要改进行为，应改哪里

同时它还必须承担 **file structure injection** 的职责。  
也就是说，对当前 Agent 来说，项目结构感知不能只来自 workspace tool 的临时探测，而要有一份稳定、可解释、可随 handoff 传递的路径清单。

同时，运行时必须显式注入以下“存储边界提示”：

- 事实与持续上下文：通过 Memory 访问
- 敏感值：通过 project secret bindings 访问
- 规则/人格/工具治理：通过 behavior files 访问
- 代码/数据/文档/notes/artifacts：通过 project workspace roots 访问

### 055-G handoff 与自改行为约束

本 Feature 不把“自动自我修改 behavior files”一次性做完，但要为它建立正式边界。

任何 handoff / subordinate / worker continuation 在需要继承项目上下文时，必须优先携带：

1. `project_path_manifest`
2. `effective behavior source chain` 摘要
3. 当前 project 的共享 instructions 摘要
4. 当前 agent 私有身份摘要

禁止只把 raw user query 裸转发给 subordinate，然后假设对方自己再去猜：

- 当前 project 根目录
- 当前行为文件在哪里
- 哪些文件允许直接改
- 哪些文件应先 proposal / diff / apply

此外，自改行为必须走两阶段：

- `proposal`
- `review/apply`

Agent 可以知道这些文件在哪、也可以提出改进建议，但不能把“知道路径”直接等同于“默认直接落盘修改”。

### 055-H Web 端改造原则

#### Agents 页成为 Behavior Center

`Agents` 页新增或重构为以下结构：

1. **Shared Files**
   - 查看 `behavior/system/` 下的共享文件
   - 说明哪些 Agent 会继承这些文件

2. **Agent Private**
   - 查看当前 Agent 的私有文件
   - 明确该文件只影响当前 Agent

3. **Project Shared**
   - 查看当前 project 的共享文件
   - 明确当前 project 下所有 Agent 是否继承

4. **Project-Agent Override**
   - 如果存在，显示“当前 project 内这个 Agent 的局部覆盖”

5. **Effective View**
   - 对当前 Agent 显示最终生效链
   - 展示每个文件来自哪里、是否覆盖了下层、哪些字段被截断
   - 展示 project path manifest，让维护者和 Agent 都知道当前 project/workspace/behavior 根在哪里
   - 展示该文件是“可直接编辑”还是“应先 proposal/apply”

#### Settings 页移除 behavior 管理

`Settings` 页里的 behavior 区块需要删除，因为它现在：

- 不是围绕 Agent 设计
- 不能解释所有权
- 不能直接操作
- 会给用户造成“这里也能管行为文件”的错误认知

`Settings` 应只保留：

- Provider / alias / runtime / memory / channels 等系统配置
- 不再承担 agent behavior 管理职责

### 055-I Agent Zero 对照吸收

本 Feature 参考 Agent Zero 的三个核心做法：

1. **共享系统 prompt 与 agent 私有 context 分离**
   - Agent Zero 在 `prompts/` 下维护系统级共享规则
   - 在 `agents/<profile>/` 下维护 agent 私有描述

2. **project instructions 是正式对象**
   - Agent Zero 使用 `.a0proj/` 及其 instructions 目录承载项目级说明
   - OctoAgent 应把 project 级 md 升格为正式行为层，而不是附属补丁

3. **项目内可以继续做 agent-specific override**
   - Agent Zero 允许项目带子 agent 配置
   - OctoAgent 也应有 `projects/<slug>/agents/<agent>/` 这一层，但默认不滥用

### 055-J 不在本轮做的事情

以下内容明确不在本轮实现范围：

- 让 Agent 自动静默改写这些 behavior files
- 设计 marketplace / persona store
- 把 Memory 的事实数据直接改成 md 文件
- 把 Settings 里所有内容都搬走；本轮只搬 behavior 区块

## Scope Alignment

### In Scope

- 重新定义行为文件的目录结构与 scope taxonomy
- 明确共享 / agent 私有 / project / project-agent 的运行时装配顺序
- 明确默认文件归属矩阵
- 引入 `project path manifest`，让 Agent 知道当前 project / workspace / behavior 根目录
- 明确 behavior / memory / secrets / workspace 的边界
- 明确事实通过 Memory、敏感值通过 secret bindings、规则通过 behavior files 的访问原则
- 设计初始化模板与 bootstrap 访谈主链
- 调整 `BehaviorWorkspaceScope` 与相关模型
- 设计 `Agents` 页作为 Behavior Center 的信息架构
- 删除 `Settings` 页中的 behavior 管理区块
- 为后续 CLI / Web 编辑与 proposal/apply 铺好 source-chain 合约

### Out of Scope

- 真正实现 Agent 自动自我修改 behavior files 的执行闭环
- 一次性改完全部 CLI 命令
- 重做整个 Memory 数据模型
- 替换现有 approval / policy / tool governance 机制

## User Stories & Testing

### User Story 1 - 我能知道某个行为文件到底影响谁 (Priority: P1)

作为维护者，我希望一眼看出一个 md 文件到底是所有 Agent 共享、某个 Agent 私有，还是当前 Project 共享。

**Independent Test**: 打开 `Agents` 页，选择任意 Agent，能看到 `Shared / Agent Private / Project Shared / Project-Agent Override / Effective View` 五块信息，且文件来源链清楚。

**Acceptance Scenarios**:

1. **Given** 存在 `behavior/system/AGENTS.md`，**When** 我查看任意 Agent 的 behavior，**Then** 系统明确显示该文件是“所有 Agent 共享”。
2. **Given** 存在 `behavior/agents/research/IDENTITY.md`，**When** 我查看 Research Agent，**Then** 系统明确显示它只影响 Research Agent，不影响别的 Agent。
3. **Given** 存在 `projects/default/behavior/PROJECT.md`，**When** 我查看当前 project 下任意 Agent，**Then** 系统显示这是当前 Project 的共享上下文层。
4. **Given** 当前 Agent 已启动，**When** 我查看 effective view，**Then** 系统同时展示 project/workspace/behavior/data/notes/artifacts roots 与关键 behavior 文件路径。

### User Story 2 - 主 Agent 不应在行为文件架构里成为特例 (Priority: P1)

作为系统设计者，我希望 Butler 只是默认主导会话的一个 Agent，而不是行为文件系统里的结构性例外。

**Independent Test**: 直接打开 Butler 会话和 Research 会话，对比 effective behavior source chain，验证两者都走同一套 scope 解析逻辑，只是继承的 agent-private 目录不同。

**Acceptance Scenarios**:

1. **Given** Butler 会话启动，**When** 系统装配行为文件，**Then** 它走 `system -> agent -> project -> project-agent` 同一条链。
2. **Given** Research 会话启动，**When** 系统装配行为文件，**Then** 它也走同一条链，而不是 Butler 特有逻辑。

### User Story 3 - 我应该在 Agents 页管理这些文件，而不是 Settings (Priority: P1)

作为用户，我希望和 Agent 行为相关的内容都在 `Agents` 页，而不是分散在一个看起来像系统配置中心的 `Settings` 页里。

**Independent Test**: 打开 `Settings` 页，不再看到 behavior 文件管理区；打开 `Agents` 页，可以查看并进入对应文件的管理入口。

**Acceptance Scenarios**:

1. **Given** 我进入 `Settings`，**When** 页面加载完成，**Then** 不再显示当前那块只读的 behavior system 区域。
2. **Given** 我进入 `Agents`，**When** 选择某个 Agent，**Then** 可以看到它的共享文件、私有文件和项目覆盖来源。
3. **Given** 我需要知道某个文件能否直接改，**When** 我查看该文件详情，**Then** 系统明确显示它是“可直接编辑”还是“建议先 proposal/apply”。

### User Story 4 - 项目内某个 Agent 可以有局部行为覆盖，但默认不复杂化 (Priority: P2)

作为维护者，我希望在需要时能给“某个项目里的某个 Agent”单独加 override，但默认情况下不把所有人都拖进复杂目录。

**Independent Test**: 为某个项目的 Research Agent 创建 `projects/<slug>/behavior/agents/research/TOOLS.md`，验证只有该 project 里的 Research Agent 会受影响。

**Acceptance Scenarios**:

1. **Given** 只有 `behavior/system/TOOLS.md` 和 `projects/default/behavior/TOOLS.md`，**When** 我查看 default project 的 Dev Agent，**Then** 系统不会凭空显示 project-agent override。
2. **Given** 新增 `projects/default/behavior/agents/research/TOOLS.md`，**When** 我查看 default project 的 Research Agent，**Then** 系统显示该 override 在最高优先级生效。

## Functional Requirements

- **FR-001**: `BehaviorWorkspaceScope` MUST 从当前 `system/project` 两层扩展为至少四层：`system_shared`、`agent_private`、`project_shared`、`project_agent`。
- **FR-002**: 系统 MUST 为任意 agent runtime 提供统一的 behavior 装配函数，不得继续把 Butler 作为文件层级特例。
- **FR-003**: 系统 MUST 正式支持以下目录：
  - `behavior/system/`
  - `behavior/agents/<agent-slug>/`
  - `projects/<project-slug>/behavior/`
  - `projects/<project-slug>/behavior/agents/<agent-slug>/`
- **FR-004**: 系统 MUST 为当前运行 agent 注入 `project path manifest`，明确 project 根目录、workspace 根目录、data/notes/artifacts 根目录，以及关键 behavior files 的实际路径。
- **FR-005**: `Agents` 页 MUST 成为 behavior 文件的正式 Web 入口，至少能展示文件归属、来源链、当前 effective view 与 project path manifest。
- **FR-006**: `Settings` 页 MUST 移除当前 behavior 文件管理区块，不再承担 agent behavior 管理职责。
- **FR-007**: 系统 MUST 定义默认文件归属矩阵，明确哪些文件默认属于共享层、agent 私有层或 project 层。
- **FR-008**: 运行时 MUST 记录每个 effective 文件的来源链、scope、覆盖关系和截断状态，以供 Web/CLI 展示。
- **FR-009**: `USER.md` 这类用户偏好文件 MUST 明确其共享规则和 project override 规则，不得继续依赖 Butler 特判决定可见性。
- **FR-010**: `MEMORY.md` MUST NOT 作为默认 behavior 文件存在；行为规范、事实存储、敏感值存储必须分别落在 behavior / memory / secrets 三种不同机制里。
- **FR-011**: project-agent overlay MUST 是显式高级能力；当该层不存在时，系统不得在 UI 中制造不存在的覆盖关系。
- **FR-012**: 系统 MUST 为任意 Agent 注入 `project_path_manifest`，其中至少包含 project/workspace/data/notes/artifacts/behavior roots 与关键 behavior 文件路径。
- **FR-013**: handoff / subordinate continuation MUST 可携带 `project_path_manifest` 与 effective behavior source chain 摘要，不得只裸转发原始用户问题。
- **FR-014**: `Agents` 页 MUST 显示文件的编辑模式元数据：可直接编辑、建议提案后编辑、或只读。
- **FR-015**: `Settings` 页中的 behavior 管理区 MUST 被移除，不得再与 `Agents` 页形成双入口。
- **FR-016**: 运行时 MUST 明确告诉模型：事实通过 Memory 读取与写入；敏感值通过 project secret bindings / SecretService 管理；behavior files 不得充当事实仓库或 secret 容器。
- **FR-017**: 初始化 MUST 为 shared / agent-private / project-shared 生成最小模板脚手架，并记录 canonical 路径。
- **FR-018**: 系统 MUST 为默认会话 Agent 提供 bootstrap 访谈主链，采集用户个人信息、默认 Agent 名称、默认性格/语气与基本环境偏好。
- **FR-019**: bootstrap 访谈采集的用户事实 MUST 进入 Memory；Agent 名称/性格偏好 MUST 以 behavior proposal 的形式落到对应的 `IDENTITY.md / SOUL.md`；敏感值 MUST 进入 secret bindings。
- **FR-020**: `Agents` 页 MUST 能解释每个 behavior 文件属于哪个作用域、影响哪个 Agent、以及它与 Memory / Secrets / Project Workspace 的边界。
- **FR-021**: 系统 MUST 为默认行为文件生成初始化模板骨架，并明确每个文件的“用途 / 应写内容 / 禁止写入内容 / 典型落点”。

## Success Criteria

- **SC-001**: 用户可以清楚地区分“共享文件 / agent 私有文件 / project 文件 / project-agent override”。
- **SC-002**: Butler 和其它 Agent 在 behavior 文件装配上不再是两套心智。
- **SC-003**: `Settings` 页不再出现当前无效且误导的 behavior 管理区块。
- **SC-004**: `Agents` 页成为行为系统的正式入口，并能回答“这个文件影响谁、从哪来、当前谁在用”。
- **SC-005**: 任意 Agent 都能在运行时明确知道当前 project/workspace 根目录和关键 behavior files 的实际路径。
- **SC-006**: 任何需要继承项目上下文的 handoff 都能携带同一份 project 结构感知，不再靠 subordinate 自己猜路径。
- **SC-007**: 初始化后，默认会话 Agent 能在 bootstrap 访谈里采集用户资料、Agent 名称与性格偏好，并把不同类型的信息正确路由到 behavior / memory / secrets。
