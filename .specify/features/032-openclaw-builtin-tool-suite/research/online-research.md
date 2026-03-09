---
required: false
mode: skip
points_count: 0
tools: []
queries: []
skip_reason: "本轮调研基于仓库内 vendored references（OpenClaw / Pydantic AI）与当前代码基线对标；目标是冻结 032 边界，不依赖实时在线资料。"
---

# Online Research: Feature 032 — OpenClaw Built-in Tool Suite + Live Graph/Subagent Runtime

## 说明

本轮没有额外做实时在线检索，原因：

1. 需求核心是对齐本仓当前实现与 vendored OpenClaw / Pydantic AI 官方参考。
2. 当前要冻结的是 032 的产品边界与“非伪实现”门禁，不是追逐最新 SaaS / 新闻变化。
3. 仓库已经包含足够的一手参考：
   - `_references/opensource/openclaw/src/agents/tools/`
   - `_references/opensource/pydantic-ai/docs/graph.md`
   - `_references/opensource/pydantic-ai/docs/multi-agent-applications.md`

## 结论

- 本轮 research 以代码库与 vendored 官方参考为准即可。
- 如果进入 032 具体实现阶段，需要选择特定 runtime backend、浏览器驱动、TTS provider 或 sandbox 执行器，再追加对应官方在线调研。
