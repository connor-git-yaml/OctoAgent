# Research: Session 驱动统一记忆管线

**Feature**: 067-session-driven-memory-pipeline
**Date**: 2026-03-19

---

## RD-001: 记忆提取 LLM 调用策略 -- 单次 vs. 多次

**Decision**: 单次 LLM 调用完成所有类型记忆提取（facts + solutions + entities + ToM）

**Rationale**:
- 现有 ConsolidationService 已经验证了单次 LLM 调用 + JSON 数组输出的模式可行
- 多次调用（先分类再分别提取）会导致延迟和成本成倍增加
- `fast` alias 模型（如 GPT-4o-mini、Claude 3.5 Haiku）的结构化输出能力足以一次性输出多类型结果
- 统一 prompt 可以让 LLM 在全局语境下做出更好的提取判断

**Alternatives Rejected**:
- 多轮分类调用：延迟高、成本高，且分类边界模糊（一段话可能同时包含 fact 和 solution）
- 先 heuristic 分类再 LLM 提取：规则维护成本高，容易误判导致信息丢失

---

## RD-002: Memory Cursor 存储位置

**Decision**: 在 AgentSession 模型新增 `memory_cursor_seq: int = 0` 字段

**Rationale**:
- Cursor 与 AgentSession 生命周期绑定——Session 创建时初始化、Session 运行期间递增
- AgentSession 已有持久化存储（`agent_sessions` SQLite 表），复用现有 store 无需新建表
- 整型字段轻量，不影响 AgentSession 的序列化性能
- 通过 AgentContextStore 的现有 save/get 方法即可读写

**Alternatives Rejected**:
- 独立 `memory_cursor` 表：增加 schema 和 store 复杂度，cursor 与 session 是 1:1 关系，没必要分表
- 存储在 metadata dict 中：类型不安全，查询不方便，无法利用 SQL 索引

---

## RD-003: 提取输入构建策略

**Decision**: 从 `agent_session_turns` 表查询 `turn_seq > cursor` 的 turns，压缩 tool calls

**Rationale**:
- `agent_session_turns` 是 turns 的单一事实源，有 `turn_seq` 索引，查询高效
- 相比从 `recent_transcript`（内存中的 list[dict]）读取，turns 表不受 compaction 截断影响
- Tool Call 压缩：只保留 `tool_name + summary`，过滤原始 input/output，减少 token 消耗
- User/Assistant 消息保留 `summary` 字段内容（已是压缩后的文本）

**Alternatives Rejected**:
- 从 `recent_transcript` 读取：compaction 后会丢失历史 turns，不适合增量提取
- 完整传入 tool call 内容：token 浪费严重，且可能包含敏感数据（文件内容、命令输出）

---

## RD-004: 并发防护机制

**Decision**: per-Session `asyncio.Lock` + try-lock 语义

**Rationale**:
- 单进程 Python asyncio 环境下，`asyncio.Lock` 是最轻量的互斥原语
- try-lock 通过 `Lock.locked()` 检查实现——若已锁定则跳过，不排队等待
- 锁存储在 `SessionMemoryExtractor` 实例的 `dict[str, asyncio.Lock]` 中，Session 维度隔离
- 无需分布式锁（单用户单进程部署）

**Alternatives Rejected**:
- 数据库行锁（SELECT FOR UPDATE）：SQLite WAL 不支持行级锁，且增加 DB 压力
- 队列（asyncio.Queue）：过于复杂，try-lock 语义更简单直接
- 无防护（依赖 cursor 幂等）：虽然 SoR 写入通过 subject_key 去重，但会浪费 LLM 调用

---

## RD-005: 废弃路径移除策略

**Decision**: 直接删除代码，不保留 feature flag 开关

**Rationale**:
- 旧路径和新路径同时存在会导致双写和混乱，不存在"渐进式迁移"的价值
- 旧路径代码清晰可辨（`_record_memory_writeback`、`_persist_compaction_flush` 中的 FlushPromptInjector 调用、`_auto_consolidate_after_flush`、FlushPromptInjector 整个文件）
- Scheduler Consolidation 作为兜底保留，可以处理过渡期遗留 Fragment
- Git history 提供完整回溯能力，无需在代码中保留死代码

**Alternatives Rejected**:
- Feature flag 控制新旧路径切换：增加代码复杂度，且新旧路径并存本身就是 spec 反对的
- 仅注释不删除：死代码会造成理解负担，违背代码卫生原则

---

## RD-006: 提取产出写入流程

**Decision**: 复用现有 `MemoryService.propose_write → validate_proposal → commit_memory` 治理流程

**Rationale**:
- propose-validate-commit 三阶段流程已经过 Feature 065 验证，成熟稳定
- 内置 subject_key 去重检测，天然支持崩溃恢复后的幂等重试
- 支持 ADD / UPDATE / MERGE / REPLACE 四种写入策略
- 保证 Constitution 原则 12（记忆写入必须治理）

**Alternatives Rejected**:
- 直接写入 SoR 跳过治理：违反 Constitution，无去重保护
- 自定义写入逻辑：重复造轮子，且失去与 Consolidation 的一致性

---

## RD-007: Fragment 角色转变实现

**Decision**: 提取管线产出同时写入 SoR（通过治理流程）和 Fragment（作为证据），Fragment 通过 `evidence_ref` 关联到 SoR

**Rationale**:
- Fragment 保留原始对话段落，为 SoR 提供审计和溯源能力
- 现有 Fragment 写入接口（`run_memory_maintenance`）可以复用
- 记忆检索（recall）已经优先返回 SoR，Fragment 自然降为次要结果
- 不需要修改 recall 排序逻辑，只需确保新写入的 Fragment metadata 包含 `evidence_for_sor_id`

**Alternatives Rejected**:
- 完全不写 Fragment：失去溯源能力，违背可观测性原则
- 改造 Fragment 为 SoR 的子表：schema 变更范围过大，不符合增量改进原则

---

## RD-008: LLM 提取 Prompt 设计

**Decision**: 统一 system prompt 覆盖 facts + solutions + entities + ToM，user prompt 传入压缩后的 turns

**Rationale**:
- 现有 ConsolidationService 的 `_CONSOLIDATE_SYSTEM_PROMPT` 已验证了 facts 提取的 prompt 模式
- 扩展为包含 solutions（复用 `_SOLUTION_EXTRACTION_PROMPT` 的逻辑）、entities、ToM 四种类型
- 输出格式统一为 JSON 数组，每条记录包含 `type` 字段区分类型
- LLM 在单次调用中同时看到所有对话内容，可以做出更整体性的判断

**Alternatives Rejected**:
- 直接复用 ConsolidationService：ConsolidationService 的输入是 Fragment 列表，而提取管线的输入是 turns，数据结构不同
- 拆分为 4 个独立 prompt：增加 LLM 调用次数和成本
