# Research Synthesis — Feature 034

## 最终方案

采用“**Agent Zero 思路 + OctoAgent 运行时适配**”：

- 保留 Agent Zero 的核心思想：
  - utility/summarizer model 独立于主模型
  - recent raw + older summarized
  - 压缩是 prompt lifecycle 的组成部分
- 不照搬 Agent Zero 的实现形式：
  - 不使用其 background thread + wait extension
  - 改为在 `TaskService.process_task_with_llm()` 内联完成上下文组装和必要压缩

## 关键设计决策

### 决策 1：完整消息必须落盘

如果 `USER_MESSAGE` 只有 preview，任何真正的多轮压缩都是伪实现。因此先补 `payload.text`，再谈 compaction。

### 决策 2：每次请求都保存 request snapshot

这样才能验证“主模型到底看到了什么”，也是后续 memory flush 的证据源。

### 决策 3：压缩结果只通过 Memory flush hook 回灌

这保证 034 只是上下文治理能力增强，而不是绕过 020/028 记忆治理。

### 决策 4：summarizer 失败时退回原始历史

这是对 Constitution 6 的直接落实。压缩失败不能成为主任务失败的新原因。

## 为什么 Subagent 不接

- Subagent 目标是更轻、更边界清晰的 delegation 执行单元
- 让 Subagent 也做 compaction 会显著增加调试成本和行为不确定性
- 用户已明确要求 Subagent 不需要

