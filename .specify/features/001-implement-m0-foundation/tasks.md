# M0 基础底座 -- 任务清单

**特性**: 001-implement-m0-foundation
**版本**: v1.0
**状态**: Draft
**日期**: 2026-02-28
**依据**: plan.md v1.0, spec.md v1.0, data-model.md, contracts/rest-api.md, contracts/sse-protocol.md

---

## 总览

| 指标 | 数值 |
|------|------|
| 总任务数 | 68 |
| 可并行任务数 | 约 38（约 56%） |
| User Story 数 | 12（P1×8 + P2×4） |
| FR 覆盖率 | 100%（31/31 条） |
| 预估工作量 | 10-13 天 |

---

## Phase 1: Setup — 项目初始化与基础设施

**目标**: 建立 uv workspace 结构、子包配置、工具链，确保所有模块可导入。

### 任务列表

- [x] T001 创建 uv workspace 根配置 `pyproject.toml`（workspace members 声明 + Python 3.12+ 约束 + ruff/pytest 全局配置） — `octoagent/pyproject.toml`
- [x] T002 [P] 创建 `packages/core` 子包配置 `pyproject.toml`（依赖：pydantic, aiosqlite, python-ulid, structlog） — `octoagent/packages/core/pyproject.toml`
- [x] T003 [P] 创建 `apps/gateway` 子包配置 `pyproject.toml`（依赖：fastapi, uvicorn, sse-starlette, litellm, logfire） — `octoagent/apps/gateway/pyproject.toml`
- [x] T004 初始化 `packages/core` 目录结构并创建包入口文件 — `octoagent/packages/core/src/octoagent/core/__init__.py`
- [x] T005 [P] 初始化 `apps/gateway` 目录结构并创建包入口文件 — `octoagent/apps/gateway/src/octoagent/gateway/__init__.py`
- [x] T006 [P] 创建全局 `conftest.py`（async pytest 配置 + 临时 SQLite 数据库 fixture） — `octoagent/conftest.py`
- [x] T007 [P] 创建 `packages/core` 测试配置与 `conftest.py`（核心层 fixture） — `octoagent/packages/core/tests/conftest.py`
- [x] T008 [P] 创建 `apps/gateway` 测试配置与 `conftest.py`（FastAPI TestClient + async DB fixture） — `octoagent/apps/gateway/tests/conftest.py`
- [x] T009 [P] 配置 `.gitignore`（排除 `data/` 目录、`.env`、`__pycache__`、`*.db` 等） — `octoagent/.gitignore`
- [x] T010 [P] 创建 `data/` 目录结构占位文件（sqlite/、artifacts/ 子目录） — `octoagent/data/.gitkeep`

**验证**: `uv run python -c "import octoagent.core"` 和 `import octoagent.gateway` 均成功。

---

## Phase 2: Foundational — Domain Models + SQLite DDL + Store 接口

**目标**: 实现所有 Pydantic Domain Models、SQLite 建表 DDL 和 Store Protocol 接口定义。本阶段不包含实际存储实现，仅建立数据契约层。

### 任务列表

- [x] T011 实现枚举模块（TaskStatus、EventType、ActorType、RiskLevel、PartType + VALID_TRANSITIONS + TERMINAL_STATES） — `octoagent/packages/core/src/octoagent/core/models/enums.py`
- [x] T012 [P] 实现 Task Domain Model（Task、RequesterInfo、TaskPointers Pydantic 模型） — `octoagent/packages/core/src/octoagent/core/models/task.py`
- [x] T013 [P] 实现 Event Domain Model（Event、EventCausality Pydantic 模型） — `octoagent/packages/core/src/octoagent/core/models/event.py`
- [x] T014 [P] 实现 Artifact Domain Model（Artifact、ArtifactPart Pydantic 模型） — `octoagent/packages/core/src/octoagent/core/models/artifact.py`
- [x] T015 [P] 实现 NormalizedMessage Domain Model（NormalizedMessage、MessageAttachment Pydantic 模型） — `octoagent/packages/core/src/octoagent/core/models/message.py`
- [x] T016 [P] 实现所有 Event Payload 子类型（TaskCreatedPayload、UserMessagePayload、ModelCallStartedPayload、ModelCallCompletedPayload、ModelCallFailedPayload、StateTransitionPayload、ArtifactCreatedPayload、ErrorPayload） — `octoagent/packages/core/src/octoagent/core/models/payloads.py`
- [x] T017 [P] 创建 models 包入口，导出所有公共类型 — `octoagent/packages/core/src/octoagent/core/models/__init__.py`
- [x] T018 [P] 实现 Store Protocol 接口定义（TaskStore、EventStore、ArtifactStore Protocol 类） — `octoagent/packages/core/src/octoagent/core/store/protocols.py`
- [x] T019 [P] 实现配置常量模块（DB_PATH、ARTIFACTS_DIR、EVENT_PAYLOAD_MAX_BYTES=8192、ARTIFACT_INLINE_THRESHOLD=4096 等可配置常量） — `octoagent/packages/core/src/octoagent/core/config.py`
- [x] T020 编写 Domain Models 单元测试（枚举序列化、状态机合法/非法流转、Pydantic 校验） — `octoagent/packages/core/tests/test_models.py`

**依赖**: T011 必须先于 T012-T016 完成（枚举是其他模型的基础）。T012-T016 可并行。

---

## Phase 3: US-1 — 消息接收与任务创建

**US-1 目标**: Owner 通过 `POST /api/message` 发送消息后，系统自动创建任务，数据库新增 `tasks` 记录和两条事件（TASK_CREATED + USER_MESSAGE）。

**US-1 独立测试**: 调用 `POST /api/message`，断言 HTTP 201 + 返回 task_id + DB 中存在对应 Task 和 2 条事件。

### 任务列表

- [x] T021 实现 SQLite 数据库初始化逻辑（PRAGMA 配置 + 三张表 DDL + 索引创建，使用 aiosqlite） — `octoagent/packages/core/src/octoagent/core/store/sqlite_init.py`
- [x] T022 实现 TaskStore SQLite 实现（`create_task`、`get_task`、`list_tasks` 方法） — `octoagent/packages/core/src/octoagent/core/store/task_store.py`
- [x] T023 实现 EventStore SQLite 实现（`append_event`、`get_events_for_task`、`get_next_task_seq`、`check_idempotency_key` 方法） — `octoagent/packages/core/src/octoagent/core/store/event_store.py`
- [x] T024 实现事件+Projection 原子事务封装（`append_event_and_update_task` 在同一 SQLite 事务内原子提交） — `octoagent/packages/core/src/octoagent/core/store/transaction.py`
- [x] T025 实现 Store 包入口和工厂函数（创建共享 aiosqlite 连接的 Store 实例组） — `octoagent/packages/core/src/octoagent/core/store/__init__.py`
- [x] T026 实现 FastAPI app 主文件（app 创建 + lifespan 管理：DB 初始化/关闭 + 路由注册） — `octoagent/apps/gateway/src/octoagent/gateway/main.py`
- [x] T027 实现消息接收路由（请求体校验 + idempotency_key 去重 + HTTP 201/200 响应） — `octoagent/apps/gateway/src/octoagent/gateway/routes/message.py`
- [x] T028 实现 TaskService（`create_task` 方法：写入 TASK_CREATED + USER_MESSAGE 事件 + 创建 Task projection） — `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py`
- [x] T029 实现依赖注入模块（通过 `FastAPI Depends` 注入 Store 实例） — `octoagent/apps/gateway/src/octoagent/gateway/deps.py`
- [x] T030 编写 US-1 集成测试（正常创建 + idempotency_key 去重 + 事件落盘验证） — `octoagent/apps/gateway/tests/test_us1_message_creation.py`

**依赖**: T021 → T022, T023, T024 → T025 → T026-T029 顺序执行。

---

## Phase 4: US-2 — 事件溯源与状态一致性

**US-2 目标**: 任务的每一步操作以事件记录，任务状态通过同一事务与事件联动更新，events 表 append-only，task_seq 严格单调递增。

**US-2 独立测试**: 验证事件写入后 tasks 表状态在同一事务内更新，尝试直接 UPDATE 事件表应被禁止（应用层约束）。

### 任务列表

- [x] T031 实现 TaskStore `update_task_status` 方法（含 updated_at 和 latest_event_id 更新） — `octoagent/packages/core/src/octoagent/core/store/task_store.py`（追加）
- [x] T032 为 EventStore 添加 `task_seq` 严格单调递增约束验证（`get_next_task_seq` 返回 MAX+1 并在事务内加锁） — `octoagent/packages/core/src/octoagent/core/store/event_store.py`（追加）
- [x] T033 [P] 编写事务一致性单元测试（事件写入 + projection 更新原子性 + 回滚验证） — `octoagent/packages/core/tests/test_store_transaction.py`
- [x] T034 [P] 编写 task_seq 单调递增测试（同 task 并发写入时序号不重复） — `octoagent/packages/core/tests/test_event_seq.py`
- [x] T035 [P] 编写 idempotency_key 唯一约束测试（重复 key 数据库层报错验证） — `octoagent/packages/core/tests/test_idempotency.py`
- [x] T036 [P] 编写状态机流转单元测试（合法流转通过 + 非法流转抛出异常 + 终态不可再流转） — `octoagent/packages/core/tests/test_state_machine.py`

**依赖**: T031, T032 依赖 T022, T023（Phase 3 产出）。

---

## Phase 5: US-3 — SSE 实时事件推送

**US-3 目标**: Owner 通过 `GET /api/stream/task/{id}` 建立 SSE 连接，历史事件先推、新事件实时推送，任务终态时携带 `final: true`，支持 Last-Event-ID 断线重连。

**US-3 独立测试**: 建立 SSE 连接，启动任务，验证收到完整事件流，任务完成后收到 `final: true`，断线后携带 Last-Event-ID 重连能收到增量事件。

### 任务列表

- [x] T037 实现 SSEHub（内存中事件广播器，每个订阅者持有一个 asyncio.Queue，支持 subscribe/unsubscribe/broadcast） — `octoagent/apps/gateway/src/octoagent/gateway/services/sse_hub.py`
- [x] T038 实现 EventStore `get_events_after` 方法（从指定 event_id 之后查询增量事件，用于断线重连） — `octoagent/packages/core/src/octoagent/core/store/event_store.py`（追加）
- [x] T039 实现 SSE 路由（sse-starlette EventSourceResponse + 历史事件推送 + SSEHub 订阅 + 心跳保活 15s） — `octoagent/apps/gateway/src/octoagent/gateway/routes/stream.py`
- [x] T040 在 TaskService 中集成 SSEHub 广播（每次写入事件后调用 SSEHub.broadcast） — `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py`（追加）
- [x] T041 实现 SSE 断线重连逻辑（解析 Last-Event-ID 头，从该 ID 之后推送增量事件） — `octoagent/apps/gateway/src/octoagent/gateway/routes/stream.py`（追加）
- [x] T042 编写 SSE 集成测试（连接建立 + 历史事件接收 + 实时推送 + final 信号 + 断线重连） — `octoagent/apps/gateway/tests/test_us3_sse.py`

**依赖**: T037 → T039, T041。T038 依赖 T023（Phase 3）。T040 依赖 T028（Phase 3）和 T037。

---

## Phase 6: US-4 — 端到端 LLM 回路验证

**US-4 目标**: 发送消息后，系统调用 Echo LLM（输入回声），生成 MODEL_CALL_STARTED + MODEL_CALL_COMPLETED 双事件，LLM 响应作为 Artifact 存储，通过 SSE 推送完整链路，日志含一致的 trace_id。

**US-4 独立测试**: 发送 "Hello OctoAgent"，验证任务状态依次为 CREATED → RUNNING → SUCCEEDED，events 表含 6 条事件（TASK_CREATED、USER_MESSAGE、STATE_TRANSITION×2、MODEL_CALL_STARTED、MODEL_CALL_COMPLETED），Artifact 引用正确。

### 任务列表

- [x] T043 实现 LLMService 接口和 EchoProvider（Echo 模式返回输入回声，Mock 模式返回固定响应） — `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`
- [x] T044 实现 Artifact 文件系统存储（按 `data/artifacts/{task_id}/{artifact_id}` 路径存储，支持 inline < 4KB 和文件 >= 4KB） — `octoagent/packages/core/src/octoagent/core/store/artifact_store.py`
- [x] T045 实现 ArtifactStore SQLite 实现（`put_artifact`、`get_artifact`、`list_artifacts_for_task`、`get_artifact_content` 方法） — `octoagent/packages/core/src/octoagent/core/store/artifact_store.py`（合并实现）
- [x] T046 实现 SHA-256 hash 计算和 size 记录工具函数（对 inline content 字节和文件字节均适用） — `octoagent/packages/core/src/octoagent/core/store/artifact_store.py`（内部函数）
- [x] T047 实现异步 LLM 后台处理流程（`asyncio.create_task` 启动后台处理：STATE_TRANSITION(CREATED→RUNNING) + MODEL_CALL_STARTED + Echo 调用 + MODEL_CALL_COMPLETED + Artifact 写入 + STATE_TRANSITION(RUNNING→SUCCEEDED)） — `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py`（追加）
- [x] T048 实现 Event payload 8KB 阈值截断逻辑（超大内容自动转存 Artifact，payload 存摘要 + artifact_ref） — `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py`（内部函数）
- [x] T049 编写 ArtifactStore 单元测试（inline 文本存取 + 大文件存取 + hash 完整性校验） — `octoagent/packages/core/tests/test_artifact_store.py`
- [x] T050 编写 US-4 端到端集成测试（发送消息 → 验证完整事件链路 → 验证 Artifact 引用 → 验证 SSE 推送） — `octoagent/apps/gateway/tests/test_us4_llm_echo.py`

**依赖**: T043 → T047。T044-T046 → T047。T047 依赖 T028, T037, T040（Phase 3, 5）。

---

## Phase 7: US-5 — 进程重启后任务不丢失

**US-5 目标**: 进程重启后，所有任务状态、事件历史和 Artifact 元数据完整保留，SQLite WAL 模式确保持久性。

**US-5 独立测试**: 创建多个任务 → 模拟进程重启（关闭再重新初始化 Store）→ 验证所有任务查询结果与重启前一致。

### 任务列表

- [x] T051 确认 SQLite WAL 模式配置正确（在数据库初始化时验证 PRAGMA journal_mode = WAL 生效） — `octoagent/packages/core/src/octoagent/core/store/sqlite_init.py`（验证追加）
- [x] T052 编写进程重启持久性测试（创建任务 → 关闭 DB 连接 → 重新打开 → 验证数据完整） — `octoagent/packages/core/tests/test_durability.py`
- [x] T053 编写 FastAPI lifespan 测试（验证启动时 DB 初始化、关闭时连接清理） — `octoagent/apps/gateway/tests/test_lifespan.py`

**依赖**: T051 依赖 T021（Phase 3）。T052 依赖 T022, T023。

---

## Phase 8: US-6 — 产物存储与检索

**US-6 目标**: 任务产生的文本和文件 Artifact 被持久化，按 task_id 可检索，小于 4KB 的文本 inline 存储，大于 4KB 写文件系统，所有 Artifact 计算 SHA-256 hash。

**US-6 独立测试**: 写入 inline 文本 Artifact 和大文件 Artifact，通过 `list_artifacts_for_task` 检索，验证 inline content 可直接访问，大文件通过 storage_ref 访问，hash 和 size 正确。

### 任务列表

- [x] T054 实现 Artifact inline 阈值判断逻辑（< 4KB 存 parts.content，>= 4KB 写文件系统 + storage_ref） — `octoagent/packages/core/src/octoagent/core/store/artifact_store.py`（已包含于 T044-T046，此任务为验证覆盖）
- [x] T055 实现 ARTIFACT_CREATED 事件写入（每次 put_artifact 后在同一任务上下文中触发 ARTIFACT_CREATED 事件） — `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py`（追加）
- [x] T056 实现 Artifact 内容检索接口（`get_artifact_content` 支持 inline 直接返回 + 文件路径读取） — `octoagent/packages/core/src/octoagent/core/store/artifact_store.py`（追加）
- [x] T057 编写 US-6 完整集成测试（inline 文本存取 + 大文件存取 + hash 完整性校验 + task_id 检索） — `octoagent/packages/core/tests/test_us6_artifact.py`

**依赖**: T054, T055, T056 依赖 T044-T046（Phase 6）。

---

## Phase 9: US-7 — 可观测日志

**US-7 目标**: 所有日志包含结构化的 request_id（请求级）和 trace_id（任务级），开发环境 pretty print，生产环境 JSON，Logfire APM 可选集成。

**US-7 独立测试**: 发送一个请求，检查日志输出含 request_id 和 trace_id，dev 模式可读，json 模式结构化。

### 任务列表

- [x] T058 实现 structlog 配置模块（dev/json 两种渲染器，环境变量控制切换） — `octoagent/apps/gateway/src/octoagent/gateway/middleware/logging_config.py`
- [x] T059 实现 LoggingMiddleware（为每个 HTTP 请求生成 request_id，绑定到 structlog contextvars） — `octoagent/apps/gateway/src/octoagent/gateway/middleware/logging_mw.py`
- [x] T060 [P] 实现 TraceMiddleware（为任务操作绑定 trace_id，贯穿任务生命周期日志） — `octoagent/apps/gateway/src/octoagent/gateway/middleware/trace_mw.py`
- [x] T061 [P] 实现 Logfire 可选初始化（`LOGFIRE_SEND_TO_LOGFIRE` 环境变量控制，false 时降级为纯本地日志） — `octoagent/apps/gateway/src/octoagent/gateway/middleware/logging_config.py`（追加）
- [x] T062 在 FastAPI app 中注册中间件（LoggingMiddleware + TraceMiddleware 注册顺序） — `octoagent/apps/gateway/src/octoagent/gateway/main.py`（追加）
- [x] T063 编写可观测性测试（每条日志含 request_id + trace_id 验证） — `octoagent/apps/gateway/tests/test_us7_observability.py`

**依赖**: T058 → T059, T060, T061 → T062。

---

## Phase 10: US-8 — 任务取消

**US-8 目标**: Owner 可通过 `POST /api/tasks/{id}/cancel` 取消非终态任务，任务推进到 CANCELLED，SSE 推送取消事件并携带 `final: true`，终态任务返回 409。

**US-8 独立测试**: 取消 RUNNING 任务 → 验证 HTTP 200 + CANCELLED 状态 + 事件落盘 + SSE final 信号。取消 SUCCEEDED 任务 → 验证 HTTP 409。

### 任务列表

- [x] T064 实现任务取消路由（状态检查 + 404/409 错误处理） — `octoagent/apps/gateway/src/octoagent/gateway/routes/cancel.py`
- [x] T065 在 TaskService 中实现 `cancel_task` 方法（STATE_TRANSITION 事件写入 + tasks 表更新 + SSEHub 广播 final=true） — `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py`（追加）
- [x] T066 编写 US-8 集成测试（取消非终态任务 + 取消终态任务 409 + SSE final 信号验证） — `octoagent/apps/gateway/tests/test_us8_cancel.py`

**依赖**: T064, T065 依赖 T028（Phase 3）和 T037（Phase 5）。

---

## Phase 11: US-9 — Projection 重建

**US-9 目标**: 系统能从 events 表重建 tasks 表（清空后重放所有事件），重建后状态与原始一致，CLI 触发，日志记录处理事件数和耗时。

**US-9 独立测试**: 创建多个任务 → 清空 tasks 表 → 执行 rebuild → 验证重建结果与原始完全一致。

### 任务列表

- [x] T067 实现 `apply_event` 函数（单个事件应用到 Task 状态，支持所有 M0 事件类型） — `octoagent/packages/core/src/octoagent/core/projection.py`
- [x] T068 实现 `rebuild_all` 函数（清空 tasks 表 → 按 task_seq 顺序回放所有事件 → 重建 projection） — `octoagent/packages/core/src/octoagent/core/projection.py`（追加）
- [x] T069 实现 CLI 入口模块（`python -m octoagent.core rebuild-projections`） — `octoagent/packages/core/src/octoagent/core/__main__.py`
- [x] T070 编写 Projection 重建单元测试（重建前后状态一致性验证 + 重建日志包含事件数和耗时） — `octoagent/packages/core/tests/test_us9_projection.py`

**依赖**: T067 → T068 → T069。T067 依赖 T012-T016（Phase 2）和 T022, T023（Phase 3）。

---

## Phase 11.5: 任务查询 API — Web UI 前置依赖

**目标**: 实现任务列表和任务详情查询 API 路由，为 Web UI 提供后端支持。

### 任务列表

- [x] T081 实现任务列表查询路由（GET /api/tasks 支持 `?status=` 筛选，按 created_at 倒序，HTTP 200） — `octoagent/apps/gateway/src/octoagent/gateway/routes/tasks.py`
- [x] T082 [P] 实现任务详情查询路由（GET /api/tasks/{task_id} 返回 task + events + artifacts，404 处理） — `octoagent/apps/gateway/src/octoagent/gateway/routes/tasks.py`（追加）

**依赖**: T081, T082 依赖 T022（Phase 3，Store 初始化）。

---

## Phase 12: US-10 + US-11 — Web UI

**US-10 目标**: 浏览器展示所有任务列表（标题、状态颜色、创建时间），按创建时间倒序排列。
**US-11 目标**: 点击任务进入详情页，展示完整事件时间线，进行中任务通过 SSE 实时追加新事件。

**Web UI 独立测试**: 手动验证——启动后端 + 前端 dev server，在浏览器中验证任务列表展示和事件时间线实时更新。

### 任务列表

- [x] T071 初始化 React + Vite 前端项目（`npm create vite@latest` react-ts 模板 + 配置 Vite 代理到后端 :8000） — `octoagent/frontend/package.json`、`octoagent/frontend/vite.config.ts`
- [x] T072 实现 TypeScript 类型定义（Task、Event、Artifact 类型，与后端 Pydantic 模型对齐） — `octoagent/frontend/src/types/index.ts`
- [x] T073 实现 API Client 模块（`fetch` 封装，GET /api/tasks、GET /api/tasks/{id} 调用，错误处理） — `octoagent/frontend/src/api/client.ts`
- [x] T074 实现全局基础样式（CSS reset + 任务状态颜色变量：CREATED 灰/RUNNING 蓝/SUCCEEDED 绿/FAILED 红/CANCELLED 黄，无 CSS 框架） — `octoagent/frontend/src/index.css`
- [x] T075 实现 useSSE Hook（封装原生 EventSource，自动连接/断连/重连，事件类型分发，终态时关闭连接） — `octoagent/frontend/src/hooks/useSSE.ts`
- [x] T076 实现 TaskList 页面组件（调用 GET /api/tasks，展示任务列表，每项含标题、状态标记、创建时间，点击导航到详情） — `octoagent/frontend/src/pages/TaskList.tsx`
- [x] T077 实现 TaskDetail 页面组件（调用 GET /api/tasks/{id}，展示任务信息 + 事件时间线，进行中任务通过 useSSE 实时追加） — `octoagent/frontend/src/pages/TaskDetail.tsx`
- [x] T078 实现 App 主组件和路由配置（`/` → TaskList，`/tasks/:id` → TaskDetail，React Router 配置） — `octoagent/frontend/src/App.tsx`

**依赖**: T071 必须最先执行。T072 → T073, T075, T076, T077。T074 独立。T075 → T077。T076, T077 → T078。

---

## Phase 13: US-12 — 健康检查

**US-12 目标**: `GET /health` 永远返回 200 ok，`GET /ready` 检查 SQLite 连通性、artifacts 目录和磁盘空间，异常时返回 503。

**US-12 独立测试**: 验证 /health 返回 200，/ready 正常时返回 200 含 checks 结构，模拟 SQLite 不可用时 /ready 返回非 200。

### 任务列表

- [x] T079 实现健康检查路由（GET /health liveness + GET /ready readiness，包含 SQLite 连通性、artifacts_dir、disk_space_mb、litellm_proxy:skipped 检查） — `octoagent/apps/gateway/src/octoagent/gateway/routes/health.py`
- [x] T080 编写健康检查集成测试（/health 200 + /ready 正常 200 + /ready SQLite 不可用非 200） — `octoagent/apps/gateway/tests/test_us12_health.py`

**依赖**: T079 依赖 T026（Phase 3，FastAPI app）。

---

## Phase 14: Polish — 集成测试、静态文件托管与清理

**目标**: 端到端场景验证、前端静态文件单端口托管、代码质量检查、文档更新。

### 任务列表

- [x] T083 [P] 编写 SC-1 集成测试（POST /api/message → Task 创建 → Event 落盘 → SSE 推送完整链路） — `octoagent/tests/integration/test_sc1_e2e.py`
- [x] T084 [P] 编写 SC-2 持久性测试（进程重启后 tasks 状态完整，Web UI 可访问） — `octoagent/tests/integration/test_sc2_durability.py`
- [x] T085 [P] 编写 SC-3 Projection Rebuild 一致性测试（重建后与原始状态完全一致） — `octoagent/tests/integration/test_sc3_projection.py`
- [x] T086 [P] 编写 SC-4 Artifact 完整性测试（存储 + 检索 + hash 校验端到端） — `octoagent/tests/integration/test_sc4_artifact.py`
- [x] T087 [P] 编写 SC-6 Task 取消集成测试（取消正确推进到 CANCELLED + SSE final） — `octoagent/tests/integration/test_sc6_cancel.py`
- [x] T088 [P] 编写 SC-8 Echo LLM 回路集成测试（全链路事件 + Artifact 引用 + trace_id 一致性） — `octoagent/tests/integration/test_sc8_llm_echo.py`
- [x] T089 实现 FastAPI 静态文件托管（挂载 `frontend/dist/` 到根路由，单端口同时服务 API 和前端） — `octoagent/apps/gateway/src/octoagent/gateway/main.py`（追加）
- [x] T090 执行 ruff 代码风格检查并修复所有警告 — 全部 Python 文件
- [x] T091 [P] 确认所有公共函数具备完整类型注解 — 全部 Python 文件
- [x] T092 [P] 更新项目 README（项目架构说明、快速启动、API 文档链接） — `octoagent/README.md`

**依赖**: T081, T082 依赖 T022（Phase 3）。T083-T088 依赖所有对应 US Phase 完成。T089 依赖 T026 和 T071。T090-T092 在所有实现完成后执行。

---

## FR 覆盖映射表

> 确认 31 条功能需求 100% 覆盖。

| FR 编号 | 描述摘要 | 覆盖任务 |
|---------|---------|---------|
| FR-M0-DM-1 | Task 数据模型 | T012, T022 |
| FR-M0-DM-2 | Task 状态机（VALID_TRANSITIONS + TERMINAL_STATES） | T011, T036, T065 |
| FR-M0-DM-3 | Event 数据模型（ULID, task_seq, payload, trace_id） | T013, T023 |
| FR-M0-DM-4 | Artifact 数据模型（A2A parts + hash + size） | T014, T044, T045 |
| FR-M0-DM-5 | NormalizedMessage 数据模型 | T015, T027 |
| FR-M0-ES-1 | Event append-only 存储（禁止 UPDATE/DELETE） | T023, T033 |
| FR-M0-ES-2 | 事件与 Projection 事务一致性（同一事务） | T024, T033 |
| FR-M0-ES-3 | event_id ULID 时间有序 | T011, T023 |
| FR-M0-ES-4 | Projection Rebuild（清空 + 重放） | T067, T068, T069, T070 |
| FR-M0-ES-5 | task_seq 严格单调递增 | T032, T034 |
| FR-M0-API-1 | POST /api/message（消息接收 + idempotency_key 去重） | T027, T028, T030 |
| FR-M0-API-2 | GET /api/tasks（任务列表 + status 筛选） | T081 |
| FR-M0-API-3 | GET /api/tasks/{task_id}（任务详情 + events + artifacts） | T082 |
| FR-M0-API-4 | POST /api/tasks/{task_id}/cancel（任务取消） | T064, T065, T066 |
| FR-M0-API-5 | GET /api/stream/task/{task_id}（SSE 事件流） | T037, T038, T039, T041, T042 |
| FR-M0-API-6 | GET /health + GET /ready（健康检查） | T079, T080 |
| FR-M0-AS-1 | Artifact 文件系统按 task_id 分组存储 | T044 |
| FR-M0-AS-2 | Artifact 元数据 SQLite 存储 | T045 |
| FR-M0-AS-3 | Artifact inline 阈值（< 4KB inline，>= 4KB 写文件） | T044, T054 |
| FR-M0-AS-4 | Artifact SHA-256 hash + size 完整性校验 | T046, T049 |
| FR-M0-OB-1 | 结构化日志（dev pretty / prod JSON） | T058, T063 |
| FR-M0-OB-2 | 请求级 request_id（每个 HTTP 请求） | T059, T063 |
| FR-M0-OB-3 | 任务级 trace_id（任务生命周期贯穿） | T060, T063 |
| FR-M0-OB-4 | Logfire APM 可选集成（send_to_logfire 开关） | T061 |
| FR-M0-UI-1 | Web UI 任务列表页（标题、状态、创建时间、倒序） | T071, T073, T074, T076, T078 |
| FR-M0-UI-2 | Web UI 事件时间线（类型、时间、payload 摘要） | T072, T075, T077 |
| FR-M0-UI-3 | Web UI SSE 实时更新（原生 EventSource） | T075, T077 |
| FR-M0-UI-4 | 最小化 UI 范围（两个页面，无 CSS 框架，无状态管理库） | T071, T074, T078 |
| FR-M0-LLM-1 | Echo 模式（输入回声，不依赖外部 LLM） | T043 |
| FR-M0-LLM-2 | LLM 调用双事件化（MODEL_CALL_STARTED + COMPLETED/FAILED） | T043, T047, T048 |
| FR-M0-LLM-3 | LLM 客户端抽象（model alias，便于替换代理） | T043 |

**覆盖率**: 31/31 = 100%

---

## 依赖关系与并行说明

### Phase 间依赖

```
Phase 1 (Setup)
  └── Phase 2 (Foundational: Domain Models)
        └── Phase 3 (US-1: Task 创建 + Store 实现)
              ├── Phase 4 (US-2: 事务一致性) [可并行于 Phase 5]
              ├── Phase 5 (US-3: SSE Hub) [可并行于 Phase 4]
              │     └── Phase 6 (US-4: LLM 回路)
              │           └── Phase 7 (US-5: 持久性验证)
              │           └── Phase 8 (US-6: Artifact)
              │           └── Phase 9 (US-7: 日志中间件)
              │           └── Phase 10 (US-8: 任务取消)
              │           └── Phase 11 (US-9: Projection 重建)
              └── Phase 12 (US-10+11: Web UI) [可与 Phase 4-11 并行启动]
              └── Phase 13 (US-12: 健康检查) [可与 Phase 4-11 并行]
                    └── Phase 14 (Polish: 集成测试 + 清理)
```

### User Story 间依赖

| US | 依赖 | 说明 |
|----|------|------|
| US-2 | US-1 | 状态机验证需要有任务存在 |
| US-3 | US-1, US-2 | SSE 依赖事件写入机制 |
| US-4 | US-1, US-3 | LLM 回路依赖任务创建和 SSE 广播 |
| US-5 | US-1 | 持久性验证需要有任务创建 |
| US-6 | US-4 | Artifact 由 LLM 服务写入 |
| US-8 | US-1, US-3 | 取消需要任务存在和 SSE 广播 |
| US-9 | US-2 | Projection 重建依赖事件记录 |
| US-10, US-11 | US-1 至 US-4 | Web UI 消费后端 API |
| US-12 | US-1（DB 初始化） | /ready 检查 SQLite 连通性 |

### Story 内部并行机会

| Phase | 可并行任务组 |
|-------|------------|
| Phase 2 | T012, T013, T014, T015, T016, T017, T018, T019 均可并行（均依赖 T011） |
| Phase 4 | T033, T034, T035, T036 均可并行（测试任务） |
| Phase 9 | T059, T060, T061 可并行（均依赖 T058） |
| Phase 12 | T072, T073, T074, T075 可并行（均依赖 T071） |
| Phase 14 | T083-T088, T090, T091, T092 均可并行 |

---

## 推荐实现策略

**MVP First（推荐）**:

1. 优先交付 US-1 → US-2 → US-3 → US-4（P1 核心链路），这 4 个 US 构成完整的端到端回路
2. 接着完成 US-5（持久性验证，Constitution C1 要求）和 US-8（任务取消，Constitution C7 要求）
3. US-6、US-7 可穿插在 US-4 之后进行
4. US-9（Projection Rebuild）和 US-10/11（Web UI）可并行开发
5. US-12（健康检查）最轻量，可在任意 Phase 穿插完成

**MVP 最小范围**: US-1 + US-2 + US-3 + US-4（前 4 个 P1 US），约覆盖 Phase 1-6，可在 5-6 天内完成并进行第一次端到端演示。
