# Research Synthesis: Feature 042 — Profile-First Tool Universe + Agent Console Reset

**特性分支**: `codex/feat-042-profile-first-agent-console`  
**汇总日期**: 2026-03-13  
**输入**: `product-research.md` + `tech-research.md` + `online-research.md`

## 1. 产品 × 技术交叉矩阵

| 目标 | 用户价值 | 技术可行性 | 风险 | 结论 |
|---|---|---|---|---|
| 默认聊天按当前 Root Agent 能力运行 | 很高 | 高 | 中 | 纳入 MVP |
| delegation 工具稳定可见 | 很高 | 高 | 中 | 纳入 MVP |
| Agent 页面 IA 重组 | 很高 | 高 | 中 | 纳入 MVP |
| ToolIndex 退到 discovery 二线 | 高 | 中高 | 中 | 纳入 MVP |
| 工具可用性解释面板 | 高 | 高 | 中低 | 纳入 MVP |
| 长尾工具搜索与收藏 | 中 | 高 | 低 | 二期 |
| Profile effectiveness / eval dashboard | 中 | 中 | 中 | 二期 |

## 2. 综合判断

### 产品判断

当前最伤体验的不是单个工具缺失，而是：

- 用户已经配置过 Agent，但默认聊天不按它的能力工作
- 用户无法从页面直观看懂“当前 Agent 是谁、能做什么、为什么没做到”
- runtime 能力来源被隐藏在内部 heuristic 里，用户视角像随机表现

### 技术判断

仓库已经具备做这件事的关键基础：

- 041 已把 `WorkerProfile` 正式落地
- `AgentProfile / WorkerProfile / Work / ControlPlane` 都有正式对象链
- 现有问题集中在“运行时工具解析次序不对”和“前端 IA 仍偏 operator-first”

所以 042 不需要推翻 041，而是要把 041 从“有 profile 对象”推进到“profile 真正驱动聊天与界面”。

## 3. 最终推荐方案

### 推荐架构

采用 `Profile-First Tool Universe`：

1. 先解析当前 chat/work 的有效 profile  
2. 再解析这个 profile 的核心工具宇宙  
3. 模型直接在核心工具宇宙内自主选择  
4. `ToolIndex` 只负责长尾发现与 explainability  

### 推荐 IA

Agent 页面重组为三栏工作台：

- **左栏：Root Agents**
  - 默认 Agent
  - 所有 Root Agents
  - Starter Templates
- **中栏：Agent Detail**
  - Overview
  - Tool Access
  - Instructions
  - Launch / Bind
- **右栏：Runtime Inspector**
  - Current Work
  - Tool Resolution
  - Warnings / Readiness
  - Recent Activity

`ControlPlane` 则保留为深度诊断与审计面。

## 4. MVP 范围

### 纳入

- 普通 chat 支持显式/隐式 profile 绑定
- profile-first core tool universe
- delegation 核心工具常驻挂载
- tool resolution explainability
- Agent 页面 IA 重组

### 排除

- 多实例 Root Agent runtime registry
- 完整 eval dashboard
- 团队级共享 Agent library
- Tool marketplace / 自动推荐系统

## 5. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 一次性把太多工具直接给模型 | 工具调用质量下降 | 区分“核心工具宇宙”和“长尾发现工具” |
| 删除 ToolIndex 主链后失去可解释性 | 排障困难 | 保留 tool resolution trace 和 control-plane lens |
| Agent 页面改动过大影响 041 用户习惯 | 学习成本 | 保持 Root Agent/Profile Studio 术语连续，重构 IA 不重写概念 |
| chat 仍不支持 profile 绑定 | 042 价值大幅缩水 | 把 `agent_profile_id` 接入 chat 作为 MVP 硬需求 |

## 6. 后续建议

- 042 完成后，再考虑 `plan/tasks` 阶段补一份 category-based acceptance matrix：
  - 实时外部事实
  - 项目上下文问题
  - delegation / handoff
  - runtime / diagnostics
- 这套 acceptance matrix 应验证“Profile-first”是否真的比旧 top-k tool selection 稳定，而不是只验证单个 case
