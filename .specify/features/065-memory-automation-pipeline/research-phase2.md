# Research: Memory Automation Pipeline -- Phase 2

**Feature**: 065-memory-automation-pipeline
**Date**: 2026-03-19

## Decision 1: Derived Memory 提取方式

**问题**: Consolidate 产出新 SoR 后，如何自动提取 entity/relation/category 类型的 DerivedMemoryRecord？

### Option A: LLM 单次调用批量提取 (SELECTED)

**方案**: 将本次 consolidate 新产出的所有 SoR 内容拼接为一个 prompt，一次 LLM 调用提取所有 derived 记录。

**优点**:
- LLM 调用次数最少（1 次/batch），成本可控
- LLM 有全局视角，能识别跨 SoR 的关系
- 复用 ConsolidationService 的 LLM 调用模式（prompt + JSON 输出）

**缺点**:
- 单次 prompt 可能过长（但 MVP 阶段 SoR 数量有限）
- JSON 输出格式解析可能失败

**决策理由**: MVP 阶段单用户场景，单次 consolidate 产出的 SoR 通常 < 10 条，prompt 长度完全可控。一次调用比逐 SoR 调用更高效。

### Option B: 逐 SoR 独立提取

**方案**: 为每条新 SoR 单独调用一次 LLM 提取 derived。

**被拒原因**: LLM 调用次数与 SoR 数量线性增长，成本高且无法识别跨 SoR 关系。

### Option C: 基于规则的 NER/RE

**方案**: 使用 spaCy/jieba 等 NLP 工具做命名实体识别和关系抽取，不依赖 LLM。

**被拒原因**: 中文 NER 模型精度有限，难以提取语义层面的 relation 和 category；且 OctoAgent 是双语场景，中英文混合内容的处理成本更高。LLM 方案在质量上显著优于规则方案。

---

## Decision 2: Derived 记录写入路径

**问题**: DerivedMemoryRecord 通过什么路径写入 SQLite？

### Option A: 新增轻量写入方法 (SELECTED)

**方案**: 在 `SqliteMemoryStore` 上新增 `upsert_derived_records(scope_id, records)` 方法，直接写入 derived_memory 表。

**优点**:
- 写入路径简洁，不绕道 ingest_batch 的复杂参数结构
- 语义清晰：derived 写入就用 derived 专用方法
- 不需要构造 MemoryIngestBatch/MemoryIngestItem 等重量级对象

**缺点**:
- 需要新增一个 store 方法

**决策理由**: `ingest_batch` 是面向外部批量导入的入口，参数结构偏重（需要 ingest_id、items 列表等）。内部 derived 提取场景更适合轻量写入。

### Option B: 复用 SqliteMemoryBackend.ingest_batch

**方案**: 构造 MemoryIngestBatch 对象调用已有的 `ingest_batch` 方法。

**被拒原因**: 需要构造大量中间对象（MemoryIngestBatch, MemoryIngestItem, metadata 等），且 `_build_derived_records` 的逻辑是从 IngestItem metadata 中读取 entity/relation 字段，与 LLM 提取的输出格式不匹配。适配成本高于新增一个简单方法。

---

## Decision 3: Flush Prompt 实现策略

**问题**: 如何在 Compaction 前注入静默 agentic turn？

### Option A: 注入 LLM 调用 + JSON 输出 -> 逐条 memory.write (SELECTED)

**方案**: 在 Compaction 前注入一次 LLM 调用（system + user prompt），LLM 输出结构化 JSON（待保存的记忆列表），然后逐条调用 `memory.write` 工具走完整治理流程。

**优点**:
- 完全走治理流程，符合宪法原则 12
- LLM 输出结构化 JSON，可控性好
- memory.write 的 ADD/UPDATE 判断已实现（Phase 1），直接复用
- 参考 OpenClaw memory-flush.ts 的成熟模式

**缺点**:
- 多一次 LLM 调用的延迟和成本
- 需要在 Compaction 流程中注入额外步骤

**决策理由**: 宪法原则 12 要求"记忆写入必须治理"。直接从 LLM 输出写数据库是违规行为。通过 memory.write 工具调用保证了完整的 propose/validate/commit 治理流程。

### Option B: 注入 Agent tool-calling turn（真正的 agentic turn）

**方案**: 让 Agent 运行时直接执行一个真正的 tool-calling turn，Agent 自主决定是否调用 memory.write。

**被拒原因**: 需要深度侵入 Agent runtime 的 turn 执行循环，实现复杂度远高于 Option A。且 Compaction 触发时机可能不在 Agent turn 循环的正常流程中。

### Option C: 不注入 LLM 调用，优化现有 Flush summary prompt

**方案**: 修改现有 `_persist_compaction_flush` 中的摘要 prompt，让其输出更结构化的内容。

**被拒原因**: 现有 Flush 产出的是 Fragment（过程性记忆），而非 SoR（权威记忆）。即使优化 prompt，产出物仍需要后续 Consolidate 才能成为 SoR。Option A 直接产出 SoR，缩短了管线路径。两者不互斥：Option A 的 memory.write 调用保存关键信息为 SoR，原有 Flush 继续保存对话摘要为 Fragment 供后续 Consolidate 使用。

---

## Decision 4: Reranker 模型选型

**问题**: 使用哪个本地 Reranker 模型？

### Option A: Qwen3-Reranker-0.6B (SELECTED)

**方案**: 使用 Qwen/Qwen3-Reranker-0.6B，通过 sentence-transformers 的 CrossEncoder API 调用。

**优点**:
- 与已有的 Qwen3-Embedding-0.6B 同系列，运行时依赖一致（sentence-transformers）
- 模型小（~600MB），CPU 推理可接受（< 500ms / 10 candidates）
- 原生支持中英双语
- 支持 instruction-aware reranking（可提供查询意图指令提升精度）
- memU 项目已验证可用

**缺点**:
- 首次使用需从 HuggingFace 下载模型文件
- CPU 推理延迟比 GPU 高（但 10 条 candidates 在可接受范围内）

**决策理由**: 与项目已有的 Qwen3 生态一致（Embedding 也用 Qwen3-0.6B），运行时依赖统一。中英双语支持对 OctoAgent 的混合语言记忆至关重要。模型体积和推理速度在 MVP 规模下完全可接受。

### Option B: bge-reranker-v2-m3

**被拒原因**: 模型更大（~1.6GB），推理更慢，且不支持 instruction-aware reranking。在同等精度下，Qwen3-Reranker-0.6B 更轻量。

### Option C: LLM API reranking（通过 LiteLLM Proxy）

**被拒原因**: 在检索热路径上引入 LLM API 调用，延迟高（1-3 秒 vs < 500ms）、成本高、且增加了外部依赖。Reranker 应该是低延迟的本地操作。

### Option D: LanceDB 内置 reranker

**被拒原因**: LanceDB 的 `LinearCombinationReranker` 已在使用（粗排阶段 0.7 vec + 0.3 BM25），但它只是线性组合，不具备 cross-encoder 的语义理解能力。两者互补而非替代：LanceDB reranker 做粗排，Qwen3-Reranker 做精排。

---

## Decision 5: Reranker 集成层级

**问题**: Reranker 集成在哪个层级？

### Option A: MemoryService._apply_recall_hooks (SELECTED)

**方案**: 在 MemoryService 的 recall_memory -> _apply_recall_hooks 方法中，新增 `MemoryRecallRerankMode.MODEL` 分支。

**优点**:
- 与现有 HEURISTIC rerank 并行放置，代码组织清晰
- 复用 hook_options/hook_trace 机制，可观测性自动获得
- 降级路径清晰：MODEL 失败 -> fallback 到 HEURISTIC
- 不影响 BuiltinMemUBridge 的搜索层（粗排仍由 LanceDB 完成）

**缺点**:
- MemoryService 需要注入 ModelRerankerService 依赖

**决策理由**: recall 的 post-processing hook 已有成熟的插件点（hook_options.rerank_mode），新增 MODEL 模式只需在 if/elif 链中增加一个分支，改动量最小且与现有模式一致。

### Option B: BuiltinMemUBridge.search 中集成

**被拒原因**: rerank 发生在所有 scope 的搜索结果汇总之后（跨 scope 排序），而 BuiltinMemUBridge.search 是 per-scope 调用的。在 Bridge 层做 rerank 无法跨 scope 比较。

---

## Decision 6: Flush Prompt 与原有 Flush 的关系

**问题**: Flush Prompt 是替代还是补充原有的 Compaction Flush？

### Option A: 补充关系（两者共存）(SELECTED)

**方案**: Flush Prompt 在 Compaction Flush 之前执行，产出 SoR 记录。原有 Flush 继续执行，产出 Fragment 记录。两者产出物在不同层级（SoR vs Fragment），互不冲突。

**优点**:
- 不破坏现有 Flush 流程，改动风险低
- SoR 层捕获关键事实（通过 Flush Prompt），Fragment 层保留完整对话摘要（通过原有 Flush）
- Fragment 仍可被后续 Consolidate 处理，可能提取出 Flush Prompt 遗漏的信息
- 降级简单：Flush Prompt 失败时原有 Flush 照常工作

**缺点**:
- 可能出现 Flush Prompt 写入的 SoR 和后续 Consolidate 从 Fragment 提取的 SoR 内容重复
- 多一次 LLM 调用的成本

**决策理由**: 安全性优先。原有 Flush 是系统的保底机制，不应被替代。Flush Prompt 是增强层，即使完全失败也不影响现有行为。SoR 重复问题由 Consolidate 的去重逻辑（检查 existing_sor_map）处理。

### Option B: 替代关系（Flush Prompt 成功时跳过原有 Flush）

**被拒原因**: 如果 Flush Prompt 只保存了 2 条关键信息但跳过了 Fragment Flush，那么对话中的完整上下文（可能后续有用的中间过程信息）就丢失了。Fragment 作为原始素材的价值不应被 Flush Prompt 的结构化输出完全替代。
