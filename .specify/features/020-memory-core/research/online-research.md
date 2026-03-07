---
required: true
mode: full
points_count: 3
tools:
  - web.search_query
  - web.open
  - openrouter-perplexity:web_search
queries:
  - "Mem0 Building Production-Ready AI Agents with Scalable Long-Term Memory"
  - "LLMLingua Compressing Prompts for Accelerated Inference of Large Language Models"
  - "LongLLMLingua Accelerating and Enhancing LLMs in Long Context Scenarios via Prompt Compression"
findings:
  - "Mem0 采用 extraction/update 双阶段，把新事实与近邻记忆比较后执行 add/update/delete/noop，证明 WriteProposal 先抽取、再仲裁的模式具备外部证据支撑。"
  - "LLMLingua 使用小模型做 coarse-to-fine prompt compression，说明廉价模型适合负责压缩与冗余识别，不应让主模型承担所有上下文卫生工作。"
  - "LongLLMLingua 强调 query-aware 和位置偏置修复，提示 020 应把长期记忆与长上下文压缩解耦：Memory Core 负责长期事实，后续 Context Manager 再负责工作上下文压缩。"
impacts_on_design:
  - "保留 `WriteProposal.action=ADD|UPDATE|DELETE|NONE`，与外部记忆系统主流实践对齐。"
  - "在 plan 中明确 cheap/summarizer 模型未来负责 `before_compaction_flush()` 和 proposal 草案，但 020 不内置压缩引擎。"
  - "冻结 search/get 两段式读取，避免把压缩后的长正文直接灌回主上下文。"
---

# 在线调研记录

## Point 1: Mem0 长期记忆双阶段写入

- 来源: `https://arxiv.org/abs/2504.19413`
- 结论: 记忆系统先抽取候选事实，再与已有记忆对比决定 add/update/delete/noop，是可扩展且成本受控的主流做法。
- 对 020 的影响: `WriteProposal` 需要保留 action、confidence、evidence_refs，不应简化成“直接 upsert SoR”。

## Point 2: LLMLingua 小模型压缩

- 来源: `https://aclanthology.org/2023.emnlp-main.825/`
- 结论: 小模型非常适合作为 prompt compression / redundancy detection 层。
- 对 020 的影响: 020 只预留 `before_compaction_flush()` 和 proposal 入口，不把上下文 GC 本体塞进 memory core。

## Point 3: LongLLMLingua 长上下文优化

- 来源: `https://www.microsoft.com/en-us/research/publication/longllmlingua-accelerating-and-enhancing-llms-in-long-context-scenarios-via-prompt-compression/`
- 结论: 长上下文优化要面向 query-aware relevance 和 position bias，而不是简单截断。
- 对 020 的影响: 020 的长期记忆接口必须独立于 future context manager，后续可按 query relevance 选择 Fragments/SoR 命中结果。

