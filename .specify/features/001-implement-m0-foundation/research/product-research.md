# OctoAgent M0 产品调研报告

> **调研日期**: 2026-02-28
> **调研范围**: M0 里程碑（基础底座）— Task/Event/Artifact + SSE 事件流 + 最小 Web UI
> **调研方法**: Web 搜索（Perplexity）+ 项目代码库分析 + LLM 知识库
> **分析师**: 产品调研子代理

---

## 1. 执行摘要

OctoAgent M0 的定位是"任务账本 + 事件流"基础底座，旨在为后续所有智能化能力提供持久化、可观测的数据基础。本次调研分析了 **6 个竞品**（Agent Zero、OpenClaw、Pydantic AI、AIOS、LangGraph、CrewAI），验证了 M0 的市场定位和用户场景合理性，并提出 MVP 范围优化建议。

**核心结论**：

1. **任务+事件+产物 三位一体的底座设计是行业共识**。所有成熟 Agent 框架都在其核心层构建了类似机制，OctoAgent 的设计方向正确
2. **Event Sourcing 是差异化优势**。多数竞品仅有简单的日志/事件总线，OctoAgent 的 append-only + projection 方案在可回放性和崩溃恢复上具有结构性优势
3. **M0 范围合理但需微调**。建议将"最小 LLM 回路"简化为 echo/mock 模式即可端到端验证，降低 M0 对外部依赖的耦合
4. **Web UI 可进一步精简**。task 列表 + 事件时间线是核心，其余可推迟

---

## 2. 市场需求验证

### 2.1 市场现状与趋势

AI Agent 基础设施市场正处于爆发期：

- **市场规模**：全球 AI Agent 市场 2025 年约 78.4 亿美元，预计 2030 年达 526.2 亿美元（CAGR 46.3%）
- **开源爆发**：2025-2026 年开源 Agent 框架迎来井喷期，OpenClaw 在 2026 年初达到 190K+ GitHub stars，Agent Zero 14.4K stars
- **关键趋势**：
  - **Durable Execution 成为共识**：从"事件驱动"向"持久化执行"演进，强调崩溃恢复和状态不丢失
  - **可观测性是产品功能**：不仅是运维需求，而是用户面向的核心体验
  - **本地优先 + 隐私**：个人 AI OS 场景下，本地部署、数据自主成为刚需
  - **Context/Memory 成为新护城河**：模型同质化后，记忆和上下文管理成为核心竞争力

### 2.2 目标用户需求痛点

基于 OctoAgent 的 Persona（Owner / 个人开发者），核心痛点为：

| 痛点 | 描述 | 竞品解决程度 |
|------|------|------------|
| 任务失联 | 长任务中断后无法恢复，上下文丢失 | Agent Zero/OpenClaw 部分解决（cron 重跑），无真正 checkpoint |
| 不可观测 | 不知道 Agent 在做什么、花了多少钱 | LangGraph + LangSmith 最佳，但耦合重 |
| 事件不可追溯 | 无法回放和审计 Agent 行为 | 多数框架仅有日志，无结构化事件溯源 |
| 产物散落 | Agent 产出的文件/报告无统一管理 | CrewAI 有 S3 方案，但偏重云端 |
| 安全失控 | Agent 执行不可逆操作时缺乏门禁 | Pydantic AI 有工具 schema，但无审批流 |

### 2.3 市场机会评估

OctoAgent 的差异化定位为 **"个人 AI OS + Event Sourcing 底座"**，这在现有竞品中是一个空白点：

- Agent Zero / OpenClaw：偏"使用型产品"，底层数据模型不够严谨
- LangGraph / CrewAI：偏"框架型工具"，缺乏个人 OS 的完整体验
- AIOS：偏"学术型内核"，实际可用性待验证
- Pydantic AI：偏"开发组件"，不是完整系统

**结论**：M0 作为"可观测的任务账本"切入，是正确的差异化策略。先建立数据基础，再叠加智能化能力。

---

## 3. 竞品分析

### 3.1 竞品概览

| 维度 | Agent Zero | OpenClaw | Pydantic AI | AIOS | LangGraph | CrewAI |
|------|-----------|----------|-------------|------|-----------|--------|
| **定位** | 自治 AI Agent | 个人 AI 网关 | Agent 开发框架 | AI OS 内核 | 状态机 Agent 框架 | 多 Agent 协作 |
| **GitHub Stars** | 14.4K | 190K+ | 高 | 4.9K | 高 | 高 |
| **语言** | Python | Node.js | Python | Python | Python | Python |
| **任务系统** | Cron + ad-hoc | Session + Cron | GraphTask | Syscall 队列 | State Machine | Crew + Flow |
| **事件系统** | 向量 DB 记忆 | Block Streaming | GraphRun 事件 | 无 | Stream Events | Event Bus |
| **Event Sourcing** | 无 | 无 | 无（有 Persistence） | 无 | Checkpoint | 无 |
| **Artifact 管理** | 文件输出 | 技能输出 | 无专用方案 | 存储管理器 | 无专用方案 | S3 存储 |
| **Web UI** | 内置 Web UI | Control UI | 无（依赖 Logfire） | Terminal UI | LangSmith | AMP Dashboard |
| **可观测** | 内存日志 | 日志 + 事件监控 | Logfire 集成 | 无 | LangSmith/Langfuse | SigNoz/自有 |
| **隔离执行** | Docker 原生 | Node.js 进程 | 无 | OS 级隔离 | 无 | 无 |

### 3.2 各竞品深度分析

#### 3.2.1 Agent Zero

**任务管理**：
- 支持计划任务（cron 语法）、定时任务（指定日期）和即席任务
- 任务与项目（Project）关联，自动加载项目资源（secrets、memory、上下文）
- 实时监控：历史记录、状态（idle/running/error）、手动停止
- **不足**：无结构化任务状态机，无 checkpoint 恢复

**事件系统**：
- 无专门的事件系统，依赖向量数据库的持久化记忆
- "utility messages" 自动记忆化到向量 DB
- **不足**：无 append-only 事件流，无法回放

**Artifact**：
- 文件直接输出到工作目录，无专门的产物管理
- **不足**：无版本化、无 hash 校验

**Web UI**：
- 内置完整 Web UI（端口 50080）
- Chat 界面 + 配置编辑 + 项目管理
- **优点**：开箱即用；**不足**：与核心代码耦合紧密

**对 M0 的启示**：Agent Zero 验证了 Docker 隔离执行方案的可行性，但其"重使用轻底层"的路线导致可审计性和可恢复性不足。OctoAgent M0 应确保底层数据模型比 Agent Zero 更严谨。

#### 3.2.2 OpenClaw

**任务管理**：
- 通过 Session + Cron Job 管理任务
- "Mission Control" 社区 Dashboard：Trello 风格的任务看板（planning/in-progress/done）
- 支持子代理追踪
- **优点**：丰富的社区插件生态

**事件系统**：
- Block Streaming 用于 Agent 输出流式传输
- "event-watcher" 技能处理实时事件流（Redis Streams 或 Webhook JSONL）
- 有社区提案的标准化 Agent Event Stream API
- **不足**：核心无内置结构化事件系统，依赖插件

**Web UI**：
- 原生 Control UI（:18789）：Chat + 配置 + Session + 节点管理
- 社区扩展 "task-monitor"：实时仪表板，移动端响应式
- **优点**：渠道丰富（50+ channels）；**不足**：UI 方案分散

**对 M0 的启示**：OpenClaw 的渠道适配能力和社区生态是其核心优势，但底层数据模型简单（无 Event Sourcing）。OctoAgent 应在 M0 确保底层比 OpenClaw 更坚实，渠道能力留到 M2。

#### 3.2.3 Pydantic AI + pydantic-graph

**任务管理**：
- `GraphTask` 对象表示工作单元，包含 `node_id` 和 `inputs`
- 支持异步迭代执行，`GraphRun` 管理执行状态
- `next_task` / `output` / `next()` 方法控制执行流
- **优点**：类型安全、强 contract

**事件/状态持久化**：
- 内置三级持久化：SimpleStatePersistence（内存）、FullStatePersistence（全历史内存）、FileStatePersistence（JSON 文件）
- 支持中断和恢复，适配 human-in-the-loop
- **不足**：无 SQLite 适配器，需 OctoAgent 自行实现

**可观测**：
- Logfire 集成：自动 instrument Pydantic AI / pydantic-graph / FastAPI
- 零手动打点，token 计数、cost 追踪、流式调用追踪
- **优点**：OctoAgent 选型已确认，M0 可受益

**对 M0 的启示**：pydantic-graph 的 Persistence 接口是 OctoAgent checkpoint 的天然基础。M0 不需要 pydantic-graph，但数据模型应预留 checkpoint 表结构（blueprint 已规划）。Logfire 集成可在 M0 的 structlog 基础上渐进引入。

#### 3.2.4 AIOS

**任务管理**：
- 类 OS 内核设计：调度器将 Agent 查询分解为线程绑定的系统调用
- 支持 FIFO/RR 调度算法、上下文切换（中断/恢复）
- **优点**：学术严谨的内核抽象；**不足**：实际可用性待验证

**事件系统**：
- 无 Event Sourcing 设计
- 基于系统调用的运行时管理，而非事件流
- **不足**：缺乏可审计性和可回放性

**对 M0 的启示**：AIOS 的"内核化"思路验证了将 Agent 基础设施建模为 OS 组件的合理性，但其跳过了事件溯源这一关键层。OctoAgent M0 的 Event Sourcing 设计填补了这一空白。

#### 3.2.5 LangGraph

**任务管理**：
- 图（Graph）模型：节点 = 动作/工具，边 = 条件流转
- 显式状态 Schema（TypedDict + reducers）贯穿所有步骤
- 支持循环、检查点、human-in-the-loop 中断
- **优点**：最成熟的状态机方案

**事件流**：
- `stream_mode="events"` 发射实时更新（on_chain_start/end, on_chat_model_stream）
- LangGraph Cloud SDK 提供 React hooks（useStream）用于 Web UI
- **优点**：前后端一体化的流式方案

**可观测**：
- LangSmith：Agent 图可视化、token/延迟追踪
- 支持 Langfuse（自托管）、LangWatch（多维监控）
- **不足**：深度绑定 LangChain 生态

**对 M0 的启示**：LangGraph 的 `stream_mode="events"` 和 React hooks 是 SSE 事件流的参考实现。OctoAgent 的 SSE 方案可参考其事件类型设计，但不必依赖 LangChain 生态。

#### 3.2.6 CrewAI

**任务管理**：
- Crew + Agent + Task 三层模型
- 支持顺序执行和层次化流程
- Flows：事件驱动工作流，包含状态管理和条件逻辑
- **优点**：多 Agent 协作模式成熟

**事件系统**：
- `crewai_event_bus`：发布-订阅模式
- 事件类型：CrewKickoffStartedEvent, TaskStartedEvent, LLMCallStartedEvent 等
- 自定义监听器扩展 `BaseEventListener`
- **不足**：非持久化事件总线，无 Event Sourcing

**Artifact 存储**：
- S3 兼容存储（Tigris / MinIO / AWS S3）
- 路径组织：`s3://bucket/crewai/<crew_name>/<run_id>/inputs/artifacts/final/`
- **优点**：生产级存储方案；**不足**：对个人使用偏重

**对 M0 的启示**：CrewAI 的事件类型分类（Crew/Agent/Task/Tool/Memory 级别）值得参考。OctoAgent 的 Event type 枚举已涵盖类似分类。Artifact 的本地文件系统方案对 M0 足够。

### 3.3 功能矩阵对比

| 功能维度 | OctoAgent M0 (计划) | Agent Zero | OpenClaw | Pydantic AI | LangGraph | CrewAI |
|---------|-------------------|-----------|----------|-------------|-----------|--------|
| **Task 状态机** | 10 态完整 FSM | 简单状态(idle/running/error) | 3 态(planning/in-progress/done) | GraphTask 基础 | 显式状态 Schema | 基础状态 |
| **Event Sourcing** | append-only + projection | 无 | 无 | 无 | Checkpoint 近似 | 无 |
| **SSE 事件流** | `/stream/task/{id}` | 无 | Block Streaming | 无 | stream_mode=events | 无 |
| **Artifact 管理** | 多 Part + 版本化 + hash | 文件输出 | 技能输出 | 无 | 无 | S3 存储 |
| **崩溃恢复** | Event replay + projection rebuild | 无 | 无 | File Persistence | Checkpoint | 无 |
| **Web UI** | Task 列表 + 事件时间线 | 完整 Chat UI | Control UI + Mission Control | Logfire Dashboard | LangSmith | AMP Dashboard |
| **可观测性** | structlog + trace_id | 内存日志 | 日志 + 插件监控 | Logfire 自动打点 | LangSmith 全链路 | Event Bus 监控 |
| **数据库** | SQLite WAL | 向量 DB | Redis + 文件 | 文件/内存 | 外部 DB | 内存 + S3 |

### 3.4 差异化机会识别

基于竞品分析，OctoAgent 在以下方面具有差异化机会：

1. **Event Sourcing 原生**：OctoAgent 是调研范围内唯一将 Event Sourcing 作为核心架构的 Agent 系统。append-only events + Task projection 提供了竞品缺乏的可回放性和崩溃恢复能力
2. **Artifact 版本化 + 完整性校验**：多 Part 结构 + hash + version 在个人 Agent 框架中独树一帜。多数竞品仅支持简单文件输出或依赖 S3
3. **10 态 Task FSM**：包含 WAITING_APPROVAL / PAUSED / REJECTED 等治理状态，是竞品中最完整的任务状态机
4. **本地优先 + 全栈可控**：SQLite WAL + 本地文件系统 + Docker 隔离，不依赖云服务，适合个人 AI OS 定位
5. **Constitution 驱动**：将安全约束（Two-Phase / Least Privilege）编码为系统行为，而非仅靠 prompt 约束

---

## 4. 用户场景验证

### 4.1 核心 Persona

**P1: Owner（个人开发者 / Power User）**

- **身份**：技术型用户，熟悉命令行和 API
- **核心需求**：让 AI Agent 帮助处理日常运维、调研、开发任务
- **痛点**：
  - 长任务执行中断后，无法知道执行到了哪一步
  - Agent 产出散落各处，无法追溯
  - 缺乏"系统在做什么"的可视化
- **使用场景**：本地 Mac + 局域网设备，7x24 运行

### 4.2 M0 用户旅程验证

#### 场景 1：提交一个任务并观察执行过程

```
用户发送消息 → 系统创建 Task → 记录 USER_MESSAGE 事件
→ (M0: mock LLM 回复) → 记录 MODEL_CALL 事件
→ SSE 推送到 Web UI → 用户看到事件时间线
→ Task 变为 SUCCEEDED → 用户在任务列表看到完成状态
```

**验证结论**：此场景完全对应 M0 的设计目标。端到端可验证，且不需要复杂的 Agent 逻辑。

#### 场景 2：系统重启后查看历史任务

```
用户重启系统 → 打开 Web UI
→ 看到之前创建的所有 Task（状态、标题、时间）
→ 点击某个 Task → 看到完整的事件时间线
→ 确认数据未丢失
```

**验证结论**：此场景直接验证 Constitution 第 1 条"Durability First"。SQLite WAL 保证此能力。

#### 场景 3：查看 Task 产出的 Artifact

```
Task 执行过程中生成报告/文件 → 记录 ARTIFACT_CREATED 事件
→ Artifact 元信息入 SQLite，文件存文件系统
→ 用户在事件流中看到 Artifact 引用
→ (M0: 仅支持查看元信息，下载功能可后续)
```

**验证结论**：Artifact store 在 M0 完成元信息 + 文件存储即可。UI 上展示 Artifact 元信息（名称、类型、大小）已足够验证。

### 4.3 场景验证总结

| 场景 | M0 覆盖度 | 评估 |
|------|----------|------|
| 提交任务 + 观察事件流 | 完全覆盖 | 核心场景，必须实现 |
| 系统重启后数据不丢 | 完全覆盖 | Constitution 核心验证 |
| Artifact 基本管理 | 基本覆盖 | 元信息+存储即可，UI展示可简化 |
| 多 Task 列表浏览 | 完全覆盖 | Web UI 核心功能 |
| 任务取消/重试 | 部分覆盖 | M0 可支持 cancel 到终态，retry 留到 M1 |

**总体结论**："任务账本 + 事件流"作为 MVP 切入点是正确的选择。它直接验证了 Constitution 的两条核心原则（Durability First + Everything is an Event），同时提供了直观的用户价值（"我能看到系统在做什么"）。

---

## 5. MVP 范围评估

### 5.1 M0 现有范围审查

Blueprint 定义的 M0 范围：

| 模块 | 内容 | 评估 |
|------|------|------|
| SQLite schema + event append API + projection | 核心底座 | **Must-have** - 一切的基础 |
| `/ingest_message` 创建 task + 写事件 | API 入口 | **Must-have** - 端到端验证起点 |
| `/stream/task/{task_id}` SSE 事件流 | 实时推送 | **Must-have** - 可观测性核心 |
| Artifact store（文件系统） | 产物管理 | **Must-have** - 但可简化（仅 text/file 两种 Part） |
| structlog + request_id/trace_id | 可观测基础 | **Must-have** - 开发调试必需 |
| 最小 LLM 回路（hardcoded model call） | 端到端验证 | **Should-have** - 建议简化为 mock/echo |
| 最小 Web UI（task 列表 + 事件流） | 用户界面 | **Must-have** - 但可极度精简 |

### 5.2 范围调整建议

#### 建议纳入 M0 的功能

| 功能 | 理由 | 优先级 |
|------|------|--------|
| Task 取消 API (`POST /tasks/{id}/cancel`) | 验证 Constitution "User-in-Control"；状态机中 CANCELLED 终态是核心路径 | Must-have |
| 健康检查端点 (`GET /health`) | 开发调试和后续集成的基础；实现成本极低 | Must-have |
| Event type 完整枚举 | M0 仅用到部分类型，但枚举定义应一次到位，避免后续破坏性变更 | Must-have |

#### 建议从 M0 简化的功能

| 功能 | 简化方案 | 理由 |
|------|---------|------|
| 最小 LLM 回路 | 改为 echo/mock 模式：收到消息后直接生成一条 mock 回复事件 | 降低对外部 LLM Provider 的依赖，确保 M0 可独立运行和测试。真正的 LLM 集成留到 M1 |
| Artifact 多 Part 结构 | M0 仅实现 text 和 file 两种 Part 类型 | json/image Part 在 M0 无消费者，可推迟 |
| Web UI | 极简方案：Task 列表页 + Task 详情页（事件时间线） | 无需 Chat 界面、无需 Artifact 下载、无需配置管理 |

#### 建议推迟到 M1+ 的功能

| 功能 | 推迟到 | 理由 |
|------|-------|------|
| Checkpoint 表结构 | M1.5 | M0 无 Graph/Skill Pipeline，无需 checkpoint |
| Approvals 表结构 | M1 | M0 无 Policy Engine，无需审批流 |
| 多线程/scope 管理 | M2 | M0 只需单线程验证 |
| Artifact 流式追加 (append mode) | M1+ | M0 无流式 LLM 输出场景 |

### 5.3 M0 MVP 功能分级

#### Must-have（M0 必须交付）

1. **SQLite Schema**：tasks / events / artifacts 三张核心表
2. **Event Append API**：写入事件 + 同事务更新 Task projection
3. **REST API**：
   - `POST /ingest_message` — 创建 Task + 写 USER_MESSAGE
   - `GET /tasks` — Task 列表（分页、按状态筛选）
   - `GET /tasks/{id}` — Task 详情
   - `POST /tasks/{id}/cancel` — 取消任务
   - `GET /stream/task/{id}` — SSE 事件流
   - `GET /health` — 健康检查
4. **Artifact Store**：文件系统存储 + SQLite 元信息
5. **structlog 配置**：request_id / trace_id 贯穿
6. **最小 Web UI**：Task 列表 + Task 详情（事件时间线）

#### Should-have（M0 尽量交付）

1. **Echo/Mock LLM 回路**：模拟 MODEL_CALL + 回复事件，端到端验证流程
2. **事件重建**：从 events 表重建 Task 状态的能力（projection rebuild）
3. **基本错误处理**：API 错误响应格式统一

#### Nice-to-have（M0 可选）

1. Artifact 内容在 Web UI 中的预览
2. Task 筛选/搜索功能
3. 深色模式 / 移动端适配

### 5.4 推荐实现顺序

```
第 1 阶段（3-4 天）：数据层
├── SQLite schema 设计 + 迁移脚本
├── Event Store API（append + query）
├── Task projection 逻辑
└── 单元测试

第 2 阶段（3-4 天）：API 层
├── FastAPI 应用骨架 + structlog 配置
├── /ingest_message + /tasks CRUD
├── /stream/task/{id} SSE
├── Echo/Mock LLM 回路
└── API 测试

第 3 阶段（2-3 天）：Artifact + Web UI
├── Artifact Store（文件系统 + SQLite 元信息）
├── React + Vite 项目初始化
├── Task 列表页
├── Task 详情页（事件时间线）
└── 端到端验证

第 4 阶段（1-2 天）：收尾
├── 健康检查 + 错误处理
├── 进程重启恢复验证
├── 文档 + 验收测试
└── Projection rebuild 验证
```

---

## 6. 关键风险与建议

### 6.1 技术风险

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| SQLite 并发写入瓶颈 | 低 | M0 单用户场景下 WAL 模式足够；预留 async 写入接口以便后续优化 |
| SSE 连接管理复杂度 | 中 | M0 仅需支持单客户端连接；使用 FastAPI 内置 SSE 支持（`sse-starlette`），避免自研 |
| Event Sourcing 性能退化 | 低 | M0 数据量极小；事件表加 `(task_id, ts)` 复合索引即可；大规模时再考虑快照 |
| Web UI 工程量溢出 | 中 | 严控 UI 范围：仅 Task 列表 + 事件时间线两个页面，不做 Chat 界面 |
| 数据模型过早锁定 | 中 | Event 使用 `schema_version` 字段预留演进能力；Payload 使用 JSON 保持灵活性 |

### 6.2 产品风险

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| M0 无智能化能力，用户价值感不足 | 中 | Echo/Mock 模式足以验证底座；M1 紧随其后引入 LLM 真正调用 |
| 与竞品功能差距大 | 低 | M0 不与竞品在功能层面竞争，而是建立更坚实的底座。底座质量 > 功能数量 |
| 过度设计数据模型 | 低 | Blueprint 已有详细设计，M0 落地时仅实现 Must-have 字段，其余标记 optional |

### 6.3 战略建议

1. **底座优先，智能后叠**：M0 的核心价值不是"能做多少事"，而是"做的每件事都有据可查"。这是 OctoAgent 相对竞品的结构性优势，必须在 M0 就建立起来

2. **验收驱动开发**：Blueprint 已定义清晰的验收标准，建议将其转化为自动化测试用例，确保：
   - Task 创建 → 事件落盘 → SSE 推送 端到端通过
   - 进程重启后 Task 状态不丢失
   - 所有日志包含 request_id / trace_id

3. **数据模型版本化**：Event 的 `schema_version` 字段是关键设计。M0 即使只用 v1，也必须从一开始就写入此字段，为后续演进预留空间

4. **Web UI 技术选型确认**：React + Vite 是 Blueprint 的选择。M0 的 UI 极简，也可考虑先用 FastAPI 内置的 Jinja2 模板快速出 HTML 页面，React 版本在 M1 引入。但考虑 Blueprint 明确要求"从 M0 起一步到位，避免迁移债务"，建议直接上 React [推断]

5. **技术调研方向建议**：后续 tech-research 子代理应重点关注：
   - SQLite WAL 模式下的 async 访问方案（aiosqlite vs 线程池）
   - FastAPI SSE 的最佳实践（sse-starlette 库 vs 自研 StreamingResponse）
   - React SSE 客户端方案（EventSource API vs fetch + ReadableStream）
   - structlog + Logfire 的集成配置

---

## 7. 竞品启示清单

从竞品分析中提炼的具体设计参考：

| 来源 | 启示 | 适用阶段 |
|------|------|---------|
| Agent Zero | Docker 隔离方案可直接复用；Task 与 Project 关联模式 | M1.5+ |
| OpenClaw | 事件流 API 标准化的社区需求验证了 SSE 方案的正确性 | M0 |
| Pydantic AI | FileStatePersistence 接口可参考设计 SQLite Persistence Adapter | M1.5 |
| LangGraph | `stream_mode="events"` 的事件类型分类；React hooks 的 SSE 消费方案 | M0 |
| CrewAI | 事件类型按层级分类（Crew/Agent/Task/Tool）的设计模式 | M0 |
| AIOS | "内核化"思路验证了 Agent 基础设施建模为 OS 组件的合理性 | 全局 |

---

## 8. 附录

### 8.1 调研信息源

- Agent Zero 官网：https://www.agent-zero.ai / GitHub 14.4K stars
- OpenClaw 社区：GitHub 190K+ stars（2026 年初数据）
- Pydantic AI 文档：pydantic-graph beta API（2025）
- AIOS 论文：COLM 2025 接收，GitHub 4.9K stars
- LangGraph 文档：LangChain 官方，stream_mode="events"
- CrewAI 文档：Event Bus / Flow / AMP Dashboard
- 市场数据：MarketsandMarkets AI Agents Market Report 2025
- 行业趋势：Flowtivity AI Agent Trends 2026

### 8.2 术语表

| 术语 | 含义 |
|------|------|
| Event Sourcing | 将系统状态变化记录为不可变事件序列的架构模式 |
| Projection | 从事件流物化出的当前状态视图 |
| SSE | Server-Sent Events，服务端单向推送协议 |
| WAL | Write-Ahead Logging，SQLite 的并发写入模式 |
| FSM | Finite State Machine，有限状态机 |
| HITL | Human-in-the-Loop，人在回路 |
| A2A | Agent-to-Agent Protocol，Google 提出的 Agent 间通信标准 |
