# F099 Ask-Back Channel + Source Generalization — 技术实施计划

**关联**: spec.md v0.2（GATE_DESIGN 已锁定）+ phase-0-recon.md + clarification.md
**Phase 数**: 5（A 已完成 / C / D / B / E）+ Verify
**Phase 顺序**: C → D → B → E → Verify
**baseline**: F098 (origin/master c2e97d5)
**计划版本**: v0.1

---

## §-1 前置验证报告（P-VAL-1 + P-VAL-2）

### P-VAL-1：ApprovalGate 超时机制验证

**验证命令**：

```bash
rg -n "timeout|time_out|expire" octoagent/apps/gateway/src/octoagent/gateway/harness/approval_gate.py
```

**关键输出**（摘录）：

```
9:   - 拒绝时 Agent 收到明确 rejected（不静默 timeout）
264: timeout_seconds: float = 300.0,
269: 或在 timeout_seconds 后返回 "rejected"（不静默超时）。
280: await asyncio.wait_for(handle._event.wait(), timeout=timeout_seconds)
281: except asyncio.TimeoutError:
283: "approval_gate_timeout",
289: handle.decision = "rejected"
290: handle.operator = "system_timeout"
292: # F27 修复：timeout 路径必须写 APPROVAL_DECIDED 终态事件
304: "reason": f"timeout_after_{timeout_seconds}s",
```

**验证结论**：**YES（假设成立）**

- `ApprovalGate.wait_for_decision(handle, timeout_seconds=300.0)` 有完整超时机制
- 超时时返回 `"rejected"`（字符串），不 raise
- 超时路径写 `APPROVAL_DECIDED` 终态事件（Constitution C2 合规）
- **对 plan 的影响**：无需在 Phase B 的 `escalate_permission` handler 中自建超时逻辑，直接调用 `approval_gate.wait_for_decision()` 即可。FR-B3 中 "超时返回 'timeout'" 的描述需修正为"超时返回 'rejected'"（与 ApprovalGate 实际行为对齐）。

---

### P-VAL-2：compaction 对 tool_call/tool_result 对的保护验证

**验证命令**：

```bash
rg -n "tool_call|tool_result" octoagent/apps/gateway/src/octoagent/gateway/services/context_compaction.py
```

**关键输出**：`No matches found`

**深入分析** — `_load_conversation_turns` 实测（context_compaction.py:807-837）：

```python
async def _load_conversation_turns(self, task_id: str) -> list[ConversationTurn]:
    events = await self._stores.event_store.get_events_for_task(task_id)
    turns: list[ConversationTurn] = []
    for event in events:
        if event.type is EventType.USER_MESSAGE:
            # → ConversationTurn(role="user", content=text)
        if event.type is EventType.MODEL_CALL_COMPLETED:
            content = await self._load_assistant_content(event.payload)
            # content = response_summary 字符串（经 _load_assistant_content 处理）
            # → ConversationTurn(role="assistant", content=content)
    return turns
```

**ConversationTurn 结构**（context_compaction.py:120-126）：

```python
class ConversationTurn:
    role: str
    content: str   # 纯文本，无工具调用结构
    source_event_id: str
    artifact_ref: str = ""
```

**验证结论**：**PARTIAL（假设部分成立，但影响面小）**

**关键发现**：compaction 服务处理的是"摘要文本层"而非"LLM 消息协议层"：

1. `_load_conversation_turns` 仅提取 `USER_MESSAGE` 和 `MODEL_CALL_COMPLETED` 事件，不涉及工具调用协议层的 `tool_call/tool_result` JSON 结构
2. `ConversationTurn.content` 是纯文本字符串（response_summary），不包含 function call schema
3. Compaction 的压缩对象是这个文本摘要序列，而非 LLM API 的原始 messages 结构

**OD-F099-7 选 A（tool_result 路径）的安全性**：

ask_back 的上下文恢复路径是：`execution_console.request_input()` → asyncio.Queue 阻塞 → 返回用户输入文本 → broker 将文本作为 `tool_result` 注入 LLM 下一轮。

这个路径在 LLM 执行层（task_runner 的 LLM 调用循环），而 compaction 是在构建 LLM 上下文时从 Event Store 重建摘要文本。两个层次不同——compaction 不会破坏正在运行的 LLM turn 的 tool_call/tool_result 对。

**潜在风险**（已知，需记录）：若 ask_back 触发时任务进入 WAITING_INPUT 并等待足够长时间，compaction 有可能在等待期间运行并压缩掉历史摘要文本。但这不影响 LLM 在 ask_back 恢复时收到的 tool_result 内容——tool_result 是通过 asyncio.Queue 传递的实时数据，不走 Event Store 重建。

**对 plan 的影响**：**无需调整主体方案**。OD-F099-7 选 A 安全。Phase E 验证时补充一个测试确认"ask_back 等待期间 compaction 不影响恢复后 tool_result 的完整性"即可。不触发 GATE_DESIGN 回审。

---

## §0 GATE_DESIGN 锁定决议引用

（完整引自 spec.md §0，作为 plan 不可偏离的锁定基准）

| 决议 | 锁定结果 |
|------|----------|
| **G-1** OD-F099-1 ~ OD-F099-7 | 全部按推荐执行（B/B/B/B/A/B/A）|
| **G-2** FR-C1 (automation/user_channel 派生)| **保留 MUST**：完整 role/session_kind/agent_uri 派生（用户 override，F101 依赖此基础设施）|
| **G-3** ApprovalGate 超时 + compaction tool_call/tool_result 保护 | **plan 阶段已 grep 验证**（见 §-1）|
| **G-4** 跨 OD 命名混淆 | **plan 阶段必须写 §命名约定 章节 + 常量化** |

**OD 决策汇总**（plan 执行时不得偏离）：

| OD | 决议 | 关键约束 |
|----|------|---------|
| OD-F099-1 | B（复用 CONTROL_METADATA_UPDATED）| source 字段用常量 |
| OD-F099-2 | B（三工具各自独立 handler）| 不继承 BaseDelegation |
| OD-F099-3 | B（扩展 source_runtime_kind 枚举）| 不新增 A2AConversation 字段 |
| OD-F099-4 | B（所有 agent kind 均可调用）| `worker.` 前缀是惯例，不是访问控制 |
| OD-F099-5 | A（复用 ApprovalGate SSE 路径）| P-VAL-1 确认超时机制已存在 |
| OD-F099-6 | B（工具层注入，不在 plane 层）| delegate_task_tool + delegation_tools 两处 |
| OD-F099-7 | A（tool_result 路径）| P-VAL-2 确认安全 |

---

## §1 命名约定（GATE_DESIGN G-4 落实）

### 两套 source 字符串的语义边界

F099 涉及两套独立的 source 字符串约定，**绝对不得混用**：

| 字段 | 所在位置 | 类型 | 语义 | F099 扩展值 |
|------|---------|------|------|------------|
| **`source_runtime_kind`** | `envelope.metadata["source_runtime_kind"]`，也读 `runtime_metadata["source_runtime_kind"]` | 字符串枚举（通过常量定义） | **Caller 身份类型**——标识"是谁发起了这次 dispatch/spawn" | 新增 `"automation"` / `"user_channel"` |
| **`control_metadata_source`** | `ControlMetadataUpdatedPayload.source` 字段 | 自由字符串（通过常量固定） | **事件来源操作**——标识"是哪个工具触发了这次 CONTROL_METADATA_UPDATED emit" | 新增 `"worker_ask_back"` / `"worker_request_input"` / `"worker_escalate_permission"` |

### 常量定义位置

**新建 `packages/core/src/octoagent/core/models/source_kinds.py`**（推荐新建独立模块，避免 enums.py 继续膨胀）：

```python
# packages/core/src/octoagent/core/models/source_kinds.py
"""F099: source_runtime_kind 枚举常量 + control_metadata_source 操作字符串常量。

两套常量语义不同，必须分开：
- SOURCE_RUNTIME_KIND_* : caller 身份枚举（用于 dispatch_service._resolve_a2a_source_role）
- CONTROL_METADATA_SOURCE_* : 事件来源操作（用于 CONTROL_METADATA_UPDATED.source 字段）
"""

# --- Caller 身份枚举（source_runtime_kind）---
SOURCE_RUNTIME_KIND_MAIN = "main"
SOURCE_RUNTIME_KIND_WORKER = "worker"
SOURCE_RUNTIME_KIND_SUBAGENT = "subagent"
SOURCE_RUNTIME_KIND_AUTOMATION = "automation"       # F099 新增
SOURCE_RUNTIME_KIND_USER_CHANNEL = "user_channel"   # F099 新增

# --- 已知值集合（用于验证和降级判断）---
KNOWN_SOURCE_RUNTIME_KINDS: frozenset[str] = frozenset({
    SOURCE_RUNTIME_KIND_MAIN,
    SOURCE_RUNTIME_KIND_WORKER,
    SOURCE_RUNTIME_KIND_SUBAGENT,
    SOURCE_RUNTIME_KIND_AUTOMATION,
    SOURCE_RUNTIME_KIND_USER_CHANNEL,
})

# --- 事件来源操作（control_metadata_source）---
CONTROL_METADATA_SOURCE_SUBAGENT_DELEGATION_INIT = "subagent_delegation_init"
CONTROL_METADATA_SOURCE_SUBAGENT_DELEGATION_BACKFILL = "subagent_delegation_session_backfill"
CONTROL_METADATA_SOURCE_ASK_BACK = "worker_ask_back"           # F099 新增
CONTROL_METADATA_SOURCE_REQUEST_INPUT = "worker_request_input" # F099 新增
CONTROL_METADATA_SOURCE_ESCALATE_PERMISSION = "worker_escalate_permission"  # F099 新增
```

**枚举扩展位置**（AgentRuntimeRole + AgentSessionKind）：

`packages/core/src/octoagent/core/models/agent_context.py`（Phase C 扩展）：

```python
class AgentRuntimeRole(StrEnum):
    MAIN = "main"
    WORKER = "worker"
    AUTOMATION = "automation"       # F099 新增（G-2 要求）
    USER_CHANNEL = "user_channel"   # F099 新增（G-2 要求）


class AgentSessionKind(StrEnum):
    MAIN_BOOTSTRAP = "main_bootstrap"
    WORKER_INTERNAL = "worker_internal"
    DIRECT_WORKER = "direct_worker"
    SUBAGENT_INTERNAL = "subagent_internal"
    AUTOMATION_INTERNAL = "automation_internal"  # F099 新增（G-2 要求）
    USER_CHANNEL = "user_channel"               # F099 新增（G-2 要求）
```

### 三工具 handler 引用方式

三工具 handler 通过 `source_kinds.py` 常量引用（不硬编码字符串）：

```python
from octoagent.core.models.source_kinds import (
    CONTROL_METADATA_SOURCE_ASK_BACK,
    CONTROL_METADATA_SOURCE_REQUEST_INPUT,
    CONTROL_METADATA_SOURCE_ESCALATE_PERMISSION,
)
```

---

## §2 模块改动清单

### Codebase Reality Check（目标文件现状）

| 文件 | LOC（估算）| 公开接口数 | 已知 debt | F099 净增减 |
|------|-----------|-----------|----------|------------|
| `dispatch_service.py` | ~970 | 10+ | 无 | +40（扩展 `_resolve_a2a_source_role`）|
| `delegate_task_tool.py` | ~280 | 1（register）| 无 | +15（source 注入）|
| `delegation_tools.py` | ~320 | 1（register）| 无 | +15（source 注入）|
| `agent_context.py`（core）| ~155 | 4 枚举值 | 无 | +4（新增枚举值）|
| `ask_back_tools.py`（新建）| 0 | 0 | — | +200（新建）|
| `source_kinds.py`（新建）| 0 | 0 | — | +30（新建）|
| `payloads.py` | ~200 | ~12 | 无 | +5（补充 source 描述）|

**前置清理规则评估**：所有目标文件 LOC < 500，无需前置 cleanup task。

### 块 B：三工具新建

| 文件 | 操作 | 净增减 | 行为变更 |
|------|------|--------|---------|
| `builtin_tools/ask_back_tools.py`（新建）| 新建 | +200 | 新功能 |
| `builtin_tools/__init__.py` | 修改（register_all 加 ask_back_tools）| +3 | 新功能 |

### 块 C：source_runtime_kind 扩展 + spawn 注入

| 文件 | 操作 | 净增减 | 行为变更 |
|------|------|--------|---------|
| `packages/core/src/octoagent/core/models/source_kinds.py`（新建）| 新建 | +30 | 纯新增常量 |
| `packages/core/src/octoagent/core/models/agent_context.py` | 修改（AgentRuntimeRole + AgentSessionKind 新增 2 值）| +4 | 新枚举值 |
| `dispatch_service.py`（`_resolve_a2a_source_role`）| 修改（扩展 automation/user_channel 分支）| +40 | 新功能（fallback 行为零变更）|
| `delegate_task_tool.py`（delegate_task_handler）| 修改（spawn 前注入 source_runtime_kind）| +15 | F098 LOW §3 修复 |
| `delegation_tools.py`（subagents.spawn 路径）| 修改（spawn 前注入 source_runtime_kind）| +15 | F098 LOW §3 修复 |
| `packages/core/src/octoagent/core/models/__init__.py` | 修改（re-export source_kinds 模块）| +5 | 无 |

### 块 D：CONTROL_METADATA_UPDATED 扩展

| 文件 | 操作 | 净增减 | 行为变更 |
|------|------|--------|---------|
| `packages/core/src/octoagent/core/models/payloads.py` | 修改（补充 source 字段文档描述）| +5 | 文档变更，无 schema 变更 |

**注**：块 D 的实际 emit 逻辑在 ask_back_tools.py 中（三工具 handler 内）。Phase D 工作量极轻，主要是确保 source 候选值有常量定义 + payloads 文档更新。

### 块 E：端到端验证

| 文件 | 操作 | 净增减 |
|------|------|--------|
| `tests/services/test_ask_back_tools.py`（新建）| 新建 | +350 |
| `tests/services/test_phase_c_source_injection.py`（新建）| 新建 | +250 |
| `tests/services/test_phase_d_ask_back_audit.py`（新建）| 新建 | +200 |
| `tests/services/test_phase_e_ask_back_e2e.py`（新建）| 新建 | +200 |
| `tests/services/test_task_runner.py`（扩展）| 修改（ask_back turn 场景）| +50 |
| `tests/services/test_capability_pack_tools.py`（扩展）| 修改（新工具注册验证）| +30 |

---

## §3 Phase 拆分（C → D → B → E → Verify）

### Phase 依赖图

```
Phase A（已完成）: phase-0-recon.md
   │
   ├─→ Phase C（source 扩展 + spawn 注入）
   │         │
   │         ↓
   │     Phase D（CONTROL_METADATA_UPDATED 常量化 + payloads 文档）
   │         │
   │         ↓
   │     Phase B（三工具引入，依赖 D 的 audit emit 路径 + C 的常量）
   │         │
   │         ↓
   │     Phase E（端到端验证，依赖 B+C+D 全部就绪）
   │         │
   │         ↓
   └─→    Verify（Final cross-Phase Codex review + completion-report）
```

---

### Phase C：source_runtime_kind 扩展 + spawn 路径注入

**目标**：
1. 新建 `source_kinds.py` 常量模块
2. 扩展 `AgentRuntimeRole` + `AgentSessionKind` 枚举（automation + user_channel）
3. 扩展 `_resolve_a2a_source_role()` 处理新 source 值
4. 在 `delegate_task_tool.py` + `delegation_tools.py` 注入 `source_runtime_kind="worker"`（F098 LOW §3 修复）

**输入制品**：phase-0-recon.md + spec.md §3 块 C

**实施步骤**：

1. 新建 `packages/core/src/octoagent/core/models/source_kinds.py`（5 个 SOURCE_RUNTIME_KIND_* 常量 + 2 个 CONTROL_METADATA_SOURCE_* 常量）
2. 在 `agent_context.py` 扩展 `AgentRuntimeRole`：加 `AUTOMATION = "automation"` / `USER_CHANNEL = "user_channel"`
3. 在 `agent_context.py` 扩展 `AgentSessionKind`：加 `AUTOMATION_INTERNAL = "automation_internal"` / `USER_CHANNEL = "user_channel"`
4. 在 `packages/core/src/octoagent/core/models/__init__.py` re-export `source_kinds` 模块
5. 扩展 `dispatch_service._resolve_a2a_source_role()`：
   - 加 `automation` 分支：返回 `(AgentRuntimeRole.AUTOMATION, AgentSessionKind.AUTOMATION_INTERNAL, self._agent_uri(f"automation.{source_id}"))`
   - 加 `user_channel` 分支：返回 `(AgentRuntimeRole.USER_CHANNEL, AgentSessionKind.USER_CHANNEL, self._agent_uri(f"user.{channel_id}"))`
   - 加无效值降级：`source_runtime_kind not in KNOWN_SOURCE_RUNTIME_KINDS` → warning log + 默认 MAIN 路径（FR-C4）
6. 在 `delegate_task_tool.py` `spawn_child` 调用前注入：

   ```python
   # F099 Phase C: FR-C2 - worker→worker dispatch source 注入
   # 仅当 caller 是 worker 环境时注入（通过 deps.execution_context 检测）
   envelope_metadata_extra: dict[str, Any] = {}
   if deps._execution_context is not None:
       envelope_metadata_extra["source_runtime_kind"] = SOURCE_RUNTIME_KIND_WORKER
       if deps._execution_context.worker_capability:
           envelope_metadata_extra["source_worker_capability"] = deps._execution_context.worker_capability
   ```

7. 在 `delegation_tools.py` subagents.spawn 路径（行 ~150）同样注入（FR-C3）
8. 新建 `tests/services/test_phase_c_source_injection.py`

**单测文件**：`tests/services/test_phase_c_source_injection.py`

```python
# 测试函数清单：
test_source_runtime_kind_constants_defined      # 常量模块完整性
test_automation_role_enum_value                 # 新枚举值验证
test_user_channel_role_enum_value               # 新枚举值验证
test_resolve_source_role_worker()               # worker → WORKER/WORKER_INTERNAL
test_resolve_source_role_subagent()             # subagent → WORKER/WORKER_INTERNAL
test_resolve_source_role_automation()           # automation → AUTOMATION/AUTOMATION_INTERNAL
test_resolve_source_role_user_channel()         # user_channel → USER_CHANNEL/USER_CHANNEL
test_resolve_source_role_unknown_value_degrades_to_main()    # FR-C4 无效值降级
test_resolve_source_role_main_backward_compat()  # AC-C2 后向兼容
test_delegate_task_injects_worker_source_kind()  # FR-C2 delegate_task 注入
test_subagents_spawn_injects_worker_source_kind()  # FR-C3 subagents.spawn 注入
```

**验收检查（对应 spec §4 AC）**：
- AC-C1（source 注入修复）：Worker 调 `delegate_task` → source_runtime_kind="worker" 注入 → `_resolve_a2a_source_role` 返回 WORKER
- AC-C2（后向兼容）：主 Agent 调 `delegate_task`（无注入）→ MAIN 路径不变
- FR-C4 验证：无效 source_runtime_kind → MAIN 降级 + warning log emit

**估算**：
- LOC：+105（源码）+ 250（测试）= +355
- 难度：**LOW**（纯函数扩展 + 常量定义）
- Codex review 触发：Phase C commit 前 per-Phase review

**Phase 完成判断**：
```bash
# 1. 常量模块存在
grep -r "SOURCE_RUNTIME_KIND_AUTOMATION" packages/core/src/
# 2. 枚举扩展
grep "AUTOMATION" packages/core/src/octoagent/core/models/agent_context.py
# 3. 降级 warning log
grep "source_runtime_kind_unknown" apps/gateway/src/
# 4. 单测通过（11 测试函数）
pytest tests/services/test_phase_c_source_injection.py -v
```

---

### Phase D：CONTROL_METADATA_UPDATED 扩展（轻量）

**目标**：将 `ask_back` / `request_input` / `escalate_permission` 的 source 候选值记录到 `source_kinds.py`（已在 Phase C 新建），更新 `payloads.py` 的 source 字段文档描述。

**输入制品**：Phase C 完成（source_kinds.py 已建）

**实施步骤**：

1. 在 `source_kinds.py` 补充 `CONTROL_METADATA_SOURCE_ASK_BACK` / `CONTROL_METADATA_SOURCE_REQUEST_INPUT` / `CONTROL_METADATA_SOURCE_ESCALATE_PERMISSION` 三个常量（FR-D4 落实）
2. 在 `payloads.py` `ControlMetadataUpdatedPayload.source` 字段注释中补充 F099 新增候选值描述（无 schema 变更）：
   - `worker_ask_back`
   - `worker_request_input`
   - `worker_escalate_permission`
3. 确认 `merge_control_metadata`（`connection_metadata.py`）无需修改（Phase 0 实测确认：CONTROL_METADATA_UPDATED 已在合并路径中）
4. 新建 `tests/services/test_phase_d_ask_back_audit.py`（仅 schema/常量验证，实际 emit 测试在 Phase B 之后）

**单测文件**：`tests/services/test_phase_d_ask_back_audit.py`

```python
# 测试函数清单（Phase D 自身部分，emit 测试在 Phase B 之后运行）：
test_control_metadata_source_constants_defined    # 常量完整性
test_control_metadata_updated_payload_source_field  # schema 字段存在
test_merge_control_metadata_handles_control_metadata_updated  # 向后兼容（merge 已支持）
test_ask_back_audit_event_not_in_conversation_turns  # AC-D2 不污染对话历史（mock emit）
```

**验收检查**：
- FR-D4：`payloads.py` source 字段文档更新（文档变更，无 AC）
- 常量存在性：`grep CONTROL_METADATA_SOURCE_ASK_BACK packages/core/src/`

**估算**：
- LOC：+10（源码）+ 150（测试）= +160
- 难度：**LOW**（文档 + 常量补充，无逻辑变更）
- Codex review 触发：Phase D 与 C 可合并 commit（同 Phase review），也可单独 commit

**Phase 完成判断**：
```bash
grep "CONTROL_METADATA_SOURCE_ASK_BACK" packages/core/src/
grep "worker_ask_back" packages/core/src/octoagent/core/models/payloads.py
pytest tests/services/test_phase_d_ask_back_audit.py -v
```

---

### Phase B：三工具引入（主行为 Phase）

**目标**：新建 `ask_back_tools.py`，注册 `worker.ask_back` / `worker.request_input` / `worker.escalate_permission` 三个工具。

**输入制品**：Phase C + Phase D 完成（常量已定义）

**实施步骤**：

1. 新建 `apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py`

   **工具注册结构**（参考 `supervision_tools.py` + `delegate_task_tool.py` 模式）：

   ```python
   # 模块常量
   _ENTRYPOINTS = frozenset({"agent_runtime", "web"})

   # 内部辅助函数（OD-F099-2：不继承 BaseDelegation，用模块内私有函数）
   async def _emit_ask_back_audit(
       deps: ToolDeps,
       source: str,
       control_metadata: dict[str, Any],
   ) -> None:
       """emit CONTROL_METADATA_UPDATED（Constitution C2 合规）。"""
       ...

   async def register(broker: Any, deps: ToolDeps) -> None:
       # 注册三工具
   ```

   **worker.ask_back handler 核心逻辑**：

   ```python
   @tool_contract(name="worker.ask_back", side_effect_level=SideEffectLevel.REVERSIBLE, ...)
   async def ask_back_handler(question: str, context: str = "") -> str:
       # FR-B4: emit CONTROL_METADATA_UPDATED(source=CONTROL_METADATA_SOURCE_ASK_BACK)
       await _emit_ask_back_audit(deps, source=CONTROL_METADATA_SOURCE_ASK_BACK,
           control_metadata={"ask_back_question": question, "ask_back_context": context, ...})
       # FR-B1: 调用 execution_context.request_input() → RUNNING → WAITING_INPUT → RUNNING
       # OD-F099-7 选 A: tool_result 路径，返回用户输入文本
       result = await deps.execution_context.request_input(
           prompt=question, actor="worker:ask_back"
       )
       return result or ""
   ```

   **worker.request_input handler 核心逻辑**（类似 ask_back，不同 source 值 + prompt 参数）

   **worker.escalate_permission handler 核心逻辑**：

   ```python
   @tool_contract(name="worker.escalate_permission", side_effect_level=SideEffectLevel.IRREVERSIBLE, ...)
   async def escalate_permission_handler(action: str, scope: str, reason: str) -> str:
       # FR-D3: emit CONTROL_METADATA_UPDATED(source=CONTROL_METADATA_SOURCE_ESCALATE_PERMISSION)
       await _emit_ask_back_audit(deps, source=CONTROL_METADATA_SOURCE_ESCALATE_PERMISSION,
           control_metadata={"escalate_action": action, "escalate_scope": scope, "escalate_reason": reason})
       # OD-F099-5 选 A: 复用 ApprovalGate SSE 路径（P-VAL-1 确认超时机制存在）
       if deps._approval_gate is None:
           return "rejected"  # ApprovalGate 不可用，降级拒绝（Constitution C6 Degrade Gracefully）
       handle = await deps.approval_gate.request_approval(
           task_id=..., tool_name="worker.escalate_permission",
           operation={"action": action, "scope": scope, "reason": reason},
       )
       # wait_for_decision 默认 timeout_seconds=300.0（P-VAL-1：超时返回 "rejected"，不 raise）
       decision = await deps.approval_gate.wait_for_decision(handle)
       return decision  # "approved" 或 "rejected"
   ```

2. 在 `builtin_tools/__init__.py` 加入 `ask_back_tools` 注册

   ```python
   from . import ask_back_tools
   # register_all 中加：await ask_back_tools.register(broker, deps)
   ```

3. 新建 `tests/services/test_ask_back_tools.py`（主测试文件，12-15 个测试函数）

**单测文件**：`tests/services/test_ask_back_tools.py`

```python
# 测试函数清单（对应 AC-B1 ~ AC-G4）：
test_ask_back_tool_registered()                  # AC-B1 工具名注册
test_request_input_tool_registered()             # AC-B1
test_escalate_permission_tool_registered()       # AC-B1
test_tool_entrypoints_include_agent_runtime()    # AC-B1 entrypoints 验证
test_ask_back_sets_waiting_input()               # AC-B2 状态变化
test_ask_back_returns_user_answer()              # AC-B3 上下文恢复
test_ask_back_does_not_raise()                   # FR-B1 不 raise
test_request_input_returns_text()                # FR-B2
test_escalate_permission_approved_path()         # AC-B4 + AC-B5（审批通过）
test_escalate_permission_rejected_path()         # AC-B5（审批拒绝）
test_escalate_permission_timeout_returns_rejected()  # FR-B3 超时返回 rejected（P-VAL-1）
test_escalate_permission_gate_unavailable_returns_rejected()  # Constitution C6 降级
test_ask_back_emits_control_metadata_updated()   # FR-B4 + AC-D1
test_request_input_emits_audit()                 # FR-B4
test_escalate_permission_emits_audit()           # FR-D3
```

**验收检查**：
- AC-B1：broker 注册查询通过
- AC-B2：ask_back → task.status == WAITING_INPUT
- AC-B3：attach_input → turn N+1 tool_result 包含用户回答
- AC-B4：escalate_permission → WAITING_APPROVAL + approval_id 不为空
- AC-B5（补充 AC）：approved/rejected 返回值验证 + 不 raise
- AC-D1：CONTROL_METADATA_UPDATED emit 验证
- AC-G3：Constitution C4/C7/C10 合规（escalate_permission 走 ApprovalGate）

**估算**：
- LOC：+210（ask_back_tools.py）+ 5（__init__.py）+ 380（测试）= +595
- 难度：**MED**（工具注册模式已有参考，核心是 execution_context + ApprovalGate 接入）
- Codex review 触发：Phase B commit 前 per-Phase review（重点检查 tool_contract schema + ApprovalGate 接入正确性）

**Phase 完成判断**：

```bash
# 1. 工具注册
grep -r "worker.ask_back\|worker.request_input\|worker.escalate_permission" apps/gateway/src/
# 2. 工具文件存在
ls apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py
# 3. 单测通过（15 测试函数）
pytest tests/services/test_ask_back_tools.py -v
# 4. 全量回归
pytest --tb=short -q | tail -5
```

---

### Phase E：端到端验证 + 单测补全

**目标**：验证 B+C+D 三 Phase 的联合正确性；补全 test_phase_d（emit 部分）+ test_phase_e（端到端）。

**输入制品**：Phase B + C + D 全部完成

**实施步骤**：

1. 补全 `tests/services/test_phase_d_ask_back_audit.py`（Phase D 预建，补 emit 断言）：
   - `test_ask_back_control_metadata_updated_not_in_conversation_turns`（AC-D2）
   - `test_ask_back_control_metadata_source_field`（AC-D1）
   - `test_escalate_permission_control_metadata_source_field`（FR-D3）

2. 新建 `tests/services/test_phase_e_ask_back_e2e.py`（端到端集成）：

   ```python
   # 测试函数清单：
   test_e2e_ask_back_full_cycle_running_waiting_running()       # AC-E1
   test_e2e_ask_back_event_store_three_events()                 # AC-E1 + FR-E3
   test_e2e_ask_back_tool_result_contains_user_answer()         # FR-E2 + AC-B3
   test_e2e_ask_back_tool_call_id_matches_tool_result()         # FR-E2 精确验证
   test_e2e_escalate_permission_approval_flow()                 # FR-E4 + AC-G3
   test_e2e_compaction_during_waiting_input_safe()              # P-VAL-2 风险缓解验证
   ```

3. 扩展 `tests/services/test_task_runner.py`：
   - `test_task_runner_ask_back_state_transition`（worker ask_back 在 task_runner 层的流程）

4. 扩展 `tests/services/test_capability_pack_tools.py`：
   - `test_ask_back_tools_in_broker_registration`

**验收检查**：
- AC-E1：Event Store 三条连续事件：TASK_STATE_CHANGED(RUNNING→WAITING_INPUT) + CONTROL_METADATA_UPDATED(ask_back) + TASK_STATE_CHANGED(WAITING_INPUT→RUNNING)
- FR-E2：tool_call_id 匹配验证
- FR-E3：Event Store audit trace 完整
- AC-G1：全量回归 ≥ F098 baseline passed 数

**估算**：
- LOC：+100（补 test_phase_d）+ 250（test_phase_e）+ 80（扩展现有测试）= +430
- 难度：**MED**（端到端需 mock execution_console + event_store + task_runner 联动）
- Codex review 触发：Phase E commit 前 per-Phase review

**Phase 完成判断**：

```bash
# 1. 端到端测试全部通过
pytest tests/services/test_phase_e_ask_back_e2e.py -v
# 2. 全量回归
pytest --tb=short -q | grep "passed\|failed"
# 3. e2e_smoke
pytest -m e2e_smoke --timeout=180
```

---

## §4 跨 Phase 风险

| 风险 | 严重度 | 触发 Phase | 缓解步骤位置 | 验证方式 |
|------|--------|------------|------------|---------|
| **R1 spawn 路径 source 注入破 baseline**：worker 注入条件判断不准，主 Agent dispatch 被误判为 worker | HIGH | Phase C | `delegate_task_tool.py`：仅在 `deps._execution_context is not None` 且存在 worker_capability 时注入；无信号时不注入（default main）| `test_resolve_source_role_main_backward_compat` + AC-C2 |
| **R2 escalate_permission ApprovalGate 超时处理**：P-VAL-1 验证超时返回 "rejected"，FR-B3 描述需对齐 | LOW | Phase B | ask_back_tools.py 直接用 `wait_for_decision(timeout_seconds=300.0)` 的返回值（"approved"/"rejected"）；测试 `test_escalate_permission_timeout_returns_rejected` | Phase B 测试覆盖 |
| **R3 compaction 期间 ask_back 等待**：P-VAL-2 确认安全，但需记录为已知 risk | LOW | Phase E | `test_e2e_compaction_during_waiting_input_safe`（Phase E 步骤 2 第 6 项）| Phase E 测试 |
| **R4 ApprovalGate 不可用时 escalate_permission 挂起** | LOW | Phase B | handler 加 `if deps._approval_gate is None: return "rejected"`（Constitution C6 Degrade Gracefully）| `test_escalate_permission_gate_unavailable_returns_rejected` |
| **R5 两处 spawn 注入不一致**（delegate_task_tool + delegation_tools）| MED | Phase C | 提取共用 `_inject_worker_source_metadata(deps, envelope_extra)` 辅助函数，两处调用同一实现 | `test_delegate_task_injects_*` + `test_subagents_spawn_injects_*` |
| **R6 automation/user_channel agent_uri 派生**：source_id / channel_id 字段从哪里取？| MED | Phase C | `_resolve_a2a_source_role` 中：automation 用 `envelope_metadata.get("source_automation_id", "unknown")`；user_channel 用 `envelope_metadata.get("source_channel_id", "unknown")`；dispatch 侧调用方负责注入 | AC-C1 automation/user_channel 路径验证 |

---

## §5 测试策略

| 测试文件 | 测试函数（粗粒度）| AC 映射 |
|---------|---------------|---------|
| `tests/services/test_phase_c_source_injection.py`（新建）| 11 函数：常量验证 / 枚举验证 / 各 source 值派生 / 降级行为 / 后向兼容 / spawn 注入 | AC-C1, AC-C2, FR-C4 |
| `tests/services/test_phase_d_ask_back_audit.py`（新建）| 7 函数：常量验证 / schema 验证 / merge 向后兼容 / 不污染对话历史 / emit source 验证 | AC-D1, AC-D2, FR-D3 |
| `tests/services/test_ask_back_tools.py`（新建）| 15 函数：工具注册 / 状态变化 / 上下文恢复 / 审批路径（approved/rejected/timeout）/ audit emit | AC-B1~B5, AC-D1, AC-G3, AC-G4 |
| `tests/services/test_phase_e_ask_back_e2e.py`（新建）| 6 函数：完整生命周期 / 三条 Event Store 事件 / tool_result 验证 / tool_call_id 匹配 / escalate e2e / compaction 安全 | AC-E1, FR-E2, FR-E3, FR-E4 |
| `tests/services/test_task_runner.py`（扩展）| +1 函数：ask_back 在 task_runner 层的流程 | FR-B1, FR-B2 |
| `tests/services/test_capability_pack_tools.py`（扩展）| +1 函数：新工具注册验证 | AC-B1 |

**集成测覆盖**：`test_phase_e_ask_back_e2e.py` 兼集成测角色（联合 execution_console + event_store + task_runner）。

**e2e_smoke 影响**：F099 不新增 e2e_smoke 能力域。若 `delegate_task` smoke 域测试断言需更新（如验证 source audit），在 Phase C 时更新现有测试。

---

## §6 Codex review 触发计划

| 节点 | 触发时机 | 范围 | 模式 |
|------|---------|------|------|
| **pre-Impl review** | plan.md v0.1 产出后（tasks agent 生成 tasks.md 之前）| spec.md v0.2 + plan.md v0.1 联合审查：OD 决策是否有遗漏风险 / Phase 拆分是否合理 / 命名约定是否充分 | background |
| **Phase C per-review** | Phase C commit 前 | source 枚举扩展 + spawn 注入逻辑：injection 条件是否严格 / automation/user_channel 路径完整性 | foreground |
| **Phase B per-review** | Phase B commit 前 | ask_back_tools.py：tool_contract schema / ApprovalGate 接入 / execution_context.request_input 调用链 / _emit_ask_back_audit 是否覆盖三工具 | foreground |
| **Phase E per-review** | Phase E commit 前 | 端到端测试覆盖维度：是否覆盖 tool_call_id 匹配 / Event Store 三条事件顺序 / compaction 安全 | foreground |
| **Final cross-Phase review** | 所有 Phase commit 完成后、Verify 阶段 | 全量改动 + spec.md §4 AC 逐条对照：实现是否偏离 spec / 是否引入隐性技术债 / F098 OD-1~OD-9 不偏离验证 | background（F098 实证最终 review 抓到 high bug）|

**处理规则**（参照 CLAUDE.local.md §Codex Adversarial Review 强制规则）：
- high/medium finding：接受 → 改；拒绝 → commit message 写明理由
- low：可 ignored，但 commit message 注明
- 处理到 0 high 残留才 commit

---

## §7 Phase 跳过预案

若实施中发现某 Phase baseline 已通（沿用 F093 "baseline 部分已通" pattern），必须：

1. 用 grep 实测验证（不允许凭记忆断言）
2. commit message 显式写明：`"Phase X 跳过，理由：baseline grep 验证 ${命令} 已通过，影响：${影响}"`
3. completion-report.md 记录跳过 Phase + 理由

**当前已知跳过可能**：
- Phase D 工作量极小（~+10 LOC 源码），若在 Phase C 中一并完成（source_kinds.py 内同时定义 CONTROL_METADATA_SOURCE_* 常量），Phase D 的独立 commit 可以合并到 Phase C（归档为 Phase C+D 联合 commit，不视为跳过）

---

## §8 Verify Phase 内容

**触发条件**：Phase C + D + B + E 全部 commit 完成，0 high Codex finding 残留。

**Verify Phase 步骤**：

1. **Final cross-Phase Codex review**（background）：
   - 输入：spec.md v0.2 + plan.md v0.1 + 全部 Phase diff
   - 重点检查：F098 OD-1~OD-9 不偏离 / ask_back 在 WAITING_INPUT 与现有状态机兼容 / automation/user_channel 派生是否影响其他 consumer

2. **全量回归**：
   - `pytest --tb=short -q` → ≥ F098 baseline c2e97d5 passed 数
   - 新增测试约 40 个函数，预期 passed 数净增约 +40

3. **e2e_smoke 5x 循环**：
   - `pytest -m e2e_smoke --timeout=180` × 5 轮，全部通过

4. **completion-report.md 产出**（`.specify/features/099-ask-back-source-generalization/completion-report.md`）：
   - 对照 plan §3 Phase 列表标注"实际做了 vs 计划"
   - Phase 跳过 / 偏离的显式说明
   - Codex review 闭环表（per-Phase + Final）

5. **handoff.md 产出**（`.specify/features/099-ask-back-source-generalization/handoff.md`），对 F100 关键信息：
   - `worker.ask_back` 三工具现状：工具名 / entrypoints / handler 路径
   - `source_runtime_kind` 已定义枚举值（5 个）+ 扩展位置（`source_kinds.py`）
   - `_resolve_a2a_source_role()` 现在处理的 source 值范围
   - F100 Decision Loop Alignment 的接入点：`recall_planner` 与 ask_back 工具的交互现状（ask_back 挂起时 recall_planner 行为）
   - `RecallPlannerMode="auto"` 启用接入点（F100 主责，F099 只标注位置）

---

## §9 完成判定

全部 AC 通过（对应 spec.md §4）：

| AC | 验证方式 |
|----|---------|
| **AC-G1**（0 regression）| `pytest --tb=short -q` ≥ F098 baseline + 0 failure |
| **AC-G2**（OD-1~OD-9 不偏离）| Final Codex review + grep 验证 F098 关键函数无变更 |
| **AC-G3**（Constitution C4/C7/C10 合规）| escalate_permission → ApprovalGate 路径测试通过 |
| **AC-G4**（audit trace 完整）| Event Store 三工具调用均有 CONTROL_METADATA_UPDATED 记录 |
| **AC-B1**（三工具注册）| broker 查询通过 |
| **AC-B2**（ask_back → WAITING_INPUT）| test_ask_back_sets_waiting_input PASS |
| **AC-B3**（上下文恢复）| test_e2e_ask_back_tool_result_contains_user_answer PASS |
| **AC-B4**（escalate_permission WAITING_APPROVAL）| test_escalate_permission_approved_path PASS |
| **AC-C1**（source 注入修复）| test_delegate_task_injects_worker_source_kind PASS |
| **AC-C2**（后向兼容）| test_resolve_source_role_main_backward_compat PASS |
| **AC-D1**（audit trace）| test_ask_back_control_metadata_source_field PASS |
| **AC-D2**（不污染对话历史）| test_ask_back_audit_event_not_in_conversation_turns PASS |
| **AC-E1**（端到端流程）| test_e2e_ask_back_full_cycle_running_waiting_running PASS |

---

## §10 Impact Assessment

**影响文件数**：
- 直接修改：7（source_kinds.py 新建 + agent_context.py + dispatch_service.py + delegate_task_tool.py + delegation_tools.py + ask_back_tools.py 新建 + payloads.py）
- 间接受影响：3（__init__.py re-export + builtin_tools/__init__.py + payloads 消费方）

**跨包影响**：1（`packages/core`：agent_context.py 枚举扩展 + source_kinds.py 新建）

**数据迁移**：无（仅枚举扩展，向后兼容；CONTROL_METADATA_UPDATED schema 无变更）

**API/契约变更**：`_resolve_a2a_source_role()` 新增返回值分支（向后兼容，原 main 路径不变）；三工具新增（向后兼容）

**风险等级**：**LOW**（影响文件 < 10，跨包影响 = 1，无数据迁移，无公共 API 破坏）

---

## §11 Constitution Check

| 宪法原则 | 适用性 | 评估 | 说明 |
|---------|--------|------|------|
| C1 Durability First | 适用 | PASS | ask_back → WAITING_INPUT 写盘；重启恢复路径已存在（task_runner._spawn_job resume_from_node）|
| C2 Everything is an Event | 适用 | PASS | 三工具调用均 emit CONTROL_METADATA_UPDATED（FR-B4/D1/D3）；TASK_STATE_CHANGED 由现有状态机 emit |
| C3 Tools are Contracts | 适用 | PASS | `@tool_contract` 装饰器保证 schema 与代码签名一致；side_effect_level 声明 |
| C4 Side-effect Must be Two-Phase | 适用 | PASS | escalate_permission 走 ApprovalGate Plan→Gate→Execute；ask_back 本身是暂停型（不可逆副作用通过 escalate 路径处理）|
| C5 Least Privilege | 低适用 | PASS | 三工具不涉及 secrets 访问 |
| C6 Degrade Gracefully | 适用 | PASS | ApprovalGate 不可用时 escalate_permission 返回 "rejected"（不崩溃）|
| C7 User-in-Control | 适用 | PASS | escalate_permission SSE 推送审批卡片；超时返回 "rejected" 而非静默执行 |
| C8 Observability | 适用 | PASS | CONTROL_METADATA_UPDATED audit + TASK_STATE_CHANGED 审计链完整 |
| C9 Agent Autonomy | 适用 | PASS | 工具调用时机由 LLM 自主决策；系统只提供工具集 |
| C10 Policy-Driven Access | 适用 | PASS | escalate_permission 经 `ApprovalGate`（PolicyAction 路径，OD-F099-5 选 A）|

**Constitution Check 结论**：全部 PASS，无 VIOLATION，无需豁免。

---

## §12 Complexity Tracking（偏离简单方案的决策）

| 决策 | 简单方案 | 实际方案 | 偏离理由 |
|------|---------|---------|---------|
| automation/user_channel 完整派生 | 仅定义常量 + fallback | 完整派生（role/session_kind/agent_uri）| GATE_DESIGN G-2 用户 override：F101 依赖此基础设施，前置便宜 |
| source_kinds.py 独立模块 | 直接加到 enums.py | 新建独立模块 | enums.py 已有 200+ 行，语义独立的常量模块更清晰 |
| escalate_permission 超时处理 | 自建 asyncio.wait_for | 复用 ApprovalGate.wait_for_decision | P-VAL-1 验证：ApprovalGate 已有超时机制（默认 300s）|

---

v0.1 - 待 GATE_TASKS 审查
