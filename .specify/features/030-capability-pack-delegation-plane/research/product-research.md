# Product Research: Feature 030 — Built-in Capability Pack + Delegation Plane + Skill Pipeline

## 结论

1. 030 的产品目标不是再加一层“聪明路由”，而是把 OctoAgent 从单 Worker、静态工具集推进到可解释的多能力运行面。
2. 用户可感知的最小产品面必须同时包含四类信息：为什么这样路由、当前 work 归谁、用了哪些能力、失败后如何降级。
3. 026 已交付 control plane 外壳，因此 030 不应重做页面框架，而应把 capability / work / pipeline / subagent/runtime 状态接进既有控制台。
4. 025-B 已把 project/workspace/secret/wizard 主路径落地，030 应直接消费 project/workspace 选择态作为 worker workspace 与 capability pack 的作用域，而不是再引入独立 scope 事实源。
5. “Built-in capability pack” 的第一阶段重点不是开放生态，而是把 bundled skills、bundled tools、worker bootstrap files 做成稳定的内置产品面，保证新 worker/new project 启动即有基础能力。

## 用户问题

当前 master 上，系统仍然停留在以下产品缺口：

- Orchestrator 只有单 Worker 路由，`worker_capability` 基本等于调用方显式指定，缺少可解释的 route reason。
- ToolBroker 只提供静态注册/发现/执行，没有“根据当前任务语义选择更小工具集”的能力，难以控制提示长度、权限面和工具噪音。
- SkillRunner 只有单 skill 自由循环，没有节点级 checkpoint/replay/HITL/重试这一层可恢复子流程。
- Control Plane 虽然已经能看 project/session/automation/diagnostics，但看不到 route reason、tool hit、work ownership、pipeline status、subagent/runtime status。

这会直接影响 M3 的“产品可用”目标：用户能看到任务在跑，但看不懂为什么派到这个 worker，也不能判断该不该人工接管。

## 产品目标拆解

### 1. Capability Pack 要解决什么

Capability Pack 需要回答三个产品问题：

- 这个系统默认自带哪些能力？
- 不同 worker 首次启动时拿到的基础上下文是什么？
- 当 ToolIndex 不可用或多 Worker 退化时，最低可用能力集合是什么？

因此本阶段的 capability pack 应至少包含：

- bundled skills
- bundled tools
- worker bootstrap files（`AGENTS.md` / `BOOTSTRAP.md` / worker profile）
- 与 worker type 的绑定关系
- 降级时的 static baseline toolset

### 2. Delegation Plane 要解决什么

Delegation Plane 不是单次 dispatch 的别名，而是主 Agent 对“委派工作”的正式控制面。

它必须把以下对象显式化：

- `Work`：主 Agent 的 create/assign/merge/cancel/timeout/escalation 单位
- `target`：worker / subagent / ACP-like runtime / graph agent
- `owner`：当前谁持有 work
- `route_reason`：为什么分配给这个目标
- `status`：当前运行、暂停、等待输入、升级、失败或合并完成

这意味着 030 的最小产品面不只是“能派出去”，还必须做到“派出去后可观察、可取消、可回退”。

### 3. Skill Pipeline 要解决什么

Skill Pipeline 是 Worker 的确定性子流程工具，而不是替代 Worker Free Loop 的新执行模式。

用户可感知价值在于：

- 多步流程可 checkpoint
- 节点失败可 retry
- 需要审批/人工输入时可暂停
- 崩溃后可 replay/恢复
- 控制台能展示 pipeline graph 与当前节点

如果这层没有落地，030 就只是“更多 worker + 更多工具”，而不是“可恢复的增强层”。

## 用户视角的关键体验

### A. 单 Worker 降级仍然可用

当 ToolIndex、graph runtime 或多 Worker registry 不可用时，系统必须退化为：

- 单 Worker
- 静态工具集
- 明确 route reason = `single_worker_fallback`
- control plane 明示 degraded，而不是静默退化

### B. 委派必须可解释

最少要让用户回答：

- 为什么派给 `ops/research/dev`
- 为什么命中了这些工具
- 当前 work 是父 work 还是子 work
- 是否进入 subagent / graph runtime / ACP-like runtime
- 当前是否等待人工输入或审批

### C. 控制动作要跨表面一致

Telegram / Web 至少要共享以下新语义：

- work cancel
- work retry / escalation
- pipeline node retry / resume
- delegation/runtime status refresh

不能出现 Web 用 resource/action，而 Telegram 仍然走私有分支。

## 范围判断

### 本 Feature 现在必须交付

- bundled capability pack 的正式模型和 producer
- ToolIndex + metadata filter + 动态工具注入 + static fallback
- Skill Pipeline Engine 的 checkpoint/replay/pause/HITL/node retry
- Work 作为 delegation unit 的正式模型与持久化
- 主 Agent 到 worker / subagent / ACP-like runtime / graph agent 的统一委派协议
- 多 Worker capability registry 与路由策略
- control plane 中的 route reason / tool hit / pipeline / work ownership / runtime status

### 本 Feature 明确不交付

- M4 remote nodes / companion surfaces
- 新的 control plane 基础框架
- 完整 Memory Console / Vault 领域视图
- 绕过 ToolBroker / Policy / audit 的“快捷执行通道”

## 产品风险

1. 如果 Work 只是内存态对象，030 会直接违反 Durability First。
2. 如果动态工具注入绕过 ToolBroker，仅在 prompt 层拼字符串，会破坏治理面和审计链。
3. 如果 pipeline 不能暂停到 WAITING_APPROVAL / WAITING_INPUT，030 对高风险流程的价值会大幅下降。
4. 如果 control plane 只展示 worker 结果，不展示 route reason / tool hit / work graph，用户仍然无法理解系统在做什么。

## 产品决策

- `Work` 是 030 的正式产品对象，而不是内部临时 DTO。
- control plane 只做增量扩展，不重做 026 的 shell。
- 默认路径优先保证单 Worker / 静态工具集可用，多 Worker / ToolIndex / graph runtime 为增强层。
- 所有新能力都以 project/workspace 为作用域消费 025-B 基线。
