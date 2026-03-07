# Spec Review: Feature 021 — Chat Import Core

**特性分支**: `codex/feat-021-chat-import-core`
**审查日期**: 2026-03-07
**审查范围**: FR-001 ~ FR-018

## 结论

- 结论: **PASS**
- 说明: 021 已形成 `octo import chats -> --dry-run -> 真实导入 -> ImportReport` 的最小用户闭环，并按 chat scope、artifact provenance、Memory proposal contract 落盘。

## FR 对齐检查

| FR | 状态 | 证据 |
|----|------|------|
| FR-001 | ✅ | `chat_import_commands.py` 新增 `octo import chats` |
| FR-002 | ✅ | dry-run 只读路径返回 `ImportReport(dry_run=true)`，不写 batch/report/event |
| FR-003 | ✅ | `ImportBatch / ImportCursor / ImportWindow / ImportSummary / ImportReport` 模型已落地 |
| FR-004 | ✅ | `source_message_id` 优先，否则回退 `sha256(sender_id + timestamp + normalized_text)` |
| FR-005 | ✅ | `chat_import_dedupe` 唯一键保证重复执行不重复写入 |
| FR-006 | ✅ | `chat_import_cursors` + `--resume` 支持增量继续 |
| FR-007 | ✅ | `scope_id=chat:<channel>:<thread_id>` 固定落地 |
| FR-008 | ✅ | raw window 写入 `ops-chat-import` artifact |
| FR-009 | ✅ | summary 写入 Memory fragment，metadata/evidence_ref 完整 |
| FR-010 | ✅ | fact hints 走 `propose_write -> validate_proposal -> commit_memory` |
| FR-011 | ✅ | 无 hints 或 validation 失败时降级为 fragment-only + warnings |
| FR-012 | ✅ | 每次真实导入持久化 `ImportReport` |
| FR-013 | ✅ | `CHAT_IMPORT_STARTED/COMPLETED/FAILED` 生命周期事件已写入 Event Store |
| FR-014 | ✅ | 使用 dedicated operational task `ops-chat-import` 承载事件与 artifact |
| FR-015 | ✅ | 复用主 SQLite / artifacts / Event Store，无第二套生产数据孤岛 |
| FR-016 | ✅ | 仅冻结 `normalized-jsonl` contract，不交付微信/Slack adapter |
| FR-017 | ✅ | dry-run 与真实导入都支持降级 warnings，不依赖在线 LLM |
| FR-018 | ✅ | CLI 输出 counts / warnings / errors / cursor，失败原因可见 |

## 边界场景检查

- dry-run 无副作用: ✅ `ops-chat-import` task、batch、report 都不会被创建
- 重复执行: ✅ dedupe ledger 命中后 `imported_count=0`
- resume 继续导入: ✅ cursor 命中后只处理边界之后的新消息
- 无 msg_id 场景: ✅ hash key 路径已实现并纳入 domain service
- fact hint 写入失败: ✅ 保留 proposal warnings，不污染 SoR current
