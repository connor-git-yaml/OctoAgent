# F101 Tech Research / 块 A 实测侦察

**特性分支**: `feature/101-notification-attention`
**调研日期**: 2026-05-15
**调研模式**: codebase-scan（独立模式，无 product-research.md）
**实测基线 commit**: `182e9ed`（F100 Phase H 完成）
**调研范围**: F101 Notification + Attention Model + 承接 F099 7 项推迟项

---

## 背景（5 句话内）

F101 是 M5 阶段 3 起点，主责优先级模型 + quiet hours 进 USER.md SoT + Web/Telegram dismiss 同步，同时承接 F099 归档的 7 项推迟项（含 F3 HIGH 状态机改造、ApprovalGate SSE production 接入等）和 F100 minimal trigger producer 实现。本次侦察在 `182e9ed` baseline 上通过 grep + 文件实读沉淀 5 个方向的基线事实，不含任何 spec 提案。调研发现：Notification 基础设施（NotificationService + SSENotificationChannel + TelegramNotificationChannel）已在 F064 完整落地；ApprovalGate 的 `sse_push_fn` 在 octo_harness.py 中被显式置为 `None` 并注明"后续阶段绑定"；force_full_recall producer 在 chat.py 中完全缺失。

---

## A-1 Notification / Attention 现状

### A-1-1 现有实体一览

| 现有功能名 | 文件路径（行号） | 当前行为 | F101 是否需扩展 | F101 是否需新建 |
|-----------|---------------|---------|---------------|---------------|
| `NotificationService` | `gateway/services/notification.py:92` | 路由分发 + 去重（内存 set），支持 `notify_task_state_change` / `notify_approval_request` / `notify_heartbeat` 三个 dispatch 方法 | 扩展：新增优先级模型 / quiet hours 过滤 | 否 |
| `SSENotificationChannel` | `notification.py:237` | 利用 SSEHub.broadcast() 广播通知事件；`send_approval_request` 返回 False（不支持交互式）| 扩展：补充 approval_request SSE 推送能力 | 否 |
| `TelegramNotificationChannel` | `notification.py:318` | 发送任务状态变更中文文本 + WAITING_APPROVAL 审批 inline keyboard；`send_message_fn` 注入 | 扩展：dismiss 同步 | 否 |
| `NotificationChannelProtocol` | `notification.py:33` | Protocol 接口，`notify` + `send_approval_request` 两个方法 | 扩展：可能补 priority / dismiss 方法 | 否 |
| `_STATUS_DISPLAY` 映射 | `notification.py:308` | 含 `WAITING_APPROVAL` 中文 "等待审批" | 无需扩展 | 否 |
| SSEHub | `harness/octo_harness.py:421` | `SSEHub()` 在 `_bootstrap_runtime_services` 初始化，被 TelegramService / ApprovalBroadcaster / LLMService 等多处引用 | 无需扩展（现有广播机制直接复用） | 否 |
| `ApprovalGate.sse_push_fn` | `harness/approval_gate.py:93` | `sse_push_fn: Any | None = None`，构造时为 None；request_approval 调用时若非 None 则 push | 需要注入实际 SSE 推送函数 | 否 |
| `attention_work_count` | `models/control_plane/agent.py:55` | `WorkerProfileDynamicContext` 字段，int 计数，无关联行为 | 可扩展为 Attention Model 输入信号 | 否 |
| USER.md 模板 `活跃时段` 字段 | `core/behavior_templates/USER.md:22` | 注释 "（待了解后补充——帮助安排异步任务的通知时机）"——意向字段，未实现 | 需实现为 quiet_hours SoT | 否（字段已存在）|
| `WAITING_APPROVAL` 状态 | `core/models/enums.py:25` | TaskStatus 枚举值，transition 集有 `{RUNNING}` → WAITING_APPROVAL，`WAITING_APPROVAL` → `{SUCCEEDED, FAILED, CANCELLED}` | 扩展：task_runner 中 WAITING_APPROVAL 没有触发通知推送（见 A-2-1） | 否 |

### A-1-2 SSE 推送路径实测

当前 `notification_service` 在 task_runner 的 `_notify_completion` 只覆盖终态。实测未发现 `WAITING_APPROVAL` 触发 notification 推送的调用点：

```
task_runner.py:404   if task.status == TaskStatus.WAITING_APPROVAL:
task_runner.py:405       await self._stores.task_job_store.mark_waiting_approval(job.task_id)
task_runner.py:406       return     ← 直接 return，无 notification_service 调用
```

**结论**：F064 已建立 Notification 基础设施（NotificationService + 两个 Channel 实现），但 WAITING_APPROVAL 状态无通知推送、quiet_hours 未实现、ApprovalGate SSE production 接入未完成——三者均是 F101 的核心工作。

---

## A-2 F099 7 项推迟项 baseline 状态（逐项实测）

### A-2-1 F3 HIGH — escalate_permission WAITING_APPROVAL 状态机

**实测方法**：读 `enums.py`、`task_runner.py`、`ask_back_tools.py`。

**发现**：
- `TaskStatus.WAITING_APPROVAL` 在 `enums.py:25` 存在，transition 表 `enums.py:46-49` 有 `WAITING_APPROVAL → {SUCCEEDED, FAILED, CANCELLED}`。
- `task_runner.py:404-406`：当恢复任务时，若 `task.status == WAITING_APPROVAL` → 仅调用 `mark_waiting_approval`，无 resume 分支，无通知推送，无超时监控。
- `task_runner.py:779`：超时清理逻辑显式跳过 `WAITING_APPROVAL` 状态（`continue`），意味着 approval pending 的任务永远不会超时清理。
- `ask_back_tools.py:362-375`：`escalate_permission_handler` 调用 `approval_gate.request_approval()` + `wait_for_decision()`；**当 approval_gate is None 时直接返回 "rejected"（降级路径，Constitution C6）**，不更新 task status 为 WAITING_APPROVAL。实际生产路径下 approval_gate is None，所以 escalate_permission 永远走降级返回 "rejected"。

**结论**：F3 HIGH 的核心缺陷是：escalate_permission 调用了 ApprovalGate 路径，但 production 中 ApprovalGate.sse_push_fn=None（见 A-2-3），任务状态机不会真正进入 WAITING_APPROVAL——这与 A-2-3 强耦合，必须联合修复。

| 推迟项 | 严重度 | F100 后状态 | F101 接管必要性 |
|-------|--------|------------|---------------|
| F3 HIGH escalate_permission WAITING_APPROVAL 状态机 | HIGH | baseline（未修）| 必修——且依赖 A-2-3 ApprovalGate SSE 接入 |

### A-2-2 F5 PARTIAL — RUNNING guard 空串返回

**实测方法**：读 `ask_back_tools.py:179-195`（ask_back handler）、`ask_back_tools.py:268-283`（request_input handler）、`ask_back_tools.py:362-376`（escalate_permission handler）。

**发现**：
- 三工具均在入口处有 `_guard_ctx = get_current_execution_context()` + `_guard_task.status != TaskStatus.RUNNING` 检查（F099 Codex Final F5 修复，`ask_back_tools.py:182-195`, `270-283`, `365-376`）。
- 非 RUNNING 时：ask_back 和 request_input 返回 `""`（空字符串），escalate_permission 返回 `"rejected"`。
- guard 的 `except Exception: pass`（`ask_back_tools.py:194`）是 M-1 broad-catch 问题的来源（见 A-2-6）。
- **F5 标注为 PARTIAL 的原因**：F099 re-re-review 记录显示"N-H1 降级 PARTIAL"——guard 只检查了 `is_caller_worker` 信号，非 worker 路径未保护。实测代码：`if getattr(_guard_ctx, "is_caller_worker", False):` — 若 `is_caller_worker=False`（非 worker 路径），guard 完全跳过，任何状态下都不检查。

| 推迟项 | 严重度 | F100 后状态 | F101 接管必要性 |
|-------|--------|------------|---------------|
| F5 PARTIAL RUNNING guard 空串返回 | LOW | PARTIAL（仅 is_caller_worker=True 路径有 guard）| 推荐——补全非 worker 路径 guard |

### A-2-3 ApprovalGate sse_push_fn production 接入

**实测方法**：读 `harness/octo_harness.py:694-709`、`harness/approval_gate.py:88-105`。

**发现**：
```python
# octo_harness.py:700-703
_approval_gate = ApprovalGate(
    event_store=...,
    task_store=...,
    sse_push_fn=None,  # SSE 推送通过 app.state.sse_hub 在后续阶段绑定
)
```
注释写"后续阶段绑定"，但实测 octo_harness.py 全文无任何后续 `sse_push_fn` 赋值——**`sse_push_fn` 永远是 None**。

`approval_gate.py:224`：`if self._sse_push_fn is not None:` — None 时 push 分支完全跳过，只写审计事件。

`octo_harness.py:705-706`：
```python
app.state.approval_gate = _approval_gate
app.state.capability_pack_service.bind_approval_gate(_approval_gate)
```
`ApprovalGate` 实例已绑定到 `app.state` 和 `capability_pack_service`，且 `capability_pack.py:266-274` 有 `bind_approval_gate` 方法会同步注入 `_tool_deps._approval_gate`。

**sse_hub 已就绪**：`octo_harness.py:421` 的 `app.state.sse_hub = SSEHub()` 在 `_bootstrap_runtime_services` 中完成，比 `_approval_gate` 创建（`_bootstrap_capability_pack`，行 694-709）**更早**执行（bootstrap 顺序：runtime_services → llm → capability_pack → executors）。因此在 `_bootstrap_capability_pack` 内可直接访问 `app.state.sse_hub`。

| 推迟项 | 严重度 | F100 后状态 | F101 接管必要性 |
|-------|--------|------------|---------------|
| ApprovalGate SSE production 接入 | MED | sse_push_fn=None（octo_harness.py:703）| 必修——escalate_permission 生产路径依赖此修复才能真实工作 |

### A-2-4 AC-E1 e2e 完整三条事件序列

**实测方法**：grep `ASK_BACK_REQUESTED`、`ASK_BACK_RESPONDED`、`ATTACH_INPUT`——三个名称均无命中（ask_back 不新建事件 type，走 `CONTROL_METADATA_UPDATED` + `APPROVAL_REQUESTED` 复用）。

**发现**：
- 无 `ASK_BACK_REQUESTED` / `ASK_BACK_RESPONDED` 专用事件。ask_back 审计链是：`CONTROL_METADATA_UPDATED`（emit ask_back 元数据）→ task 进 WAITING_INPUT → 用户 attach_input → resume。
- 现有 e2e 覆盖：`tests/services/test_phase_e_ask_back_e2e.py`（单测，mock-based）+ `tests/services/test_phase_c_source_injection.py`（N-H1 修复路径验证）。
- 实测未发现覆盖"Worker ask_back → WAITING_INPUT → 用户回答 → RUNNING resume → CONTROL_METADATA_UPDATED 事件链完整"的 integration test。

| 推迟项 | 严重度 | F100 后状态 | F101 接管必要性 |
|-------|--------|------------|---------------|
| AC-E1 e2e 完整三条事件序列 | MED | 单测局部覆盖，无完整 integration test | 推荐——补 integration test 覆盖 ask_back 完整链 |

### A-2-5 N-H1 PARTIAL — is_caller_worker resume 其余路径

**实测方法**：grep `is_caller_worker_signal` 全库，读 `task_runner.py:613-639`、`worker_runtime.py:398-443`、`connection_metadata.py`。

**发现**：
- F099 N-H1 修复已覆盖：WorkerRuntime 首次 dispatch 时写 `CONTROL_METADATA_UPDATED(is_caller_worker_signal="1")`（`worker_runtime.py:398-443`）；`task_runner.attach_input` resume 路径从 `latest_user_metadata` 读取该信号并写入 `_resume_snapshot`（`task_runner.py:613-626`）。
- `connection_metadata.py:57`：`is_caller_worker_signal` 已在 `TASK_SCOPED_CONTROL_KEYS` 中注册。
- **PARTIAL 体现**：`task_runner.py:438-448` 的 `startup_recovery` 路径（gateway 重启后 resume RUNNING job）调用 `resume_engine.try_resume`，但 `_resume_snapshot` 在此路径不包含 `is_caller_worker_signal` 的读取逻辑（对比 `attach_input` 路径的 614-626 行，startup_recovery 无此段）。F100 handoff §4 明确：此 PARTIAL 待 F101 / F107。

| 推迟项 | 严重度 | F100 后状态 | F101 接管必要性 |
|-------|--------|------------|---------------|
| N-H1 PARTIAL is_caller_worker resume 其余路径 | MED | attach_input 路径已修，startup_recovery 路径未覆盖 | 推荐（F101 或 F107）|

### A-2-6 M-1 broad-catch 吞异常

**实测方法**：读 `ask_back_tools.py` 全文，定位所有 `except Exception` 块。

**发现**：共 4 处 broad-catch：
- `ask_back_tools.py:194`：guard 内 `except Exception: pass` — guard 失败时无日志，静默跳过
- `ask_back_tools.py:219`：ask_back handler 外层 `except Exception as exc: log.warning(...)` — 有日志，合理降级
- `ask_back_tools.py:282`：request_input handler guard 内 `except Exception: pass` — 同 ask_back，静默
- `ask_back_tools.py:376`：escalate_permission handler guard 内 `except Exception: pass` — 同上

**M-1 定义的问题**：guard 内的 `except Exception: pass` 无日志，execution_context 或 task_store 不可用时静默跳过 RUNNING 检查。在 production 出问题时无法追踪原因。

| 推迟项 | 严重度 | F100 后状态 | F101 接管必要性 |
|-------|--------|------------|---------------|
| M-1 broad-catch 吞异常 | LOW | 未修（3 处 guard 内 `except Exception: pass` 无 log）| 可选——改为 log.debug 即可，极低风险改动 |

### A-2-7 N-L1 — source_kinds.py `__all__`

**实测方法**：读 `packages/core/src/octoagent/core/models/source_kinds.py` 全文。

**发现**：文件共 72 行，无 `__all__` 定义。文件导出 11 个符号（5 个 `SOURCE_RUNTIME_KIND_*` + `KNOWN_SOURCE_RUNTIME_KINDS` frozenset + 5 个 `CONTROL_METADATA_SOURCE_*`）——无 `__all__` 意味着 `from source_kinds import *` 会导入模块级所有非下划线名称（共 11 个），在实际使用中均为显式 import 所以无实际影响。

| 推迟项 | 严重度 | F100 后状态 | F101 接管必要性 |
|-------|--------|------------|---------------|
| N-L1 source_kinds.py `__all__` | LOW style | 未修 | 可选——任意 commit 顺手清 |

---

## A-3 ApprovalGate sse_push_fn production 接入设计

### 当前现状

`octo_harness.py:700-703` 创建 ApprovalGate 时 `sse_push_fn=None`。注释说"后续阶段绑定"，但全文无后续赋值——**当前生产路径 SSE push 完全不工作**。

`approval_gate.py` 内 `sse_push_fn` 签名（`approval_gate.py:92-94`）：
```python
sse_push_fn: Any | None = None
# 异步函数 (session_id, payload) -> None
```
参数：`(session_id: str, payload: dict) -> None`，异步。

### 现有 SSE 机制

`SSEHub` 实例在 `app.state.sse_hub`，被以下组件复用：
- `TelegramGatewayService`（`octo_harness.py:427`）
- `SSEApprovalBroadcaster`（`octo_harness.py:436`）—— **已有 SSE approval 广播器**
- `LargeOutputHandler`（`octo_harness.py:472`）
- task_runner（`octo_harness.py:789`）
- ControlPlaneService（`octo_harness.py:1017`）

`SSEApprovalBroadcaster` 已存在，职责是把 ApprovalManager 的审批事件推送到 SSE。**注意**：这是 `ApprovalManager`（policy-level）的 broadcaster，不是 `ApprovalGate`（harness-level）的 sse_push_fn——两者是不同层次的对象。

### 候选注入点（≥3 个）

**选项 1：octo_harness._bootstrap_capability_pack 内直接构造 sse_push_fn 闭包**

位置：`octo_harness.py:700-703`，改为：
```python
_sse_hub = getattr(app.state, "sse_hub", None)  # _bootstrap_runtime_services 已完成
async def _sse_push(session_id: str, payload: dict) -> None:
    if _sse_hub:
        await _sse_hub.broadcast_to_session(session_id, {"type": "approval_sse", **payload})
_approval_gate = ApprovalGate(
    event_store=..., task_store=..., sse_push_fn=_sse_push,
)
```
优点：改动最小，不改签名；SSEHub 在 `_bootstrap_runtime_services` 中更早创建，此时已可用。缺点：SSEHub 的 broadcast 方法需确认是否支持按 session_id 广播（需核实 `SSEHub.broadcast` 签名，当前实测只看到 `broadcast(task_id, event)` 形态）。

**选项 2：octo_harness._bootstrap_executors 后期通过 app.state.approval_gate 注入**

位置：在 `_bootstrap_executors` 末尾，`app.state.approval_gate` 已存在后调用 `app.state.approval_gate._sse_push_fn = sse_push_fn`。优点：SSEHub 100% 可用；缺点：绕过构造函数，需要 ApprovalGate 暴露 setter。

**选项 3：复用 SSEApprovalBroadcaster 现有推送路径**

`octo_harness.py:436` 创建的 `SSEApprovalBroadcaster` 已负责推送审批事件到 Web。调整 ApprovalGate.sse_push_fn 使其复用 `SSEApprovalBroadcaster.broadcast_approval(session_id, payload)` 方法（如该方法存在或可新增）。优点：统一审批 SSE 路径；缺点：需确认 SSEApprovalBroadcaster 是否有 per-session 推送能力（实测未确认接口），且可能耦合两个不同抽象层。

**推荐**：选项 1（就地闭包）最简单，改动范围最小，唯一需确认的是 `SSEHub.broadcast` 是否支持 `session_id` 维度的过滤。如不支持，需在 SSEHub 上新增 `broadcast_to_session` 方法，或通过 task_id（session_id 可能需要转换）。

---

## A-4 chat 路由 force_full_recall producer 入口

### 当前 dispatch_metadata 构造

`chat.py:422-444`（新对话路径）：
```python
dispatch_metadata = dict(chat_control_metadata)  # line 422
...
dispatch_metadata[RUNTIME_CONTEXT_JSON_KEY] = encode_runtime_context(
    RuntimeControlContext(
        task_id=task_id,
        surface="web",
        ...
        metadata=runtime_metadata,  # line 443, runtime_metadata 含 new_conversation_token 等
    )
)
```

`chat.py:479-493`（续对话路径）：类似构造，`runtime_metadata` 不传。

**关键发现**：两条路径的 `RuntimeControlContext` 构造均未设置 `force_full_recall` 字段。`RuntimeControlContext` 默认 `force_full_recall=False`（`packages/core/models/orchestrator.py:102`）。

### force_full_recall 调用图

```
chat.py:433 → encode_runtime_context(RuntimeControlContext(...))
    → dispatch_metadata[RUNTIME_CONTEXT_JSON_KEY]
        → task_runner.enqueue → orchestrator._prepare_single_loop_request
            → orchestrator._with_delegation_mode(force_full_recall=None)
                → 读 metadata["force_full_recall"] hint（FR-H1）
                    → 若 True → resolved_force_full_recall=True
                    → 写入 patched runtime_context.force_full_recall
```

**producer 候选位置**：`chat.py:422-444`（新对话路径 dispatch_metadata 构造处），在 `encode_runtime_context` 调用之前加入：
```python
if len(body.message) > LONG_PROMPT_THRESHOLD:
    dispatch_metadata["force_full_recall"] = True
```
或通过 body 参数直接透传（若 ChatSendRequest 扩展 `force_full_recall: bool = False` 字段）。

**现状（缺失）**：
- 无 prompt 长度检测逻辑
- 无跨 session context 检测
- 无任何 `force_full_recall` 写入

**接入复杂度评估**：低——注入点已由 F100 FR-H 精确指定（`dispatch_metadata["force_full_recall"] = True`），chat.py 仅需在构造 dispatch_metadata 时条件写入，不需要改 orchestrator 或 runtime_control。

---

## A-5 control_plane D8 耦合实测

### domain service 清单（实测 `_coordinator.py:147-163`）

`ControlPlaneService.__init__` 实例化 9 个 domain service：

| # | service 变量名 | 类名 | import 路径 |
|---|--------------|------|-----------|
| 1 | `_session_service` | `SessionDomainService` | `.session_service` |
| 2 | `_work_service` | `WorkDomainService` | `.work_service` |
| 3 | `_agent_service` | `AgentProfileDomainService` | `.agent_service` |
| 4 | `_automation_service` | `AutomationDomainService` | `.automation_service` |
| 5 | `_import_service` | `ImportDomainService` | `.import_service` |
| 6 | `_mcp_service` | `McpDomainService` | `.mcp_service` |
| 7 | `_memory_service` | `MemoryDomainService` | `.memory_service` |
| 8 | `_setup_service` | `SetupDomainService` | `.setup_service` |
| 9 | `_worker_service` | `WorkerProfileDomainService` | `.worker_service` |

注：F101 CLAUDE.local.md 提及"D8 control_plane DI 隐性耦合"，但实测 `_coordinator.py` 实现的是显式构造模式（`_ctx = ControlPlaneContext(...)` 共享上下文），9 个 domain service 共享同一 `_ctx` 对象——这是弱耦合（共享数据容器），而非"隐性耦合"。

### 构造注入问题（实测 `_coordinator.py:93-109`）

`ControlPlaneService.__init__` 接收 12 个外部依赖：`project_root / store_group / sse_hub / task_runner / operator_action_service / operator_inbox_service / telegram_state_store / update_status_store / update_service / memory_console_service / capability_pack_service / delegation_plane_service / import_workbench_service / policy_engine`（实际 14 个参数，部分可选）。

`octo_harness.py:1017-1031` 中 ControlPlaneService 构造：
```python
ControlPlaneService(
    ...,
    sse_hub=app.state.sse_hub,
    ...
)
```
`sse_hub` 每次构造都传入，是真实依赖注入——但如果后续 sse_hub 重建（不太可能），ControlPlaneService 无法感知。

**重复构造点**：grep 未发现 ControlPlaneService 多处实例化，仅在 `_bootstrap_executors` 中一处构造。未发现明显循环依赖。

### D8 重构方案对比

| 选项 | 描述 | 优点 | 缺点 | 适用场景 |
|------|------|------|------|---------|
| **选项 X：工厂函数** | 抽取 `make_control_plane_service(app_state) -> ControlPlaneService` | 参数化清晰，可测试 | 不减少耦合，仅封装构造 | 参数多但稳定 |
| **选项 Y：轻型 IoC（ServiceLocator）** | 在 `ControlPlaneContext` 中存储所有依赖，domain service 通过 `ctx.get(sse_hub)` 懒加载 | 解耦 domain service 与顶层注入，支持后期绑定 | 增加间接层，类型丢失 | 依赖图复杂、需要后期动态注入 |
| **选项 Z：保留现状（不重构）** | 维持 14 参数构造 | 零改动风险 | 参数继续增加时维护难度线性增加 | F101 范围仅需顺手清一处 |

**F101 范围评估**：D8 并非阻塞项。实测 ControlPlaneService 构造方式是显式 DI，已是最佳实践形态——问题是参数数量多，而非"隐性耦合"。F101 如需 Notification/Attention Model 集成到 control_plane，最简路径是在现有 14 参数中加 `notification_service` 参数，不需要重架。

---

## 关键风险 / 不确定性

### 风险 1：SSEHub.broadcast 不支持 per-session_id 广播

**来源**：A-3 选项 1 假设 SSEHub 有 `broadcast_to_session(session_id, payload)` 能力，但实测 octo_harness.py 只看到 `broadcast(task_id, event)` 形态的调用（`octo_harness.py:472`）。SSEHub 内部实现未实测，`approval_gate.sse_push_fn` 的期望签名是 `(session_id, payload)`，如果 SSEHub 只支持 task_id 广播，需要额外 session_id → task_id 映射层。

**影响**：可能增加 A-3 实施复杂度（MED）。

### 风险 2：WAITING_APPROVAL 超时清理缺失

**来源**：`task_runner.py:777-782` 的超时清理逻辑显式 `continue` 跳过 WAITING_APPROVAL 状态，意味着用户 300s 不操作后 ApprovalGate 内部超时（`wait_for_decision` 返回 "rejected"），但 task_runner 的超时 monitor 不知道此事——可能造成任务状态不一致。

**影响**：F3 HIGH 修复时需要同时处理任务状态机同步（HIGH）。

### 风险 3：NotificationService 未绑定到 task_runner

**来源**：实测 `task_runner.py` 的 `_notify_completion`，向上追踪未确认 `notification_service` 是否在 task_runner 构造时注入（本次侦察未完整读 task_runner 构造函数）。若 NotificationService 未注入，F101 quiet_hours 过滤逻辑无处接入。

**需要后续确认**：`task_runner.__init__` 中是否有 `notification_service` 参数。

### 风险 4：USER.md 活跃时段字段无解析逻辑

**来源**：`USER.md:22` 有 `活跃时段` 字段描述，但无解析实现。quiet_hours 落地需要：①USER.md 新增 quiet_hours 结构化字段；②parser 解析该字段；③NotificationService 集成过滤逻辑。改动链比预想长。

**影响**：quiet_hours SoT 实施复杂度可能被低估（MED）。

### 风险 5：F3 HIGH 与 A-2-3 强耦合

**来源**：escalate_permission 在 production 中 approval_gate is None，直接降级 "rejected"，永远不会真正进入 WAITING_APPROVAL 状态。F3 HIGH 状态机改造只有在 A-2-3 SSE production 接入完成后才有意义。两项必须联合实施，不能分 Phase 各自独立验证。

---

## 引用文件清单（含行号）

| 编号 | 文件路径 | 关键行号 | 用途 |
|------|---------|---------|------|
| 1 | `octoagent/apps/gateway/src/octoagent/gateway/services/notification.py` | 92-229 | NotificationService 实现 |
| 2 | `octoagent/apps/gateway/src/octoagent/gateway/services/notification.py` | 237-300 | SSENotificationChannel |
| 3 | `octoagent/apps/gateway/src/octoagent/gateway/services/notification.py` | 318-485 | TelegramNotificationChannel |
| 4 | `octoagent/apps/gateway/src/octoagent/gateway/harness/approval_gate.py` | 88-105 | ApprovalGate.__init__ sse_push_fn 参数 |
| 5 | `octoagent/apps/gateway/src/octoagent/gateway/harness/approval_gate.py` | 222-244 | request_approval 内 sse_push_fn None guard |
| 6 | `octoagent/apps/gateway/src/octoagent/gateway/harness/octo_harness.py` | 694-709 | ApprovalGate 构造（sse_push_fn=None）|
| 7 | `octoagent/apps/gateway/src/octoagent/gateway/harness/octo_harness.py` | 420-421 | SSEHub 初始化 |
| 8 | `octoagent/apps/gateway/src/octoagent/gateway/harness/octo_harness.py` | 435-436 | SSEApprovalBroadcaster 构造 |
| 9 | `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py` | 179-226 | ask_back_handler（含 guard + broad-catch）|
| 10 | `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py` | 362-444 | escalate_permission_handler（含 approval_gate None 降级）|
| 11 | `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py` | 194, 282, 376 | M-1 broad-catch `except Exception: pass` 三处 |
| 12 | `octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/_deps.py` | 65 | ToolDeps._approval_gate 字段（默认 None）|
| 13 | `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py` | 401-406 | WAITING_INPUT / WAITING_APPROVAL 无通知推送 |
| 14 | `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py` | 613-639 | attach_input resume + is_caller_worker_signal 读取（N-H1 已修路径）|
| 15 | `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py` | 770-782 | 超时监控跳过 WAITING_APPROVAL / WAITING_INPUT |
| 16 | `octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py` | 422-444 | dispatch_metadata 构造（force_full_recall 候选注入点）|
| 17 | `octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py` | 479-493 | 续对话 dispatch_metadata 构造（同上，第二注入点）|
| 18 | `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane/_coordinator.py` | 93-176 | ControlPlaneService.__init__ + 9 domain service 构造 |
| 19 | `octoagent/packages/core/src/octoagent/core/models/enums.py` | 24-49 | TaskStatus WAITING_APPROVAL + transition 表 |
| 20 | `octoagent/packages/core/src/octoagent/core/models/source_kinds.py` | 1-72 | source_kinds 全文（无 `__all__`）|
| 21 | `octoagent/packages/core/src/octoagent/core/behavior_templates/USER.md` | 22 | 活跃时段字段（quiet_hours SoT 前身）|
| 22 | `octoagent/packages/core/src/octoagent/core/models/control_plane/agent.py` | 55 | `attention_work_count` 字段 |
| 23 | `octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py` | 398-443 | `_emit_is_caller_worker_signal`（N-H1 已修）|
| 24 | `octoagent/apps/gateway/src/octoagent/gateway/services/runtime_control.py` | 106-124 | is_recall_planner_skip + force_full_recall override 逻辑 |
