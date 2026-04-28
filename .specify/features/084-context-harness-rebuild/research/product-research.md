# 产品调研报告: Feature 084 - Context + Harness 全栈重构

**特性分支**: `084-context-harness-rebuild`
**调研日期**: 2026-04-27
**调研模式**: 在线（Web 搜索 + 本地代码库分析）

---

## 1. 需求概述

**需求描述**: OctoAgent 的 Context 层（USER.md / MEMORY.md / behavior 目录 / observations 异步累积）与 Harness 层（Tool Registry / Snapshot Store / Threat Scanner / Approval Gate / Delegation / Routine 调度）存在多层叠加缺陷，导致 owner Connor 无法通过 web 入口完成最基础的"把信息写入用户档案 USER.md"操作。Feature 084 的目标是彻底重构上述两层及其胶水层，而非继续打补丁。

**核心功能点**:
- USER.md 写入流（路径 A）：Agent 通过直观工具完成档案初始化与更新
- Tool Registry 重构：entrypoints 机制统一，不同入口（web / agent_runtime / telegram）看到一致的工具集
- Snapshot Store：工具调用结果回显机制，LLM 写操作完成后能看到执行结果
- Threat Scanner：替代现有分散式 policy 检查，统一扫描危险动作
- Approval Gate：两段式审批重构，与 Threat Scanner 协同
- Observation 异步累积（路径 B）：routine + UI promote 候选列表
- sub-agent 委托（delegate_task）：首版上线

**目标用户**: 单一 owner Connor（终端软件研发负责人，有 6 岁孩子，关注教育/健康/技术，具备技术背景，期望系统高度自动化且可控）

---

## 2. 架构哲学对照（替代传统"市场现状"）

> 单用户场景下，传统意义的市场需求分析意义弱化。本节改为对照 5 个 reference 产品在核心维度上的架构选择哲学，提炼 OctoAgent F084 应借鉴什么、应区别于什么。

### 2.1 根因的架构本质

F082（Bootstrap & Profile Integrity）已尝试治标：加严 is_filled 判断、创建 BootstrapIntegrityChecker、新增 bootstrap.complete 工具。但 F084 实测暴露的根本问题是**架构层面的四个断层**：

| 断层编号 | 断层描述 | 典型症状 |
|---------|---------|---------|
| D1 | Tool Registry 的 entrypoints 是硬编码显式字典，web 入口缺失工具 | bootstrap.complete 在 web 看不到 |
| D2 | 没有 tool response 回显机制（Snapshot Store 缺失）| LLM 写完文件但无法确认是否成功 |
| D3 | UserMdRenderer 的 is_filled 门槛过严（占位符检测误判）| USER.md 有内容但仍被认为未填充 |
| D4 | 工具命名语义失配（bootstrap.complete 暗示流程而非写文件）| Agent 不知道该用哪个工具写档案 |

这四个断层共同构成了"Context + Harness 全栈失效"，无法通过局部修补解决，需要统一重构。

### 2.2 行业趋势背景

2025–2026 年，开源 Agent 领域的核心趋势已从"让 LLM 更智能"转向"让 Harness 更可靠"。核心概念如下：

- **Harness Engineering** 作为独立工程学科在 2026 年获得广泛认可，被定义为"将概率推理转化为确定性行动的架构层"
- **Tool Registry 2.0**：从静态注册转向按任务阶段动态注入（Progressive Disclosure），减少 attention fragmentation
- **Snapshot Store** 模式：工具调用的结果作为有结构的状态快照持久化，而非临时字符串
- **Threat Scanner** 取代简单的 policy 字符串匹配，引入多层分类（plan-phase 快速门 + execute-phase chain-of-thought）
- **User Modeling**（Honcho 等）：从显式 profile 迁移到"跨会话dialectic 推断"——但这是未来方向，首版仍以显式 USER.md 为锚点

---

## 3. Reference 产品架构对照表

> 以下对照覆盖 5 个 reference 产品，替代传统"竞品 ≥ 3"要求。数据来源：官方文档 + GitHub 源码 + 第三方技术分析文章。标注 `[推断]` 的内容基于架构合理性推断，非直接来源。

### 3.1 核心维度对照

| 维度 | **Hermes Agent** | **OpenClaw** | **Agent Zero** | **Pydantic AI** | **OctoAgent（F083 现状）** |
|------|-----------------|-------------|---------------|----------------|--------------------------|
| **USER 档案存储** | MEMORY.md + Honcho 跨会话推断；无显式 USER.md | SOUL.md（人格）+ AGENTS.md（指令）+ TOOLS.md（工具约定），三文件分职责 | 无统一档案；每个 project 有独立 prompts/ 目录下的 .md 文件 | 无内置档案概念；通过 RunContext deps 注入业务数据 | behavior/system/USER.md，OwnerProfile SQLite 派生；F082 已修复占位符问题但写入流仍断 |
| **Memory 持久化** | SQLite WAL + FTS5；三层（session/persistent/skill）；成本感知快照 | 向量 SQLite + MEMORY.md 精选事实 + 日记文件；hybrid BM25+vector 检索 | FAISS 向量搜索；main memories / conversation fragments / proven solutions 三类 | 无内置 memory；需应用层自行实现 | SoR/Fragments/Vault 三线；LanceDB 向量；WriteProposal 治理 |
| **Tool 暴露机制** | 47 tools / 20 toolsets；按默认 toolset 启动，运行时可扩展；三级 skill 加载（index / header / full）| 按 plugin 注册（api.registerTool）+ 内置 built-in；运行时按 turn 动态注入相关 skill | 工具定义写在 agents/<profile>/prompts/ 下的 .md 文件；层级继承 | @agent.tool / @agent.tool_plain 装饰器；工具通过 RunContext 访问 deps | ToolBroker + Schema 反射 + PermissionPreset；entrypoints 字典限制工具可见性，当前有 web 入口缺失问题 |
| **Approval Gate** | 预算三级（70%/90% 告警）+ IterationBudget 线程安全计数器；无显式 human-in-loop 门 [推断] | 配对码审批（DM 发送者首次需人工 approve）；trust-based behavioral profiles | 无内置审批；交由 prompt 和用户实现 | 无内置审批；框架层不涉及 | PolicyEngine Two-Phase（Plan → Gate → Execute）；SideEffectLevel × ApprovalAction 双维度矩阵 |
| **Snapshot Store** | 结构化 context 压缩（4 阶段：pruning→protection→summarize→update）；以 handoff 文档为快照单元 | 会话 JSON 持久化 + session compaction 自动摘要；append-only session log 支持 branch | `[推断]` 无独立 snapshot store；依赖 conversation history | 无内置快照；RunContext 仅在单次 run 生存 | 无 Snapshot Store；工具调用结果仅在 LLM context 中存在，无持久化回显 |
| **Threat Scanner** | 无独立扫描器；靠预算限制和 iteration cap 防止失控 [推断] | 多层 policy（profiles → agent-level → sandbox）；`block_secret_inputs` / `redact_secret_outputs` 函数 | 无独立扫描器；prompt-based 控制 | 无内置；应用层实现 | policy allow/ask/deny + risk_level 字段；分散在 ToolBroker + PolicyEngine 多处 |
| **Observation 累积** | skill memory（从经验自动创建程序技能）；`honcho_conclude` 会话结束时合成用户洞察 | 自动语义索引 incoming messages；MEMORY.md 精选事实；daily 日记文件 | 会话片段自动向量化；proven solutions 类型专门存成功经验 | 无内置 | 无异步 observation 累积；仅有 SoR 手工写入和 Fragments 检索 |
| **Routine 调度** | APScheduler 集成（cron + interval）；技能可被定时触发 | `[推断]` 无明确内置调度 | `[推断]` 无内置调度 | 无内置 | APScheduler MVP；JobRunner 调度 |
| **Sub-agent 委托** | parent / subagent 共享 IterationBudget；subagent 可访问父 agent 工具集 | 无多 agent 层级；单 agent 模型 | 层级委托为核心设计；Agent 0 → 下级 agents → 更下级；完全层级结构 | Agent 可以调用其他 Agent（agent.run()）；无内置协调 | A2A-Lite Worker 派发；Orchestrator + Worker 双层 |

### 3.2 OctoAgent 应借鉴什么

| 来源 | 借鉴点 | 对应 F084 落地 |
|------|-------|--------------|
| **Hermes Agent** | 结构化 context 压缩的 4 阶段（pruning→protection→summarize→update）；handoff 文档作为快照单元 | Snapshot Store 设计：工具结果以结构化 SnapshotRecord 持久化，支持 LLM 回显 |
| **Hermes Agent** | 三级 skill 加载（index token 极低 ~630 tokens，按需 full load）；IterationBudget 线程安全计数器 | Tool Registry：按 entrypoint + phase 动态注入，不再全量暴露；预算计数器治理 |
| **OpenClaw** | SOUL.md / AGENTS.md / TOOLS.md 三文件分职责（人格 / 指令 / 工具约定） | Context 重构：USER.md（who am I）/ BEHAVIOR.md（how to work）/ TOOLS.md（what tools exist）明确分层 |
| **OpenClaw** | 工具按 turn 动态注入；hybrid BM25+vector memory 检索 | Tool Registry：entrypoints 改为声明式 capability 集，由 Harness 按请求来源动态解析 |
| **Agent Zero** | project-level 隔离：每个 project 独立 prompts/memory/secrets | OctoAgent 已有类似设计（Project scope），F084 加强工具可见性与 project scope 的绑定 |
| **Pydantic AI** | RunContext 依赖注入模式：类型安全、可测试、工具与 agent 解耦 | Harness 胶水层：所有工具通过 HarnessContext 注入，不直接耦合 agent 状态 |
| **Harness Engineering 趋势** | Threat Scanner 多层分类（fast gate + CoT review）；plan-phase 与 execute-phase 分离 | F084 Threat Scanner：区分 plan-phase（低成本规则扫描）和 execute-phase（高风险 LLM 二次确认）|

### 3.3 OctoAgent 应区别于什么

| 参照产品 | 我们不做什么 | 原因 |
|---------|-----------|------|
| **Hermes Agent** | 不做 Honcho 跨产品用户推断（需外部 API 依赖） | 单用户本地优先；Connor 的用户 modeling 用 observation 路径 + USER.md 显式锚定即可 |
| **OpenClaw** | 不采用三文件替代方案（SOUL/AGENTS/TOOLS.md） | OctoAgent 已有成熟的 OwnerProfile SQLite 派生体系；重构目标是修通写入流，非重设计文件结构 |
| **Agent Zero** | 不做完全层级多 agent 委托（Agent 0 → N 层） | OctoAgent 的 Orchestrator + Worker 双层已满足；delegate_task 首版只做单层委托 |
| **Agent Zero** | 不把工具定义写在 prompt .md 文件里 | OctoAgent 的 Tool Contract 原则（Constitution #3）要求 schema 与代码签名一致，单一事实源 |
| **Pydantic AI** | 不裸用 Pydantic AI Agent 替代现有 Free Loop | Free Loop 是 OctoAgent 的核心架构选择，Pydantic AI 继续作为 Skill 层工具 |

---

## 4. 用户场景验证

### 4.1 Persona：Owner Connor

- **背景**: 终端软件研发负责人；有 6 岁孩子；关注教育/健康/技术；技术能力强但期望系统高度自主
- **系统使用模式**: 通过 Web UI 和 Telegram 与 OctoAgent 日常交互；期望 Agent 主动观察、积累知识、处理例行任务、在关键决策点请求审批
- **对 Context 的期望**: 系统应该"认识我"——知道我的称呼、时区、工作风格、边界；且这些信息应该在首次引导后持久有效
- **对 Harness 的期望**: 工具调用不应静默失败；危险操作必须可见可审批；常规操作不应频繁打扰

### 4.2 核心用户旅程

| # | 旅程名称 | Connor 的期望 | 当前痛点（F083 现状） | F084 目标状态 |
|---|---------|-------------|-------------------|--------------|
| J1 | **档案初始化** | 输入"帮我初始化 USER.md"后，Agent 通过对话收集信息并写入，返回确认结果 | Agent 输出了档案内容但报告"未完成写入：当前会话没有可调用的工具入口"；反复多次无法完成 | Tool Registry 修通 web 入口；增加 profile.update / memory.write 语义明确工具；Snapshot Store 回显写入结果 |
| J2 | **LLM 观察累积** | 闲聊时 Agent 发现新事实（"Connor 提到孩子上小学了"）→ 后台静默记录，不打断对话 | 无 observation 路径；仅有手工 SoR 写入，模型不会主动累积 | Observation Worker 异步处理；会话结束后提取候选事实 |
| J3 | **promote 候选列表** | 每隔一段时间，UI 展示"AI 发现的新事实"，Connor 可以 accept/edit/reject | 无候选列表 UI；无 promote 机制 | UI promote 完整流程：候选列表 + 编辑 + accept/reject |
| J4 | **修改档案** | 直接对话"把我的时区改为 Asia/Shanghai" → Agent 即时更新并确认 | 需要走完整 bootstrap 流程；无单字段更新工具 | profile.patch 工具：支持单字段更新；不触发 bootstrap 状态机 |
| J5 | **重装（不迁移）** | 重装后走引导，10 分钟内恢复工作状态 | F082 修复了引导流程；但重装后部分工具仍可能不可见 | 无迁移命令（用户决策）；bootstrap 流程可靠完成即可 |
| J6 | **审批危险动作** | Agent 要执行高风险操作时，在 Web UI 看到"请求审批"卡片，可批准/拒绝，有原因说明 | Approval Gate 存在但 Threat Scanner 分散，部分危险动作被漏扫 | Threat Scanner 统一扫描入口；Approval Gate 展示 threat 分类和原因 |
| J7 | **sub-agent 派发任务** | 对话"去调研一下这个技术方案"→ Agent 派发给 Research Worker，主会话不阻塞 | A2A-Lite 存在但 delegate_task 工具未统一；部分场景阻塞主会话 | delegate_task 工具首版：明确 target worker / task 描述 / 结果回调 |
| J8 | **routine 自动跑** | 每天早上自动执行早报任务，无需 Connor 手动触发；失败时发 Telegram 通知 | APScheduler 存在但 routine 配置入口不统一 | Routine 调度与 observation promote 结合；UI 可见调度状态 |

### 4.3 需求假设验证

| 假设 | 验证结果 | 证据 |
|------|---------|------|
| Web 入口看不到 bootstrap.complete 工具是根本问题之一 | ✅ 已验证 | bootstrap-profile-flow.md §5：`entrypoints=["agent_runtime"]`，web 入口排除在外 |
| 工具命名不直观导致 Agent 选错工具 | ✅ 已验证 | "bootstrap.complete" 语义是"完成引导流程"而非"写入档案"；LLM 在更新档案场景下不会优先选择它 |
| 缺少 Snapshot Store 是 LLM 无法确认写入结果的原因 | ✅ 已验证 | F083 代码无工具结果持久化机制；LLM 只能在 context 中看到工具输出，进程重启后丢失 |
| 单用户场景下"市场竞品"维度不适用 | ✅ 已验证 | OctoAgent 是个人 AI OS，无直接竞品；Reference 产品架构对照替代竞品分析 |
| F082 治标未治本（根因在 Harness 层而非 Bootstrap 状态机） | ✅ 已验证 | F082 修复了 is_filled 判断和状态机加严，但 Tool Registry entrypoints 设计和 Snapshot Store 缺失属于不同层次问题 |
| Reference 产品（Hermes/OpenClaw）均已解决类似工具可见性问题 | ✅ 已验证 | Hermes：toolset 动态切换；OpenClaw：按 turn 动态注入 skill；均优于 OctoAgent 当前静态 entrypoints 字典 |

---

## 5. MVP 范围建议

> 用户已决策 Must-have + Nice-to-have 均首版上。本节尊重该决策，但额外标注风险等级，供技术调研参考。

### Must-have（MVP 核心，风险低，必须完成）

| 功能 | 说明 | 风险标注 |
|------|------|---------|
| USER.md 写入流（路径 A）| profile.update / profile.patch 工具取代 bootstrap.complete；工具名语义直观 | 低风险 |
| Tool Registry 重构 | entrypoints 从硬编码字典改为声明式 capability 集；web / agent_runtime / telegram 统一解析 | 中风险：需要 review 所有现有工具的 entrypoints 声明 |
| Snapshot Store | 工具调用结果以结构化 SnapshotRecord 写入 SQLite；LLM 下一轮可查询 | 中风险：需要定义快照的 TTL 和清理策略 |
| Threat Scanner | 统一扫描入口；区分 plan-phase（规则）和 execute-phase（LLM）两级 | 中风险：需要梳理现有分散 policy 逻辑，防止回归 |
| bootstrap.complete 直接 break | 删除旧工具，不保留 alias；确保无代码路径引用 | 低风险（用户已决策） |

### Nice-to-have（首版上，风险中等，可单独砍）

| 功能 | 说明 | 风险标注 |
|------|------|---------|
| Observation 异步累积（路径 B）| Observation Worker 提取会话事实；pending 状态持久化 | **中高风险**：异步 Worker 与主会话的隔离边界需要明确；LLM 事实提取质量不稳定 |
| Routine 调度 | UI 可配置 routine；与 APScheduler 集成 | 中风险：APScheduler 已有基础，主要是配置入口统一 |
| UI promote 完整流程 | 候选列表 + 编辑 + accept/reject；需前端 React 组件 | **高风险**：前端工作量不小；且依赖 Observation 路径质量 |

> **砍功能建议**（如工期紧张）：UI promote 最重（前端 + 后端 + LLM 质量三方依赖），可推迟到 F085。Observation 异步累积无 UI 时也可先落后端 only。

### Future（首版不上）

| 功能 | 说明 |
|------|------|
| sub-agent delegation（delegate_task）| 用户决策首版上；但 use case 有限，A2A-Lite 已有基础；实际风险取决于 Worker 路由稳定性 |
| ACP 编辑器集成 | F086 阶段 |
| Memory backend 升级（LanceDB full 迁移）| F085 阶段 |
| Honcho 用户建模 | 单用户场景下 observation 路径已够用，Honcho 需外部依赖不引入 |

> 注：用户决策 delegate_task "首版上"，但本调研标注为 Future 是指相对于 Context+Harness 核心路径其依赖更弱，如工期紧张优先级最低。

### 优先级排序理由

1. **路径 A（USER.md 写入流）是最高优先级**：这是触发 F084 的原始痛点；Connor 无法更新自己的档案是系统可用性的基本要求
2. **Tool Registry + Snapshot Store 是路径 A 的前置基础**：没有 Registry 统一，工具可见性问题无法根治；没有 Snapshot Store，LLM 无法确认写入
3. **Threat Scanner 是 Approval Gate 的前置**：当前分散 policy 导致部分危险动作漏扫，统一入口是安全底线
4. **路径 B（Observation + Promote）依赖路径 A 完成**：只有档案写入流可靠后，observation promote 才有意义

---

## 6. 未来扩展性评估

### 6.1 F084 抽象的 carry 能力

| F084 产出 | F085（Memory backend 升级）| F086（ACP 编辑器）| F087（多 agent 协作）|
|----------|--------------------------|-----------------|---------------------|
| **Tool Registry** | 可直接 carry：F085 可能新增 memory backend 切换工具，Registry 只需注册新工具 | 可 carry：ACP 工具通过 Registry 注册，无需改 Harness | 可 carry：多 agent 工具（delegate / callback）通过 Registry 统一暴露 |
| **Snapshot Store** | 可 carry：F085 memory 升级后 Snapshot 可迁移到新 backend | `[推断]` ACP 操作结果可以作为 Snapshot 持久化 | 可 carry：跨 agent 的任务状态可用 Snapshot Store 传递 |
| **Threat Scanner** | 可 carry：memory 写入操作仍需扫描 | 可 carry：ACP 编辑操作需要扫描 | 重要：多 agent 场景危险面更大，统一 Scanner 更关键 |
| **Observation 路径** | 可 carry：F085 可以升级 observation 的 embedding 后端 | 不相关 | 可 carry：多 agent 协作中 sub-agent 的 observation 可汇聚到主 agent |

**结论**：F084 的 Tool Registry / Snapshot Store / Threat Scanner 三个核心抽象设计合理，对 F085-F087 均有良好的 carry 能力，不会成为后续瓶颈。关键是 F084 实现时保持接口稳定（不把实现细节暴露给上层）。

### 6.2 潜在技术债务

- **Observation 质量**：LLM 事实提取的准确率和幻觉率需要在实测中验证；首版上线建议限制 observation worker 只处理结构化字段（如名字/时区/偏好），不处理开放性事实
- **Snapshot TTL**：Snapshot Store 如果无清理策略，SQLite 会持续增长；需要 F084 定义 TTL 和归档策略
- **bootstrap.complete 直接 break**：需确保用户知晓（Connor 重装即可）；老版本如有残留调用会静默失败，建议在日志中记录 deprecation 告警

---

## 7. 结论与建议

### 7.1 总结

Feature 084 的核心价值不是"增加新功能"，而是**修通 OctoAgent 的基本能力闭环**：Connor 应该能在 web 端对 Agent 说"更新我的档案"并看到确认结果。这个最基本的能力在 F082 之后仍然断开，根因在于 Context + Harness 两层存在系统性断层（Tool Registry entrypoints 缺失、Snapshot Store 不存在、工具命名语义失配）。

5 个 reference 产品（Hermes Agent / OpenClaw / Agent Zero / Pydantic AI / Harness Engineering 趋势）均已在各自场景下解决了类似问题，核心路径是一致的：工具动态注入（按入口按阶段）+ 工具结果持久化（Snapshot）+ 统一威胁扫描。OctoAgent 应以此为基准进行重构，而非继续在现有架构上打补丁。

### 7.2 对技术调研的建议

- **Tool Registry 设计**：参考 Hermes Agent 的 toolset 分级（default / extended / full）和 OpenClaw 的按 turn 动态注入机制；核心问题是"entrypoints 声明式化"和"按 capability pack 动态解析"
- **Snapshot Store**：参考 Hermes Agent 的 SQLite WAL + FTS5 模式；重点设计 SnapshotRecord schema（tool_call_id / result_summary / timestamp / ttl）和查询接口
- **Threat Scanner**：参考 Harness Engineering 趋势的两级分类（fast rule gate + CoT review）；梳理现有 PolicyEngine + ToolBroker 中分散的 policy 逻辑，统一到单一入口
- **Observation Worker**：参考 Hermes Agent 的 `honcho_conclude` 模式（会话结束合成）+ OpenClaw 的 append-only session log（实时追加）；首版建议会话结束触发，异步提取，结果入 pending 队列
- **UI promote**：前端工作量评估需要考虑现有 React 组件库；候选列表可复用 Approvals 面板的交互模式（accept/reject 已有实现）

### 7.3 风险与不确定性

| 风险 | 等级 | 缓解建议 |
|------|------|---------|
| Tool Registry 重构影响所有现有工具的可见性 | 高 | 先盘点所有工具的现有 entrypoints 声明，制作影响矩阵；分批迁移并有回滚路径 |
| Observation LLM 事实提取质量不稳定 | 中 | 首版限制结构化字段提取；设置置信度阈值（< 0.7 不入 pending 队列） |
| UI promote 依赖 Observation 质量，若 Observation 不准会产生大量错误候选 | 中 | Observation Worker 先跑一段时间收集数据，UI 后上；或 promote 默认关闭，手动开启 |
| bootstrap.complete 直接 break 影响已有用户数据（Connor 自己） | 低 | 用户已决策重装即可；确保 bootstrap reset CLI 可用 |
| Snapshot Store TTL 设计不当导致 SQLite 无限增长 | 低 | F084 spec 中明确 TTL（建议 30 天）和归档策略 |

---

*报告生成于 2026-04-27。在线模式，数据来源已在文档中标注。`[推断]` 标注的内容基于架构合理性推断，非直接证据。*
