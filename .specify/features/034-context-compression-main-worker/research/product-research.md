# Product Research — Feature 034

## 调研目标

明确 Agent Zero 的上下文压缩从用户价值上解决什么问题，以及 OctoAgent 应该保留哪些体验特征。

## 参考来源

- `_references/opensource/agent-zero/docs/developer/architecture.md`
- `_references/opensource/agent-zero/knowledge/main/about/installation.md`

## 关键结论

### 1. 压缩是“持续对话可用性”能力，不是后台优化细节

Agent Zero 在架构文档里把 history summarization 直接描述为维持上下文有效性的方法，而不是附属工具。这一点对 OctoAgent 的意义是：压缩必须进入真正的 prompt assembly 路径，否则用户感知不到价值。

### 2. utility model 要和主模型分工明确

Agent Zero 的安装/配置文档明确把 utility models 单独作为角色配置，并强调太小的模型在 context extraction / memory consolidation 上经常失败。对 OctoAgent 的直接启发是：

- 业务层使用语义 alias `summarizer`
- 与 `main` 模型解耦
- 降级时宁可退回原始历史，也不要把失败摘要当真

### 3. 最近轮次要保持原文，压缩旧历史

Agent Zero 的 history 设计并不是“整个历史统一摘要”，而是尽量保留近期注意力窗口，只压缩较旧内容。这个体验约束被直接保留下来：034 只压缩 older turns，最近轮次保持原文。

## 产品化结论

Feature 034 的产品定位应是：

- 主 Agent / Worker 的可持续对话能力增强
- 真实主链路能力，而不是控制台上的可选按钮
- 发生时可追溯，失败时可降级

