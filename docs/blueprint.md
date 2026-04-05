# OctoAgent 项目 BluePrint（内部代号：ATM）

> ATM = Advanced Token Monster
> 本文档是 OctoAgent 工程蓝图的**索引文件**。详细内容拆分在 [`docs/blueprint/`](blueprint/) 子目录中。
> 目标是：**不用再回翻调研材料，也能按本文档开工**。

---

## 0. 文档元信息

- 项目名称：**OctoAgent**
- 内部代号：**ATM（Advanced Token Monster）**
- 文档类型：Project Blueprint / Engineering Blueprint
- 版本：v0.1（实现准备版）
- 状态：M0-Delivered / M1-Delivered / M1.5-Delivered / M2-Delivered / M3-Delivered（2026-03-08 同步）
- M0 完成日期：2026-02-28（commit `52959a7`）
- 目标读者：
  - 你（Owner / PM / 架构师 / 最终用户）
  - 未来可能加入的 1-3 名协作者（工程实现、前端、运维）
- 约束假设（可调整）：
  - 单用户为主（你的个人 AI OS），允许未来扩展到"小团队/家庭"但不以此为第一目标
  - 本地优先（个人电脑 + 局域网设备），允许部分组件云端化（如 GPU worker / 远端 job runner）
  - 需要 7x24 长期运行能力与可恢复能力（durable & resumable）

### 子文档索引

| 文件 | 对应章节 | 说明 |
|------|---------|------|
| [requirements.md](blueprint/requirements.md) | §5 | 功能需求 + 非功能需求 |
| [architecture-overview.md](blueprint/architecture-overview.md) | §6 | 分层架构 + Mermaid 图 + 关键路径 |
| [core-design.md](blueprint/core-design.md) | §8 | 9 个子系统核心设计（最大章节） |
| [module-design.md](blueprint/module-design.md) | §9 | Monorepo 结构 + 12 个模块职责 |
| [api-and-protocol.md](blueprint/api-and-protocol.md) | §10 | Gateway-Kernel / A2A / Tool Call 协议 |
| [architecture-tradeoffs.md](blueprint/architecture-tradeoffs.md) | §11 | 14 个架构权衡点与收敛方案 |
| [deployment-and-ops.md](blueprint/deployment-and-ops.md) | §12 | 部署拓扑 / Docker / 备份 / 故障策略 / DX |
| [testing-strategy.md](blueprint/testing-strategy.md) | §13 | 10 个测试类别 + 覆盖矩阵 |
| [milestones.md](blueprint/milestones.md) | §14 | M0-M5 里程碑 Feature 列表 |
| [architecture-audit.md](blueprint/architecture-audit.md) | §14.5-14.8 | 短板 / 架构问题 / Worker 审计 / 代码审计 |
| [appendix.md](blueprint/appendix.md) | 附录 | 术语表 + 示例配置 |

---

## 1. 执行摘要（Executive Summary）

OctoAgent 的定位不是"一个聊天机器人"，而是一个 **个人智能操作系统（Personal AI OS）**：

- 入口：多渠道（Web/Telegram 起步，后续可接入微信导入、Slack 等）
- 内核：任务化（Task）与事件化（Event）驱动，**可观测、可恢复、可中断、可审批**
- 执行：可隔离（Docker / SSH / 远程节点），可回放，产物（Artifacts）可追溯
- 记忆：有治理（SoR/Fragments 双线 + 版本化 + 冲突仲裁 + Vault 分区）
- 模型：统一出口（LiteLLM Proxy），别在业务代码里写死厂商模型名；以 alias + 策略路由
- 工具：契约化（schema 反射）+ 动态注入（Tool RAG）+ 风险门禁（policy allow/ask/deny）
- 目标：把你现有痛点收敛为一套"工程化可持续运行"的系统，且具备可演进能力。

**关键设计取舍：**
- 不追求一开始就做成"通用多智能体平台"。先把"单体 OS"打牢。
- 不追求一开始就引入重量级 Durable Orchestrator（如 Temporal）。先用 SQLite Event Store + Checkpoint + Watchdog 达到 80/20，预留升级路径。
- 不绑死任何一个 Provider、Channel、Memory 实现。所有外部依赖都必须可替换、可降级。

---

## 2. Constitution（系统宪章）

Constitution 是"不可谈判的硬规则"，用于防止系统在实现过程中走偏（尤其是你经历过生产事故后的硬约束）。

### 2.1 系统级宪章（System Constitution）

1) **Durability First（耐久优先）**
   - 任何长任务/后台任务必须落盘：Task、Event、Artifact、Checkpoint 至少具备本地持久化。
   - 进程重启后：任务状态不能"消失"，要么可恢复，要么可终止到终态（FAILED/CANCELLED/REJECTED）。

2) **Everything is an Event（事件一等公民）**
   - 模型调用、工具调用、状态迁移、审批、错误、回放，都必须生成事件记录。
   - UI/CLI 不应直接读内存状态，应以事件流/任务视图为事实来源。

3) **Tools are Contracts（工具即契约）**
   - 工具对模型暴露的 schema 必须与代码签名一致（单一事实源）。
   - 工具必须声明副作用等级：`none | reversible | irreversible`，并进入权限系统。

4) **Side-effect Must be Two-Phase（副作用必须二段式）**
   - 不可逆操作必须拆成：`Plan`（无副作用）→ Gate（规则/人审/双模一致性）→ `Execute`。
   - 任何绕过 Gate 的实现都视为严重缺陷。

5) **Least Privilege by Default（默认最小权限）**
   - Kernel/Orchestrator 默认不持有高权限 secrets（设备、支付、生产配置）。
   - secrets 必须按 project / scope 分区；工具运行时按需注入，不得进入 LLM 上下文。

6) **Degrade Gracefully（可降级）**
   - 任一插件/外部依赖不可用时，系统不得整体不可用；必须支持 disable/降级路径。
- 例如：外部 memory engine 失效 → 记忆能力降级为本地 SQLite 直查，不影响任务系统。
  - `packages/memory` 保留 governance plane；当前使用本地 SQLite backend，后续可按需接入向量检索等高级 engine。

7) **User-in-Control（用户可控 + 策略可配）**
   - 系统必须提供审批、取消、删除等控制能力（capability always available）。
   - 所有门禁默认启用（safe by default），但用户可通过 PermissionPreset + ApprovalOverride 调整——包括自动批准、静默执行等。
   - 对用户已明确授权的场景（定时任务、低风险工具链），应减少打扰、体现智能化。
   - 在无任何策略授权的情况下，不得静默执行不可逆操作。

8) **Observability is a Feature（可观测性是产品功能）**
   - 每个任务必须可看到：当前状态、已执行步骤、消耗、产物、失败原因与下一步建议。
   - 没有可观测性，就谈不上长期运行。

### 2.2 代理行为宪章（Agent Behavior Constitution）

> 这部分用于约束 Orchestrator / Worker 的行为策略（prompt + policy 的组合），避免"动作密度低""猜配置""乱写记忆"等典型事故模式。

1) **不猜关键配置与事实**
   - 改配置/发命令前必须通过工具查询确认（read → propose → execute）。

2) **默认动作密度（Bias to Action）**
   - 对可执行任务，必须输出下一步"具体动作"；禁止无意义的"汇报-等待"循环。
   - 但动作必须满足安全门禁与可审计。

3) **上下文卫生（Context Hygiene）**
   - 禁止把长日志/大文件原文直接塞进主上下文；必须走"工具输出压缩/摘要 + artifact 引用"。

4) **记忆写入必须治理**
   - 禁止模型直接写入 SoR；只能提出 WriteProposal，由仲裁器验证后提交。

5) **失败必须可解释**
   - 失败要分类（模型/解析/工具/业务），并给出可恢复路径（重试、降级、等待输入、人工介入）。

6) **优先提供上下文，而不是堆积硬策略**
   - 除权限、审批、审计、loop guard、memory 写入治理等硬边界外，系统应优先通过显式上下文、行为文件、runtime hints、工具能力与已确认事实来引导模型决策。
   - 当模型可以基于充分上下文稳定判断时，优先减少 case-by-case 的代码特判、字符串 heuristic 和过度防御式 prompt 限制。
   - 默认要充分信任模型在完整上下文下的理解、规划与表达能力，而不是把常见行为写死在代码分支里。

---

## 3. 目标、非目标与成功判据

### 3.1 项目目标（Goals）

- G1：构建一个能长期运行的 OctoAgent 内核：Task/Event/Artifact/Checkpoint 闭环
- G2：解决"主 Session 带宽不足"与"子任务失联/中断丢上下文"的核心痛点
- G3：多渠道输入输出：至少 Web + Telegram；后续可插件化扩展
- G4：工具治理：工具契约化 + 动态注入 + 风险门禁
- G5：记忆治理：SoR/Fragments 双线 + 版本化 + 冲突仲裁 + Vault 分区
- G6：统一模型出口与成本治理：LiteLLM Proxy + alias 路由 + fallback + 统计
- G7：提供最小可用 UI：Chat + Task 面板 + Approvals（审批）+ Artifacts 查看

### 3.2 非目标（Non-goals）

- NG1：不在 v0.x 阶段构建"插件市场/生态平台"
- NG2：不在 v0.x 阶段支持"企业级多租户/权限体系/复杂 RBAC"
- NG3：不在 v0.x 阶段追求"全自动无人值守做所有高风险动作"
- NG4：不在 v0.x 阶段把所有子流程都 Pipeline 化

### 3.3 成功判据（Success Metrics）

- S1：系统重启后，所有未完成任务都能在 UI 列表中看到，并且能 resume 或 cancel
- S2：任一任务可完整回放：能看到事件流、工具调用、产物列表
- S3：高风险操作默认需要审批或双模一致性门禁
- S4：多渠道一致性：同一 thread 的消息能落到同一 scope；支持增量去重与���要
- S5：记忆一致性：同一 subject_key 在 SoR 永远只有 1 条 `current`
- S6：成本可见：每个 task 可看到 tokens/cost（按 model alias 聚合）

---

## 4. 用户画像与核心场景

### 4.1 Persona

- P1：Owner（你）— 长任务、跨设备、可审计、可控风险、可治理记忆
- P2：未来协作者（可选）— 可读的工程结构、可测试、可扩展

### 4.2 核心场景（Use Cases）

- UC1：每日/每周例行任务（早报、日报、周报、复盘）
- UC2：长时间研究与产出（调研报告、技术方案、对比分析）
- UC3：跨设备运维（NAS/Windows/Mac 脚本执行、状态检查）
- UC4：外部聊天导入与记忆更新（微信/Telegram 历史 → SoR/Fragments）
- UC5：有副作用的系统操作（改配置、发消息、创建日程）——默认审批
- UC6：项目资产治理（Projects / Skills / Scripts）
- UC7：故障恢复（崩溃、断网、provider 429、插件失效）下的自动降级

---

## 5. 需求（Requirements）

> 详见 [blueprint/requirements.md](blueprint/requirements.md)

| 领域 | 关键需求编号 | 优先级 |
|------|------------|--------|
| 多渠道接入 | FR-CH-1~5 | 必须/应该 |
| Task/Event/Artifact | FR-TASK-1~4 | 必须 |
| Orchestrator + Workers | FR-A2A-1~3 | 必须 |
| Skills / Tools | FR-TOOL-1~3, FR-SKILL-1, FR-TOOLRAG-1 | 必须/应该/可选 |
| Memory | FR-MEM-1~4 | 必须/应该/可选 |
| 执行层 | FR-EXEC-1~4 | 必须/应该 |
| Provider | FR-LLM-1~2 | 必须/应该 |
| 运维 | FR-OPS-1~4 | 必须/应该 |
| 非功能 | NFR-1~5 | — |

---

## 6. 总体架构（Architecture Overview）

> 详见 [blueprint/architecture-overview.md](blueprint/architecture-overview.md)

```
Channels (Telegram/Web) → OctoGateway → OctoKernel → Workers → LiteLLM Proxy
```

- **主 Agent**：Free Loop 主执行者 + 监督者，绑定 Project
- **Workers**：持久化自治智能体，独立 Free Loop + Session + Memory
- **Subagent**：临时智能体，共享 Worker 的 Project 上下文
- **Skill Pipeline / Graph**：Worker 的确定性编排工具（DAG/FSM + checkpoint）
- **LiteLLM Proxy**：统一模型网关（alias + fallback + 成本统计）

---

## 7. 技术选型（Tech Stack & Rationale）

> 目标：用尽可能少的组件实现核心价值；同时所有关键依赖都要可替换。

### 7.1 语言与运行时

- Python 3.12+（主工程）
- uv（依赖与环境管理）
- Docker（执行隔离）

理由：
- 生态与 agent 框架成熟；落地速度快；易于沉淀工具与技能。

### 7.2 Web / API

- FastAPI + Uvicorn（Gateway + Kernel API）
- SSE（任务流式事件）优先；WS 可选

理由：
- SSE 足够满足 task stream（one-way），比 WS 简单稳定；可降级到长轮询。

### 7.3 数据持久化

- SQLite（结构化数据默认）
  - WAL 模式
  - 事件表 append-only
  - 用于 Task/Event/Artifact 元信息等结构化存储

- 向量数据库（语义检索默认）
  - LanceDB（嵌入式 in-process，MVP 首选）
  - 用于 ToolIndex / 记忆检索 / 知识库
  - 直接上 embedding 方案，不经过 FTS 中间态
  - 原生支持版本化 Lance 格式、混合检索（vector + FTS + SQL）、增量更新

### 7.4 模型网关

- LiteLLM Proxy（必选）

理由：
- 把 provider 差异、密钥托管、fallback、限流、成本统计从业务代码剥离；
- 让你未来切换模型/订阅/供应商时不需要大改。

### 7.5 Agent / Workflow / Contract

- Pydantic（数据模型、输入输出校验）
- Pydantic AI（Skill 层，结构化输出 + 工具调用）
- Graph Engine：pydantic-graph（Pydantic AI 内置子模块）

理由：
- Contract 优先：把"约束"从 prompt 转移到 schema；
- Orchestrator 和 Workers 永远 Free Loop；Skill Pipeline 仅用于有副作用/需要 checkpoint 的子流程。

### 7.6 Channel 适配

- Telegram：aiogram（原生 async + 内置 FSM + webhook 模式）
- Web UI：React + Vite（从 M0 起步，SSE 原生 EventSource）

### 7.7 可观测

- Logfire（Pydantic 团队出品，OTel 原生，自动 instrument）
- structlog（结构化日志，canonical log lines + trace_id）
- SQLite Event Store（metrics 数据源，直接 SQL 聚合）

### 7.8 任务调度

- APScheduler（MVP）

---

## 8. 核心设计（Core Design）

> 详见 [blueprint/core-design.md](blueprint/core-design.md)

| 子系统 | 关键词 | 状态 |
|--------|--------|------|
| 8.1 统一数据模型 | NormalizedMessage / Task / Event / Artifact / AgentRuntime | ✅ |
| 8.2 Task/Event Store | SQLite WAL + append-only + projection | ✅ |
| 8.3 编排模型 | Free Loop + Skill Pipeline（pydantic-graph） | ✅ |
| 8.4 Skills 设计 | Pydantic AI + SkillRunner + 生命周期钩子 | ✅ |
| 8.5 Tooling | 分级 + 契约 + Tool Index + 权限 Preset + 输出压缩 | ✅ |
| 8.6 Policy Engine | PermissionPreset × SideEffectLevel + Two-Phase Approval | ✅ |
| 8.7 Memory | SoR/Fragments/Vault + Facade + fast_commit + 并行 recall | ✅ |
| 8.8 Execution Plane | Worker + JobRunner + Docker Sandboxing | ✅ |
| 8.9 Provider Plane | LiteLLM alias + fallback + Auth Adapter + PKCE | ✅ |

---

## 9. 模块设计（Module Breakdown）

> 详见 [blueprint/module-design.md](blueprint/module-design.md)
> 另见 [codebase-architecture/](codebase-architecture/) 的实现级文档

| 模块 | 职责 |
|------|------|
| packages/core | Domain Models + Event Store + SQLite |
| apps/gateway | 渠道适配 + SSE 转发 + 出站发送 |
| apps/kernel | Orchestrator + Policy + Memory Core |
| workers/* | 自治智能体（ops/research/dev） |
| packages/protocol | A2A-Lite envelope + NormalizedMessage |
| packages/tooling | Schema 反射 + ToolBroker + Permission |
| packages/memory | SoR/Fragments/Vault + 仲裁 |
| packages/provider | LiteLLM client + alias + cost |
| packages/observability | Logfire + structlog |
| frontend/ | React + Vite Web UI |

---

## 10. API 与协议（Interface Spec）

> 详见 [blueprint/api-and-protocol.md](blueprint/api-and-protocol.md)

- **Gateway ↔ Kernel**：HTTP（ingest_message / tasks / stream / approvals）
- **Kernel ↔ Worker**：A2A-Lite Envelope（TASK/UPDATE/CANCEL/RESULT/ERROR/HEARTBEAT）
- **A2A 状态映射**：内部超集 ↔ 标准 A2A TaskState 双向映射
- **Tool Call 协议**：tool_calls JSON → ToolBroker 执行 → ToolResult 回��

---

## 11. 冲突排查与合理性校验

> 详见 [blueprint/architecture-tradeoffs.md](blueprint/architecture-tradeoffs.md)

14 个架构权衡点，涵盖：事件溯源 vs 快速迭代、Free Loop vs 安全门禁、记忆自动写入 vs 污染、多 Channel 一致性、Policy Profile 可配 vs 安全底线等。

---

## 12. 运行与部署（Ops & Deployment）

> 详见 [blueprint/deployment-and-ops.md](blueprint/deployment-and-ops.md)

覆盖：部署拓扑（开发/生产）、Docker Compose、健康检查、备份与恢复、故障策略、优雅关闭、升级迁移、日志管理、SSL/TLS、DX 工具（octo config / doctor / onboard / start）。

---

## 13. 测试策略（Testing Strategy）

> 详见 [blueprint/testing-strategy.md](blueprint/testing-strategy.md)

10 层测试：基础设施 → 单元 → LLM 交互 → 集成 → 编排 → 安全 → 可观测 → 回放 → 韧性 + 覆盖对齐矩阵（Constitution × 成功判据）。

---

## 14. 里程碑与交付物（Roadmap）

> 详见 [blueprint/milestones.md](blueprint/milestones.md)
> 审计记录见 [blueprint/architecture-audit.md](blueprint/architecture-audit.md)

| 里程碑 | 状态 | 核心交付 |
|--------|------|---------|
| M0 基础底座 | ✅ | Task/Event/Artifact + SSE + 最小 Web UI |
| M1 最小智能闭环 | ✅ | LiteLLM + Auth + Skill + Tool Contract |
| M1.5 Agent 闭环 | ✅ | Orchestrator + Worker + Policy |
| M2 多渠道多 Worker | ✅ | Telegram + A2A + JobRunner + Memory |
| M3 增强 | ✅ | Chat Import + Vault + ToolIndex + Pipeline |
| M4 引导式工作台 | 🔄 | Feature 050, 063 待完成 |
| M5 文件工作台 | ⏳ | 语音/多模态/Companion/通知中心 |

### 待办汇总（§14.5-14.8）

> 详见 [blueprint/architecture-audit.md](blueprint/architecture-audit.md)

**已完成（✅）**：短板 1-5 + 架构问题 1-7

**待改善（🟠 Worker/Subagent）**：
- W1: Worker 工具集膨胀（9 个 → 合并）
- W2: DockerRuntimeBackend 空壳
- W3: Graph cancel_signal 未连接
- W4: Work 状态机无形式化约束
- W5: WAITING_INPUT deadline 无限重置

**代码架构问题**：
- ✅ A1: capability_pack.py God Object（5,112→2,052 行，47 工具迁移到 builtin_tools/ 子包）
- 🔴 A2: provider/dx → apps/gateway 反向依赖 → Protocol 注入
- ✅ A3: tooling ↔ policy 循环依赖（已修复 2026-04-05）
- 🟠 A4-A7: dx 定位模糊 / control_plane 模型混合 / Butler 遗留命名 / 状态枚举重叠

---

## 15. 风险清单与缓解（Risks & Mitigations）

> 每条风险附带检测指标与触发阈值，确保可操作化。

1) **Provider/订阅认证不稳定** — LiteLLM alias + fallback；连续 3 次失败自动切换
2) **Tool/插件供应链风险** — manifest + health gate；未注册工具调用直接 deny
3) **记忆污染** — WriteProposal + 仲裁；confidence < 0.5 → 不写入
4) **长任务失控与成本爆炸** — 预算三级阈值（80%/100%/150%）+ watchdog
5) **SQLite 扩展瓶颈** — WAL > 100MB 或跨机 Worker 时升级 Postgres
6) **LLM 幻觉** — OutputModel 强校验 + guardrails；校验失败率 > 30% 升级模型
7) **上下文窗口溢出** — 工具输出压缩 + Context GC；> 80% 窗口触发
8) **安全攻击面** — Docker 隔离 + secrets 不进 LLM 上下文 + 输入消毒

---

## 16. 实现前检查清单（Pre-Implementation Checklist）

### 16.1 决策类（需要拍板，影响架构）

- [ ] 明确 v0.1 的 P0 场景（建议：早报/日报 + 局域网运维 + 调研报告）
- [ ] 确定第一批高风险工具清单与默认策略（哪些必须审批）
- [ ] 确定 secrets 分区方案（哪些放 Vault、哪些放 provider）
- [ ] 确定本地运行拓扑（单进程/多进程/容器化）
- [ ] 确定 UI 最小形态（task 面板字段 + 审批交互）

### 16.2 准备类（工程环境就绪，开工前完成）

- [x] 开发环境确认：Python 3.12 + uv + Docker Desktop + Node.js（Web UI）— M0 已验证
- [ ] LiteLLM Proxy 就绪：至少 1 个 provider 可用 + cheap/main/fallback 运行时 group 配通 — M1 前置
- [x] SQLite schema 初始化脚本准备 — M0 已交付（lifespan 自动建表）
- [x] CI/测试基础设施：pytest + pytest-asyncio + ruff — M0 已交付（105 tests）
- [x] 可观测性基础：structlog + Logfire 本地配置 — M0 已交付
- [ ] Telegram Bot 注册（如 M2 需要接入，提前准备 bot token + allowlist）
- [x] 配置诊断工具 `octo doctor` + 统一模型配置 `octo config`（详见 §12.9）— Feature 014 已交付

---

## 17. 待确认事项（需要你拍板/补充信息）

> 为避免"边做边返工"，这里列出我认为会影响架构的关键决策点。你不需要现在回答，但在进入 M1/M2 前至少要冻结。

1) **目标运行拓扑**：v0.1 单进程还是多进程？
2) **渠道优先级**：Telegram 是否第一优先？微信"导入"还是"实时接入"？
3) **高风险动作列表**：哪些动作必须永远审批？
4) **记忆敏感分区**：health/finance 是否默认完全不可检索？
5) **设备控制方式**：LAN 设备是否统一走 SSH？
6) **数据存储位置**：SQLite/artifacts/vault 放本机还是 NAS？
7) **预算策略**：per-task 硬预算上限，超过后暂停还是降级？
8) **错误上报渠道**：Telegram / Web UI / 两者都要？
9) **Free Loop 迭代上限**：建议默认 50，是否 per-worker 可配？

---

## 附录

> 详见 [blueprint/appendix.md](blueprint/appendix.md)

- 附录 A：术语表（Glossary）
- 附录 B：示例配置片段（system.yaml + telegram.yaml）

---

**END**
