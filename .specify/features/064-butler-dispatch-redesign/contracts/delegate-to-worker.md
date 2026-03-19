# 工具契约: delegate_to_worker

**Feature**: 064 — Butler Dispatch Redesign
**引入 Phase**: Phase 2
**副作用等级**: `reversible`（委派操作可取消）

---

## 概述

`delegate_to_worker` 是 Butler 的内建工具，允许 LLM 在推理过程中自主判断是否需要将任务委派给专业 Worker 执行。该工具替代了原来的 Butler Decision Preflight 中的代码预判委派逻辑。

---

## Schema 定义

```json
{
  "name": "delegate_to_worker",
  "description": "将任务委派给专业 Worker 执行。仅在任务需要长时间运行、专业能力（编程/搜索/运维）或独立上下文时使用。简单问答、闲聊、确认等不需要委派。",
  "parameters": {
    "type": "object",
    "required": ["worker_type", "task_description"],
    "properties": {
      "worker_type": {
        "type": "string",
        "enum": ["research", "dev", "ops", "general"],
        "description": "Worker 类型。research: 搜索/调研/信息检索；dev: 编程/调试/代码生成；ops: 运维/系统管理/部署；general: 通用任务"
      },
      "task_description": {
        "type": "string",
        "description": "给 Worker 的任务描述，需包含足够上下文让 Worker 理解任务目标和期望输出"
      },
      "urgency": {
        "type": "string",
        "enum": ["normal", "high"],
        "default": "normal",
        "description": "任务紧急程度。high: 优先调度"
      }
    },
    "additionalProperties": false
  }
}
```

---

## 执行语义

### 输入处理

1. 验证 `worker_type` 为合法枚举值
2. 验证 `task_description` 非空且长度 > 5
3. 将 `urgency` 默认为 `"normal"`

### 执行流程

1. **构建 context capsule**: 工具执行器从当前 ButlerSession 提取：
   - 近期对话摘要（最近 3-5 轮）
   - 相关 memory recall 结果（如已执行）
   - 用户原始请求文本
2. **构建委派请求**: 注入 metadata（`delegate_source="butler_tool_call"` 等）
3. **调用 Delegation Plane**: 走现有 `prepare_dispatch()` → `dispatch_envelope()` 路径
4. **返回 Worker 执行结果**: Worker 完成后，结果通过 A2A 协议返回

### 输出格式

```json
{
  "status": "delegated",
  "worker_type": "research",
  "task_id": "task-xxx",
  "message": "任务已委派给 research Worker 执行"
}
```

或失败时:

```json
{
  "status": "failed",
  "error": "Worker 创建失败: ...",
  "fallback_message": "无法完成委派，请稍后重试或尝试直接描述需求"
}
```

---

## 使用指引（注入 system prompt）

以下指引在 Butler 的 system prompt 中注入，引导 LLM 正确使用该工具：

```
## delegate_to_worker 使用指引

你可以通过 delegate_to_worker 工具将复杂任务委派给专业 Worker。

### 何时使用
- 任务需要长时间运行（如代码生成、深度调研）
- 任务需要专业能力（编程、搜索、运维）
- 任务需要独立执行上下文

### 何时不使用
- 简单问答、闲聊、确认
- 你已经可以通过当前工具直接完成的任务
- 用户只是在打招呼或询问你的能力

### worker_type 选择
- research: 需要搜索、调研、信息检索
- dev: 需要编程、调试、代码生成
- ops: 需要运维、系统管理
- general: 不确定时使用

### task_description 编写
- 包含任务目标和期望输出
- 包含必要的上下文信息
- 不需要包含对话历史（系统会自动附加）
```

---

## 与 A2A 协议的关系

`delegate_to_worker` 工具调用最终通过现有的 Delegation Plane → Worker Dispatch 路径执行，A2A 协议不变：

```
Butler (delegate_to_worker tool call)
  → 工具执行器构建 context capsule
  → Delegation Plane.prepare_dispatch()
  → DispatchEnvelope 创建
  → Worker Runtime.execute()
  → A2A TaskMessage / ResultMessage 通信
  → WorkerResult 返回给 Butler
```

---

## 安全考量

### 副作用等级

`reversible` — 委派操作创建 Worker 任务，但任务可被取消（通过 Task 状态机的 CANCELLED 终态）。

### Prompt Injection 防护

- `delegate_to_worker` 的执行经过 ToolBroker + Policy Engine，继承现有的工具权限控制
- 工具执行器构建 context capsule 时不直接传递用户原始输入，而是从 ButlerSession 提取结构化数据
- Worker 收到的任务描述经过 Butler LLM 的语义理解和重新表述，降低了原始 injection payload 的传递风险

### 成本控制

- 每次 `delegate_to_worker` 调用创建独立的 Worker 任务，受 Worker 的轮次上限（50）控制
- Butler 的轮次上限（10）限制了单次请求中 `delegate_to_worker` 的最大调用次数
