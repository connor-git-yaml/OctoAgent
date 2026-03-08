---
required: true
mode: full
points_count: 3
tools:
  - web.search_query
  - web.open
queries:
  - "GitHub NevaMind-AI memU official repository multimodal memory"
  - "site:docs.openclaw.ai/reference/session-management-compaction OpenClaw official"
  - "site:agent-zero.ai/p/docs/memory Agent Zero memory management official"
findings:
  - "MemU 官方仓库把自己定义为 agentic memory framework，支持 conversations / documents / images / audio / video 的统一记忆抽取，并提供 Resource → Item → Category 三层结构、RAG/LLM 双检索路径和 `memorize()/retrieve()` API。"
  - "OpenClaw 官方文档明确把 pre-compaction memory flush 设计成 compaction 前的显式执行链：先在软阈值前运行 silent flush，再进入真正 compaction；同时强调 search/get 两段式读取和 fallback。"
  - "Agent Zero 官方文档把 memory dashboard、project memory isolation、knowledge import 和 memory cleanup 分离出来，说明“记忆引擎”和“记忆产品面”应解耦，而 project-scoped isolation 是长期可用的上游模型。"
impacts_on_design:
  - "028 应吸收 MemU 的多模态 ingest、hierarchical retrieval 和 derived layers，但不能把它的 categories/items 直接当作 SoR 事实源。"
  - "028 的 compaction/consolidation/flush 必须是可审计执行链，并继续保留 020 的 `before_compaction_flush()` 作为草案钩子，而不是隐式副作用。"
  - "028 必须先冻结 027 可消费的 query/projection/integration contract，把 dashboard/UI 留给 027，而把 project-scoped isolation、diagnostics 和 maintenance hooks 留在 backend。"
skip_reason: ""
---

# 在线调研记录

## Point 1: MemU 官方仓库

- 来源: `https://github.com/NevaMind-AI/memU`
- 结论:
  - MemU 官方将自身描述为 memory infrastructure / agentic memory framework。
  - 支持 `conversation`、`document`、`image`、`audio`、`video` 多模态输入。
  - 提供 `Resource -> Item -> Category` 三层结构，强调 traceability 与 progressive summarization。
  - 官方 API 将“写入抽取”和“检索召回”分为 `memorize()` / `retrieve()` 两类。
- 对 028 的影响:
  - 可以借鉴多模态 ingest 和派生层设计。
  - 但 OctoAgent 的权威事实层仍然是 SoR/Vault/WriteProposal，不能直接用 MemU categories/items 取代。

## Point 2: OpenClaw 官方 memory / compaction 文档

- 来源:
  - `https://docs.openclaw.ai/concepts/memory`
  - `https://docs.openclaw.ai/reference/session-management-compaction`
- 结论:
  - OpenClaw 把 memory search 设计为 `memory_search` / `memory_get` 两段式工具。
  - memory search 可以在主 backend 失败时 fallback。
  - pre-compaction memory flush 是显式执行链，在 soft threshold 前做 silent flush，再由 compaction summary 持久化。
- 对 028 的影响:
  - 028 的高级检索仍应遵循 `search -> inspect/get` 的消费模式。
  - `before_compaction_flush()` 应继续作为草案入口；真正 maintenance 要有 run/status/audit，而不是静默执行。

## Point 3: Agent Zero 官方 memory 文档

- 来源:
  - `https://www.agent-zero.ai/p/docs/memory/`
  - `https://www.agent-zero.ai/p/docs/projects/`
  - `https://www.agent-zero.ai/p/architecture/`
- 结论:
  - Agent Zero 将 memory dashboard、knowledge import、project memory isolation、cleanup/export 作为正式产品能力。
  - projects 自带独立 memory / secrets / knowledge 边界，避免 context bleed。
  - memory dashboard 与引擎能力是解耦的，先有引擎与隔离，再有浏览与编辑产品面。
- 对 028 的影响:
  - 028 应基于 project/workspace 提供 project-scoped memory engine 配置与诊断。
  - 027 负责 Memory Console 产品面；028 只负责 backend query/projection/integration hooks。
