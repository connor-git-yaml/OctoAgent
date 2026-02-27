# OctoAgent 项目宪法（Constitution）

> 项目名称：**OctoAgent**
> 内部代号：**ATM（Advanced Token Monster）**
> 定位：**个人智能操作系统（Personal AI OS）**
> 版本：v0.1
> 来源：OctoAgent_Blueprint.md §2

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
- **MUST**：进程重启后，任务状态不能"消失"，要么可恢复，要么可终止到终态（FAILED/CANCELLED）
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

### 原则 7：User-in-Control（用户始终可控）

- **MUST**：任何高风险动作必须可审批
- **MUST**：任何任务必须可取消
- **MUST**：任何重要数据必须可删除
- **MUST NOT**：禁止"我觉得这样更好所以直接做了"的自主决策行为

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

- **MUST**：MVP 阶段使用 SQLite（WAL 模式）
- **MUST**：事件表 append-only
- **SHOULD**：预留 PostgreSQL 升级路径

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
