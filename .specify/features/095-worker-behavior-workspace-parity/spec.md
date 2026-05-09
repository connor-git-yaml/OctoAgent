# F095 Worker Behavior Workspace Parity — Spec（Codex review #1 闭环版）

| 字段 | 值 |
|------|-----|
| Feature ID | F095 |
| 阶段 | M5 阶段 1（Worker 完整对等性，与 F094 并行）|
| 主责设计哲学 | H2 完整 Agent 对等性 |
| 前置依赖 | F092（DelegationPlane 统一）+ F093（Worker Full Session Parity）|
| 并行 Feature | F094 Worker Memory Parity（独立 worktree，独立改动域）|
| baseline | 284f74d |
| 分支 | feature/095-worker-behavior-workspace-parity |
| 状态 | spec drafted（GATE_DESIGN 通过，Codex review #1 全闭环）|
| 修订记录 | v0.1 GATE_DESIGN 通过 → v0.2 Codex review #1 闭环（USER/BOOTSTRAP 决策翻转、Phase 顺序调整、AC 扩展）|

---

## 1. 目标（Why）

让 Worker 在 LLM context 里能看到与主 Agent **同等深度**的 4 层 behavior（ROLE / COMMUNICATION / SOLVING / TOOL_BOUNDARY），加上 BOOTSTRAP layer 的 lifecycle 子集（HEARTBEAT），而不只是当前的部分子集。这样 Worker 才能在 H2 完整对等性下独立承担专业领域工作，不依赖主 Agent 的隐性偏好补全。

**反命题**：F095 不让 Worker 也获得 user-facing 表面（H1 仍由主 Agent 独占）；不放开 Worker 的 hire/fire 权（F098 范围）。

---

## 2. 实测侦察对照（块 A 验收）

> spec 阶段实测在 baseline 284f74d，文件路径已校正为实际仓库布局。

### 2.1 关键路径

| 项 | 实际路径 | 说明 |
|----|----------|------|
| BehaviorLoadProfile.WORKER 白名单 | `octoagent/packages/core/src/octoagent/core/behavior_workspace.py:119-127` | `_PROFILE_ALLOWLIST[WORKER]` |
| envelope 二次过滤 | `octoagent/apps/gateway/src/octoagent/gateway/services/agent_decision.py:319-342` | `build_behavior_slice_envelope` |
| Worker LLM context 注入点 | `octoagent/apps/gateway/src/octoagent/gateway/services/agent_decision.py:495-508` | `effective_layers = ... if load_profile == WORKER` |
| IDENTITY.worker.md 模板表 | `octoagent/packages/core/src/octoagent/core/behavior_workspace.py:100-103` | `_BEHAVIOR_TEMPLATE_VARIANTS` |
| 模板派发 | `octoagent/packages/core/src/octoagent/core/behavior_workspace.py:1490-1494` | `_template_name_for_file` |
| 9 个文件 share_with_workers 默认值 | `octoagent/packages/core/src/octoagent/core/behavior_workspace.py:1362-1467` | `_build_file_templates` |

### 2.2 prompt 假设 vs 实测对照

| 项 | prompt 假设 | 实测 baseline | 偏离 |
|----|-------------|---------------|------|
| `BehaviorLoadProfile.WORKER` 白名单数 | 5 文件 | 5 文件（AGENTS / TOOLS / IDENTITY / PROJECT / KNOWLEDGE）| ✅ 一致 |
| 主 Agent FULL 白名单数 | 9 文件 | 9 文件（CORE 6 + ADVANCED 3）| ✅ 一致 |
| Worker 真正进入 LLM context 的文件数 | 5（隐含）| **4** ← envelope 二次过滤剥离 IDENTITY | ⚠️ 重要偏离 |
| `share_with_workers` 是否真过滤 | "可能存在且无必要" | ✅ 真在过滤：`build_behavior_slice_envelope` line 329 `share_with_workers AND in worker_allowlist` 双 AND | ⚠️ bug 不是冗余 |
| `IDENTITY.worker.md` 是否真消费 | 可能根本没引用 | ✅ 模板存在 + `_BEHAVIOR_TEMPLATE_VARIANTS` 引用 + `_default_content_for_file` 调用；**但 envelope 把 IDENTITY 剥了**——模板渲染了 Worker 永远看不到 | ⚠️ 更严重 bug |
| `SOUL.worker.md` 默认模板 | 实测确认 | ❌ 不存在 | 缺失，Phase B 新建 |
| `HEARTBEAT.worker.md` 默认模板 | 实测确认 | ❌ 不存在 | 缺失，Phase B 新建 |
| `BEHAVIOR_LOADED` 事件 | "应有" | ❌ 仓库内 0 命中 | 块 D 新建 |

### 2.3 BOOTSTRAP.md / USER.md 内容实测（Codex review #1 触发的实测）

| 文件 | 实测内容（节选）| 对 Worker 适配性 |
|------|----------------|------------------|
| **BOOTSTRAP.md** | "你好！我是 `__AGENT_NAME__`。这是我们第一次见面，我想先花一点时间认识你"... "用自然的对话方式依次了解：称呼 / 给我起个名字 / 沟通风格 / 时区 / 其他偏好"... "完成引导后使用 behavior.write_file 标记 `__BOOTSTRAP_COMPLETED_MARKER__`"... 用 `behavior.propose_file` 改 IDENTITY.md / SOUL.md | ❌ **完全不适合 Worker**：含 user-facing 对话引导脚本 + 主 Agent onboarding 完成标记机制 + 跨文件 propose 越权 |
| **USER.md** | "## 用户画像 ... 主要语言: 中文 / 回复风格 / 信息组织: 优先回答…避免冗长背景铺垫 / 确认偏好 / 活跃时段 / 任务偏好"... "稳定事实应通过 Memory 服务写入" | ✅ **适合 Worker**：纯偏好参考（语言 / 格式 / 确认风格），无 user-facing 指令；Worker 也需对齐（如写 commit message 中文） |

### 2.4 baseline production grep 命中数

| 关键 token | production 文件 | 命中数 | F094 改动域是否重叠 |
|------------|-----------------|-------|--------------------|
| `BehaviorLoadProfile.WORKER` | agent_context.py / agent_decision.py / behavior_workspace.py | 5 | ✅ 不重叠 |
| `share_with_workers` | agent_decision.py / behavior_workspace.py / models/behavior.py / behavior_commands.py | 14 | ✅ 不重叠 |
| `IDENTITY.worker` | behavior_workspace.py | 1（`_BEHAVIOR_TEMPLATE_VARIANTS`）| ✅ 不重叠 |

### 2.5 9 个文件 default 矩阵

| file_id | layer | share_with_workers | 当前 WORKER 白名单 | 当前实际进 envelope | 主 Agent 是否进 |
|---------|-------|-------------------:|:--------------------|:---------------------|:----------------|
| AGENTS.md | ROLE | True | ✓ | ✓ | ✓ |
| TOOLS.md | TOOL_BOUNDARY | True | ✓ | ✓ | ✓ |
| PROJECT.md | SOLVING | True | ✓ | ✓ | ✓ |
| KNOWLEDGE.md | SOLVING | True | ✓ | ✓ | ✓ |
| **IDENTITY.md** | ROLE | **False** | ✓ | **✗（被 envelope 剥离）** | ✓ |
| USER.md | COMMUNICATION | True | ✗ | ✗ | ✓ |
| BOOTSTRAP.md | BOOTSTRAP | True | ✗ | ✗ | ✓（未完成时）|
| SOUL.md | COMMUNICATION | False (advanced) | ✗ | ✗ | ✓ |
| HEARTBEAT.md | BOOTSTRAP | False (advanced) | ✗ | ✗ | ✓ |

**baseline Worker LLM context 实际只有 4 个文件**：AGENTS / TOOLS / PROJECT / KNOWLEDGE。

---

## 3. 范围（What）

### 3.1 In Scope

- **块 A 实测侦察**：本节 §2 已完成。
- **块 B 白名单扩展 + 双过滤收敛**：
  - `_PROFILE_ALLOWLIST[WORKER]` 扩到 8 项 = `{AGENTS, TOOLS, IDENTITY, PROJECT, KNOWLEDGE, USER, SOUL, HEARTBEAT}`（去 BOOTSTRAP 加 USER；详见 §6 决策）
  - `build_behavior_slice_envelope` 移除 `share_with_workers AND` 子句（白名单本身已是过滤源）
  - 保留 `share_with_workers` 字段：UI / `behavior_commands` 仍读它显示，**只去掉 envelope 过滤**
  - 保留 `BehaviorSliceEnvelope.shared_file_ids` 字段名但 docstring 显式说明语义变更（"profile 白名单内文件 ID 列表"，不再是"share_with_workers=True 文件 ID 列表"）
  - effective_behavior_source_chain 显示 Worker 加载 8 文件覆盖 ROLE / COMMUNICATION / SOLVING / TOOL_BOUNDARY 4 层 H2 核心 + BOOTSTRAP lifecycle layer
- **块 C 私有 worker 模板**：
  - 新增 `SOUL.worker.md` 模板（聚焦专业领域 + 简洁高效 + 不主动与用户对话）
  - 新增 `HEARTBEAT.worker.md` 模板（按主 Agent 节奏运行 + 通过 A2A 回报 + 不直接面向用户）
  - **不新增** `BOOTSTRAP.worker.md`（决策见 §6.2 — BOOTSTRAP 整体不扩入 Worker 白名单）
  - `_BEHAVIOR_TEMPLATE_VARIANTS` 增 `(SOUL.md, True)` / `(HEARTBEAT.md, True)` 条目，最终含 3 个 worker variant 条目
  - Worker 创建路径自动渲染 worker variant
- **可观测**：新增 `BEHAVIOR_PACK_LOADED` 事件，每次 `resolve_behavior_pack` cache miss 时 emit 一次（payload 见 AC-5）；BehaviorPack 增 `pack_id` 字段供 F096 USED 事件引用
- **测试覆盖**：
  - WORKER profile 装载 8 个文件
  - Worker LLM context 真能看到 IDENTITY / SOUL / HEARTBEAT / USER 内容
  - IDENTITY.worker.md / SOUL.worker.md / HEARTBEAT.worker.md 模板差异化测试
  - share_with_workers=False 不再剥离白名单内的文件
  - BehaviorSliceEnvelope 全部消费者列表（plan §0 contract audit）

### 3.2 Out of Scope（明确排除）

- ❌ Worker session / turn 写入（F093 已完成）
- ❌ Worker memory namespace 隔离（F094 范围 — 并行）
- ❌ RecallFrame `agent_id` 填充（F096 范围）
- ❌ Subagent / A2A / Ask-Back 路径（F097-F099）
- ❌ D6 `agent_context.py` 拆分（F093 已做最小拆分）
- ❌ D2 `WorkerProfile` 完全合并（F107）
- ❌ `share_with_workers` 字段彻底删除（保留作为 UI / behavior_commands 提示信号；只去掉 envelope 过滤逻辑）
- ❌ `BEHAVIOR_PACK_USED` 事件（F095 仅 emit LOADED；F096 实现 USED 引用 `pack_id`）
- ❌ **F094 改动文件**：`packages/memory/` / RecallFrame / `agent_context.py` 中 recall preferences / migrate-094

---

## 4. 行为预期（Acceptance Criteria）

### AC-1：Worker 4 层 H2 核心 + BOOTSTRAP lifecycle layer 完整覆盖

**Given** 一个 Worker 通过 `delegate_task` 被派发，
**When** Worker 进入 LLM 决策环，
**Then** `behavior_layers` 至少包含 ROLE（IDENTITY + AGENTS）、COMMUNICATION（USER + SOUL）、SOLVING（PROJECT + KNOWLEDGE）、TOOL_BOUNDARY（TOOLS）四层 H2 核心；并包含 BOOTSTRAP layer（仅 HEARTBEAT，不含 BOOTSTRAP.md）。

### AC-2：IDENTITY.worker.md 真到 LLM context + prompt 拼接优先级

**AC-2a Envelope**：Given 一个 Worker AgentProfile（`kind=worker`），When `resolve_behavior_pack(load_profile=WORKER)` + `build_behavior_slice_envelope`，Then envelope.shared_file_ids 包含 `IDENTITY.md`，且其 content 来自 `IDENTITY.worker.md` 模板（不是 `IDENTITY.main.md`）。

**AC-2b Prompt 拼接**：Given Worker decision loop 拼接 system prompt，When 同时存在 IDENTITY.worker.md 内容与 `delegate_task additional_instructions`，Then 两者来源、顺序、冲突解决策略可观测、可断言（不允许后注入指令静默覆盖 IDENTITY layer 的 ROLE 约束）。

### AC-3：share_with_workers 双过滤收敛

**Given** 任一 BehaviorPackFile（`share_with_workers=True` 或 `False`），
**When** `build_behavior_slice_envelope` 处理，
**Then** 仅按 `_PROFILE_ALLOWLIST[WORKER]` 过滤，不再叠加 `share_with_workers` 子句。
**And** 已有的 `share_with_workers` 字段保留，UI / `behavior_commands` 输出仍显示。
**And** `BehaviorSliceEnvelope.shared_file_ids` 字段名保留但 docstring 显式说明语义变更（"profile 白名单文件 ID 列表"）。

### AC-4（v0.3 调整）：Worker advanced 模板自动初始化 + production 路径覆盖

**v0.3 调整原因**：实施时确认 Worker 创建 production 路径在 `agent_service.py:683-688`
（`ensure_filesystem_skeleton` → `materialize_agent_behavior_files(is_worker_profile=True)`），
而非由 `delegate_task` tool 直接触发——`delegate_task` 是消息派发而非 Worker AgentProfile
首次创建。spec AC-4 测试范围调整为覆盖真实 production 路径。

**AC-4a Worker 创建路径模板派发**：Given 一个 Worker AgentProfile 首次创建（`kind="worker"` 或 metadata 携带 `worker_profile_mirror`），When `build_default_behavior_workspace_files(include_advanced=True)` 被调用（任一 Worker 创建路径），Then 渲染 IDENTITY.worker.md / SOUL.worker.md / HEARTBEAT.worker.md 三个 variant 模板。

**AC-4b Production filesystem 路径端到端**：Given `ensure_filesystem_skeleton + materialize_agent_behavior_files(is_worker_profile=True)` 模拟 production worker 创建路径（与 `agent_service.py:683-688` 同序），When `resolve_behavior_workspace(load_profile=WORKER)`，Then 返回的 workspace.files 包含 8 文件含 worker variant 内容（SOUL "服务对象 = 主 Agent" / HEARTBEAT "通过当前 Worker 回报通道" 等特征短语）。

**`delegate_task` tool 集成测推迟到 F096**：F096 实施 BEHAVIOR_PACK_USED 事件 + EventStore 接入时，自然需要 dispatch 端到端测，可一并覆盖 `delegate_task → worker_service.create_worker → workspace 初始化 → BEHAVIOR_PACK_LOADED emit` 完整路径。F095 提供的 helper / fixture 可被 F096 复用。

### AC-5：BEHAVIOR_PACK_LOADED schema + pack_id 可追溯（F095 范围 infrastructure；emit 推迟 F096）

**v0.3 调整原因**：实施时发现 sync/async 边界硬约束（`resolve_behavior_pack` sync + `EventStore.append_event` async + 所有 production caller 都 sync），完整接入 EventStore 需要 invasive async refactor 超出 F095 minimal 范围。**用户 Phase D 范围决策已确认采用 infrastructure ready + emit 推迟 F096 方案**。

**F095 范围（已实施）**：

**AC-5a Schema + helper ready**：Given 任一 `resolve_behavior_pack` 调用，When 命中 cache miss，Then 返回的 `BehaviorPack` 在 `metadata` 标记 `cache_state="miss"` + `pack_source`（"filesystem" / "default"）；caller 可调用 `make_behavior_pack_loaded_payload(pack, agent_profile, load_profile)` 生成 `BehaviorPackLoadedPayload` 实例（10 字段对齐下方）。

**AC-5b Cache hit 不污染**：cache hit 时返回的 pack 不带 `cache_state="miss"`/`pack_source` 标记，caller 据此跳过 emit。

**AC-5c Pack_id F096 引用前提**：`BehaviorPack.pack_id` hash 化（profile_id + load_profile + source_chain + per-file content sha256），同 input → 同 pack_id；同 file_id 同字符数不同内容 → 不同 pack_id。F096 BEHAVIOR_PACK_USED 事件通过 `pack_id` 关联到 F095 LOADED schema。

**Payload 字段（BehaviorPackLoadedPayload）**：`pack_id` / `agent_id` / `agent_kind` / `load_profile` / `pack_source` / `file_count` / `file_ids` / `source_chain` / `cache_state` / `is_advanced_included`。

**F096 范围（推迟）**：
- 实际 EventStore.append_event 接入 BEHAVIOR_PACK_LOADED 事件（async caller 注入）
- BEHAVIOR_PACK_USED 事件每次 LLM 决策环 emit
- 通过 pack_id 双向关联 LOADED ↔ USED 形成完整可审计链路

### AC-6：行为零变更（所有 non-WORKER profile）

**Given** 一个非 WORKER profile 的 AgentProfile（main with FULL profile / subagent with MINIMAL profile / 任何代码内现存的其他 profile），
**When** spec 改动落地后，
**Then** 该 profile 的 LLM context 行为 100% 等价 baseline 284f74d（文件清单一致、source_chain 一致、IDENTITY.main.md 选择正确、token 计数一致）。

### AC-7：F094 不重叠 + 集成验证

**AC-7a 文件级低冲突**：Given F095 完成后的 commit chain，When `git diff baseline..HEAD --stat`，Then 不改动 F094 域文件清单（`packages/memory/`、RecallFrame schema、`agent_context.py` recall planner 区域、migrate-094 CLI）。

**AC-7b（v0.3 调整）F094/F095 间接关联 partial 验证**：

实施时发现 F094 RecallFrame schema **没有 agent_id 字段**（实际是 `agent_runtime_id` / `agent_session_id` / `task_id` / `project_id`），AC-7b 无法直接做"双 agent_id 一致性"的端到端断言。

**实际间接关联路径**：
```
F095 BehaviorPackLoadedPayload.agent_id (= AgentProfile.profile_id)
    ↓
AgentRuntime.profile_id（生产时 worker dispatch 创建 AgentRuntime）
    ↓
F094 RecallFrame.agent_runtime_id
```

**F095 范围（已实施 partial 验证）**：单测 `test_ac_7b_double_agent_id_consistency` 验证 F095 自身：`payload.agent_id == agent_profile.profile_id`，保证 F095 helper 生成的 payload.agent_id 与 AgentProfile.profile_id 同 source。

**F096 范围（完整集成验证推迟）**：F096 集成测覆盖 AgentRuntime 表的 (profile_id ↔ runtime_id) 映射，对齐两侧 audit；同一 worker dispatch 产生的 BEHAVIOR_PACK_LOADED.agent_id 经 AgentRuntime 关联到 RecallFrame.agent_runtime_id 必一致。

理由：完整 dispatch e2e 集成测需要 BEHAVIOR_PACK_LOADED 事件真实 emit 到 EventStore（AC-5 推迟到 F096），此时 F095 单独跑端到端集成测无对齐对象；F096 接入 EventStore 后两个集成测合并实施更高效。

---

## 5. 设计哲学映射

| 哲学 | F095 落地点 |
|------|-------------|
| **H2 完整 Agent 对等性** | Worker 加载 8 文件覆盖 4 层 H2 核心 + BOOTSTRAP lifecycle layer；私有 SOUL/HEARTBEAT/IDENTITY variant 表达专业 persona 而非 user-facing persona |
| H1 管家 mediated 模式 | F095 **不动**：Worker 仍无 user-facing 表面（SOUL.worker.md 显式约束"不主动与用户对话"；BOOTSTRAP.md 不扩入即避免 user-facing onboarding 脚本泄漏给 Worker）|
| H3 委托模式两路分离 | F095 **不动**：H3-A subagent 共享调用方 behavior 仍由 capability_pack 处理（F097 范围）；F095 只改"Worker 自身 behavior 加载" |

---

## 6. 关键决策（Codex review #1 闭环版）

### 6.1 USER.md 是否扩入 Worker 白名单？→ **扩入**（v0.2 翻转）

**v0.1 原决策**：不扩入（H1 论证）
**v0.2 修订决策**：**扩入**

**论据（实测驱动）**：
- 实测 USER.md 内容 = 用户**长期偏好**（主要语言:中文 / 回复风格 / 信息组织 / 确认偏好 / 活跃时段 / 任务偏好），**没有 user-facing 对话指令**
- "Worker 看到偏好"≠"Worker 直接对话用户"——H1 仍由 SOUL.worker.md "不主动与用户对话" 约束守住
- Worker 写 commit message / 任务报告 / 错误信息时也需对齐用户偏好（中文、信息组织风格等）
- 让主 Agent 在 `delegate_task additional_instructions` 显式传 USER 偏好是隐含人工同步协议，违反 H2 完整对等

**反对意见处理**：H1 哲学仍由 SOUL.worker.md 内容守住，USER.md 扩入只增加偏好可见性，不增加对话权。

### 6.2 BOOTSTRAP.md 是否扩入 Worker 白名单？→ **不扩入**（v0.2 翻转）

**v0.1 原决策**：扩入沿用通用 BOOTSTRAP.md 模板
**v0.2 修订决策**：**不扩入**

**论据（实测驱动）**：
- 实测 BOOTSTRAP.md 实际内容是**主 Agent 用户首次见面对话脚本**：
  - "你好！我是 __AGENT_NAME__。这是我们第一次见面"
  - "用自然的对话方式依次了解：称呼 / 给我起个名字 / 沟通风格 / 时区"
  - "完成引导后使用 `behavior.write_file` 标记 `__BOOTSTRAP_COMPLETED_MARKER__`"
  - "通过 `behavior.propose_file` 更新 IDENTITY.md / SOUL.md"
- 完全是 user-facing onboarding，含跨文件 propose 越权指令
- Worker 没有"首次见面"周期 — Worker 是主 Agent 派发的 specialist
- "派发后 ramp-up" 已由 IDENTITY.worker.md（身份信息 + A2A 状态机回报）+ HEARTBEAT.worker.md（自检节奏）覆盖
- 不新建 BOOTSTRAP.worker.md：避免引入"Worker 也有 onboarding"的概念漂移；如果未来真需要 Worker ramp-up 提示，应在 IDENTITY.worker.md 末尾加节，而不是新建 lifecycle 文件

### 6.3 share_with_workers 字段保留 vs 删除？→ **保留字段，只去掉 envelope 过滤**

**论据**：
- UI / behavior_commands.py:113,165 / models/behavior.py:78,114 仍读它显示给用户（用户看 behavior 列表时知道哪些"标记为 worker 共享"）
- 字段语义从"运行期过滤源"降级为"显示提示"，符合 D1 双轨保守路径（F091 沿用过的 pattern）
- 完全删除字段需要 SQL schema 变更 + UI 同步，超 F095 范围（推迟到 F107 capability layer refactor）
- **关键**：share_with_workers 不再影响 LLM context；无 1-truth 偏离

**`shared_file_ids` 字段语义说明**（HIGH 4 处理）：
- 字段名保留（避免破坏既有 contract）
- envelope docstring 显式说明语义变更（"现在 = profile 白名单内文件 ID 列表，曾经 = share_with_workers=True 文件 ID 列表"）
- plan §0 增 contract audit 步骤：grep `shared_file_ids` 全部 production 消费者，确认无依赖旧语义的代码

### 6.4 _PROFILE_ALLOWLIST[WORKER] 最终白名单（v0.2）

```python
BehaviorLoadProfile.WORKER: frozenset({
    "AGENTS.md",       # ROLE 共享行为约束
    "TOOLS.md",        # TOOL_BOUNDARY 工具与边界
    "IDENTITY.md",     # ROLE Worker 自身身份（来自 IDENTITY.worker.md 模板）
    "PROJECT.md",      # SOLVING 项目语境
    "KNOWLEDGE.md",    # SOLVING 知识入口
    "USER.md",         # COMMUNICATION 用户长期偏好（语言/格式/确认偏好；H1 哲学由 SOUL.worker.md 守住）
    "SOUL.md",         # COMMUNICATION Worker 表达风格（来自 SOUL.worker.md）
    "HEARTBEAT.md",    # BOOTSTRAP Worker 运行节奏（来自 HEARTBEAT.worker.md）
}),  # = 8 文件，主 Agent FULL 9 文件 - BOOTSTRAP.md
```

### 6.5 BEHAVIOR_PACK_LOADED 事件 emit 时机 + 范围

**v0.1 原决策**：仅 cache miss emit
**v0.2 修订决策**：仅 cache miss emit + BehaviorPack 新增 `pack_id` 字段（让 F096 USED 事件可引用）

**论据**：
- F095 范围保持 minimal — 只 emit LOADED；USED 是 F096 责任
- BehaviorPack 加 `pack_id` 是必要 hook，否则 F096 USED 事件无法引用具体 pack
- cache hit 不重复 emit（每个 LLM 决策环都会读 pack，emit 会污染事件流）
- 如 F096 实施时认为需要 USED 事件，由 F096 自行引入并通过 `pack_id` 关联到 F095 的 LOADED

**emit 路径**（plan §1 Phase D 详细）：
- filesystem_pack 路径
- metadata raw_pack 路径
- default fallback 路径
- 三条路径的 emit payload 用 `pack_source` 字段区分

---

## 7. 与 F094 / F096 的接口

### 7.1 与 F094（Worker Memory Parity）并行

| 接口点 | F095 责任 | F094 责任 |
|--------|----------|-----------|
| `agent_context.py` | **不动** recall preferences 区域；只读 `_is_worker_behavior_profile`（F090 既有）| 修 recall preferences 从 `AgentProfile` 读 |
| `BehaviorPack` | F095 改 envelope 过滤 + 白名单 + 加 `pack_id` 字段；不改其他字段 schema | F094 不读 BehaviorPack |
| `agent_id` 字段 | F095 在 BEHAVIOR_PACK_LOADED 事件含 `agent_id` | F094 在 RecallFrame 添加 `agent_id` |
| 启动 baseline | 同 baseline 284f74d | 同 baseline 284f74d |
| 隔离假设 | **文件级低冲突**（不是"完全静态隔离"）+ Phase E 集成验证 | — |
| 合并策略 | F094 先合 master，F095 final review 前 rebase F094 完成的 master + 跑 AC-7b 集成测 | — |

### 7.2 与 F096（Worker Recall Audit & Provenance）

- F096 将利用 F095 引入的 `BEHAVIOR_PACK_LOADED` 事件 + `pack_id` 字段做 Worker 行为可审计
- F096 自己定义 `BEHAVIOR_PACK_USED` 事件（每次 LLM 决策环 emit），通过 `pack_id` 引用 F095 LOADED
- F096 还会按 `agent_id` / `session_id` 维度审计 Worker memory recall（F094 提供）
- F095 BEHAVIOR_PACK_LOADED payload 字段（`pack_id` / `agent_id` / `agent_kind` / `load_profile` / `pack_source` / `file_ids` / `source_chain` / `cache_state` / `is_advanced_included`）已对齐 F096 预期

---

## 8. 风险与缓解

| 风险 | 缓解 |
|------|------|
| Worker prefix cache 失效（IDENTITY/SOUL/HEARTBEAT/USER 新增进上下文导致 token 变化）| 接受：F095 是行为层增量；token 数据由日志观察是否需要按 budget 收敛 |
| `share_with_workers` 字段语义降级被遗漏 | spec §6.3 显式留 docs/codebase-architecture 更新（implement Phase 同步）；plan §0 contract audit 落地 |
| Worker 私有模板泄漏主 Agent persona（如 SOUL.worker.md 写得太"主"）| 模板内容在 commit 前 Codex review；spec §6.4 关键决策摘录贴入模板顶部注释 |
| BOOTSTRAP.md 不扩入导致 Worker 无 ramp-up 指引 | IDENTITY.worker.md / HEARTBEAT.worker.md 已覆盖派发后 ramp-up；如真有缺失，未来 Feature 再加 |
| F094 并行 rebase 隐性耦合（agent_id / source_chain）| Phase E rebase 后必跑 AC-7b 集成测，验证双 agent_id 一致性 |
| BEHAVIOR_PACK_LOADED 事件 producer 过高 | cache hit 不 emit；冷启动每 Worker 一条，符合 F087 e2e 事件密度预算 |
| Phase 顺序中间态 Worker 看到通用 SOUL/HEARTBEAT | Phase 顺序调整：B（创建 worker variant）先于 C（扩白名单）—— 详见 plan §1 |
| `shared_file_ids` 字段消费者依赖旧语义 | plan §0 contract audit 必须先做 |

---

## 9. 实施 Phase 概览（v0.2 调整顺序）

> **顺序原则**（Codex H2 推动）：先把 worker variant 模板就位，再扩白名单——避免中间态 Worker 看到通用 SOUL/HEARTBEAT 主 Agent 语气

- **Phase A — envelope 双过滤收敛 + IDENTITY 修复**（最小风险，先做）
  - 移除 `build_behavior_slice_envelope` 的 `share_with_workers AND` 子句
  - IDENTITY.md 现有白名单内被剥离的 bug 顺手修
  - 测试更新：`test_worker_profile_*` 系列断言反转
- **Phase B — Worker 私有模板（SOUL.worker.md / HEARTBEAT.worker.md）+ variant 注册**
  - 新建 2 个模板文件
  - 扩 `_BEHAVIOR_TEMPLATE_VARIANTS` 加 2 个条目（最终 3 个 worker variant）
  - 测试 variant 派发
- **Phase C — Worker allowlist 扩展 + USER 扩入**
  - `_PROFILE_ALLOWLIST[WORKER]` 扩到 8 文件（去 BOOTSTRAP 加 USER + 加 SOUL + HEARTBEAT）
  - 此时 worker variant 已就位，扩白名单不会让 Worker 看到通用 SOUL/HEARTBEAT
  - 测试 8 文件覆盖 + Worker 创建入口审计
- **Phase D — BEHAVIOR_PACK_LOADED 事件 + BehaviorPack.pack_id**
  - BehaviorPack 增 `pack_id` 字段
  - `resolve_behavior_pack` cache miss 三条路径 emit
  - sink = EventStore.record_event（M10 闭环）
  - 单测 + 集成测
- **Phase E — Final + rebase F094 + AC-7b 集成测**
  - rebase F094 完成后的 master
  - 全量回归 + AC-7b dispatch 双 agent_id 集成测
  - Final cross-Phase Codex review

每 Phase 之间执行 Codex per-Phase review + e2e_smoke + 全量回归 0 regression。

---

## 10. 验收 checklist（v0.2 拆分已决策 / 待完成）

### spec 阶段已决策 ✅

- [x] §2 实测对照表完成（含 prompt 假设 vs 实测、9 文件 default 矩阵、baseline grep 命中数、BOOTSTRAP/USER 内容实测）
- [x] §6.1 USER.md 扩入决策（v0.2 翻转，已用户拍板）
- [x] §6.2 BOOTSTRAP.md 不扩入决策（v0.2 翻转，已用户拍板）
- [x] §6.3 share_with_workers 字段保留 + envelope 去过滤决策
- [x] §6.4 最终白名单（8 文件，去 BOOTSTRAP 加 USER）
- [x] §6.5 BEHAVIOR_PACK_LOADED + pack_id 决策
- [x] Phase 顺序 A→B→C→D→E 决策
- [x] AC-1~AC-7 验收标准定义
- [x] F094 / F096 接口点说明

### implement 阶段待完成

- [ ] Phase A 实施 + Codex per-Phase review
- [ ] Phase B 实施（含 SOUL.worker.md / HEARTBEAT.worker.md 内容评审）
- [ ] Phase C 实施（含 Worker 创建入口枚举测试）
- [ ] Phase D 实施（含 BehaviorPack.pack_id schema 演进）
- [ ] Phase E rebase + AC-7b 集成测
- [ ] Final cross-Phase Codex review
- [ ] completion-report.md 产出
- [ ] handoff.md 产出（F096 接口点）
- [ ] docs/codebase-architecture/harness-and-context.md 同步
- [ ] docs/blueprint.md 同步审计（plan §3 grep 决定）

---

## 11. 待 plan 阶段细化项

- 测试夹具：Worker AgentProfile fixture 是否需要新增（priority: 高）
- BehaviorPack.pack_id schema 演进（priority: 高，Phase D 实施前必明确）
- envelope 二次过滤移除后老断言数量（priority: 中）
- contract audit `shared_file_ids` 全消费者列表（priority: 高，Phase A 之前）
- Worker 创建入口枚举（priority: 高，Phase C 之前）
- 文档同步：`docs/codebase-architecture/harness-and-context.md` worker behavior 章节（priority: 中）
- blueprint.md 同步审计（priority: 低，Phase E 收尾）
