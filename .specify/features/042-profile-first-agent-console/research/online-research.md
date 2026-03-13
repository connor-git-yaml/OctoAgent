---
required: true
mode: full
points_count: 3
tools:
  - web.search
queries:
  - "site:anthropic.com/docs agents and tools implement tool use"
  - "site:anthropic.com engineering writing tools for agents"
  - "site:openai.com practical guide to building agents handoffs evals"
findings:
  - "Anthropic 官方文档明确说明，`tools` 参数会与工具定义、配置和用户 system prompt 一起构造成专用系统提示。这支持 042 采用“先确定 Agent 的工具宇宙，再让模型自己选”的方向，而不是继续依赖外部 top-k 猜测。"
  - "Anthropic 关于工具设计的工程文章强调：工具描述、输入输出命名、评测驱动迭代会显著影响工具调用成功率。042 应把 tool selection 的优化重点从 heuristic 调整，转向 profile 工具带设计与 acceptance/eval。"
  - "OpenAI 的 agent 实践指南把 handoff 视为一种工具调用，并强调多 Agent 系统需要以 eval 建立基线。042 应把 delegation 工具作为稳定能力暴露，并为 category-based acceptance matrix 留出正式位置。"
impacts_on_design:
  - "保留 ToolBroker / policy / tool schema 作为正式边界，但默认聊天改为 profile-first 工具解析。"
  - "delegation/handoff 工具不应继续被动等待 ToolIndex 命中，应作为某些 profile 的核心工具带一部分。"
  - "Feature 042 需要同步定义 tool-resolution explainability 和 acceptance matrix。"
skip_reason: ""
sources:
  - title: "How to implement tool use"
    url: "https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/implement-tool-use"
  - title: "Writing effective tools for AI agents—using AI agents"
    url: "https://www.anthropic.com/engineering/writing-tools-for-agents"
  - title: "A practical guide to building agents"
    url: "https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/"
---

# Online Research: Feature 042 — Profile-First Tool Universe + Agent Console Reset

## 结论

本次在线调研没有推翻本地参考结论，反而强化了两个判断：

1. 工具集合本身就是模型行为的重要输入，应该被稳定地定义，而不是每轮模糊猜测。
2. handoff / delegation 需要被当作正式工具与正式评测对象，而不是隐式副产物。

## 设计影响

- 042 应把“工具宇宙”上移为 profile 解析结果，而不是 `ToolIndex` 的单次 top-k 结果。
- 042 应加强工具描述、工具边界和 explainability，而不是继续增加隐式 heuristic。
- 042 应补 category-based acceptance matrix，用真实任务类别验证“工具可见性”和“delegation 成功率”。
