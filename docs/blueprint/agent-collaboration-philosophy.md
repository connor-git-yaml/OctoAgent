# §2.3 Agent 协作三条设计哲学（H1 / H2 / H3）

> 本文件是 [blueprint.md](../blueprint.md) §2.3 的完整内容，与 §2 Constitution 同级，是 OctoAgent **多 Agent 协作模型的权威说明**。
> 三条哲学在 M5（F090-F103）期间被显式建模到代码，是后续所有 Agent 协作行为的依据。
> 决策来源：2026-05-05 拍板（架构战略评估 + Worker vs 主 Agent 实测 + 架构债 review 对照 Hermes Agent / OpenClaw / Agent Zero）。

---

## §0 章节定位

OctoAgent 不只是"一个聊天机器人"，而是"个人智能操作系统"。当系统从单一主 Agent 演进到"主 Agent + Worker 群 + Subagent"的多 Agent 协作体系时，需要明确：

- **谁负责跟用户说话？**（H1）
- **每个 Agent 拥有什么上下文栈？**（H2）
- **委托一个任务有几种方式？**（H3）

如果这三个问题答不清楚，多 Agent 系统会迅速退化为"硬编码场景树 + 临时变量传递 + 散落 audit chain"。M5 13 个 Feature 的核心目的就是把这三条哲学落到代码，让"长期助手组织"成为系统的**结构性约束**而不是 prompt 提示。

本章节是 [agent-collaboration-philosophy 实施记录](../../CLAUDE.local.md#m5--m6-战略规划2026-05-05-拍板13-feature-完整版)（CLAUDE.local.md §"M5 / M6 战略规划"）的 Blueprint 权威版本。

---

## §1 三条哲学概览

| 哲学 | 一句话定义 | 对应代码层产出 |
|------|----------|-------------|
| **H1 管家 mediated 模式** | 主 Agent 总是 receive 用户消息 + 总是 reply + 唯一 user-facing speaker；主 Agent 倾向派活，有权 hire/fire/reassign Worker | F100 `RuntimeControlContext.force_full_recall` + `RecallPlannerMode="auto"` 自动决议 |
| **H2 完整 Agent 对等性** | Worker = 主 Agent − {hire/fire/reassign Worker} − {user-facing 表面}；每个 Agent 都有完整上下文栈（Project / Memory / Behavior / Session / Persona / 决策环） | F093-F096 Worker Session / Memory / Behavior / Recall Audit 4 维对等 |
| **H3 两种委托模式并存** | A 临时 Subagent：spawn-and-die，共享调用方 Project/Memory/Context；B A2A 真 P2P：消息送对方，对方在自己的 Project/Memory 工作，可中途 ask back | F097 `SubagentDelegation`（H3-A）+ F098 A2A `WorkerDelegation`（H3-B）+ F099 `source_runtime_kind` 5 值 + ask_back 三工具 |

---

## §2 H1 管家 mediated 模式

### §2.1 定义

主 Agent 是用户与系统的**唯一接触面**：

- **总是 receive**：用户发来的所有消息（Web / Telegram / 其他渠道）都先到主 Agent
- **总是 reply**：用户看到的所有回复都来自主 Agent（不是 Worker / Subagent 直接对用户说话）
- **倾向派活（persona）**：主 Agent 设计上偏好把具体执行任务委托给 Worker，而不是亲自执行 —— 这是为了避免主 Agent 上下文窗口被业务细节淹没
- **有权 hire/fire/reassign**：主 Agent 可创建 Worker、终止 Worker、重新分配任务给不同 Worker

### §2.2 代码层落地

#### F100 `RuntimeControlContext.force_full_recall`（H1 override）

```python
# packages/core/src/octoagent/core/models/orchestrator.py:55（简化）
DelegationMode = Literal["unspecified", "main_inline", "worker_inline", "main_delegate", "subagent"]
RecallPlannerMode = Literal["full", "skip", "auto"]

class RuntimeControlContext(BaseModel):
    # ... task_id / trace_id / project_id 等 20+ 字段省略
    delegation_mode: DelegationMode = Field(default="unspecified")
    turn_executor_kind: TurnExecutorKind = Field(default=TurnExecutorKind.SELF)
    recall_planner_mode: RecallPlannerMode = Field(default="full")
    force_full_recall: bool = False   # F100 引入，H1 主 Agent override（最高优先级）
```

`force_full_recall=True` 时，主 Agent 强制跑 full memory recall（不走 `RecallPlannerMode="auto"` 自动决议）。F101 `chat_control_metadata` 持久化 + `TURN_SCOPED_CONTROL_KEYS` 白名单 + ENV-aware threshold 让该 override 可在 chat turn 维度精确生效。

#### F100 `RecallPlannerMode="auto"` 自动决议

实际实施：`apps/gateway/src/octoagent/gateway/services/runtime_control.py:106` `is_recall_planner_skip()` 函数

```python
# 简化语义（实际逻辑见 runtime_control.py:106）
def is_recall_planner_skip(runtime_context, metadata) -> bool:
    # 优先级 1: force_full_recall override 最高（H1 完整决策环 override）
    if runtime_context.force_full_recall:
        return False  # 强制完整 recall

    # 优先级 2: 显式 delegation_mode + recall_planner_mode
    if runtime_context.delegation_mode != "unspecified":
        if runtime_context.recall_planner_mode == "skip":
            return True
        if runtime_context.recall_planner_mode == "full":
            return False
        # AUTO 决议：依 delegation_mode 自动决议
        if runtime_context.recall_planner_mode == "auto":
            if runtime_context.delegation_mode in {"main_inline", "worker_inline"}:
                return True   # SKIP（F051 inline 性能兼容）
            if runtime_context.delegation_mode in {"main_delegate", "subagent"}:
                return False  # FULL（走完整决策环）

    # 优先级 3: unspecified → return False（baseline 默认行为等价）
    return False
```

#### `NotificationService`（F101）也是 H1 实现

主 Agent 通过 `NotificationService.notify_xxx()` 主动告知用户任务状态变化 —— 用户始终从主 Agent 接收消息（即使消息源是 Worker 的执行结果）。F102 `DailyRoutineService` 是 H1"主动告知"的另一具体落地。

### §2.3 业界对照

| 系统 | 是否 H1 管家模式 | 差异 |
|------|----------------|------|
| **OctoAgent**（本系统）| ✅ 严格 H1 | 主 Agent 是唯一 user-facing speaker，DirectWorkerSession 是未来扩展能力 |
| Claude Code | ✅ Top-level Agent 与 Task subagent 都不直接对用户说话；CLI 是 UI 层 | Top-level Agent 没有 hire/fire 权限语义（Task subagent 是 spawn-and-die）|
| OpenClaw | 部分 H1 | 主 session 是 user-facing；subagents 通过 `subagents.spawn` 工具创建，但产物仍由主 session 综合回复 |
| Agent Zero | 部分 H1 | Agent0 是 user-facing；通过 `call_subordinate` 递归创建子 agent，但子 agent 的回复直接渗透到 Agent0 上下文 |
| Hermes Agent | ✅ 严格 H1 | "Butler" 概念明确；delegate_task 创建的 task 不直接对用户说话 |

### §2.4 不变量

- 任何 Worker / Subagent 不得绕过主 Agent 直接对用户发消息
- 用户的 reply 必须由主 Agent 综合（Worker RESULT/ERROR → 主 Agent 综合 → 对用户发言）
- 未来若开放 `DirectWorkerSession`，必须创建独立的 `worker_direct` session，并维持独立 memory / recall / policy / audit 链，不得复用 user-facing 主链

---

## §3 H2 完整 Agent 对等性

### §3.1 定义

每个 Agent（主 Agent 与 Worker）都拥有完整的上下文栈，差异仅在权限：

```
Worker = 主 Agent − {hire/fire/reassign Worker} − {user-facing 表面}
```

完整上下文栈包括：

1. **Project**：所有者身份 / instructions / memory bindings / secret bindings / asset bindings / channel/A2A routing
2. **Memory**：独立 namespace（AGENT_PRIVATE） + 共享 namespace（PROJECT_SHARED）+ recall runtime
3. **Behavior**：独立 IDENTITY / SOUL / HEARTBEAT + 共享 AGENTS / USER / TOOLS / PROJECT / KNOWLEDGE
4. **Session**：独立 `AgentSession` + rolling_summary + memory_cursor + recent_transcript
5. **Persona**：独立 instruction overlays / role 定位
6. **决策环**：完整 LLM loop + tool broker + recall planner + ask_back 能力

### §3.2 代码层落地（F093-F096 4 维对等）

#### F093 Session 对等

- Worker turn 写入 `AgentSession`（baseline 7 跳 grep 验证已通）
- `rolling_summary` / `memory_cursor` 字段持久化 round-trip OK
- 新增 `AGENT_SESSION_TURN_PERSISTED` event（5 字段 payload）
- agent_context.py 4112→4008 行（D6 拆分，抽出 turn-writer mixin）

#### F094 Memory 对等

- `AGENT_PRIVATE` namespace 真生效（Worker 路径）；main direct 保留 `PROJECT_SHARED`（完整对等留 F107，避免破坏 main direct baseline）
- `RecallFrame.agent_runtime_id` 字段（不是 `agent_id`）
- 审计真实路径：`AgentProfile.profile_id → AgentRuntime.profile_id → RecallFrame.agent_runtime_id`
- 废弃 `WORKER_PRIVATE` 路径（合并进 `AGENT_PRIVATE`）

#### F095 Behavior 对等

`_PROFILE_ALLOWLIST[WORKER]` 5 → **8 文件**（用户 GATE_DESIGN v0.2 翻转决策）：

```
{AGENTS, TOOLS, IDENTITY, PROJECT, KNOWLEDGE, USER, SOUL, HEARTBEAT}
```

**关键决策**：去 BOOTSTRAP 加 USER。
- BOOTSTRAP 是主 Agent 用户首次见面对话脚本，违反 H1（Worker 不直接对用户说话）
- USER 是用户长期偏好对 Worker 有价值

修复 baseline 隐性 bug：原 envelope `share_with_workers AND` 子句剥离 IDENTITY → `IDENTITY.worker.md` 模板渲染了 Worker LLM 永远看不到。新增 `IDENTITY.worker.md` / `SOUL.worker.md` / `HEARTBEAT.worker.md` worker variant 模板。

#### F096 Recall Audit 对等

- `list_recall_frames` audit endpoint（过滤维度：agent_runtime_id / agent_session_id / namespace / project_id / 时间窗）
- `MEMORY_RECALL_COMPLETED` 同步路径 emit（F094 仅 delayed recall path 覆盖）
- `BEHAVIOR_PACK_LOADED` EventStore 接入 + `BEHAVIOR_PACK_USED` 新增
- **AC-7b 四层 audit chain 实测通过**：`AgentProfile.profile_id ↔ AgentRuntime.profile_id ↔ BEHAVIOR_PACK_LOADED.agent_id ↔ RecallFrame.agent_runtime_id`

### §3.3 业界对照

| 系统 | Worker 是否有完整上下文栈 | 差异 |
|------|------------------------|------|
| **OctoAgent**（本系统）| ✅ Session / Memory / Behavior / Recall 4 维对等（F093-F096） | AGENT_PRIVATE namespace 仅 Worker 路径生效（main direct 留 F107 完全对等）|
| Hermes Agent | ✅ delegate_task 创建的 task 有独立 session / memory / behavior | OctoAgent 直接参照其设计 |
| OpenClaw | 部分 | session-key + memory partition，但 worker 是 ephemeral subagent，没持久化 |
| Agent Zero | ✅ 每个 Agent 有自己的 project + memory + history | 但所有 Agent 都通过 `call_subordinate` 递归创建，没有 Worker / Subagent 概念区分 |
| Claude Code | 部分 | Task subagent 有独立 context window，但共享父 Task 的 session / memory；没有持久化 worker |

### §3.4 不变量

- Worker 拥有的所有上下文 = 主 Agent 拥有的上下文 − {user-facing 表面 + Worker 管理权限}
- Worker 不得读取主 Agent Session 的完整历史（必须通过 `context_capsule_ref` 显式声明授权与 provenance）
- Worker 写入 memory 默认 `AGENT_PRIVATE` namespace，跨 Agent 共享需走 `PROJECT_SHARED`

---

## §4 H3 两种委托模式并存

### §4.1 定义

OctoAgent 同时支持两种委托模式：

#### H3-A 临时 Subagent（spawn-and-die）

- 调用方需要"分担一小段思考 / 执行"，但不希望对方拥有独立上下文
- 受派方共享调用方的 Project / Memory / Context
- 任务完成后整个 session 可回收
- 类比：函数调用（共享内存）

#### H3-B A2A 真 P2P（持续协作）

- 调用方需要把任务**真正委托**给另一个 Agent
- 受派方在自己的 Project / Memory 工作，与调用方上下文隔离
- 受派方可中途 ask back（澄清需求）
- request 来源泛化：butler / user / worker / automation 等不同来源
- 类比：进程间消息（隔离内存）

### §4.2 代码层落地

#### F097 H3-A：`SubagentDelegation`

```python
# packages/core/src/octoagent/core/models/delegation.py:371
class SubagentDelegation(BaseDelegation):
    """F097：H3-A 临时 Subagent 委托的结构化载体。
    F098 Phase J 继承 BaseDelegation（共享字段下沉到父类）。
    持久化路径：child_task.metadata["subagent_delegation"]（JSON 序列化，无独立 SQL 表）"""

    # SubagentDelegation 专属字段：
    child_agent_session_id: str | None = None     # SUBAGENT_INTERNAL session ID
    caller_project_id: str                         # α 共享：receiver 复用 caller project
    caller_memory_namespace_ids: list[str]         # α 共享 AGENT_PRIVATE namespace IDs
    target_kind: Literal[DelegationTargetKind.SUBAGENT] = DelegationTargetKind.SUBAGENT
    # 父类 BaseDelegation 提供 delegation_id / parent_runtime_id 等公共字段
```

- ephemeral `AgentProfile (kind=subagent)`
- `SUBAGENT_INTERNAL` session 路径（与 A2A receiver session 区分）
- cleanup hook + `SUBAGENT_COMPLETED` event emit
- Memory α 共享引用（caller `AGENT_PRIVATE`），通过 `caller_memory_namespace_ids` 字段直接传递
- ephemeral runtime 独立路径（F098 P1-2 修复）：audit chain 严格隔离

#### F098 H3-B：A2A `WorkerDelegation`

- A2A source+target 双向独立加载
- **删除 `_enforce_child_target_kind_policy`（关闭 D14 Worker↔Worker 硬禁止）**
- `BaseDelegation` 公共抽象提取（F097/F098 共享）
- D7 真实施：`A2ADispatchMixin` 15 helpers（972 行）抽到 `dispatch_service.py`，行为零变更

#### F099 source_runtime_kind 5 值 + ask_back 三工具

```python
# packages/core/src/octoagent/core/models/source_kinds.py
MAIN = "main"
WORKER = "worker"
SUBAGENT = "subagent"
AUTOMATION = "automation"      # F099 新增
USER_CHANNEL = "user_channel"  # F099 新增
KNOWN_SOURCE_RUNTIME_KINDS = frozenset({MAIN, WORKER, SUBAGENT, AUTOMATION, USER_CHANNEL})
```

A2A source 派生**仅信任**显式 `envelope.metadata.source_runtime_kind` 信号（缺信号默认 `main`）。F098 Phase D post-review 抓到的 bug：baseline 用 `turn_executor_kind` 派生 source role（target 侧字段）→ 主 Agent 派 worker 时 source 误判 worker。修复后必须显式 signal。

三工具（`apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py`）：

```python
# 简化签名（实际 handler 见源文件）
async def ask_back_handler(question: str, context: str = "") -> str
    """Worker 向用户/主 Agent 提问，任务进入 WAITING_INPUT"""

async def request_input_handler(prompt: str, expected_format: str = "") -> str
    """请求结构化输入（JSON/配置/代码片段）"""

async def escalate_permission_handler(action: str, scope: str, reason: str) -> str
    """向用户申请敏感操作的执行权限，走 ApprovalGate SSE（F101 production 接入）"""
```

统一 emit `CONTROL_METADATA_UPDATED` 审计事件。**N-H1 修复**：is_caller_worker resume 持久化通过 CONTROL_METADATA_UPDATED 事件机制。

### §4.3 H3-A vs H3-B 对照

| 维度 | H3-A SubagentDelegation | H3-B A2A WorkerDelegation |
|------|-------------------------|---------------------------|
| 生命周期 | spawn-and-die（短）| 长生命周期 |
| Project | 共享 caller project | receiver 在自己 project |
| Memory | α 共享 caller AGENT_PRIVATE | receiver 独立 namespace |
| AgentRuntime | ephemeral（F098 P1-2 修复后独立）| 独立 |
| AgentSession | SUBAGENT_INTERNAL kind | A2A receiver session |
| ask_back 能力 | 不支持（属于函数调用语义）| ✅ 支持（F099 三工具） |
| 主要使用场景 | 调用方做不完的小段思考 | 跨 Agent 真协作 |

### §4.4 业界对照

| 系统 | H3 委托模式 | 差异 |
|------|-----------|------|
| **OctoAgent**（本系统）| ✅ H3-A + H3-B 并存，显式建模 | 是当前唯一同时显式支持两种模式的开源 Agent 系统 |
| Hermes Agent | ✅ H3-A subagent + H3-B delegate_task（max_depth=2 限制） | OctoAgent 直接参照其设计 |
| OpenClaw | 部分 H3-A | `subagents.spawn` 工具创建临时子 agent，没 H3-B 真 P2P；session 推送模式不够灵活 |
| Agent Zero | 部分 H3-A | `call_subordinate` 单一接口，共享 project，没 H3-B；无持久化通信 |
| Claude Code | 部分 H3-A | Task tool 创建 subagent，共享 context；没 H3-B 真 P2P |
| OpenAI Swarm | 部分 H3-B | handoff 模式（A 把控制权交给 B）；但没持久化、没 ask_back、没并存的 H3-A |
| CrewAI | 部分 H3-B | role-based crew + Pipeline 调度；没 ask_back 中途澄清，没 H3-A 短生命周期 |

### §4.5 不变量

- **F098 已关闭 D14**：Worker→Worker 委托现在合法（必须走 H3-B A2A）
- H3-A 不能跨 Project（共享 caller project 是定义）
- H3-B 不能共享 Memory α（隔离 namespace 是定义）
- A2A source 派生仅信任显式 `envelope.metadata.source_runtime_kind`（缺信号默认 `main`）
- `escalate_permission` 必须走 ApprovalGate SSE（F101 接入），不得绕过 Two-Phase Approval（Constitution 4）

---

## §5 业界对照横向定位表

| 系统 | H1 管家模式 | H2 完整对等性 | H3-A 临时 Subagent | H3-B A2A 真 P2P | 总体定位 |
|------|----------|-------------|---------------------|----------------|---------|
| **OctoAgent** | ✅ 严格 | ✅ 4 维对等 | ✅ SubagentDelegation | ✅ A2A WorkerDelegation + ask_back | 三条哲学全部显式建模到代码 |
| Hermes Agent | ✅ 严格 | ✅ 有 | ✅ subagent | ✅ delegate_task | OctoAgent 直接参照其设计 |
| OpenClaw | 部分 | 部分（session-key） | ✅ subagents.spawn | ❌ | session 推送模式，没 P2P |
| Agent Zero | 部分 | ✅ 有 project | ✅ call_subordinate | ❌ | 单一委托接口，递归模式 |
| Claude Code | ✅ | 部分（共享 context）| ✅ Task tool | ❌ | TS-only，短生命周期为主 |
| OpenAI Swarm | 部分 | ❌ | ❌ | 部分（handoff） | 无持久化、无并存模式 |
| CrewAI | 部分 | 部分 | ❌ | 部分（Pipeline） | role-based，无 ask_back |

### §5.1 OctoAgent 的独特性

OctoAgent 是**当前唯一同时显式支持 H1 + H2 + H3-A + H3-B 全部四条**的开源 Agent 系统：

- H1 严格：主 Agent 是唯一 user-facing speaker，DirectWorkerSession 是未来扩展能力
- H2 4 维对等：Session / Memory / Behavior / Recall Audit 全部代码层落地
- H3-A 显式建模：`SubagentDelegation` Pydantic model，与 H3-B 严格区分
- H3-B 真 P2P：A2A WorkerDelegation + ask_back 三工具 + source_runtime_kind 5 值

参照系统 Hermes Agent 同样实现了 H1 + H2 + H3，但**未开源**；OctoAgent 是其设计的开源落地。

### §5.2 设计哲学 vs 实施约束

哲学是方向，实施会有保守路径。M5 期间确实保留了几处与哲学不完全一致的实施：

- **F094 AGENT_PRIVATE 仅 Worker 路径生效**：main direct 保留 PROJECT_SHARED，完整对等留 F107（避免破坏 main direct baseline，渐进式演进）
- **F090 D2 WorkerProfile 类完全保留**：仅加 kind 字段，独立 SQL 表 + revision 机制 + FE 类型推迟到 F107 完全合并
- **F107 Capability Layer Refactor** 会清理这些保守路径

这些保守是**有意为之**——CLAUDE.md §"代码规范"原则"先从长期演进视角判断更合理的整体架构"在 M5 实践为"先建立哲学骨架，再渐进式收口边角"。

---

## §6 三条哲学的耦合性

三条哲学相互独立但有协同：

- **H1 单独不够**：如果只做 H1（主 Agent 唯一 user-facing），但 Worker 没完整上下文（违反 H2），那 Worker 实际上是无状态执行器，主 Agent 仍要承担全部决策
- **H2 单独不够**：如果只做 H2（Worker 完整对等），但没有 H3 区分两种委托，那所有委托都退化为"复制粘贴 + spawn-and-die"，无法支持长协作
- **H3 单独不够**：如果只做 H3（两种委托模式），但 Worker 上下文不完整（违反 H2），那 H3-B 真 P2P 模式没有 receiver 自己的"主场"
- **三者协同**：H1（管家说话）+ H2（Worker 有家）+ H3（两种委托）=> 真正的"长期助手组织"

---

## §7 引用

- M5 13 Feature 完整实施记录：[CLAUDE.local.md §"M5 / M6 战略规划"](../../CLAUDE.local.md)
- F100 H1 落地：[architecture-audit.md §14.12 F100](architecture-audit.md#-f100-decision-loop-alignmenth1-主-agent-override)
- F093-F096 H2 落地：[architecture-audit.md §14.11](architecture-audit.md#1411-f093-f096-worker-完整对等审计)
- F097-F099 H3 落地：[architecture-audit.md §14.12](architecture-audit.md#1412-f097-f100-委托模式两路分离审计)
- A2A envelope + source_runtime_kind：[api-and-protocol.md §10.2](api-and-protocol.md#102-kernel--workera2a-lite-envelope)
- 三层消息模型（Work × DispatchEnvelope × A2AMessage）：[codebase-architecture/message-model.md](../codebase-architecture/message-model.md)（F103 D13 关闭）

---
