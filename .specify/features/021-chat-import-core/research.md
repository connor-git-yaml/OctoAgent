# Research Summary: Feature 021 — Chat Import Core

**Feature**: `021-chat-import-core`
**Created**: 2026-03-07
**Mode**: full

---

## 输入材料

- [产品调研](./research/product-research.md)
- [技术调研](./research/tech-research.md)
- [在线调研](./research/online-research.md)
- [产研汇总](./research/research-synthesis.md)

---

## 关键结论

1. 021 不能只做“导入内核”，必须补齐最小可用入口：
   - `octo import chats`
   - `--dry-run`
   - `ImportReport`

2. OpenClaw 与 Agent Zero 的共识都指向同一件事：
   - session/transcript 与 memory 必须分层
   - 导入结果必须可回看、可定位、可审计
   - 显式 CLI / UI 入口是可用性的基本门槛

3. 020 已冻结了 memory 治理 contract，021 应只消费：
   - `propose_write()`
   - `validate_proposal()`
   - `commit_memory()`
   - `search_memory()` / `get_memory()`

4. 最大实现缺口有四个：
   - 没有 generic input contract
   - 没有 dedupe ledger / cursor / batch/report durable schema
   - 没有导入生命周期审计链
   - 没有用户可触达入口

5. 021 的推荐 MVP 方案是：
   - 输入采用 `normalized-jsonl`
   - 原文窗口写 artifact
   - 摘要写 fragment
   - 事实候选通过 optional hints 走 proposal 仲裁
   - 生命周期通过 `ops-chat-import` 写 event chain

---

## 设计决策

- CLI 入口：`octo import chats`
- 输入契约：`normalized-jsonl`
- 共享连接策略：`create_store_group()` 后在同一连接上补 `init_memory_db()` 与 `init_chat_import_db()`，避免 core -> memory 反向依赖
- 摘要策略：MVP 使用 deterministic window summary，避免把 021 绑定到在线 LLM 可用性
- SoR 写入策略：只有存在明确 `fact_hints` 或足够证据时才生成 proposal；否则 fragment-only
- 审计链：`CHAT_IMPORT_STARTED / COMPLETED / FAILED`

---

## Gate 结论

- `GATE_RESEARCH`: PASS
- `GATE_DESIGN`: PASS
- 当前可进入：`plan -> data-model -> contracts -> tasks`
