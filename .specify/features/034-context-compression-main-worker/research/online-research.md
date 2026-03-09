# Online Research — Feature 034

## 说明

本轮没有额外执行公网搜索，原因不是省略调研，而是仓库已经 vendored 了足够完整的 Agent Zero 源码与文档快照，直接读上游实现比二手网页摘要更可靠。

## 使用的上游证据

- `_references/opensource/agent-zero/python/helpers/history.py`
- `_references/opensource/agent-zero/python/extensions/message_loop_end/_10_organize_history.py`
- `_references/opensource/agent-zero/python/extensions/message_loop_prompts_before/_90_organize_history_wait.py`
- `_references/opensource/agent-zero/agent.py`
- `_references/opensource/agent-zero/docs/developer/architecture.md`
- `_references/opensource/agent-zero/knowledge/main/about/installation.md`

## Skip Reason

- 已存在本地上游源码和官方文档快照
- 本 Feature 的关键问题是“如何接入我们当前代码”，本地代码与 vendored upstream 才是第一手证据

