# OctoAgent 项目宪法（Constitution）

> 项目名称：**OctoAgent**
> 内部代号：**ATM（Advanced Token Monster）**
> 定位：**个人智能操作系统（Personal AI OS）**
> 版本：v0.1
> 来源：docs/blueprint.md §2

---

## 项目概述

OctoAgent 是一个个人智能操作系统（Personal AI OS），而非聊天机器人。其核心特征：

- **入口**：多渠道（Web/Telegram 起步）
- **内核**：任务化（Task）与事件化（Event）驱动，可观测、可恢复、可中断、可审批
- **执行**：可隔离（Docker/SSH/远程节点），可回放，产物可追溯
- **记忆**：有治理（SoR/Fragments 双线 + 版本化 + 冲突仲裁 + Vault 分区）
- **模型**：统一出口（LiteLLM Proxy），alias + 策略路由
- **工具**：契约化（schema 反射）+ 动态注入（Tool RAG）+ 风险门禁（policy allow/ask/deny）

---

## I. 系统级宪章（System Constitution）

> 系统级宪章是"不可谈判的硬规则"，用于防止系统在实现过程中走偏。

### 原则 1：Durability First（耐久优先）

- **MUST**：任何长任务/后台任务必须落盘 -- Task、Event、Artifact、Checkpoint 至少具备本地持久化
- **MUST**：进程重启后，任务状态不能"消失"，要么可恢复，要么可终止到终态（FAILED/CANCELLED/REJECTED）
- **MUST NOT**：不得存在仅存在于内存中、重启即丢失的关键状态

### 原则 2：Everything is an Event（事件一等公民）

- **MUST**：模型调用、工具调用、状态迁移、审批、错误、回放，都必须生成事件记录
- **MUST**：UI/CLI 不应直接读内存状态，应以事件流/任务视图为事实来源
- **MUST NOT**：不得绕过事件系统直接修改任务状态

### 原则 3：Tools are Contracts（工具即契约）

- **MUST**：工具对模型暴露的 schema 必须与代码签名一致（单一事实源）
- **MUST**：工具必须声明副作用等级：`none | reversible | irreversible`，并进入权限系统
- **MUST NOT**：不得存在未声明副作用等级的工具

### 原则 4：Side-effect Must be Two-Phase（副作用必须二段式）

- **MUST**：不可逆操作必须拆成 `Plan`（无副作用） -> Gate（规则/人审/双模一致性） -> `Execute`
- **MUST**：任何绕过 Gate 的实现都视为严重缺陷
- **MUST NOT**：不得将不可逆操作合并为单一步骤直接执行

### 原则 5：Least Privilege by Default（默认最小权限）

- **MUST**：Kernel/Orchestrator 默认不持有高权限 secrets（设备、支付、生产配置）
- **MUST**：secrets 必须按 project/scope 分区；工具运行时按需注入
- **MUST NOT**：secrets 不得进入 LLM 上下文

### 原则 6：Degrade Gracefully（可降级）

- **MUST**：任一插件/外部依赖不可用时，系统不得整体不可用；必须支持 disable/降级路径
- **SHOULD**：降级路径应有明确的文档说明和事件记录
- **示例**：memU 插件失效 -> 记忆能力降级为本地 SQLite/FTS，不影响任务系统

### 原则 7：User-in-Control（用户可控 + 策略可配）

- **MUST**：系统必须提供审批、取消、删除等控制能力（capability always available）
- **MUST**：所有门禁（审批/取消/风险拦截）默认启用（safe by default）
- **MUST**：用户可通过策略配置（Policy Profile）调整门禁行为——包括降级、自动批准、静默执行等
- **SHOULD**：对用户已明确授权的场景（如定时任务、低风险工具链），系统应减少打扰、体现智能化
- **MUST NOT**：在无任何策略授权的情况下，不得静默执行不可逆操作

### 原则 8：Observability is a Feature（可观测性是产品功能）

- **MUST**：每个任务必须可看到：当前状态、已执行步骤、消耗、产物、失败原因与下一步建议
- **MUST**：没有可观测性的功能不可上线
- **SHOULD**：可观测数据应结构化，便于检索和分析

---

## II. 代理行为宪章（Agent Behavior Constitution）

> 代理行为宪章用于约束 Orchestrator/Worker 的行为策略（prompt + policy 的组合），避免"动作密度低""猜配置""乱写记忆"等典型事故模式。

### 原则 9：不猜关键配置与事实

- **MUST**：改配置/发命令前必须通过工具查询确认（read -> propose -> execute）
- **MUST NOT**：不得基于假设或推测执行涉及外部系统的操作

### 原则 10：默认动作密度（Bias to Action）

- **MUST**：对可执行任务，必须输出下一步"具体动作"
- **MUST NOT**：禁止无意义的"汇报-等待"循环
- **SHOULD**：动作必须满足安全门禁与可审计要求

### 原则 11：上下文卫生（Context Hygiene）

- **MUST NOT**：禁止把长日志/大文件原文直接塞进主上下文
- **MUST**：必须走"工具输出压缩/摘要 + artifact 引用"模式
- **SHOULD**：上下文中的内容应保持精简、结构化

### 原则 12：记忆写入必须治理

- **MUST NOT**：禁止模型直接写入 SoR（Source of Record）
- **MUST**：只能提出 WriteProposal，由仲裁器验证后提交
- **MUST**：写入提案必须包含证据引用和置信度

### 原则 13：失败必须可解释

- **MUST**：失败要分类（模型/解析/工具/业务）
- **MUST**：失败必须给出可恢复路径（重试、降级、等待输入、人工介入）
- **MUST NOT**：不得出现无分类、无恢复路径的失败状态

### 原则 14：A2A 协议兼容（A2A Protocol Compatibility）

- **MUST**：内部 Task 状态机是 A2A TaskState 的超集，保留 WAITING_APPROVAL、PAUSED、CREATED 等内部治理状态
- **MUST**：对外暴露 A2A 接口时，通过 A2AStateMapper 将内部状态映射为标准 A2A TaskState（submitted/working/input-required/completed/canceled/failed/rejected）
- **MUST**：终态包含 REJECTED（策略拒绝/能力不匹配），区别于运行时 FAILED
- **MUST NOT**：不得在 Kernel ↔ Worker 内部通信中丢失内部状态精度（不降级为 A2A 状态）
- **SHOULD**：Worker ↔ 外部 SubAgent 通信使用标准 A2A TaskState，确保互操作性
- **MUST**：Artifact 采用 A2A 兼容的 parts 多部分结构（text/file/json/image），同时保留 artifact_id、version、hash、size 等内部治理字段
- **MUST**：Artifact 支持 append 流式追加模式（对齐 A2A append + lastChunk）
- **SHOULD**：对外暴露 A2A Artifact 时，通过映射层转换（内部独有字段降级到 metadata）

---

## III. 技术栈约束

> 以下技术选型已在蓝图中确定，是系统的技术底线约束。

### 语言与运行时

- **MUST**：主工程使用 Python 3.12+
- **MUST**：依赖管理使用 uv
- **MUST**：执行隔离使用 Docker

### Web / API

- **MUST**：API 层使用 FastAPI + Uvicorn
- **MUST**：任务流式事件优先使用 SSE

### 数据持久化

- **MUST**：结构化数据（Task/Event/Artifact 元信息）使用 SQLite（WAL 模式）
- **MUST**：事件表 append-only
- **MUST**：语义检索（记忆/工具索引/知识库）直接使用向量数据库（如 ChromaDB / Qdrant）
- **MUST NOT**：不经过 SQLite FTS 中间态，直接上 embedding 方案
- **SHOULD**：预留 PostgreSQL + pgvector 升级路径

### 模型网关

- **MUST**：统一通过 LiteLLM Proxy 访问模型
- **MUST NOT**：业务代码中不得硬编码厂商模型名，必须使用 alias

### Agent / Workflow

- **MUST**：数据模型使用 Pydantic
- **MUST**：Skill 层使用 Pydantic AI（结构化输出 + 工具调用）
- **SHOULD**：Graph Engine 保留可替换能力（pydantic-graph / LangGraph）

### 可观测

- **MUST**：使用 OpenTelemetry（traces）
- **MUST**：使用结构化日志（JSON logging）
- **SHOULD**：使用 Prometheus（metrics）

---

## IV. 质量门控

> 确保交付质量的最低标准。

### 测试基线

- **MUST**：核心 domain models 具备单元测试
- **MUST**：事件存储的事务一致性有测试覆盖
- **MUST**：工具 schema 反射一致性有 contract test
- **SHOULD**：关键流程有集成测试覆盖

### 安全基线

- **MUST**：secrets 不进 prompt
- **MUST**：Vault 分区默认不可检索
- **MUST**：所有外部发送类动作必须经过门禁

### 可靠性基线

- **MUST**：单机断电/重启后不丢任务元信息
- **MUST**：插件崩溃不应拖死主进程（隔离/超时/熔断）

---

## V. 关键设计取舍

> 明确记录的战略性取舍，避免实现过程中反复讨论。

1. **不追求通用多智能体平台**：先把"单体 OS"打牢，不在早期追求可扩展的多代理生态
2. **不引入重量级编排器**：先用 SQLite Event Store + Checkpoint + Watchdog 达到 80/20，预留 Temporal 升级路径
3. **不绑死任何外部依赖**：所有外部依赖（Provider、Channel、Memory 实现）都必须可替换、可降级
4. **Free Loop 与 Graph 双模式共存**：自由任务用 Free Loop，关键流程用 Graph；安全边界始终在 Policy Engine
5. **本地优先**：Mac + 局域网设备为主，允许部分组件云端化但不以此为第一目标

---

## VI. 非目标（Anti-goals）

> 以下内容在 v0.x 阶段明确排除，引入这些方向视为违反宪法。

- **NG1**：不构建"插件市场/生态平台"
- **NG2**：不支持"企业级多租户/权限体系/复杂 RBAC"
- **NG3**：不追求"全自动无人值守做所有高风险动作"（高风险动作必须默认需要审批或强规则门禁）
- **NG4**：不在 v0.x 阶段把所有工作流都图化（允许 Free Loop 存在，关键流程逐步固化为 Graph）
