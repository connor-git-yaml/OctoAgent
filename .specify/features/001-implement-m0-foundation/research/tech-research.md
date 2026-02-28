# M0 基础底座 -- 技术调研报告

> **[独立模式]** 本次技术调研未参考产品调研结论，直接基于 blueprint.md 需求描述与代码上下文执行。

## 0. 文档元信息

| 字段 | 值 |
|------|------|
| 功能 | M0 基础底座（Task/Event/Artifact + SSE + 最小 Web UI） |
| Blueprint 依据 | `docs/blueprint.md` -- 14.M0 里程碑定义 |
| 调研日期 | 2026-02-28 |
| 本地环境 | Python 3.12.8 / macOS Darwin 25.3.0 |
| 项目状态 | 尚无业务代码，仅有 blueprint + CLAUDE.md |

---

## 1. M0 功能范围确认

根据 blueprint 14 里程碑定义，M0 需交付：

| # | 交付项 | Blueprint 来源 |
|---|--------|---------------|
| 1 | SQLite schema + event append API + projection | 8.2 |
| 2 | `/ingest_message` 创建 task + 写 USER_MESSAGE 事件 | 10.1 |
| 3 | `/stream/task/{task_id}` SSE 事件流 | 10.1 |
| 4 | Artifact store（文件系统） | 8.1.2 |
| 5 | 可观测性基础：structlog + request_id/trace_id | 7.7 |
| 6 | 最小 LLM 回路：hardcoded model call -> 事件记录 -> SSE 推送 | M0 验收标准 |
| 7 | 最小 Web UI：task 列表 + 事件流 | 9.12 |

---

## 2. SQLite 事件溯源

### 2.1 架构方案对比

| 维度 | 方案 A: aiosqlite（推荐） | 方案 B: 同步 sqlite3 + run_in_executor |
|------|--------------------------|--------------------------------------|
| 并发模型 | 原生 async，通过内部线程池代理 | 需手动 `asyncio.to_thread()` 包装 |
| FastAPI 集成 | 天然兼容 async endpoint | 需额外封装，代码风格不统一 |
| WAL 支持 | 完整支持，非阻塞读 | 完整支持 |
| Python 3.12 兼容性 | v0.21.0 已验证 | 标准库原生支持 |
| 社区维护 | omnilib 维护，PyPI 月下载量 ~3M | 标准库，无需额外依赖 |
| 学习曲线 | 低（API 与 sqlite3 几乎一致） | 低 |
| 性能 | 并发 I/O 下约 4x 优于串行 sqlite3 | 串行略快（无队列开销） |

**推荐：方案 A（aiosqlite）**。理由：OctoAgent 基于 FastAPI 异步架构，aiosqlite 与 async/await 天然兼容，避免在 async endpoint 中调用同步数据库操作导致 event loop 阻塞。

### 2.2 WAL 模式并发策略

```python
# 连接初始化 PRAGMA 配置
PRAGMAS = [
    "PRAGMA journal_mode=WAL;",        # WAL 模式：允许并发读写
    "PRAGMA synchronous=NORMAL;",       # 速度/持久性平衡（WAL 下安全）
    "PRAGMA busy_timeout=5000;",        # 写冲突等待 5s
    "PRAGMA cache_size=-64000;",        # 64MB 缓存
    "PRAGMA foreign_keys=ON;",          # 启用外键约束
    "PRAGMA wal_autocheckpoint=1000;",  # 每 1000 页自动 checkpoint
]
```

关键并发规则：
- **读：** WAL 模式下支持无限并发读（快照隔离），不阻塞写
- **写：** SQLite 单写者限制。M0 单进程场景下不成问题
- **写事务策略：** 使用 `BEGIN IMMEDIATE` 尽早获取写锁，避免事务中途升级失败
- **M1+ 多进程考量：** 当引入 Worker 独立进程时，写操作需通过消息队列串行化（预留接口即可，M0 不需实现）

### 2.3 Schema 设计

基于 blueprint 8.2.2 的表建议，优化后的 M0 schema：

```sql
-- 事件表（append-only，事实来源）
CREATE TABLE events (
    event_id   TEXT PRIMARY KEY,          -- ULID，时间有序
    task_id    TEXT NOT NULL,
    ts         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
    type       TEXT NOT NULL,             -- TASK_CREATED|USER_MESSAGE|MODEL_CALL|...
    schema_version INTEGER NOT NULL DEFAULT 1,
    actor      TEXT NOT NULL,             -- user|kernel|worker|tool|system
    payload    TEXT NOT NULL DEFAULT '{}', -- JSON
    trace_id   TEXT,
    span_id    TEXT,
    parent_event_id   TEXT,
    idempotency_key   TEXT UNIQUE,        -- 幂等键，防重复写入
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
CREATE INDEX idx_events_task_ts ON events(task_id, ts);
CREATE INDEX idx_events_type ON events(type);

-- 任务表（projection，物化视图）
CREATE TABLE tasks (
    task_id       TEXT PRIMARY KEY,       -- UUID v4
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'CREATED',
    title         TEXT,
    thread_id     TEXT,
    scope_id      TEXT,
    parent_task_id TEXT,
    requester     TEXT DEFAULT '{}',      -- JSON: {channel, sender_id}
    assigned_worker TEXT,
    risk_level    TEXT DEFAULT 'low',
    budget        TEXT DEFAULT '{}',      -- JSON
    latest_event_id TEXT,
    latest_checkpoint_id TEXT
);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_thread ON tasks(thread_id);
CREATE INDEX idx_tasks_updated ON tasks(updated_at);

-- 产物表（元数据，文件在文件系统）
CREATE TABLE artifacts (
    artifact_id  TEXT PRIMARY KEY,        -- ULID
    task_id      TEXT NOT NULL,
    ts           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
    name         TEXT NOT NULL,
    description  TEXT,
    parts        TEXT NOT NULL DEFAULT '[]',  -- JSON: [{type,mime,content,uri}]
    storage_ref  TEXT,
    size         INTEGER,
    hash         TEXT,                    -- sha256
    version      INTEGER NOT NULL DEFAULT 1,
    append       INTEGER NOT NULL DEFAULT 0,  -- boolean
    last_chunk   INTEGER NOT NULL DEFAULT 0,  -- boolean
    meta         TEXT DEFAULT '{}',       -- JSON
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
CREATE INDEX idx_artifacts_task ON artifacts(task_id);

-- schema 版本追踪
CREATE TABLE schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
    description TEXT
);
```

### 2.4 写事件 + 更新 Projection 的单事务模式

这是 M0 事件溯源的核心模式。blueprint 8.2.1 明确要求"写事件与更新 projection 必须在同一事务内"：

```python
async def append_event_and_update_task(
    db: aiosqlite.Connection,
    event: Event,
    task_updates: dict[str, Any],
) -> None:
    """在单事务内写入事件并更新 task projection。"""
    async with db.execute("BEGIN IMMEDIATE"):
        # 1. 写入事件（append-only）
        await db.execute(
            """INSERT INTO events
               (event_id, task_id, ts, type, schema_version, actor,
                payload, trace_id, span_id, parent_event_id, idempotency_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event.event_id, event.task_id, event.ts, event.type,
             event.schema_version, event.actor, event.payload_json,
             event.trace_id, event.span_id,
             event.causality.parent_event_id,
             event.causality.idempotency_key),
        )
        # 2. 更新 task projection
        set_clause = ", ".join(f"{k} = ?" for k in task_updates)
        values = list(task_updates.values()) + [event.task_id]
        await db.execute(
            f"UPDATE tasks SET {set_clause}, "
            f"latest_event_id = ?, updated_at = ? "
            f"WHERE task_id = ?",
            values + [event.event_id, event.ts, event.task_id],
        )
        await db.commit()
```

### 2.5 Projection 重建（Replay）

Blueprint 8.2.2 提到"Projection 重建"能力，M0 应实现最小版本：

```python
async def rebuild_task_projection(db: aiosqlite.Connection, task_id: str) -> None:
    """从事件流重建 task projection（用于崩溃恢复或数据修复）。"""
    async with db.execute(
        "SELECT * FROM events WHERE task_id = ? ORDER BY event_id", (task_id,)
    ) as cursor:
        task_state = {"status": "CREATED"}
        async for row in cursor:
            event_type = row["type"]
            payload = json.loads(row["payload"])
            task_state = apply_event_to_projection(task_state, event_type, payload)
        # 写回 projection
        await db.execute(
            "UPDATE tasks SET status = ?, ... WHERE task_id = ?",
            (task_state["status"], ..., task_id),
        )
```

### 2.6 ULID 选型

| 库 | 版本 | 特点 |
|----|------|------|
| python-ulid | v3.1.0 | 纯 Python，Pydantic 集成，CLI 工具 |

**推荐 python-ulid**。ULID 相比 UUID 的优势：时间有序（天然支持按时间排序查询事件）、可排序、兼容 UUID 格式、128-bit 无碰撞风险。Blueprint 8.2.2 明确要求"events 使用 ULID/时间有序 id"。

### 2.7 与 Blueprint 的一致性分析

| Blueprint 设计 | M0 实现建议 | 偏差说明 |
|---------------|-------------|---------|
| 8.2.2 五张表（tasks/events/artifacts/checkpoints/approvals） | M0 实现 3 张（tasks/events/artifacts） | checkpoints 和 approvals 在 M1.5 引入，M0 无需 |
| 8.1.2 Task.budget 字段 | M0 存为 JSON 但不校验 | M1 接入 LiteLLM 后才有意义 |
| 8.2.1 "写事件+更新 projection 同事务" | 完整实现 | 核心一致性保障 |
| Event.causality 字段 | M0 实现 parent_event_id + idempotency_key | 完整实现 |

---

## 3. SSE 事件流

### 3.1 方案对比

| 维度 | 方案 A: sse-starlette（推荐） | 方案 B: 原生 StreamingResponse |
|------|------------------------------|-------------------------------|
| SSE 合规性 | 完整 W3C 规范：自动格式化 data/event/id/retry | 需手动拼接 `data: ...\n\n` 字符串 |
| 重连支持 | 内置 Last-Event-ID 支持 | 需手动解析 header 和状态管理 |
| 断连检测 | 自动 `request.is_disconnected()` + CancelledError | 需手动检查 |
| 心跳 | 内置 ping 机制（可配置间隔） | 需手动实现 |
| 性能 | ~130k/s 单客户端，6-13k events/s（20客户端） | 原始吞吐略高 ~10-20% |
| 额外依赖 | `pip install sse-starlette` (v3.0.2) | 无 |
| 代码复杂度 | 低：yield dict 即可 | 中：需处理格式化/心跳/断连 |

**推荐：方案 A（sse-starlette）**。理由：
1. M0 的 SSE 端点需要支持客户端重连（Last-Event-ID），sse-starlette 内置支持
2. Blueprint 10.1 要求"终态事件携带 `final: true`"，sse-starlette 的 event 类型支持天然匹配
3. 代码量显著更少，且处理了生产环境常见的边界情况（zombie connection 等）

### 3.2 M0 SSE 实现模式

```python
from fastapi import FastAPI, Request
from sse_starlette.sse import EventSourceResponse

app = FastAPI()

@app.get("/kernel/stream/task/{task_id}")
async def stream_task_events(task_id: str, request: Request):
    """SSE 端点：推送 task 的事件流。"""
    # 获取客户端重连时的 last-event-id
    last_event_id = request.headers.get("last-event-id")

    async def event_generator():
        # 1. 先回放历史事件（从 last_event_id 之后开始）
        async for event in replay_events(task_id, after=last_event_id):
            yield {
                "event": event.type,
                "id": event.event_id,
                "data": event.payload_json,
            }

        # 2. 然后实时推送新事件
        async for event in subscribe_new_events(task_id):
            yield {
                "event": event.type,
                "id": event.event_id,
                "data": event.payload_json,
            }
            # 终态信号：客户端据此关闭连接
            if is_terminal_state(event):
                return

    return EventSourceResponse(
        event_generator(),
        ping=15,           # 15 秒心跳
        ping_message_factory=lambda: {"comment": "keepalive"},
    )
```

### 3.3 实时推送机制

M0 阶段推荐使用 **asyncio.Queue 的发布-订阅模式**（进程内）：

```python
import asyncio
from collections import defaultdict

class EventBus:
    """进程内事件总线，支持按 task_id 订阅。"""
    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, task_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[task_id].append(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        self._subscribers[task_id].remove(queue)
        if not self._subscribers[task_id]:
            del self._subscribers[task_id]

    async def publish(self, task_id: str, event: dict) -> None:
        for queue in self._subscribers.get(task_id, []):
            await queue.put(event)
```

**M1+ 演进路径：** 当引入多进程 Worker 时，EventBus 可升级为 Redis Pub/Sub 或 PostgreSQL LISTEN/NOTIFY，接口保持不变。

### 3.4 与 Blueprint 的一致性分析

| Blueprint 设计 | M0 实现 | 偏差 |
|---------------|---------|------|
| 10.1 `GET /kernel/stream/task/{task_id}` SSE events | 完整实现 | 无 |
| 10.1 终态事件携带 `"final": true` | 通过 event type + payload 实现 | 无 |
| FR-TASK-2 每条事件有唯一 id/类型/时间/payload/trace_id | ULID 作为 id，全部字段齐备 | 无 |

---

## 4. Artifact Store

### 4.1 方案对比

| 维度 | 方案 A: 扁平目录（task 分组）（推荐） | 方案 B: Content-Addressable（HashFS） |
|------|-------------------------------------|--------------------------------------|
| 目录结构 | `data/artifacts/{task_id}/{artifact_id}/` | `data/artifacts/{hash_prefix[0:2]}/{hash_prefix[2:4]}/{full_hash}` |
| 查找方式 | 按 task_id 直接定位 | 需通过 SQLite 元数据查 hash 再定位 |
| 去重 | 不去重（同内容可存多份） | 自动去重（同 hash 只存一次） |
| 复杂度 | 极低 | 中等 |
| 适用场景 | 单用户、任务绑定的产物 | 大规模、需去重的存储 |
| 删除/清理 | 按 task_id 整目录删除 | 需引用计数，复杂 |

**推荐：方案 A（扁平目录，task 分组）**。理由：
1. M0 是单用户系统，去重收益极低
2. 按 task_id 组织直觉清晰，与 blueprint 的 "artifact 可按 task_id 检索" 验收标准一致
3. 清理/归档操作直接操作目录即可
4. 足够简单，后续如需去重可在不改接口的前提下叠加

### 4.2 目录结构设计

```
data/
  artifacts/
    {task_id}/                    # 以 task UUID 为一级分组
      {artifact_id}.meta.json     # 元数据（冗余，主要用于离线诊断）
      {artifact_id}/              # 产物文件目录
        part_0.txt                # text part -> 纯文本文件
        part_1.json               # json part -> JSON 文件
        part_2.bin                # file part -> 二进制文件
```

### 4.3 ArtifactStore 接口设计

```python
from pathlib import Path
import hashlib

class FileArtifactStore:
    """基于文件系统的 Artifact 存储。"""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def put(
        self, task_id: str, artifact_id: str, parts: list[ArtifactPart],
    ) -> str:
        """存储产物，返回 storage_ref。"""
        artifact_dir = self.base_dir / task_id / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        for i, part in enumerate(parts):
            filepath = artifact_dir / f"part_{i}{self._ext(part)}"
            await self._write_part(filepath, part)
        return f"file://{artifact_dir}"

    async def get(self, task_id: str, artifact_id: str) -> Path:
        """获取产物目录路径。"""
        return self.base_dir / task_id / artifact_id

    async def list_by_task(self, task_id: str) -> list[str]:
        """列出 task 下所有产物 ID。"""
        task_dir = self.base_dir / task_id
        if not task_dir.exists():
            return []
        return [d.name for d in task_dir.iterdir() if d.is_dir()]

    def compute_hash(self, content: bytes) -> str:
        """计算 SHA-256 hash。"""
        return hashlib.sha256(content).hexdigest()
```

### 4.4 流式追加支持

Blueprint 8.1.2 定义了 `append: true` + `last_chunk: true` 的流式追加模式：

```python
async def append_part(
    self, task_id: str, artifact_id: str, part: ArtifactPart, is_last: bool,
) -> None:
    """追加 part 到已有 artifact（流式写入场景）。"""
    artifact_dir = self.base_dir / task_id / artifact_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    existing = len(list(artifact_dir.iterdir()))
    filepath = artifact_dir / f"part_{existing}{self._ext(part)}"
    await self._write_part(filepath, part)
```

### 4.5 与 Blueprint 的一致性分析

| Blueprint 设计 | M0 实现 | 偏差 |
|---------------|---------|------|
| 8.1.2 多 Part 结构 | 每个 Part 独立文件存储 | 无 |
| 8.1.2 inline 内容与 URI 引用双模 | 小内容 inline（存 SQLite payload），大文件 file:// URI | 无 |
| 8.1.2 version/hash/size | hash 计算 + version 字段 + size 统计 | 无 |
| 8.1.2 append + last_chunk | 实现追加模式 | 无 |
| M0 验收标准 "artifact 文件可存储、可按 task_id 检索" | 直接按 task_id 目录检索 | 完全匹配 |

---

## 5. 最小 Web UI（React + Vite）

### 5.1 方案对比

| 维度 | 方案 A: 原生 React（无状态库）（推荐） | 方案 B: React + Zustand/Jotai |
|------|---------------------------------------|------------------------------|
| 依赖数量 | react + react-dom + vite（3 个核心依赖） | 额外引入状态管理库 |
| 复杂度 | 低（useState + useEffect + useReducer） | 中等 |
| M0 适用性 | task 列表 + 事件流两个组件，状态简单 | 过度设计 |
| M1+ 扩展 | 需要时再引入 | 提前就位 |
| 学习曲线 | 极低 | 低 |

**推荐：方案 A（原生 React，无状态管理库）**。M0 只需两个页面组件，状态结构极其简单（task 列表 + 当前 task 的事件流），不需要全局状态管理。M1+ 引入 Approvals/Config/Memory 面板后再评估是否引入 Zustand。

### 5.2 组件设计

```
frontend/
  src/
    App.tsx                 # 路由（仅两个视图）
    components/
      TaskList.tsx          # task 列表（GET /api/tasks，轮询刷新）
      EventStream.tsx       # 事件时间线（SSE /api/stream/task/{id}）
    hooks/
      useSSE.ts             # EventSource 封装 hook
      useApi.ts             # fetch 封装
    types/
      task.ts               # Task/Event/Artifact 类型定义
    main.tsx
  index.html
  vite.config.ts
  package.json
  tsconfig.json
```

### 5.3 SSE EventSource 封装

```typescript
// hooks/useSSE.ts
import { useEffect, useRef, useCallback, useState } from 'react';

interface SSEEvent {
  id: string;
  type: string;
  data: string;
}

export function useSSE(url: string | null) {
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!url) return;

    const es = new EventSource(url);
    esRef.current = es;

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    // 监听所有事件类型
    const handleEvent = (e: MessageEvent) => {
      setEvents(prev => [...prev, {
        id: e.lastEventId,
        type: e.type,
        data: e.data,
      }]);
    };

    // 注册 blueprint 定义的事件类型
    const eventTypes = [
      'TASK_CREATED', 'USER_MESSAGE', 'MODEL_CALL',
      'TOOL_CALL', 'TOOL_RESULT', 'STATE_TRANSITION',
      'ARTIFACT_CREATED', 'ERROR', 'HEARTBEAT',
    ];
    eventTypes.forEach(type => es.addEventListener(type, handleEvent));
    es.onmessage = handleEvent; // fallback

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [url]);

  return { events, connected };
}
```

### 5.4 Vite 开发代理配置

```typescript
// vite.config.ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',  // FastAPI Gateway
        changeOrigin: true,
      },
    },
  },
});
```

### 5.5 M0 前端依赖清单

```json
{
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "^5.6.0",
    "vite": "^6.0.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0"
  }
}
```

**注意：** M0 不引入 CSS 框架。使用 CSS Modules 或简单的 CSS 文件即可。M1+ 可根据需要引入 Tailwind CSS。

### 5.6 与 Blueprint 的一致性分析

| Blueprint 设计 | M0 实现 | 偏差 |
|---------------|---------|------|
| 9.12 M0: TaskList + EventStream 两个核心组件 | 完全匹配 | 无 |
| 9.12 SSE 消费：原生 EventSource 对接 Gateway | 使用 useSSE hook 封装 | 无 |
| 9.12 开发时 Vite dev server 代理到 Gateway | vite.config.ts proxy 配置 | 无 |
| 9.12 独立于 Python 后端 | 独立 frontend/ 目录，通过 API 通信 | 无 |

---

## 6. Logfire + structlog 集成

### 6.1 方案对比

| 维度 | 方案 A: Logfire + structlog（推荐） | 方案 B: 纯 structlog + 标准 OTel |
|------|--------------------------------------|----------------------------------|
| 自动 instrument | FastAPI / Pydantic AI / pydantic-graph 全自动 | 需手动配置 OTel + 多个 instrumentor |
| LLM 可观测 | 内置 token/cost/latency 追踪 | 需自建 |
| structlog 集成 | `logfire.StructlogProcessor()` 一行代码 | 需要 OTel SDK + exporter 配置 |
| 维护成本 | Pydantic 团队维护，与核心依赖同生态 | 多组件集成，升级风险高 |
| 付费考量 | 有免费 tier | 需自建 Jaeger/Grafana |

**推荐：方案 A（Logfire + structlog）**。Blueprint 7.7 已明确选择此方案，且 Logfire 与 Pydantic AI 同生态，M1.5 接入 Pydantic AI 时可无缝获得 LLM 可观测性。

### 6.2 M0 阶段配置

```python
# packages/observability/setup.py
import logfire
import structlog

def setup_observability(app=None, service_name: str = "octoagent"):
    """M0 可观测性初始化。"""
    # 1. Logfire 初始化
    logfire.configure(
        service_name=service_name,
        send_to_logfire=True,  # 上报到 Logfire 云端（可选，开发期可 False）
    )

    # 2. FastAPI 自动 instrument
    if app:
        logfire.instrument_fastapi(app)

    # 3. structlog 配置
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,       # 上下文变量注入
            structlog.processors.add_log_level,             # 日志级别
            structlog.processors.TimeStamper(fmt="iso"),     # ISO 时间戳
            structlog.processors.StackInfoRenderer(),        # 异常栈
            logfire.StructlogProcessor(),                    # 转发到 Logfire
            structlog.dev.ConsoleRenderer(),                 # 开发环境美化输出
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
```

### 6.3 M0 阶段应记录的 Span/Metric

| Span/Metric | 类型 | 说明 |
|-------------|------|------|
| `gateway.ingest_message` | Span | 消息接收 -> task 创建的全链路 |
| `store.append_event` | Span | 事件写入（含 task projection 更新） |
| `store.query_events` | Span | 事件查询 |
| `sse.stream` | Span | SSE 连接生命周期 |
| `llm.call` | Span | LLM 调用（M0 hardcoded 版本） |
| `artifact.put` | Span | 产物写入 |
| `request_id` / `trace_id` | Context | 贯穿所有日志和 span |
| `task_id` | Context | 绑定到任务相关的所有操作 |

### 6.4 trace_id 贯穿策略

```python
# 在请求入口绑定 context
from structlog.contextvars import bind_contextvars, clear_contextvars

@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    bind_contextvars(request_id=request_id)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        clear_contextvars()
```

### 6.5 依赖版本

| 库 | 版本 | 说明 |
|----|------|------|
| logfire | ~=4.24.0 | Pydantic 团队出品，OTel 原生 |
| structlog | ~=25.4.0 | 结构化日志，Python 3.12 验证 |

### 6.6 与 Blueprint 的一致性分析

| Blueprint 设计 | M0 实现 | 偏差 |
|---------------|---------|------|
| 7.7 Logfire 自动 instrument FastAPI | `logfire.instrument_fastapi(app)` | 无 |
| 7.7 structlog canonical log lines | `merge_contextvars` 自动绑定 trace_id/task_id | 无 |
| M0 验收 "所有日志包含 request_id/trace_id" | middleware + contextvars 绑定 | 无 |
| 7.7 Event Store metrics（SQL 聚合） | M0 暂不实现 metrics 聚合 UI，但数据已落盘 | 合理裁剪 |

**注意（blueprint 审查）：** Blueprint 7.7 提到"不需要 Prometheus"——这里需注意 M0 的 metrics 确实通过 Event Store SQL 查询即可，不需要独立的 metrics 采集。但 Logfire 本身提供了 metrics 面板，两者互补而非冲突。

---

## 7. 最小 LLM 回路

M0 需要一个 hardcoded model call 来端到端验证事件系统。这不是完整的 LLM 集成（那是 M1），而是验证：

```
消息接收 -> task 创建 -> LLM 调用 -> 事件记录 -> SSE 推送
```

### 7.1 M0 LLM 方案

| 方案 | 说明 |
|------|------|
| 直接调用 OpenAI/Anthropic SDK | 最简单，但违反"不在业务代码写死厂商" |
| 通过 httpx 调 LiteLLM Proxy | 需要先部署 LiteLLM Proxy |
| **litellm 客户端库直接调用（推荐）** | `pip install litellm`，直接 `litellm.acompletion()`，支持多 provider |

**推荐：使用 litellm 客户端库**。理由：
1. 不需要 M0 就部署 LiteLLM Proxy（M1 才需要）
2. litellm 客户端已支持 100+ provider，切换零成本
3. 保持"不写死厂商"的 Constitution 约束
4. M1 升级到 Proxy 时只需改 base_url

```python
import litellm

async def hardcoded_llm_call(user_message: str) -> str:
    """M0 最小 LLM 回路。"""
    response = await litellm.acompletion(
        model="gpt-4o-mini",  # M0 hardcoded，M1 改为 alias
        messages=[
            {"role": "system", "content": "你是 OctoAgent 助手。"},
            {"role": "user", "content": user_message},
        ],
        max_tokens=1024,
    )
    return response.choices[0].message.content
```

---

## 8. M0 项目结构评估

### 8.1 Blueprint 完整结构 vs M0 裁剪

Blueprint 9.1 定义了完整的 monorepo 结构。M0 需要裁剪到最小可行：

```
octoagent/
  pyproject.toml              # 根 pyproject（uv workspace）
  uv.lock
  packages/
    core/                     # [M0] Domain Models + Event Store + Artifact Store
      pyproject.toml
      src/octoagent_core/
        __init__.py
        models/               # Task / Event / Artifact Pydantic 模型
          __init__.py
          task.py
          event.py
          artifact.py
          enums.py            # TaskStatus / EventType 等枚举
        store/
          __init__.py
          database.py         # aiosqlite 连接管理 + PRAGMA
          event_store.py      # append_event / query_events
          task_store.py       # create_task / get_task / list_tasks
          artifact_store.py   # put / get / list_by_task
          migrations.py       # schema 初始化 + 版本管理
        bus.py                # EventBus（进程内 pub/sub）
    protocol/                 # [M0-minimal] NormalizedMessage 定义
      pyproject.toml
      src/octoagent_protocol/
        __init__.py
        messages.py           # NormalizedMessage
    observability/            # [M0] structlog + Logfire 初始化
      pyproject.toml
      src/octoagent_observability/
        __init__.py
        setup.py              # setup_observability()
  apps/
    gateway/                  # [M0] FastAPI API + SSE
      pyproject.toml
      src/octoagent_gateway/
        __init__.py
        app.py                # FastAPI app 创建 + middleware
        routes/
          __init__.py
          ingest.py           # POST /api/message -> /kernel/ingest_message
          stream.py           # GET /api/stream/task/{task_id}
          tasks.py            # GET /api/tasks（列表）
        deps.py               # FastAPI 依赖注入
  frontend/                   # [M0] React + Vite
    package.json
    vite.config.ts
    tsconfig.json
    index.html
    src/
      main.tsx
      App.tsx
      components/
        TaskList.tsx
        EventStream.tsx
      hooks/
        useSSE.ts
        useApi.ts
      types/
        task.ts
  data/                       # runtime 数据（.gitignore）
    sqlite/
    artifacts/
  tests/
    test_core/
      test_event_store.py
      test_task_store.py
      test_artifact_store.py
    test_gateway/
      test_ingest.py
      test_stream.py
```

### 8.2 M0 裁剪说明

| Blueprint 目录 | M0 状态 | 说明 |
|---------------|---------|------|
| packages/core | **实现** | 核心数据模型 + 事件存储 |
| packages/protocol | **最小实现** | 仅 NormalizedMessage |
| packages/observability | **实现** | structlog + Logfire 初始化 |
| packages/plugins | 跳过 | M2+ 才需要 |
| packages/tooling | 跳过 | M1 才需要 |
| packages/memory | 跳过 | M2 才需要 |
| packages/provider | 跳过 | M1 才需要（M0 用 litellm 客户端直连） |
| apps/gateway | **实现** | M0 合并 gateway + kernel 为单进程 |
| apps/kernel | **合并到 gateway** | M0 不需要独立 kernel 进程 |
| apps/workers | 跳过 | M1.5 才需要 |
| frontend/ | **实现** | 最小 React UI |
| plugins/ | 跳过 | M2+ 才需要 |

### 8.3 M0 Gateway/Kernel 合并的合理性

**关键设计决策：M0 将 Gateway 和 Kernel 合并为单个 FastAPI 进程。**

理由：
1. M0 没有 Orchestrator / Worker / Policy Engine，Kernel 的核心职责尚未就绪
2. 避免进程间通信的额外复杂度（M0 不需要）
3. Gateway API 直接调用 core 包的 Store 层即可
4. 拆分边界通过 packages（core/protocol/observability）保持清晰

演进路径：
- M1: 引入 kernel 模块（同进程，但逻辑独立）
- M1.5: 可考虑拆为独立进程（如需 Worker 隔离）

### 8.4 uv Workspace 配置

```toml
# 根 pyproject.toml
[project]
name = "octoagent"
version = "0.1.0"
description = "Personal AI OS"
requires-python = ">=3.12"

[tool.uv.workspace]
members = [
    "packages/core",
    "packages/protocol",
    "packages/observability",
    "apps/gateway",
]

[tool.uv]
dev-dependencies = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "httpx>=0.27",       # FastAPI TestClient
    "ruff>=0.8",         # linter + formatter
]
```

---

## 9. 依赖库评估矩阵

### 9.1 M0 核心依赖

| 库 | 版本 | 用途 | 许可证 | 维护状态 | Python 3.12 |
|----|------|------|--------|---------|-------------|
| fastapi | ~=0.115 | Web 框架 + API | MIT | 活跃（tiangolo） | 兼容 |
| uvicorn | ~=0.34 | ASGI 服务器 | BSD-3 | 活跃 | 兼容 |
| pydantic | ~=2.10 | 数据模型 + 校验 | MIT | 活跃（Pydantic 团队） | 兼容 |
| aiosqlite | ~=0.21 | 异步 SQLite | MIT | 活跃（omnilib） | 兼容 |
| sse-starlette | ~=3.0 | SSE 事件流 | BSD-3 | 活跃 | 兼容 |
| python-ulid | ~=3.1 | ULID 生成 | MIT | 活跃 | 兼容 |
| logfire | ~=4.24 | 可观测（OTel） | MIT | 活跃（Pydantic 团队） | 兼容 |
| structlog | ~=25.4 | 结构化日志 | Apache-2.0 | 活跃 | 兼容 |
| litellm | ~=1.55 | LLM 客户端（M0 hardcoded） | MIT | 非常活跃 | 兼容 |
| httpx | ~=0.27 | HTTP 客户端 | BSD-3 | 活跃 | 兼容 |

### 9.2 开发/测试依赖

| 库 | 版本 | 用途 |
|----|------|------|
| pytest | ~=8.0 | 测试框架 |
| pytest-asyncio | ~=0.24 | 异步测试支持 |
| ruff | ~=0.8 | Linter + Formatter |

### 9.3 前端依赖

| 库 | 版本 | 用途 |
|----|------|------|
| react | ^19.0 | UI 库 |
| react-dom | ^19.0 | DOM 渲染 |
| vite | ^6.0 | 构建工具 |
| typescript | ^5.6 | 类型系统 |
| @vitejs/plugin-react | ^4.3 | Vite React 插件 |

---

## 10. 设计模式调研

### 10.1 事件溯源模式（Event Sourcing）

**适用性：** 核心模式，blueprint 整体架构基于此。

M0 实现要点：
- **Event Store** 是事实来源（source of truth）
- **Task 表** 是 projection（物化视图），通过 apply_event 从事件流派生
- **写入必须经过 Event Store**，禁止直接修改 Task 表
- **单事务原子性**：写事件 + 更新 projection 在同一 SQLite 事务内

风险与缓解：
- 风险：事件数量增长导致 replay 变慢 -> 缓解：M0 数据量小不成问题，M2+ 引入快照
- 风险：projection 逻辑 bug 导致 Task 状态不一致 -> 缓解：提供 rebuild_projection 工具

### 10.2 Repository 模式

**适用性：** Store 层封装数据库访问。

M0 实现：
- `EventStore`：封装 events 表的 append / query
- `TaskStore`：封装 tasks 表的 CRUD
- `ArtifactStore`：封装文件系统操作 + artifacts 表元数据

### 10.3 发布-订阅模式（Pub/Sub）

**适用性：** SSE 实时推送。

M0 实现：
- 进程内 `EventBus`（asyncio.Queue 实现）
- 写入事件时同时 publish 到 EventBus
- SSE 端点 subscribe 对应 task_id 的队列

### 10.4 中间件模式（Middleware）

**适用性：** 横切关注点（trace_id 注入、请求日志）。

M0 实现：
- FastAPI middleware 注入 request_id / trace_id
- structlog contextvars 绑定

---

## 11. 技术风险清单

| # | 风险 | 概率 | 影响 | 缓解策略 |
|---|------|------|------|---------|
| R1 | SQLite 单写者在 M1+ 多进程场景下成为瓶颈 | 中 | 中 | M0 设计 Store 接口层，M1+ 可替换为 writer queue 或 PostgreSQL |
| R2 | SSE 长连接在 Nginx/CDN 代理后断连 | 中 | 低 | sse-starlette 内置 ping；开发文档注明 proxy buffering 需禁用 |
| R3 | aiosqlite 线程池在高并发下的性能天花板 | 低 | 低 | M0 单用户，并发极低；M1+ 评估是否切换到 aiosqlite 连接池 |
| R4 | Logfire 免费 tier 配额限制 | 低 | 低 | 可设 `send_to_logfire=False` 仅本地日志；structlog 独立于 Logfire 可用 |
| R5 | 前后端 API contract 缺乏自动校验 | 中 | 中 | M0 用 TypeScript 类型手动对齐；M1 可引入 OpenAPI TypeScript codegen |
| R6 | ULID 排序依赖毫秒精度，同毫秒内事件顺序不确定 | 低 | 低 | ULID 规范在同毫秒内使用随机数保证唯一性；可接受 |
| R7 | litellm 直连（无 Proxy）缺少 fallback/限流 | 中 | 低 | M0 为验证阶段，M1 升级到 LiteLLM Proxy 解决 |

---

## 12. 需求-技术对齐度评估

### 12.1 M0 需求覆盖度

| M0 验收标准 | 技术方案覆盖 | 状态 |
|-------------|-------------|------|
| task 创建 -> 事件落盘 -> LLM 调用 -> SSE 推送 端到端通过 | aiosqlite + litellm + sse-starlette | 完整覆盖 |
| 进程重启后 task 状态不丢失（Durability First 验证） | SQLite WAL + 单事务 event+projection | 完整覆盖 |
| artifact 文件可存储、可按 task_id 检索 | FileArtifactStore + SQLite 元数据 | 完整覆盖 |
| Web UI 可展示 task 列表 + 事件时间线 | React + Vite + SSE EventSource | 完整覆盖 |
| 所有日志包含 request_id/trace_id | structlog contextvars + Logfire | 完整覆盖 |

### 12.2 Constitution 合规性

| Constitution 条款 | M0 合规分析 |
|------------------|-------------|
| C1 Durability First | SQLite WAL 持久化 + 事件溯源，进程重启不丢状态 |
| C2 Everything is an Event | 所有操作（创建 task、LLM 调用、产物生成）均产生 Event |
| C6 Degrade Gracefully | Logfire 可降级为纯 structlog 本地日志；LLM 不可用时 task 进入 FAILED |
| C8 Observability is a Feature | structlog + Logfire 全链路 trace；Event Store 可审计 |
| 其余（C3-C5, C7） | M0 不涉及（工具/审批/权限在 M1+）|

### 12.3 技术扩展性评估

| M1+ 需求 | M0 预留的扩展点 |
|----------|----------------|
| M1: LiteLLM Proxy | litellm 客户端 -> 改 base_url 指向 Proxy |
| M1: Pydantic Skill | core models 已用 Pydantic，Skill 可直接集成 |
| M1: Policy Engine | Event type 已预留 APPROVAL_REQUESTED/APPROVED/REJECTED |
| M1.5: Orchestrator + Worker | Store 接口层 -> 支持进程间调用 |
| M1.5: Checkpoint | schema 可加 checkpoints 表，不影响现有表 |
| M2: Telegram | NormalizedMessage 已定义，Gateway 可加 ChannelAdapter |
| M2: Memory | core models 可扩展，LanceDB 独立于 SQLite |

---

## 13. Blueprint 不合理之处与建议

### 13.1 Gateway/Kernel 拆分粒度

**问题：** Blueprint 9.3/9.4 将 Gateway 和 Kernel 定义为独立 app，但 M0 阶段 Kernel 的核心职责（Orchestrator/Policy/Memory）均未就绪。

**建议：** M0 合并为单进程 `apps/gateway`，内部通过模块边界保持逻辑分离。M1.5 引入 Worker 后再评估是否拆分。

### 13.2 events 表的 schema_version 字段

**问题：** Blueprint 8.1.2 定义 `schema_version: 1`，但未说明版本升级时的迁移策略。

**建议：** M0 实现时设 `schema_version=1` 并在 event 写入时强制带上版本号。后续版本升级时，通过读取端兼容多版本（reader 判断 version 分支处理）而非修改历史事件。

### 13.3 Artifact.parts 的 inline 内容阈值

**问题：** Blueprint 8.1.2 提到"小内容 inline，大文件 storage_ref"但未定义阈值。

**建议：** 设定默认阈值 **4KB**。< 4KB 的 text/json part 直接 inline 存入 SQLite payload；>= 4KB 的写入文件系统并记录 `uri: file://...`。此阈值可配置。

### 13.4 M0 里程碑中"最小 LLM 回路"的定位

**问题：** Blueprint M0 列出"最小 LLM 回路：hardcoded model call"，但未说明这与 M1 LiteLLM 集成的关系。

**建议：** M0 使用 litellm 客户端库直连（不部署 Proxy），仅作为端到端验证。M1 升级到 LiteLLM Proxy 时，只需修改 base_url 配置。代码需预留 model alias 配置点。

---

## 14. 推荐技术栈总结

```
+-------------------+------------------------------------------+
| 层              | M0 技术选择                                |
+-------------------+------------------------------------------+
| Web 框架          | FastAPI 0.115 + Uvicorn 0.34              |
| 数据库            | SQLite WAL (aiosqlite 0.21)               |
| 事件 ID           | ULID (python-ulid 3.1)                   |
| SSE              | sse-starlette 3.0                         |
| 数据模型          | Pydantic 2.10                             |
| LLM 客户端        | litellm 1.55 (直连，无 Proxy)             |
| 可观测            | Logfire 4.24 + structlog 25.4             |
| 前端              | React 19 + Vite 6 + TypeScript 5.6       |
| 包管理            | uv workspace                              |
| 测试              | pytest 8 + pytest-asyncio 0.24            |
| Lint/Format      | ruff 0.8                                  |
+-------------------+------------------------------------------+
```

---

## 15. 后续建议

1. **Spec 编写优先级：** 建议先编写 `packages/core` 的 spec（数据模型 + Store 接口），这是 M0 所有其他组件的基础
2. **端到端 Smoke Test：** M0 的第一个集成测试应验证完整链路：POST /api/message -> task 创建 -> event 落盘 -> SSE 推送
3. **数据库迁移策略：** 从 M0 起即使用 schema_migrations 表管理版本，避免后续升级困难
4. **API 文档：** FastAPI 自动生成 OpenAPI spec，M0 应确保所有端点有正确的 response model 定义
5. **前端-后端 Contract：** 考虑从 FastAPI OpenAPI spec 自动生成 TypeScript 类型（M1 引入，M0 手动对齐）
