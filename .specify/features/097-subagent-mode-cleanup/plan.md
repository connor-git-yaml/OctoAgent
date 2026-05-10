# F097 Subagent Mode Cleanup — 实施 Plan（v0.1）

> 上游：[spec.md](spec.md) v0.2（GATE_DESIGN 已拍板）/ [research/tech-research.md](research/tech-research.md) / [clarification.md](clarification.md)
>
> baseline：cc64f0c（origin/master，含 F096）/ 3260 passed（F096 final，CLAUDE.local.md 记录）
>
> 分支：feature/097-subagent-mode-cleanup

---

## 执行摘要

F097 在 F096 audit chain 基础上，让 H3-A 临时 Subagent 委托成为显式可观测的一等公民。共 8 个 Phase（Phase 0/A/C/E/B/D/F/G）+ Verify，新增代码估算约 250-350 行（不含测试），总耗时约 14h。GATE_DESIGN 5 项决策全部锁定，Memory α 共享引用已拍板，无遗留 Open Decision。BEHAVIOR_PACK_LOADED 消费方实测完成（详见 §3），`"subagent"` 值可直接引入，无需 schema version bump。

---

## §0. GATE_DESIGN 决策映射表

| 决策 ID | 锁定内容 | Plan 落点 |
|---------|---------|----------|
| **OD-1** | Memory α 共享引用：Subagent 复用 caller AGENT_PRIVATE namespace ID | Phase F：`_ensure_memory_namespaces` 增加 subagent 路径，不创建新 row；`caller_memory_namespace_ids` 从 caller AgentRuntime 读取 namespace ID 集合 |
| **OD-2** | `subagents.spawn` 保持不写 SUBAGENT_SPAWNED | AC-EVENT-1 仅验证 `delegate_task` 路径；plan 不修改 `emit_audit_event` 参数 |
| **C-1** | `SubagentDelegation` 增 `child_agent_session_id` 字段 | Phase A：model 定义含此字段；Phase B：session 创建后立即写入；Phase E：cleanup 直接用此字段定位 |
| **C-2** | plan 阶段 grep 消费方后直接引入（§3 实测结论：无枚举硬校验）| Phase G：验证 AC-G1；无需 schema version bump；`test_agent_decision_envelope.py:640` 断言针对 Worker 路径，不影响新 subagent 路径 |
| **CL#16** | SubagentDelegation 写入 `child_task.metadata.subagent_delegation` | Phase A：model 含 `to_metadata_json()` helper；Phase B：spawn 后写入 child task metadata；Phase E：cleanup 从 task metadata 读回 `closed_at` |

---

## §1. Phase 顺序与依赖图

```
Phase 0（前置实测，~30min）
  ↓
Phase A（SubagentDelegation model，~1h）
  ↓
Phase C（ephemeral AgentProfile kind=subagent，~2h）
  ↓
Phase E（session cleanup hook + 幂等，~2h）
  ↓
Phase B（_ensure_agent_session SUBAGENT_INTERNAL 路径，~3h）
  ↓
Phase D（RuntimeHintBundle caller→child 拷贝，~1h）
  ↓
Phase F（Memory α 共享引用实施，~2h）
  ↓
Phase G（BEHAVIOR_PACK_LOADED agent_kind=subagent 验证，~30min）
  ↓
Verify（全量回归 + e2e_smoke + Final cross-Phase Codex review，~2h）
```

**依赖说明**（analysis F-02 修订后）：
- Phase C 依赖 Phase A（ephemeral profile 持久化引用 SubagentDelegation）
- **Phase E 仅依赖 Phase A**（通过 `child_agent_session_id` 字段定位 cleanup 目标）；**cleanup hook 在 Phase B 完成前对 SUBAGENT_INTERNAL session 静默跳过**（无 session 时 noop 幂等），Phase B 完成后 cleanup 才真正激活生效。Phase E 在 Phase B 之前实施可降低 Phase B 调试期 zombie 累积风险
- Phase B 依赖 Phase A（spawn 时立即写入 SubagentDelegation）
- Phase D 独立（新增字段不破坏现有路径，但建议在 B 后做，避免 session 路径不完整时测试混乱）
- Phase F 依赖 Phase A（`caller_memory_namespace_ids` 字段）和 Phase B（subagent AgentRuntime 已建立）
- Phase G 依赖 Phase C（ephemeral profile kind 正确后自动副产品）

> **修订说明（analysis F-02）**：原文表述"Phase E 依赖 Phase B"与实施顺序 0→A→C→**E→B**→D→F→G→Verify 矛盾。修订后 E 仅依赖 A，cleanup hook 在 B 前为静默 noop，B 完成后激活——与 tasks.md Phase E 头部"cleanup 函数若无 SUBAGENT_INTERNAL session 则静默跳过"注释一致。

---

## §2. 各 Phase 详细规划

### Phase 0：实测侦察（前置，~30min）

**目的**：在开始实施前对 Phase 0-G 的关键路径做代码级确认，消除不确定性。

**AC 映射**：前置侦察，无直接 spec AC（结论用于闭环 spec §13 Plan 阶段需精确定位）

**侦察清单**：

| 侦察项 | 位置 | 要确认的内容 |
|--------|------|------------|
| P0-1：SUBAGENT_COMPLETED 事件存在性 | `octoagent/packages/core/src/octoagent/core/models/enums.py` | `EventType.SUBAGENT_COMPLETED` 是否已定义 |
| P0-2：cleanup hook 最佳挂载点 | `task_runner.py:560-577`（终态处理）+ `_notify_completion:632` | 确认 `mark_succeeded / mark_cancelled / mark_failed` 调用后是否有回调扩展点 |
| P0-3：`_resolve_or_create_agent_profile` 调用链 | `agent_context.py`，grep `_resolve_or_create_agent_profile` | 定位 ephemeral profile 创建的最早注入点（spawn_child 内部 vs `build_task_context` 入口） |
| P0-4：RuntimeHintBundle 字段精确列表 | `behavior.py:206`，`RuntimeHintBundle` class | 列出所有字段名，确定 surface / tool_universe / recent_failure_budget 实际字段名 |
| P0-5：task metadata write/read 路径 | task store 层 `update_task_metadata` | 确认写入 `child_task.metadata["subagent_delegation"]` 的 API |

**改动文件**：无（仅 grep / 读代码）

**结论归档**：作为 Phase A 的前置注释记录到 commit message 中

**Codex review 要点**：无（侦察 Phase 不走 Codex review）

---

### Phase A：SubagentDelegation Pydantic Model（~1h）

**AC 映射**：AC-A1, AC-A2, AC-A3

**改动文件清单**：

| 文件 | 改动类型 | 估算 LOC |
|------|---------|---------|
| `octoagent/packages/core/src/octoagent/core/models/delegation.py`（326 行）| 新增 class + helper | +60 行 |
| `octoagent/packages/core/src/octoagent/core/models/__init__.py` 或导出文件 | 新增导出 | +2 行 |
| `octoagent/packages/core/tests/test_subagent_delegation_model.py`（新建）| 单测 | +80 行 |

**关键代码草图**：

```python
# delegation.py 新增，紧接 DelegationTargetKind 枚举之后

from __future__ import annotations
from datetime import datetime
import json
from typing import Literal
from pydantic import BaseModel, Field
from ulid import ULID  # 已有依赖

class SubagentDelegation(BaseModel):
    """H3-A 临时 Subagent 委托的结构化数据载体。生命周期从 spawn 到 closed。"""

    delegation_id: str = Field(default_factory=lambda: str(ULID()))
    parent_task_id: str
    parent_work_id: str
    child_task_id: str
    child_agent_session_id: str | None = None  # C-1: spawn 后立即填充，cleanup 直接用此字段
    caller_agent_runtime_id: str
    caller_project_id: str
    caller_memory_namespace_ids: list[str] = Field(default_factory=list)  # OD-1 α 共享 namespace IDs
    spawned_by: str  # "delegate_task" or "subagents.spawn"
    target_kind: DelegationTargetKind = DelegationTargetKind.SUBAGENT
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    closed_at: datetime | None = None

    def to_metadata_json(self) -> str:
        """CL#16: 序列化为 task metadata JSON 字符串。"""
        return self.model_dump_json()

    @classmethod
    def from_metadata_json(cls, raw: str) -> SubagentDelegation:
        """CL#16: 从 task metadata JSON 反序列化。"""
        return cls.model_validate_json(raw)

    def mark_closed(self, closed_at: datetime) -> SubagentDelegation:
        """返回标记 closed_at 的新实例（immutable pattern）。"""
        return self.model_copy(update={"closed_at": closed_at})
```

**测试策略**：

- 单测：`test_subagent_delegation_model.py`
  - 字段默认值校验（delegation_id 是 ULID 格式，target_kind 默认 SUBAGENT，closed_at 默认 None）
  - `to_metadata_json` + `from_metadata_json` round-trip（含 child_agent_session_id 字段）
  - `mark_closed` 不可变 pattern
- 集成测：不需要（model 层单测已充分）

**Codex review 要点**：
- 字段命名是否与 F098 WorkerDelegation 的命名惯例兼容（`caller_agent_runtime_id` 而非 `parent_agent_runtime_id`）
- `child_agent_session_id` 默认 None 的场景（spawn 失败时 SubagentDelegation 不含 session_id）是否合理

**回滚方案**：新建文件直接删除，不影响现有任何路径

---

### Phase C：ephemeral AgentProfile（kind=subagent）创建逻辑（~2h）

**AC 映射**：AC-C1, AC-C2

**改动文件清单**：

| 文件 | 改动类型 | 估算 LOC |
|------|---------|---------|
| `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`（4196 行）| `_resolve_or_create_agent_profile` 增 subagent 路径 | +40 行 |
| `octoagent/apps/gateway/tests/test_subagent_profile.py`（新建）| 单测 | +60 行 |

**关键代码草图**：

Phase 0 侦察定位 `_resolve_or_create_agent_profile` 后，在该函数中增加 subagent 路径：

```python
# agent_context.py _resolve_or_create_agent_profile 内新增判断分支
# 条件：agent_runtime.delegation_mode == "subagent"

if agent_runtime.delegation_mode == "subagent":
    # 方案 A：轻量 ephemeral profile，不写入持久化 store
    ephemeral_profile = AgentProfile(
        profile_id=str(ULID()),          # ULID 生成，不写入 agent_profile 表
        kind="subagent",
        scope=AgentProfileScope.PROJECT,  # 与 caller 同 project scope
        # 其余字段从 control_metadata 或 caller profile 派生（Phase 0 侦察后确定）
    )
    return ephemeral_profile
```

**关键约束**：ephemeral profile **不调用** `agent_context_store.save_agent_profile`（AC-C1 验证：spawn 前后 `agent_profile` 表 COUNT 不变）。

**测试策略**：

- 单测：mock `agent_context_store.save_agent_profile`，断言 Subagent 路径下不调用该方法
- 单测：检查 ephemeral profile 的 `profile_id` 是 ULID 格式、`kind == "subagent"`、`scope == PROJECT`
- 集成测：dispatch 一次 subagent task，查询 `agent_profile` 表 COUNT 与 dispatch 前一致

**Codex review 要点**：
- ephemeral profile 与现有 `_resolve_or_create_agent_profile` 的 Worker 路径是否完全隔离（`delegation_mode == "subagent"` 判断条件是否足够精确）
- ULID 生成的 profile_id 在运行时是否可能与持久化 profile 的 ID 混淆（不会，因为不写入表）

**回滚方案**：仅在新分支条件内改动，revert 时删除 `if delegation_mode == "subagent"` 块即可

---

### Phase E：Session Cleanup Hook + 幂等（~2h）

**AC 映射**：AC-E1, AC-E2, AC-E3

**改动文件清单**：

| 文件 | 改动类型 | 估算 LOC |
|------|---------|---------|
| `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py`（670 行）| `_notify_completion` 增 subagent cleanup 逻辑 | +30 行 |
| `octoagent/packages/core/src/octoagent/core/store/agent_context_store.py`（1653 行）| 复用 `save_agent_session` 幂等写 CLOSED | +0 行（复用已有方法） |
| `octoagent/apps/gateway/tests/test_subagent_cleanup.py`（新建）| 单测 + 集成测 | +80 行 |

**关键代码草图**：

cleanup hook 挂载点为 `task_runner.py:_notify_completion`（line 632），在 completion notifier 调用后或前增加 subagent session 清理：

```python
# task_runner.py _run_job 中，task 进入终态后调用 cleanup
async def _close_subagent_session_if_needed(
    self,
    task_id: str,
    terminal_at: datetime,
) -> None:
    """Phase E: 子任务进入终态后，关闭关联的 SUBAGENT_INTERNAL session。幂等。"""
    # 1. 从 task metadata 读取 SubagentDelegation
    task = await self._stores.task_store.get_task(task_id)
    if task is None or "subagent_delegation" not in (task.metadata or {}):
        return  # 非 subagent task，跳过

    delegation = SubagentDelegation.from_metadata_json(task.metadata["subagent_delegation"])
    if delegation.child_agent_session_id is None:
        return  # spawn 时 session 未记录（spawn 失败场景），跳过

    if delegation.closed_at is not None:
        return  # 已关闭，幂等直接返回（AC-E2）

    # 2. 关闭 SUBAGENT_INTERNAL AgentSession
    session = await self._stores.agent_context_store.get_agent_session(
        delegation.child_agent_session_id
    )
    if session is not None and session.status != AgentSessionStatus.CLOSED:
        closed_session = session.model_copy(
            update={"status": AgentSessionStatus.CLOSED, "closed_at": terminal_at}
        )
        await self._stores.agent_context_store.save_agent_session(closed_session)

    # 3. 更新 SubagentDelegation.closed_at（顺序写入，不强制事务，Q-5 AUTO-CLARIFIED）
    closed_delegation = delegation.mark_closed(terminal_at)
    updated_metadata = {**(task.metadata or {}), "subagent_delegation": closed_delegation.to_metadata_json()}
    await self._stores.task_store.update_task_metadata(task_id, updated_metadata)
```

**幂等设计**：`delegation.closed_at is not None` 检查（持久化在 task metadata），进程重启后重新触发 cleanup 时不重复关闭。

**关键约束**：RecallFrame **不删除**（AC-E3）—— cleanup 函数仅操作 AgentSession 和 SubagentDelegation，不触碰 RecallFrame 表。

**测试策略**：

- 单测：mock stores，测试 cleanup 被调用两次时幂等（`closed_at` 保持首次值）
- 单测：mock stores，测试非 subagent task（无 subagent_delegation 字段）时 cleanup 直接 return
- 集成测：完整 subagent task 生命周期，succeeded 后查 AgentSession status=CLOSED + closed_at 已填充
- 集成测：cleanup 后 `list_recall_frames(agent_runtime_id=subagent_runtime_id)` 仍有数据（AC-E3）

**Codex review 要点**：
- cleanup 挂载在 `_notify_completion` 内的异常处理（cleanup 失败是否不影响主流程）
- `update_task_metadata` 是否线程安全（SQLite WAL 下，同一 task_id 的顺序写入是安全的）

**回滚方案**：删除 `_close_subagent_session_if_needed` 调用及函数定义；task metadata 中的 `subagent_delegation` 字段无害（已存在 task 不受影响）

---

### Phase B：`_ensure_agent_session` 增 SUBAGENT_INTERNAL 路径（~3h，最高风险）

**AC 映射**：AC-B1, AC-B2

**改动文件清单**：

| 文件 | 改动类型 | 估算 LOC |
|------|---------|---------|
| `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`（4196 行）| `_ensure_agent_session` 增第 4 路 | +25 行 |
| 关联 spawn 路径：`delegation_plane.py` 或 `capability_pack.py` | 写 `child_agent_session_id` 到 SubagentDelegation | +15 行 |
| `octoagent/apps/gateway/tests/services/test_agent_context_ensure_session.py`（新建或补充到已有文件）| 单测 | +70 行 |

**关键代码草图**：

```python
# agent_context.py _ensure_agent_session（line 2318 附近）
# 在现有 3 路判断前，增加第 4 路：

# F097 Phase B: SUBAGENT_INTERNAL 路径
# 条件：agent_runtime.delegation_mode == "subagent"（Q-2 AUTO-CLARIFIED 选项 A）
if agent_runtime.delegation_mode == "subagent":
    kind = AgentSessionKind.SUBAGENT_INTERNAL
    # parent_worker_runtime_id 从 agent_runtime.parent_runtime_id 或 control_metadata 读取
    parent_runtime_id = _extract_parent_runtime_id(agent_runtime)
    session = AgentSession(
        agent_session_id=str(ULID()),
        kind=kind,
        agent_runtime_id=agent_runtime.agent_runtime_id,
        parent_worker_runtime_id=parent_runtime_id,
        # 其余字段由 Phase 0 侦察后确定
    )
    await self._store.save_agent_session(session)
    # 写回 SubagentDelegation.child_agent_session_id（C-1 决策）
    await _update_subagent_delegation_session_id(agent_runtime, session.agent_session_id)
    return session

# 现有 3 路保持不变（Worker / main 路径不受影响，AC-B2 验证）
```

**保守原则**：`delegation_mode == "subagent"` 是充分条件（Q-2 AUTO-CLARIFIED），现有 Worker 路径（`delegation_mode == "worker_inline"` / `"main_delegate"` 等）不触发新路径。

**测试策略**：

- 单测（关键）：`DIRECT_WORKER` 路径、`WORKER_INTERNAL` 路径、`MAIN_BOOTSTRAP` 路径的现有单测**全部继续通过**（0 regression，AC-B2）
- 单测（新增）：`delegation_mode == "subagent"` 触发 SUBAGENT_INTERNAL 路径，session kind 正确
- 单测（新增）：SUBAGENT_INTERNAL session 的 `parent_worker_runtime_id` 正确填充
- 集成测：spawn subagent 后 `AgentSession` 表中存在 kind=SUBAGENT_INTERNAL 的记录

**Codex review 要点**：
- 第 4 路的条件判断是否与现有 3 路存在交集（特别是 `WORKER_INTERNAL` 路径的 fallback 逻辑）
- `parent_worker_runtime_id` 字段的信号来源（Phase 0 侦察确认后填入此处）

**回滚方案**：删除 `if agent_runtime.delegation_mode == "subagent"` 块；`delegation_plane.py` / `capability_pack.py` 的 session_id 写入补丁一并 revert

---

### Phase D：RuntimeHintBundle caller→child 拷贝（~1h）

**AC 映射**：AC-D1, AC-D2

**改动文件清单**：

| 文件 | 改动类型 | 估算 LOC |
|------|---------|---------|
| `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`（2088 行）| `_launch_child_task` 增 target=SUBAGENT 时拷贝 RuntimeHintBundle | +20 行 |
| `octoagent/apps/gateway/tests/services/test_capability_pack_launch.py`（新建或补充）| 单测 | +40 行 |

**Phase 0 侦察依赖**：Phase 0 P0-4 确认 `RuntimeHintBundle` 的字段名（`behavior.py:206`），拷贝字段列表为：
- `surface`（调用方 surface）
- `tool_universe`（工具集范围约束）
- `recent_failure_budget`（最近失败限制，若字段存在）
- `recent_worker_lane_*` 相关字段（Phase 0 侦察后决定是否包含）

**关键代码草图**：

```python
# capability_pack.py _launch_child_task（line 1229 附近）
# 仅在 target_kind == "subagent" 时追加 RuntimeHintBundle 字段

control_metadata = {
    "parent_task_id": parent_task.task_id,
    "parent_work_id": parent_work.work_id,
    "requested_worker_type": worker_type,
    "target_kind": target_kind,
    # ... 现有字段保持不变 ...
}

if target_kind == DelegationTargetKind.SUBAGENT:
    # F097 Phase D: 从 caller RuntimeHintBundle 拷贝上下文
    caller_hints = _extract_runtime_hints(caller_runtime_context)  # Phase 0 确认 caller 引用
    if caller_hints is not None:
        control_metadata.update({
            "surface": caller_hints.surface,
            "tool_universe": caller_hints.tool_universe,
            # recent_failure_budget 等按 Phase 0 侦察结果决定
        })
```

**保守原则**：仅在 `target_kind == SUBAGENT` 时添加字段，Worker 路径 `control_metadata` 不变（AC-D2）。

**测试策略**：

- 单测：mock caller RuntimeHintBundle 含 `surface="web"`，spawn SUBAGENT，检查 `child_message.control_metadata["surface"] == "web"`
- 单测：spawn WORKER（`target_kind=WORKER`），检查 `child_message.control_metadata` 不含 `surface` 字段（AC-D2）

**Codex review 要点**：
- caller RuntimeHintBundle 的获取路径（从 agent_runtime 还是从 control_metadata 读取 caller 信息）
- `surface` 字段是否可能为 None（caller 在 telegram 上时 surface 值是什么）

**回滚方案**：删除 `if target_kind == SUBAGENT` 块；现有 Worker 路径完全不受影响

---

### Phase F：Memory α 共享引用实施（~2h）

**AC 映射**：AC-F1, AC-F2, AC-F3

**改动文件清单**：

| 文件 | 改动类型 | 估算 LOC |
|------|---------|---------|
| `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`（4196 行）| `_ensure_memory_namespaces` 增 subagent 路径（不创建新 namespace row）| +30 行 |
| 关联：spawn 时从 caller AgentRuntime 读取 AGENT_PRIVATE namespace IDs | `delegation_plane.py` 或 `capability_pack.py` | +15 行 |
| `octoagent/apps/gateway/tests/test_subagent_memory_sharing.py`（新建）| 集成测 | +70 行 |

**α 语义实施方案（OD-1 已锁定）**：

Subagent 不调用 `_ensure_memory_namespaces` 的创建路径，而是直接将 caller 的 `AGENT_PRIVATE` namespace ID 集合绑定到 Subagent 的 AgentRuntime：

```python
# agent_context.py _ensure_memory_namespaces（line 2517 附近）
# 在 subagent 路径增加 α 共享引用逻辑：

if agent_runtime.delegation_mode == "subagent":
    # α 共享引用：直接使用 caller 的 AGENT_PRIVATE namespace ID
    # caller_memory_namespace_ids 从 SubagentDelegation 读取（已在 spawn 时填充，AC-F2）
    delegation = _load_subagent_delegation_from_task(agent_runtime.task_id)
    if delegation and delegation.caller_memory_namespace_ids:
        # 不创建新的 AGENT_PRIVATE namespace row
        # 直接返回 caller 的 namespace ID 集合
        return delegation.caller_memory_namespace_ids
    # fallback：若无法读取，走正常创建路径（异常恢复）
```

spawn 时（Phase A/B 完成后），在 SubagentDelegation 创建时填充 `caller_memory_namespace_ids`：从 caller AgentRuntime 读取当前的 AGENT_PRIVATE namespace IDs → 写入 `SubagentDelegation.caller_memory_namespace_ids` → 持久化到 child task metadata。

**测试策略**：

- 集成测（关键，AC-F3）：
  1. Worker（caller）在 AGENT_PRIVATE namespace 写入 fact X
  2. spawn Subagent
  3. Subagent 触发 Memory recall
  4. 断言 caller 在 spawn 之后能读到该写入（namespace ID 一致性）
- 单测：Worker spawn 路径（target_kind=WORKER）的 `_ensure_memory_namespaces` 行为不受影响（F094 AGENT_PRIVATE 独立路径）

**Codex review 要点**：
- α 语义的并发风险：多个 Subagent 并发写同一 caller namespace 时的 SQLite WAL 行为（已知 trade-off，在 spec §10 Edge Cases 中有说明）
- caller AGENT_PRIVATE namespace IDs 可能为空的 fallback 场景

**回滚方案**：删除 `if delegation_mode == "subagent"` 块，回到默认创建路径；已有 Subagent task 的 caller_memory_namespace_ids 字段在 task metadata 中仍存在但不影响（新 subagent task 重新走创建路径）

---

### Phase G：BEHAVIOR_PACK_LOADED agent_kind=subagent 验证（~30min）

**AC 映射**：AC-G1, AC-AUDIT-1, AC-COMPAT-1

**注意**：Phase G 无需新增实施代码。Gap-C（Phase C）实施后，`make_behavior_pack_loaded_payload`（`agent_decision.py:352`）读取 `str(agent_profile.kind)` 自动返回 `"subagent"`。Phase G 的工作是**补充验证 AC**。

**改动文件清单**：

| 文件 | 改动类型 | 估算 LOC |
|------|---------|---------|
| `octoagent/apps/gateway/tests/test_task_service_context_integration.py`（已有文件）| 补充 subagent 路径的 BEHAVIOR_PACK_LOADED 断言 | +40 行 |

**验证逻辑**：

```python
# 新增测试：验证 Subagent 路径 BEHAVIOR_PACK_LOADED.agent_kind == "subagent"
# 与 test_task_service_context_integration.py:2369 的 Worker 测试对称

async def test_subagent_behavior_pack_loaded_agent_kind():
    """AC-G1: Subagent dispatch 时 BEHAVIOR_PACK_LOADED.agent_kind == 'subagent'。"""
    # spawn subagent task → dispatch → query EventStore
    events = await event_store.list_events(task_id=subagent_task_id)
    loaded_events = [ev for ev in events if ev.type == EventType.BEHAVIOR_PACK_LOADED]
    assert len(loaded_events) >= 1
    for ev in loaded_events:
        payload = BehaviorPackLoadedPayload.model_validate(ev.payload)
        assert payload.agent_kind == "subagent"  # AC-G1

async def test_existing_worker_agent_kind_unchanged():
    """AC-COMPAT-1: 现有 Worker 路径 agent_kind 仍为 'worker'，不受 F097 影响。"""
    # 原有 test_agent_decision_envelope.py:640 的测试不做修改，继续通过
```

**Codex review 要点**：
- AC-AUDIT-1 四层对齐测试是否需要端到端（`AgentProfile.profile_id → AgentRuntime.profile_id → BEHAVIOR_PACK_LOADED.agent_id → RecallFrame.agent_runtime_id`）

---

### Verify（全量回归 + e2e_smoke + Final Codex Review，~2h）

**AC 映射**：AC-GLOBAL-1 ~ 6, AC-SCOPE-1, AC-EVENT-1

**检查清单**：

```bash
# 1. 全量回归（AC-GLOBAL-1）
pytest --timeout=60 -q
# 目标：≥ 3260 passed（F096 baseline），0 regression

# 2. e2e_smoke（AC-GLOBAL-2）
pytest -m e2e_smoke
# 目标：8/8 PASS

# 3. 范围边界验证（AC-SCOPE-1）
git diff cc64f0c -- \
  "octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py" \
  "octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py"
# 验证 F098/F099/F100 相关文件无改动

# 4. AC-EVENT-1 手工验证（delegate_task 路径 SUBAGENT_SPAWNED）
# 在集成测中查 EventStore，确认 SUBAGENT_SPAWNED 事件存在

# 5. Codex Final cross-Phase review
# /codex:adversarial-review background
# 输入：spec.md + plan.md + all Phase commits
```

**completion-report.md 产出**（AC-GLOBAL-5）：
```
.specify/features/097-subagent-mode-cleanup/completion-report.md
```

---

## §3. BEHAVIOR_PACK_LOADED 消费方实测（C-2 决策派生）

### 3.1 实测时间：plan 阶段 grep 完成（2026-05-10）

### 3.2 BEHAVIOR_PACK_LOADED emit 位置

| 位置 | 文件 | 行号 | 说明 |
|------|------|------|------|
| **唯一 emit 点** | `agent_context.py` | L976-1007 | `build_task_context` 内，cache miss 时调用 `make_behavior_pack_loaded_payload` + `append_event_committed` |

### 3.3 `agent_kind` 字段的所有 reader（代码层）

| 位置 | 文件 | 类型 | 内容 |
|------|------|------|------|
| payload 定义 | `behavior.py:306` | 字段定义 | `agent_kind: str = Field(description="main / worker / subagent；与 AgentProfile.kind 对齐")` — **str 类型，无枚举约束** |
| payload 定义 | `behavior.py:329` | 字段定义（USED 事件）| `agent_kind: str = Field(description="main / worker；F096 仅这两个值，subagent 由 F097 引入")` |
| payload 构造 | `agent_decision.py:352` | 写入 | `agent_kind=str(agent_profile.kind)` — 直接 str 转换，无硬编码值 |
| payload 构造 | `agent_decision.py:389` | 写入（USED 事件）| `agent_kind=str(agent_profile.kind)` — 同上 |
| 单测断言 | `test_agent_decision_envelope.py:640` | 读取（测试）| `assert payload.agent_kind == "worker"` — **针对 Worker 路径的具体值断言，非枚举限制** |
| 集成测查询 | `test_task_service_context_integration.py:2373` | 读取（测试）| `ev for ev in events if ev.type is EventType.BEHAVIOR_PACK_LOADED` — 仅过滤事件类型，不校验 agent_kind 值 |

### 3.4 frontend 消费方

**实测结论**：frontend（`octoagent/frontend/`）无任何 `agent_kind` 引用。F096 Phase E（Memory Console agent 视角 UI）已推迟，当前 frontend 不消费 BEHAVIOR_PACK_LOADED 事件。

### 3.5 兼容性判定

| 检查项 | 结论 |
|--------|------|
| backend 代码中是否有 `agent_kind in ["main", "worker"]` 枚举硬校验 | **无** |
| frontend 是否消费 agent_kind 字段 | **无**（Phase E 推迟）|
| 测试中是否有"只允许 main/worker"的断言 | **无**（`test_agent_decision_envelope.py:640` 是 Worker 路径值断言，与 subagent 路径无关）|
| `behavior.py` 字段类型 | `str`（无 Literal 枚举约束）|

### 3.6 实测结论

**AC-COMPAT-1 自动成立**：

F097 可以直接引入 `"subagent"` 值，无需 schema version bump，无需任何兼容扩展。`test_agent_decision_envelope.py:640` 的 `assert payload.agent_kind == "worker"` 是针对 Worker dispatch 路径的具体测试，F097 新增的 Subagent 路径不影响该测试（两个路径独立）。

---

## §4. Codex Adversarial Review 节点

| Phase | Review 时机 | 模式 | 关注点 |
|-------|------------|------|--------|
| Phase A 完成后 | commit 前 | foreground（代码量小）| SubagentDelegation 字段命名与 F098 兼容性 |
| Phase C 完成后 | commit 前 | foreground | ephemeral profile 路径隔离；不写持久化表 |
| Phase B 完成后 | commit 前 | **background**（最高风险 Phase）| _ensure_agent_session 4 路无交集；现有 3 路 0 regression |
| Phase F 完成后 | commit 前 | foreground | α 语义并发安全；fallback 场景 |
| **Final cross-Phase** | Verify 前 | **background** | 全 Phase 串联：是否漏 Phase / 偏离 spec / 审计链四层对齐 |

**触发命令**：`/codex:adversarial-review`

**高 finding 处理规则**（按 CLAUDE.local.md）：
- 接受 → 修改实现
- 拒绝 → commit message 明确写 "Codex F{N} rejected: 理由"
- 0 high 残留才允许进入 Verify

---

## §5. e2e_smoke 不变量 + 回归基线

### e2e_smoke 不变量

- **每 Phase commit 后必跑**：`pytest -m e2e_smoke`（pre-commit hook 已内置）
- **目标**：8/8 PASS
- **e2e_smoke 不覆盖 Subagent spawn 路径**（已知，F092 completion-report 记录）—— F097 新增 Subagent 路径通过集成测覆盖，不依赖 e2e_smoke

### 回归基线

| 指标 | 值 | 说明 |
|------|-----|------|
| **F096 final passed** | **3260** | CLAUDE.local.md F096 实施记录 |
| F097 任意 Phase 后最低要求 | ≥ 3260 | 0 regression（新增测试允许，总数只增不减）|
| 目标（含 F097 新增测试）| ~3300-3330 | 估算 +40-70 新测试 |

---

## §6. 风险闭环（R1-R5）

| 风险 | Phase 缓解措施 |
|------|-------------|
| **R1 Memory OD-1 走错方向** | 已锁定 α 共享引用（GATE_DESIGN 拍板），Phase F 前无歧义；Phase 0 侦察确认 `_ensure_memory_namespaces` 接口形式 |
| **R2 _ensure_agent_session 破坏 Worker 路径** | Phase B 保守原则：仅 `delegation_mode == "subagent"` 时走新路径；per-Phase Codex review（background）；AC-B2 单测全路径回归 |
| **R3 cleanup hook 时机错误** | cleanup 挂在 `_notify_completion`（task 终态确认后），不在 worker_runtime.run() 内部；幂等保证（AC-E2）；进程重启后 task runner 重新扫描终态 task |
| **R4 ephemeral profile ULID 冲突** | ULID 128 bit 随机，碰撞概率极低；ephemeral profile 不写入持久化表（无唯一键冲突）|
| **R5 cleanup 查询路径多跳** | C-1 决策：`child_agent_session_id` 字段在 spawn 时记录；cleanup 直接用此字段定位（无多跳），避免 `parent_worker_runtime_id` 信号不准问题 |

---

## §7. F098 接入点准备

F097 完成后，以下制品为 F098 提供接入基础：

### 7.1 SubagentDelegation → WorkerDelegation 扩展点

`SubagentDelegation` 的字段命名遵循 F098 WorkerDelegation 可派生惯例：

| 字段 | F097 SubagentDelegation | F098 WorkerDelegation（预期）|
|------|------------------------|------------------------------|
| `delegation_id` | ULID，`str` | 同名，同类型 |
| `parent_task_id` | `str` | 同名，同类型 |
| `caller_agent_runtime_id` | 调用方 runtime | 同名（A2A 场景同样需要记录 caller）|
| `target_kind` | `SUBAGENT` | `WORKER` |
| `closed_at` | Subagent 终态时间 | 同名（生命周期概念相同）|

F098 评估时可提取 `BaseDelegation` 基类，或直接新建 `WorkerDelegation`（YAGNI 保留扩展点即可）。

### 7.2 BEHAVIOR_PACK_LOADED agent_kind 演化

F097 引入 `"subagent"` 后，F098 需要为 A2A Receiver（Worker 模式的独立 agent）引入新的 agent_kind 值（如 `"a2a_worker"` 或复用 `"worker"`）。向后兼容原则：F097 写入的 `"subagent"` 数据在 F098 实施后仍可被正确读取（`str` 类型无约束）。

### 7.3 F096 AC-F1 推迟项（worker_capability 路径）

F096 final review H2 推迟到 F098 的项：`worker_capability` 路径完整 audit chain test（delegate_task fixture 完备 + Worker→Worker 解禁后实施）。F097 不承接此项，由 F098 负责。

### 7.4 `_enforce_child_target_kind_policy` 保持不动

F097 不删除此 policy（Worker→Worker 硬禁止），F098 负责在 Worker→Worker 解禁时删除。

---

## §8. 测试矩阵

| Gap | AC | 单测 | 集成测 | e2e_smoke |
|-----|-----|------|--------|-----------|
| Gap-A SubagentDelegation model | AC-A1/A2/A3 | ✅ round-trip + 字段校验 | — | — |
| Gap-B SUBAGENT_INTERNAL session | AC-B1/B2 | ✅ 4 路路径验证 | ✅ spawn 后 AgentSession kind 检查 | — |
| Gap-C ephemeral AgentProfile | AC-C1/C2 | ✅ store 未调用 + kind 正确 | ✅ agent_profile 表 COUNT 不变 | — |
| Gap-D RuntimeHintBundle 拷贝 | AC-D1/D2 | ✅ SUBAGENT 有字段 / WORKER 无 | — | — |
| Gap-E session cleanup | AC-E1/E2/E3 | ✅ 幂等 + 非 subagent 跳过 | ✅ session CLOSED + RecallFrame 保留 | — |
| Gap-F Memory α 共享 | AC-F1/F2/F3 | ✅ namespace 不创建新 row | ✅ caller 可读 subagent 写入 | — |
| Gap-G agent_kind 验证 | AC-G1 | — | ✅ EventStore agent_kind=subagent | — |
| 兼容性 | AC-COMPAT-1 | ✅ Worker agent_kind=worker 不变 | — | — |
| 事件可观测 | AC-EVENT-1 | — | ✅ SUBAGENT_SPAWNED 在 EventStore | — |
| audit chain | AC-AUDIT-1 | — | ✅ 四层对齐 list_recall_frames | — |
| 全局回归 | AC-GLOBAL-1/2 | — | — | ✅ 8/8 |
| 范围边界 | AC-SCOPE-1 | — | ✅ git diff 验证 | — |

---

## §9. 实施时序粗估

| Phase | 估算耗时 | 备注 |
|-------|---------|------|
| Phase 0（侦察）| ~30min | grep + 读代码，无 commit |
| Phase A（SubagentDelegation model）| ~1h | 低风险，独立文件 |
| Phase C（ephemeral AgentProfile）| ~2h | 中风险，`agent_context.py` 新增路径 |
| Phase E（session cleanup）| ~2h | 中风险，`task_runner.py` 新增路径 |
| Phase B（SUBAGENT_INTERNAL session）| ~3h | **高风险**，需 per-Phase Codex background review |
| Phase D（RuntimeHintBundle 拷贝）| ~1h | 低风险，新增字段 |
| Phase F（Memory α 共享）| ~2h | 中风险，`_ensure_memory_namespaces` 新增路径 |
| Phase G（验证 AC）| ~30min | 低风险，补测试 |
| Verify（全量回归 + Final Codex review）| ~2h | 包含 Codex review 处理时间 |
| **总计** | **~14h** | |

---

## §10. Done Criteria（完成定义）

所有以下条件满足后，F097 视为完成：

**AC 完成度**：

- [ ] AC-A1, A2, A3（SubagentDelegation model）
- [ ] AC-B1, B2（SUBAGENT_INTERNAL session 路径）
- [ ] AC-C1, C2（ephemeral AgentProfile）
- [ ] AC-D1, D2（RuntimeHintBundle 拷贝）
- [ ] AC-E1, E2, E3（session cleanup 幂等）
- [ ] AC-F1, F2, F3（Memory α 共享）
- [ ] AC-G1（BEHAVIOR_PACK_LOADED agent_kind=subagent）
- [ ] AC-AUDIT-1（四层 audit chain 对齐）
- [ ] AC-COMPAT-1（兼容性：main/worker agent_kind 不变）
- [ ] AC-EVENT-1（SUBAGENT_SPAWNED 在 delegate_task 路径存在）
- [ ] AC-SCOPE-1（F098/F099/F100 范围无改动）
- [ ] AC-GLOBAL-1（≥ 3260 passed，0 regression）
- [ ] AC-GLOBAL-2（e2e_smoke PASS）
- [ ] AC-GLOBAL-3（每 Phase Codex review 0 high 残留）
- [ ] AC-GLOBAL-4（Final cross-Phase Codex review 通过）
- [ ] AC-GLOBAL-5（completion-report.md 产出）
- [ ] AC-GLOBAL-6（Phase 跳过显式归档，若有）

**流程完成度**：

- [ ] `completion-report.md` 产出，含"实际 vs 计划"Phase 对照表 + Codex finding 闭环表 + F098 接入点说明
- [ ] 分支 `feature/097-subagent-mode-cleanup` 已 commit（不主动 push origin/master，等用户拍板）

---

## 附录：Codebase Reality Check

| 目标文件 | LOC | 主要接口 | 已知 debt |
|---------|-----|---------|---------|
| `agent_context.py` | 4196 | `build_task_context`, `_ensure_agent_session`, `_resolve_or_create_agent_profile`, `_ensure_memory_namespaces` | 超大文件（4196 行 > 500 LOC 门限）；F093 已做最小拆分（-188 行），本次 F097 新增约 +70 行仍在可控范围 |
| `capability_pack.py` | 2088 | `_launch_child_task`, `_enforce_child_target_kind_policy` | 中等规模；F097 仅新增 +20 行 |
| `task_runner.py` | 670 | `_run_job`, `_notify_completion` | 合理规模；F097 新增 +30 行 |
| `agent_decision.py` | 1376 | `make_behavior_pack_loaded_payload`, `make_behavior_pack_used_payload` | 合理规模；F097 仅补测试（+0 行改动）|
| `delegation.py`（core model）| 326 | `DelegationTargetKind`, `SpawnChildResult` | 合理规模；F097 新增 `SubagentDelegation` +60 行 |
| `agent_context_store.py` | 1653 | `save_agent_session`, `list_subagent_sessions` | 合理规模；F097 复用 `save_agent_session`（0 改动）|

**前置 cleanup 判定**：`agent_context.py`（4196 行）将新增约 +70 行，满足"> 500 行且新增 > 50 行"条件。但该文件已在 F093 做过拆分，本次改动均在新路径（subagent 条件分支），不存在与现有逻辑的纠缠。**判定：不强制前置 CLEANUP task**，改动集中在清晰的 `delegation_mode == "subagent"` 分支内，风险可控。

---

## 附录：Constitution Check

| Constitution 原则 | 适用性 | 评估 | 说明 |
|------------------|--------|------|------|
| **C1 Durability First** | ✅ 适用 | PASS | SubagentDelegation 持久化（child task metadata）；session cleanup 写入 SQLite；RecallFrame 不删除 |
| **C2 Everything is an Event** | ✅ 适用 | PASS（OD-2 说明）| SUBAGENT_SPAWNED（delegate_task 路径）保持；BEHAVIOR_PACK_LOADED.agent_kind=subagent；`subagents.spawn` 不写 SUBAGENT_SPAWNED（OD-2 设计决策，非遗漏）|
| **C7 User-in-Control** | ✅ 适用 | PASS | `subagents.kill` 工具已存在；session cleanup 不影响用户可控性 |
| **C8 Observability is a Feature** | ✅ 适用 | PASS | SubagentDelegation + ephemeral AgentProfile + RecallFrame 完整可审计；BEHAVIOR_PACK_LOADED.agent_kind 正确标记 |
| **C9 Agent Autonomy** | ✅ 适用 | PASS | spawn 时机和工具选择由 LLM 决策；F097 仅增加基础设施（dispatch 层路径判断，非 LLM 决策替代）|
| **C10 Policy-Driven Access** | ✅ 适用 | PASS | DelegationManager gate（depth/concurrent/blacklist）继续生效；ephemeral profile 在 gate 通过后才创建 |

**Constitution Check 结论：全部 PASS，无 VIOLATION。**

---

## 附录：Impact Assessment

| 维度 | 评估 |
|------|------|
| 直接修改文件数 | 6（`agent_context.py` / `capability_pack.py` / `task_runner.py` / `delegation.py` / `delegation_plane.py` 或等价文件 / 新建测试文件若干）|
| 间接受影响文件 | 3（`agent_decision.py` 无修改但补测试；`agent_context_store.py` 复用现有方法）|
| 跨包影响 | 是（`core/models/` + `gateway/services/` 跨包）|
| 数据迁移 | **无**（ephemeral profile 不持久化；SubagentDelegation 写 task metadata，零 migration）|
| API/契约变更 | 无（不改变 delegate_task / subagents.spawn 工具 schema；不改变 list_recall_frames endpoint）|
| **风险等级** | **MEDIUM**（影响文件 < 20，跨包 = 1 边界，无 schema migration，无公共 API 变更）|

**风险等级判定依据**：影响文件 ~9 个（< 20 门限）；跨 1 包边界（core/models → gateway/services）；无数据迁移；无公共 API schema 变更 → MEDIUM。

**无需强制分阶段**（HIGH 风险才需要）；当前 Phase 顺序按"先简后难"已足够。
