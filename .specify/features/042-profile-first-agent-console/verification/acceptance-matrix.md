# Acceptance Matrix: Feature 042 Profile-First Tool Universe + Agent Console Reset

## 目标

验证 042 修复的是“通用 Agent 能力主链”，而不是单个 weather/news 场景。

## 类别 1: 实时外部事实

### 示例任务

- 查询某城市当天天气
- 查询某产品官网最新说明
- 查询最近一场比赛结果

### 期望行为

- 若当前 Agent profile 具备 web/browser/core discovery 能力：
  - 首轮应尝试调用受治理的外部事实工具或 handoff
  - 不应先默认声称“我没有实时能力”
- 若缺少关键参数：
  - 应先追问参数
  - 不应把“缺参数”和“没工具”混为一谈

### 核验点

- `agent_profile_id` 是否正确继承
- `tool_resolution_mode` 是否为 `profile_first_core`
- `selected_tools_json` 是否包含应有核心工具
- blocked reason 是否可解释

## 类别 2: 项目上下文问题

### 示例任务

- 总结当前 project 目录结构
- 检查本地配置文件是否齐全
- 对当前 repo 给出新手导览

### 期望行为

- 默认 chat 使用当前 project 绑定 Agent
- 首轮直接调用 project/session/context 工具
- Agent 页面与 chat header 都能显示当前绑定来源

### 核验点

- session/project/system 回退顺序正确
- Project 默认 Agent 与 chat 当前 Agent 一致
- tool access 面板能解释当前能力边界

## 类别 3: Delegation / Handoff

### 示例任务

- 把复杂目标拆分成多个子任务
- 发起调研型 subagent
- 把运行中的问题转给更合适的 Agent

### 期望行为

- 当 profile/policy 允许 delegation：
  - `workers.review`、`subagents.spawn` 等核心工具稳定可见
- 当不允许 delegation：
  - UI 和 runtime truth 明确解释原因

### 核验点

- 模型是否稳定看到 delegation 核心工具
- blocked reason 是否区分 policy / readiness / tool_profile
- Agent 页面 Inspector 是否可直接说明“为什么不能委派”

## 类别 4: Runtime Diagnostics

### 示例任务

- 查看某次 work 到底挂了哪些工具
- 回看某个历史任务为什么使用了某个 Agent
- 判断某工具是 discovery-only 还是直接挂载

### 期望行为

- AgentCenter 可完成“谁 / 在做什么 / 能做什么 / 警告是什么”的快速判断
- ControlPlane 可完成完整 lineage / trace / blocked reason 深挖

### 核验点

- `tool_resolution_trace` 可读
- legacy work 不空白
- `selected_tools_json` 与 explainability 字段一致

## 验收阈值

- 四大类别都至少有 1 条 happy path 和 1 条 degraded / blocked path
- 具备相应能力的 Agent profile 首轮有效工具或 handoff 触发率目标：90%+
- Agent 页面首次进入 30 秒内可回答：
  - 当前默认 Agent 是谁
  - 它当前是否在工作
  - 当前主要 warning 是什么
