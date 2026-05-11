# F099 Phase-0 实测侦察报告

**Feature**: F099 Ask-Back Channel + Source Generalization
**Baseline**: c2e97d5（origin/master，F098 完成后）
**侦察日期**: 2026-05-11
**侦察方法**: grep + 直接读源码，禁止凭 prompt 上下文猜测

---

## 实测项 1：当前 ask_back / request_input / escalate_permission 工具状态

### grep 结果

```
grep ask_back     → 零命中（全仓无此工具名）
grep escalate_permission → 零命中（全仓无此工具名）
grep request_input → 命中 4 处，均为 execution_context / execution_console 层的人工输入方法
```

**命中文件:行号**

| 文件 | 行号 | 含义 |
|------|------|------|
| `apps/gateway/src/.../execution_context.py:60` | `async def request_input(...)` | ExecutionContext 暴露给 LLM 任务的人工输入等待方法 |
| `apps/gateway/src/.../execution_console.py:262` | `async def request_input(...)` | 底层实现：设 WAITING_INPUT 状态 + asyncio.Queue 等待 |
| `apps/gateway/tests/test_execution_api.py:36` | `ctx.request_input(...)` | 测试调用 |
| `apps/gateway/tests/test_task_runner.py:75` | `ctx.request_input(...)` | 测试调用 |

### 结论

- **ask_back**：全新引入（零 baseline，无任何声明、capability_pack 注册或 entrypoint）
- **escalate_permission**：全新引入（零 baseline）
- **request_input**（作为 LLM 工具名）：全新引入。注意：当前 baseline 有同名的 Python 方法 `execution_console.request_input()`，但这是内部基础设施方法，**不是** 向 LLM 暴露的 agent_runtime 工具。F099 引入的 `worker.request_input` 工具将调用此内部方法。

**对 spec 影响**：三工具全部需要新建，复杂度不被 baseline 覆盖减轻。

---

## 实测项 2：WAITING_INPUT 状态当前消费链

### WAITING_INPUT 写入路径

`execution_console.py:294-310`（核心写入点）：
```python
state.session.state = ExecutionSessionState.WAITING_INPUT
await service._write_state_transition(
    task_id=task_id,
    from_status=TaskStatus.RUNNING,
    to_status=TaskStatus.WAITING_INPUT,
    ...
    reason="execution_console_input_requested",
)
await self._stores.task_job_store.mark_waiting_input(task_id)
```

触发条件：`execution_console.request_input()` 被调用（目前仅通过 `execution_context.request_input()` → tests 直接调用；LLM 工具层尚未封装）。

### attach_input 唤醒路径（WAITING_INPUT → RUNNING）

`task_runner.py:577-622`（attach_input 实现）：
1. 调用 `execution_console.attach_input(...)` → 若 live waiter 存在，直接通过 asyncio.Queue 投递（`delivered_live=True`）
2. 若无 live waiter（任务重启场景），调用 `task_job_store.mark_running_from_waiting_input(task_id)` + `_spawn_job(resume_from_node="state_running", resume_state_snapshot={...})`

**resume_state_snapshot 含**：`execution_session_id` / `human_input_artifact_id` / `input_request_id`

### 结论

- **WAITING_INPUT → RUNNING 路径已完整存在**（live waiter + 持久化重启两路）
- `ask_back` 工具调用 `execution_context.request_input()` 即可触发状态切换，**无需新建专用唤醒路径**
- 关键问题：`request_input()` 回返的是用户输入字符串；F099 需要确保 LLM turn N+1 能看到"原问题 + 用户回答"（上下文恢复机制需在 spec 的 OD-F099-7 决议）
- `execution_console.py:329-341`：已有 `_a2a_notifier.record_waiting_input(...)` 调用点，A2A 侧已记录 waiting_input 状态（F099 ask_back 发送 A2A 消息时可扩展此点）

**对 spec 影响**：唤醒路径（块 E）可复用 baseline，核心工作在"上下文恢复"而非"状态机新建"。

---

## 实测项 3：A2AConversation source_type 当前枚举

### 实测结论

`packages/core/src/octoagent/core/models/a2a_runtime.py:29-53`（A2AConversation 模型）：

```python
class A2AConversation(BaseModel):
    source_agent_runtime_id: str = Field(default="")
    source_agent_session_id: str = Field(default="")
    target_agent_runtime_id: str = Field(default="")
    target_agent_session_id: str = Field(default="")
    source_agent: str = Field(default="")
    target_agent: str = Field(default="")
    # ... metadata: dict[str, Any] = Field(default_factory=dict)
    status: A2AConversationStatus = A2AConversationStatus.ACTIVE
```

**关键发现**：`A2AConversation` **完全没有 `source_type` 字段**。F098 handoff 中提到的 `source_type` 扩展是**未来规划**，不是已存在的字段。source 语义通过以下机制实现：
- `source_agent`（字符串 URI，如 `"main.agent"` / `"worker.<cap>"`）
- `source_agent_runtime_id` / `source_agent_session_id`（runtime 层关联）
- dispatch_service.py 的 `_resolve_a2a_source_role()` 函数通过 `envelope.metadata.source_runtime_kind` 派生

### source 值当前实际覆盖

`dispatch_service.py:858-880`（`_resolve_a2a_source_role` 实现）：
- `source_runtime_kind in ("worker", "subagent")` → WORKER 路径
- 其他 → MAIN 路径（默认）

**没有 "butler_session" / "worker_session" 等字面枚举值**——这些是 handoff 文档中用于描述的概念，不是代码层 Literal 枚举。

### 结论

- **source_type 字段：全新引入**（A2AConversation 当前无此字段）
- F099 若要在 A2AConversation 上加 source_type，需修改 core 模型 + 所有构造点
- 更轻量的替代方案：复用现有 `source_agent` URI 字段 + 扩展 `envelope.metadata.source_runtime_kind` 枚举值（不新增模型字段）
- 引用方评估：`dispatch_service._resolve_a2a_source_role()` 是唯一读取 source_runtime_kind 的地方，扩展影响面小

**对 spec 影响**：OD-F099-3 需决议"扩展 A2AConversation.source_type 字段 vs 扩展 source_runtime_kind 枚举"；后者改动更小且向后兼容。

---

## 实测项 4：CONTROL_METADATA_UPDATED 当前消费者

### 消费者列表（grep 结果）

| 文件 | 消费方式 |
|------|----------|
| `connection_metadata.py:141`（`merge_control_metadata`）| 同时消费 USER_MESSAGE + CONTROL_METADATA_UPDATED，按 TURN_SCOPED/TASK_SCOPED 规则合并 |
| `task_runner.py:295-320`（`_emit_subagent_delegation_init_if_needed`）| 写入端：emit CONTROL_METADATA_UPDATED 含 SubagentDelegation |
| `agent_context.py:2627-2667`（`_ensure_agent_session` B-3）| 写入端：emit CONTROL_METADATA_UPDATED（session backfill）|
| `memory_tools.py:430-444` | 读取端：同时读 USER_MESSAGE 和 CONTROL_METADATA_UPDATED 反序列化 SubagentDelegation |
| `session_service.py:993` | 读取端：调用 `merge_control_metadata(events)` |

### source 字段当前约定值

`payloads.py:59-66`（ControlMetadataUpdatedPayload.source 字段描述）：
- `subagent_delegation_init`（task_runner emit 点）
- `subagent_delegation_session_backfill`（agent_context emit 点）
- 注释中明确说明"后续 Feature 可扩展"

### merge_control_metadata 扩展点评估

`connection_metadata.py:141-183`：函数按 EventType.USER_MESSAGE 和 EventType.CONTROL_METADATA_UPDATED 过滤，提取 `control_metadata` dict 按 TURN_SCOPED/TASK_SCOPED 两类 key 合并。**F099 扩展只需在 ask_back 触发时 emit 一个新的 CONTROL_METADATA_UPDATED 事件，无需修改 merge 函数本身**（除非需要新的 TASK_SCOPED key）。

### 结论

- **CONTROL_METADATA_UPDATED 已稳定**，F099 可直接复用
- `source` 字段可扩展为 `worker_ask_back` / `worker_escalate_permission` 等值，无需修改 payload schema（字段是自由字符串）
- 是否需要新增 EventType.ASK_BACK_REQUESTED？实测表明 CONTROL_METADATA_UPDATED 足够承载，新增 EventType 增加复杂度但提升可观测性——这是 OD-F099-1 的核心决议点

**对 spec 影响**：CONTROL_METADATA_UPDATED 复用路径已经打通，OD-F099-1 可以有充分的"复用 vs 新增"对比。

---

## 实测项 5：决策环对 ask_back 工具的当前支持

### LLM 工具调用 → task 状态切换路径

当 LLM 调用任意工具时，broker 调用工具 handler，handler 同步/异步返回结果后 LLM 继续下一轮。**当前决策环没有"暂停型工具"（pausing tool）概念**。

如果 ask_back 工具内部调用 `execution_context.request_input()`，则：
1. `request_input()` 设 `session.state = WAITING_INPUT`
2. `request_input()` await `asyncio.Queue.get()`（阻塞，等待 attach_input）
3. 决策环（LLM service 调用栈）挂起在此 await 点
4. 外部 `attach_input()` 投递消息到 Queue → `request_input()` 返回输入文本
5. ask_back 工具 handler 将输入文本作为工具返回值返回给 LLM
6. LLM turn N+1 以工具调用结果的形式看到输入（**这是最自然的上下文恢复路径**）

### capability_pack 工具注册现状

`supervision_tools.py` 注册 `subagents.list` / `work.plan`，两者 entrypoints 均为 `["agent_runtime", "web"]`。Worker 的工具集通过 `tool_profile`（如 `"default"` / `"standard"` / `"research"`）决定。

**当前没有专属 worker 工具集定义机制**（工具注册是 broker 级别的，不区分 agent kind）——所有注册到 broker 的工具都可被主 Agent 和 Worker 调用（受 policy 控制）。

### recall_planner 对暂停型工具的处理

`llm_service.py:220-431`（`supports_single_loop_executor` 机制）：当前 recall_planner skip 逻辑与工具类型无关——这是 F100 的范围（Decision Loop Alignment）。F099 只需确保 ask_back 工具在工具注册时 entrypoints 包含 `agent_runtime`。

### 结论

- **决策环已天然支持暂停型工具**：通过 asyncio.Queue 阻塞机制，ask_back 调用 `execution_context.request_input()` 后 LLM turn 天然挂起等待
- **工具返回值是上下文恢复载体**：用户 attach_input → Queue 投递 → `request_input()` 返回文本 → broker 将文本作为 tool_result 返回 LLM → LLM turn N+1 看到完整上下文（原问题 + 用户回答）
- **无需新建"暂停型工具"专属框架**：复用 execution_console.request_input 机制即可
- **capability_pack / kind 过滤**：当前不区分 worker/subagent kind，ask_back 注册到 broker 即所有 agent 可用；若需要限制只在 Worker 下可用，可通过工具 metadata 标注 + policy 控制（但 baseline 无此机制，超出 F099 范围）

**对 spec 影响**：块 B 实现路径比预期更简单——核心是工具注册 + 调用 `execution_context.request_input()`，不需要决策环改造。OD-F099-4 可以明确为"所有 agent kind 均可调用"（不区分）。

---

## 汇总：baseline 已通 vs 需新增 vs 需修改

| 侦察项 | baseline 状态 | F099 工作量 |
|--------|--------------|-------------|
| ask_back / escalate_permission 工具 | **全新引入**（零 baseline）| 新建 3 个工具 handler |
| WAITING_INPUT 状态机 + 唤醒路径 | **已完整存在**，直接复用 | 无需修改状态机本身 |
| 上下文恢复机制 | **已天然存在**（tool_result 路径）| 无需新建，验证即可 |
| A2AConversation.source_type 字段 | **全新引入**（模型无此字段）| 轻量替代：扩展 source_runtime_kind 枚举 |
| source_runtime_kind 枚举值 | **需扩展**（当前仅 worker/subagent/main）| 加 automation/user_channel 两值 + 注入逻辑 |
| spawn 路径 source_runtime_kind 注入 | **缺失**（F098 已知 LOW，当前默认 main）| 需在 spawn_child/delegate_task 时注入 |
| CONTROL_METADATA_UPDATED 事件 | **已稳定**，可直接复用 | 扩展 source 字段候选值（无 schema 变更）|
| BaseDelegation 抽象 | **已就位**（F098 Phase J）| F099 可直接继承 |
| execution_console.request_input() | **已存在**（infrastructure 方法）| 工具层包装即可 |
| Policy Engine (PolicyAction) | **已存在**（packages/policy）| escalate_permission 接入需了解接入点 |

---

v0.1 - 待 GATE_DESIGN 审查
