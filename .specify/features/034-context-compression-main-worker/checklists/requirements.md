# Requirements Checklist — Feature 034

- [x] 主 Agent 续对话会读取完整历史，而不是只看最新一句
- [x] Worker 复用同一条上下文压缩路径
- [x] Subagent 明确绕过 compaction
- [x] `USER_MESSAGE` 持久化完整 `text`
- [x] compaction 产生 request snapshot artifact
- [x] compaction 成功时产生 summary artifact + `CONTEXT_COMPACTION_COMPLETED`
- [x] compaction 通过 `MemoryMaintenanceCommand(kind=FLUSH)` 回灌 Memory 治理层
- [x] summarizer 失败或空摘要时退回原始历史，不静默丢轮次
- [x] 已有定向 lint / pytest 作为门禁

