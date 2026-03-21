# 关键设计决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| 结构化存储 | SQLite WAL | Task/Event/Artifact 元信息，单用户足够 |
| 语义检索 | LanceDB | 嵌入式零运维 + 版本化存储 + 混合检索 + async 原生 |
| 编排模型 | 全层 Free Loop + Skill Pipeline | Orchestrator/Workers Free Loop；Skill Pipeline（pydantic-graph）作为确定性编排工具 |
| 模型网关 | LiteLLM Proxy | 统一 alias 路由，业务代码不写死厂商型号 |
| 执行隔离 | Docker 默认 | 安全边界清晰 |
| 事件溯源 | 最小 Event Sourcing | append-only events + tasks projection，崩溃不丢 |
| 门禁策略 | Safe by default + Policy Profile 可配 | 平衡安全与智能化 |
| A2A 兼容 | 内部超集 + A2AStateMapper 双向映射 | 内部保留治理状态，对外映射标准 A2A TaskState |
| Task 终态 | SUCCEEDED / FAILED / CANCELLED / REJECTED | REJECTED 区分策略拒绝与运行时失败 |
| Artifact 模型 | A2A parts 超集 + version/hash/size | 多 Part 结构对齐 A2A，支持版本化与流式追加 |
| Telegram | aiogram | 原生 async + FSM + 共享 event loop |
| Web UI | React + Vite | SSE 原生 EventSource 对接 Gateway |
| 可观测 | Logfire + structlog + Event Store | 自动 instrument Pydantic AI/FastAPI；Event Store 已有 metrics 数据源 |
