---
required: true
mode: tech-only
points_count: 2
tools: [mcp__openrouter-perplexity__web_search]
queries:
  - "Pydantic AI deferred tools DynamicToolset lazy loading 2025 2026"
  - "Claude Code ToolSearch deferred tools architecture implementation context optimization 2026"
findings:
  - "Pydantic AI 原生支持 DeferredToolRequests + DynamicToolset + ApprovalRequired，可直接集成"
  - "Claude API 原生支持 defer_loading: true 标记 + tool_search_tool (regex/BM25)，context 节省 85%+"
impacts_on_design:
  - "Deferred Tools 方案可同时在 API 层（Claude defer_loading）和框架层（Pydantic AI DynamicToolset）实现"
  - "ApprovalRequired 机制可直接桥接为权限 Preset 的 soft deny 实现"
---

# 在线调研证据 — Feature 061

## 调研点 1: Pydantic AI Deferred Tools / DynamicToolset

**查询**: Pydantic AI deferred tools DynamicToolset lazy loading 2025 2026

**关键发现**:

1. **DeferredToolRequests**: Pydantic AI 原生支持 deferred tools — 工具调用时抛出 `CallDeferred` 或 `ApprovalRequired`，agent run 返回 `DeferredToolRequests` 对象，外部处理后通过 `DeferredToolResults` 恢复执行
2. **DynamicToolset**: 通过 `@agent.toolset` 装饰器或 `RunContext` 依赖函数实现运行时工具集切换，每个 run step 可返回不同 toolset
3. **可组合 Toolset**: `PreparedToolset`、`FilteredToolset`、`CombinedToolset` 支持重命名、过滤、前缀等动态修改
4. **ApprovalRequired**: 专用异常类，工具抛出后触发审批流程，元数据可附加用于追踪 — **直接对应我们的 soft deny 需求**
5. **ExternalToolset**: 支持前端/服务提供的外部工具，按需加载

**对设计的影响**:
- 权限 Preset 的 soft deny 可直接用 `ApprovalRequired` 实现，无需自建审批桥接
- `DynamicToolset` 可用于实现 Deferred → Active 的运行时工具提升
- `FilteredToolset` 可用于 Core Tools 过滤（始终加载的工具集）

## 调研点 2: Claude API Tool Search 原生支持

**查询**: Claude Code ToolSearch deferred tools architecture implementation context optimization 2026

**关键发现**:

1. **defer_loading 标记**: Claude API 原生支持在工具定义上设置 `defer_loading: true`，模型初始不看到这些工具的完整 schema
2. **tool_search_tool**: 两个变体 — `tool_search_tool_regex_20251119`（正则匹配）和 `tool_search_tool_bm25_20251119`（BM25 关键词排序）
3. **性能数据**: context 减少 85%+（多 server 场景从 55k tokens 降至数千），工具选择准确率从 49% 提升到 74%
4. **规模**: 支持最多 10,000 个 deferred tools，200 字符正则限制
5. **Claude Code 实践**: 当工具描述超过 10k tokens 时自动启用 deferred，配合 1M context window 和 compaction API
6. **兼容性**: Claude 4.6+ 模型支持，包括 opus-4-6

**对设计的影响**:
- **双层实现路径**:
  - API 层：直接用 Claude `defer_loading: true`（零开发成本，但绑定 Claude 模型）
  - 框架层：通过 Pydantic AI `DynamicToolset` + 自建 `tool_search` 工具（模型无关，兼容 LiteLLM）
- **推荐**: 框架层实现为主（模型无关），API 层 defer_loading 作为 Claude 模型的加速优化
- 10k 工具上限远超 OctoAgent 当前规模（~49 内置 + MCP），无需担心容量
