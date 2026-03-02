# 快速上手指南: Feature 006 — Policy Engine + Approvals + Chat UI

**Feature Branch**: `feat/006-policy-engine-approvals`
**日期**: 2026-03-02
**前置条件**: Feature 004 契约已锁定（可使用 Mock 实现）

---

## 1. 环境准备

```bash
# 切换到 Feature 分支
git checkout feat/006-policy-engine-approvals

# 安装依赖（使用 uv）
uv sync
```

---

## 2. 最小可运行示例

### 2.1 纯策略评估（无审批）

```python
"""
演示 PolicyPipeline 纯函数评估。
不涉及审批流，仅展示策略决策。
"""
from packages.policy.models import PolicyDecision, PolicyAction, PolicyStep, PolicyProfile
from packages.policy.pipeline import evaluate_pipeline
from packages.policy.evaluators.profile_filter import profile_filter
from packages.policy.evaluators.global_rule import global_rule
from packages.tooling.models import ToolMeta, SideEffectLevel, ToolProfile, ExecutionContext

# 1. 定义工具元数据（mock）
readonly_tool = ToolMeta(
    name="get_time",
    description="获取当前时间",
    parameters_json_schema={"type": "object", "properties": {}},
    side_effect_level=SideEffectLevel.NONE,
    tool_profile=ToolProfile.MINIMAL,
    tool_group="system",
)

dangerous_tool = ToolMeta(
    name="shell_exec",
    description="执行 shell 命令",
    parameters_json_schema={"type": "object", "properties": {"command": {"type": "string"}}},
    side_effect_level=SideEffectLevel.IRREVERSIBLE,
    tool_profile=ToolProfile.STANDARD,
    tool_group="system",
)

# 2. 构建 Pipeline（M1: 2 层）
profile = PolicyProfile(name="default")
steps = [
    PolicyStep(
        evaluator=lambda tm, p, ctx: profile_filter(tm, p, ctx, allowed_profile=profile.allowed_tool_profile),
        label="tools.profile",
    ),
    PolicyStep(
        evaluator=lambda tm, p, ctx: global_rule(tm, p, ctx, profile=profile),
        label="global",
    ),
]

# 3. 评估
context = ExecutionContext(task_id="task-001", trace_id="trace-001")

decision_safe, trace_safe = evaluate_pipeline(steps, readonly_tool, {}, context)
print(f"只读工具: action={decision_safe.action}, label={decision_safe.label}")
# 输出: 只读工具: action=allow, label=global.readonly

decision_danger, trace_danger = evaluate_pipeline(steps, dangerous_tool, {"command": "rm -rf /"}, context)
print(f"危险工具: action={decision_danger.action}, label={decision_danger.label}")
# 输出: 危险工具: action=ask, label=global.irreversible
```

### 2.2 完整审批流程（含 ApprovalManager）

```python
"""
演示 Two-Phase Approval 完整流程。
需要异步环境（asyncio）。
"""
import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

from packages.policy.models import ApprovalRequest, ApprovalDecision
from packages.policy.approval_manager import ApprovalManager


async def demo_approval():
    # 1. 初始化（使用 mock Event Store）
    manager = ApprovalManager(
        event_store=MockEventStore(),
        default_timeout_s=120.0,
        grace_period_s=15.0,
    )

    # 2. Phase 1: 注册审批请求
    request = ApprovalRequest(
        approval_id=str(uuid4()),
        task_id="task-001",
        tool_name="shell_exec",
        tool_args_summary="command: rm -rf /tmp/build-***",
        risk_explanation="irreversible shell command execution",
        policy_label="global.irreversible",
        side_effect_level="irreversible",
        expires_at=datetime.utcnow() + timedelta(seconds=120),
    )
    record = await manager.register(request)
    print(f"审批已注册: {record.request.approval_id}, status={record.status}")

    # 3. 模拟用户批准（另一个异步任务）
    async def simulate_user_approve():
        await asyncio.sleep(2)  # 模拟用户 2 秒后审批
        success = manager.resolve(
            request.approval_id,
            ApprovalDecision.ALLOW_ONCE,
            resolved_by="user:web",
        )
        print(f"用户审批: success={success}")

    # 4. Phase 2: 等待决策
    approve_task = asyncio.create_task(simulate_user_approve())
    decision = await manager.wait_for_decision(request.approval_id)
    print(f"审批结果: {decision}")
    # 输出: 审批结果: allow-once

    # 5. 消费一次性令牌
    consumed = manager.consume_allow_once(request.approval_id)
    print(f"令牌消费: {consumed}")
    # 输出: 令牌消费: True

    # 6. 再次消费（应失败）
    consumed_again = manager.consume_allow_once(request.approval_id)
    print(f"重复消费: {consumed_again}")
    # 输出: 重复消费: False

    await approve_task


asyncio.run(demo_approval())
```

### 2.3 PolicyCheckHook 集成示例

```python
"""
演示 PolicyCheckHook 注册到 ToolBroker。
"""
from packages.policy.policy_engine import PolicyEngine
from packages.policy.models import PolicyProfile
# Feature 004 mock（并行开发期间使用）
from packages.tooling.mock import MockToolBroker

async def demo_integration():
    # 1. 初始化 PolicyEngine
    engine = PolicyEngine(
        event_store=MockEventStore(),
        profile=PolicyProfile(name="default"),
    )
    await engine.startup()  # 恢复未完成审批

    # 2. 注册 PolicyCheckHook 到 ToolBroker
    broker = MockToolBroker()
    broker.add_hook(engine.hook)

    # 3. 执行工具调用（Pipeline + 审批自动触发）
    context = ExecutionContext(task_id="task-001", trace_id="trace-001")
    result = await broker.execute("shell_exec", {"command": "ls"}, context)
    print(f"执行结果: {result}")
```

---

## 3. 前端开发指南

### 3.1 Approvals 面板

```tsx
// frontend/src/components/ApprovalPanel/ApprovalPanel.tsx
import { useApprovals } from '../../hooks/useApprovals';

export function ApprovalPanel() {
  const { approvals, approve, deny, loading } = useApprovals();

  if (loading) return <div>Loading...</div>;
  if (approvals.length === 0) return <div>No pending approvals</div>;

  return (
    <div className="approval-panel">
      {approvals.map((item) => (
        <div key={item.approval_id} className="approval-card">
          <h3>{item.tool_name}</h3>
          <p>{item.tool_args_summary}</p>
          <p className="risk">{item.risk_explanation}</p>
          <p className="timer">Expires in: {Math.round(item.remaining_seconds)}s</p>

          <div className="actions">
            <button onClick={() => approve(item.approval_id, 'allow-once')}>
              Allow Once
            </button>
            <button onClick={() => approve(item.approval_id, 'allow-always')}>
              Always Allow
            </button>
            <button onClick={() => deny(item.approval_id)}>
              Deny
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
```

### 3.2 useApprovals Hook

```tsx
// frontend/src/hooks/useApprovals.ts
import { useState, useEffect, useCallback } from 'react';

const API_BASE = '/api';
const POLL_INTERVAL = 30_000; // 30s 轮询兜底

export function useApprovals() {
  const [approvals, setApprovals] = useState([]);
  const [loading, setLoading] = useState(true);

  // 获取审批列表
  const fetchApprovals = useCallback(async () => {
    const res = await fetch(`${API_BASE}/approvals`);
    const data = await res.json();
    setApprovals(data.approvals);
    setLoading(false);
  }, []);

  // SSE 实时更新 + 轮询兜底
  useEffect(() => {
    fetchApprovals();

    // SSE 连接（如果支持）
    const es = new EventSource(`${API_BASE}/stream/approvals`);
    es.addEventListener('approval:requested', () => fetchApprovals());
    es.addEventListener('approval:resolved', () => fetchApprovals());
    es.addEventListener('approval:expired', () => fetchApprovals());
    es.onerror = () => {
      // SSE 断线，启动轮询兜底
      es.close();
    };

    // 轮询兜底
    const timer = setInterval(fetchApprovals, POLL_INTERVAL);

    return () => {
      es.close();
      clearInterval(timer);
    };
  }, [fetchApprovals]);

  // 审批操作
  const approve = async (id: string, decision: string) => {
    await fetch(`${API_BASE}/approve/${id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    });
    fetchApprovals(); // 刷新列表
  };

  const deny = async (id: string) => {
    await approve(id, 'deny');
  };

  return { approvals, approve, deny, loading };
}
```

### 3.3 Chat UI（SSE 流式）

```tsx
// frontend/src/components/ChatUI/ChatUI.tsx
import { useChatStream } from '../../hooks/useChatStream';

export function ChatUI() {
  const { messages, streamingContent, sendMessage } = useChatStream();
  const [input, setInput] = useState('');

  const handleSend = () => {
    if (input.trim()) {
      sendMessage(input.trim());
      setInput('');
    }
  };

  return (
    <div className="chat-ui">
      <div className="messages">
        {messages.map((msg, i) => (
          <div key={i} className={`message ${msg.role}`}>
            {msg.content}
          </div>
        ))}
        {streamingContent && (
          <div className="message assistant streaming">
            {streamingContent}
          </div>
        )}
      </div>
      <div className="input-area">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSend()}
          placeholder="输入消息..."
        />
        <button onClick={handleSend}>发送</button>
      </div>
    </div>
  );
}
```

---

## 4. 测试指南

### 4.1 单元测试: Pipeline 纯函数

```python
# tests/unit/policy/test_pipeline.py
import pytest
from packages.policy.pipeline import evaluate_pipeline
from packages.policy.models import PolicyStep, PolicyDecision, PolicyAction

def test_empty_pipeline_returns_allow():
    """空 Pipeline 返回默认 allow"""
    decision, trace = evaluate_pipeline([], mock_tool_meta, {}, mock_context)
    assert decision.action == PolicyAction.ALLOW

def test_deny_short_circuits():
    """deny 决策短路返回"""
    steps = [
        PolicyStep(evaluator=always_deny, label="layer1"),
        PolicyStep(evaluator=always_allow, label="layer2"),
    ]
    decision, trace = evaluate_pipeline(steps, mock_tool_meta, {}, mock_context)
    assert decision.action == PolicyAction.DENY
    assert len(trace) == 1  # 短路，第二层未执行

def test_only_tighten_never_relax():
    """后续层只能收紧不能放松"""
    steps = [
        PolicyStep(evaluator=always_ask, label="layer1"),
        PolicyStep(evaluator=always_allow, label="layer2"),
    ]
    decision, trace = evaluate_pipeline(steps, mock_tool_meta, {}, mock_context)
    assert decision.action == PolicyAction.ASK  # 不被 allow 放松
```

### 4.2 单元测试: ApprovalManager

```python
# tests/unit/policy/test_approval_manager.py
import pytest
import asyncio
from packages.policy.approval_manager import ApprovalManager
from packages.policy.models import ApprovalDecision

@pytest.mark.asyncio
async def test_idempotent_register():
    """同一 ID 重复注册返回同一 record"""
    manager = ApprovalManager(event_store=mock_store)
    r1 = await manager.register(approval_request)
    r2 = await manager.register(approval_request)
    assert r1.request.approval_id == r2.request.approval_id

@pytest.mark.asyncio
async def test_consume_allow_once_prevents_replay():
    """allow-once 消费后不可重放"""
    manager = ApprovalManager(event_store=mock_store)
    await manager.register(approval_request)
    manager.resolve(approval_request.approval_id, ApprovalDecision.ALLOW_ONCE)
    assert manager.consume_allow_once(approval_request.approval_id) is True
    assert manager.consume_allow_once(approval_request.approval_id) is False

@pytest.mark.asyncio
async def test_timeout_auto_deny():
    """超时自动 deny"""
    manager = ApprovalManager(event_store=mock_store, default_timeout_s=0.5)
    await manager.register(short_timeout_request)
    decision = await manager.wait_for_decision(short_timeout_request.approval_id, timeout_s=1.0)
    assert decision is None  # 超时
```

### 4.3 集成测试: REST API

```python
# tests/integration/test_approval_api.py
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_get_approvals_empty():
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/api/approvals")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

@pytest.mark.asyncio
async def test_approve_nonexistent_returns_404():
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/api/approve/nonexistent", json={"decision": "allow-once"})
        assert resp.status_code == 404
```

---

## 5. 开发检查清单

- [ ] packages/policy/__init__.py 创建
- [ ] models.py 定义所有数据模型
- [ ] pipeline.py 实现 evaluate_pipeline() 纯函数
- [ ] evaluators/profile_filter.py 实现 Layer 1
- [ ] evaluators/global_rule.py 实现 Layer 2
- [ ] approval_manager.py 实现 Two-Phase Approval
- [ ] policy_check_hook.py 实现 BeforeHook 适配器
- [ ] policy_engine.py 实现门面类
- [ ] apps/gateway/routes/approvals.py 实现 REST API
- [ ] apps/gateway/routes/chat.py 实现 Chat API
- [ ] EventType 扩展（5 个新值）
- [ ] TaskStatus 激活 WAITING_APPROVAL
- [ ] validate_transition() 新增 3 条转换规则
- [ ] frontend/src/components/ApprovalPanel/ 实现
- [ ] frontend/src/components/ChatUI/ 实现
- [ ] frontend/src/hooks/useApprovals.ts 实现
- [ ] frontend/src/hooks/useChatStream.ts 实现
- [ ] 单元测试: Pipeline + Evaluators
- [ ] 单元测试: ApprovalManager
- [ ] 集成测试: REST API
- [ ] 集成测试: SSE 事件流
