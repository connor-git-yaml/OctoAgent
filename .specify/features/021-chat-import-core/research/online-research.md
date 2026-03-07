---
required: true
mode: full
points_count: 3
tools:
  - perplexity-web-search
queries:
  - "OpenClaw session management compaction transcript sessions dashboard memory CLI official docs chat session usability"
  - "Agent Zero load save chats web UI backup restore memory deferred tasks official docs README usability"
  - "OpenClaw memory import sessions memory cli dashboard official docs transcript search"
findings:
  - OpenClaw 官方文档明确把 session transcript 与 memory search 分层管理，说明历史聊天导入不应伪装成 live session，而应进入独立 memory / import 路径。
  - Agent Zero 把 chats 的 save/load、memory dashboard 的可见/可搜/可编辑作为核心用户承诺，说明导入结果必须可回看、可审计、可定位。
  - OpenClaw 的 memory CLI / sessions CLI 都是显式入口，这进一步证明 021 不能只交付库层，不交付入口。
impacts_on_design:
  - 021 需要补 `octo import chats` 最小 CLI 入口，满足 M2 的用户可达性承诺。
  - 021 需要把原文窗口与摘要分层持久化，避免把导入数据混入 live session transcript。
  - 021 需要持久化导入报告与 provenance，而不是只在控制台打印一次结果。
skip_reason: ""
---

# 在线调研证据（Feature 021）

## Findings

1. **OpenClaw 明确区分 sessions 与 memory，两者不应混为一谈**
- OpenClaw 官方文档把 `openclaw sessions` 与 `openclaw memory` 定义成两组不同的能力：前者管理 transcript / session store，后者管理可检索 memory。
- 这直接支持 021 的一个关键设计：历史聊天导入不应直接伪装成 live session，也不应直接污染当前 transcript；它应进入独立 import / memory 路径，并保留 provenance。
- 参考：
  - [OpenClaw Sessions CLI](https://docs.openclaw.ai/cli/sessions)
  - [OpenClaw Memory CLI](https://docs.openclaw.ai/cli/memory)
  - [OpenClaw Memory Concepts](https://docs.openclaw.ai/concepts/memory)

2. **Agent Zero 的用户承诺不是“系统记住了”，而是“用户看得见、找得到、能导出”**
- Agent Zero 官方 README 与文档把 `load/save chats`、自动保存 session、memory dashboard 的搜索/编辑/导出当作核心可用性能力。
- 这意味着 021 若只把内容写入内部 memory，而不给用户报告、来源引用和可定位结果，就无法达到同等级的可用性。
- 对 OctoAgent 而言，这要求 021 至少交付：导入报告、artifact refs、cursor 与结果摘要。
- 参考：
  - [Agent Zero README](https://github.com/agent0ai/agent-zero)
  - [Agent Zero Installation Docs](https://agent-zero.ai/en/docs/installation/)

3. **显式 CLI 入口是聊天历史治理的可用性基线**
- OpenClaw 的 memory / sessions 都有显式 CLI 命令；Agent Zero 也把 load/save、backup/restore 放在用户可触达表面，而不是只留内部 API。
- 因此 021 只交付导入 service 而不交付入口，会继续违背 M2 对“Chat Import 有稳定入口”的承诺。
- 参考：
  - [OpenClaw Memory CLI](https://docs.openclaw.ai/cli/memory)
  - [OpenClaw Sessions CLI](https://docs.openclaw.ai/cli/sessions)
  - [Agent Zero README](https://github.com/agent0ai/agent-zero)

## impacts_on_design

- 设计决策 D1：021 的 MVP 必须包含 `octo import chats`，不能只停留在库层。
- 设计决策 D2：导入原文与长期记忆必须分层：原文窗口进 artifact，摘要与事实候选走 memory contract。
- 设计决策 D3：导入后必须产出可持久化报告，至少包含计数、cursor、scope、warnings 和 artifact refs。

## 结论

在线证据与本地 references 结论一致：Feature 021 的关键不是“把聊天文本导进去”，而是让用户对导入行为有入口、预期、审计和恢复信心。
