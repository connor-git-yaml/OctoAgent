# M0 基础底座 -- 数据模型定义

**特性**: 001-implement-m0-foundation
**阶段**: Phase 1 -- 设计与契约
**日期**: 2026-02-28
**依据**: spec.md §4.1, Blueprint §8.1

---

## 1. Pydantic Domain Models

### 1.1 枚举定义

```python
"""packages/core/models/enums.py"""
from enum import StrEnum


class TaskStatus(StrEnum):
    """Task 状态机 -- 对齐 spec FR-M0-DM-2"""
    # M0 活跃状态
    CREATED = "CREATED"
    RUNNING = "RUNNING"

    # M0 终态
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    # M1+ 预留状态（M0 数据模型定义但无消费者）
    QUEUED = "QUEUED"
    WAITING_INPUT = "WAITING_INPUT"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    PAUSED = "PAUSED"
    REJECTED = "REJECTED"


# M0 合法状态流转 -- 对齐 spec FR-M0-DM-2
VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    # 终态不可再流转
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}

TERMINAL_STATES: set[TaskStatus] = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.REJECTED,
}


class EventType(StrEnum):
    """事件类型 -- 对齐 spec FR-M0-DM-3"""
    TASK_CREATED = "TASK_CREATED"
    USER_MESSAGE = "USER_MESSAGE"
    MODEL_CALL_STARTED = "MODEL_CALL_STARTED"
    MODEL_CALL_COMPLETED = "MODEL_CALL_COMPLETED"
    MODEL_CALL_FAILED = "MODEL_CALL_FAILED"
    STATE_TRANSITION = "STATE_TRANSITION"
    ARTIFACT_CREATED = "ARTIFACT_CREATED"
    ERROR = "ERROR"


class ActorType(StrEnum):
    """操作者类型 -- 对齐 Blueprint §8.1.2"""
    USER = "user"
    KERNEL = "kernel"
    WORKER = "worker"
    TOOL = "tool"
    SYSTEM = "system"


class RiskLevel(StrEnum):
    """风险等级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PartType(StrEnum):
    """Artifact Part 类型 -- 对齐 spec FR-M0-DM-4"""
    # M0 支持
    TEXT = "text"
    FILE = "file"
    # M1+ 预留
    JSON = "json"
    IMAGE = "image"
```

### 1.2 Task Model

```python
"""packages/core/models/task.py"""
from datetime import datetime
from pydantic import BaseModel, Field
from .enums import TaskStatus, RiskLevel


class RequesterInfo(BaseModel):
    """请求者信息"""
    channel: str = Field(description="渠道标识")
    sender_id: str = Field(description="发送者 ID")


class TaskPointers(BaseModel):
    """Task 指针信息"""
    latest_event_id: str | None = Field(default=None, description="最新事件 ID")


class Task(BaseModel):
    """Task 数据模型 -- 对齐 spec FR-M0-DM-1, Blueprint §8.1.2

    tasks 表是 events 的物化视图（projection），
    所有状态更新必须通过写入事件触发。
    """
    task_id: str = Field(description="唯一标识，ULID 格式")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="更新时间")
    status: TaskStatus = Field(default=TaskStatus.CREATED, description="当前状态")
    title: str = Field(description="任务标题（消息摘要）")
    thread_id: str = Field(description="线程标识")
    scope_id: str = Field(description="作用域标识")
    requester: RequesterInfo = Field(description="请求者信息")
    risk_level: RiskLevel = Field(default=RiskLevel.LOW, description="风险等级")
    pointers: TaskPointers = Field(default_factory=TaskPointers, description="指针信息")
```

### 1.3 Event Model

```python
"""packages/core/models/event.py"""
from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field
from .enums import EventType, ActorType


class EventCausality(BaseModel):
    """事件因果链信息"""
    parent_event_id: str | None = Field(default=None, description="父事件 ID")
    idempotency_key: str | None = Field(
        default=None,
        description="幂等键，入口操作和带副作用操作必填"
    )


class Event(BaseModel):
    """Event 数据模型 -- 对齐 spec FR-M0-DM-3, Blueprint §8.1.2

    事件表 append-only，不允许更新或删除。
    event_id 使用 ULID 格式，时间有序。
    task_seq 同一 task 内严格单调递增。
    """
    event_id: str = Field(description="唯一标识，ULID 格式，时间有序")
    task_id: str = Field(description="关联的 Task ID")
    task_seq: int = Field(description="任务内序号，严格单调递增")
    ts: datetime = Field(description="事件时间戳")
    type: EventType = Field(description="事件类型")
    schema_version: int = Field(default=1, description="Schema 版本号")
    actor: ActorType = Field(description="操作者")
    payload: dict[str, Any] = Field(default_factory=dict, description="结构化 payload")
    trace_id: str = Field(description="追踪标识，同一 task 共享")
    span_id: str = Field(default="", description="Span 标识")
    causality: EventCausality = Field(
        default_factory=EventCausality,
        description="因果链信息"
    )
```

### 1.4 Artifact Model

```python
"""packages/core/models/artifact.py"""
from datetime import datetime
from pydantic import BaseModel, Field
from .enums import PartType


class ArtifactPart(BaseModel):
    """Artifact Part -- 对齐 A2A Part 规范

    M0 支持 text 和 file 类型。
    """
    type: PartType = Field(description="Part 类型")
    mime: str = Field(default="text/plain", description="MIME 类型")
    content: str | None = Field(
        default=None,
        description="inline 内容（小于 4KB 的文本）"
    )
    uri: str | None = Field(
        default=None,
        description="文件引用 URI（大文件）"
    )


class Artifact(BaseModel):
    """Artifact 数据模型 -- 对齐 spec FR-M0-DM-4, Blueprint §8.1.2

    采用 A2A 兼容的 parts 多部分结构。
    hash 和 size 用于完整性校验。
    """
    artifact_id: str = Field(description="唯一标识，ULID 格式")
    task_id: str = Field(description="关联的 Task ID")
    ts: datetime = Field(description="创建时间戳")
    name: str = Field(description="产物名称")
    description: str = Field(default="", description="产物描述")
    parts: list[ArtifactPart] = Field(default_factory=list, description="Parts 数组")
    storage_ref: str | None = Field(
        default=None,
        description="存储引用路径"
    )
    size: int = Field(default=0, description="内容大小（字节）")
    hash: str = Field(default="", description="SHA-256 哈希")
    version: int = Field(default=1, description="版本号")
```

### 1.5 NormalizedMessage Model

```python
"""packages/core/models/message.py"""
from datetime import datetime
from pydantic import BaseModel, Field


class MessageAttachment(BaseModel):
    """消息附件"""
    id: str = Field(description="附件 ID")
    mime: str = Field(description="MIME 类型")
    filename: str = Field(default="", description="文件名")
    size: int = Field(default=0, description="文件大小")
    storage_ref: str = Field(default="", description="存储引用")


class NormalizedMessage(BaseModel):
    """NormalizedMessage -- 对齐 spec FR-M0-DM-5, Blueprint §8.1.1

    消息入站的统一格式。M0 仅支持 "web" 渠道。
    """
    channel: str = Field(default="web", description="渠道标识，M0 仅 web")
    thread_id: str = Field(default="default", description="线程标识")
    scope_id: str = Field(default="", description="作用域标识")
    sender_id: str = Field(default="owner", description="发送者 ID")
    sender_name: str = Field(default="Owner", description="发送者名称")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="时间戳")
    text: str = Field(description="文本内容")
    attachments: list[MessageAttachment] = Field(
        default_factory=list,
        description="附件列表"
    )
    idempotency_key: str = Field(description="幂等键，必填")
```

### 1.6 Event Payload 子类型

```python
"""packages/core/models/payloads.py"""
from pydantic import BaseModel, Field
from .enums import TaskStatus


class TaskCreatedPayload(BaseModel):
    """TASK_CREATED 事件 payload"""
    title: str
    thread_id: str
    scope_id: str
    channel: str
    sender_id: str


class UserMessagePayload(BaseModel):
    """USER_MESSAGE 事件 payload"""
    text_preview: str = Field(description="消息预览（截断到 200 字符）")
    text_length: int = Field(description="原始文本长度")
    attachment_count: int = Field(default=0)


class ModelCallStartedPayload(BaseModel):
    """MODEL_CALL_STARTED 事件 payload"""
    model_alias: str = Field(description="模型别名")
    request_summary: str = Field(description="请求摘要")
    artifact_ref: str | None = Field(default=None, description="完整请求的 Artifact 引用")


class ModelCallCompletedPayload(BaseModel):
    """MODEL_CALL_COMPLETED 事件 payload"""
    model_alias: str
    response_summary: str = Field(description="响应摘要")
    duration_ms: int = Field(description="调用耗时（毫秒）")
    token_usage: dict[str, int] = Field(
        default_factory=dict,
        description="Token 用量 {prompt, completion, total}"
    )
    artifact_ref: str | None = Field(default=None, description="完整响应的 Artifact 引用")


class ModelCallFailedPayload(BaseModel):
    """MODEL_CALL_FAILED 事件 payload"""
    model_alias: str
    error_type: str
    error_message: str
    duration_ms: int


class StateTransitionPayload(BaseModel):
    """STATE_TRANSITION 事件 payload"""
    from_status: TaskStatus
    to_status: TaskStatus
    reason: str = Field(default="")


class ArtifactCreatedPayload(BaseModel):
    """ARTIFACT_CREATED 事件 payload"""
    artifact_id: str
    name: str
    size: int
    part_count: int


class ErrorPayload(BaseModel):
    """ERROR 事件 payload"""
    error_type: str = Field(description="错误分类：model/tool/system/business")
    error_message: str
    recoverable: bool = Field(default=False)
    recovery_hint: str = Field(default="")
```

---

## 2. SQLite DDL

### 2.1 tasks 表

```sql
-- tasks 表：events 的物化视图（projection）
-- 对齐 spec FR-M0-DM-1, Blueprint §8.2.2
CREATE TABLE IF NOT EXISTS tasks (
    task_id     TEXT PRIMARY KEY,                    -- ULID 格式
    created_at  TEXT NOT NULL,                        -- ISO 8601
    updated_at  TEXT NOT NULL,                        -- ISO 8601
    status      TEXT NOT NULL DEFAULT 'CREATED',      -- TaskStatus 枚举值
    title       TEXT NOT NULL DEFAULT '',
    thread_id   TEXT NOT NULL DEFAULT 'default',
    scope_id    TEXT NOT NULL DEFAULT '',
    requester   TEXT NOT NULL DEFAULT '{}',           -- JSON: RequesterInfo
    risk_level  TEXT NOT NULL DEFAULT 'low',
    pointers    TEXT NOT NULL DEFAULT '{}'            -- JSON: TaskPointers
);

-- 查询索引
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_thread_id ON tasks(thread_id);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC);
```

### 2.2 events 表

```sql
-- events 表：append-only 事件存储
-- 对齐 spec FR-M0-DM-3, FR-M0-ES-1, Blueprint §8.2.2
CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,                 -- ULID 格式，时间有序
    task_id         TEXT NOT NULL,                    -- FK → tasks.task_id
    task_seq        INTEGER NOT NULL,                 -- 同一 task 内严格单调递增
    ts              TEXT NOT NULL,                    -- ISO 8601
    type            TEXT NOT NULL,                    -- EventType 枚举值
    schema_version  INTEGER NOT NULL DEFAULT 1,
    actor           TEXT NOT NULL,                    -- ActorType 枚举值
    payload         TEXT NOT NULL DEFAULT '{}',       -- JSON
    trace_id        TEXT NOT NULL DEFAULT '',
    span_id         TEXT NOT NULL DEFAULT '',
    parent_event_id TEXT,                             -- 父事件 ID（可选）
    idempotency_key TEXT,                             -- 幂等键（入口操作必填）

    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

-- 任务内事件序号唯一约束（确保 task_seq 严格单调递增）
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_task_seq
    ON events(task_id, task_seq);

-- 任务内事件时间排序索引
CREATE INDEX IF NOT EXISTS idx_events_task_ts
    ON events(task_id, ts);

-- 幂等键唯一约束（仅对非 NULL 值生效）
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_idempotency_key
    ON events(idempotency_key) WHERE idempotency_key IS NOT NULL;
```

### 2.3 artifacts 表

```sql
-- artifacts 表：产物元数据
-- 对齐 spec FR-M0-DM-4, FR-M0-AS-2, Blueprint §8.2.2
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id  TEXT PRIMARY KEY,                    -- ULID 格式
    task_id      TEXT NOT NULL,                       -- FK → tasks.task_id
    ts           TEXT NOT NULL,                       -- ISO 8601
    name         TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    parts        TEXT NOT NULL DEFAULT '[]',          -- JSON: list[ArtifactPart]
    storage_ref  TEXT,                                -- 文件系统路径（大文件）
    size         INTEGER NOT NULL DEFAULT 0,          -- 字节大小
    hash         TEXT NOT NULL DEFAULT '',            -- SHA-256
    version      INTEGER NOT NULL DEFAULT 1,

    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

-- 按任务查询产物
CREATE INDEX IF NOT EXISTS idx_artifacts_task_id
    ON artifacts(task_id);
```

### 2.4 数据库初始化

```sql
-- 数据库初始化 pragma
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
```

---

## 3. Store 接口定义

```python
"""packages/core/store/protocols.py"""
from typing import Protocol
from ..models.task import Task
from ..models.event import Event
from ..models.artifact import Artifact


class TaskStore(Protocol):
    """Task 存储接口 -- 对齐 Blueprint §9.2"""

    async def create_task(self, task: Task) -> None: ...
    async def get_task(self, task_id: str) -> Task | None: ...
    async def list_tasks(self, status: str | None = None) -> list[Task]: ...
    async def update_task_status(
        self, task_id: str, status: str, updated_at: str, latest_event_id: str
    ) -> None: ...


class EventStore(Protocol):
    """Event 存储接口 -- 对齐 Blueprint §9.2

    事件表 append-only：只允许插入，不允许更新或删除。
    """

    async def append_event(self, event: Event) -> None: ...
    async def get_events_for_task(self, task_id: str) -> list[Event]: ...
    async def get_events_after(
        self, task_id: str, after_event_id: str
    ) -> list[Event]: ...
    async def get_next_task_seq(self, task_id: str) -> int: ...
    async def check_idempotency_key(self, key: str) -> str | None: ...


class ArtifactStore(Protocol):
    """Artifact 存储接口 -- 对齐 Blueprint §9.2"""

    async def put_artifact(self, artifact: Artifact, content: bytes | None = None) -> None: ...
    async def get_artifact(self, artifact_id: str) -> Artifact | None: ...
    async def list_artifacts_for_task(self, task_id: str) -> list[Artifact]: ...
    async def get_artifact_content(self, artifact_id: str) -> bytes | None: ...
```

---

## 4. 数据模型约束总结

| 约束 | 实现方式 | 对齐 |
|------|---------|------|
| event_id 时间有序 | ULID 格式 | FR-M0-ES-3 |
| events append-only | 应用层禁止 UPDATE/DELETE | FR-M0-ES-1 |
| 事件+projection 事务一致 | 同一 SQLite 事务内 | FR-M0-ES-2 |
| task_seq 严格单调递增 | UNIQUE INDEX + 事务内 MAX+1 | FR-M0-ES-5 |
| idempotency_key 去重 | UNIQUE INDEX WHERE NOT NULL | FR-M0-API-1, EC-1 |
| Artifact SHA-256 校验 | 写入时计算 hash + size | FR-M0-AS-4 |
| inline 阈值 4KB | 应用层判断 content 大小 | FR-M0-AS-3 |
| Task 终态不可流转 | VALID_TRANSITIONS 映射 | FR-M0-DM-2 |
| M1+ 状态预留 | TaskStatus 枚举包含全部状态 | FR-M0-DM-2 |
