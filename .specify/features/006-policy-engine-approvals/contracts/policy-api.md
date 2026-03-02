# 接口契约: Feature 006 — Policy Engine + Approvals API

**Feature Branch**: `feat/006-policy-engine-approvals`
**日期**: 2026-03-02
**状态**: Draft
**消费方**: 前端 Approvals 面板, Chat UI, Feature 007 端到端集成
**依赖**: Feature 004 契约（ToolBrokerProtocol, BeforeHook, PolicyCheckpoint, CheckResult）

---

## 1. REST API 契约

### 1.1 GET /api/approvals — 获取待审批列表

**对齐 FR**: FR-018

```
GET /api/approvals
Content-Type: application/json
```

**Response 200**:

```json
{
  "approvals": [
    {
      "approval_id": "550e8400-e29b-41d4-a716-446655440000",
      "task_id": "task-abc-123",
      "tool_name": "shell_exec",
      "tool_args_summary": "command: rm -rf /tmp/build-***",
      "risk_explanation": "irreversible shell command execution",
      "policy_label": "global.irreversible",
      "side_effect_level": "irreversible",
      "remaining_seconds": 95.3,
      "created_at": "2026-03-02T10:30:00Z"
    }
  ],
  "total": 1
}
```

**Response Schema**:

```python
class ApprovalsListResponse(BaseModel):
    approvals: list[ApprovalListItem]
    total: int
```

**行为约定**:
- 仅返回 `status=PENDING` 的审批请求
- `remaining_seconds` 由服务端实时计算（expires_at - now）
- 按 `created_at` 升序排列（最早的在前）
- 空列表返回 `{"approvals": [], "total": 0}`

---

### 1.2 POST /api/approve/{approval_id} — 提交审批决策

**对齐 FR**: FR-019

```
POST /api/approve/{approval_id}
Content-Type: application/json

{
  "decision": "allow-once"
}
```

**Path Parameters**:
- `approval_id` (string, required): 审批请求 ID

**Request Body**:

```python
class ApprovalResolveRequest(BaseModel):
    decision: ApprovalDecision  # "allow-once" | "allow-always" | "deny"
```

**Response 200 (成功)**:

```json
{
  "success": true,
  "approval_id": "550e8400-e29b-41d4-a716-446655440000",
  "decision": "allow-once",
  "message": "Approval resolved successfully"
}
```

**Response 404 (审批不存在)**:

```json
{
  "success": false,
  "error": "approval_not_found",
  "message": "Approval '550e8400-...' not found"
}
```

**Response 409 (审批已处理)**:

```json
{
  "success": false,
  "error": "approval_already_resolved",
  "message": "Approval '550e8400-...' has already been resolved",
  "current_status": "approved"
}
```

**Response 422 (无效决策)**:

```json
{
  "success": false,
  "error": "invalid_decision",
  "message": "Decision must be one of: allow-once, allow-always, deny"
}
```

**Response Schema**:

```python
class ApprovalResolveResponse(BaseModel):
    success: bool
    approval_id: str | None = None
    decision: str | None = None
    message: str
    error: str | None = None
    current_status: str | None = None
```

**行为约定**:
- 对已解决的审批返回 409 Conflict
- 对不存在（含已过宽限期）的审批返回 404 Not Found
- 决策值必须为 `allow-once` / `allow-always` / `deny` 之一
- 成功后触发:
  1. ApprovalManager.resolve() 更新内存状态
  2. Event Store 写入 APPROVAL_APPROVED 或 APPROVAL_REJECTED 事件
  3. SSE 推送 `approval:resolved` 事件
  4. asyncio.Event.set() 唤醒等待方

---

### 1.3 POST /api/chat/send — 发送聊天消息

**对齐 FR**: FR-023

```
POST /api/chat/send
Content-Type: application/json

{
  "message": "请帮我创建一个 Python 项目",
  "task_id": null
}
```

**Request Body**:

```python
class ChatSendRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    task_id: str | None = Field(
        default=None,
        description="关联的 Task ID（续对话时传入，新对话为 null）"
    )
```

**Response 200**:

```json
{
  "task_id": "task-xyz-789",
  "status": "accepted",
  "stream_url": "/stream/task/task-xyz-789"
}
```

**Response Schema**:

```python
class ChatSendResponse(BaseModel):
    task_id: str
    status: str = "accepted"
    stream_url: str
```

**行为约定**:
- 创建新 Task（或复用已有 task_id）
- 返回 SSE 流 URL，前端使用 EventSource 连接获取流式输出
- task_id=null 时创建新对话，非 null 时续对话

---

### 1.4 GET /stream/task/{task_id} — SSE 任务事件流

**对齐 FR**: FR-022, FR-024

```
GET /stream/task/{task_id}
Accept: text/event-stream
```

**SSE 事件类型**:

| event type | 触发场景 | data 格式 |
|------------|---------|----------|
| `message:chunk` | Agent 流式回复的一个 token 块 | `{"content": "你好", "is_final": false}` |
| `message:complete` | Agent 回复完成 | `{"content": "完整回复", "is_final": true}` |
| `approval:requested` | 新审批请求产生 | `ApprovalRequestedEventPayload` JSON |
| `approval:resolved` | 审批已决策 | `ApprovalResolvedEventPayload` JSON |
| `approval:expired` | 审批已过期 | `ApprovalExpiredEventPayload` JSON |
| `task:status_changed` | Task 状态变更 | `{"task_id": "...", "old_status": "...", "new_status": "..."}` |
| `error` | 处理错误 | `{"error": "...", "message": "..."}` |

**SSE 格式**:

```
event: message:chunk
data: {"content": "你", "is_final": false}

event: message:chunk
data: {"content": "你好", "is_final": false}

event: approval:requested
data: {"approval_id": "...", "tool_name": "shell_exec", ...}

event: message:complete
data: {"content": "完整回复内容", "is_final": true}
```

**行为约定**:
- 使用 sse-starlette 库实现
- 支持 `Last-Event-ID` 断点续传（FR-022 SSE 断线重连）
- 心跳间隔 15 秒（防止连接超时）
- Task 不存在时返回 404

---

## 2. PolicyEngine 内部接口

### 2.1 PolicyPipeline（纯函数）

```python
def evaluate_pipeline(
    steps: list[PolicyStep],
    tool_meta: ToolMeta,
    params: dict[str, Any],
    context: ExecutionContext,
) -> tuple[PolicyDecision, list[PolicyDecision]]:
    """评估策略管道

    纯函数，无副作用。逐层评估，取最严格决策。

    Args:
        steps: 策略步骤列表（按评估顺序）
        tool_meta: 工具元数据
        params: 工具调用参数
        context: 执行上下文

    Returns:
        (final_decision, trace): 最终决策 + 各层评估结果链

    行为约定:
        - 遇到 deny 立即短路返回（D10）
        - 后续层只能收紧不能放松（FR-003）
        - 每层评估结果附带 label（FR-002）
        - 空 steps 列表返回默认 allow
    """
    ...
```

### 2.2 ApprovalManager

```python
class ApprovalManager:
    """Two-Phase Approval 管理器

    管理审批请求的注册、等待、解决和消费。
    内存状态 + Event Store 双写。

    对齐 FR: FR-007, FR-008, FR-009, FR-010, FR-011
    """

    def __init__(
        self,
        event_store: "EventStoreProtocol",
        sse_broadcaster: "SSEBroadcasterProtocol | None" = None,
        default_timeout_s: float = 120.0,
        grace_period_s: float = 15.0,
    ) -> None: ...

    async def register(self, request: ApprovalRequest) -> ApprovalRecord:
        """Phase 1: 幂等注册审批请求

        Args:
            request: 审批请求

        Returns:
            ApprovalRecord（新建或已有）

        行为约定:
            - 同一 approval_id 重复注册返回已有 record（幂等）
            - 注册时检查 allow-always 白名单，命中则直接返回 APPROVED record
            - 写入 APPROVAL_REQUESTED 事件到 Event Store
            - 推送 SSE 'approval:requested' 事件
            - 启动超时定时器（call_later）
        """
        ...

    async def wait_for_decision(
        self, approval_id: str, timeout_s: float | None = None
    ) -> ApprovalDecision | None:
        """Phase 2: 异步等待用户决策

        Args:
            approval_id: 审批 ID
            timeout_s: 等待超时（覆盖默认值）

        Returns:
            用户决策，超时返回 None

        行为约定:
            - 使用 asyncio.Event.wait() + asyncio.wait_for()
            - 审批已解决时立即返回（宽限期内）
            - 审批不存在时返回 None
        """
        ...

    def resolve(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        resolved_by: str = "user:web",
    ) -> bool:
        """解决审批请求

        Args:
            approval_id: 审批 ID
            decision: 用户决策
            resolved_by: 解决者标识

        Returns:
            True 成功，False 审批不存在或已解决

        行为约定:
            - 原子操作：检查 + 更新 + 通知
            - 取消超时定时器
            - 写入 APPROVAL_APPROVED 或 APPROVAL_REJECTED 事件
            - 设置 asyncio.Event（唤醒等待方）
            - 推送 SSE 事件
            - allow-always 时将 tool_name 加入内存白名单
            - 宽限期后清理 pending 记录
        """
        ...

    def consume_allow_once(self, approval_id: str) -> bool:
        """原子消费一次性审批令牌

        Args:
            approval_id: 审批 ID

        Returns:
            True 成功消费，False 审批不是 allow-once 或已消费

        行为约定:
            - 仅对 decision=ALLOW_ONCE 且 consumed=False 的记录有效
            - 消费后设置 consumed=True，防止重放
        """
        ...

    async def recover_from_store(self) -> int:
        """启动恢复: 从 Event Store 恢复未完成的审批

        Returns:
            恢复的 pending 审批数量

        行为约定:
            - 扫描 APPROVAL_REQUESTED 事件中无配对 APPROVED/REJECTED/EXPIRED 的记录
            - 检查是否已过期（expires_at < now），过期的直接标记 EXPIRED
            - 未过期的重建 PendingApproval（含新的 asyncio.Event 和超时定时器）
        """
        ...
```

### 2.3 PolicyCheckHook

```python
class PolicyCheckHook:
    """PolicyCheckpoint 的 BeforeHook 适配器

    将 PolicyPipeline 的决策映射为 Feature 004 的 BeforeHookResult。
    对 ask 决策在 hook 内部完成审批等待。

    对齐 FR: FR-015, FR-016, FR-017
    """

    @property
    def name(self) -> str:
        return "policy_checkpoint"

    @property
    def priority(self) -> int:
        return 0  # 最高优先级

    @property
    def fail_mode(self) -> FailMode:
        return FailMode.CLOSED  # 强制 fail-closed

    async def before_execute(
        self,
        tool_meta: ToolMeta,
        args: dict[str, Any],
        context: ExecutionContext,
    ) -> BeforeHookResult:
        """执行策略评估 + 审批等待

        流程:
            1. evaluate_pipeline() 获取 PolicyDecision
            2. 写入 POLICY_DECISION 事件
            3. 根据 action 映射:
               - allow -> BeforeHookResult(proceed=True)
               - deny  -> BeforeHookResult(proceed=False, rejection_reason=...)
               - ask   -> register() + wait_for_decision() + 映射为 proceed=True/False
            4. 异常 -> BeforeHookResult(proceed=False)（fail_mode=closed）

        Returns:
            BeforeHookResult
        """
        ...
```

### 2.4 PolicyEngine（门面类）

```python
class PolicyEngine:
    """Policy Engine 门面

    组合 PolicyPipeline + ApprovalManager + PolicyCheckHook，
    提供统一的初始化和配置接口。

    对齐 FR: FR-001 ~ FR-017
    """

    def __init__(
        self,
        event_store: "EventStoreProtocol",
        profile: PolicyProfile | None = None,
        sse_broadcaster: "SSEBroadcasterProtocol | None" = None,
    ) -> None: ...

    @property
    def hook(self) -> PolicyCheckHook:
        """返回可注册到 ToolBroker 的 BeforeHook 实例"""
        ...

    @property
    def approval_manager(self) -> ApprovalManager:
        """返回 ApprovalManager 实例（供 REST API 使用）"""
        ...

    async def startup(self) -> None:
        """启动恢复: 从 Event Store 恢复未完成的审批"""
        ...

    def update_profile(self, profile: PolicyProfile) -> None:
        """更新策略 Profile（写入 POLICY_DECISION 变更事件）"""
        ...
```

---

## 3. Evaluator 函数签名

### 3.1 Layer 1: Profile 过滤

```python
def profile_filter(
    tool_meta: ToolMeta,
    params: dict[str, Any],
    context: ExecutionContext,
    *,
    allowed_profile: ToolProfile = ToolProfile.STANDARD,
) -> PolicyDecision:
    """Layer 1: 根据 ToolProfile 过滤工具

    Args:
        tool_meta: 工具元数据
        params: 调用参数（此层不使用）
        context: 执行上下文
        allowed_profile: 当前允许的最高工具级别

    Returns:
        PolicyDecision(action=allow/deny, label="tools.profile")

    行为约定:
        - tool_meta.tool_profile > allowed_profile -> deny
        - 否则 -> allow
        - 防御性校验: 如果 deny 会导致所有核心工具被排除，发出警告（EC-7）

    对齐 FR: FR-004
    """
    ...
```

### 3.2 Layer 2: Global 规则

```python
def global_rule(
    tool_meta: ToolMeta,
    params: dict[str, Any],
    context: ExecutionContext,
    *,
    profile: PolicyProfile | None = None,
) -> PolicyDecision:
    """Layer 2: 基于 SideEffectLevel 的全局规则

    Args:
        tool_meta: 工具元数据
        params: 调用参数（此层不使用）
        context: 执行上下文
        profile: 策略配置档案（决定各级别的默认动作）

    Returns:
        PolicyDecision(action=allow/ask/deny, label="global.<detail>")

    行为约定:
        - side_effect_level=none -> profile.none_action (默认 allow)
        - side_effect_level=reversible -> profile.reversible_action (默认 allow)
        - side_effect_level=irreversible -> profile.irreversible_action (默认 ask)
        - label 格式: "global.readonly", "global.reversible", "global.irreversible"

    对齐 FR: FR-005
    """
    ...
```

---

## 4. Protocol 依赖（Feature 004 锁定）

以下 Protocol 和类型直接复用 Feature 004 契约，不在 Feature 006 中重新定义:

| 类型 | 来源 | 用途 |
|------|------|------|
| `ToolMeta` | packages/tooling | Pipeline 评估输入 |
| `SideEffectLevel` | packages/tooling | 决策依据 |
| `ToolProfile` | packages/tooling | Profile 过滤 |
| `ExecutionContext` | packages/tooling | 上下文传递 |
| `CheckResult` | packages/tooling | PolicyCheckpoint 返回值 |
| `BeforeHook` | packages/tooling | PolicyCheckHook 实现此 Protocol |
| `BeforeHookResult` | packages/tooling | hook 返回值 |
| `FailMode` | packages/tooling | hook fail_mode |
| `ToolBrokerProtocol` | packages/tooling | add_hook() 注册点 |

---

## 5. EventStoreProtocol 依赖

Feature 006 依赖 `packages/core` 的 Event Store 接口:

```python
class EventStoreProtocol(Protocol):
    """Event Store 最小接口（Feature 006 依赖）"""

    async def append(self, event: Event) -> None:
        """追加事件"""
        ...

    async def query_by_task(self, task_id: str) -> list[Event]:
        """按 Task ID 查询事件"""
        ...

    async def query_by_type(
        self,
        event_type: EventType,
        since: datetime | None = None,
    ) -> list[Event]:
        """按事件类型查询"""
        ...
```

---

## 6. SSEBroadcasterProtocol 依赖

Feature 006 依赖 `apps/gateway` 的 SSE 广播接口:

```python
class SSEBroadcasterProtocol(Protocol):
    """SSE 广播器最小接口（Feature 006 依赖）"""

    async def broadcast(
        self,
        event_type: str,
        data: dict,
        task_id: str | None = None,
    ) -> None:
        """广播 SSE 事件

        Args:
            event_type: SSE event 字段值（如 'approval:requested'）
            data: JSON 数据
            task_id: 关联的 task（用于 per-task 流过滤）
        """
        ...
```

---

## 7. FR 覆盖追踪

| API / 接口 | 覆盖 FR |
|------------|---------|
| GET /api/approvals | FR-018 |
| POST /api/approve/{id} | FR-019 |
| POST /api/chat/send | FR-023 |
| GET /stream/task/{id} | FR-022, FR-024 |
| evaluate_pipeline() | FR-001, FR-002, FR-003 |
| profile_filter() | FR-004 |
| global_rule() | FR-005 |
| ApprovalManager.register() | FR-007, FR-011 |
| ApprovalManager.wait_for_decision() | FR-007 |
| ApprovalManager.resolve() | FR-008, FR-009 |
| ApprovalManager.consume_allow_once() | FR-008 |
| ApprovalManager.recover_from_store() | FR-011 |
| PolicyCheckHook.before_execute() | FR-015, FR-016 |
| PolicyEngine.hook | FR-017 |
