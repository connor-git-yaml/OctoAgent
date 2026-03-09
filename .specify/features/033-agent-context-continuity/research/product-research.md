# 产品调研报告：Feature 033 Agent Profile + Bootstrap + Context Continuity

## 1. 用户真正感知到的问题

从当前产品形态看，OctoAgent 已经有 Project、Memory Console、Import Workbench、Delegation Plane 和 Control Plane，但用户实际聊天时仍会遇到三个明显断层：

1. **Agent 没有“人设和关系”**
   当前系统没有 owner basics、assistant identity、interaction preferences 的正式入口。用户配置完 provider/channel 后，聊天得到的仍只是一个通用模型，而不是一个已经认识自己的长期助手。

2. **对话没有连续性**
   用户刚说完上一轮上下文，下一轮主 Agent 仍然像新会话一样处理问题。哪怕 Memory Console 中已经有数据，用户也感知不到这些记忆真的参与了回答。

3. **控制台只能看 Memory，不能解释“这次回答用了什么上下文”**
   当前 control plane 能看 memory records，但无法回答“本次响应到底用了哪个 profile、哪个 bootstrap、哪些 recent summary、哪些 memory hits、为什么 degraded”。

## 2. OpenClaw 借鉴点

### 2.1 首启不是只配 provider，而是建立身份关系

OpenClaw 的 bootstrapping 文档和 `BOOTSTRAP.md` 模板强调：

- 第一次运行时先建立 “Who am I / Who are you”
- 生成 `IDENTITY.md`、`USER.md`、`SOUL.md`
- 后续 session startup 默认先读取这些文件，再读取最近 memory

对 OctoAgent 的启发：

- bootstrap 不该只是“可选文档模板”，而应成为 owner/assistant basics 的正式产品路径
- 但我们的 canonical truth 不应放在 markdown 文件，而应放在 profile/store，再按需 materialize 成文件

### 2.2 `AGENTS.md` 体现的是 session startup contract

OpenClaw 的 `AGENTS.md` 模板本质是在定义 session startup 规则：

- 先读谁
- 哪些 memory 只在 main session 读
- 哪些信息是 shared context 禁止泄露

对 OctoAgent 的启发：

- 033 需要把这些“启动时应该加载什么”的规则 formalize 成 `AgentProfile + ContextPolicy`
- 特别是 main-session-only / shared-context-safe 的边界，必须进入正式模型而不是靠口头约定

## 3. Agent Zero 借鉴点

### 3.1 project 是 instructions + memory + secrets + files 的统一隔离单位

Agent Zero 的 projects 文档把 project 定义为：

- instructions
- isolated/shared memory
- secrets/variables
- files/knowledge
- subagent configuration

这说明项目隔离真正要成立，主 Agent 的 persona、memory mode、bootstrap context 也必须 project-aware。

对 OctoAgent 的启发：

- 033 不能做成 global-only 的 user profile
- 至少要支持 `global owner profile + project overlay + project default agent profile`

### 3.2 Memory dashboard 的价值不只是“能搜索”，而是用户可感知

Agent Zero 把 Memory dashboard 做成用户直接可管理的对象，同时配合项目隔离与 memory subdir 解析，让用户知道自己当前在看哪个 memory scope。

对 OctoAgent 的启发：

- 027 已经解决了“看得见 memory”
- 033 需要解决“看得见它如何进入响应”
- 用户真正需要的是 response provenance，而不是另一个只读 memory 列表

## 4. 产品结论

033 的产品价值不在“再做一个高级 Memory 功能”，而在把以下三件事接成真正可用的一条线：

1. **Bootstrap**：第一次建立 owner/assistant 的关系与边界
2. **Continuity**：后续每一轮对话都能继续这段关系和上下文
3. **Visibility**：控制台能解释这次回答到底带了哪些上下文

没有这三件事，OctoAgent 即便底层具备 Memory、Project、Delegation，也仍然像“带很多后台功能的 stateless chat shell”。
