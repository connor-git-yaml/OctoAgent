# M0 基础底座 -- 功能需求规范

**特性**: 001-implement-m0-foundation
**版本**: v1.0
**状态**: Draft
**日期**: 2026-02-28
**调研基础**: [research-synthesis.md](research/research-synthesis.md)
**蓝图依据**: docs/blueprint.md §5, §8, §9, §10, §14

---

## 1. 概述

### 1.1 功能名称

M0 基础底座 -- Task/Event/Artifact + SSE 事件流 + 最小 Web UI

### 1.2 目标

构建 OctoAgent 的持久化任务底座，实现"可观测的任务账本"。M0 的核心价值是验证 Event Sourcing 架构的端到端可行性：用户发送消息后，系统创建任务、记录事件、调用 LLM（Echo/Mock 模式）、推送 SSE 事件流，并在 Web UI 上展示完整的任务生命周期。进程重启后，所有任务状态不丢失。

### 1.3 范围概述

M0 是 OctoAgent 四个里程碑中的第一个，聚焦于数据持久化层、REST API 层和最小可视化层。本阶段不涉及智能编排（Orchestrator/Worker）、多渠道接入（Telegram）、记忆系统、工具治理等能力，这些属于后续里程碑（M1-M3）的范畴。

M0 采用单进程合并架构：Gateway 与 Kernel 合并为单个 FastAPI 进程，通过 packages 保持逻辑边界清晰。[AUTO-RESOLVED: M0 阶段 Kernel 核心职责尚未就绪，独立进程是过度设计，产研调研报告一致推荐合并]

### 1.4 受众声明

本规范面向 OctoAgent 技术团队（包括开发者和技术架构师），文中使用的领域术语（如 Event Sourcing、SSE、Projection 等）假定读者具备相关技术背景。非技术利益相关者请参考 §1.2 和 §6 了解项目目标与成功标准。

---

## 2. 参与者（Actors）

| 参与者 | 描述 | M0 交互方式 |
|--------|------|-------------|
| **Owner（用户）** | OctoAgent 的唯一用户，即系统的主人 | 通过 Web UI 查看任务列表和事件时间线；通过 REST API 发送消息和取消任务 |
| **系统（OctoAgent 后端）** | FastAPI 服务进程，负责消息处理、任务管理、事件记录 | 接收消息、创建任务、调用 LLM、写入事件、推送 SSE |
| **Echo/Mock LLM** | 模拟的 LLM 服务端点，用于端到端验证 | 接收模型调用请求，返回回声/固定响应 |
| **Web UI** | React + Vite 前端应用 | 消费 SSE 事件流，展示任务列表和事件时间线 |

---

## 3. User Stories

### P1 -- MVP 核心（必须交付）

#### US-1: 消息接收与任务创建

**作为** Owner，**我希望** 通过 API 发送一条消息后，系统自动创建一个任务并开始处理，**以便** 我的每个请求都被可追踪地记录和执行。

**优先级理由**: 这是整个系统的入口，没有任务创建就没有后续一切功能。

**验收场景**:

```
Given 系统正在运行且数据库已初始化
When Owner 通过 POST /api/message 发送一条文本消息
Then 系统返回新创建的 task_id
  And 数据库 tasks 表新增一条记录，状态为 CREATED
  And 数据库 events 表新增一条 TASK_CREATED 事件
  And 数据库 events 表新增一条 USER_MESSAGE 事件
```

#### US-2: 事件溯源与状态一致性

**作为** Owner，**我希望** 任务的每一步操作都以事件形式记录在数据库中，且任务状态始终从事件推导而来，**以便** 我能完整审计任务的执行历史，且系统在崩溃后可恢复。

**优先级理由**: 对齐 Constitution C1（Durability First）和 C2（Everything is an Event），是系统可靠性的基石。

**验收场景**:

```
Given 一个处于 RUNNING 状态的任务
When 系统记录了 STATE_TRANSITION 事件（RUNNING → SUCCEEDED）
Then tasks 表的 status 字段在同一事务内更新为 SUCCEEDED
  And events 表中该任务的事件序列完整记录了从 CREATED 到 SUCCEEDED 的全过程
  And 任何对 tasks 表状态的更新都必须通过写入事件触发，而非直接修改
```

#### US-3: SSE 实时事件推送

**作为** Owner，**我希望** 在任务执行过程中，能通过 SSE 连接实时接收任务的事件流，**以便** 我能实时观察任务进展而不需要轮询。

**优先级理由**: 对齐 Constitution C8（Observability is a Feature），是可观测性的核心交付手段。

**验收场景**:

```
Given Owner 已建立到 GET /api/stream/task/{task_id} 的 SSE 连接
When 该任务产生新事件（如 MODEL_CALL、STATE_TRANSITION）
Then SSE 连接实时推送该事件的 JSON 数据
  And 每条 SSE 消息包含唯一的 event id
  And 当任务到达终态时，SSE 推送携带 "final": true 的终止信号
```

#### US-4: 端到端 LLM 回路验证

**作为** Owner，**我希望** 发送一条消息后，系统能调用 LLM（Echo/Mock 模式）生成响应，并将响应作为事件记录和推送，**以便** 验证从消息到 LLM 到事件到推送的完整链路。

**优先级理由**: 端到端验证是 M0 的核心交付目标，证明底座架构可支撑后续智能化能力。

**验收场景**:

```
Given 系统配置为 Echo 模式（LLM 返回输入消息的回声）
When Owner 发送消息 "Hello OctoAgent"
Then 系统创建任务，状态依次流转 CREATED → RUNNING → SUCCEEDED
  And events 表记录 MODEL_CALL_STARTED 事件（包含请求摘要和 artifact_ref）
  And events 表记录 MODEL_CALL_COMPLETED 事件（包含响应摘要、调用耗时和 artifact_ref）
  And SSE 推送以上所有事件
  And 整个流程的日志包含一致的 trace_id
```

#### US-5: 进程重启后任务不丢失

**作为** Owner，**我希望** 即使系统进程意外退出并重启，所有已创建的任务和事件都不会丢失，**以便** 我对系统的持久性有信心。

**优先级理由**: 对齐 Constitution C1（Durability First），这是"不可谈判的硬规则"。

**验收场景**:

```
Given 系统中存在若干任务（包含不同状态：RUNNING、SUCCEEDED、FAILED）
When 系统进程被强制终止并重新启动
Then 所有任务的状态、事件历史、Artifact 元数据完整保留
  And Web UI 重新加载后能正确展示所有任务
  And 重启前处于 RUNNING 状态的任务，其中间状态可查询
```

#### US-6: 产物存储与检索

**作为** Owner，**我希望** 任务产生的产物（文本、文件等）被持久化存储，并能按任务检索，**以便** 我能回溯任务的输出结果。

**优先级理由**: Artifact 是任务产出的载体，没有产物管理，任务系统的价值大打折扣。

**验收场景**:

```
Given 一个已完成的任务产生了文本产物
When Owner 通过 GET /api/tasks/{task_id} 查询任务详情
Then 返回体中包含该任务关联的所有 Artifact 列表及其元数据（名称、类型、大小、哈希）
  And 文本类产物的 inline 内容包含在返回体中
  And 文件类产物包含 storage_ref 引用（M0 不提供独立的 Artifact 下载端点，文件访问通过 Store 层）
```

#### US-7: 可观测日志

**作为** Owner，**我希望** 系统的所有日志都包含结构化的追踪标识（request_id / trace_id），**以便** 我能在排查问题时快速关联一个请求的完整调用链。

**优先级理由**: 对齐 Constitution C8（Observability is a Feature），没有可观测性的功能不可上线。

**验收场景**:

```
Given 系统正在处理一个任务
When 查看系统日志输出
Then 每条日志包含 request_id（标识单次 HTTP 请求）
  And 每条日志包含 trace_id（标识任务级别的调用链）
  And 日志格式为结构化输出（开发环境可读格式，生产环境 JSON 格式）
```

#### US-8: 任务取消

**作为** Owner，**我希望** 能取消一个正在进行的任务，使其推进到终态，**以便** 我对系统有控制权，不想要的任务可以被停止。

**优先级理由**: 对齐 Constitution C7（User-in-Control），用户必须能取消任务。Constitution 明确要求"系统必须提供审批、取消、删除等控制能力"，取消是不可商量的 MUST。

**验收场景**:

```
Given 一个处于非终态的任务（如 CREATED、RUNNING）
When Owner 通过 POST /api/tasks/{task_id}/cancel 取消该任务
Then 任务状态推进到 CANCELLED
  And events 表记录 STATE_TRANSITION 事件（原状态 → CANCELLED）
  And SSE 推送取消事件并携带 "final": true
  And 后续对该任务的操作被拒绝（任务已在终态）

Given 一个已处于终态的任务（如 SUCCEEDED）
When Owner 尝试取消该任务
Then 系统返回错误，说明任务已在终态，无法取消
```

### P2 -- 重要补充（应当交付）

#### US-9: Projection 重建

**作为** Owner，**我希望** 系统能从事件历史重建任务状态（projection rebuild），且重建后的状态与原始状态一致，**以便** 在数据出现不一致时有恢复手段。

**优先级理由**: 对齐 Constitution C1（Durability First），验证 Event Sourcing 的可回放能力。

**验收场景**:

```
Given 系统中存在多个已完成和进行中的任务
When 执行 Projection Rebuild 操作（清空 tasks 表并从 events 表重放）
Then 重建后 tasks 表的每条记录与重建前完全一致（状态、时间戳、指针）
  And 重建过程记录日志，包含处理的事件数量和耗时
```

#### US-10: Web UI 任务列表

**作为** Owner，**我希望** 在浏览器中看到所有任务的列表，包含每个任务的当前状态，**以便** 我能一目了然地掌握系统中所有任务的情况。

**优先级理由**: Web UI 是用户可见价值的直接体现，让"任务账本"对用户可感知。

**验收场景**:

```
Given 系统中存在若干不同状态的任务
When Owner 打开 Web UI 的任务列表页面
Then 页面展示所有任务，每个任务显示：标题、状态、创建时间
  And 任务按创建时间倒序排列（最新的在前）
  And 状态使用可区分的视觉标识（如不同颜色或图标）
```

#### US-11: Web UI 事件时间线

**作为** Owner，**我希望** 点击某个任务后，能看到该任务的完整事件时间线，且新事件实时追加，**以便** 我能详细了解任务的每一步执行过程。

**优先级理由**: 事件时间线是 Event Sourcing 架构对用户的核心呈现方式。

**验收场景**:

```
Given Owner 在 Web UI 中点击了某个任务
When 任务详情页加载完成
Then 页面展示该任务的所有事件，按时间正序排列
  And 每条事件显示：类型、时间、关键 payload 摘要
  And 如果任务仍在进行中，新事件通过 SSE 实时追加到时间线末尾
  And 无需手动刷新页面
```

#### US-12: 健康检查

**作为** Owner（或运维监控工具），**我希望** 通过 HTTP 端点检查系统的存活状态和就绪状态，**以便** 及时发现系统异常。

**优先级理由**: 健康检查是系统可运维的基础能力。

**验收场景**:

```
Given 系统正在正常运行
When 请求 GET /health
Then 返回 200 {"status": "ok"}

Given 系统正在正常运行且所有依赖就绪
When 请求 GET /ready
Then 返回 200 {"status": "ready", "profile": "core", "checks": {"sqlite": "ok", "artifacts_dir": "ok", "disk_space_mb": 2048, "litellm_proxy": "skipped"}}

Given SQLite 数据库不可访问
When 请求 GET /ready
Then 返回非 200 状态码，checks 中标明 sqlite 异常
  And 未启用的组件（如 litellm_proxy）仍返回 "skipped"，不影响整体判定
```

---

## 4. Functional Requirements（功能需求）

### 4.1 数据模型

#### FR-M0-DM-1: Task 数据模型 [MUST]

系统必须支持 Task 数据实体，包含以下核心属性：唯一标识（task_id）、创建时间、更新时间、状态、标题、线程标识（thread_id）、作用域标识（scope_id）、请求者信息、风险等级，以及指向最新事件的指针。

**追踪**: US-1, US-2, US-5

#### FR-M0-DM-2: Task 状态机 [MUST]

Task 的状态必须遵循以下状态机：

- 初始状态：CREATED
- 合法流转：CREATED -> RUNNING -> SUCCEEDED | FAILED | CANCELLED
- 终态：SUCCEEDED、FAILED、CANCELLED
- 到达终态后不可再流转

M0 暂不启用 QUEUED、WAITING_INPUT、WAITING_APPROVAL、PAUSED、REJECTED 状态，但数据模型中必须预留这些状态的定义，以便 M1+ 扩展。[AUTO-RESOLVED: M0 无 Orchestrator/Worker/Policy Engine，这些中间状态无消费者，但数据模型预留确保向前兼容]

**追踪**: US-1, US-2, US-8

#### FR-M0-DM-3: Event 数据模型 [MUST]

系统必须支持 Event 数据实体，包含以下核心属性：唯一标识（event_id，ULID 格式，时间有序）、关联的 task_id、任务内序号（task_seq，同一 task 内严格单调递增，用于确定性回放）、时间戳、事件类型、schema 版本号、操作者（actor）、结构化 payload、trace_id、span_id，以及因果链信息（parent_event_id、idempotency_key）。

Event payload 默认遵循最小化原则：仅存储排障和审计所需字段。大体积内容（如 LLM 完整响应）和敏感原文不得直接存入 payload，必须通过 artifact 引用（artifact_ref）方式存储。

idempotency_key 对入口操作（如消息接收）和带副作用操作为必填，用于去重与重试安全。

M0 支持的事件类型：TASK_CREATED、USER_MESSAGE、MODEL_CALL_STARTED、MODEL_CALL_COMPLETED、MODEL_CALL_FAILED、STATE_TRANSITION、ARTIFACT_CREATED、ERROR。

**追踪**: US-2, US-3, US-4

#### FR-M0-DM-4: Artifact 数据模型 [MUST]

系统必须支持 Artifact 数据实体，采用 A2A 兼容的 parts 多部分结构。核心属性包括：唯一标识（artifact_id，ULID 格式）、关联的 task_id、时间戳、名称、描述、parts 数组、存储引用（storage_ref）、大小（size）、哈希（hash，SHA-256）、版本号。

M0 支持的 Part 类型：text（纯文本/markdown）和 file（文件引用）。json 和 image 类型在数据模型中预留定义但 M0 无消费者。

**追踪**: US-6

#### FR-M0-DM-5: NormalizedMessage 数据模型 [MUST]

系统必须支持 NormalizedMessage 作为消息入站的统一格式，包含：渠道标识、线程标识、作用域标识、发送者信息、时间戳、文本内容、附件列表。M0 阶段仅支持 "web" 渠道。

**追踪**: US-1

### 4.2 事件存储

#### FR-M0-ES-1: Event append-only 存储 [MUST]

事件表必须为 append-only：只允许插入，不允许更新或删除。这是 Event Sourcing 的核心不变量。

**追踪**: US-2

#### FR-M0-ES-2: 事件与 Projection 事务一致性 [MUST]

写入事件与更新 tasks 表（projection）必须在同一数据库事务内完成。不允许出现事件已写入但 projection 未更新的不一致状态。

**追踪**: US-2, US-5

#### FR-M0-ES-3: 事件 ID 时间有序 [MUST]

事件的唯一标识（event_id）必须使用 ULID 格式，确保时间有序性，便于流式读取和排序。

**追踪**: US-3, US-9

#### FR-M0-ES-5: 任务内事件序号 [MUST]

同一 task 的事件必须具有严格单调递增的 task_seq 序号（无重复、无回退），用于确定性回放和并发冲突检测。

**追踪**: US-2, US-9

#### FR-M0-ES-4: Projection Rebuild [MUST]

系统必须提供 Projection Rebuild 能力：清空 tasks 表后，按事件时间顺序重放所有事件，重建每个任务的当前状态。重建后的状态必须与重建前一致。对齐 Constitution C1（Durability First）和成功标准 SC-3。

**追踪**: US-9

### 4.3 REST API

#### FR-M0-API-1: 消息接收接口 [MUST]

系统必须提供 `POST /api/message` 接口，接收 NormalizedMessage 格式的消息（请求体须包含 idempotency_key），创建 Task，写入 TASK_CREATED 和 USER_MESSAGE 事件，并返回 task_id。若 idempotency_key 已存在，返回已有 task_id 而非创建新任务。

**追踪**: US-1, US-4

#### FR-M0-API-2: 任务列表查询接口 [MUST]

系统必须提供 `GET /api/tasks` 接口，返回所有任务的列表，支持按状态筛选。

**追踪**: US-10

#### FR-M0-API-3: 任务详情查询接口 [MUST]

系统必须提供 `GET /api/tasks/{task_id}` 接口，返回指定任务的当前状态快照，包含关联的事件列表和 Artifact 列表。

**追踪**: US-10, US-11

#### FR-M0-API-4: 任务取消接口 [MUST]

系统必须提供 `POST /api/tasks/{task_id}/cancel` 接口，将非终态的任务推进到 CANCELLED 状态。对已处于终态的任务返回错误。对齐 Constitution C7（User-in-Control）。

**追踪**: US-8

#### FR-M0-API-5: SSE 事件流接口 [MUST]

系统必须提供 `GET /api/stream/task/{task_id}` 接口，以 Server-Sent Events 协议推送该任务的事件流。要求：
- 连接建立后先推送已有的历史事件
- 后续新事件实时推送
- 每条消息包含唯一 event id
- 任务到达终态时推送 `"final": true` 终止信号
- 支持 Last-Event-ID 头实现断线重连

**追踪**: US-3, US-4, US-11

#### FR-M0-API-6: 健康检查接口 [SHOULD]

系统应当提供：
- `GET /health`：Liveness 检查，返回 `{"status": "ok"}`
- `GET /ready`：Readiness 检查，采用分级 profile 机制：
  - `core` profile（M0 默认）：检测 SQLite 连通性、artifacts 目录可访问性、磁盘空间
  - 未启用的组件返回 `skipped`，不应导致 profile 失败

**追踪**: US-12

### 4.4 Artifact Store

#### FR-M0-AS-1: Artifact 文件存储 [MUST]

系统必须将 Artifact 文件按 task_id 分组存储在文件系统中。目录结构为 `data/artifacts/{task_id}/{artifact_id}`。

**追踪**: US-6

#### FR-M0-AS-2: Artifact 元数据管理 [MUST]

系统必须在 SQLite 的 artifacts 表中记录 Artifact 的元数据（artifact_id、task_id、名称、parts 信息、storage_ref、size、hash、version）。

**追踪**: US-6

#### FR-M0-AS-3: Artifact inline 阈值 [SHOULD]

小于 4KB 的文本内容应当以 inline 方式存储在 parts 的 content 字段中，无需写入文件系统。大于 4KB 的内容必须写入文件系统并通过 storage_ref 引用。

**追踪**: US-6

#### FR-M0-AS-4: Artifact 完整性校验 [MUST]

每个 Artifact 必须计算并存储 SHA-256 哈希值和文件大小，用于完整性校验。

**追踪**: US-6

### 4.5 可观测性

#### FR-M0-OB-1: 结构化日志 [MUST]

系统必须输出结构化日志，开发环境使用可读格式（pretty print），生产环境使用 JSON 格式。

**追踪**: US-7

#### FR-M0-OB-2: 请求级追踪标识 [MUST]

每个 HTTP 请求必须生成唯一的 request_id，并贯穿该请求的所有日志输出。

**追踪**: US-7

#### FR-M0-OB-3: 任务级追踪标识 [MUST]

每个任务必须关联 trace_id，贯穿该任务生命周期内的所有事件和日志。同一任务的所有事件共享同一个 trace_id。

**追踪**: US-7, US-4

#### FR-M0-OB-4: APM/Trace 集成 [SHOULD]

系统应当集成应用性能监控（APM）工具进行 trace 数据采集，自动采集 Web 框架请求。支持配置开关切换为仅本地日志模式。

**追踪**: US-7

### 4.6 Web UI

#### FR-M0-UI-1: 任务列表页 [MUST]

Web UI 必须提供任务列表页面，展示所有任务的标题、状态和创建时间，按创建时间倒序排列。对齐 Blueprint FR-CH-1（M0 必须提供 Task 面板）。

**追踪**: US-10

#### FR-M0-UI-2: 任务详情页（事件时间线）[MUST]

Web UI 必须提供任务详情页面，展示指定任务的完整事件时间线。事件按时间正序排列，每条事件显示类型、时间和关键 payload 摘要。对齐 Blueprint FR-CH-1（M0 必须提供事件流可视化）。

**追踪**: US-11

#### FR-M0-UI-3: SSE 实时更新 [MUST]

Web UI 必须通过原生 EventSource 消费 SSE 事件流，对进行中的任务实时追加新事件到时间线，无需手动刷新。

**追踪**: US-11

#### FR-M0-UI-4: 最小化 UI 范围 [MUST]

M0 Web UI 必须控制在两个页面（任务列表 + 任务详情）范围内，不包含 Chat 界面、审批面板、配置管理等功能。不使用 CSS 框架，不使用状态管理库。

**追踪**: US-10, US-11

### 4.7 Echo/Mock LLM 回路

#### FR-M0-LLM-1: Echo 模式 [MUST]

系统必须支持 Echo 模式：LLM 调用返回输入消息的回声内容。此模式用于端到端验证，不依赖任何外部 LLM 服务。

**追踪**: US-4

#### FR-M0-LLM-2: LLM 调用事件化 [MUST]

每次 LLM 调用必须生成两条事件：

- **MODEL_CALL_STARTED**：记录请求摘要、模型标识、trace_id。完整请求内容通过 Artifact 引用存储（payload 中包含 artifact_ref）。
- **MODEL_CALL_COMPLETED**（或 MODEL_CALL_FAILED）：记录响应摘要、调用耗时、token 用量。完整响应内容通过 Artifact 引用存储。

双事件设计确保：即使 LLM 调用过程中崩溃，MODEL_CALL_STARTED 事件已落盘可审计。

**追踪**: US-4, US-2

#### FR-M0-LLM-3: LLM 客户端抽象 [SHOULD]

LLM 客户端应当通过统一的模型网关库调用，使用 model alias 而非硬编码厂商型号，确保后续升级到独立模型代理时仅需修改连接地址。

**追踪**: US-4

---

## 5. Non-Functional Requirements（非功能需求）

### NFR-M0-1: 可靠性 [MUST]

- 单机断电/重启后不丢任务元信息（tasks 表、events 表、artifacts 元数据）
- SQLite 使用 WAL 模式，确保并发读写安全
- 写事件与更新 projection 在同一事务内，确保原子性

### NFR-M0-2: 性能 [SHOULD]

- 消息接收到任务创建的延迟应当小于 500ms
- SSE 事件推送延迟（从事件写入到客户端接收）应当小于 200ms
- 任务列表查询在 1000 条任务范围内响应时间应当小于 200ms

### NFR-M0-3: 可维护性 [MUST]

- packages/core 与 apps/gateway 保持清晰的逻辑边界
- 数据模型使用 Pydantic BaseModel，具备类型安全
- Event 的 schema_version 字段预留版本迁移能力
- 所有公共函数具备完整类型注解

### NFR-M0-4: 可扩展性 [SHOULD]

- Store 层通过接口抽象，预留 M1+ 替换存储后端的能力
- 事件类型可扩展，新增事件类型不影响已有逻辑
- API 路由模块化，M1 新增接口不需修改现有路由

### NFR-M0-5: 安全性 [MUST]

- M0 不暴露外网访问（仅 localhost）
- 不存储任何 secrets 或敏感凭证
- 日志中不输出消息的完整原文（仅摘要或截断）

---

## 6. Success Criteria（成功标准）

| 编号 | 标准 | 验证方式 |
|------|------|---------|
| SC-1 | 用户发送消息后，系统创建任务、记录事件并实时推送状态变更，端到端通过 | 集成测试 |
| SC-2 | 进程重启后，所有任务状态不丢失，Web UI 正常展示 | 手动验证 + 自动化测试 |
| SC-3 | 从事件日志重建任务状态后，结果与原始状态一致 | 单元测试 |
| SC-4 | 产物文件可存储、可按任务检索、完整性校验通过 | 单元测试 |
| SC-5 | 所有日志包含请求标识和追踪标识，支持链路追踪 | 日志审查 |
| SC-6 | 任务取消操作正确推进到 CANCELLED 终态 | 集成测试 |
| SC-7 | Web UI 可展示任务列表 + 事件时间线，实时更新 | 手动验证 |
| SC-8 | 模拟 LLM 回路端到端通过，模型调用事件记录完整 | 集成测试 |

---

## 7. Edge Cases（边界场景）

### EC-1: 重复消息提交

**场景**: 由于网络重试，同一条消息被提交两次。
**关联**: FR-M0-API-1, US-1
**处理策略**: POST /api/message 通过 idempotency_key 实现去重。若相同 idempotency_key 已存在，返回已有 task_id（HTTP 200），不创建新任务。客户端须在请求体中携带唯一的 idempotency_key。

### EC-2: SSE 连接中断

**场景**: 客户端的 SSE 连接因网络问题断开。
**关联**: FR-M0-API-5, US-3
**降级策略**: 客户端使用 Last-Event-ID 头重新连接，服务端从该 ID 之后继续推送事件。EventSource 原生支持自动重连。

### EC-3: 任务在 LLM 调用期间进程崩溃

**场景**: LLM 调用尚未返回时进程崩溃，任务停留在 RUNNING 状态。
**关联**: FR-M0-DM-2, US-5
**降级策略**: M0 阶段不自动恢复 RUNNING 状态的任务。重启后这些任务保持 RUNNING 状态可查询，但不自动重试。M1.5 引入 Watchdog 后处理此场景。

### EC-4: SQLite 数据库文件损坏

**场景**: 数据库文件因磁盘错误损坏。
**关联**: NFR-M0-1, US-5
**降级策略**: WAL 模式提供一定的损坏保护。M0 不提供自动备份，但 Projection Rebuild 可从事件恢复任务状态（前提是 events 表可读）。建议用户自行定期备份数据库文件。

### EC-5: Artifact 文件系统空间不足

**场景**: artifacts 目录所在磁盘空间不足，无法写入新产物。
**关联**: FR-M0-AS-1, US-6
**处理策略**: Artifact 写入失败时记录 ARTIFACT_WRITE_FAILED 类型的 ERROR 事件（包含失败原因）。若该 Artifact 是任务的关键产物（如 LLM 响应），任务状态推进到 FAILED 并在事件中记录失败原因。若为辅助产物，任务可继续执行但在任务元数据中标记产物完整性告警。Readiness 检查应当检测磁盘空间。

### EC-6: 查询不存在的 task_id

**场景**: 用户通过 API 查询或取消一个不存在的 task_id。
**关联**: FR-M0-API-3, FR-M0-API-4
**降级策略**: 返回 404 Not Found，错误消息说明 task_id 不存在。

### EC-7: 大量并发 SSE 连接

**场景**: 多个浏览器标签页同时建立 SSE 连接到同一个任务。
**关联**: FR-M0-API-5, US-3
**降级策略**: M0 为单用户场景，预期并发 SSE 连接数极少（< 10）。不做特殊限流处理，FastAPI 的异步模型足以支撑。

### EC-8: Event payload 过大

**场景**: LLM 返回的响应内容非常长，导致事件 payload 过大。
**关联**: FR-M0-DM-3, FR-M0-LLM-2, US-4
**降级策略**: Event payload 默认仅存摘要与 artifact_ref 引用。完整的 LLM 请求/响应内容作为 Artifact 存储，事件 payload 中通过 artifact_ref 指向完整内容。对齐 Constitution C8（日志最小化）和 C11（上下文卫生）。默认阈值 8KB（见 AC-3）。

---

## 8. Constraints（约束）

### 8.1 Constitution 约束

| 宪法原则 | M0 中的体现 |
|----------|-------------|
| C1: Durability First | Event 落盘 + SQLite WAL + Projection Rebuild |
| C2: Everything is an Event | 所有操作通过事件记录，tasks 表仅为 projection |
| C7: User-in-Control | Task 取消 API |
| C8: Observability is a Feature | 结构化日志 + APM/Trace + SSE + Web UI |
| C8 补充: 日志最小化 | Event payload 默认写摘要与引用，不存大文本/敏感原文 |
| C8 补充: 敏感数据脱敏 | 日志中不输出完整原文，secrets/凭证/隐私数据不进 Event payload |

### 8.2 技术栈约束（来自 Blueprint §7 已锁定决策）

> 以下技术选型来自 Blueprint §7 和 Constitution §III 的已锁定决策，非本规范首次引入的实现细节。列出它们是为了约束实现边界，而非规定具体实现方式。

- 后端：Python 3.12+, FastAPI, Uvicorn, aiosqlite, sse-starlette
- 前端：React 19 + Vite 6
- 数据模型：Pydantic 2.x
- 包管理：uv workspace
- 事件 ID：python-ulid（ULID 格式）
- 可观测：OTel 语义兼容（默认基线 structlog + Logfire）
- LLM 客户端：模型网关库（默认基线 litellm 直连模式）

### 8.3 架构约束

- M0 为单进程合并架构（Gateway + Kernel 合并）
- 单用户场景，不考虑多租户
- 仅 localhost 访问，不暴露外网
- SQLite 单写者模式，M0 单进程无影响

---

## 9. 排除项（Out of Scope）

以下功能明确不在 M0 范围内，将在后续里程碑中实现：

| 排除功能 | 计划里程碑 | 理由 |
|----------|-----------|------|
| Orchestrator / Worker 编排 | M1.5 | M0 无需多代理，单进程直接处理 |
| Policy Engine（allow/ask/deny） | M1 | M0 无不可逆工具操作 |
| Pydantic Skill 框架 | M1 | M0 仅需 Echo/Mock 回路 |
| LiteLLM Proxy 模式 | M1 | M0 使用 litellm 直连足够 |
| Telegram 渠道 | M2 | M0 仅 Web 渠道 |
| 记忆系统（SoR/Fragments/Vault） | M2 | M0 无记忆消费者 |
| Checkpoint 表结构 | M1.5 | M0 无 Graph/Skill Pipeline |
| Approvals 表结构 | M1 | M0 无 Policy Engine |
| Artifact 流式追加（append + lastChunk） | M1+ | M0 无流式 LLM 输出 |
| json/image Part 类型 | M1+ | M0 无消费者 |
| Chat 输入界面 | M1 | M0 Web UI 仅展示，不做输入交互 |
| 多线程/scope 管理 | M2 | M0 单线程验证 |
| 工具 schema 反射 + ToolBroker | M1 | M0 无工具调用 |
| 配置管理（system/user/project） | M2 | M0 使用环境变量/文件配置 |
| 数据备份自动化 | M2+ | M0 建议手动备份 |
| A2A-Lite 消息协议 | M2 | M0 单进程无需进程间通信 |

---

## Clarifications

### Session 2026-02-28

#### AC-1: LLM 处理是同步还是异步（对 POST /api/message 而言）

**问题**: FR-M0-API-1 要求 POST /api/message 返回 task_id，但 US-4 要求 LLM 调用完成后推送所有事件。spec 未说明 API 返回时 LLM 调用是否已完成，还是 LLM 在后台异步执行。

**自动解决**: [AUTO-CLARIFIED: 异步后台执行 — POST /api/message 立即返回 task_id（仅完成 Task 创建 + TASK_CREATED + USER_MESSAGE 事件），LLM 调用在后台 async task 中执行并通过 SSE 推送进度。理由：(1) NFR-M0-2 要求消息接收到任务创建 < 500ms，同步等待 LLM 无法满足；(2) SSE 的存在价值正是支持异步观察；(3) 符合"先创建任务再处理"的 Event Sourcing 范式]

#### AC-2: Projection Rebuild 触发方式

**问题**: FR-M0-ES-4 定义了 Projection Rebuild 能力，但未说明触发方式是 CLI 命令、管理 API 端点，还是通过 `/ready` 端点在启动时自动执行。

**自动解决**: [AUTO-CLARIFIED: 提供专用管理 CLI 命令（例如 `python -m octoagent.core rebuild-projections`）— 理由：(1) Rebuild 是破坏性操作（清空 tasks 表），不应暴露为 REST API 以防误触；(2) CLI 便于在进程停止时执行，避免并发写入冲突；(3) M0 无调度器，不宜在启动时自动执行，需要明确人工触发]

#### AC-3: Event payload 过大截断阈值

**问题**: EC-8 说明 LLM 响应超过"阈值"时截断存入事件、完整内容存 Artifact，但未定义具体阈值数值。

**自动解决**: [AUTO-CLARIFIED: 默认阈值为 8KB（8192 字节）— 理由：(1) FR-M0-AS-3 将 Artifact inline 阈值设为 4KB，Event payload 阈值取其 2 倍以留有余量；(2) 8KB 可覆盖大多数 LLM 简短回复；(3) Blueprint §8.5.2 中 Tool 输出 max_inline_chars 建议 4000 字符，事件 payload 适当放宽；此阈值应作为可配置常量存于配置模块]

#### AC-4: GET /api/tasks/{task_id} 返回的事件列表是否分页

**问题**: GET /api/tasks/{task_id} 返回"关联的事件列表"，但未说明是否存在事件数量上限或分页机制。对于运行时间较长的任务，事件可能达到数百条。

**自动解决**: [AUTO-CLARIFIED: M0 阶段不分页，返回全量事件列表 — 理由：(1) M0 为 Echo/Mock 回路，单任务事件数量极少（< 20 条）；(2) 分页增加前后端契约复杂度，与"最小化 UI 范围"原则（FR-M0-UI-4）冲突；(3) 性能需求 NFR-M0-2 仅针对任务列表，未对详情 API 设限；分页能力推迟到事件量真正成为问题时（M1+）]

#### AC-5: inline text Artifact 的 hash/size 计算范围

**问题**: FR-M0-AS-4 要求每个 Artifact 必须计算并存储 SHA-256 hash 和 size，但 FR-M0-AS-3 允许 < 4KB 的文本内容以 inline 方式存储（不写入文件系统）。对于纯 inline Artifact，hash 和 size 应计算 inline content 还是整个 parts JSON？

**自动解决**: [AUTO-CLARIFIED: 对 inline text Artifact，hash 和 size 计算 inline content 字段的原始字节内容（UTF-8 编码后）— 理由：(1) Blueprint §8.1.1 明确 hash/size 用于完整性校验，应反映实际内容而非序列化格式；(2) 与 file 类型的处理逻辑对称（file 类型计算文件字节内容）；(3) 便于内容去重判断（相同内容的 inline Artifact 产生相同 hash）]

---

## 附录 A: User Story 与 FR 追踪矩阵

| FR 编号 | US 追踪 | 级别 |
|---------|---------|------|
| FR-M0-DM-1 | US-1, US-2, US-5 | MUST |
| FR-M0-DM-2 | US-1, US-2, US-8 | MUST |
| FR-M0-DM-3 | US-2, US-3, US-4 | MUST |
| FR-M0-DM-4 | US-6 | MUST |
| FR-M0-DM-5 | US-1 | MUST |
| FR-M0-ES-1 | US-2 | MUST |
| FR-M0-ES-2 | US-2, US-5 | MUST |
| FR-M0-ES-3 | US-3, US-9 | MUST |
| FR-M0-ES-4 | US-9 | MUST |
| FR-M0-ES-5 | US-2, US-9 | MUST |
| FR-M0-API-1 | US-1, US-4 | MUST |
| FR-M0-API-2 | US-10 | MUST |
| FR-M0-API-3 | US-10, US-11 | MUST |
| FR-M0-API-4 | US-8 | MUST |
| FR-M0-API-5 | US-3, US-4, US-11 | MUST |
| FR-M0-API-6 | US-12 | SHOULD |
| FR-M0-AS-1 | US-6 | MUST |
| FR-M0-AS-2 | US-6 | MUST |
| FR-M0-AS-3 | US-6 | SHOULD |
| FR-M0-AS-4 | US-6 | MUST |
| FR-M0-OB-1 | US-7 | MUST |
| FR-M0-OB-2 | US-7 | MUST |
| FR-M0-OB-3 | US-7, US-4 | MUST |
| FR-M0-OB-4 | US-7 | SHOULD |
| FR-M0-UI-1 | US-10 | MUST |
| FR-M0-UI-2 | US-11 | MUST |
| FR-M0-UI-3 | US-11 | MUST |
| FR-M0-UI-4 | US-10, US-11 | MUST |
| FR-M0-LLM-1 | US-4 | MUST |
| FR-M0-LLM-2 | US-4, US-2 | MUST |
| FR-M0-LLM-3 | US-4 | SHOULD |
