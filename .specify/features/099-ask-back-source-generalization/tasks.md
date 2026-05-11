# F099 Ask-Back Channel + Source Generalization — 任务清单

**关联**: spec.md v0.2（GATE_DESIGN 已锁）+ plan.md v0.1
**Phase 顺序**: C → D → B → E → Verify
**总任务数**: 36（Phase A 5 已完成 + 实施阶段 31）
**估算实施时间**: 12-18h
**baseline**: F098 (origin/master c2e97d5)

---

## Phase A — 实测侦察（已完成）

- [x] T-A-1: phase-0-recon.md 产出（ApprovalGate/compaction/request_input 路径实测）
- [x] T-A-2: P-VAL-1 ApprovalGate 超时机制 grep 验证（结论：YES，300s timeout 返回 "rejected"）
- [x] T-A-3: P-VAL-2 compaction tool_call/tool_result 保护 grep 验证（结论：PARTIAL，OD-F099-7 选 A 安全）
- [x] T-A-4: GATE_DESIGN 候选分析 + spec v0.2 锁定
- [x] T-A-5: plan.md v0.1 产出（§1 命名约定 + §2 模块清单 + §3 Phase 拆分）

---

## Phase C — source_runtime_kind 扩展 + spawn 路径注入

**目标**: 新建常量模块，扩展枚举，修复 F098 已知 LOW §3（worker→worker spawn 注入缺失），支持 automation/user_channel 两个新 source 路径。

**关键依赖**: Phase A 已完成（无其他前置）

### T-C-1：新建 source_kinds.py 常量模块

- **Phase**: C
- **依赖**: 无
- **目标**: 新建 `packages/core/src/octoagent/core/models/source_kinds.py`，定义两套常量（caller 身份枚举 + 事件来源操作字符串），为后续所有 Phase 提供单一事实源
- **改动文件**:
  - `packages/core/src/octoagent/core/models/source_kinds.py`（新建）
- **实施步骤**:
  1. 新建文件，写模块 docstring 说明两套常量的语义边界（不得混用）
  2. 定义 5 个 `SOURCE_RUNTIME_KIND_*` 常量：`main / worker / subagent / automation / user_channel`
  3. 定义 `KNOWN_SOURCE_RUNTIME_KINDS: frozenset[str]` 包含全部 5 个值
  4. 定义 5 个 `CONTROL_METADATA_SOURCE_*` 常量：`subagent_delegation_init / subagent_delegation_session_backfill / worker_ask_back / worker_request_input / worker_escalate_permission`
- **验收**:
  - [x] `grep SOURCE_RUNTIME_KIND_AUTOMATION packages/core/src/octoagent/core/models/source_kinds.py` 有输出
  - [x] `grep CONTROL_METADATA_SOURCE_ASK_BACK packages/core/src/octoagent/core/models/source_kinds.py` 有输出
  - [x] `grep KNOWN_SOURCE_RUNTIME_KINDS packages/core/src/octoagent/core/models/source_kinds.py` 有输出
- **预估**: +30 LOC，30min
- **可合并 commit**: T-C-1 + T-C-2 + T-C-3 可合一个 commit（同属常量/枚举定义）

---

### T-C-2：扩展 AgentRuntimeRole + AgentSessionKind 枚举

- **Phase**: C
- **依赖**: T-C-1（source_kinds.py 需先建，保持命名对齐）
- **目标**: 在 `agent_context.py` 追加 AUTOMATION / USER_CHANNEL 枚举值，满足 GATE_DESIGN G-2 要求
- **改动文件**:
  - `packages/core/src/octoagent/core/models/agent_context.py`（修改）
- **实施步骤**:
  1. 读取 `agent_context.py` 找到 `AgentRuntimeRole(StrEnum)` 定义位置
  2. 追加 `AUTOMATION = "automation"` 和 `USER_CHANNEL = "user_channel"` 两个值
  3. 找到 `AgentSessionKind(StrEnum)` 定义位置
  4. 追加 `AUTOMATION_INTERNAL = "automation_internal"` 和 `USER_CHANNEL = "user_channel"` 两个值
- **验收**:
  - [x] `grep 'AUTOMATION' packages/core/src/octoagent/core/models/agent_context.py` 有 2 处以上
  - [x] `grep 'USER_CHANNEL' packages/core/src/octoagent/core/models/agent_context.py` 有 2 处以上
  - [x] Python import 无报错：`python3 -c "from octoagent.core.models.agent_context import AgentRuntimeRole, AgentSessionKind; print(AgentRuntimeRole.AUTOMATION)"`
- **预估**: +4 LOC，15min
- **可合并 commit**: T-C-1 + T-C-2 + T-C-3

---

### T-C-3：在 core models __init__.py re-export source_kinds 模块

- **Phase**: C
- **依赖**: T-C-1
- **目标**: 确保 `from octoagent.core.models import source_kinds` 可正常 import
- **改动文件**:
  - `packages/core/src/octoagent/core/models/__init__.py`（修改）
- **实施步骤**:
  1. 读取 `packages/core/src/octoagent/core/models/__init__.py`
  2. 添加 `from . import source_kinds` 或等效 re-export
- **验收**:
  - [x] `python3 -c "from octoagent.core.models import source_kinds; print(source_kinds.SOURCE_RUNTIME_KIND_WORKER)"` 无报错
- **预估**: +5 LOC，10min
- **可合并 commit**: T-C-1 + T-C-2 + T-C-3

---

### T-C-4：扩展 dispatch_service._resolve_a2a_source_role()

- **Phase**: C
- **依赖**: T-C-1、T-C-2（常量 + 枚举就绪）
- **目标**: 在 `dispatch_service.py` 中扩展 `_resolve_a2a_source_role()` 函数，新增 automation / user_channel 分支，并加无效值降级（FR-C1 + FR-C4）
- **改动文件**:
  - `apps/gateway/src/octoagent/gateway/services/dispatch_service.py`（修改 `_resolve_a2a_source_role` 函数）
- **实施步骤**:
  1. 读取 `dispatch_service.py` 第 858-876 行（`_resolve_a2a_source_role` 函数范围）
  2. 在现有 worker/subagent/main 分支后追加：
     - `automation` 分支：返回 `(AgentRuntimeRole.AUTOMATION, AgentSessionKind.AUTOMATION_INTERNAL, self._agent_uri(f"automation.{envelope_metadata.get('source_automation_id', 'unknown')}"))`
     - `user_channel` 分支：返回 `(AgentRuntimeRole.USER_CHANNEL, AgentSessionKind.USER_CHANNEL, self._agent_uri(f"user.{envelope_metadata.get('source_channel_id', 'unknown')}"))`
  3. 加无效值降级分支（FR-C4）：`if source_runtime_kind not in KNOWN_SOURCE_RUNTIME_KINDS: logger.warning("source_runtime_kind_unknown", source_runtime_kind=source_runtime_kind); return (AgentRuntimeRole.MAIN, ...)`
  4. 在文件顶部 import 中加 `from octoagent.core.models.source_kinds import KNOWN_SOURCE_RUNTIME_KINDS, SOURCE_RUNTIME_KIND_AUTOMATION, SOURCE_RUNTIME_KIND_USER_CHANNEL`
- **验收**:
  - [x] `grep 'AUTOMATION_INTERNAL' apps/gateway/src/octoagent/gateway/services/dispatch_service.py` 有输出
  - [x] `grep 'source_runtime_kind_unknown' apps/gateway/src/octoagent/gateway/services/dispatch_service.py` 有输出（降级 warning log key）
  - [x] 现有主 Agent → MAIN 路径无变更（AC-C2 后向兼容）
- **预估**: +40 LOC，45min
- **可合并 commit**: 独立（改动集中在一个函数，逻辑独立）

---

### T-C-5：提取辅助函数 _inject_worker_source_metadata 并在 delegate_task_tool.py 注入

- **Phase**: C
- **依赖**: T-C-1（SOURCE_RUNTIME_KIND_WORKER 常量就绪）
- **目标**: 在 `delegate_task_tool.py` 的 `spawn_child` 调用前注入 `source_runtime_kind="worker"`，修复 F098 LOW §3（FR-C2）
- **改动文件**:
  - `apps/gateway/src/octoagent/gateway/services/builtin_tools/delegate_task_tool.py`（修改 handler）
- **实施步骤**:
  1. 读取 `delegate_task_tool.py` 找到 `spawn_child` 调用位置
  2. 在调用前构建 `envelope_metadata_extra` dict：
     ```python
     envelope_metadata_extra: dict[str, Any] = {}
     if deps._execution_context is not None:
         envelope_metadata_extra["source_runtime_kind"] = SOURCE_RUNTIME_KIND_WORKER
         if getattr(deps._execution_context, "worker_capability", None):
             envelope_metadata_extra["source_worker_capability"] = deps._execution_context.worker_capability
     ```
  3. 将 `envelope_metadata_extra` 合并到 `spawn_child` 调用的 envelope metadata 参数中
  4. 在文件顶部 import `SOURCE_RUNTIME_KIND_WORKER` from source_kinds
- **验收**:
  - [x] `grep 'source_runtime_kind' apps/gateway/src/octoagent/gateway/services/builtin_tools/delegate_task_tool.py` 有输出
  - [x] 仅在 worker 环境（runtime_kind=="worker"）条件下注入（避免主 Agent 误判）
- **预估**: +15 LOC，30min
- **可合并 commit**: T-C-5 + T-C-6（同属 spawn 注入，可一个 commit）

---

### T-C-6：在 delegation_tools.py subagents.spawn 路径注入 source_runtime_kind

- **Phase**: C
- **依赖**: T-C-1、T-C-5（参考同一注入模式）
- **目标**: 在 `delegation_tools.py` subagents.spawn 路径（约行 150）同样注入 `source_runtime_kind="worker"`（FR-C3）
- **改动文件**:
  - `apps/gateway/src/octoagent/gateway/services/builtin_tools/delegation_tools.py`（修改 subagents.spawn handler）
- **实施步骤**:
  1. 读取 `delegation_tools.py` 找到 subagents.spawn handler 的 spawn_child 调用位置（约行 150）
  2. 复用与 T-C-5 相同的注入条件逻辑（`deps._execution_context is not None`）
  3. 注入 `source_runtime_kind="worker"` 到 envelope metadata
  4. 在文件顶部 import `SOURCE_RUNTIME_KIND_WORKER`
- **验收**:
  - [x] `grep 'source_runtime_kind' apps/gateway/src/octoagent/gateway/services/builtin_tools/delegation_tools.py` 有输出
  - [x] 注入条件与 delegate_task_tool.py 保持一致（仅在 worker 环境下注入）
- **预估**: +15 LOC，20min
- **可合并 commit**: T-C-5 + T-C-6

---

### T-C-7：新建 tests/services/test_phase_c_source_injection.py（11 测试函数）

- **Phase**: C
- **依赖**: T-C-1、T-C-2、T-C-4、T-C-5、T-C-6
- **目标**: 覆盖 AC-C1 / AC-C2 / FR-C4 + 常量/枚举/注入验证，共 11 个测试函数
- **改动文件**:
  - `tests/services/test_phase_c_source_injection.py`（新建）
- **实施步骤**:
  1. 新建测试文件，添加以下 11 个测试函数：
     - `test_source_runtime_kind_constants_defined`：验证 source_kinds.py 包含全部 5 个 SOURCE_RUNTIME_KIND_* 常量
     - `test_known_source_runtime_kinds_set`：验证 KNOWN_SOURCE_RUNTIME_KINDS 包含全部 5 个值
     - `test_automation_role_enum_value`：AgentRuntimeRole.AUTOMATION == "automation"
     - `test_user_channel_role_enum_value`：AgentRuntimeRole.USER_CHANNEL == "user_channel"
     - `test_automation_session_kind_enum_value`：AgentSessionKind.AUTOMATION_INTERNAL == "automation_internal"
     - `test_resolve_source_role_automation`：mock `_resolve_a2a_source_role`，automation → AUTOMATION/AUTOMATION_INTERNAL
     - `test_resolve_source_role_user_channel`：user_channel → USER_CHANNEL/USER_CHANNEL
     - `test_resolve_source_role_unknown_value_degrades_to_main`：FR-C4 无效值 → MAIN + warning log
     - `test_resolve_source_role_main_backward_compat`：AC-C2，主 Agent 无注入时 → MAIN 路径不变
     - `test_delegate_task_injects_worker_source_kind`：AC-C1，worker 调 delegate_task → source_runtime_kind="worker" 注入
     - `test_subagents_spawn_injects_worker_source_kind`：FR-C3，worker 调 subagents.spawn → 同样注入
  2. 使用 `AsyncMock` / `MagicMock` mock deps._execution_context
- **验收**:
  - [x] `pytest tests/services/test_phase_c_source_injection.py -v` 全部 PASS（13/13，含 2 额外 backward compat 测试）
  - [x] 无 import error
- **预估**: +250 LOC，60min
- **可合并 commit**: 独立

---

### T-C-8：Phase C per-Phase Codex review + 闭环 + commit

- **Phase**: C
- **依赖**: T-C-1 ~ T-C-7 全部完成
- **目标**: Phase C commit 前触发 per-Phase Codex review，处理 finding，完成 commit
- **改动文件**: 无（review 流程任务）
- **实施步骤**:
  1. 触发 `/codex:adversarial-review`（foreground 模式）
  2. 范围：Phase C 全部 diff（source_kinds.py + agent_context.py + dispatch_service.py + delegate_task_tool.py + delegation_tools.py + test_phase_c_source_injection.py）
  3. 重点检查：injection 条件是否严格（主 Agent 不误注入）/ automation/user_channel 路径完整性
  4. 处理 high/medium finding（接受改 / 拒绝写明理由）
  5. commit：`feat(F099-Phase-C): source_runtime_kind 扩展 + spawn 路径注入（FR-C1~FR-C4，F098 LOW §3 修复）`
  6. 运行全量回归确认 0 regression：`pytest --tb=short -q | tail -5`
- **验收**:
  - [x] 全量回归 ≥ F098 baseline c2e97d5 passed 数
  - [x] Codex review 0 high finding 残留
  - [x] commit message 含 Codex review 闭环说明
- **预估**: 60min（含 review 等待）
- **可合并 commit**: 独立（review 后 commit）

---

## Phase D — CONTROL_METADATA_UPDATED 常量化 + payloads 文档

**目标**: 在 source_kinds.py 补充 CONTROL_METADATA_SOURCE_* 常量（若 Phase C 未包含），更新 payloads.py source 字段注释，建立 Phase D 测试框架。

**注意**: plan §7 提示：若 Phase C source_kinds.py 中已同时定义 CONTROL_METADATA_SOURCE_* 常量，Phase D 的独立 commit 可与 Phase C 合并（归档为 C+D，不视为跳过）。实施者在 T-C-1 完成后检查是否需要单独 commit。

**关键依赖**: Phase C 完成

### T-D-1：确认/补充 CONTROL_METADATA_SOURCE_* 常量完整性

- **Phase**: D
- **依赖**: T-C-1
- **目标**: 确认 source_kinds.py 已包含 3 个 CONTROL_METADATA_SOURCE_ASK_BACK / REQUEST_INPUT / ESCALATE_PERMISSION 常量（T-C-1 已含则此任务为验证性任务）
- **改动文件**:
  - `packages/core/src/octoagent/core/models/source_kinds.py`（补充或确认，若 T-C-1 已完整则无需改动）
- **实施步骤**:
  1. 检查：`grep CONTROL_METADATA_SOURCE_ASK_BACK packages/core/src/octoagent/core/models/source_kinds.py`
  2. 若缺失，补充 `CONTROL_METADATA_SOURCE_ASK_BACK = "worker_ask_back"`、`CONTROL_METADATA_SOURCE_REQUEST_INPUT = "worker_request_input"`、`CONTROL_METADATA_SOURCE_ESCALATE_PERMISSION = "worker_escalate_permission"` 三个常量
  3. 若 T-C-1 已包含，记录为 "验证通过，无需额外改动"
- **验收**:
  - [x] `grep -c 'CONTROL_METADATA_SOURCE_' packages/core/src/octoagent/core/models/source_kinds.py` 输出 ≥ 5
- **预估**: +0~10 LOC，10min
- **可合并 commit**: T-D-1 + T-D-2（若 T-C-1 已含则无源码改动，合到 T-D-2 commit）

---

### T-D-2：更新 payloads.py ControlMetadataUpdatedPayload.source 字段注释

- **Phase**: D
- **依赖**: T-D-1（常量名已确认）
- **目标**: 在 `payloads.py` 的 `ControlMetadataUpdatedPayload.source` 字段 docstring 中追加 F099 新增候选值列表（FR-D4，文档变更，无 schema 变更）
- **改动文件**:
  - `packages/core/src/octoagent/core/models/payloads.py`（修改 ControlMetadataUpdatedPayload.source 字段注释）
- **实施步骤**:
  1. 读取 `payloads.py`，找到 `ControlMetadataUpdatedPayload` 定义（约第 40-67 行）
  2. 在 `source` 字段的 `Field(description=...)` 或注释中追加候选值：`worker_ask_back / worker_request_input / worker_escalate_permission`（F099 新增）
  3. 确认无 schema 破坏（字段类型仍为 `str`，无 Literal 约束变更）
- **验收**:
  - [x] `grep 'worker_ask_back' packages/core/src/octoagent/core/models/payloads.py` 有输出
  - [x] `python3 -c "from octoagent.core.models.payloads import ControlMetadataUpdatedPayload"` 无报错
- **预估**: +5 LOC，15min
- **可合并 commit**: T-D-1 + T-D-2

---

### T-D-3：新建 tests/services/test_phase_d_ask_back_audit.py（框架 + 可验证部分，共 4 函数）

- **Phase**: D
- **依赖**: T-D-1、T-D-2
- **目标**: 建立 Phase D 测试框架，覆盖常量/schema 验证 + 不污染对话历史（mock emit）；emit 实测断言在 Phase E 补全
- **改动文件**:
  - `tests/services/test_phase_d_ask_back_audit.py`（新建）
- **实施步骤**:
  1. 新建测试文件，包含以下 4 个测试函数：
     - `test_control_metadata_source_constants_defined`：验证 source_kinds.py 中 3 个 CONTROL_METADATA_SOURCE_* 常量值正确（"worker_ask_back" 等）
     - `test_control_metadata_updated_payload_source_field`：构造 `ControlMetadataUpdatedPayload(source="worker_ask_back", ...)` 并 model_dump round-trip
     - `test_merge_control_metadata_handles_control_metadata_updated`：确认 merge_control_metadata 已支持 CONTROL_METADATA_UPDATED 事件类型（向后兼容）
     - `test_ask_back_audit_event_not_in_conversation_turns`：mock event store 返回一个 CONTROL_METADATA_UPDATED 事件，验证 `_load_conversation_turns` 不包含该事件（AC-D2）
  2. 在文件末尾加注释标记"# Phase E 补全：emit 实测断言（test_ask_back_control_metadata_source_field 等）"
- **验收**:
  - [x] `pytest tests/services/test_phase_d_ask_back_audit.py -v` 全部 PASS（4/4）
  - [x] AC-D2 测试函数明确验证 CONTROL_METADATA_UPDATED 不出现在 conversation_turns 中
- **预估**: +150 LOC，45min
- **可合并 commit**: 独立

---

### T-D-4：Phase D per-Phase Codex review + 闭环 + commit

- **Phase**: D
- **依赖**: T-D-1 ~ T-D-3 全部完成
- **目标**: Phase D commit 前 Codex review，处理 finding，完成 commit（Phase D 轻量，可与 Phase C 合并 review）
- **改动文件**: 无（review 流程任务）
- **实施步骤**:
  1. 若 T-D-1 无独立源码改动，Phase D 可与 Phase C 合并 commit（记录为 feat(F099-Phase-C+D): ...）
  2. 若有独立改动，触发 foreground Codex review（范围：Phase D diff）
  3. 处理 finding，commit：`feat(F099-Phase-D): CONTROL_METADATA_UPDATED 常量化 + payloads 文档更新（FR-D4）`
  4. 确认全量回归 0 regression
- **验收**:
  - [x] 全量回归 ≥ Phase C baseline passed 数
  - [x] Codex finding 闭环说明写入 commit message 或合并到 Phase C commit 中
- **预估**: 30min
- **可合并 commit**: 可合并至 Phase C commit

---

## Phase B — 三工具引入（主行为 Phase）

**目标**: 新建 `ask_back_tools.py`，实现并注册 `worker.ask_back` / `worker.request_input` / `worker.escalate_permission` 三工具，接入 execution_context.request_input 和 ApprovalGate。

**关键依赖**: Phase C（常量就绪）+ Phase D（CONTROL_METADATA_SOURCE 常量就绪）

### T-B-1：新建 ask_back_tools.py 框架（imports + 注册入口）

- **Phase**: B
- **依赖**: T-C-1、T-D-1（所有常量就绪）
- **目标**: 新建 `ask_back_tools.py`，包含模块级常量 `_ENTRYPOINTS`、`_emit_ask_back_audit` 辅助函数框架、`register` 函数入口框架（handler 在后续任务填充）
- **改动文件**:
  - `apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py`（新建）
- **实施步骤**:
  1. 新建文件，参考 `supervision_tools.py` + `delegate_task_tool.py` 的注册模式
  2. 定义 `_ENTRYPOINTS = frozenset({"agent_runtime", "web"})`（FR-B6）
  3. 实现 `_emit_ask_back_audit(deps, source, control_metadata)` 辅助函数：调用 deps.event_store.append_event，emit CONTROL_METADATA_UPDATED 事件（FR-B4/D1/D3）
  4. 定义 `async def register(broker, deps) -> None:` 入口（handler 在 T-B-2~T-B-4 填充）
  5. 在文件顶部 import：source_kinds 常量 + AgentRuntimeRole + ControlMetadataUpdatedPayload + tool_contract
- **验收**:
  - [ ] 文件存在：`ls apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py`
  - [ ] `python3 -c "from octoagent.gateway.services.builtin_tools import ask_back_tools"` 无报错
  - [ ] `_emit_ask_back_audit` 函数定义存在
- **预估**: +60 LOC，30min
- **可合并 commit**: T-B-1 ~ T-B-5 整体 commit（Phase B 主体可一次 commit）

---

### T-B-2：实现 worker.ask_back handler

- **Phase**: B
- **依赖**: T-B-1（文件框架就绪）
- **目标**: 在 `ask_back_tools.py` 中实现 `worker.ask_back` handler，调用 `execution_context.request_input()` 实现 RUNNING → WAITING_INPUT → RUNNING 循环（FR-B1 / FR-B5 / OD-F099-7 选 A）
- **改动文件**:
  - `apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py`（修改：填充 handler）
- **实施步骤**:
  1. 在 `ask_back_tools.py` 中添加 `ask_back_handler` 函数
  2. 用 `@tool_contract(name="worker.ask_back", side_effect_level=SideEffectLevel.REVERSIBLE, ...)` 装饰
  3. 参数：`question: str`（工具描述向 LLM 说明"向当前工作来源提问"，FR-B5）, `context: str = ""`
  4. handler 逻辑：先调用 `_emit_ask_back_audit(deps, CONTROL_METADATA_SOURCE_ASK_BACK, {"ask_back_question": question, "ask_back_context": context, "created_at": ...})`，再调用 `await deps.execution_context.request_input(prompt=question, actor="worker:ask_back")`，返回结果文本（OD-F099-7 A：tool_result 路径）
  5. handler 不得 raise（FR-B1）：用 try/except 包装，异常时返回空字符串
  6. 在 `register` 函数中用 `broker.register(ask_back_handler)` 注册
- **验收**:
  - [ ] `grep 'worker.ask_back' apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py` 有输出
  - [ ] handler 文档字符串包含"向当前工作来源提问"说明（FR-B5）
  - [ ] 不使用 raise 终止流程
- **预估**: +50 LOC，40min
- **可合并 commit**: T-B-1 ~ T-B-5

---

### T-B-3：实现 worker.request_input handler

- **Phase**: B
- **依赖**: T-B-2（参考 ask_back handler 模式）
- **目标**: 实现 `worker.request_input` handler，语义为"请求额外结构化输入"（FR-B2）
- **改动文件**:
  - `apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py`（修改：追加 handler）
- **实施步骤**:
  1. 添加 `request_input_handler` 函数
  2. 装饰器：`@tool_contract(name="worker.request_input", side_effect_level=SideEffectLevel.REVERSIBLE, ...)`
  3. 参数：`prompt: str`, `expected_format: str = ""`
  4. handler 逻辑：emit `CONTROL_METADATA_SOURCE_REQUEST_INPUT` audit，调用 `execution_context.request_input(prompt=f"{prompt}\n期望格式：{expected_format}", actor="worker:request_input")`，返回结果（FR-B2：返回用户输入文本）
  5. 在 `register` 中注册
- **验收**:
  - [ ] `grep 'worker.request_input' apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py` 有输出
  - [ ] 函数签名包含 `expected_format` 参数
- **预估**: +40 LOC，20min
- **可合并 commit**: T-B-1 ~ T-B-5

---

### T-B-4：实现 worker.escalate_permission handler

- **Phase**: B
- **依赖**: T-B-1（ApprovalGate import 框架）
- **目标**: 实现 `worker.escalate_permission` handler，走 ApprovalGate SSE 路径（FR-B3 / OD-F099-5 A / AC-G3）
- **改动文件**:
  - `apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py`（修改：追加 handler）
- **实施步骤**:
  1. 添加 `escalate_permission_handler` 函数
  2. 装饰器：`@tool_contract(name="worker.escalate_permission", side_effect_level=SideEffectLevel.IRREVERSIBLE, ...)`（C4 两阶段合规）
  3. 参数：`action: str`, `scope: str`, `reason: str`
  4. handler 逻辑：
     - 先 emit `CONTROL_METADATA_SOURCE_ESCALATE_PERMISSION` audit（FR-D3）
     - 检查 `deps._approval_gate is None` → 返回 `"rejected"`（Constitution C6 降级，R4 缓解）
     - 调用 `handle = await deps.approval_gate.request_approval(task_id=..., tool_name="worker.escalate_permission", operation={"action": action, "scope": scope, "reason": reason})`
     - 调用 `decision = await deps.approval_gate.wait_for_decision(handle, timeout_seconds=300.0)`（P-VAL-1：超时返回 "rejected"，不 raise）
     - 返回 `decision`（"approved" 或 "rejected"，FR-B3：均不 raise）
  5. 在 `register` 中注册
- **验收**:
  - [ ] `grep 'worker.escalate_permission' apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py` 有输出
  - [ ] `grep 'wait_for_decision' apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py` 有输出
  - [ ] `grep 'approval_gate is None' apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py` 有输出（降级检查）
- **预估**: +60 LOC，45min
- **可合并 commit**: T-B-1 ~ T-B-5

---

### T-B-5：_emit_ask_back_audit 完整实现

- **Phase**: B
- **依赖**: T-B-1（函数框架），T-B-2、T-B-3、T-B-4 明确调用签名
- **目标**: 完善 `_emit_ask_back_audit` 辅助函数的完整 emit 逻辑（FR-B4，Constitution C2）
- **改动文件**:
  - `apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py`（修改：完善 _emit_ask_back_audit 函数体）
- **实施步骤**:
  1. 函数签名：`async def _emit_ask_back_audit(deps: ToolDeps, source: str, control_metadata: dict[str, Any]) -> None`
  2. 构造 `ControlMetadataUpdatedPayload(source=source, control_metadata=control_metadata, task_id=deps.task_id, ...)`
  3. 调用 `await deps.event_store.append_event(task_id=..., event_type=EventType.CONTROL_METADATA_UPDATED, payload=...)` 写入 Event Store
  4. 确认不抛异常（emit 失败时 log warning，不阻断工具调用）
- **验收**:
  - [ ] `_emit_ask_back_audit` 函数体包含 `append_event` 调用
  - [ ] 异常处理：emit 失败不影响工具主流程（try/except + log warning）
- **预估**: +20 LOC，20min
- **可合并 commit**: T-B-1 ~ T-B-5

---

### T-B-6：在 builtin_tools/__init__.py 注册 ask_back_tools

- **Phase**: B
- **依赖**: T-B-1（ask_back_tools.py 存在）
- **目标**: 确保系统启动时 ask_back_tools 被加载注册（FR-B6 / AC-B1）
- **改动文件**:
  - `apps/gateway/src/octoagent/gateway/services/builtin_tools/__init__.py`（修改：加入 ask_back_tools 注册）
- **实施步骤**:
  1. 读取 `builtin_tools/__init__.py` 找到 register_all 函数
  2. 在 import 区添加 `from . import ask_back_tools`
  3. 在 `register_all` 函数中追加 `await ask_back_tools.register(broker, deps)`
- **验收**:
  - [ ] `grep 'ask_back_tools' apps/gateway/src/octoagent/gateway/services/builtin_tools/__init__.py` 有输出
- **预估**: +3 LOC，10min
- **可合并 commit**: 独立（或合并到 T-B-5 commit）

---

### T-B-7：新建 tests/services/test_ask_back_tools.py（15 测试函数）

- **Phase**: B
- **依赖**: T-B-2、T-B-3、T-B-4、T-B-5、T-B-6（全部 handler 就绪）
- **目标**: 覆盖 AC-B1~B5 / AC-D1 / AC-G3 / AC-G4，共 15 个测试函数
- **改动文件**:
  - `tests/services/test_ask_back_tools.py`（新建）
- **实施步骤**:
  1. 新建测试文件，建立 mock ToolDeps（mock execution_context / approval_gate / event_store）
  2. 实现以下 15 个测试函数：
     - `test_ask_back_tool_registered`：broker 中存在 "worker.ask_back"（AC-B1）
     - `test_request_input_tool_registered`：broker 中存在 "worker.request_input"（AC-B1）
     - `test_escalate_permission_tool_registered`：broker 中存在 "worker.escalate_permission"（AC-B1）
     - `test_tool_entrypoints_include_agent_runtime`：三工具 entrypoints 包含 "agent_runtime"（AC-B1）
     - `test_ask_back_sets_waiting_input`：调用 ask_back_handler → execution_context.request_input 被调用（AC-B2）
     - `test_ask_back_returns_user_answer`：mock request_input 返回 "用户回答" → handler 返回 "用户回答"（AC-B3）
     - `test_ask_back_does_not_raise`：mock request_input 抛异常 → handler 仍返回字符串不 raise（FR-B1）
     - `test_request_input_returns_text`：mock request_input 返回 "输入文本" → handler 返回（FR-B2）
     - `test_escalate_permission_approved_path`：mock approval_gate.wait_for_decision 返回 "approved" → handler 返回 "approved"（AC-B4）
     - `test_escalate_permission_rejected_path`：返回 "rejected" → handler 返回 "rejected"（FR-B3）
     - `test_escalate_permission_timeout_returns_rejected`：模拟 300s 超时路径返回 "rejected"（FR-B3，P-VAL-1 验证）
     - `test_escalate_permission_gate_unavailable_returns_rejected`：`deps._approval_gate = None` → handler 返回 "rejected"（C6 降级，R4 缓解）
     - `test_ask_back_emits_control_metadata_updated`：调用 ask_back_handler → event_store.append_event 被调用，event_type=CONTROL_METADATA_UPDATED，payload.source="worker_ask_back"（FR-B4 / AC-D1）
     - `test_request_input_emits_audit`：event_store.append_event 被调用，source="worker_request_input"（FR-B4）
     - `test_escalate_permission_emits_audit`：event_store.append_event 被调用，source="worker_escalate_permission"（FR-D3）
- **验收**:
  - [ ] `pytest tests/services/test_ask_back_tools.py -v` 全部 PASS（15/15）
- **预估**: +380 LOC，90min
- **可合并 commit**: 独立

---

### T-B-8：Phase B per-Phase Codex review + 闭环 + commit

- **Phase**: B
- **依赖**: T-B-1 ~ T-B-7 全部完成
- **目标**: Phase B commit 前触发 per-Phase Codex review（重点：tool_contract schema / ApprovalGate 接入 / _emit_ask_back_audit 覆盖三工具）
- **改动文件**: 无（review 流程任务）
- **实施步骤**:
  1. 触发 `/codex:adversarial-review`（foreground 模式）
  2. 范围：ask_back_tools.py + builtin_tools/__init__.py + test_ask_back_tools.py
  3. 重点检查：tool_contract schema 是否合规 / ApprovalGate.wait_for_decision 调用是否正确 / 三工具均调用 _emit_ask_back_audit
  4. 处理 finding，commit：`feat(F099-Phase-B): 三工具引入 worker.ask_back/request_input/escalate_permission（AC-B1~B5，AC-G3，AC-G4）`
  5. 全量回归确认 0 regression
- **验收**:
  - [ ] 全量回归 ≥ Phase D baseline passed 数
  - [ ] Codex review 0 high finding 残留
- **预估**: 60min（含 review 等待）
- **可合并 commit**: 独立（review 后 commit）

---

## Phase E — 端到端验证 + 单测补全

**目标**: 验证 B+C+D 三 Phase 联合正确性；补全 test_phase_d emit 断言 + 新建 test_phase_e 端到端测试。

**关键依赖**: Phase B + C + D 全部完成

### T-E-1：补全 test_phase_d_ask_back_audit.py（Phase B 实现后 emit 断言，+3 函数）

- **Phase**: E
- **依赖**: T-B-2、T-B-4、T-B-5（ask_back_tools.py emit 逻辑就绪）
- **目标**: 补充 Phase D 测试文件中需要真实 emit 的断言（Phase D 建框架时 mock emit，Phase E 换真实调用）
- **改动文件**:
  - `tests/services/test_phase_d_ask_back_audit.py`（修改：追加 3 个测试函数）
- **实施步骤**:
  1. 在现有 4 个 Phase D 测试后追加：
     - `test_ask_back_control_metadata_updated_not_in_conversation_turns`：真实调用 ask_back_handler（mock execution_context），验证 emit 的 CONTROL_METADATA_UPDATED 事件不出现在 `_load_conversation_turns` 返回值中（AC-D2）
     - `test_ask_back_control_metadata_source_field`：验证 event_store 中 CONTROL_METADATA_UPDATED 的 payload.source == "worker_ask_back"（AC-D1）
     - `test_escalate_permission_control_metadata_source_field`：验证 escalate_permission emit 的 source == "worker_escalate_permission"（FR-D3）
- **验收**:
  - [ ] `pytest tests/services/test_phase_d_ask_back_audit.py -v` 全部 PASS（7/7，原 4 + 新 3）
  - [ ] AC-D2 验证函数使用真实的 `_load_conversation_turns` 逻辑
- **预估**: +100 LOC，45min
- **可合并 commit**: T-E-1 + T-E-2 + T-E-3

---

### T-E-2：新建 tests/services/test_phase_e_ask_back_e2e.py（6 端到端测试）

- **Phase**: E
- **依赖**: T-B-2、T-B-4、T-B-5、T-C-4、T-C-5（全部实现就绪）
- **目标**: 验证 RUNNING → WAITING_INPUT → RUNNING 完整生命周期 + Event Store audit trace（AC-E1 / FR-E2 / FR-E3 / FR-E4）
- **改动文件**:
  - `tests/services/test_phase_e_ask_back_e2e.py`（新建）
- **实施步骤**:
  1. 新建测试文件，联合 mock：execution_console + event_store + task_runner（参考现有 test_task_runner.py 的集成 mock 模式）
  2. 实现以下 6 个测试函数：
     - `test_e2e_ask_back_full_cycle_running_waiting_running`：Worker RUNNING → ask_back 调用 → WAITING_INPUT → attach_input → RUNNING（AC-E1）
     - `test_e2e_ask_back_event_store_three_events`：Event Store 中按顺序存在 TASK_STATE_CHANGED(RUNNING→WAITING_INPUT) + CONTROL_METADATA_UPDATED(ask_back) + TASK_STATE_CHANGED(WAITING_INPUT→RUNNING)（FR-E3 / AC-E1）
     - `test_e2e_ask_back_tool_result_contains_user_answer`：turn N+1 的 tool_result 包含 attach_input 的文本（FR-E2 / AC-B3）
     - `test_e2e_ask_back_tool_call_id_matches_tool_result`：tool_call_id 在 turn N（tool_call）和 turn N+1（tool_result）中匹配（FR-E2 精确验证）
     - `test_e2e_escalate_permission_approval_flow`：escalate_permission → WAITING_APPROVAL → SSE 审批通过 → RUNNING（FR-E4 / AC-G3）
     - `test_e2e_compaction_during_waiting_input_safe`：ask_back WAITING_INPUT 等待期间运行 compaction，验证恢复后 tool_result 内容完整（P-VAL-2 风险缓解验证）
- **验收**:
  - [ ] `pytest tests/services/test_phase_e_ask_back_e2e.py -v` 全部 PASS（6/6）
  - [ ] `test_e2e_ask_back_event_store_three_events` 验证三条事件的顺序（不仅是存在性）
- **预估**: +250 LOC，75min
- **可合并 commit**: T-E-1 + T-E-2 + T-E-3

---

### T-E-3：扩展现有测试文件（test_task_runner.py + test_capability_pack_tools.py）

- **Phase**: E
- **依赖**: T-B-6（ask_back_tools 已在 register_all 中）
- **目标**: 在 task_runner 集成层验证 ask_back 状态迁移；在 capability_pack 验证新工具注册（AC-B1 broker 查询）
- **改动文件**:
  - `tests/services/test_task_runner.py`（修改：追加 1 个测试函数）
  - `tests/services/test_capability_pack_tools.py`（修改：追加 1 个测试函数）
- **实施步骤**:
  1. 在 `test_task_runner.py` 追加：
     - `test_task_runner_ask_back_state_transition`：task_runner 层完整 ask_back 流程（RUNNING → WAITING_INPUT → attach_input → RUNNING），验证 task.status 迁移
  2. 在 `test_capability_pack_tools.py` 追加：
     - `test_ask_back_tools_in_broker_registration`：调用 register_all 后，broker 可查到 "worker.ask_back" / "worker.request_input" / "worker.escalate_permission" 三个工具名（AC-B1）
- **验收**:
  - [ ] `pytest tests/services/test_task_runner.py::test_task_runner_ask_back_state_transition -v` PASS
  - [ ] `pytest tests/services/test_capability_pack_tools.py::test_ask_back_tools_in_broker_registration -v` PASS
- **预估**: +80 LOC，40min
- **可合并 commit**: T-E-1 + T-E-2 + T-E-3

---

### T-E-4：Phase E per-Phase Codex review + 闭环 + commit

- **Phase**: E
- **依赖**: T-E-1 ~ T-E-3 全部完成
- **目标**: Phase E commit 前触发 per-Phase Codex review（重点：端到端覆盖维度是否完整）
- **改动文件**: 无（review 流程任务）
- **实施步骤**:
  1. 触发 `/codex:adversarial-review`（foreground 模式）
  2. 范围：Phase E 全部 diff（test_phase_d 补全 + test_phase_e + test_task_runner + test_capability_pack_tools 扩展）
  3. 重点检查：tool_call_id 匹配验证 / Event Store 三条事件顺序 / compaction 安全测试的断言是否充分
  4. 处理 finding，commit：`test(F099-Phase-E): 端到端验证 + 单测补全（AC-E1，FR-E2，FR-E3，FR-E4，P-VAL-2 缓解）`
  5. 全量回归确认 ≥ F098 baseline c2e97d5 passed 数
- **验收**:
  - [ ] 全量回归 ≥ F098 baseline passed 数
  - [ ] e2e_smoke 8/8 通过（pre-commit hook）
  - [ ] Codex review 0 high finding 残留
- **预估**: 60min（含 review 等待）
- **可合并 commit**: 独立（review 后 commit）

---

## Phase Verify — 全量验证 + 文档产出

**关键依赖**: Phase C + D + B + E 全部 commit 完成，0 high Codex finding 残留

### T-V-1：全量回归验证

- **Phase**: Verify
- **依赖**: T-E-4（Phase E commit 完成）
- **目标**: 确认全量测试 ≥ F098 baseline c2e97d5 passed 数，0 regression
- **改动文件**: 无
- **实施步骤**:
  1. 运行 `pytest --tb=short -q`，记录 passed 数
  2. 确认 passed 数 ≥ F098 baseline（实测记录 baseline 数值）
  3. 检查 failed 列表：若有 failure，必须修复或明确归档为已知 risk
- **验收**:
  - [ ] `pytest --tb=short -q | tail -5` 显示 ≥ F098 baseline passed 数
  - [ ] 0 failure（或所有 failure 已归档）
- **预估**: 10min（运行时间）
- **可合并 commit**: 无独立 commit（验证任务）

---

### T-V-2：e2e_smoke 5x 循环验证

- **Phase**: Verify
- **依赖**: T-V-1 通过
- **目标**: e2e_smoke 5x 循环稳定通过，验证 F099 改动不影响现有 smoke 能力域
- **改动文件**: 无
- **实施步骤**:
  1. 运行 `pytest -m e2e_smoke --timeout=180` × 5 轮（或使用 `octo e2e smoke --loop=5`）
  2. 每轮记录 PASS/FAIL
  3. 若有 FAIL，分析根因：F099 引入还是已知 flaky test
- **验收**:
  - [ ] 5/5 轮全部 PASS（8/8 能力域）
- **预估**: 20min（运行时间）
- **可合并 commit**: 无独立 commit

---

### T-V-3：Final cross-Phase Codex review（background）

- **Phase**: Verify
- **依赖**: T-V-1、T-V-2 通过
- **目标**: 全量 Final review，检查实现是否偏离 spec，F098 OD-1~OD-9 不偏离验证，audit trace 完整性
- **改动文件**: 无（review 任务）
- **实施步骤**:
  1. 触发 `/codex:adversarial-review`（background 模式）
  2. 输入：spec.md v0.2 + plan.md v0.1 + 全部 Phase diff（C+D+B+E）
  3. 重点检查：
     - F098 OD-1~OD-9 是否全部仍成立（AC-G2）
     - ask_back 在 WAITING_INPUT 与现有状态机兼容性
     - automation/user_channel 派生是否影响其他 consumer（AC-G2 范围）
     - 所有 FR（C1~E4）是否全部有对应实现
     - AC-G3 Constitution 合规（C4/C7/C10）
- **验收**:
  - [ ] Final review 触发，finding 收集完毕
- **预估**: 启动 5min，review 完成 30-60min（background）
- **可合并 commit**: 无

---

### T-V-4：Final Codex finding 闭环

- **Phase**: Verify
- **依赖**: T-V-3（review 完成，finding 收集）
- **目标**: 处理 Final review 所有 high/medium finding
- **改动文件**: 视 finding 决定（可能涉及任意 Phase 文件的修复）
- **实施步骤**:
  1. 逐条处理 high finding：接受 → 立即改动；拒绝 → 写明拒绝理由
  2. 逐条处理 medium finding：同上
  3. low finding：记录并 ignored（commit message 注明）
  4. 修复完成后重新运行全量回归确认 0 regression
  5. 处理到 0 high 残留
- **验收**:
  - [ ] 0 high finding 残留
  - [ ] 所有 medium finding 处理完毕（接受或拒绝+理由）
  - [ ] 修复后全量回归仍 ≥ F098 baseline
- **预估**: 30-90min（取决于 finding 数量）
- **可合并 commit**: 修复 commit 含 Codex finding 闭环说明

---

### T-V-5：产出 completion-report.md

- **Phase**: Verify
- **依赖**: T-V-4（finding 全部闭环）
- **目标**: 对照 plan §3 Phase 列表产出 completion-report.md，记录"实际做了 vs 计划"
- **改动文件**:
  - `.specify/features/099-ask-back-source-generalization/completion-report.md`（新建）
- **实施步骤**:
  1. 新建文件，列出每个 Phase（C/D/B/E/Verify）的计划 vs 实际对照
  2. 记录所有 Phase 跳过或偏离（若有），含理由
  3. 列出 Codex review 闭环表（per-Phase review finding 数 + Final review finding 数 + 处理结果）
  4. 列出推迟项（F100 接收的事项）
  5. 列出 spec.md §4 AC 逐条验证结果（PASS / SKIP / DEFERRED）
- **验收**:
  - [ ] 文件存在：`ls .specify/features/099-ask-back-source-generalization/completion-report.md`
  - [ ] AC 验证表包含全部 13 条 AC（AC-B1~B4 / AC-C1~C2 / AC-D1~D2 / AC-E1 / AC-G1~G4）
- **预估**: +200 LOC（文档），30min
- **可合并 commit**: 独立 docs commit

---

### T-V-6：产出 handoff.md（给 F100 Decision Loop Alignment）

- **Phase**: Verify
- **依赖**: T-V-5（completion-report 完成，全局视图清晰）
- **目标**: 产出 handoff.md，为 F100 提供 F099 完成后的关键信息
- **改动文件**:
  - `.specify/features/099-ask-back-source-generalization/handoff.md`（新建）
- **实施步骤**:
  1. 新建文件，包含以下信息给 F100：
     - 三工具现状：工具名 / entrypoints / handler 文件路径 / 参数签名
     - `source_runtime_kind` 已定义枚举值（5 个）+ 扩展位置（`source_kinds.py` 绝对路径）
     - `_resolve_a2a_source_role()` 现在处理的 source 值范围（main/worker/subagent/automation/user_channel）
     - F100 接入点说明：`recall_planner` 与 ask_back 工具的交互现状（ask_back 挂起时 recall_planner 行为）
     - `RecallPlannerMode="auto"` 启用接入点位置（F100 主责，F099 仅标注文件/行号）
     - F099 已知 LOW / deferred 项清单（若有）
     - 枚举扩展位置（`agent_context.py`）供 F100 参考
- **验收**:
  - [ ] 文件存在：`ls .specify/features/099-ask-back-source-generalization/handoff.md`
  - [ ] 包含"F100 接入点"章节和 `RecallPlannerMode` 位置标注
- **预估**: +150 LOC（文档），20min
- **可合并 commit**: T-V-5 + T-V-6 合并一个 docs commit

---

### T-V-7：Verify commit 候选（用户拍板后再 push）

- **Phase**: Verify
- **依赖**: T-V-1 ~ T-V-6 全部完成，0 high finding 残留
- **目标**: 最终 commit（含 completion-report + handoff），标记为可 push 候选，等用户拍板
- **改动文件**: 无新改动（commit 已有修改）
- **实施步骤**:
  1. 确认所有 Phase commit 已落盘（本 worktree 分支）
  2. 最终 commit message：`docs(F099-Verify): completion-report + handoff + Codex Final review N high / M medium 已处理 / K low ignored`
  3. **不主动 push origin**，等用户显式确认后 push（Spawned Task 处理流程强制规则）
  4. 生成归总报告给主 session：改动文件清单 + Codex review 闭环结果 + 建议合入 origin/master
- **验收**:
  - [ ] 所有改动已 commit 到本 worktree 分支
  - [ ] commit message 含 Codex review 闭环说明
  - [ ] **未 push origin**（等用户拍板）
- **预估**: 10min
- **可合并 commit**: 独立（最终 docs commit）

---

## 任务统计

| Phase | 任务数 | 预估 LOC（源码）| 预估 LOC（测试）| 预估时间 |
|-------|--------|----------------|----------------|---------|
| A（已完成）| 5 | — | — | — |
| C | 8 | +110 | +250 | 4-5h |
| D | 4 | +15 | +150 | 1-2h |
| B | 8 | +215 | +380 | 4-5h |
| E | 4 | 0（仅测试）| +430 | 3-4h |
| Verify | 7 | 0 | 0（文档 +350）| 2-3h |
| **合计实施阶段** | **31**（不含 Phase A 5 个）| **+340** | **+1210** | **14-19h** |
| **总计（含 Phase A）** | **36** | — | — | — |

**总任务数（含 Phase A）**: 41

---

## 关键路径（必须串行的任务链）

```
T-C-1（source_kinds.py）
  → T-C-2（枚举扩展）
  → T-C-3（__init__ re-export）
  → T-C-4（dispatch_service 扩展）
  → T-C-5（delegate_task_tool 注入）
  → T-C-6（delegation_tools 注入）
  → T-C-7（Phase C 单测）
  → T-C-8（Phase C Codex review + commit）
    → T-D-1（CONTROL_METADATA_SOURCE 确认/补充）
    → T-D-2（payloads.py 文档）
    → T-D-3（Phase D 测试框架）
    → T-D-4（Phase D Codex review + commit）
      → T-B-1（ask_back_tools.py 框架）
      → T-B-2（ask_back handler）
      → T-B-3（request_input handler）
      → T-B-4（escalate_permission handler）
      → T-B-5（_emit_ask_back_audit 完整实现）
      → T-B-6（__init__ 注册）
      → T-B-7（Phase B 单测）
      → T-B-8（Phase B Codex review + commit）
        → T-E-1（test_phase_d 补全）
        → T-E-2（test_phase_e 端到端）
        → T-E-3（现有测试扩展）
        → T-E-4（Phase E Codex review + commit）
          → T-V-1 → T-V-2 → T-V-3 → T-V-4 → T-V-5 → T-V-6 → T-V-7
```

---

## 可并行机会

| 并行组 | 任务 | 条件 |
|--------|------|------|
| **Phase C 内部并行** | T-C-5 + T-C-6（两处 spawn 注入）| 均依赖 T-C-1，文件不同，可并行 |
| **Phase B 内部并行** | T-B-2 + T-B-3 + T-B-4（三工具 handler）| 均依赖 T-B-1，互不依赖，可并行；T-B-5 需等待三者签名确认 |
| **Phase D 轻量合并** | T-D-1 + T-D-2 可在 Phase C 时一并完成 | 若 T-C-1 已包含 CONTROL_METADATA_SOURCE 常量，Phase D 仅剩 payloads.py 文档变更 |
| **Phase E 内部并行** | T-E-1 + T-E-2 + T-E-3 | 均依赖 Phase B/C/D 完成，互不依赖，可并行 |
| **Verify 文档并行** | T-V-5 + T-V-6（completion-report + handoff）| 均依赖 T-V-4 finding 闭环，互不依赖，可并行 |

---

## FR 覆盖映射

| FR | 对应任务 |
|----|---------|
| FR-B1（ask_back 不 raise）| T-B-2、T-B-7（test_ask_back_does_not_raise）|
| FR-B2（request_input 返回文本）| T-B-3、T-B-7（test_request_input_returns_text）|
| FR-B3（escalate_permission 不 raise）| T-B-4、T-B-7（test_escalate_permission_*_path）|
| FR-B4（三工具 emit audit）| T-B-5、T-B-7（test_*_emits_audit）|
| FR-B5（ask_back 描述提示 caller）| T-B-2（handler docstring）|
| FR-B6（entrypoints 含 agent_runtime）| T-B-1（_ENTRYPOINTS）、T-B-7（test_tool_entrypoints）|
| FR-C1（automation/user_channel 派生）| T-C-4（扩展 _resolve_a2a_source_role）、T-C-7（test_resolve_source_role_*）|
| FR-C2（delegate_task 注入 worker）| T-C-5、T-C-7（test_delegate_task_injects）|
| FR-C3（subagents.spawn 注入 worker）| T-C-6、T-C-7（test_subagents_spawn_injects）|
| FR-C4（无效值降级）| T-C-4（降级分支）、T-C-7（test_resolve_source_role_unknown）|
| FR-D1（ask_back/request_input emit audit）| T-B-5、T-E-1（test_ask_back_control_metadata_source_field）|
| FR-D2（不污染对话历史）| T-D-3（test_ask_back_audit_event_not_in_conversation_turns）、T-E-1 补全|
| FR-D3（escalate_permission emit audit）| T-B-4、T-B-7（test_escalate_permission_emits_audit）|
| FR-D4（payloads 文档更新）| T-D-2|
| FR-E1（WAITING_INPUT 可 attach_input 唤醒）| T-E-2（test_e2e_ask_back_full_cycle）|
| FR-E2（tool_result 包含用户回答）| T-E-2（test_e2e_tool_result + test_e2e_tool_call_id）|
| FR-E3（Event Store 三条事件）| T-E-2（test_e2e_ask_back_event_store_three_events）|
| FR-E4（escalate_permission e2e）| T-E-2（test_e2e_escalate_permission_approval_flow）|

**FR 覆盖率**: 18/18 FR，100%

---

v0.1 - 待 GATE_TASKS 审查
