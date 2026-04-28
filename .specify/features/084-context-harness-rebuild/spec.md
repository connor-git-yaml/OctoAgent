# Feature Specification: 084 - Context + Harness 全栈重构

**Feature Branch**: `084-context-harness-rebuild`
**Created**: 2026-04-27（spec），更新 2026-04-28（C1 已解决）
**Status**: Ready for Design Gate
**上游**: research-synthesis.md (2026-04-28)
**利益相关者**: 系统单 owner Connor（终端软件研发负责人），技术受众；本规范允许出现实现锚点（标准库 API、Hermes 文件名）以便 plan 阶段直接落地。

---

## 背景

### 根因：四层架构断层导致 F082 治标失败

F082（Bootstrap & Profile Integrity）加严了 is_filled 判断、创建了 BootstrapIntegrityChecker，但实测中 Connor 连续多次通过 web 入口请求 Agent "帮我初始化 USER.md"，Agent 均输出了档案内容，却报告"未完成写入：当前会话没有可调用的工具入口"。这一失败不是 bug，而是四个架构断层的叠加效应：

| 断层 | 描述 | 典型症状 |
|------|------|---------|
| **D1** | Tool Registry entrypoints 是硬编码显式字典，`bootstrap.complete` 仅声明 `entrypoints=["agent_runtime"]`，web 入口不可见 | web 会话中 Agent 无法调用档案写入工具 |
| **D2** | Snapshot Store 不存在：工具调用结果不持久化，LLM 写操作完成后无法确认是否成功 | Agent 写完后无回显，无法向用户确认 |
| **D3** | UserMdRenderer 的 `is_filled` 占位符检测误判：USER.md 有内容仍被认定为未填充 | 档案已写但系统持续要求重新初始化 |
| **D4** | 工具命名语义失配：`bootstrap.complete` 语义是"完成引导流程"而非"写入档案"，LLM 在更新档案场景下不优先选用 | Agent 无法自主决策用哪个工具执行档案写入 |

四个断层共同构成"Context + Harness 全栈失效"，无法通过局部补丁解决，必须统一重构。

### 为什么参考 Hermes Agent

Hermes Agent 是本次重构的首要参考架构。它在同等规模的单用户 Agent 系统中已生产验证以下模式：

- **Snapshot 模式**：`_system_prompt_snapshot` 在 session 开始时冻结，整个 session 内系统提示不变，保护 prefix cache；工具写入只改磁盘，下次 session 加载最新内容
- **Tool Registry**：AST 扫描 + module-level `registry.register()`，工具自描述、自注册，约 450 行可直接移植
- **Approval Gate + Threat Scanner**：`_MEMORY_THREAT_PATTERNS` 正则表 + invisible unicode 检测，无外部依赖，微秒级扫描
- **USER.md 合并**：`§` 分隔符 + add/replace/remove 三操作，append-only 模式，零 token 成本，用户审视成本低

---

## 用户故事

### J1 - USER.md 档案初始化（优先级：P0 Must）

Connor 通过 web UI 对 Agent 说"帮我初始化 USER.md"，Agent 通过语义明确的 `user_profile.update` 工具收集信息并写入，返回确认结果，Connor 能在同一对话中看到写入内容的回显。

**优先级理由**：这是触发 F084 的原始痛点，也是 OctoAgent 可用性的基本要求。档案初始化失败意味着系统对用户的理解为零，所有后续个性化功能均无意义。此旅程打通后，路径 A（档案写入流）完整可用。

**独立测试**：在全新 OctoAgent 实例中，通过 web UI 对话请求 Agent 初始化档案，验证工具可见、写入成功、回显完整，无需其他旅程前置。

**验收场景**：

1. **Given** USER.md 不存在，**When** Connor 通过 web UI 请求初始化档案，**Then** Agent 调用 `user_profile.update` 工具，USER.md 被写入，同一对话中 LLM 回显确认写入内容，系统提示注入的快照不变
2. **Given** USER.md 已存在且有内容，**When** Connor 请求"把我的时区改为 Asia/Shanghai"，**Then** Agent 调用 `user_profile.update`（replace 操作），时区字段被精确更新，其他 section 内容不变
3. **Given** Tool Registry 已加载，**When** web 入口发起会话，**Then** `user_profile.update` / `user_profile.observe` / `user_profile.read` 三工具均可见（entrypoints 包含 `web`）
4. **Given** 写入操作完成，**When** Agent 收到工具调用结果，**Then** 写入结果以 SnapshotRecord 形式持久化，LLM 在下一 turn 可通过 `snapshot.read` 确认写入成功

---

### J2 - 写入回显（优先级：P0 Must）

Agent 执行 USER.md 写入操作后，工具调用结果以结构化形式回显给 LLM，LLM 能向用户确认"已写入以下内容：..."，不再出现静默成功或静默失败的情况。

**优先级理由**：断层 D2 的直接修复。缺少回显机制时，LLM 在工具写入后无法区分成功和失败，用户体验退化为"黑洞写入"。回显是所有写入流程的信任基础。

**独立测试**：向 Agent 发送写入档案请求，观察 LLM 的响应是否包含具体写入内容的确认；通过 API 查询 SnapshotRecord，验证记录存在且内容正确。

**验收场景**：

1. **Given** `user_profile.update` 被调用，**When** 写入成功，**Then** SnapshotRecord 被写入存储，包含 `tool_call_id`、`result_summary`、`timestamp`；LLM 下一 turn 的响应包含具体写入内容的确认
2. **Given** `user_profile.update` 被调用，**When** 写入因 Threat Scanner 拦截失败，**Then** 工具返回 `blocked: true` + `pattern_id`，LLM 向用户说明被拦截的原因，`MEMORY_ENTRY_BLOCKED` 事件写入 Event Store
3. **Given** 会话重启，**When** Agent 在新 session 中查询，**Then** 上一 session 的 SnapshotRecord 可通过 `snapshot.read` 检索（TTL 30 天内）

---

### J3 - Threat Scanner 拦截恶意输入（优先级：P0 Must）

任何写入 USER.md 或 MEMORY.md 的内容，在执行前经过 Threat Scanner 扫描。Prompt injection、role hijacking、exfiltration 指令被拦截并在 Web UI 展示告警，合法内容不受干扰地通过。

**优先级理由**：USER.md 内容注入系统提示，若被污染则整个系统提示上下文被攻击者控制。Constitution C5（Least Privilege）要求 secrets 不进 LLM 上下文；Threat Scanner 是防注入的最后一道防线。

**独立测试**：通过 API 直接调用 `user_profile.update`，分别传入含 prompt injection 指令和正常内容，验证前者被拦截、后者通过，拦截时 `MEMORY_ENTRY_BLOCKED` 事件被记录。

**验收场景**：

1. **Given** 用户输入包含 `ignore previous instructions` 类指令，**When** `user_profile.update` 被调用，**Then** Threat Scanner 命中 pattern，操作被 block，返回 `blocked: true` + `pattern_id`，事件写入 Event Store
2. **Given** 输入内容包含不可见 unicode 字符（零宽字符注入），**When** 扫描执行，**Then** invisible unicode 检测命中，内容被 block
3. **Given** 合法的档案更新内容（如"职业：工程师"），**When** 扫描执行，**Then** 所有 pattern 均未命中，操作继续执行（无 false positive）
4. **Given** pattern 命中但置信度低时（通过 `--force` flag 旁路），**When** 用户明确授权，**Then** 操作通过 Approval Gate 进行人工确认后执行

---

### J4 - 修改档案（覆盖 + Section 级更新）（优先级：P0 Must）

Connor 通过对话对档案进行精确修改：单字段更新、section 替换、条目删除。不需要重走 bootstrap 流程，写入范围精确，其余内容不受影响。

**优先级理由**：档案是长期维护的，初始化后 Connor 会持续修改。缺少精确更新能力意味着每次修改都需要全量重写，成本高且覆盖丢失风险大。

**独立测试**：对已有内容的 USER.md，分别测试 add（新增条目）、replace（替换指定文本）、remove（删除条目）三个操作，验证只有目标内容变更，其余不变。

**验收场景**：

1. **Given** USER.md 有时区字段，**When** Connor 说"把时区改为 Asia/Shanghai"，**Then** Agent 调用 `user_profile.update`（replace 操作，`old_text` 为当前时区值），只更新时区字段，其他内容不变
2. **Given** USER.md 有任意内容，**When** Connor 说"删除我的职业信息"，**Then** Agent 调用 `user_profile.update`（remove 操作），目标条目被移除，其余 section 完整保留
3. **Given** 执行 replace 或 remove（不可逆操作），**When** 操作触发，**Then** 必须经过二段式确认：先预览 diff → 用户确认 → 执行（Constitution C4 Two-Phase 合规）

---

### J5 - 重装路径（优先级：P0 Must）

Connor 清除 `~/.octoagent/data/` 和 `~/.octoagent/behavior/` 后执行 `octo update` 重启，bootstrap 流程可靠完成，10 分钟内恢复可用状态。保留 `octoagent.yaml` 和 `.env`，不需要迁移 CLI 命令。

**优先级理由**：F082 修复了部分 bootstrap 路径，但 Tool Registry web 入口缺失问题未修复，重装后仍可能遇到工具不可见的问题。可靠的重装路径是系统可维护性的基础。

**独立测试**：清除 data/ 和 behavior/ 目录，保留 octoagent.yaml 和 .env，重启服务，通过 web UI 完成 bootstrap 流程，验证 USER.md 写入成功、所有工具可见。

**验收场景**：

1. **Given** data/ 和 behavior/ 已清除，保留 octoagent.yaml 和 .env，**When** 执行 `octo update` 重启，**Then** bootstrap 流程正常启动，工具注册完成，web 入口可见全部 entrypoints 包含 `web` 的工具
2. **Given** bootstrap 流程进行中，**When** Connor 通过 web UI 完成档案初始化，**Then** bootstrap 状态正确迁移，OwnerProfile 从 USER.md 派生 sync，系统进入正常运行状态
3. **Given** USER.md 已存在且非空（重装前保留），**When** bootstrap 流程检查，**Then** 跳过初始化写入，不覆盖已有档案（防止 R9 数据丢失风险）

---

### J6 - Observation 异步累积（优先级：P1 Nice）

Connor 在闲聊中提到"孩子刚上小学了"，Agent 在会话结束后异步提取这一事实，以 confidence ≥ 0.7 为门槛写入候选队列，不打断当前对话，不产生额外 LLM 调用延迟。

**优先级理由**：这是从"被动响应"到"主动理解"的关键能力跃升。首版上线，但置信度 gate 保护质量，避免噪声候选堆积。

**独立测试**：模拟一段包含用户事实的对话，会话结束后触发 observation routine，验证 candidates 表中有对应候选、confidence 字段正确、低于 0.7 的候选不入队列。

**验收场景**：

1. **Given** 对话中 Connor 提及新事实（如孩子年级），**When** 会话结束触发 observation routine，**Then** pipeline 执行 extract → dedupe → categorize 三阶段，`OBSERVATION_OBSERVED` 事件写入 Event Store
2. **Given** categorize 阶段完成，**When** LLM 评估 confidence < 0.7，**Then** 候选直接丢弃，不入 review queue（仲裁 2：避免噪声）
3. **Given** categorize 阶段完成，**When** LLM 评估 confidence ≥ 0.7，**Then** 候选写入 candidates 表，状态为 `pending`，`OBSERVATION_OBSERVED` 事件含 confidence 字段
4. **Given** utility model 不可用（C6 降级），**When** categorize 阶段执行，**Then** 跳过 categorize，候选全部以低置信度进入 review queue，observation routine 不中断

---

### J7 - UI Promote 候选列表（优先级：P1 Nice）

Connor 打开 Web UI 时，看到"AI 发现的新事实"候选列表，可以逐条 accept（写入 USER.md）、edit（修改后 accept）、reject（丢弃），也可以批量全选 reject。超过 30 天未处理的候选自动归档。

**优先级理由**：observation 路径的用户侧闭环。没有 promote 流程，候选事实永远停在队列里，observation 能力无法形成价值。首版做完整（仲裁 3），加批量 reject 防止堆积。

**独立测试**：在 candidates 表中预插入若干候选，通过 Web UI 分别执行 accept、edit+accept、reject、批量 reject，验证 USER.md 正确更新、`OBSERVATION_PROMOTED` 和 `OBSERVATION_DISCARDED` 事件正确写入。

**验收场景**：

1. **Given** candidates 表有 pending 候选，**When** Connor 在 Web UI 点击 accept，**Then** 候选内容通过 Threat Scanner 扫描后经 ToolBroker + PolicyGate 写入 USER.md，`OBSERVATION_PROMOTED` 事件写入
2. **Given** Connor 编辑候选内容后 accept，**When** 提交，**Then** 写入 USER.md 的是编辑后的内容，`OBSERVATION_PROMOTED` 事件包含 `edited: true` 字段
3. **Given** Connor 点击 reject 或批量全选 reject，**When** 提交，**Then** 候选状态变为 `rejected`，`OBSERVATION_DISCARDED` 事件写入，USER.md 不变
4. **Given** 候选在 candidates 表中存在超过 30 天，**When** 自动归档任务执行，**Then** 候选状态变为 `archived`，`OBSERVATION_DISCARDED` 事件写入（含 `reason: auto_archive`）
5. **Given** candidates 队列超过 50 条，**When** observation routine 尝试新增候选，**Then** 提取停止，Telegram 推送"待确认 memory 候选已达上限"提醒

---

### J8 - Sub-agent 派发任务（优先级：P1 Nice）

Connor 对话中说"去调研一下这个技术方案"，Agent 调用 `delegate_task` 工具将任务派发给 Research Worker，主会话不阻塞，任务结果通过回调回传，派发深度不超过 2 层（max_depth=2）。

**优先级理由**：多步骤子任务阻塞主会话是常见体验问题。`delegate_task` 首版上线，A2A-Lite 已有基础，主要是统一工具接口和防死循环约束。

**独立测试**：通过 API 调用 `delegate_task` 工具，验证任务被派发到指定 Worker、主会话立即返回、`SUBAGENT_SPAWNED` 事件写入、结果回调正确触发。

**验收场景**：

1. **Given** `delegate_task` 被 Agent 调用，**When** 参数包含 `target_worker`、`task_description`、`callback_mode`，**Then** 任务派发给目标 Worker，主 Agent 立即返回，`SUBAGENT_SPAWNED` 事件写入
2. **Given** 已有深度 1 的 sub-agent（由主 Agent 派发），**When** sub-agent 尝试再次调用 `delegate_task`（depth 达到 2），**Then** 请求成功；若再深一层尝试（depth 将超过 2），则被拒绝并记录原因
3. **Given** `delegate_task` 被调用时 target_worker 在黑名单中，**When** 调用执行，**Then** 立即返回错误，`SUBAGENT_SPAWNED` 事件不写入，错误信息说明黑名单原因
4. **Given** sub-agent 任务完成，**When** Worker 返回结果，**Then** `SUBAGENT_RETURNED` 事件写入，回调结果注入主 Agent 上下文

---

### J9 - Approval Gate 危险动作（优先级：P0 Must）

Agent 要执行高风险操作（如批量删除、执行 shell 命令）时，在 Web UI 显示"请求审批"卡片，展示 Threat Scanner 分类、操作详情和原因说明，Connor 可批准或拒绝。`APPROVAL_REQUESTED` 和 `APPROVAL_DECIDED` 事件完整记录。

**优先级理由**：Constitution C7（User-in-Control）和 C4（Two-Phase Side-effect）的直接要求。Threat Scanner 统一入口修复了当前分散 policy 导致的漏扫问题。

**独立测试**：触发高风险工具调用，验证 Approval Gate 拦截并生成审批卡片，Web UI 正确展示 threat 分类和原因，批准后操作执行、拒绝后操作取消，对应事件正确写入。

**验收场景**：

1. **Given** Agent 调用高风险工具（side_effect 级别为 HIGH 或 CRITICAL），**When** PolicyGate 检测到，**Then** 操作暂停，`APPROVAL_REQUESTED` 事件写入，Web UI 显示包含 threat_category 和操作详情的审批卡片
2. **Given** 审批卡片展示，**When** Connor 点击批准，**Then** 操作恢复执行，`APPROVAL_DECIDED(approved)` 事件写入，SSE 通知 Agent 继续
3. **Given** 审批卡片展示，**When** Connor 点击拒绝，**Then** 操作取消，`APPROVAL_DECIDED(rejected)` 事件写入，Agent 收到拒绝通知并向用户说明
4. **Given** session allowlist 中已有该操作类型的授权记录，**When** 相同类型操作再次触发，**Then** 直接通过，不重复弹出审批卡片（session 级 allowlist 生效）

---

## 边界场景

- **USER.md 外部修改**：进程运行中外部程序修改 USER.md 时，当前 session 的 Snapshot 不感知变化（session 级冻结）；session 结束后日志记录 mtime 漂移；下次 session 重新加载最新内容。此行为是设计意图，需在用户文档中说明
- **并发写竞争**：两个 Agent 会话同时调用 `user_profile.update` 时，`fcntl.flock(LOCK_EX)` + atomic rename 保证只有一个写入成功，另一个排队等待；单用户场景极低概率，不做额外处理
- **Threat Scanner 误杀**：合法内容被 pattern 误匹配时，提供 `--force` flag 旁路，旁路操作需经 Approval Gate 人工确认；`pattern_id` 在错误响应中展示，便于调试
- **observation routine 无活跃会话**：系统无近期对话记录时 routine 定期检查，无数据则空跑返回，不写入任何事件
- **bootstrap 重入**：重装后 bootstrap 流程如发现 USER.md 已存在且非空，跳过初始化写入，直接进入 sync 流程；防止误覆盖已有档案（R9 缓解）
- **sub-agent 返回超时**：delegate_task 后 Worker 长时间不返回，主 Agent 在 `max_wait_seconds`（默认 300s）后记录超时事件，用户可查询 Worker 状态

---

## 功能需求

### FR-1 Tool Registry（自描述 + AST 发现 + toolset 解析）[必须]

**FR-1.1** MUST：Tool Registry 使用 AST 扫描 `builtin_tools/` 目录，检测含顶层 `registry.register()` 调用的模块，动态 import 并注册 `ToolEntry`；扫描仅在启动时执行一次，耗时 < 200ms。

**FR-1.2** MUST：每个 ToolEntry MUST 声明：`name`（工具唯一标识符）、`entrypoints`（`web` / `agent_runtime` / `telegram` 的子集）、`toolset`（工具所属 capability pack）、`handler`（可调用对象）、`schema`（Pydantic BaseModel 定义，Constitution C3 单一事实源）。

**FR-1.3** MUST：Tool Registry 根据请求来源（entrypoint）动态过滤可见工具列表，不同入口看到不同工具集；web 入口 MUST 可见 `user_profile.update`、`user_profile.observe`、`user_profile.read`、`delegate_task`。

**FR-1.4** MUST：ToolBroker 保留为外层契约层（schema 反射、side_effect 分级、policy 决策），ToolRegistry 作为工具来源，`ToolBroker.execute()` 调用 `ToolRegistry.dispatch()`，职责分离。

**FR-1.5** SHOULD：Tool Registry 支持热更新（`deregister(name)` + re-register），为未来 MCP 动态工具扩展预留接口；当前版本不强制实现热更新触发路径。

**FR-1.6** MUST：`bootstrap.complete` 工具直接删除，不保留 alias，不保留注释代码；删除前 `grep -r "bootstrap.complete"` 全量扫描引用，确认清零后执行删除（Constitution C9：Agent 自主决策不被硬编码工具驱动）。

---

### FR-2 Snapshot Store（冻结快照 + live state + 写入回显 + mtime race 保护）[必须]

**FR-2.1** MUST：Session 开始时，SnapshotStore 读取 USER.md / MEMORY.md 文件，冻结为内存 dict（`_system_prompt_snapshot`），整个 session 内 `format_for_system_prompt()` 始终返回冻结副本；工具写入只改磁盘和 live state，不改冻结副本。

[AUTO-CLARIFIED: live state 定义 — live state 是独立于冻结快照的第二个可变内存 dict，随每次写入同步更新；`user_profile.read` 工具读取 live state（非冻结副本）；系统提示注入始终读冻结副本；两者在 SnapshotStore 内作为独立字段维护]

**FR-2.2** MUST：写入磁盘使用 `tempfile.mkstemp` + `os.replace()` 原子替换（atomic rename），并以 `fcntl.flock(LOCK_EX)` 保护 read-modify-write 窗口（标准库实现，无新依赖）。

**FR-2.3** MUST：每次工具调用成功写入后，创建 SnapshotRecord 并持久化，包含字段：`tool_call_id`（UUID）、`result_summary`（写入摘要，UTF-8 ≤ 500 字符）、`timestamp`（ISO 8601）、`ttl_days`（默认 30）；LLM 可通过 `snapshot.read` 工具按 `tool_call_id` 或最近 N 条查询。

[AUTO-CLARIFIED: SnapshotRecord 存储位置 — 复用现有 SQLite DB 文件（不新增 DB 文件），在同一文件中创建独立的 `snapshot_records` 表；"Event Store 相邻表"即此含义]

**FR-2.4** MUST：写入操作完成后，工具响应体 MUST 包含 `success: bool`、`written_content` 的前 200 字符摘要（供 LLM 回显用）。

**FR-2.5** SHOULD：Session 结束时，比对磁盘文件 mtime 与 session 开始时记录的 mtime；若漂移则写 `SNAPSHOT_DRIFT_DETECTED` 日志（warn 级）。不阻断流程，但记录供排查。

**FR-2.6** MUST：SnapshotRecord TTL 到期后，后台任务定期清理（建议每日一次），删除超过 30 天的记录；不引入新调度依赖，复用现有 cleanup 机制。

---

### FR-3 Threat Scanner（pattern table + invisible-char + severity 分级）[必须]

**FR-3.1** MUST：所有写入 USER.md 或 MEMORY.md 的内容，在执行前经 Threat Scanner 扫描；扫描在 PolicyGate 层统一触发，不在工具层单独拦截（Constitution C10：Policy-Driven Access）。

**FR-3.2** MUST：Threat Scanner 实现正则 pattern table（参考 Hermes `_MEMORY_THREAT_PATTERNS`，≥ 15 条 pattern），覆盖：prompt injection、role hijacking、exfiltration via curl/wget、SSH backdoor 指令、base64 编码 payload。每条 pattern 有唯一 `pattern_id` 和 `severity`（WARN / BLOCK）。

**FR-3.3** MUST：Threat Scanner 实现 invisible unicode 字符检测（`_INVISIBLE_CHARS` frozenset 遍历，O(n) 扫描），检测零宽字符（U+200B、U+200C、U+200D、ZWNBSP 等）注入；命中则 severity = BLOCK。

**FR-3.4** MUST：severity = BLOCK 时，操作被拦截，返回 `blocked: true` + `pattern_id` + `pattern_description`；`MEMORY_ENTRY_BLOCKED` 事件写入 Event Store，含 `pattern_id` 和输入内容摘要（不含原始恶意内容完整文本）。

**FR-3.5** SHOULD：severity = WARN 时（pattern 命中但置信度模糊），可通过 `--force` flag 旁路，旁路操作必须经 Approval Gate 人工确认后执行；`blocked_reasons` 字段与 Feature 079 Phase 4 已实现的结构化机制对齐。

**FR-3.6** MAY：扫描结果命中但语义模糊时，可 escalate 给 utility model（`smart_scan` 模式，ProviderRouter 直调）进行 APPROVE/DENY/ESCALATE 三态判断；首版不实现，接口预留。[YAGNI-移除：当前迭代不需要，扫描结果直接走 severity 分级即可，smart_scan 在质量问题出现后再实现]

---

### FR-4 Approval Gate（session allowlist + dangerous-action gate + SSE 异步路径）[必须]

**FR-4.1** MUST：Approval Gate 与 Threat Scanner 协同：Threat Scanner 扫描结果（pattern_id、severity）作为 Approval Gate 的输入依据，ApprovalGate 基于 `SideEffectLevel × PolicyAction` 双维度矩阵决策是否需要人工审批。

**FR-4.2** MUST：`APPROVAL_REQUESTED` 事件写入 Event Store 时，包含 `tool_name`、`threat_category`、`severity`、`operation_summary`；Web UI 通过 SSE 接收到事件后展示审批卡片，卡片包含上述信息。

**FR-4.3** MUST：Session 级 allowlist：用户在当前 session 中批准某类操作后，同 session 内相同类型操作无需重复审批；session 结束后 allowlist 清零（不跨 session 持久化）。

**FR-4.4** MUST：`APPROVAL_DECIDED` 事件写入 Event Store，含 `decision`（approved / rejected）、`operator`（owner）、`timestamp`；Agent 通过 SSE 异步收到决策结果后恢复或取消操作。

**FR-4.5** MUST：拒绝操作时，Agent 必须收到明确的拒绝通知（非 timeout），并向用户说明原因；不允许操作静默取消。

---

### FR-5 Sub-agent Delegation（delegate_task + max_depth=2 + max_concurrent_children=3 + 黑名单）[必须]

**FR-5.1** MUST：新增 `delegate_task` 工具，schema 包含：`target_worker`（Worker 名称）、`task_description`（任务描述）、`callback_mode`（`async` / `sync`）、`max_wait_seconds`（sync 模式超时，默认 300s）。

**FR-5.2** MUST：`delegate_task` 调用时，检查当前 Agent 的 `delegation_depth`；depth ≥ 2 时调用失败，返回错误说明（防止无限嵌套，缓解 R5 风险）。

**FR-5.3** MUST：`max_concurrent_children = 3`：同一 Agent 最多同时持有 3 个活跃 sub-agent；超出时 `delegate_task` 返回 `CAPACITY_EXCEEDED` 错误，用户可查询当前子任务状态。

**FR-5.4** MUST：Worker 黑名单：`delegate_task` 不允许派发到黑名单 Worker（初始黑名单为空，通过配置扩展）；黑名单命中时立即返回错误，`SUBAGENT_SPAWNED` 事件不写入。

**FR-5.5** MUST：Sub-agent 生命周期事件：`SUBAGENT_SPAWNED`（含 `task_id`、`target_worker`、`depth`）、`SUBAGENT_RETURNED`（含 `task_id`、`result_summary`）写入 Event Store（Constitution C2 合规）。

**FR-5.6** MUST：写操作类的子任务（需修改档案或执行有副作用操作）MUST 经过 ToolBroker + PolicyGate 路径（Constitution C4 Two-Phase）；纯读/分类子任务 MAY 通过 ProviderRouter 直调（降低 latency）。

---

### FR-6 Routine Scheduler（asyncio.Task / cron 30min observation_promoter / 隔离会话）[必须]

**FR-6.1** MUST：Observation Routine 以独立 `asyncio.Task` 运行（不经 APScheduler），默认每 30 分钟执行一次；通过 `asyncio.Task.cancel()` 支持精确停止（Constitution C7：用户可取消）。

**FR-6.2** MUST：Routine 运行在隔离会话中，不访问当前活跃用户 session 的 context，不共享 LLM 调用窗口，不修改活跃 session 的系统提示快照。

**FR-6.3** MUST：Routine 每个 stage 完成时写入 `OBSERVATION_STAGE_COMPLETED` 事件，含 `stage_name`（extract / dedupe / categorize）、`input_count`、`output_count`、`duration_ms`（Constitution C2 合规）。

**FR-6.4** SHOULD：Routine 可通过 feature flag（配置文件）关闭；关闭后 `asyncio.Task` 不启动，不影响其他功能。

**FR-6.5** MUST：APScheduler 继续承担现有 cron jobs（chat_import_service 等），职责不与 Routine Scheduler 混淆；两者共存，互不干扰。

---

### FR-7 USER.md 写入流（user_profile.update / observe / read 三工具，append-only § 分隔符）[必须]

**FR-7.1** MUST：新增 `user_profile.update` 工具（替代 `bootstrap.complete`），语义明确：写入/更新 USER.md 内容；支持三种操作：`add`（追加新 entry）、`replace`（替换含 `old_text` 的 entry）、`remove`（删除含 `target_text` 的 entry）；工具名在 entrypoints 中包含 `web`、`agent_runtime`、`telegram`。

**FR-7.2** MUST：USER.md 使用 `§` 分隔符作为 entry 边界（Hermes 模式）；每个 entry 为一条独立事实，追加操作在文件末尾新增 `§ {content}` 行；replace/remove 操作通过 substring 匹配目标行，精确替换或删除。

**FR-7.3** MUST：`user_profile.read` 工具：读取 USER.md 当前内容（live state，非快照），返回 `§` 分隔符解析后的 entry 列表；entrypoints 包含 `web`、`agent_runtime`、`telegram`。

**FR-7.4** MUST：`user_profile.observe` 工具：Agent 在对话中发现用户新事实时，调用此工具将候选事实写入 candidates 队列（非直接写入 USER.md）；包含 `fact_content`、`source_turn_id`、`initial_confidence` 字段；写入前经 Threat Scanner 扫描。

[AUTO-CLARIFIED: observation routine 与 user_profile.observe 去重策略 — 两条路径共享同一 candidates 表；routine dedupe 阶段按 `source_turn_id + fact_content_hash` 去重，已由 `user_profile.observe` 写入的候选不会被 routine 重复写入；routine 只处理尚未有对应候选记录的对话轮次]

**FR-7.5** MUST：replace 和 remove 操作（不可逆）MUST 经过 two-phase 确认 —— 第一阶段触发 `APPROVAL_REQUESTED` 事件并写入 Event Store，Web UI 通过 SSE 接收后展示包含 diff 内容的审批卡片；第二阶段用户在 UI 点击批准后执行写入（复用 FR-4 Approval Gate 机制）；add 操作（可逆，用 remove 撤销）可直接执行。符合 Constitution C4（Two-Phase）+ C10（Policy-Driven Access 单一入口收敛）。[C1 已解决：选项 B Approval Gate 卡片]

**FR-7.6** MUST：USER.md 字符总量 MUST 有上限（建议 50,000 字符），超出时拒绝新增 entry 并告知用户；防止无限增长影响系统提示 token 消耗。

---

### FR-8 UI Promote 流程（候选列表 + 编辑 + accept/reject + 批量操作）[必须]

**FR-8.1** MUST：Web UI 新增"Memory 候选"面板，通过 `GET /api/memory/candidates` API 获取 `pending` 状态候选列表；每条候选展示：`fact_content`、`category`、`confidence`、`created_at`、`source`（来源对话摘要）。

**FR-8.2** MUST：每条候选支持三种操作：accept（直接写入 USER.md）、edit+accept（修改内容后写入）、reject（丢弃）；accept 和 edit+accept 写入前经 Threat Scanner 扫描和 PolicyGate 检查。

**FR-8.3** MUST：UI 提供批量操作：全选 + 批量 reject（一键清理所有待处理候选）；减少候选堆积时的用户操作成本（仲裁 3）。

**FR-8.4** MUST：UI 在有未处理候选时展示红点提醒（badge count）；候选数量清零时红点消失。

**FR-8.5** SHOULD：后端 `POST /api/memory/candidates/{id}/promote`（accept / edit+accept）和 `POST /api/memory/candidates/{id}/discard`（reject）API；`PUT /api/memory/candidates/bulk_discard`（批量 reject）API。

---

### FR-9 OwnerProfile 派生 Sync（USER.md 是 SoT，启动时 + 写入 hook 触发 sync）[必须]

**FR-9.1** MUST：USER.md 是所有用户档案数据的权威来源（Single Source of Truth）；OwnerProfile SQLite 表退化为派生只读视图，不作为写入目标。

**FR-9.2** MUST：系统启动时，从 USER.md 解析内容 sync 到 OwnerProfile 表（`owner_profile_sync_on_startup()`）；解析失败时 OwnerProfile 保持上次 sync 状态，写 WARN 日志，不阻断启动。

**FR-9.3** MUST：`user_profile.update` 工具写入 USER.md 成功后，同步触发 OwnerProfile sync hook（`sync_owner_profile_from_user_md()`）；sync hook 异步执行，不阻塞工具响应。

**FR-9.4** MUST：BootstrapSession 表（F082 引入）在 Phase 4 退役，所有引用该表的代码路径删除，`grep -r "BootstrapSession"` 结果必须为零；bootstrap 状态管理迁移到 OwnerProfile 的 `bootstrap_completed` 字段。

**FR-9.5** MUST：OwnerProfile 的 `is_filled` 方法（F082 的 D3 断层根因）删除，替换为直接检查 USER.md 是否存在且字符数 > 100（更可靠的有内容判断方式）。

---

### FR-10 事件类型定义（C2 Everything is Event 合规）[必须]

**FR-10.1** MUST：以下 10 个新事件类型定义并写入 SQLite event store schema，每个事件有唯一 `event_type` 字符串常量和必要字段定义：

[AUTO-CLARIFIED: 补充 MEMORY_ENTRY_REMOVED 事件 — remove 操作是独立的写操作，Constitution C2 要求所有写操作有对应事件；原 spec 仅定义 9 个事件，遗漏了 remove 操作，现补为 10 个]

| 事件类型 | 触发时机 | 必要字段 |
|---------|---------|---------|
| `MEMORY_ENTRY_ADDED` | `user_profile.update` add 操作成功后 | `tool_call_id`, `entry_content_hash`, `user_id` |
| `MEMORY_ENTRY_REPLACED` | `user_profile.update` replace 操作成功后 | `tool_call_id`, `old_content_hash`, `new_content_hash` |
| `MEMORY_ENTRY_REMOVED` | `user_profile.update` remove 操作成功后 | `tool_call_id`, `removed_content_hash`, `user_id` |
| `MEMORY_ENTRY_BLOCKED` | Threat Scanner 拦截后 | `pattern_id`, `severity`, `input_content_hash` |
| `OBSERVATION_OBSERVED` | `user_profile.observe` 写入 candidates 后 | `candidate_id`, `confidence`, `source_turn_id` |
| `OBSERVATION_STAGE_COMPLETED` | Routine pipeline 每个 stage 完成后 | `stage_name`, `input_count`, `output_count`, `duration_ms` |
| `OBSERVATION_PROMOTED` | 候选 accept 写入 USER.md 后 | `candidate_id`, `edited`, `user_id` |
| `OBSERVATION_DISCARDED` | 候选 reject 或自动归档后 | `candidate_id`, `reason` (manual_reject / auto_archive) |
| `SUBAGENT_SPAWNED` | `delegate_task` 成功派发后 | `task_id`, `target_worker`, `depth` |
| `SUBAGENT_RETURNED` | Sub-agent 任务返回后 | `task_id`, `result_summary`, `duration_ms` |

**FR-10.2** MUST：`APPROVAL_REQUESTED` 和 `APPROVAL_DECIDED` 事件（已有）扩展字段：新增 `threat_category`（来自 Threat Scanner）和 `pattern_id`（若由 Threat Scanner 触发）。

---

## 不变量（Invariants）

以下约束在 F084 实现全程必须保持：

1. **测试 0 regression**：F084 完成时，全量测试（≥ 2759 个）通过率 100%，无新增失败用例
2. **prefix cache 命中率不降**：mid-session 写入 USER.md 不改变当前 session 的系统提示 token 序列；Snapshot Store 冻结机制保证此约束
3. **Constitution 10 条全部对齐**：每个 FR 在其定义中已标注对应的 Constitution 条款；Constitution C2 由 FR-10 明确覆盖
4. **LLM 决策不被硬编码**：`user_profile.observe` 的调用时机由 LLM 自主决策（Agent 发现新事实时）；observation routine 的 promote 决策由 LLM categorize（不由规则引擎）；Constitution C9 遵从
5. **max_depth=2 防死循环**：`delegate_task` depth 检查是必须的运行时约束，不可被 bypass
6. **所有写文件操作经 ThreatScanner**：`user_profile.update`、`user_profile.observe` 写入前均经扫描；promotion accept 写入前亦经扫描；不存在绕过 Scanner 的写入路径
7. **USER.md SoT 不可破坏**：OwnerProfile 表只读，任何代码路径不得直接写入 OwnerProfile（OwnerProfile 只由 sync hook 更新）

---

## Scope Lock

### 本次不动的模块

- ❌ ProviderRouter / SkillRunner / SkillPipeline（保持现有接口不变）
- ❌ Telegram channel（Telegram 入口的工具可见性问题在 Tool Registry 重构中被动修复，但不专项改 Telegram channel 代码）
- ❌ Memory backend（LanceDB / Fragments / SoR 保持现有实现）

### 不做的事项

- ❌ migrate CLI 命令（仲裁 1：用户决策，重装路径为清 data/ + behavior/ 后 octo update）
- ❌ confidence < 0.7 的 observation 写入 review queue（仲裁 2：直接丢弃）
- ❌ filelock / portalocker / markdown-it-py / mistune / 新调度框架（不引入新依赖）
- ❌ Honcho 跨产品用户建模（外部 API 依赖，单用户场景 observation 路径已够用）
- ❌ OpenClaw SOUL/AGENTS/TOOLS 三文件替代方案（保留现有 OwnerProfile + USER.md 架构）
- ❌ 多层 Agent 委托超过 max_depth=2（首版上限）
- ❌ Threat Scanner smart_scan / utility model 仲裁模式（FR-3.6 标注 YAGNI-移除，首版只做规则扫描）

---

## 风险盘点

### P0 风险

**R3 - 删除 F082 代码遗漏依赖（silent break）**
- 概率：中 | 影响：高
- 描述：约 2500 行 F082 代码删除时，若遗漏 import 引用，导致运行时 ImportError 或功能静默失效
- 缓解：Phase 1 先保留 shim 接口（保持 API 不变但实现改写）；删除前 `grep -r "bootstrap.complete"` / `grep -r "BootstrapSession"` / `grep -r "is_filled"` 全量扫描引用清零；每 phase 独立回归测试
- 落实：Plan Phase 1（shim）+ Phase 4（退役清理）

**R7 - OwnerProfile vs USER.md 双写一致性**
- 概率：中 | 影响：高
- 描述：F082 写入 OwnerProfile（SQLite），F084 写入 USER.md（文件），两者可能漂移；用户已有 OwnerProfile 数据在重构后可能丢失或不同步
- 缓解：明确 USER.md 为 SoT（FR-9.1）；启动时 sync OwnerProfile from USER.md（FR-9.2）；`user_profile.update` 成功后触发 sync hook（FR-9.3）；Phase 2 优先实现 sync 逻辑并全量测试
- 落实：Plan Phase 2

### P1 风险

**R1 - Snapshot mtime race（外部进程改 USER.md）**
- 概率：中 | 影响：中
- 描述：外部进程改 USER.md 时，`_system_prompt_snapshot` 不感知，当前 session 内信息不一致
- 缓解：Session 开始时记录 mtime；session 结束时 diff，漂移则写 `SNAPSHOT_DRIFT_DETECTED` WARN 日志；文档说明"修改 USER.md 需重开 session 生效"（FR-2.5）
- 落实：Plan Phase 2

**R2 - Threat Scanner 误杀（false positive）**
- 概率：中 | 影响：中
- 描述：正则语义粗糙，"you are now an expert" 等合法内容被误匹配，阻断用户正常写入档案
- 缓解：pattern 添加词边界（`\b`）和上下文锚点；WARN 级 pattern 提供 `--force` 旁路（需 Approval Gate 确认）；`pattern_id` 在错误响应中展示（FR-3.5）
- 落实：Plan Phase 1 + Phase 3（scanner 调优）

**observation 质量 / 幻觉**
- 概率：中 | 影响：中
- 描述：LLM 事实提取准确率不稳定，产生大量错误候选
- 缓解：confidence ≥ 0.7 gate（仲裁 2）；30 天自动归档（仲裁 3）；用户 UI 提供批量 reject（FR-8.3）；首版监控 promoted/discarded 比率
- 落实：Plan Phase 3（quality gate）

### P2 风险

**R4 - routine + 现有 cron 冲突**
- 概率：低 | 影响：中
- 描述：observation routine 与 APScheduler cron 同时调度，争抢 LLM 调用窗口
- 缓解：observation routine 使用独立 asyncio.Task，不经 APScheduler；两者使用不同 model alias（FR-6.5 职责分离）
- 落实：Plan Phase 3

**R5 - sub-agent token / latency 翻倍**
- 概率：中 | 影响：低
- 描述：categorize stage 频繁调用 utility model，成本线性增长
- 缓解：categorize 阶段 `max_tokens=200`；observation routine interval 默认 30min（非实时）；批量候选处理（N 条候选一次 LLM 调用）；Logfire 追踪 token 消耗（FR-5 / FR-6）
- 落实：Plan Phase 3

**R6 - UI promote 无人 review → 候选堆积**
- 概率：低 | 影响：低
- 描述：单用户 AI OS 典型场景：observation 持续提取，用户长时间不打开 UI review
- 缓解：候选队列上限 50 条，超上限停止提取；30 天自动归档；Telegram 定期推送摘要；UI 红点提醒（FR-8.4）
- 落实：Plan Phase 3

**R8 - Tool Registry AST 扫描启动延迟**
- 概率：低 | 影响：低
- 描述：builtin_tools 工具数量增加时，AST parse + import 时间超过 1s
- 缓解：扫描仅在启动时执行一次；设置 5s 超时；`_module_registers_tools()` 快速过滤非工具模块（FR-1.1）
- 落实：Plan Phase 1（可观测监控）

**R9 - 重装路径误覆盖已有 USER.md**
- 概率：中 | 影响：高
- 描述：重装后 bootstrap 逻辑误判为全新用户，覆盖已有 USER.md
- 缓解：bootstrap 逻辑必须检查 USER.md 是否已存在且非空，存在则跳过初始化写入（J5 验收场景 3）
- 落实：Plan Phase 2

---

## 验收准则

### 实测场景（4 个核心路径）

**验收 1 - 路径 A 完整打通**
- Connor 在全新实例中，通过 web UI 对 Agent 说"帮我初始化档案，我叫 Connor，时区 Asia/Shanghai，职业工程师"
- Agent 调用 `user_profile.update`（add 操作），USER.md 被写入，LLM 响应包含确认内容，SnapshotRecord 被创建
- **判定**：PASS = USER.md 文件存在且包含输入内容，SnapshotRecord 存在，`MEMORY_ENTRY_ADDED` 事件写入 Event Store，web UI 无"工具不可见"或"写入失败"错误

**验收 2 - Threat Scanner 防护**
- 通过 API 调用 `user_profile.update`，传入包含 `ignore previous instructions` 的内容
- Threat Scanner 必须拦截，`MEMORY_ENTRY_BLOCKED` 事件写入，API 响应包含 `blocked: true` + `pattern_id`
- 同时，传入正常内容（"喜欢读技术书籍"），Threat Scanner 必须通过（零 false positive）
- **判定**：PASS = 恶意内容被 block + 正常内容通过，USER.md 中无恶意内容

**验收 3 - Observation → UI Promote 闭环**
- 触发 observation routine（可手动触发），提供包含用户新事实的对话记录
- candidates 表出现 pending 候选（confidence ≥ 0.7），Web UI 候选列表可见
- Connor 在 UI 中 accept 一条候选，该候选内容写入 USER.md，`OBSERVATION_PROMOTED` 事件写入
- **判定**：PASS = candidates 表有记录 + Web UI 候选列表渲染 + accept 后 USER.md 更新

**验收 4 - 重装路径**
- 清除 `~/.octoagent/data/` 和 `~/.octoagent/behavior/`，保留 `octoagent.yaml` 和 `.env`
- 执行 `octo update` 重启，完成 bootstrap 流程
- 通过 web UI 请求 Agent 写入档案，成功完成
- **判定**：PASS = bootstrap 完成 + USER.md 写入成功 + 全程无 ImportError

### 技术指标

- **SC-001** 退役结果可观察：F082 抽象层（BootstrapSession 状态机 + Orchestrator + UserMdRenderer + bootstrap_tools + capability_pack 旧 explicit 字典）在最终代码库 grep 结果为零。代码变更量（≥ 2000 行删除 / ≥ 3500 行新增）作为实施过程参考，不作为业务验收硬指标。
- **SC-002** 无新依赖：`pyproject.toml` 的 dependencies 列表不增加新条目
- **SC-003** BootstrapSession 退役：`grep -r "BootstrapSession"` 结果为零
- **SC-004** bootstrap.complete 退役：`grep -r "bootstrap.complete"` 结果为零
- **SC-005** is_filled 退役：`grep -r "is_filled"` 结果为零（替换为直接检查 USER.md 存在性和内容长度）
- **SC-006** 事件类型覆盖：Event Store schema 包含 FR-10 定义的全部 10 个新事件类型
- **SC-007** 测试 0 regression：`pytest` 全量运行，失败用例数不超过 F084 开始前基准值
- **SC-008** Constitution C2 合规：每个 FR 的写操作均有对应事件写入 Event Store，可通过 Event Store 查询追踪完整操作链
- **SC-009** Constitution C4 合规：replace / remove 操作、sub-agent 写操作均有 Plan → Gate → Execute 两阶段记录
- **SC-010** Tool Registry entrypoints 完整：`user_profile.update` / `user_profile.read` / `user_profile.observe` / `delegate_task` 的 entrypoints 包含 `web`，通过单元测试验证
- **SC-011** prefix cache 保护：同一 session 内多 turn 之间，系统提示 token 序列不变（可通过 LLM provider 的 cache hit 指标验证）

---

## 关键实体

- **ToolEntry**：工具注册单元，属性：`name`、`entrypoints`、`toolset`、`handler`、`schema`、`side_effect_level`；ToolRegistry 的基本管理单元
- **SnapshotRecord**：工具调用结果持久化记录，属性：`id`（UUID）、`tool_call_id`、`result_summary`、`timestamp`、`ttl_days`；存储于现有 SQLite DB 的独立 `snapshot_records` 表
- **ObservationCandidate**：observation 候选事实，属性：`id`（UUID）、`fact_content`、`category`、`confidence`、`status`（pending / promoted / rejected / archived）、`source_turn_id`、`created_at`、`expires_at`（created_at + 30 天）
- **ThreatScanResult**：扫描结果值对象，属性：`blocked`（bool）、`pattern_id`（nullable）、`severity`（WARN / BLOCK）、`matched_pattern_description`
- **DelegationContext**：sub-agent 调用上下文，属性：`task_id`（UUID）、`depth`（int）、`target_worker`、`parent_task_id`（nullable）

---

## 5 阶段交付计划

### Phase 1 — Harness 基础层（~2天）

**目标**：Tool Registry + Threat Scanner 上线，bootstrap.complete 退役，web 入口工具可见性修通

- 实现 ToolRegistry（AST 扫描 + ToolEntry + dispatch + threading.RLock）
- 迁移现有 builtin_tools，添加 `entrypoints` 字段声明（全量工具）
- 实现 ThreatScanner（正则 pattern table + invisible unicode 检测 + severity 分级）
- 集成 ThreatScanner 到 PolicyGate（统一扫描入口，C10 合规）
- 删除 `bootstrap.complete` 工具（全量 grep 确认引用清零后删除）
- Phase 1 回归测试：`pytest tests/` 全量通过

### Phase 2 — Snapshot Store + USER.md 写入流（~2天）

**目标**：SnapshotStore + user_profile 三工具 + OwnerProfile sync 上线，路径 A 完整打通

- 实现 SnapshotStore（内存 dict 冻结 + atomic rename + fcntl.flock + SnapshotRecord TTL）
- 实现 `user_profile.update`（add / replace / remove + § 分隔符 + two-phase 确认）
- 实现 `user_profile.read`（entry 列表返回）
- 实现 `user_profile.observe`（写入 candidates，Threat Scanner 前置）
- 实现 OwnerProfile sync hook（USER.md 写入后触发 + 启动时 sync）
- 验收场景 1（路径 A 打通）

### Phase 3 — Approval + Delegation + Routine + UI（~4天）

**目标**：全部 Nice-to-have 功能上线，observation → UI promote 完整闭环

- 重构 Approval Gate（与 Threat Scanner 协同 + SSE 异步路径 + session allowlist）
- 实现 `delegate_task` 工具（max_depth=2 + max_concurrent_children=3 + 黑名单）
- 实现 Observation Routine（asyncio.Task + extract/dedupe/categorize pipeline + confidence gate）
- 实现 candidates 表 schema + `GET/POST /api/memory/candidates` API
- 实现 Web UI Memory 候选面板（候选列表 + accept/edit/reject + 批量 reject + 红点）
- 验收场景 2（Threat Scanner）+ 验收场景 3（observation → promote 闭环）

### Phase 4 — 退役 + 文档（~1天）

**目标**：F082 遗留代码完全清除，架构文档同步更新

- 退役 BootstrapSession 表（`grep -r "BootstrapSession"` 清零）
- 清理 F082 全部遗留代码（is_filled、UserMdRenderer 旧版、BootstrapIntegrityChecker 等）
- 更新 `docs/blueprint/` 相关文档（bootstrap-profile-flow.md、architecture-audit.md）
- 验收场景 4（重装路径）
- 全量测试通过，代码变更量统计

### Phase 5 — 稳定性（~1天）

**目标**：端对端压测、边界场景验证、observation 质量调优

- 端对端场景回归（4 个验收场景连续 3 次通过）
- Threat Scanner false positive 测试（30 条边界测试用例，FP 率 < 5%）
- Observation Routine 压测（模拟 10 次 routine 运行，event 写入完整性检查）
- Logfire / structlog 覆盖检查（所有新模块有 span + 结构化日志）
- Commit + Push，更新里程碑状态（M5 文件工作台阶段）

---

## 复杂度评估（供 GATE_DESIGN 审查）

- **组件总数**：6 个新增核心组件（ToolRegistry、SnapshotStore、ThreatScanner、ObservationRoutine、DelegationContext 管理、Web UI Memory 候选面板）
- **接口数量**：新增 API 端点 5 个（`GET /api/memory/candidates`、`POST /api/memory/candidates/{id}/promote`、`POST /api/memory/candidates/{id}/discard`、`PUT /api/memory/candidates/bulk_discard`、`GET /api/snapshots/{tool_call_id}`）；新增工具接口 4 个（`user_profile.update` / `user_profile.read` / `user_profile.observe` / `delegate_task`）
- **依赖新引入数**：0（零新外部依赖，全部标准库 + 现有依赖实现）
- **跨模块耦合**：涉及 3 个现有模块接口变更——ToolBroker（新增 ToolRegistry dispatch 下游）、PolicyGate（集成 ThreatScanner 结果）、ApprovalManager（扩展 APPROVAL 事件字段）
- **复杂度信号**：存在以下信号——asyncio 并发控制（SnapshotStore + Routine Task）；状态机（ObservationCandidate.status pending/promoted/rejected/archived）；多阶段 pipeline（observation extract/dedupe/categorize）
- **总体复杂度**：**HIGH**

**判定依据**：组件数 6 > 5（HIGH 阈值）；存在 3 个复杂度信号（并发控制 + 状态机 + pipeline）；跨 3 个现有模块接口变更。建议 GATE_DESIGN 进行人工审查，重点确认 ToolBroker 和 PolicyGate 的接口契约设计不引入职责漂移。

---

*Spec 基于 research-synthesis.md (2026-04-28)、product-research.md、tech-research.md 生成。6 个用户已锁定决策全部 honored（OwnerProfile 派生表保留、observation 路径首版上、delegate_task 首版上、UI promote 完整做、bootstrap.complete 直接 break、不做 migrate CLI）。3 个冲突仲裁（不做 migrate CLI / confidence ≥ 0.7 gate / 批量 reject + 30 天自动归档）已在对应 FR 和 Scope Lock 中体现。*

---

## NEEDS CLARIFICATION

**扫描日期**: 2026-04-27
**检测结果**: 5 个歧义点，自动解决 4 个，1 个 CRITICAL 需用户决策

---

### CRITICAL 问题 C1：replace/remove 的 two-phase 确认交互形态 [已解决 → Option B]

**决策**：选 B（Approval Gate 卡片），理由 = 与 FR-4 已有机制收敛，符合 Constitution C10 单一入口原则；FR-7.5 已更新落实。本节保留作历史档案。

---

### CRITICAL 问题 C1（原始描述）

**位置**: FR-7.5、J4 验收场景 3

**上下文**: spec 要求 replace/remove 操作必须经过"先 diff preview → 用户确认 → 执行"的两阶段流程（Constitution C4）。但 spec 未明确这个确认是通过哪种交互机制实现的。

**为什么是 CRITICAL**: 两种实现路径在 Phase 2 的代码架构上有本质差异：
- **选项 A（LLM 对话轮次）**：工具第一次调用仅返回 diff preview 文本给 LLM，LLM 向用户展示 diff 并请求确认，用户下一条消息确认后工具再次被调用执行写入。无需修改 Approval Gate，但需要工具自己维护"待确认"状态（临时存储 pending diff）。
- **选项 B（Approval Gate 卡片）**：replace/remove 触发时走现有 Approval Gate SSE 路径，Web UI 弹出包含 diff 内容的审批卡片，用户点击批准后执行。复用 FR-4 已有机制，无需工具维护中间状态，但 Approval Gate 需支持展示结构化 diff。

**推荐**: 选项 B（Approval Gate 卡片）— 理由：FR-4 的 Approval Gate 已是 F084 的必要实现，复用它处理 replace/remove 确认可以统一高风险操作的确认入口，符合 Constitution C10（Policy-Driven Access 收敛到单一入口）；选项 A 需要工具层自行管理待确认状态，引入额外状态管理复杂度，与"工具无状态"的设计原则相悖。

**选项对比**:

| 选项 | 描述 | 架构影响 |
|------|------|---------|
| A | LLM 对话轮次内 two-phase：工具返回 preview，用户下一条消息确认，工具再次调用执行 | 工具需维护 pending diff 状态（临时存储）；LLM 负责 diff 展示和确认引导；无需改 Approval Gate |
| B | Approval Gate 卡片：replace/remove 触发 APPROVAL_REQUESTED 事件，Web UI 展示结构化 diff 卡片，用户点击批准后执行 | 复用 FR-4 Approval Gate；需扩展卡片支持展示 diff 内容；工具无需维护中间状态 |

---

### 自动解决的澄清

| # | 问题 | 自动选择 | 理由 | 位置 |
|---|------|---------|------|------|
| W1 | live state 与冻结快照的具体区分 | live state = 独立可变内存 dict，随写入同步更新；`user_profile.read` 读 live state；系统提示注入读冻结副本 | Hermes 模式的标准实现；两者分离是 prefix cache 保护的前提 | FR-2.1（已在 FR 内补注） |
| W2 | SnapshotRecord 存储位置（"Event Store 相邻表"含义） | 复用现有 SQLite DB 文件，创建独立 `snapshot_records` 表，不新增 DB 文件 | 保持 0 新依赖原则；关键实体描述已更新为明确说法 | FR-2.3、关键实体（已更新） |
| W3 | `user_profile.observe`（Agent 主动调用）与 observation routine 的去重逻辑 | routine dedupe 阶段按 `source_turn_id + fact_content_hash` 去重，已有候选的轮次不重复写入 | 两条路径的设计意图不同（实时 vs 批量），共享 candidates 表但去重防止重复 | FR-7.4（已在 FR 内补注） |
| W4 | FR-10.1 缺少 `MEMORY_ENTRY_REMOVED` 事件（Constitution C2 遗漏） | 补充 `MEMORY_ENTRY_REMOVED` 事件，FR-10.1 表格由 9 个扩展为 10 个，SC-006 对应更新 | remove 是独立写操作，C2 要求全部写操作有对应事件；遗漏会导致审计链断裂 | FR-10.1（已更新表格）、SC-006（已更新计数） |
