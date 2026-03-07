# Feature 020 技术调研：Memory Core + WriteProposal + Vault Skeleton

**日期**: 2026-03-07  
**调研模式**: full  
**核心参考**:
- `docs/blueprint.md` §8.7 / §9.9 / §13.4
- `.specify/memory/constitution.md`
- `_references/opensource/agent-zero/python/helpers/memory.py`
- `_references/opensource/agent-zero/python/helpers/vector_db.py`
- `_references/opensource/openclaw/extensions/memory-core/index.ts`
- `_references/opensource/openclaw/docs/reference/session-management-compaction.md`
- `_references/opensource/openclaw/src/cli/memory-cli.ts`
- `_research/16-R4-design-MemU-vNext记忆体系与微信插件化.md`
- `_research/13-R4-design-claude-code启发的AgentOS设计.md`
- `_research/15-R4-design-Graph+PydanticAI+LiteLLM三层架构.md`

## 1. 设计约束

Feature 020 必须同时满足以下硬约束：

1. 模型不得直接写 SoR，只能提交 `WriteProposal`。
2. 同一 `subject_key` 永远只有 1 条 `current`。
3. `Fragments` 必须 append-only。
4. Vault 分区默认不可检索。
5. 020 不包含 Chat Import Core、本地工作上下文 GC、微信 adapter。

## 2. 参考实现启发

### 2.1 Agent Zero

可借鉴：

- `Area` 元数据分区思路轻量有效，适合作为 020 的 `layer`/`kind` 枚举来源。
- embedding 元信息和正文元信息分开管理，有助于后续向量索引重建。
- 增量索引思路适合未来 Fragments/Chat Import 异步同步。

不应照搬：

- 直接对 vector store 做 insert/update/delete，不符合当前仓库的治理宪法。
- durable memory 与 imported knowledge 混在同一存储层，会模糊 020 与后续知识库能力边界。
- 过滤逻辑过于依赖字符串和运行期约定，不适合需要强约束的 SoR.current 查询。

### 2.2 OpenClaw

可借鉴：

- `memory_search` / `memory_get` 两段式读取契约。
- compaction 前 silent housekeeping / memory flush 钩子，适合给 020 预留 `before_compaction_flush()`。
- session store 与 transcript 分层持久化的思路，说明“运行态状态”与“记忆对象”应拆层。

不应照搬：

- 当前 memory core 更像文件检索系统，不是带仲裁的 SoR/Fragments domain model。
- compaction 本身属于 session/context manager，不能直接替代长期记忆层。

### 2.3 MemU / 本地研究材料

最重要的收敛结论：

- `area/type` 与 `partition/scope` 必须拆开。
- cheap/utility 模型适合做摘要、压缩、`subject_key` 归一和 proposal 草案。
- 020 的核心是“长期记忆 plane”，不是聊天工作上下文回收站。

## 3. 技术决策

### 3.1 数据模型

020 先落以下对象：

- `FragmentRecord`
- `SorRecord`
- `WriteProposal`
- `VaultRecord`
- `MemorySearchHit`
- `MemoryAccessPolicy`

关键字段：

- `scope_id`: `core` / `profile` / `work` / `chat:<channel>:<thread_id>`
- `partition`: `core` / `profile` / `work` / `health` / `finance` / `chat`
- `layer`: `fragment` / `sor` / `vault`
- `subject_key`
- `status`
- `version`
- `content`
- `metadata`
- `evidence_refs`

### 3.2 SQLite 持久化

020 先用 SQLite 完成元信息与一致性约束，不在 MVP 引入 LanceDB 写路径。

新增表：

- `memory_fragments`
- `memory_sor`
- `memory_write_proposals`
- `memory_vault`

关键约束：

- `memory_sor(scope_id, subject_key)` 对 `status='current'` 建 partial unique index
- `memory_fragments` append-only，无 update API
- `memory_write_proposals` 记录验证和提交状态，保留审计信息

### 3.3 服务接口

冻结以下五个核心接口：

- `propose_write()`
- `validate_proposal()`
- `commit_memory()`
- `search_memory()`
- `before_compaction_flush()`

接口语义：

- `propose_write()` 先落 proposal，不直接改 SoR
- `validate_proposal()` 负责证据存在性、action 合法性、当前版本冲突检测、Vault 路由
- `commit_memory()` 只接受已验证 proposal
- `search_memory()` 默认排除 Vault，且 SoR 默认只查 `current`
- `before_compaction_flush()` 当前只产出 proposal / fragment，不直接绑定 compaction 实现

### 3.4 检索策略

- SoR:
  - 默认 `status=current`
  - 支持按 `subject_key` 精确查询
  - 模糊搜索先走 SQLite `LIKE` / metadata filter，占位未来向量检索
- Fragments:
  - 按 `scope_id` + 时间倒序查询
  - 支持关键词和标签过滤
- Vault:
  - 默认拒绝
  - 未授权时不返回实体详情

## 4. 测试策略

### 单元测试

- `subject_key` 唯一 current 约束
- `UPDATE` 时旧版本自动转 `superseded`
- `DELETE` 只允许已有 current 的 subject
- `Fragments` 不提供 update 能力
- Vault 默认拒绝读取

### 集成测试

- `WriteProposal -> validate -> commit` 正常链路
- 同一 `subject_key` 并发/重复写入冲突
- `before_compaction_flush()` 只生成提案与摘要，不直接改 SoR
- `search_memory()` / `get_memory()` 对不同 layer 的返回语义

## 5. 结论

020 的最佳落点不是“做一个记忆向量库封装”，而是：

1. 用 SQLite 先锁死 SoR/Fragments/Vault 的行为约束。
2. 用强类型 proposal 和仲裁器把写入契约冻结。
3. 给未来 compaction、Chat Import、LanceDB、Vault 授权检索留明确接口。

