# Research Synthesis - Feature 054

## 1. 结论

这次规划的核心结论有三条：

1. **Memory 必须从“bridge 配置入口”升级成“内建记忆引擎”**
2. **MemU 风格能力应该承担加工智能，但不能接管最终治理**
3. **embedding/index 平台必须从第一天起同时服务 Memory 与未来知识库**

## 2. 当前实现基线

从当前代码与 blueprint 看，系统已经具备以下基础：

- `MemUBackend` 已是正式 memory engine adapter，而不是纯外挂检索器
- canonical governance 仍在本地：
  - SoR
  - Fragments
  - Vault
  - proposal / audit
- LanceDB 已在 blueprint 中被指定为 Memory / ToolIndex / 知识库共享的向量数据库
- `local_only` 当前实际不是“无 Memory”，而是带本地 SQLite metadata fallback 的 Memory 路径
- recall 主链已经具备：
  - expanded queries
  - keyword overlap post-filter
  - heuristic rerank

但当前产品心智仍落后于架构现状：

- README、Feature 045、Settings 都还在强调 `local_only / memu(command/http)` 配置路径
- 用户仍被迫理解 transport / bridge
- embedding 升级没有正式的 generation lifecycle

## 3. 外部对标启发

### OpenClaw

OpenClaw 的重要启发不是“它有没有单独的 memory 服务”，而是：

- 用户心智是 local-first 的
- 默认就有本地 memory search 能力
- provider/embedding 是升级层，不是功能前提
- 模型或维度变化时，系统做后台重建，而不是让用户去搬运数据真源

这说明我们的产品方向应该是：

- **去掉“独立部署 MemU”作为普通用户必懂概念**
- **把默认 Memory 做成内建能力**

### 轻量双语 embedding 候选

这次只形成结论，不锁死最终选型：

- `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  - 优点：小、便宜、双语/多语覆盖足够、适合保底默认层
  - 风险：上下文窗口偏短，更适合作为 bootstrap tier
- `Alibaba-NLP/gte-multilingual-base`
  - 优点：多语、上下文更长、适合作为更稳的默认主力候选
  - 风险：资源占用高于 MiniLM

规划结论是：

- **需要一个内建双语轻量 embedding 默认层**
- 最终具体模型在实施阶段结合包体积、首装体验和本机资源再定

## 4. 关键架构判断

### 4.1 canonical store 不能迁移

当用户换 embedding 时，不应在“非向量库”和“向量库”之间搬运 fragments/facts/vault。本体一直留在 canonical store，变化的只是 projection。

### 4.2 vector index 必须版本化

embedding 切换本质上是“新 generation 的 projection build”，不是“覆盖旧向量”。否则无法做到：

- 无缝切换
- 可取消
- 可回滚
- migration 期间不断流

### 4.3 facts / Vault 加工不该回到硬策略

facts / Vault 的加工可以交给 MemU 风格引擎；OctoAgent 只做：

- candidate 接收
- proposal / approval
- commit / grant
- audit / recovery

这是“智能加工”和“治理裁决”的正确分层。

### 4.4 知识库必须共用 retrieval platform

若现在只按 Memory 特例设计 embedding/index 生命周期，未来知识库一定会分叉。正确做法是提前抽象：

- `CorpusKind`
- `EmbeddingProfile`
- `IndexGeneration`
- `IndexBuildJob`

## 5. 对现有 Feature 的影响

### 对 Feature 045 的影响

Feature 045 的页面表达方向仍然有效：

- `/memory` 页面要讲用户语言
- 要解释当前状态与下一步

但它关于 `local_only / memu(command/http)` 的产品语义需要被 054 替代。045 里的 transport 表述会变成迁移期兼容信息，不再是长期主心智。

### 对 Settings 的影响

Settings 的 Memory 区不应该再是：

- 选择 backend mode
- 填 bridge transport / URL / command

而应该是：

- 当前 Memory engine 状态
- retrieval model bindings
- 当前 active embedding / migration progress
- 提升质量的下一步

## 6. 最终建议

054 应作为 Memory 长期主义纠偏 Feature 推进，明确以下新上游事实：

- **内建 Memory Engine 是默认产品面**
- **内建 embedding 是默认层，不只是 fallback**
- **向量库是 projection，不是真源**
- **embedding 切换必须有 generation lifecycle**
- **MemU 负责加工智能，OctoAgent 负责最终治理**
- **Memory 与知识库共享 retrieval platform**
