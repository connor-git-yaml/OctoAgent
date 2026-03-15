# Research Synthesis

## 1. 当前 OctoAgent 现状

### 1.0 Memory / Secret / Onboarding 已经有正式实现，不应再绕开它们

对照当前代码后，有三个事实必须进入 055：

- `packages/memory/.../service.py` 已经提供正式的 `MemoryService`
  - 写入走 `propose -> validate -> commit`
  - 读取走 recall/search/vault
- `packages/provider/.../secret_service.py` 已经提供 project-scoped secret lifecycle
  - bindings 属于 project
  - 值通过 env/keychain/ref 解析，不该进 markdown
- `packages/provider/.../onboarding_service.py` 目前只覆盖 provider/runtime/channel/doctor
  - 还没有覆盖 Agent 名称、性格、用户画像与 bootstrap 问答

这意味着 055 不能再把事实、密钥、初始化都重新发明一遍，而应把 behavior workspace 明确接到这三套既有机制上。

### 1.1 行为文件目录还只有两层

当前实现位于：

- `/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/packages/core/src/octoagent/core/behavior_workspace.py`

关键事实：

- 当前只支持 `behavior/system/`
- 和 project 级 behavior overlay（尚未统一成 project-centered 目录）
- `BehaviorWorkspaceScope` 只有 `SYSTEM / PROJECT`

这意味着：

- 还没有正式的 agent-private 行为目录
- 也没有 project-agent override 目录

### 1.2 当前默认文件集合

当前核心文件是：

- `AGENTS.md`
- `USER.md`
- `PROJECT.md`
- `TOOLS.md`

高级文件：

- `SOUL.md`
- `IDENTITY.md`
- `HEARTBEAT.md`

但这些高级文件目前还没有通过正式的 agent-private 目录挂到统一解析模型里。

### 1.3 Settings 里的 behavior 入口位置错了

当前 `SettingsPage.tsx` 确实在展示 behavior system 摘要，但它只是：

- 一个只读投影
- 不能直接表达“哪个文件影响哪个 Agent”
- 不能形成有效管理闭环

用户的判断是成立的：这块能力应该迁移到 `Agents`。

### 1.4 当前还没有 project path manifest

现在系统虽然能解析 project selector、workspace 和 behavior workspace，但对运行中的 Agent 来说，还没有一份正式的“路径清单”告诉它：

- 当前 project 根目录在哪里
- 当前 workspace 根目录在哪里
- 当前 project 的 behavior 目录在哪里
- 哪些关键 md 是 canonical behavior files
- 哪些文件允许直接编辑，哪些应该走 proposal/apply

这会直接影响 Agent 后续自我改进能力。

## 2. Agent Zero 官方结构给出的启发

### 2.1 共享层与 agent 私有层是分开的

Agent Zero 官方仓库里：

- `prompts/` 承载系统级共享 prompt 片段
- `agents/default/`、`agents/researcher/` 等目录承载 agent 私有身份与上下文

官方文件示例：

- `prompts/agent.system.main.md`
- `agents/default/agent.json`
- `agents/default/_context.md`
- `agents/researcher/agent.json`
- `agents/researcher/_context.md`

这说明它的结构不是“一个共享文件 + 一些 project override”，而是明确区分：

- 全局共享规则
- agent 私有角色上下文

### 2.2 project instructions 是正式对象

Agent Zero 官方 `projects.md` 明确说明：

- project 有独立目录
- 有 `.a0proj/`
- 有 instructions 目录
- 有 project-specific secrets / variables / knowledge
- 也有 project-specific subagent settings

这意味着 project 级上下文不是补丁，而是正式结构。

### 2.3 subordinate handoff 强调不要裸转发原任务

Agent Zero 的：

- `prompts/agent.system.tool.call_sub.md`

明确要求 superior：

- `delegate specific subtasks not entire task`
- `message field: always describe role, task details goal overview`

这和我们最近在 OctoAgent 里补的 handoff composer 是同一方向，也说明 agent-specific context 和 project context 应该是可解释地注入，而不是隐式混在一个大 prompt 里。

### 2.4 Agent Zero 的 project 不是“只有 prompt”，而是工作容器

Projects guide 明确把 project 描述成同时包含：

- instructions
- memory
- secrets
- knowledge
- repo / file structure injection

这说明 project 在 Agent Zero 里不是一个“只装说明文档的目录”，而是完整工作容器。  
这和 OctoAgent 这轮改成 `project-centered` 目录是一致方向：project 目录应该同时承载 behavior、workspace、data、notes、artifacts 和 secret bindings metadata。

### 2.5 Agent Zero 的 agent context 文件说明了“人格文件”与“项目事实”应该分层

官方示例里：

- `agents/default/_context.md`
- `agents/researcher/_context.md`

更像是 agent 私有工作方式和长期人格描述，而不是“把事实仓库也写进去”。  
这进一步支持 OctoAgent 在 055 中：

- 把 `IDENTITY.md / SOUL.md / HEARTBEAT.md` 固定到 agent-private 层
- 把事实交给 Memory
- 把项目真实材料留在 project workspace

## 3. 对 OctoAgent 的设计结论

### 3.1 不能继续只有 system/project 两层

如果继续只有：

- `system`
- `project`

就会把以下问题都推给 Butler 特判或 profile 代码：

- 某个 agent 的长期 persona
- 某个 agent 的协作节奏
- 某个 project 内某个 agent 的局部特殊规则

这会继续破坏“任何 Agent 都是完整运行体”的原则。

### 3.2 推荐的正式模型

建议采用四层解析模型：

1. `system_shared`
2. `agent_private`
3. `project_shared`
4. `project_agent`

对应用户心智仍然保持为三类：

- 所有 Agent 共享
- 单个 Agent 私有
- 当前 Project 共享

第四层 `project_agent` 只作为高级 override 使用，不在默认流程里复杂化。

### 3.3 `MEMORY.md` 不适合作为默认 behavior 文件

这轮分析后，`MEMORY.md` 建议从默认 behavior 文件集合中移除。

原因：

- 它容易和真实 Memory 事实仓库混淆
- 容易把“事实”错误地写进规则文件
- 会模糊 behavior / memory / secrets / workspace 的边界

如果未来需要类似文件，应该回归为：

- `MEMORY_POLICY.md`

也就是“记忆规则文件”，而不是“记忆内容文件”。

### 3.4 Web 入口应迁到 Agents

因为行为文件的真正语义是：

- 它影响哪个 Agent
- 这个 Agent 在哪个 Project 内又被怎样覆盖

所以它天然属于 `Agents` 页，而不是 `Settings`。

### 3.5 Agent 需要一份正式的路径清单，而不只是“自己去猜”

如果 Agent 后续要能稳定地：

- 找到当前 project 根目录
- 找到 workspace / data / notes / artifacts 目录
- 找到当前生效的关键 behavior files
- 判断某个文件是可直接编辑还是应先 proposal/apply

那么这些信息就不能只靠临时工具探测。  
它们应该以正式的 `project_path_manifest` 进入上下文，并在 handoff 时一起传递。

否则 subordinate/worker 会不断重复：

- 自己重新猜目录
- 自己重新猜哪些 md 是 canonical
- 自己重新猜当前 project 的工作边界

这会直接削弱多 Agent 连续性。

### 3.6 `proposal/apply` 应是行为文件修改的默认边界

Agent Zero 的结构说明了“文件可组织、可继承、可专门化”，但并不意味着任何 Agent 都应默认静默直接改文件。  
对 OctoAgent 更合理的做法是：

- 先让 Agent 知道文件在哪、影响谁、应该改哪里
- 再让它生成 proposal
- 最后由用户 review/apply

因此，Web 和 CLI 都应该尽早能展示：

- 文件路径
- 来源链
- editability
- review mode

### 3.7 初始化模板与 bootstrap 不能继续缺席

当前 onboarding 已能把 provider/runtime/config/doctor 收口，但它还不能回答：

- 默认会话 Agent 该叫什么
- 默认会话 Agent 应该是什么性格
- 用户的时区/地点/长期偏好从哪里来
- 这些信息是写进 behavior、Memory 还是 secrets

Agent Zero 的项目/agent 结构说明了：初始化时应同时建立

- 共享规则文件
- agent 私有身份文件
- project 共享文件

OpenClaw 的 `AGENTS / SOUL / USER / BOOTSTRAP` 思路则说明：这些模板应该足够少、足够稳定，让 Agent 明确知道“哪些文件定义规则，哪些信息必须走 Memory / Secrets”，而不是继续靠新增 md 文件堆复杂度。

而不是只生成 provider 配置。  
所以 055 应把 bootstrap 访谈正式写进范围：由 `BOOTSTRAP.md` 规定问答方式，再把不同信息路由到 Memory、behavior proposal 或 secret bindings。

## 4. 推荐目录

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
    data/
    notes/
    artifacts/
    project.secret-bindings.json
```

## 5. 这轮调研的核心判断

- 用户提出的方向是对的：不应再围绕 Butler 特殊化行为文件系统
- Agent Zero 的官方结构确实支持“共享层 / agent 私有层 / project 层”的拆分
- OctoAgent 当前最需要补的不是再加一两个 md 文件，而是：
  - project-centered 目录
  - scope taxonomy
- behavior / memory / secrets / workspace 边界
- 运行时装配统一化
- project path manifest
- bootstrap 模板与问答路由
- 默认模板骨架合同（用途 / 应写内容 / 禁止内容 / 典型落点）
- handoff capsule
- proposal/apply 边界
- Web 入口迁移到 Agents
