# Feature 038 调研综合：Agent Memory Recall Optimization

## 1. 本仓真实现状

- `MemoryConsoleService` 已经通过 `MemoryBackendResolver` 按 project/workspace 解析 backend。
- 但本轮 review 前，主 Agent runtime、context compaction flush、chat import 并没有统一消费这条 resolver 主链。
- 这意味着 MemU/project-scoped backend 在控制台可见，但在主聊天和导入链路上并不一定真实生效。

## 2. OpenClaw 主仓源码结论

- OpenClaw 的强项在 runtime memory contract：`memory_search -> memory_get -> prompt`，以及 session-memory hook、pre-compaction memory flush、source-aware memory/session 区分。
- 它并没有在主仓里提供正式的 MemU runtime integration。
- 因此，对 OctoAgent 真正有价值的是“runtime recall 的产品面”和“记忆使用时机”，而不是照抄一个并不存在的 MemU 集成层。

## 3. OpenClaw 上实际 MemU 用法结论

- 你在 OpenClaw 上的实际 MemU 使用主要落在 `_references/openclaw-snapshot/data/workspace/projects/wechat-memory/` 里的脚本：
  - `memu_integration.py`：以 `MemoryAgent.call_function("add_activity_memory")` 做分步提取，`RecallAgent.retrieve_default_category()` 做 recall
  - `memu_daily.py`：实践里把 Step 1 事实提取保留，Step 2/3 suggestion/update 因稳定性问题关闭；另有 `use_v1` 的结构化提取 + 全局压缩
  - `memu_link_memories.py`：通过 embeddings 打开“关联记忆链接”能力
- 这些脚本体现了三个有价值的方向：
  - 分步提取优于一次性黑盒 run
  - 压缩层要和事实提取分开
  - recall / link 可以作为后处理增强，而不是一开始就绑死在写入主链
- 但它们不是正式的产品 runtime，也不具备 OctoAgent 要求的 project isolation、approval、audit 和 durability contract。

## 4. Agent Zero 源码结论

- Agent Zero 在 monologue start 初始化 memory index，支持 embedding cache、knowledge preload、embedding 配置变更后的重建。
- recall 发生在 prompt assembly 期间，具有 query prep、可选 post-filter、delayed recall。
- monologue end 还有 fragments / solutions 的记忆回写，但这条链依赖后台线程 `DeferredTask`，不是 durable queue。
- 可借鉴点：
  - recall 是 prompt 组装期的一等能力
  - query expansion / post-filter 能提升 recall 质量
  - history compression 与 memory writeback 分链处理
- 不应照抄点：
  - 单一可变 FAISS 兼作索引和事实源
  - best-effort 后台写入
  - 基于 `simpleeval` 的宽松 metadata filter

## 5. 对 Feature 038 的落地结论

应该吸收的：

- runtime recall 需要正式 contract，而不是 search 列表
- recall 需要 query expansion、citation、preview、backend truth
- indexing/write path 和 recall/read path 必须共享同一 project-scoped resolver
- `memory.recall` 应成为 built-in tool，而不是要求调用方手工拼多次 memory tool

不应吸收的：

- 旁路 SoR / Vault / WriteProposal 的可变 memory store
- 无治理的脚本式落地目录
- 非 durable 的后台回写线程
