# F099 需求澄清报告

**Feature**: F099 Ask-Back Channel + Source Generalization
**Spec 版本**: v0.1 Pre-GATE_DESIGN
**澄清日期**: 2026-05-11
**状态**: 待 GATE_DESIGN 审查

---

## 1. OD-F099-1 ~ OD-F099-7 推荐合理性评估

### OD-F099-1：ask_back 事件承载方式（推荐 B = 复用 CONTROL_METADATA_UPDATED）

**评估：推荐合理，但有一个隐藏成本未在 spec 中说明。**

- 推荐依据在 phase-0-recon 中可验证：`payloads.py:59-66` 的 source 字段确实是自由字符串，`connection_metadata.py:141-183` 的 merge 函数无需修改。
- **隐藏成本**：选 B 意味着 ask_back 无法通过 EventType 过滤直接查询（`event_store.query(event_type=ASK_BACK_REQUESTED)` 不可用），只能通过 `source="worker_ask_back"` 字符串过滤。字符串约定不受类型系统保护，未来 rename 可能导致历史记录 source 值不一致。
- **是否需要用户确认**：不是 CRITICAL，但建议在 §6 Risks 补充"source 字符串约定无类型保护"风险，implement 阶段用常量（如 `ASK_BACK_SOURCE = "worker_ask_back"`）固定。

### OD-F099-2：三工具抽象层次（推荐 B = 独立 handler，无共享抽象）

**评估：推荐合理，phase-0-recon 实测支撑充分。**

- `execution_context.request_input()` 直接返回用户文本，不走 delegation_id / child_task_id，确实不需要 BaseDelegation 字段。
- 隐藏成本：三个工具 handler 如果共享相同的 emit CONTROL_METADATA_UPDATED 逻辑，代码会有约 20-30 行重复。建议在 ask_back_tools.py 内提取 `_emit_ask_back_audit(...)` 私有函数（不是抽象类，是模块内工具函数），无需 GATE_DESIGN 决议。

### OD-F099-3：source 语义扩展方式（推荐 B = 扩展 source_runtime_kind 枚举值）

**评估：推荐合理，是三个 OD 中改动影响面最可控的选择。**

- phase-0-recon 实测验证：A2AConversation 完全无 source_type 字段，选 A 需修改 core 模型 + 所有构造点，风险显著大于选 B。
- **潜在冲突（见第 2 节）**：source 字符串约定在 dispatch_service 和 Event Store 两处分别表达，存在不一致风险，需在实施中统一常量定义。

### OD-F099-4：三工具适用 agent kind（推荐 B = 所有 agent kind 均可调用）

**评估：推荐合理，但"主 Agent 调 escalate_permission"场景有语义悖论（见第 2 节第 3 项）。**

- phase-0-recon 实测：当前 broker 无 kind 过滤机制，强制区分超出 F099 范围，符合 YAGNI。
- 工具名前缀 `worker.` 与"所有 agent kind 可用"存在命名矛盾（见第 5 节"隐含决策"）。

### OD-F099-5：escalate_permission 接入 Policy Engine 方式（推荐 A = 复用 ApprovalGate）

**评估：推荐合理，但 ApprovalGate 超时机制是否真实存在需 implement 阶段验证（见第 3 节开放问题 2）。**

- 选 A 与 Constitution C4/C7/C10 完全对齐，是正确选择。
- 风险点：spec §6 写"复用 ApprovalGate 现有超时机制（baseline 已有）"，但 phase-0-recon 未实测此机制是否存在。如果不存在，"不 raise 而返回 timeout 字符串"逻辑需要 F099 自建。

### OD-F099-6：spawn 路径 source_runtime_kind 注入位置（推荐 B = 工具层注入）

**评估：推荐合理，F098 dispatch_service 注释明确设计为工具层扩展点。**

- 与职责分离原则一致：plane 层不应感知 source 身份。
- 注意：FR-C2 和 FR-C3 分别覆盖 `delegate_task_tool.py` 和 `delegation_tools.py` 两个注入点，两处逻辑需保持一致——建议提取共用的 `_inject_worker_source_kind(envelope)` 函数。

### OD-F099-7：ask_back 唤醒后上下文恢复机制（推荐 A = tool_result 路径）

**评估：推荐合理，是技术上最简单且语义最正确的选择。**

- phase-0-recon 实测链路完整（execution_console asyncio.Queue → 返回值 → broker tool_result）。
- 隐藏风险：compaction 对 tool_call/tool_result 对的处理（见第 3 节开放问题 3）。

---

## 2. 跨 OD 冲突 / 依赖检查

### 冲突 1（重要）：OD-F099-3 与 OD-F099-1 的 source 字符串约定

**问题**：
- OD-F099-1（选 B）：CONTROL_METADATA_UPDATED 的 source 字段用 `"worker_ask_back"` / `"worker_request_input"` / `"worker_escalate_permission"` 字符串区分来源（用于 Event Store 审计查询）
- OD-F099-3（选 B）：`dispatch_service._resolve_a2a_source_role()` 接收 `envelope.metadata.source_runtime_kind`，取值为 `"worker"` / `"automation"` / `"user_channel"`

这是**两套完全不同的 source 字符串约定**，分别用于不同目的（audit trace vs A2A role 派生）。当前 spec 没有明确这两套约定的边界，实施时可能出现：
- 混淆 `source_runtime_kind` 值（`"worker"`）与 CONTROL_METADATA_UPDATED 的 source 值（`"worker_ask_back"`）
- 在错误的层使用错误的 source 字符串

**建议**：在 clarification 或 plan 阶段明确命名约定：
- **`source_runtime_kind`**（envelope.metadata 字段）：标识 **caller 身份类型**，取值 `"main" | "worker" | "subagent" | "automation" | "user_channel"`
- **`control_metadata_source`**（CONTROL_METADATA_UPDATED payload 字段）：标识 **事件来源操作**，取值 `"worker_ask_back" | "worker_request_input" | "worker_escalate_permission" | "subagent_delegation_init" | ...`

两者语义不同，实施时应分开处理，不得混用。

### 冲突 2：OD-F099-2（无 BaseDelegation 继承）和 OD-F099-7（tool_result 恢复）的协同

**问题**：OD-F099-6 要求在 spawn 路径注入 `source_runtime_kind`。但 OD-F099-2 决定 ask_back 工具**不走 spawn 路径**（直接调用 `execution_context.request_input()`）。

**推论**：`source_runtime_kind` 注入逻辑（块 C）和 ask_back 工具（块 B）**互不依赖**：
- 块 C 修复的是 delegate_task/subagents.spawn 路径的 F098 LOW §3 遗留问题
- 块 B 的 ask_back 工具调用 request_input() 走独立路径，不经过 spawn 逻辑

两块可以独立验证，不需要协同。spec 中的 Phase 顺序（C → D → B → E）是合理的，但应在 plan 中明确说明块 C 的 source 注入修复与块 B 的 ask_back 功能**在技术层面无直接依赖关系**。

### 冲突 3（需用户确认）：OD-F099-4 + OD-F099-5 — 主 Agent 调 escalate_permission 语义悖论

**问题**：OD-F099-4 说所有 agent kind（包括主 Agent）均可调用 `worker.escalate_permission`。但主 Agent 理论上已持有系统最高权限（H1 管家 mediated 模式），向 ApprovalGate 请求权限提升在语义上是矛盾的：

- 主 Agent 本身就是用户-facing speaker，"向用户请求权限"等同于"主 Agent 自己审批自己"
- ApprovalGate 的审批界面如何区分"主 Agent 请求 vs Worker 请求"？SSE 推送的审批卡片会误导用户认为是 Worker 在请求

**选项**：
| 选项 | 描述 | 影响 |
|------|------|------|
| A | 工具名用 `worker.` 前缀，主 Agent 技术上可调但 spec 文档明确说明"主 Agent 调用此工具语义不明确，由 LLM 自主判断是否合理" | 零代码变更，文档说明责任 |
| B | 在 ApprovalGate handler 中加 caller_kind 字段，让审批卡片显示来源（主 Agent vs Worker） | 需修改 ApprovalGate SSE payload，影响前端审批 UI |
| C | 将此场景标注为 Non-Goal，文档说明 kind 限制在 F107 策略层 | 最小改动，接受歧义 |

**推荐**：选 C，与 spec §5 Non-Goals 第 7 条（kind=worker 专属工具集过滤留 F107）一致。但需要在 spec 中补充说明，避免 LLM 主 Agent 误用。

---

## 3. 三个开放问题（spec §6 Risks 相关）

### 开放问题 1：automation / user_channel 派生路径范围

**背景**：FR-C1 要求 `_resolve_a2a_source_role()` 扩展 `"automation"` / `"user_channel"` 两个新值并派生完整路径（role/session_kind/agent_uri）。但 spec §5 Non-Goals 和 §YAGNI 检验均标注"枚举约定先定，派生逻辑留 F101"。

**矛盾**：FR-C1 是 MUST，但 YAGNI 检验标注为 [可选]。这个矛盾需要在 GATE_DESIGN 锁定。

**选项**：
| 选项 | 描述 | 影响 |
|------|------|------|
| A | 仅在代码中定义 `"automation"` / `"user_channel"` 字符串常量（枚举约定），`_resolve_a2a_source_role()` 中对新值 fallback 到 MAIN 路径并 emit warning log | F101 接手时直接改派生逻辑，常量名已稳定 |
| B | 实施完整的 automation/user_channel 派生路径（role/session_kind/agent_uri 均定义）即便无消费方 | 超出 F099 核心范围，增加约 40-60 行代码 |

**推荐**：选 A。与 §YAGNI 检验一致，FR-C1 的 MUST 降级为"定义常量 + warning fallback"，完整派生是 F101 的事。

**影响范围**：FR-C1 描述需修订（从"派生独立路径"改为"定义枚举约定 + warning fallback"），AC 无需新增（FR-C4 的 invalid 值降级行为已覆盖 fallback 逻辑）。

**建议处理时机**：**spec 阶段在 GATE_DESIGN 决议**（影响 FR-C1 的 MUST 级别）。

---

### 开放问题 2：escalate_permission 无人审批超时策略 — ApprovalGate 超时机制是否存在

**背景**：spec §6 Risks 写"复用 ApprovalGate 现有超时机制（baseline 已有）；超时时返回 'timeout' 字符串"。但 phase-0-recon 未实测 ApprovalGate 的超时机制。

**需验证的问题**：`harness/approval_gate.py` 是否有 timeout 参数或超时回调逻辑？

经分析 spec 内容和 phase-0-recon 侦察范围，**phase-0-recon 没有覆盖 ApprovalGate 超时机制**（侦察项 1-5 均未涉及）。

**两种可能**：
- **Scenario A**：ApprovalGate 已有超时机制（如 asyncio.wait_for 包装）→ escalate_permission 直接复用，无需新建
- **Scenario B**：ApprovalGate 无超时机制（只有用户手动审批/拒绝两路）→ F099 需在 escalate_permission handler 中自建 `asyncio.wait_for(..., timeout=N)` 并 catch `asyncio.TimeoutError` 返回 "timeout"

**影响**：若是 Scenario B，块 B 的 escalate_permission handler 实现复杂度显著增加，约需额外 20-30 行代码 + 超时参数（谁来配置？task profile？工具参数？）。

**建议处理时机**：**implement 阶段 Phase B 开始前 grep 验证**（`grep -r "timeout" harness/approval_gate.py`）。如果发现是 Scenario B，应 escalate 到用户决策超时时长配置方式。

---

### 开放问题 3：compaction 对 ask_back tool_call/tool_result 对的影响

**背景**：spec §6 Risks 提到"验证现有 compaction 机制是否保留 tool_call/tool_result 对"，并说"若不保留则记录为已知 risk"。

**问题**：OD-F099-7 选 A 依赖 tool_result 路径是上下文恢复载体。若 compaction 压缩后 turn N 的 ask_back tool_call 消失，turn N+1 的 tool_result 会出现"孤立 tool_result"——大多数 LLM 要求 tool_call 和 tool_result 必须成对出现，否则报错。

**两种情形**：
- **Scenario A（安全）**：compaction 按对保留 tool_call/tool_result，不分割——验证通过即可关闭此风险
- **Scenario B（危险）**：compaction 可能截断 tool_call，但保留 tool_result——LLM 下次 turn 收到孤立 tool_result，Anthropic/OpenAI API 可能 400 error

**建议处理时机**：**spec 阶段前置验证**，因为如果是 Scenario B，OD-F099-7 的推荐（选 A）就需要重新评估，影响核心设计决策。

**验证方法**：grep `ContextCompactionService` 的 `_load_conversation_turns` 是否有 tool_call/tool_result 成对保护逻辑。如果 phase-0-recon 实测项 5 已测试过决策环，应补充此验证。

**推荐**：在 GATE_DESIGN 前补一个针对性 grep（`grep -n "tool_call\|tool_result" apps/gateway/src/.../context_compaction*.py`），5 分钟可验证，比 implement 阶段发现要便宜得多。

---

## 4. AC 缺口检查

### FR 到 AC 覆盖矩阵

| FR | AC | 覆盖状态 |
|----|-----|---------|
| FR-B1（ask_back → WAITING_INPUT，不 raise）| AC-B2 | ✅ 覆盖 |
| FR-B2（request_input 返回用户输入文本）| AC-B3（部分）| ⚠️ 弱覆盖：AC-B3 仅验证 tool_result 包含文本，未验证 task 状态在 attach_input 后回 RUNNING |
| FR-B3（escalate_permission → WAITING_APPROVAL，审批通过返回 "approved"，不 raise）| AC-B4 | ⚠️ 缺口：AC-B4 仅验证 WAITING_APPROVAL 状态进入，**未验证审批通过/拒绝后的返回值**（"approved" / "rejected"），也未验证不 raise |
| FR-B4（三工具均 emit CONTROL_METADATA_UPDATED）| AC-D1，AC-G4 | ✅ 覆盖 |
| FR-B5（工具描述提示 caller 信息）| 无 AC | ⚠️ 缺口：文档质量 FR，但可测性低——建议降级为 non-AC 的 spec 说明，或在 AC-B1 中补充"工具描述包含 caller context 提示" |
| FR-B6（entrypoints={"agent_runtime", "web"}）| AC-B1（部分）| ⚠️ 弱覆盖：AC-B1 只验证 `agent_runtime` 在 entrypoints，未验证 `"web"` |
| FR-C1（automation/user_channel 派生）| 无 AC | ⚠️ 缺口：若开放问题 1 选 A（只定义常量），可用 AC-C4（warning log emit）覆盖；若选 B，需补 AC-C3 |
| FR-C2（delegate_task_tool 注入）| AC-C1 | ✅ 覆盖 |
| FR-C3（subagents.spawn 注入）| AC-C1（共用）| ⚠️ 弱覆盖：AC-C1 只描述 worker→worker source=WORKER，未区分 delegate_task vs subagents.spawn 两个注入点 |
| FR-C4（无效值降级 + warning log）| 无 AC | ⚠️ 缺口：建议补 AC-C3（invalid source_runtime_kind → default main + warning log） |
| FR-D1（ask_back emit metadata）| AC-D1 | ✅ 覆盖 |
| FR-D2（不污染对话历史）| AC-D2 | ✅ 覆盖 |
| FR-D3（escalate_permission emit metadata）| AC-G4（部分）| ⚠️ 弱覆盖：AC-G4 是全局 AC，escalate_permission 的 CONTROL_METADATA_UPDATED emit 没有专属 AC |
| FR-D4（source 字段描述更新）| 无 AC | ✅ 合理：文档变更不需要测试 AC |
| FR-E1（attach_input 唤醒）| AC-B3，AC-E1 | ✅ 覆盖 |
| FR-E2（tool_result 包含 attach_input 文本 + tool_call_id 匹配）| AC-B3（部分）| ⚠️ 缺口：AC-B3 未验证 tool_call_id 匹配 |
| FR-E3（Event Store 三条连续事件）| AC-E1 | ✅ 覆盖 |
| FR-E4（escalate_permission WAITING_APPROVAL + SSE + 审批通过继续）| AC-B4，AC-G3 | ⚠️ 缺口：AC-B4 + AC-G3 联合覆盖，但没有验证"拒绝后 LLM 收到 'rejected' 可自主决策"的 AC |

### 建议补充的 AC

1. **AC-B5**（escalate_permission 返回值）：
   - Given: ApprovalGate 注册 escalate_permission 请求
   - When: 用户通过 SSE 批准；OR 用户拒绝
   - Then: Worker LLM 收到 tool_result = "approved" / "rejected"；task 不 raise；status 从 WAITING_APPROVAL 回 RUNNING

2. **AC-C3**（无效 source_runtime_kind 降级）：
   - Given: envelope.metadata.source_runtime_kind = "unknown_value"
   - When: `_resolve_a2a_source_role()` 执行
   - Then: source role 降级为 MAIN；结构化 warning log emit；0 exception raised

3. **AC-C4**（subagents.spawn 路径注入，区别于 delegate_task）：
   - 可合并到 AC-C1 补充 "Via subagents.spawn 路径同样生效"

---

## 5. spec 中未明确的隐含决策

### 隐含决策 1（需 GATE_DESIGN 确认）：工具名前缀 `worker.` 与 OD-F099-4 的矛盾

**现象**：spec §3 块 B 定义工具名为 `worker.ask_back` / `worker.request_input` / `worker.escalate_permission`，但 OD-F099-4 选 B 说"所有 agent kind 均可调用"（包括主 Agent 和 subagent）。

**矛盾**：`worker.` 前缀在语义上暗示"仅 Worker 使用"，但 OD-F099-4 说不限制 kind。

**选项**：
| 选项 | 工具名 | 含义 |
|------|--------|------|
| A | `worker.ask_back` | 前缀表示"典型用于 Worker 场景"，主 Agent 技术上可调但不推荐 |
| B | `agent.ask_back` | 前缀表示"任何 Agent 可用"，语义更准确 |
| C | `ask_back`（无前缀）| 扁平命名空间，但与现有工具命名约定不一致（`subagents.steer` / `work.plan` 均有前缀）|

**推荐**：选 A，保持 `worker.` 前缀，因为：(1) 语义上 ask_back 确实主要用于 Worker 场景；(2) 前缀不影响可调用性（policy 控制而非名字控制）；(3) 与现有工具命名约定（`subagents.` / `work.`）一致。但需在 spec 中明确说明"前缀是惯例归属，不是访问控制"。

### 隐含决策 2：三工具是否进入 capability_pack 默认 Worker bundle

**现象**：spec §3 FR-B6 说 entrypoints = `{"agent_runtime", "web"}`，但未说明三工具是否自动进入所有 Worker 的工具集，还是需要在 behavior 文件（如 AGENTS.md 或 TOOLS.md）中显式启用。

**影响**：如果三工具进入默认 bundle，所有现有 Worker 立即获得 ask_back 能力（可能改变现有 Worker 行为）；如果需要显式启用，需要更新相关 behavior 文件。

**推荐**：进入 ToolRegistry 注册后，让所有启用了 `interaction` tool_group 的 Worker 自动获得。如果当前没有 `interaction` tool_group，则随默认 Worker bundle（tool_profile="standard"）发布。需在 plan 阶段确认 capability_pack 的 tool_profile 配置。

### 隐含决策 3：escalate_permission handler 是同步阻塞还是返回 approval_id

**现象**：spec 中 OD-F099-5 选 A（复用 ApprovalGate SSE 路径），FR-B3 说"审批通过返回 'approved' / 审批拒绝返回 'rejected'"——这隐含了**同步阻塞等待**语义（类似 ask_back 的 asyncio.Queue.get()）。

**两种实现模式**：
| 模式 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| **同步阻塞**（spec 暗示）| handler await ApprovalGate 决议，直接返回 "approved"/"rejected" | LLM 上下文完整，tool_result 自然 | 若无超时，LLM turn 挂起（开放问题 2 的风险） |
| **异步 polling**| handler 立即返回 approval_id，LLM 通过 poll 工具查询状态 | 不阻塞 turn | 需要新建 poll 工具，增加 LLM 交互复杂度 |

**推荐**：同步阻塞（与 ask_back 的 asyncio.Queue 模式一致，保持 API 风格统一）。超时保护是关键——需在实施前确认开放问题 2。

---

## 6. clarification 汇总：GATE_DESIGN 前需锁定的决策

| 编号 | 决策点 | 建议 | 时机 |
|------|--------|------|------|
| G1 | FR-C1 范围：automation/user_channel 是完整派生还是仅常量约定 | 选 A（仅常量 + fallback），FR-C1 描述需修订 | spec/GATE_DESIGN 阶段 |
| G2 | tool_call/tool_result compaction 安全性验证 | Phase B 前补一次 grep 验证；若不安全则 OD-F099-7 需重新评估 | spec 阶段前置验证 |
| G3 | 工具名前缀 `worker.` 语义说明 | 明确"前缀是归属惯例，不是访问控制"，写入 spec | spec 阶段明确 |
| G4 | 主 Agent 调 escalate_permission 的语义处理 | Non-Goal（F107 策略层），在 spec 中补充说明 | spec 阶段澄清 |

---

v0.1 - 待 GATE_DESIGN 审查
