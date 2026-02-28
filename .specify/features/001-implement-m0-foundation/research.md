# M0 基础底座 -- 技术决策研究

**特性**: 001-implement-m0-foundation
**阶段**: Phase 0 -- 研究决策
**日期**: 2026-02-28

---

## RD-1: Gateway + Kernel 合并策略

**Decision**: M0 阶段 Gateway 与 Kernel 合并为单个 FastAPI 进程，通过 packages 保持逻辑边界。

**Rationale**:
- M0 阶段 Kernel 的核心职责（Orchestrator/Policy Engine/Memory）尚未就绪，独立进程是过度设计
- 单进程降低开发与调试复杂度，SQLite 单写者模式天然适配
- Blueprint §12.1.1 明确推荐开发拓扑为单进程模式
- 产研报告两份调研均推荐合并

**Alternatives**:
1. Gateway 与 Kernel 独立进程，通过 HTTP 通信 -- 过度设计，M0 无 Orchestrator/Worker
2. 单体应用不分 packages -- 丢失逻辑边界，M1 拆分代价高

**Migration Path**: M1.5 评估拆分需求，通过 packages 边界清晰可低成本拆分为独立进程。

---

## RD-2: SQLite 表结构（M0 最小集）

**Decision**: M0 仅创建 3 张表：`tasks`、`events`、`artifacts`。不创建 `checkpoints` 和 `approvals` 表。

**Rationale**:
- M0 无 Skill Pipeline（Graph），checkpoint 无消费者
- M0 无 Policy Engine，approval 无消费者
- 最小表集合降低 schema 维护成本
- spec.md §9 排除项明确列出 Checkpoint 和 Approvals 推迟到 M1/M1.5

**Alternatives**:
1. 创建全部 5 张表但留空 -- 增加 DDL 复杂度，空表误导开发者
2. 仅创建 events 表，tasks 作为纯视图 -- SQLite 不支持物化视图，查询性能差

---

## RD-3: Event ID 格式

**Decision**: 使用 python-ulid 生成 ULID 作为 event_id，字符串格式存储。task_id 同样使用 ULID。

**Rationale**:
- Blueprint §8.2.2 明确要求 "events 使用 ULID/时间有序 id 便于流式读取"
- ULID 比 UUID v7 更广泛支持，python-ulid 库成熟稳定
- 时间有序性对 SSE 断线重连（Last-Event-ID）至关重要
- 字符串存储（26 字符）比二进制更易调试

**Alternatives**:
1. UUID v4 -- 非时间有序，SSE 重连需额外排序
2. UUID v7 -- Python 标准库尚未原生支持
3. 自增整数 -- 无法分布式，M1.5 多进程时瓶颈

---

## RD-4: SSE 实现方案

**Decision**: 使用 sse-starlette 库实现 SSE 端点。

**Rationale**:
- W3C 规范合规，内置 Last-Event-ID 支持和 ping 心跳
- 与 FastAPI/Starlette 生态天然兼容
- 产研报告确认版本 3.0 稳定
- 原生支持 async generator 模式

**Alternatives**:
1. 手动实现 StreamingResponse -- 缺少 Last-Event-ID / ping / 规范合规性
2. WebSocket -- 双向通信在 M0 无需求，SSE 单向推送更简单
3. 长轮询 -- 延迟高，浪费资源

---

## RD-5: LLM 客户端模式

**Decision**: M0 使用 litellm 直连模式（不启动 Proxy 服务），Echo/Mock 模式实现端到端验证。

**Rationale**:
- M0 的 LLM 调用仅用于端到端验证（Echo 模式），不依赖外部 LLM 服务
- litellm 直连模式零外部依赖，开发体验更好
- M1 升级到 Proxy 仅需修改 `base_url`，迁移成本极低
- Blueprint §7.4 明确 LiteLLM Proxy 为 M1 必选

**Alternatives**:
1. M0 即启动 LiteLLM Proxy Docker -- 增加开发复杂度，M0 无实际 LLM 调用需求
2. 自己实现 Echo handler 不用 litellm -- 丢失 M1 升级路径，多写一层抽象

**Echo 模式实现**: 自定义 `EchoProvider`，接收输入消息并原样返回，不走网络请求。通过 litellm 的 custom provider 或直接实现轻量 `LLMClient` 接口。

---

## RD-6: 后台任务处理模型

**Decision**: POST /api/message 同步创建 Task + 写入事件后立即返回 task_id，LLM 调用在后台 asyncio.Task 中异步执行。

**Rationale**:
- spec.md AC-1 明确要求异步后台执行
- NFR-M0-2 要求消息接收到任务创建 < 500ms，同步等待 LLM 无法满足
- SSE 的存在价值正是支持异步观察
- 符合 Event Sourcing 范式："先创建任务再处理"

**Alternatives**:
1. 同步等待 LLM 完成 -- 延迟不可控，违反 NFR-M0-2
2. Celery/Arq 任务队列 -- M0 单进程过度设计

**Implementation**: 使用 `asyncio.create_task()` 启动后台协程，协程内完成 LLM 调用、事件写入、SSE 通知。M0 无需持久化任务队列。

---

## RD-7: Artifact 存储策略

**Decision**: Artifact 文件按 `data/artifacts/{task_id}/{artifact_id}` 目录结构存储。< 4KB 的文本内容 inline 存储在 parts.content 字段中。

**Rationale**:
- spec.md FR-M0-AS-1 明确目录结构
- spec.md FR-M0-AS-3 设定 4KB inline 阈值
- 按 task_id 分组便于任务级备份和清理
- inline 阈值减少小文件 IO 开销

**Alternatives**:
1. 所有 Artifact 都写文件 -- 小文本不必要的 IO
2. 所有 Artifact 都 inline -- 大文件会撑爆 SQLite
3. 使用 S3/MinIO -- M0 单机过度设计

---

## RD-8: Event Payload 最小化策略

**Decision**: Event payload 默认仅存摘要与 artifact_ref 引用。超过 8KB 的内容必须通过 Artifact 存储。8KB 作为可配置常量。

**Rationale**:
- spec.md AC-3 设定 8KB 默认阈值
- Blueprint §8.5.2 中 Tool 输出 max_inline_chars 建议 4000 字符（约 8KB UTF-8）
- 对齐 Constitution C8（日志最小化）和 C11（上下文卫生）
- 过大的 Event payload 影响 SSE 传输效率和 SQLite 查询性能

**Alternatives**:
1. 无阈值，全部内容直接存 payload -- 违反 C8 最小化原则
2. 4KB 阈值 -- 过于激进，大多数短回复需要额外 Artifact

---

## RD-9: Projection Rebuild 触发方式

**Decision**: 提供专用 CLI 命令 `python -m octoagent.core rebuild-projections`。

**Rationale**:
- spec.md AC-2 明确采用 CLI 方式
- Rebuild 是破坏性操作（清空 tasks 表），不应暴露为 REST API 防误触
- CLI 便于在进程停止时执行，避免并发写入冲突
- M0 无调度器，不宜在启动时自动执行

**Alternatives**:
1. REST API 端点 -- 误触风险高
2. 启动时自动执行 -- M0 无调度器支持，且影响启动速度

---

## RD-10: 前端技术选型

**Decision**: React 19 + Vite 6，无 CSS 框架，无状态管理库。使用原生 EventSource 消费 SSE。

**Rationale**:
- Blueprint §7.6 和 spec.md FR-M0-UI-4 明确约束
- M0 仅 2 个页面（TaskList + TaskDetail），组件状态简单，useState/useReducer 足够
- 原生 EventSource 天然支持 SSE 断线重连
- 无 CSS 框架减少学习成本和包体积

**Alternatives**:
1. Tailwind CSS -- 增加构建复杂度，M0 UI 极简无需
2. Zustand/Redux -- 状态管理库对 2 个页面过度设计
3. SWR/React Query -- M0 数据流简单，fetch + useState 足够

---

## RD-11: 可观测性配置策略

**Decision**: structlog 作为主日志库 + Logfire 可选启用（通过 `LOGFIRE_SEND_TO_LOGFIRE` 环境变量控制）。

**Rationale**:
- Blueprint §7.7 选型，Logfire 自动 instrument FastAPI/Pydantic
- structlog 提供开发环境 pretty print + 生产环境 JSON 两种模式
- `send_to_logfire=False` 时仅本地日志，零外部依赖
- 免费 tier 配额足够 M0 验证

**Alternatives**:
1. 仅 structlog -- 丢失 Logfire 自动 instrument 能力
2. 仅 Logfire -- 本地开发日志可读性差
3. Python logging -- 缺少结构化能力

---

## RD-12: task_seq 并发安全

**Decision**: 使用 `SELECT MAX(task_seq) FROM events WHERE task_id = ? FOR UPDATE` 在同一事务内获取并递增 task_seq。SQLite 单写者模式下天然串行化。

**Rationale**:
- spec.md FR-M0-ES-5 要求同一 task 的 task_seq 严格单调递增
- M0 单进程 + SQLite 单写者，事务内 MAX + INSERT 保证原子性
- 不使用应用层锁，依赖数据库事务隔离

**Alternatives**:
1. 应用层原子计数器 -- 进程重启丢失，违反 C1
2. 数据库序列 -- SQLite 不支持序列对象
3. 乐观并发（version check）-- M0 单进程无需

---

## RD-13: idempotency_key 去重实现

**Decision**: events 表的 `idempotency_key` 列设置 UNIQUE 约束（WHERE NOT NULL）。POST /api/message 在创建 Task 前检查 idempotency_key 是否已存在。

**Rationale**:
- spec.md FR-M0-API-1 和 EC-1 明确要求 idempotency_key 去重
- Blueprint §8.2.2 要求 `unique(idempotency_key where not null)`
- 数据库级 UNIQUE 约束比应用层检查更可靠
- 仅对入口操作必填，内部事件可为 NULL

**Alternatives**:
1. Redis SET NX -- M0 不引入 Redis
2. 应用层 Set 缓存 -- 进程重启丢失
3. 全表扫描检查 -- 性能差

---

## RD-14: Web UI 开发与生产服务模式

**Decision**: 开发时 Vite dev server 代理 API 请求到 FastAPI；生产时 FastAPI 托管静态文件。

**Rationale**:
- Blueprint §9.12 明确此方案
- 开发时享受 Vite HMR，不影响后端开发
- 生产时单进程服务前后端，部署简单
- M0 不需要独立的前端服务或 CDN

**Alternatives**:
1. 独立 Nginx 服务前端 -- M0 过度设计
2. 后端模板渲染 -- 丢失 React 生态能力
