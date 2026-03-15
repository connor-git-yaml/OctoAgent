# Behavior Template Skeletons

## 1. 设计原则

- 参考 `Agent Zero` 的“shared system prompt + agent profile/context + project guide”分层，以及 `OpenClaw` 的“共享规范 + agent personality + bootstrap”分层。
- 行为文件只负责：
  - 规则
  - 人格
  - 协作方式
  - 工具治理
  - 项目工作约束
- 行为文件不负责：
  - 动态事实
  - 敏感值
  - 原始代码/数据/文档正文
  - 会话日志或长篇工作记录
- 事实必须通过 `MemoryService` 访问与写入。
- 敏感值必须通过 `SecretService / project secret bindings` 访问与配置。
- 任何 Agent 都必须从 `project_path_manifest` 得知当前 project、workspace、data、notes、artifacts 与关键 behavior 文件的 canonical 路径。

## 2. Shared Templates

### 2.1 `behavior/system/AGENTS.md`

**用途**
- 所有 Agent 共享的顶层操作契约。
- 对齐 Agent Zero 的 shared system instructions，与 OpenClaw 的 workspace-level agent charter。

**应该包含**
- 系统目标与总体工作原则
- 统一的回答契约
- handoff / worker continuation 契约
- facts / secrets / workspace 边界
- `project_path_manifest` 的使用要求
- proposal/apply 的治理要求

**不应该包含**
- 某个具体 Agent 的个性化语气
- 某个具体 Project 的细节
- 动态事实
- 密钥

**初始化骨架**
```md
# AGENTS

## System Mission
- 这个系统要解决什么问题
- 所有 Agent 共同遵守的最高原则

## Execution Contract
- 先理解目标，再决定直解还是委派
- handoff 不得裸转发原始用户问题
- 输出必须对用户可解释

## Storage Boundaries
- 事实 -> Memory
- 敏感值 -> Secrets
- 代码/数据/文档/notes/artifacts -> Project Workspace
- behavior files -> 规则与人格

## Path Awareness
- 任何 Agent 都必须先查看 `project_path_manifest`
- 改文件前先确认 canonical path 与 editability / review mode

## Governance
- 高风险操作必须审批
- 行为文件默认 proposal -> review/apply
```

### 2.2 `behavior/system/USER.md`

**用途**
- 所有 Agent 可共享的长期用户偏好入口。
- 只放少量稳定、人工确认过的用户偏好，不替代 Memory。

**应该包含**
- 默认称呼
- 默认语言
- 默认时区 / 地点
- 长期协作偏好
- 不希望的回答风格

**不应该包含**
- 临时对话事实
- 搜索结果
- 长篇会话摘要
- 敏感值

**初始化骨架**
```md
# USER

## Stable Preferences
- 用户希望如何被称呼
- 默认语言
- 默认时区 / 城市

## Collaboration Preferences
- 希望简洁还是详细
- 是否偏好先结论后细节
- 是否接受主动澄清

## Do Not Store Here
- 敏感值
- 临时事实
- 工作日志
```

### 2.3 `behavior/system/TOOLS.md`

**用途**
- 所有 Agent 共享的工具治理规则。

**应该包含**
- 默认允许的工具类别
- 只读与有副作用操作的差异
- 审批规则
- 路径解析规则
- 优先使用的工具层级（例如：先 filesystem，再 terminal）

**初始化骨架**
```md
# TOOLS

## Default Tool Priorities
- 能用受治理文件工具时，不优先走 terminal
- 能用 project/workspace manifest 时，不自己猜路径

## Approval Rules
- 哪些操作需要审批
- 哪些操作可直接执行

## Read vs Write
- 只读查询优先
- 写操作必须遵守 review / approval

## Path Rules
- 先读 `project_path_manifest`
- 使用 canonical roots，不凭空猜测项目目录
```

### 2.4 `behavior/system/BOOTSTRAP.md`

**用途**
- 定义首次进入 project / 初始会话时该如何完成 bootstrap。
- 对齐 OpenClaw 的 bootstrap mind-set，同时保留 OctoAgent 的治理边界。

**应该包含**
- bootstrap 何时触发
- 必问问题集合
- 结果落点规则
- facts / secrets / behavior proposal 的分流

**初始化骨架**
```md
# BOOTSTRAP

## When To Run
- 首次进入 project
- 当前用户资料缺失
- 默认会话 Agent 尚未命名或缺少人格设定

## Ask For
- 用户希望如何被称呼
- 默认会话 Agent 叫什么
- 默认会话 Agent 的性格 / 语气
- 用户时区 / 地点 / 默认语言
- 应长期记住的稳定个人信息

## Route Results
- 用户事实 -> Memory
- Agent 名称 / 性格 -> `IDENTITY.md` / `SOUL.md` proposal
- 敏感信息 -> secret bindings

## Never Do
- 不把密钥写进 md
- 不把大量事实堆进 behavior files
```

## 3. Agent Private Templates

### 3.1 `behavior/agents/<agent>/IDENTITY.md`

**用途**
- 定义这个 Agent 是谁、负责什么、适合做什么。
- 相当于 Agent Zero 的 agent profile + role 层。

**初始化骨架**
```md
# IDENTITY

## Agent Name
- 显示名称
- 简短自我描述

## Mission
- 这个 Agent 主要负责什么
- 什么时候应该直解，什么时候应该委派

## Expertise
- 擅长主题
- 不擅长主题

## Boundaries
- 不能代替 Memory 做事实存储
- 不能把敏感值写进行为文件
```

### 3.2 `behavior/agents/<agent>/SOUL.md`

**用途**
- 定义这个 Agent 的气质、风格和价值排序。
- 更接近 OpenClaw 的 personality/soul layer。

**初始化骨架**
```md
# SOUL

## Tone
- 表达风格
- 简洁/详细偏好

## Decision Style
- 更偏保守还是更偏主动
- 澄清优先还是先给 best-effort

## Value Order
- 正确性
- 解释性
- 速度
- 谨慎性
```

### 3.3 `behavior/agents/<agent>/HEARTBEAT.md`

**用途**
- 定义这个 Agent 的节奏、自检与长期任务行为。

**初始化骨架**
```md
# HEARTBEAT

## Execution Rhythm
- 长任务如何定期自检
- 何时报告进度

## Interrupt / Queue Rules
- 被用户 steer 时怎么处理
- 何时把消息排队，何时继续当前执行

## Long-Task Discipline
- 何时停止过度探索
- 何时主动收口
```

## 4. Project Shared Templates

### 4.1 `projects/<project>/behavior/PROJECT.md`

**用途**
- 项目目标、术语、成功标准、关键目录说明。

**初始化骨架**
```md
# PROJECT

## Project Purpose
- 这个项目一句话在做什么

## Success Criteria
- 做到什么算成功

## Key Terms
- 项目术语
- 关键缩写

## Important Roots
- workspace 的作用
- data 的作用
- notes 的作用
- artifacts 的作用
```

### 4.2 `projects/<project>/behavior/KNOWLEDGE.md`

**用途**
- 项目知识入口索引，而不是知识正文仓库。

**初始化骨架**
```md
# KNOWLEDGE

## Canonical Docs
- 蓝图
- spec
- 架构图
- 数据字典

## Reading Map
- 遇到什么问题先看哪份文档

## Do Not Duplicate
- 不在这里复制整份文档正文
```

### 4.3 `projects/<project>/behavior/USER.md`

**用途**
- 当前 project 下的用户协作偏好覆盖层。

**初始化骨架**
```md
# USER (Project Override)

## Project-Specific Preferences
- 在这个项目里，用户更偏好的表达方式
- 是否更看重速度 / 稳定 / 引用

## Working Agreements
- 这个项目里和用户的特殊约定
```

### 4.4 `projects/<project>/behavior/TOOLS.md`

**用途**
- 当前 project 的工具/路径/环境覆盖层。

**初始化骨架**
```md
# TOOLS (Project Override)

## Allowed Roots
- 可访问的 workspace / data / notes / artifacts 根

## Preferred Tools
- 在这个项目里优先用哪些工具

## Approval Notes
- 该项目里哪些动作一定要审批
```

### 4.5 `projects/<project>/behavior/instructions/README.md`

**用途**
- 当前 project 的工作说明入口。
- 相当于项目级的 instruction readme，不是 behavior 规则全集。

**初始化骨架**
```md
# Project Instructions README

## How To Use This Folder
- 这里有哪些 instructions
- 各自适用场景

## Priority
- 哪些 instruction 优先级更高
- 与 shared / agent-private 文件如何组合
```

## 5. Project-Agent Overlay Templates

默认不生成所有文件，只在确有需要时创建。可选文件：
- `IDENTITY.md`
- `SOUL.md`
- `TOOLS.md`
- `PROJECT.md`

**用途**
- 仅在“某个 Agent 在某个 Project 中需要额外特殊化”时使用。

**原则**
- 不复制 shared / private / project-shared 全文
- 只写当前 project-agent 的最小 delta
- 优先用 override，不另起事实仓库

## 6. Bootstrap 问题骨架

### 6.1 默认问题集

1. 你希望系统怎么称呼你？
2. 你希望默认和你聊天的 Agent 叫什么？
3. 你希望它更偏什么性格或语气？
4. 你的默认语言、时区、城市是什么？
5. 有哪些长期信息值得系统记住？
6. 有哪些信息不希望系统长期记住？
7. 当前 project 有哪些长期工作约定？

### 6.2 路由规则

- 用户称呼、语言、时区、地点、稳定偏好 -> `Memory`
- 默认会话 Agent 的名称、性格、语气偏好 -> `IDENTITY.md / SOUL.md` proposal
- API keys、tokens、passwords、cookies -> `SecretService / secret bindings`
- 当前 project 的长期工作约定 -> `projects/<project>/behavior/USER.md` 或 `PROJECT.md`

## 7. 上下文装配提示

任意 Agent 在运行时都应拿到：

- `effective_behavior_source_chain`
- `project_path_manifest`
- `storage_boundary_hints`

其中 `storage_boundary_hints` 至少应包括：

```text
facts -> MemoryService
sensitive values -> SecretService / secret bindings
rules/persona/tool governance -> behavior files
code/data/docs/notes/artifacts -> project workspace roots
```

## 8. Proposal / Apply 原则

- Agent 可以知道文件在哪里、哪个文件影响自己、哪个文件应改。
- 默认不直接静默改写 behavior files。
- 优先走：
  - proposal
  - review
  - apply

只有被明确标记为 `editable_mode=direct` 的低风险文件，才允许直接编辑。
