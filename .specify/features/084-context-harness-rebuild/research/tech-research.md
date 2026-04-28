# 技术调研报告：Feature 084 - Context + Harness 全栈重构

**特性分支**: `084-context-harness-rebuild`
**调研日期**: 2026-04-27
**调研模式**: 在线
**调研基础**: [独立模式] 本次技术调研未参考产品调研结论（product-research.md 不存在）；基于需求描述、Hermes 参考源码和 OctoAgent 现有代码库执行。

---

## 1. 调研目标

**核心技术问题**：

1. SnapshotStore 应采用何种持久化策略以同时保护 prefix cache 并支持多进程写入安全？
2. Tool Registry 应如何兼容现有 ToolBroker 并实现模块自注册？
3. Threat Scanner 的误杀率/性能权衡如何取舍，是否依赖外部库？
4. observation routine 如何与现有 APScheduler cron 共存而不产生资源冲突？
5. Sub-agent delegation 是复用 Worker 框架还是轻量 LLM 工具封装？
6. USER.md section 合并策略哪种对用户审视最友好且覆盖丢失风险最低？

**需求范围（来自需求描述）**：

Must-have:
- Snapshot Store（prefix cache 保护 + 多进程写安全）
- Tool Registry（AST 发现 + ToolEntry 模型）
- Threat Scanner（prompt injection + exfil 检测）
- Routine Scheduler（observation routine 周期运行）
- Sub-agent delegation（token 预算隔离）
- USER.md 合并策略（section 级 upsert）

Nice-to-have:
- SnapshotStore 跨进程持久化恢复
- Tool Registry 热更新（deregister/re-register）
- Threat Scanner smart approval（utility model 辅助）
- observation routine 结果 promote 工作流
- UI candidate queue 展示

---

## 2. 架构方案对比

### 2.1 Snapshot Store

**背景**：核心问题是"session 开始时冻结一份系统提示快照，保护 prefix cache；session 期间工具写入只改磁盘不改快照；下次 session 加载最新内容"。Hermes `MemoryStore._system_prompt_snapshot` 是最直接的参考。

| 维度 | 方案 A：纯内存 dict 快照（Hermes 风格） | 方案 B：SQLite snapshot 表 | 方案 C：fs mtime watch |
|------|---------------------------------------|--------------------------|------------------------|
| 概述 | 进程启动时读文件，冻结 dict，mid-session 写只改磁盘 | 每次 session 开始写 snapshots 表，记录版本号 | 用 mtime 判断文件是否变化，按需重新加载 |
| prefix cache 保护 | ✅ 完美：系统提示 token 序列在整个 session 内绝对稳定 | ✅ 可做到：加载快照行后不改 | ⚠️ 风险：mtime 轮询会导致对话中途系统提示刷新，破坏 prefix cache |
| 并发写安全 | ✅ 写磁盘用 atomic rename（Hermes 已实现）+ fcntl.flock | ⚠️ SQLite WAL 并发写可靠，但需处理 SQLITE_BUSY | ⚠️ mtime race：外部写入与检查之间存在窗口期 |
| 持久化恢复 | ✅ 从磁盘文件恢复（文件即持久化）| ✅ 数据库表，可按 session_id 精确恢复 | ✅ 文件即持久化 |
| 复杂度 | ⭐ 极低：约 80 行，无新依赖 | ⭐⭐⭐ 中：需建表 + schema + migration | ⭐⭐ 低：轮询逻辑简单，但语义不清 |
| 与现有兼容性 | ✅ 直接复用 aiosqlite 的文件路径配置，无须新表 | ✅ 接入现有 SQLite WAL | ⚠️ 与多进程写不兼容，mtime 可能陈旧 |

**推荐：方案 A（纯内存 dict 快照 + atomic rename 写磁盘）**

理由：
1. Hermes 已生产验证此模式：`_system_prompt_snapshot` 在 `load_from_disk()` 一次性冻结，`format_for_system_prompt()` 返回冻结副本，工具写入不改快照。prefix cache 保护最彻底。
2. 写磁盘安全性通过 `tempfile.mkstemp` + `os.replace()` 原子替换实现，fcntl.flock 保护 read-modify-write；OctoAgent 是单用户系统，并发写竞争极低。
3. 零新依赖，100% 标准库，与现有 `~/.octoagent/memory/USER.md` 文件系统路径无缝对接。
4. 方案 B 的额外 snapshot 表在只有一个用户的场景中是过度设计；方案 C 的 mtime watch 会在 session 中途触发系统提示变更，直接破坏 prefix cache 保护目标。

---

### 2.2 Tool Registry

**背景**：OctoAgent 现有 `CapabilityPackService`（重构后约 2138 行，builtin_tools 子包）和 `ToolBroker`。Hermes 用 AST 扫描 + module-level `registry.register()` 实现工具自注册。

| 维度 | 方案 A：AST 扫描 + module-level register()（Hermes 风格） | 方案 B：Pydantic AI tool decorator | 方案 C：保留 capability_pack 但简化 |
|------|----------------------------------------------------------|----------------------------------|-------------------------------------|
| 工具发现 | ✅ 自动：扫描 builtin_tools/*.py，检测 `registry.register()` 调用，动态 import | ⚠️ 半自动：需显式 @tool 装饰器，Pydantic AI 框架内置管理 | ⚠️ 手动：需维护工具列表映射 |
| 配置中心 | ✅ ToolEntry 携带 toolset/check_fn/requires_env/max_result_size | ✅ Pydantic AI RunContext 携带类型安全依赖 | ⚠️ 分散在多处 |
| 与现有 ToolBroker 兼容性 | ✅ 高：ToolRegistry.dispatch() 可作为 ToolBroker 的后端，ToolBroker 保留外层契约检查 | ⚠️ 需适配：Pydantic AI tool 是框架侧管理，与 OctoAgent PolicyGate 解耦困难 | ✅ 最高：几乎不改现有接口 |
| 热更新 | ✅ deregister(name) + re-register 支持 MCP 动态工具 | ⚠️ Pydantic AI 工具列表在 Agent 构造时固化 | ⚠️ 无明确热更新机制 |
| 首次启动延迟 | ⚠️ AST 扫描约 50-200ms（取决于工具模块数量和磁盘 IO） | ✅ 零扫描延迟 | ✅ 无扫描 |
| 学习曲线 | ⭐⭐ 中：开发新工具需遵循 register() 调用规范 | ⭐⭐⭐ 较高：需理解 Pydantic AI RunContext 依赖注入 | ⭐ 低：现有模式 |

**推荐：方案 A（AST 扫描 + module-level register()）**

理由：
1. Hermes ToolRegistry 是成熟的生产设计（threading.RLock 保护 + snapshot 读取 + deregister 级联清理），可直接移植为 OctoAgent 的 builtin_tools 发现机制。
2. AST 扫描仅在启动时执行一次（约 50-200ms），不影响运行期性能。可通过 `_module_registers_tools()` 快速过滤非工具模块，只 import 声明了 `registry.register()` 的文件。
3. ToolBroker 保留契约检查（schema 反射、policy 决策），ToolRegistry 作为其工具来源，两者职责分离清晰。Constitution C3（Tools are Contracts）得到保证。
4. 方案 B 与 OctoAgent 的 PolicyGate 和 ApprovalManager 解耦困难；方案 C 不解决工具发现的维护成本问题。

---

### 2.3 Threat Scanner

**背景**：USER.md 和 MEMORY.md 内容注入系统提示，必须防止 prompt injection 和 exfiltration。Hermes 用约 15 条正则 pattern + invisible unicode 检测，纯标准库实现。

| 维度 | 方案 A：纯正则 pattern table（Hermes 风格） | 方案 B：dedicated lib（safety / bandit 等） | 方案 C：LLM-based detection |
|------|-------------------------------------------|--------------------------------------------|----------------------------|
| 性能 | ✅ 微秒级：re.search() 单次约 0.1-1ms | ⚠️ 毫秒到秒级，取决于扫描范围 | ❌ 100ms-1s，依赖网络 |
| 误杀率 | ⚠️ 中：正则不理解语义，"you are now an expert" 可能被 `you are now` 误匹配 | ⚠️ 工具设计目标不同（代码安全 vs prompt injection），误杀率难预测 | ✅ 低：LLM 可理解语义，结合 `APPROVE/DENY/ESCALATE` 三态 |
| 维护成本 | ✅ 低：pattern table 是人类可读的，可逐条审查 | ⚠️ 依赖第三方维护节奏，引入新依赖 | ⚠️ 需要 utility model 可用，额外 token 成本 |
| cost | ✅ 零 cost | ✅ 零 cost（本地扫描）| ⚠️ 每次写 memory 触发模型调用，约 0.001-0.01 USD |
| 无网络降级 | ✅ 完全离线 | ✅ 完全离线 | ❌ 无网络时降级为 allow-all 或 block-all |
| Constitution C6 兼容（降级不崩）| ✅ | ✅ | ⚠️ 需处理 provider 不可用场景 |

**推荐：方案 A（纯正则 pattern table）+ 可选方案 C 作为扩展点**

理由：
1. Hermes 的 15 条 pattern 覆盖了 prompt injection、role hijacking、exfil via curl/wget、SSH backdoor 等主要威胁向量，已在 Hermes 生产中验证。直接移植即可。
2. 额外添加 invisible unicode 检测（`_INVISIBLE_CHARS` frozenset 遍历，O(n) 字符扫描）可覆盖零宽字符注入攻击。
3. LLM-based detection 适合作为可选的 `smart_scan` 模式：当正则命中但置信度低时 escalate 给 utility model（Hermes `_smart_approve()` 的 memory 变体），与 ProviderRouter 对接，不增加强依赖。
4. 方案 B 的 `safety` 库针对 Python 包漏洞扫描（pip safety），不适用于 prompt injection 检测；其他专门库尚无成熟通用选项。

---

### 2.4 Routine Scheduler

**背景**：OctoAgent 已有 APScheduler 用于 cron jobs（chat_import_service、定时任务）。observation routine 是 F084 新增的周期性后台任务（从对话摘要提取 user facts → dedupe → categorize → notify）。

| 维度 | 方案 A：复用现有 APScheduler | 方案 B：纯 asyncio.Task 轻量调度 |
|------|-----------------------------|---------------------------------|
| 与现有 cron 耦合 | ⚠️ 共享 scheduler 实例，cron 配置变化可能影响 routine；需隔离 job_id 命名空间 | ✅ 完全隔离：独立 asyncio.Task，不碰 APScheduler |
| 隔离会话开销 | ⚠️ APScheduler 为每个 job 维护独立上下文，但共享线程池 | ✅ 可精确控制每个 observation Task 的生命周期和取消 |
| 功能完整度 | ✅ 支持 cron 表达式、interval、一次性任务；有 misfire 处理 | ⚠️ 只能做 interval 和一次性，cron 表达式需手写 |
| 与 Event Store 集成 | ✅ APScheduler listener 可以写 TASK_HEARTBEAT 事件 | ✅ 可直接 await event_store.append() |
| 启动/停止生命周期 | ✅ 与 Gateway lifespan 绑定 | ✅ 同上，asyncio.Task cancel 即停 |
| 依赖 | ✅ 已有，无新增 | ✅ 纯标准库 |

**推荐：方案 B（asyncio.Task 轻量调度）用于 observation routine，方案 A 保留用于 cron jobs**

理由：
1. observation routine 的调度模式是"每 N 分钟扫描一次近期对话记录，提取候选事实"，是简单的固定 interval 模式，不需要 cron 表达式，asyncio.sleep(interval) 循环即可。
2. 避免将新的 observation 逻辑与现有 cron 系统耦合——Hermes cron scheduler 已有 file-based lock（`_LOCK_FILE`）防止多进程竞争，observation routine 如果也进入同一 lock 域会产生不必要的阻塞。
3. observation routine 需要精确的 cancel/pause 语义（当用户主动关闭会话或降低 tier 时暂停），asyncio.Task.cancel() 比 APScheduler.remove_job() 更轻量可控。
4. APScheduler 继续承担 cron job（一次性/周期性用户任务），职责不混淆。

---

### 2.5 Sub-agent Delegation

**背景**：observation routine 的 categorize 阶段需要让 utility model 判断候选事实的 category 和置信度；approve flow 需要 agent 执行实际的 USER.md 写入。

| 维度 | 方案 A：复用 Worker 框架（worker_runtime + A2A） | 方案 B：纯 LLM 工具层封装（ProviderRouter 直调）|
|------|------------------------------------------------|------------------------------------------------|
| 执行隔离 | ✅ 完全隔离：独立 Worker Task + Event 链，可查询/取消 | ⚠️ 在调用者上下文内，失败不隔离 |
| token 预算 | ✅ WorkerProfile.budget 精确控制 max_tokens | ⚠️ 需手动在请求中设置 max_tokens |
| 延迟 | ⚠️ 较高：Worker spawn → A2A dispatch → Worker 初始化约 200-500ms | ✅ 低：直接 HTTP 调用约 50-200ms |
| 复用度 | ✅ 利用现有 A2A + Work 状态机 + 审计链 | ⚠️ 重复实现结果解析、错误处理、重试 |
| 适用场景 | 复杂多步骤任务（≥3 工具调用）；需要审批的写操作 | 简单单次 LLM 分类（输入→结构化输出）；utility 模型调用 |
| Constitution C8 可观测 | ✅ 有完整 Event 链 | ⚠️ 需手动写 MODEL_CALL 事件 |

**推荐：根据子任务性质分层使用**

- **分类/评分子任务**（categorize, score candidates）：方案 B（ProviderRouter 直调），约 2-10 个 token，延迟敏感，不需要独立 Work 状态机。
- **写入/approve 执行子任务**（实际 USER.md 写入，需经 PolicyGate）：方案 A（复用 Worker + ToolBroker + PolicyGate），确保 Constitution C4（Two-Phase Side-effect）生效。

理由：observation routine 的 categorize 是纯 read 操作（无副作用），强制走 Worker 开销过大；USER.md 写入是不可逆操作，必须经过 PolicyGate 和 ApprovalManager，Worker 框架的审批链是必须的。

---

### 2.6 USER.md 合并策略（Agent-Zero 模式）

**背景**：多个来源（observation routine、用户主动纠正、OwnerProfile 初始化）可能并发写 USER.md，需要安全合并。

| 维度 | 方案 A：utility model 调和（LLM 决定合并方式） | 方案 B：结构化 section 切片（按 # heading upsert） | 方案 C：append-only（§ 分隔符 + 去重）|
|------|----------------------------------------------|-----------------------------------------------------|---------------------------------------|
| 覆盖丢失风险 | ⚠️ 中：LLM 可能改写用户不想改的 section | ✅ 低：只改指定 heading 下的内容，其他 section 不动 | ✅ 低：只追加，不删不改（需手动 remove）|
| token 成本 | ⚠️ 高：每次写 USER.md 触发全文重写 + utility model 调用 | ✅ 低：只读目标 section，只改目标 section | ✅ 零 cost：纯字符串操作 |
| 可解释性 | ⚠️ 低：LLM 重写后用户难以追踪具体改动 | ✅ 高：diff 清晰，section 级粒度 | ✅ 高：追加记录可读，但文件会增长 |
| 用户审视难度 | ⚠️ 高：每次 merge 可能产生难以预测的 rewrite | ✅ 中：审查具体 section 改动 | ✅ 低：追加内容显而易见 |
| 字符限制控制 | ⚠️ LLM 可能生成超限内容 | ✅ 可按 section 设置字符预算 | ✅ 全局字符上限即可 |
| 与 Hermes MemoryStore 兼容 | ⚠️ 改变了 entry 分隔符（§）范式 | ⚠️ 切换为 Markdown heading 范式，需调整读取逻辑 | ✅ 直接兼容 § 分隔符 + dedup 机制 |

**推荐：方案 C（append-only + § 分隔符）作为基础，方案 B（section upsert）作为结构化可选扩展**

理由：
1. Hermes 的 `§` 分隔符 + `add/replace/remove` 三操作模型已验证：agent 自主决定追加新 fact、替换旧 fact（通过 old_text substring 匹配）、删除过期 fact。简单可靠，token 成本极低。
2. 用户审视成本低：每次写入都是一条独立 entry，diff 清晰。
3. section upsert（方案 B）适合将来的"结构化 USER.md 编辑器"集成，但当前 MVP 不必引入 heading parser。若需要，`markdown-it-py` 可作为轻量解析器（CommonMark 合规，Apache-2.0），但整体不推荐为 MVP 引入。
4. 方案 A（LLM 调和）的不可预测性和 token 成本是核心反模式——Hermes 明确用 agent 的 add/replace/remove 工具代替全文重写，这是设计原则而非工具限制。

---

## 3. 依赖库评估

### 3.1 评估矩阵

| 库名 | 用途 | 版本 | 周下载量（估） | 许可证 | 评级 | 决策 |
|------|------|------|--------------|--------|------|------|
| `filelock` | SnapshotStore 文件级互斥锁（AsyncFileLock 可用） | 3.16.x | ~30M | MIT | ✅ | **可选引入**（若不想手写 fcntl/msvcrt 跨平台兼容） |
| `portalocker` | 跨平台文件锁 | 2.x | ~5M | BSD | ✅ | **不引入**（filelock 已覆盖且有 asyncio 支持）|
| `markdown-it-py` | USER.md section 切片（heading 级解析） | 3.x | ~50M | MIT | ✅ | **暂不引入**（append-only 方案不需要 parser；待 section upsert 扩展时再评估）|
| `mistune` | Markdown 解析（速度更快，非 CommonMark） | 3.x | ~20M | BSD | ✅ | **不引入**（非 CommonMark 合规，边界案例处理差）|
| ProviderRouter（内部） | utility model 调用（Threat Scanner smart mode + sub-agent LLM 调用） | N/A | N/A | N/A | ✅ | **直接复用**（Feature 080/081 已实现，无新依赖）|

### 3.2 关于 filelock 引入决策

Hermes 已用标准库 `fcntl`（Unix）+ `msvcrt`（Windows）+ `os.replace()` atomic rename 实现完整的 read-modify-write 安全，代码约 40 行（`MemoryStore._file_lock`）。OctoAgent 目标平台为 macOS/Linux，不需要 Windows 兼容，因此：

- **推荐做法**：直接移植 Hermes 的 `fcntl.flock(LOCK_EX)` + atomic rename 模式，零新依赖。
- **如果需要跨平台**：引入 `filelock`（MIT，asyncio 支持好，`AsyncFileLock` 可在 aiosqlite 同线程使用）。
- **不引入 portalocker**：portalocker 无原生 asyncio 支持。

### 3.3 推荐依赖集

**核心依赖（已稳定，直接复用）**：
- `pydantic` / `pydantic-ai`：ToolEntry 模型、MemoryStore 数据模型
- `aiosqlite`：SQLite WAL，Event Store 写入
- `APScheduler`：cron jobs（保持现有用法，observation routine 不经此路径）
- `structlog` + `logfire`：observation routine 的 memory_write_completed 结构化日志
- `fcntl`（标准库）：SnapshotStore 文件锁

**新增依赖（极简原则，MVP 阶段）**：
- 无强制新增。所有核心功能用标准库 + 现有依赖实现。

**可选/延迟依赖**：
- `markdown-it-py`：section upsert 扩展时引入（MIT，CommonMark 合规）
- `filelock`：Windows 兼容需求出现时引入

### 3.4 与现有项目的兼容性

| 现有依赖 | 兼容性 | 说明 |
|---------|--------|------|
| pydantic v2 | ✅ 兼容 | ToolEntry、MemoryEntry 均为标准 Pydantic BaseModel |
| pydantic-ai | ✅ 兼容 | Sub-agent LLM 调用可通过 ProviderRouter 而非 pydantic-ai 框架层，避免工具管理冲突 |
| aiosqlite | ✅ 兼容 | Event Store 写入无变化；SnapshotStore 不使用 SQLite（方案 A 选择）|
| APScheduler | ✅ 兼容 | 保留现有 cron 用途；observation routine 用 asyncio.Task，不引入新 scheduler |
| structlog | ✅ 兼容 | observation routine 写 `memory_write_completed` 等结构化日志 |
| logfire | ✅ 兼容 | OTel trace 可覆盖 observation routine 的 span |

---

## 4. 设计模式推荐

### 4.1 Snapshot Pattern（冻结系统提示 + Live Diff）

**适用场景**：SnapshotStore，USER.md/MEMORY.md 注入系统提示。

**实现要点**（参考 Hermes `MemoryStore`）：
- `_system_prompt_snapshot: dict[str, str]`：启动时冻结，整个 session 不变。
- `format_for_system_prompt()` 始终返回冻结副本，工具写入只改 `memory_entries`（live）和磁盘文件。
- 下次 session 的 `load_from_disk()` 重新冻结最新状态，形成自然 session 边界。
- **注意**：冻结粒度是 session 级，不是请求级。同一 session 内多 turn 的 prefix cache 100% 命中。

**对 OctoAgent 的适配**：session 对应 `session_id`（对话 session）；bootstrap session 和 regular session 可以有不同的快照版本（bootstrap 时 USER.md 为空，regular session 时已有内容）。

### 4.2 Registry Pattern（自描述模块自动注册）

**适用场景**：Tool Registry，builtin_tools 模块发现。

**实现要点**（参考 Hermes `ToolRegistry`）：
- 模块级 `registry.register()` 调用，在 import 时自动注册。
- AST 扫描只检查 module-body 的顶层 statement，避免将工具函数内部调用误判为自注册模块。
- `threading.RLock` 保护 dict mutation，reader 通过 `_snapshot_entries()` 获得 list 副本（无锁读）。
- `deregister()` 级联清理 toolset_checks 和 aliases（MCP 热更新场景）。

**与 ToolBroker 集成**：ToolBroker 保持为外层契约层（schema 反射、side_effect 分级、policy 决策）；ToolRegistry 作为"工具来源"，ToolBroker.execute() 调用 ToolRegistry.dispatch()。

### 4.3 Pipeline Pattern（observation routine 多 Stage）

**适用场景**：observation routine，extract → dedupe → categorize → promote 四阶段。

**实现要点**：
- 每个 stage 是独立的 async 函数，接受前一 stage 的输出。
- categorize stage 调用 ProviderRouter（utility model，轻量 LLM 调用）打标签（category, confidence_score）。
- promote stage 检查 confidence_score 阈值，低于阈值加入 candidates queue，高于阈值直接触发写入（通过 ToolBroker + PolicyGate）。
- 整个 pipeline 在 asyncio.Task 中运行，可通过 `cancel()` 优雅停止。
- 每个 stage 完成时写 `AUTOMATION_STAGE_COMPLETED` 事件（Constitution C2）。

**参考案例**：Hermes cron scheduler 的 `_build_job_prompt()` → `run_job()` → `_deliver_result()` 三段流水线，OctoAgent 的 GraphPipelineTool DAG 执行模式。

### 4.4 Approval Workflow（two-phase dangerous action gate）

**适用场景**：USER.md 写入（constitution C4 Two-Phase），高置信度候选自动写入，低置信度需用户 approve。

**实现要点**（参考 Hermes `approval.py`）：
- 检测阶段（`detect_dangerous_command()` 类比）：Threat Scanner 扫描候选内容。
- 审批阶段：通过 OctoAgent 现有 ApprovalManager + PolicyGate，session 级 allowlist。
- `smart_approve` 变体：高置信度（≥0.85）内容直接走 ToolBroker + fast-path 写入，低置信度进入 candidates queue 等待用户 review。
- 不可逆操作（replace/remove）必须二段式，预览 diff → 用户确认 → 执行。

---

## 5. 技术风险清单

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---------|------|------|---------|
| R1 | **Snapshot mtime race**：外部进程改 USER.md 时，_system_prompt_snapshot 不感知。session 中途信息不一致（LLM 看到旧内容但磁盘已更新） | 低（单用户，外部写入少） | 中（下次 session 才生效，不影响当前 session 稳定性） | 文档说明"修改 USER.md 需重开 session 生效"；或在 session 结束时 diff 磁盘 vs snapshot，日志记录漂移 |
| R2 | **Threat Scanner 误杀**：正则 false positive 阻塞合法内容（如 "you are now an expert" 被 `you are now` pattern 命中） | 中（正则语义粗糙） | 高（用户写 memory 被拒，体验破坏） | 细化 pattern 边界（加词边界 `\b`、上下文锚点）；引入 `smart_scan` 升级路径（utility model 仲裁 false positive）；blocked 内容给用户展示具体 pattern_id，便于调试 |
| R3 | **删除 ~2500 行 F082 代码遗漏依赖（silent break）** | 高（大范围删除必有漏） | 高（运行时 ImportError 或功能静默失效） | 删除前用 `grep -r "from bootstrap_orchestrator"` 等工具全量扫描引用；分步删除（先置废弃注释 + 测试通过，再删除）；CI 全量测试覆盖 |
| R4 | **observation routine + 现有 cron 任务冲突**：两者同时调度，争抢 LLM 调用窗口，互相 delay | 中（特别是高频 routine + 密集 cron 场景） | 中（延迟增加，但不崩溃） | observation routine 使用独立 asyncio.Task，不经 APScheduler；为 ProviderRouter 调用增加 rate limit / concurrency limit（信号量）；cron 和 routine 使用不同 model alias（cheap vs utility）|
| R5 | **Sub-agent token / latency 翻倍**：categorize stage 每次调 utility model，observation routine 频繁触发时成本线性增长 | 中（取决于 routine 频率和 LLM cost） | 中（成本可见，但需要用户感知） | 设置 categorize stage 的 token budget（max_tokens=200）；observation routine interval 默认 30min（非实时）；batch candidates（一次 LLM 调用处理 N 条候选，而非 N 次调用）；Logfire 追踪 token 消耗，超预算触发 alert |
| R6 | **UI promote 流程没人 review，candidates 堆积**：observation routine 持续提取候选，但用户长时间不打开 UI review | 高（单用户 AI OS 的典型场景） | 中（候选队列无限增长；低置信候选永不落地） | 设置 candidates queue 上限（如 50 条），超上限停止提取直到 review；高置信候选（≥0.85）经 smart_approve 自动写入，不等用户 review；Telegram 定期推送"待确认 memory 候选"摘要 |
| R7 | **Pydantic OwnerProfile 表 vs USER.md 文件双写一致性**：F082 写入 OwnerProfile（SQLite），F084 写入 USER.md（文件），两者数据可能漂移 | 高（F084 重构 F082 但用户已有 OwnerProfile 数据）| 高（用户数据丢失 or 重复，bootstrap 路径错误）| 明确 USER.md 为权威来源（SoR）；提供一次性迁移脚本（OwnerProfile → USER.md）；OwnerProfile 表保留为 backup / quick-read cache，以 USER.md 为准，读时优先读 USER.md |
| R8 | **Tool Registry AST 扫描首次启动延迟**：builtin_tools 目录工具模块数量增加时，AST parse + import 时间可能超过 1s | 低（当前约 47 个工具，扫描约 100-300ms） | 低（启动时一次，不影响运行期）| 设置 AST 扫描超时（5s）；添加 import 失败 warning 而非 error；如延迟严重，可缓存 `_module_registers_tools()` 结果（文件 mtime 作为 cache key） |
| R9 | **F082 已写入 USER.md 的用户重装路径错误**：用户卸载重装后，F084 的 bootstrap 逻辑误判为全新用户，覆盖已有 USER.md | 中（重装场景；~/.octoagent 保留时不触发） | 高（用户数据丢失）| bootstrap 逻辑必须检查 USER.md 是否已存在且非空，存在则跳过初始化写入；`octo migrate` 提供"检查并恢复"命令；备份操作前先 snapshot 当前状态 |

---

## 6. 需求-技术对齐度评估

### 6.1 Must-have 覆盖评估

| MVP 功能 | 技术方案覆盖 | 说明 |
|---------|-------------|------|
| Snapshot Store（prefix cache 保护） | ✅ 完全覆盖 | 方案 A（内存冻结 + atomic rename）直接实现，零新依赖 |
| Snapshot Store（多进程写安全） | ✅ 完全覆盖 | fcntl.flock(LOCK_EX) + 单 user 场景，竞争极低 |
| Tool Registry（AST 发现） | ✅ 完全覆盖 | Hermes ToolRegistry 可直接移植，约 450 行 |
| Tool Registry（ToolEntry + dispatch） | ✅ 完全覆盖 | ToolEntry.__slots__ + ToolRegistry.dispatch() |
| Threat Scanner（正则 pattern） | ✅ 完全覆盖 | Hermes `_MEMORY_THREAT_PATTERNS` + `_INVISIBLE_CHARS` |
| Routine Scheduler（asyncio.Task） | ✅ 完全覆盖 | 轻量 asyncio.sleep loop，不增加依赖 |
| Sub-agent delegation（LLM 直调）| ✅ 完全覆盖 | ProviderRouter 直调，categorize stage |
| Sub-agent delegation（写入走 PolicyGate）| ✅ 完全覆盖 | 复用 ToolBroker + ApprovalManager |
| USER.md 合并策略（append-only + § 分隔符）| ✅ 完全覆盖 | Hermes add/replace/remove 三操作模型 |

### 6.2 Nice-to-have 扩展性评估

| Nice-to-have 功能 | 技术方案是否支持扩展 | 说明 |
|------------------|---------------------|------|
| SnapshotStore 跨进程持久化恢复 | ✅ 可扩展 | 方案 A 文件即持久化，进程重启自动恢复；如需更强一致性，可加 SQLite snapshot 表 |
| Tool Registry 热更新 | ✅ 可扩展 | `deregister(name)` + re-import 即可；MCP 动态工具场景已在 Hermes 验证 |
| Threat Scanner smart approval | ✅ 可扩展 | utility model + ProviderRouter 接入点已预留；pattern 命中后 escalate 路径需开发 |
| observation routine promote 工作流 | ✅ 可扩展 | candidates queue + Telegram 推送；UI 侧 candidates review 页面需前端支持 |
| UI candidate queue 展示 | ⚠️ 需新 API | 需新增 `GET /api/memory/candidates` 端点；前端 candidates 组件需新开发 |
| acp 编辑器集成（未来） | ⚠️ 需 section upsert | append-only 方案不支持 acp 编辑器的 cursor-aware 写入；届时引入 markdown-it-py |
| 多 agent 协作（未来） | ⚠️ 需重新评估 | 多 agent 共享 USER.md 时，fcntl.flock 单机锁不够，需分布式锁 |

### 6.3 Constitution 约束检查

| Constitution 条款 | 兼容性 | 说明 |
|------------------|--------|------|
| C1 Durability First | ✅ 兼容 | SnapshotStore atomic rename + fsync 保证写入持久；observation routine 的候选队列持久化到 SQLite（Event Store）|
| C2 Everything is an Event | ✅ 兼容 | memory_write / observation_stage / threat_blocked 均需写事件；需显式实现 `MEMORY_ENTRY_ADDED` / `MEMORY_ENTRY_BLOCKED` 事件类型 |
| C3 Tools are Contracts | ✅ 兼容 | ToolRegistry ToolEntry.schema 与 handler 签名一一对应；AST 扫描只 import 有顶层 register() 调用的模块，防止工具注册漂移 |
| C4 Side-effect Two-Phase | ✅ 兼容 | USER.md 写入：observation routine 提 candidate（Plan）→ user approve（Gate）→ ToolBroker 写入（Execute）；replace/remove 操作必须 two-phase |
| C5 Least Privilege | ✅ 兼容 | Threat Scanner 扫描 memory 内容防止 exfil；SnapshotStore 不把 secrets 注入系统提示（user_char_limit 硬限制）；路径访问走 PathAccessPolicy |
| C6 Degrade Gracefully | ✅ 兼容 | utility model 不可用时，observation routine 跳过 categorize stage，候选全部进 review queue；Threat Scanner 纯正则不依赖网络 |
| C7 User-in-Control | ✅ 兼容 | 低置信度候选进 review queue，用户主动 approve/reject；promote 流程有 Telegram/Web approve UI |
| C8 Observability is a Feature | ✅ 兼容 | observation routine 每个 stage 写结构化日志（structlog）；Logfire span 覆盖 pipeline；memory_write_completed 事件含 candidate→delivered 漏斗指标 |
| C9 Agent Autonomy | ✅ 兼容 | Threat Scanner 用 pattern 规则（非 LLM 决策），但 memory 写入工具的使用由 LLM 自主决策；pattern 只是防注入护栏，不替代 LLM 判断 |
| C10 Policy-Driven Access | ✅ 兼容 | 所有 memory 写入经 ToolBroker → PolicyGate → ApprovalManager 统一入口；Threat Scanner 不在工具层做路径拦截，扫描结果返回给 PolicyGate 决策 |

**主要对齐缺口**：
- C2（Everything is an Event）：需要明确定义新事件类型（`MEMORY_ENTRY_ADDED`、`MEMORY_ENTRY_BLOCKED`、`OBSERVATION_STAGE_COMPLETED`、`MEMORY_CANDIDATE_QUEUED`），并在 Event Store schema 中补充。
- 删除 F082 的 ~2500 行代码时需逐一检查 Constitution C4 覆盖，确保所有 side-effect path 都有 two-phase 保护（风险 R3）。

---

## 7. 结论与建议

### 7.1 总结

F084 的技术方向与 Hermes 高度对齐，主要设计决策已有生产验证的参考实现：

**已确认的核心选型**：
- **SnapshotStore**：内存 dict 冻结（Hermes 风格）+ atomic rename + fcntl.flock，零新依赖。
- **Tool Registry**：AST 扫描 + module-level register()，Hermes ToolRegistry 可直接移植约 450 行。
- **Threat Scanner**：正则 pattern table + invisible unicode 检测，Hermes `_MEMORY_THREAT_PATTERNS` 直接复用（约 15 条 pattern 扩展）。
- **Routine Scheduler**：asyncio.Task 轻量调度，不引入 APScheduler，与现有 cron 完全隔离。
- **Sub-agent**：分层策略——读操作（categorize）用 ProviderRouter 直调，写操作（USER.md 写入）走 ToolBroker + PolicyGate。
- **USER.md 合并**：append-only § 分隔符（Hermes 模型），add/replace/remove 三操作，不引入 Markdown parser。

**最大技术风险**：R3（F082 代码删除遗漏依赖）和 R7（OwnerProfile vs USER.md 双写一致性）是最高优先级的防线性工作，应在实现前完成依赖扫描和迁移路径设计。

**无需引入的新依赖**：filelock、portalocker、markdown-it-py、mistune 均可不引入（MVP 阶段）。

### 7.2 对后续技术规划的建议

1. **先做迁移脚本（优先级最高）**：F082 → F084 的 OwnerProfile 数据迁移到 USER.md，需在实现 SnapshotStore 前完成，避免 R7 风险。
2. **Tool Registry 与 ToolBroker 的接口契约需在 spec 中明确**：ToolRegistry.dispatch() 的调用方式（sync/async bridge）需与现有 ToolBroker.execute() 对齐，避免 asyncio 上下文混用问题。
3. **新事件类型 schema 需提前定义**：C2 合规要求在 Event Store schema 中预先定义 `MEMORY_*` 事件类型，避免后期 migration。
4. **Threat Scanner 的 pattern_id 应纳入 PolicyGate 的 blocked_reasons 结构**：与 F079 已实现的 `blocking_reasons` 结构化机制对齐（见 CLAUDE.md Feature 079 Phase 4），统一 pattern 命中和 policy 决策的日志格式。

---

*注：参考来源包括 `_references/opensource/hermes-agent/tools/registry.py`、`tools/memory_tool.py`、`tools/approval.py`、`cron/scheduler.py`（均已本地读取分析）；OctoAgent `docs/blueprint/architecture-audit.md`、`docs/blueprint/milestones.md`。Web 搜索参考：[markdown-it-py](https://pypi.org/project/markdown-it-py/0.1.0/)、[filelock](https://py-filelock.readthedocs.io/)、[portalocker](https://github.com/wolph/portalocker)。*
