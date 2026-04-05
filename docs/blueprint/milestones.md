# §14 里程碑与交付物（Roadmap）

> 本文件是 [blueprint.md](../blueprint.md) §14 的完整内容（不含审计部分）。
> 审计部分见 [architecture-audit.md](architecture-audit.md)。

---

## 14. 里程碑与交付物（Roadmap）

> 这里给出"可以直接开工"的拆解顺序，按收益/风险比排序。

**分层策略说明**：M0-M1 聚焦核心基础设施（数据模型 + 事件系统 + 工具治理），此阶段部分"必须"级需求（Telegram、Workers、Memory）尚未引入，这属于**有意的架构分层策略**——先保证 Constitution 中 Durability First 和 Everything is an Event 的基础牢固，再叠加智能与交互能力。M1.5 补齐最小 Agent 闭环（Orchestrator + Worker），M2 扩展多渠道与治理，M3 深化增强。

### M0（基础底座）：Task/Event/Artifact + 端到端验证 ✅ 已完成

> **完成日期**：2026-02-28 | **测试**：105 passed | **代码**：`octoagent/` | **Spec**：`.specify/features/001-implement-m0-foundation/`

- [x] SQLite schema（WAL 模式）+ event append API + projection + rebuild CLI
- [x] `POST /api/message` 创建 task + 写 TASK_CREATED / USER_MESSAGE 事件（含 idempotency_key 去重）
- [x] `GET /api/stream/task/{task_id}` SSE 事件流（历史回放 + 实时推送 + final 标记）
- [x] Artifact store（inline < 4KB + 文件系统 > 4KB，含 SHA-256 校验）
- [x] 可观测性基础：structlog + Logfire 配置 + x-request-id/trace_id 贯穿所有日志
- [x] 最小 LLM 回路：Echo LLM → MODEL_CALL_STARTED/COMPLETED 双事件 + token_usage → SSE 推送
- [x] 最小 Web UI：TaskList 页 + TaskDetail 页（事件时间线 + Artifact 展示）+ useSSE Hook
- [x] Task 取消：`POST /api/tasks/{id}/cancel` → CANCELLED 终态（终态任务返回 409）
- [x] Readiness check：`GET /ready` profile-based（core/llm/full）

交付：一个可跑的"任务账本 + 事件流 + 最小 LLM 回路"系统，端到端已验证。

验收标准（6/6 通过）：

- [x] SC-1：task 创建 → 事件落盘 → LLM 调用 → SSE 推送 端到端通过
- [x] SC-2：进程 kill -9 后重启，task 状态 + events + artifacts 完好（Durability First 验证）
- [x] SC-3：Projection Rebuild 从 events 重建 task 状态，与原始一致
- [x] SC-4：Artifact 文件可存储（inline + 文件系统双模式）、可按 task_id 检索
- [x] SC-5：所有响应头包含 x-request-id（ULID），日志绑定 request_id/trace_id
- [x] SC-6：Task 取消 API 正确推进到 CANCELLED 终态

M0 实现要点与 Blueprint 偏差记录：

- Gateway/Kernel 合并为单 FastAPI 进程（M0 阶段 Kernel 核心职责未就绪，独立进程过度设计）
- LLM 使用 Echo 模式直连（非 LiteLLM Proxy），M1 升级仅改 base_url
- Event payload 遵循最小化原则：摘要 + artifact_ref，原文不入 Event

### M1（最小智能闭环）：LiteLLM + Auth + Skill + Tool contract（2 周）

- [x] 接入 LiteLLM Proxy + 运行时 alias group 配置（cheap/main/fallback）+ 语义 alias 映射 — Feature 002 已交付
- [x] 语义 alias → 运行时 group 映射 + FallbackManager + 成本双通道记录 — Feature 002 已交付
- [x] Auth Adapter + DX 工具（§8.9.4 + §12.9）— Feature 003 已交付（253 tests）
  - 凭证数据模型（ApiKey/Token/OAuth 三种类型定义）+ AuthAdapter 接口
  - ApiKeyAdapter + SetupTokenAdapter + CodexOAuthAdapter（Device Flow）
  - Credential Store + Handler Chain + `octo init` / `octo doctor` + dotenv 自动加载
- [x] OAuth Authorization Code + PKCE + Per-Provider Auth — Feature 003-b 已交付（404 tests）
  - PKCE 生成器 + 本地回调服务器 + Per-Provider OAuth 注册表
  - 多认证路由隔离（HandlerChainResult 路由覆盖）+ Codex Reasoning 配置
  - 环境检测 + 手动粘贴降级 + Token 自动刷新
- [x] 工具 schema 反射 + ToolBroker 执行 — Feature 004 已交付
- [x] 实现 Pydantic Skill Runner（结构化输出）— Feature 005 已交付
- [x] Policy Engine（allow/ask/deny）+ Approvals UI — Feature 006 已交付
- [x] 端到端集成 + M1 验收 — Feature 007 已交付并合入 master（2026-03-02）
- [x] 工具输出压缩（summarizer）— Feature 004（路径引用）+ 007（可选激活）已就绪
- [x] Feature 007 集成补齐运行治理能力（随 007 一并交付）：
  - Task Journal（TASK_MILESTONE / TASK_HEARTBEAT 事件 + 投影视图）
  - Runner 漂移检测（stale-progress + status drift detector，含修复建议）
  - Schedule Job Contract（payload 模板 + preflight + retry/backoff + delivery ack）
  - 运行治理视图（运行中 / 疑似卡死 / 已漂移 / 待审批）
  - Secret Hygiene 收口（配置快照/运行日志/事件统一脱敏 + 漏检扫描）

交付：能安全调用工具、能审批、能产出 artifacts；模型调用有成本可见性；三种认证模式全部就绪（API Key + Setup Token + OAuth PKCE）。

验收标准：

- LLM 调用 → 结构化输出 → 工具执行 端到端通过
- irreversible 工具触发审批流，approve 后继续执行
- 工具 schema 自动反射与代码签名一致（contract test 通过）
- 每次模型调用生成 cost/tokens 事件
- 配置 alias 与运行时消费一致；legacy 语义 alias 仅在未显式配置同名 alias 时按兼容映射回退
- Auth：OpenAI/OpenRouter API Key → credential store → LiteLLM Proxy → 真实 LLM 调用成功
- Auth：OAuth PKCE 全流程（本地回调 + 手动降级 + Token 自动刷新）
- Auth/DX：`octo init`（历史路径）+ `octo config`（当前路径）可完成认证配置，`octo doctor` 诊断凭证状态
- Auth：凭证不出现在日志/事件/LLM 上下文中（C5 合规）

Feature 007（已完成）验证快照（2026-03-02）：

- 已新增真实联调测试：`octoagent/tests/integration/test_f007_e2e_integration.py`
- 已验证链路：`SkillRunner -> ToolBroker -> check_permission() -> ApprovalManager`
- 已验证事件链：`POLICY_DECISION / APPROVAL_REQUESTED / APPROVAL_APPROVED / TOOL_CALL_*`
- 说明：本轮按范围控制不改 Gateway 主聊天链路（主链路重构移至 M1.5 评估）

### M1.5（最小 Agent 闭环）：Orchestrator + Worker + Checkpoint（2 周）✅ 已交付

> **完成日期**：2026-03-04（核心闭环 008-013）/ 2026-03-06（DX 收口 014）
> **拆解文档**：`docs/milestone/m1.5-feature-split.md`

- [x] Feature 008：Orchestrator Skeleton（版本化派发契约 + Worker 回传）
- [x] Feature 009：Worker Runtime（Free Loop + Docker/timeout/cancel）
- [x] Feature 010：Checkpoint & Resume（幂等恢复 + 损坏降级）
- [x] Feature 011：Watchdog + Task Journal + Drift Detector
- [x] Feature 012：Logfire + Health/Plugin Diagnostics
- [x] Feature 013：M1.5 E2E 集成验收
- [x] Feature 014：统一模型配置管理（`octo config`，M1.5 DX 收口）
- [ ] 插件**进程级**隔离仍保留为后续增强项；M1.5 已先完成诊断与健康治理

M1.5 交付约束（已验证）：

- 控制平面契约版本化：`DispatchEnvelope` 包含 `contract_version`、`route_reason`、`worker_capability`、`hop_count/max_hops`
- Checkpoint 恢复幂等：重复恢复不重复执行已落盘副作用，快照损坏可安全降级
- Watchdog 默认阈值生效：heartbeat / no-progress / cooldown 有默认值且可配置

交付：已具备最小自治 Agent 闭环能力——Orchestrator 接收任务、派发 Worker、Worker 自主执行并回传结果；任务可恢复、可监控，并且 DX 配置入口已统一到 `octo config`。

验收标准（已通过）：

- [x] 用户消息 → Orchestrator 路由 → Worker 执行 → 结果回传 端到端通过
- [x] `DispatchEnvelope` 版本字段与跳数保护生效（`hop_count <= max_hops`）
- [x] Worker 中断后可从 checkpoint 恢复，不需全量重跑
- [x] 重复恢复幂等（不重复执行已落盘副作用）
- [x] 无进展任务被 watchdog 检测并触发提醒
- [x] 默认 watchdog 阈值生效（heartbeat/no-progress/cooldown）
- [x] Logfire 面板可查看 trace 链路（Gateway → Kernel → Worker → LLM）
- [x] `task_id/trace_id/span_id` 在关键链路透传一致并可校验

### M2（多渠道 + 运行治理体验化）：Telegram + A2A + JobRunner + Memory（4-5 周）✅ 已交付

- 拆解文档：`docs/milestone/m2-feature-split.md`（2026-03-06 新增）
- 当前基线（2026-03-08）：015 / 016 / 017 / 018 / 019 / 020 / 021 / 022 / 023 已交付
- [x] Feature 015：`octo onboard` + doctor guided remediation（首次使用闭环）
- [x] Feature 016：TelegramChannel（pairing + webhook/polling + session routing）
- [x] Feature 017：统一操作收件箱（approvals / alerts / retry / cancel，Web + Telegram 等价）
- [x] Feature 018：A2A-Lite 消息投递 + A2AStateMapper
- [x] Feature 019：JobRunner docker backend + 交互式执行控制台
- [x] Feature 020：基础 memory（Fragments + SoR + WriteProposal + Vault skeleton）
- [x] Feature 021：Chat Import Core（`octo import chats` / dry-run / report）
- [x] Feature 022：Backup/Restore + 会话导出 + 恢复演练记录
- [x] Feature 023：M2 集成验收（不引入新能力）

M2 执行约束（2026-03-06 OpenClaw / Agent Zero 可用性复核）：

- 上手路径必须闭环：`octo config` → `octo doctor --live` → channel pairing → 首条消息验证，禁止要求用户手改多份配置后自行猜下一步
- 操作控制必须渠道等价：approve / retry / cancel / 查看 pending 队列在 Web 与 Telegram 上使用同一事件语义
- 长任务交互必须可审计：日志流、人工输入、取消、重试都要落同一任务事件链
- 备份恢复必须自助化：至少提供 backup/export/restore dry-run，而不是只留底层脚本

交付：从"能运行的 Agent"推进到"每天能稳定使用的 Personal AI OS"——新用户可完成首次配置并真正发出第一条消息；操作者可在 Web/Telegram 统一处理审批、告警、重试与取消；A2A、JobRunner、Memory 与导入链路全部具备可用入口。

验收标准：

- 新用户在引导式流程内完成 provider 配置、doctor 自检、Telegram pairing，并成功发送首条测试消息
- Telegram 消息 → NormalizedMessage → Task 创建 / 审批 / 回传 端到端通过
- A2A-Lite 消息在 Orchestrator ↔ Worker 间可靠投递，A2AStateMapper 映射幂等
- JobRunner 在 Docker 内执行任务并支持日志流、取消、可选人工输入
- Memory 写入经仲裁（WriteProposal → 验证 → commit），SoR 同 subject_key 只有 1 条 current
- Chat Import 增量导入去重 + 窗口化摘要正确，且不污染主聊天 scope
- 备份包可在 dry-run 中完成校验，并能恢复 tasks / events / chats / config 元数据

### M3（用户 Ready 增强）：统一配置 / 管理台 / 记忆产品化（补位中）

- 拆解文档：`docs/milestone/m3-feature-split.md`（2026-03-09 已同步到"033 context continuity 补位"版本）
- 本阶段目标不是继续堆"高级能力名词"，而是把 OctoAgent 推到**普通用户可安装、可配置、可升级、可恢复、可迁移**的状态
- 参考复核（2026-03-08）：OpenClaw 的 wizard / onboarding protocol / Control UI / updating / export session / subagents；Agent Zero 的 projects / backup / memory / settings / tunnel
- 当前里程碑判断：Feature 024-031 已交付并合入 `master`；2026-03-09 的 live-usage 复核曾发现主 Agent 缺少 context continuity 主链，后续以 Feature 033 完成补位并关闭该 gate
- [x] 一键安装 / 一键升级 / 迁移修复（installer + updater + doctor/migrate）
- [x] 统一配置与 Secret Store（Provider / Channel / Model / Gateway 一体化向导，环境变量退居高级路径）
- [x] Project / Workspace 一等公民（project = instructions + memory + secrets + files + channel/A2A bindings 的统一隔离单位）
- [~] WorkerProfile / capability pack 与主 Agent Profile + Context Continuity 主链已具备骨架；Butler/Worker 的全 Agent session/memory/recall parity 仍待补齐
- [x] Telegram / Web 控制命令面（`approve` / model 切换 / skill 调用 / subagent 控制 / status）
- [x] 用户友好的 Web 管理台（dashboard / agents / memory / permissions / secrets / runtime status）
- [x] Session / Chat Lifecycle Center（history / export / queue / focus / reset / interrupt / resume）
- [x] Automation / Scheduler 产品化（recurring jobs / run history / project-scoped automation）
- [x] Runtime Diagnostics Console（logs / event stream / provider/model health / usage&cost / worker&subagent&work status）
- [x] Vault 授权检索 + Memory 浏览 / 证据追溯
- [x] Project Asset Manifest（knowledge / files / artifacts 的 upload / list / inspect / bind 最小产品面）
- [x] ~~`MemUBackend` 深度集成~~（已于 2026-03-17 移除，后续由嵌入式向量引擎替代）
- [x] 微信导入插件 + 多源导入工作台
- [x] 内置 Skill/Tools 与 Bootstrap Agent Pack（bundled skills / bundled tools / worker bootstrap）
- [x] Delegation Plane（A2A / Work graph / subagent / ACP-like runtime / graph agents）
- [x] ToolIndex（向量检索）+ 动态工具注入
- [x] Skill Pipeline Engine（关键子流程固化、可回放）+ 多 Worker 类型（ops/research/dev）+ Orchestrator 智能派发 / Work 合并
- [x] Feature 031：M3 User-Ready E2E Acceptance（正式 release gates、迁移演练、最终验收报告）
- [~] Feature 033：Agent Profile + Bootstrap + Context Continuity（Butler 主链已接入 profile / context frame / recent context / memory retrieval；Worker runtime continuity 与独立 session 仍待补齐）
- [~] Feature 038：Agent Memory Recall Optimization（project-scoped recall 主链、agent-private namespace、worker hint-first recall runtime 已打通；仍待更细粒度 user-facing evidence）
- [ ] 多端远程节点 / companion surfaces（按需引入，留给 M4）

2026-03-08 进展：

- Feature 024 已交付 installer / updater / preflight / migrate / restart / verify operator flow。
- Feature 025 已交付 project/workspace、default project migration、Secret Store、统一 wizard 与 asset manifest。
- Feature 026 已交付统一 control plane backend 与正式 Web 控制台：六类 canonical resources、snapshot/per-resource/actions/events routes、Telegram/Web 共用 action semantics、Session Center、Automation/Scheduler、Runtime Diagnostics Console、配置中心、channel/device 管理入口与统一 operator/ops 控制入口均已落地。
- Feature 027 已交付 Memory Console、Vault authorized retrieval、proposal audit 与 memory inspect / export / restore verify 入口。
- Feature 028 曾交付 MemU integration point、检索/索引/降级路径与 evidence-aligned ingest（MemU bridge 已于 2026-03-17 整体移除，evidence-aligned ingest 与降级路径保留）。
- Feature 029 已交付 WeChat adapter、Import Workbench、mapping/dry-run/dedupe/resume 与 memory effect 链路。
- Feature 030 已交付 built-in capability pack、ToolIndex、Delegation Plane、Skill Pipeline Engine 与多 Worker 路由增强，并把 tool hit、route reason、work ownership、pipeline replay 接入现有 control plane。
- Feature 031 原范围已完成：M3 已具备正式的 acceptance matrix、migration rehearsal、front-door boundary 与 release report；随后由 Feature 033 关闭 context continuity gate，M3 现已完成最终签收。
- 2026-03-09 设计复核新增 Feature 033：当时主 Agent 仍未真实消费 `AgentProfile`、owner basics、bootstrap、recent summary 与 memory retrieval；该补位已完成，不再作为当前 blocker。
- 2026-03-10 设计复核新增并实现 Feature 038：memory runtime 已补齐 `project/workspace -> resolver -> recall pack -> context/tooling/import` 主链，不再把 `MemoryBackendResolver` 限制在 console-only 路径。
- 2026-03-14 产品化纠偏：`/memory` 必须先经过用户态 display model，再展示 current memory / vault refs / derived 结果；不得把 raw projection、技术写回或占位摘要直接暴露给用户。
- 2026-03-14 配置纠偏：Memory 设置原要求显式支持三条路径（已于 2026-03-17 简化为 `local_only` 单一模式，MemU bridge 实现已整体移除）。
- 2026-03-10 M4 升级波次已启动：Feature 035 已落地 guided workbench shell 与五个主页面骨架；Feature 036 已落地 setup-governance 资源与 review/profile/policy 主链；Feature 037 已完成 runtime lineage hardening；Feature 039 已完成 supervisor-only 主 Agent、worker review/apply 与 message-native A2A 主链。
- 2026-03-12 起持续补齐的 Feature 041 已把 ambient current time、Butler-owned freshness delegation、worker governed web/tool readiness、worker private recall、缺城市追问、backend unavailable 降级与 runtime truth/workbench 可视化收口到同一主链；041 现已完成签收。
- front-door `loopback` 模式已补充对常见代理转发 header 的 fail-closed 拒绝，降低"本机反向代理误暴露 = owner-facing API 被放行"的风险。

2026-03-13 架构复核纠偏（基于 live usage + Agent Zero + OpenClaw 对标）：

- 当前实现已具备 `project / profile / work graph / dispatch envelope / memory recall` 的骨架，但运行语义仍偏向"单主 Agent + preflight 路由 + worker 直调"
- 这与目标中的"Butler 拥有自己的 session/memory/recall，并通过 message-native A2A 与拥有独立 session/memory/recall 的 Worker 通信"仍有语义差距
- 自本次复核起，Feature 033 / 038 / 039 / 041 的后续验收以"每个 Agent 都有完整上下文系统 + Butler ↔ Worker 真 A2A roundtrip + Worker 默认不直读用户主会话"为准
- Agent Zero 的 `project = instructions + memory + secrets + subagent settings + workspace` 设计被明确吸收为 `Project` 根隔离单位
- OpenClaw 的 `agentId + sessionKey` 维度、按 session 的 compaction / usage / metadata 管理被明确吸收为 `AgentSession` 设计基线

M3 产品化约束（基于 OpenClaw / Agent Zero 调研）：

- 安装、配置、首聊、管理台打开必须是一条连续路径；不能要求用户手工拼装多份 `.env`、Docker 命令和 channel token
- secret 默认应集中收敛到统一 store，并提供 audit / reload / rotate / apply；环境变量只保留给 CI、容器编排和高级用户
- `project/workspace` 必须成为 M3 的一等公民；instructions、memory、secrets、knowledge、files、A2A target 与 channel bindings 都应优先挂在 project 上，而不是散落为独立配置块
- `AgentProfile` / `WorkerProfile` 必须是正式产品对象；session、automation、work delegation 必须引用 profile id 与 effective config snapshot，而不是把 prompt、模型、工具包、策略散落在多处
- `AgentProfile` / owner basics / bootstrap / recent summary / memory retrieval 必须进入 Butler 与 Worker 的真实运行链；不能只在控制台、文档或 worker preflight 中存在
- CLI / Web 共享同一 wizard session 与 config schema，避免出现"CLI 能做、Web 不能做"或两边语义不一致
- Telegram / Web 必须共用同一命令/动作语义，不能出现"Web 能 approve，Telegram 只能看不能控"的半控制面
- 用户与 Agent 的"会话"必须成为可管理对象，而不是仅把一切折叠成 task；history/export/focus/queue/reset/intervene 等生命周期操作要进入正式产品面
- `AgentRuntime -> AgentSession -> Work/A2AConversation` 必须成为正式运行链；不得继续把 Worker 私有上下文压扁为 task metadata 或 runtime 临时对象
- 每个 Agent 都必须拥有完整上下文栈：persona / project markdown / session recency / memory namespaces / recall frame / capability / scratchpad
- Butler 当前必须是唯一 user-facing speaker；后续若开放 DirectWorkerSession，必须在产品面和数据模型中显式建模
- automation / scheduler 必须是用户可理解、可操作、可回放的产品能力，而不是只在底层放一个 APScheduler job
- 必须明确 `project -> agent runtime -> agent session -> work` 与 `project -> automation -> work` 两条继承链：project 提供默认 bindings，agent runtime 选择 profile 与 context policy，session/automation 决定交互边界，work 继承 effective config 并允许显式覆盖少数字段
- 管理台优先复用成熟开源 UI primitives，而不是手写整套控件体系；配置中心、审批、恢复、Memory 浏览应统一在同一控制台
- Agent / Worker / Subagent / Graph Agent 的管理与状态查询必须进入统一控制面，而不是散落在日志和底层脚本中；同时应提供 runtime diagnostics console，汇总 health、logs、event stream、usage/cost、provider/model 与 work graph 状态
- `project/workspace` 至少要有最小 asset manifest 能力（upload / list / inspect / bind）；真正的 file browser / editor / diff 可以延后到 M4，但 M3 不能只有概念没有挂载点
- 高级 Memory engine 必须服从 SoR / Fragments / Vault / WriteProposal 的治理边界，而不是绕过核心设计另起一套记忆模型；并且其索引与召回必须支持 Agent 私有 namespace
- 若 control-plane / ops 入口继续维持当前单 owner、localhost 或 trusted-network 假设，则 Feature 031 的验收与最终报告必须明确写出部署边界；不得默认暗示"可直接公网暴露"
- M3 正式签收前必须完成一次 OpenClaw -> OctoAgent 迁移演练，至少覆盖 project 建立、secret 处理、导入、memory/vault 审计、dashboard 操作与 rollback 记录
- 验收 harness 必须考虑共享 `.venv` 并发 `uv run` 的环境竞争；需要串行化相关步骤或显式使用隔离环境，避免把工具链竞争误判成产品不稳定
- 术语必须收敛：`tool profile`、`auth profile`、`agent profile`、`readiness level` 分开命名；除记忆分区外不再使用裸 `profile`

M3 核心对象关系（2026-03-08 补充）：

| 对象 | 归属 / 作用域 | 主要承载 | 默认继承来源 | 说明 |
|------|---------------|----------|--------------|------|
| `Project` | 主 Agent / Worker 共同拥有的一级产品对象 | instructions、memory bindings、secret bindings、asset bindings、channel/A2A routing、`primary_agent_id`（主负责人） | system defaults | M3 的根隔离单位；每个 Project 同时只有一个活跃 Session（Project ↔ Session 一一对应）；Worker 无合适 Project 时可动态创建 |
| `BehaviorWorkspace` | `system_shared / agent_private / project_shared / project_agent` 四层作用域 | `AGENTS.md / USER.md / TOOLS.md / BOOTSTRAP.md` 等共享规则文件、`IDENTITY.md / SOUL.md / HEARTBEAT.md` 等 Agent 私有文件、`PROJECT.md / KNOWLEDGE.md / instructions/*` 等项目行为文件、可见性、版本、effective source chain | system defaults + agent defaults + project overrides + project-agent overrides | 任意 Agent 的正式行为文件入口，不再围绕 Butler 特殊化 |
| `AgentProfile` | system 或 project 作用域的可复用模板 | persona、instruction overlays、model route、tool profile、capability refs、policy refs、budget defaults | project | 主 Agent / Worker runtime 的静态模板 |
| `WorkerProfile` | project 作用域的可复用模板 | worker role、bootstrap、工具集合、权限集合、能力集合 | project + AgentProfile | WorkerRuntime 的静态模板；Worker 是持久化角色，类似 Agent Zero 的 Agent0 |
| `AgentRuntime` | 严格隶属于一个 project | agent identity、effective config、persona、capability、memory namespace bindings | project + selected profile | 主 Agent 或 Worker 的长期运行实体 |
| `ButlerSession` | 严格隶属于一个 ButlerRuntime | 用户 ↔ Butler 对话、history、queue、focus、rolling summary | project + ButlerRuntime | 当前阶段唯一 user-facing session；与 Project 一一对应 |
| `WorkerSession` | 严格隶属于一个 WorkerRuntime | Butler ↔ Worker 内部对话、worker recency、tool/evidence summary、compaction | project + WorkerRuntime + A2AConversation | 默认 internal-only，不直接面向用户 |
| `DirectWorkerSession` | 严格隶属于一个 WorkerRuntime | 用户 ↔ Worker 直接对话 | project + WorkerRuntime | 后续扩展能力；当前不默认开放 |
| `SubagentSession` | 严格隶属于一个 WorkerSession | Worker ↔ Subagent 临时对话 | Worker 的 Project + WorkerRuntime | Subagent 不拥有 Project，共享 Worker 的 Project 上下文；任务完成后整个 session 可回收 |
| `Automation` | 严格隶属于一个 project | schedule、trigger、target、run history、effective config snapshot | project + selected runtime/profile | 可创建 session 或直接派生 work |
| `Work` | 隶属于一个 ButlerSession / WorkerSession / Automation | delegation graph、owner、children、artifacts、budget、state | session 或 automation | 执行与委派单元，不再兼职承载 Agent 私有会话 |
| `A2AConversation` | 隶属于一个 Work | Butler ↔ Worker / Worker ↔ Subagent 消息往返、context capsule、message lineage | Work + source/target sessions | 多 Agent 运行链的一等对象；Subagent 的 A2AConversation 在任务完成后可归档或删除 |
| `MemoryNamespace` | project 或 agent 作用域 | shared memory / private memory / partition bindings | project 或 agent runtime | 支撑 SoR / Fragments / Vault |
| `RecallFrame` | 单次响应或单次 A2A 交互 | session recency、memory hits、artifact evidence、provenance | AgentSession + MemoryNamespace + Work | "当前问题真正取回了什么"的 durable 证明 |
| `RuntimeHintBundle` | 单次 Butler/Worker/Subagent 响应 | 当前时间、surface、tool availability、confirmed facts、user defaults、最近失败限制、RecentConversation 摘要 | session + project + runtime | 供 Agent 进行 `direct / ask / delegate / best-effort` 判断，而不是让代码写场景树 |

BehaviorWorkspace 设计补充（2026-03-15，2026-03-21 更新）：

- 当前行为文件系统已正式收口为四层：`system_shared -> agent_private -> project_shared -> project_agent`
- 共享层默认文件为：`AGENTS.md`、`USER.md`、`TOOLS.md`、`BOOTSTRAP.md`
- Agent 私有层默认文件为：`IDENTITY.md`、`SOUL.md`、`HEARTBEAT.md`
- Project 共享层默认文件为：`PROJECT.md`、`KNOWLEDGE.md`、`USER.md`、`TOOLS.md`、`instructions/*.md`
- `MEMORY.md` 已从默认 behavior 文件集合中移除；行为文件负责规则，长期事实走 `Memory`，敏感值走 `secret bindings / SecretService`
- `BehaviorWorkspace` 目录已按 project-centered 方式定义：全局共享与 agent 通用人格保留在 `behavior/` 下，某个 project 自己的行为文件、代码、数据、文档、notes、artifacts 都进入 `projects/<project-slug>/`
- 任意 Agent 的 effective context 都必须携带 `project_path_manifest`，明确 `project/workspace/data/notes/artifacts/behavior` 根目录与关键行为文件路径
- subordinate / worker handoff 不得裸转发原始用户问题，必须携带 `project_path_manifest + effective_behavior_source_chain + shared/project instructions summary + agent private identity summary`
- **行为文件生命周期管理**（Feature 063）：BOOTSTRAP.md 支持"完成即不再注入"（双触发：`<!-- COMPLETED -->` 标记 OR 文件删除）；`BehaviorLoadProfile` 按 Agent 角色差异化加载（FULL/WORKER/MINIMAL）；head/tail 截断策略替代硬截断；session 级缓存减少重复 resolve IO；Behavior Compactor 支持 LLM 智能合并 + `<!-- 🔒 PROTECTED -->` 保护标记

**Behavior 磁盘目录树**（2026-03-21 确认）：

```
$PROJECT_ROOT (~/.octoagent)/
├── behavior/
│   ├── system/                           # 系统共享（SHARED）
│   │   ├── AGENTS.md                     # role layer — 行为总约束
│   │   ├── USER.md                       # communication layer — 用户偏好
│   │   ├── TOOLS.md                      # tool_boundary layer — 工具策略
│   │   └── BOOTSTRAP.md                  # bootstrap layer — 初始化引导
│   └── agents/{agent_slug}/              # Agent 私有（AGENT_PRIVATE）
│       ├── IDENTITY.md                   # role layer — 身份与定位
│       ├── SOUL.md                       # communication layer — 价值观与风格
│       └── HEARTBEAT.md                  # bootstrap layer — 运行节奏
├── projects/{project_slug}/
│   └── behavior/                         # 项目共享（PROJECT_SHARED）
│       ├── PROJECT.md                    # solving layer — 项目语境
│       ├── KNOWLEDGE.md                  # solving layer — 知识入口
│       └── agents/{agent_slug}/          # 项目-Agent 覆盖（PROJECT_AGENT）
│           ├── IDENTITY.md / SOUL.md     # 项目维度的 agent 覆盖
│           ├── TOOLS.md / PROJECT.md     # 项目维度的工具/语境覆盖
```

覆盖优先级（9 层，`BEHAVIOR_OVERLAY_ORDER`）：
`default_template < system_file < system_local < agent_file < agent_local < project_file < project_local < project_agent_file < project_agent_local`

**Behavior 工具接口**（2026-03-21 确认）：

- `behavior.read_file` **已删除**——所有 behavior 文件内容已在 system prompt 的 `BehaviorSystem` block 中按 `[file_id]` 标注内联展示，Agent 不需要工具读取
- `behavior.write_file(file_id, content, confirmed)` 接口：Agent 只传 `file_id` 短名（如 `USER.md`），系统根据当前 session 的 agent/project 上下文通过 `resolve_write_path_by_file_id()` 自动解析磁盘路径
- 参考 Agent Zero 的 `behaviour_adjustment` 设计——Agent 不感知文件路径，系统全权处理

**双维度审批模型**（§8.6 已有详述，此处索引）：

- **维度 1：PolicyAction**（`allow / ask / deny`）— 按工具的 `SideEffectLevel` 和当前 `PolicyProfile` 决定
- **维度 2：ApprovalDecision**（`allow-once / allow-always / deny`）— 用户对 `ask` 状态的审批决策
- 三个预置 Profile：`default`（irreversible ask）、`strict`（reversible+irreversible ask）、`permissive`（全放行）
- **待对齐**：`filesystem.*` 工具的路径越界应走 Policy Engine `ASK` 流程，而非工具层硬拒绝

**工具输出截断策略**（§8.5 已有详述，此处索引）：

- 工具层返回尽量完整内容（read 100K / exec 200K / web 100K），由 `LargeOutputHandler` 按上下文窗口 50% 统一管理
- 超阈值时 Head + Tail 智能截断，标记引导 LLM 用 offset/limit 分段重读
- 参考 OpenClaw `tool-result-truncation.ts`（30%上下文，400K 硬上限）+ Agent Zero（工具全量返回，上下文压缩在历史管理层）

交付：从"能力齐全的 Agent 系统"推进到"普通用户 Ready 的 Personal AI OS"——新用户可一键安装并完成统一向导配置，随后在 Web 管理台完成渠道接入、审批、恢复和 Memory 浏览。

验收标准：

- 新机器从安装脚本或 App 入口开始，在 10 分钟内完成安装、统一向导配置、dashboard 打开和首条消息验证；过程中不要求用户手工维护多处环境变量
- 升级路径支持 doctor/migrate/preflight，失败时可给出回滚或恢复建议；用户可从 CLI 或 Web 发起一键升级
- 用户可以创建 / 选择 / 切换 project，并让 project 统一承载 instructions、memory mode、secrets bindings、knowledge/files、channel/A2A routing
- 用户可以为 project 选择默认 `AgentProfile` / `WorkerProfile`，并让 runtime / session / automation / work 展示继承后的 effective config；跨 project 切换时不得串用 secrets、memory 或 profile
- Butler 与 Worker 的每次实际响应都必须消费各自的 profile/bootstrap/recent summary/memory retrieval 形成的 context frame，而不是只基于当前一句话
- 用户可以在 Web 或 CLI 中查看并编辑当前 project 的核心 behavior files（至少 `AGENTS.md / USER.md / PROJECT.md / TOOLS.md`），并看到每次运行的 effective behavior source
- 当前阶段 Web 已把行为文件管理入口收口到 `Agents` 页的 `Behavior Center`；CLI 提供 `octo behavior ls/show/init/edit/diff/apply --agent ...` 作为 canonical 管理入口
- Telegram 与 Web 都可以完成最基本的控制命令：approve、model 切换、skill 调用、subagent/work 控制、状态查询
- Web 管理台可以完成 provider/channel 配置、device pairing、agents / memory / permissions / secrets 管理、任务查看、backup/restore dry-run、memory 浏览与证据追溯，不再依赖终端作为唯一操作面
- 用户可以在正式的 session/chat center 中完成 ButlerSession / WorkerSession 的 history/export、queue、focus/unfocus、reset/new、interrupt/resume 等日常会话操作
- 用户可以创建 recurring automation / scheduler job，查看 run history，并把任务明确绑定到某个 project / channel / target
- Project 至少提供 asset manifest 的 upload / list / inspect / bind 路径，使 knowledge/files/artifacts 能稳定挂载到 project，而不是只停留在目录约定
- runtime diagnostics console 可以查看 health、logs、event stream、provider/model 状态、usage/cost、worker/subagent/work graph 执行态与最近失败原因
- Vault 分区默认不可检索，授权后可查且带证据链
- 多模态记忆、Category、ToM 等高级能力通过 Memory backend 提供，其输出必须可追溯、可审核，并通过 SoR/WriteProposal 治理落盘
- Butler 能创建/管理/合并 Work，能把 Work 派发给 Worker / Subagent / ACP-like runtime / Graph Agent，且整条委派链可审计、可中断、可降级
- Butler ↔ Worker 的委派链必须能在控制台中看到 `A2AConversation + A2AMessage + WorkerSession + RecallFrame`，而不是只有 `WORKER_DISPATCHED`
- 默认行为判断必须由 `behavior files + runtime hints + agent decision` 形成主路径；代码只保留治理、权限和审计护栏，不得继续把天气/推荐/排期等场景扩张为硬编码分类树
- automation 触发的 work 必须保留其继承来源（project / agent profile / budget / target），并能在控制台与事件链中解释"为什么使用这套配置"
- ToolIndex 向量检索精度满足 top-5 命中率 > 80%，Skill Pipeline 可 checkpoint + 可回放 + 可中断（HITL），多 Worker 派发策略可解释且失败可降级回单 Worker 路径
- Feature 031 已补齐 M3 acceptance matrix、deployment boundary、OpenClaw migration rehearsal 与最终 release report；结合 Feature 033 关闭 `GATE-M3-CONTEXT-CONTINUITY` 后，M3 现已按 user-ready 版本签收

### M3 Carry-Forward（Feature 033）：Agent Profile + Bootstrap + Context Continuity

- 目标：把 `AgentProfile`、owner basics、bootstrap、recent session summary 和 long-term memory retrieval 真正接进 Butler 与 Worker 的运行链
- 这不是 M4 体验增强，而是当前多 Agent 系统"是否像长期助手组织而不是 stateless router + tools shell" 的基础门槛
- 当前状态：Butler 主链已大体补齐；Worker 侧的 session continuity / private memory / recall parity 仍未完成，因此 033 的"全 Agent 完成态"仍需后续补位
- `GATE-M3-CONTEXT-CONTINUITY` 仅对主 Agent 路径关闭；从 2026-03-13 起，新增 `GATE-M4-AGENT-RUNTIME-CONTINUITY`

### M3 Carry-Forward（Feature 038）：Agent Memory Recall Optimization

- 目标：把 Agent Memory 从 `chat import / fragment & SoR 写入 / backend resolve / runtime recall / built-in tool` 收敛为同一条 `project shared + agent private + work evidence` 主链
- 已完成项：`MemoryService.recall_memory()`、`MemoryRecallHit/Result`、`ContextFrame.memory_recall provenance`、`memory.recall` built-in tool、`ChatImportService` runtime resolver 接线
- 已完成项：delayed recall durable carrier、`MEMORY_RECALL_*` events/artifacts、Control Plane recall provenance 可视化、内建 `keyword_overlap post-filter + heuristic rerank` hooks
- 2026-04-05 架构整治新增：多 scope 并行 recall（asyncio.gather）、`memory_recall_completed` 可观测日志、recall hooks 拆为 MemoryRecallService 独立模块
- 吸收了 Agent Zero 的 project-scoped memory 隔离经验，也吸收 OpenClaw 的 session-key / compaction / recall ordering 思路，但当前实现仍缺 `agent private namespace + worker recall runtime`
- 038 的完成态定义被上调：backend resolver 必须进入 Butler 与 Worker 的真实运行链，并能按 namespace / agent / session 维度审计 recall 质量与 provenance

### M3 Carry-Forward（Feature 067）：Session-Driven Memory Pipeline

- 目标：替换旧的 per-event compaction-flush fragment 创建，改为 session 级别 LLM 引导的结构化记忆提取
- 已完成项：`SessionMemoryExtractor`（cursor-based 增量提取、LLM 结构化输出、fast_commit 快速写入、scope 自动注册）
- 已完成项：LITELLM_MASTER_KEY 自动注入、Qwen3 thinking 模式兼容（enable_thinking=false + _build_result reasoning_content fallback）
- 已完成项：turn 数量截断（_MAX_TURNS_PER_EXTRACTION=50）、失败时 cursor 推进（防死循环）、partition 映射表（枚举对齐 + personal→PROFILE 别名）
- 已完成项：Memory Console scope fallback（list_scope_ids 查所有有数据的 scope）、scope 自动注册为 PROJECT_SHARED namespace
- JSON 解析容错：markdown code block 剥离 + JSON object 拆包 + 正则提取 fallback

### M4（引导式工作台 / Setup Governance / Runtime Safety / Supervisor）

- 本阶段聚焦 032 之后这一轮"可用性 / 串联 / 安全性 / 三层结构"升级，不再把语音、companion、通知中心混在当前里程碑里
- 033 与 038 均已作为 M3 carry-forward 完成；它们服务 M4，但不改写当前 M4 feature 编号面
- [x] Feature 032：OpenClaw Built-in Tool Suite + Live Runtime Truth（built-in tool catalog、graph/subagent live runtime、child work split/merge、control plane runtime truth）
- [x] Feature 034：主 Agent / Worker 上下文压缩（cheap/summarizer 驱动，artifact/evidence 可审计，Subagent 排除）
- [~] Feature 035：Guided User Workbench + Visual Config Center（`Home / Chat / Work / Memory / Settings / Advanced` 已落地；已接入 setup readiness、worker review/apply、context degraded 提示，以及 `memory -> operator -> export/recovery` guided 主路径；`/memory` 已补齐用户态 display model、internal writeback 过滤与派生信息可读化；仍待更细粒度 context evidence）
- [x] Feature 036：Guided Setup Governance（`setup-governance / policy-profiles / skill-governance / setup.review / setup.apply / agent_profile.save / policy_profile.select / skills.selection.save` 已落地；CLI/Web 已汇流到 canonical setup review/apply 语义；Memory 配置已简化为 `local_only` 单一模式）
- [x] Feature 037：Runtime Context Hardening（runtime lineage、selector drift、session authority 收口）
- [x] Feature 039：Supervisor Worker Governance + Internal A2A Dispatch（已完成 supervisor-only 主 Agent、`workers.review`、`worker.review/apply`、message-native A2A roundtrip 与 durable `A2AConversation / A2AMessage / WorkerSession`）
- [x] Feature 040：M4 Guided Experience Integration Acceptance（已形成 M4 acceptance matrix / release gate report，并打通 `setup -> workbench -> chat -> worker review/apply -> memory/operator/export/recovery` 主链；033/036 blocker 已关闭）
- [x] Feature 041：Butler / Worker Runtime Readiness + Ambient Context（已补齐当前本地时间/日期、Butler-owned freshness delegation 主链、缺城市显式追问、backend unavailable 降级、worker private recall runtime、message-native 返回链与 runtime truth surface）
- [x] Feature 049：Butler Behavior Workspace & Agentic Decision Runtime（已完成初版 `BehaviorWorkspace + RuntimeHintBundle + session-backed RecentConversation + ButlerDecision preflight` 主链，补齐 Web/CLI 的初始行为文件视图与 CLI `octo behavior ls/show/init/edit/diff/apply`；其 scope 仍以 `system/project` 为起点，后续多 Agent parity、project-centered 目录、bootstrap 模板与 `Agents` 行为中心收口到 055）
- [x] Feature 055：Agent Behavior Scope Reset & Behavior Center（已完成四层 `BehaviorWorkspaceScope`、project-centered 路径解析、`project_path_manifest` 与 `storage_boundary_hints` 注入、bootstrap 模板与默认会话 Agent 用户画像/个性引导 contract、`octo behavior --agent ...`、`Agents` 页的 Behavior Center 与 `Settings` 行为入口迁移）
- [x] Feature 071：Session Owner / Execution Target Separation（已完成 `session owner / turn executor / delegation target / inherited context owner` 语义拆分；`Profile + Project` 只决定先和谁说话；默认主会话与 direct worker 会话可并存；`worker -> worker` 已被硬禁止，并补齐历史污染会话 reset/兼容链）
- [x] Feature 051：Session-Native Agent Runtime & Recall Loop（`behavior budget + ToolUniverseHints` 已落地；`AgentSession` 除正式 `recent_transcript / rolling_summary` 外，已补齐 `AgentSessionTurn` store，`user / assistant / tool_call / tool_result / context_summary` 会落到 `agent_session_turns`，`RecentConversation / session.export / session.reset` 都优先消费该 store；控制面已新增 `session.new / session.reset / session.unfocus`，Session Center 已提供 `全部 / 运行中 / 队列 / 历史` lane 视图；Butler chat 默认切到 `agent-led hint-first` memory runtime，并已把 `ButlerDecision + RecallPlan` 收口为统一 `ButlerLoopPlan`；Worker 默认切到 planner-capable `hint-first` runtime，仅在显式 profile override 下保留 `detailed_prefetch`；`AgentSessionTurn` 现在还会生成正式 replay/sanitize 投影，并进入预算驱动裁剪链；默认 `single_loop_executor` 已从 general Butler 扩到显式 `research/dev/ops` worker lens，主模型调用直接带着 profile-first 工具集进入 `LLM + SkillRunner` 工具循环，不再额外触发 `butler-decision` 或 `memory-recall-planning` 辅助 phase；当高级 Memory backend 可用时，`MemorySearchOptions` 会把 `expanded_queries / focus_terms / rerank_mode / post_filter_mode` 下发到高级 backend search path；compatibility fallback 已收缩为 guardrail，仅保留天气缺地点边界与天气 follow-up 恢复语义）
- [x] Feature 052：Trusted Tooling Surface & Permission Relaxation（trusted local baseline 已把 `general / research / dev / ops` 默认 permission preset 收口到 `NORMAL`；MCP provider 已支持 `mount_policy=explicit|auto_readonly|auto_all`，其中 `auto_readonly` 默认自动挂载 `minimal` 工具；Skill provider 已支持 `permission_mode=inherit|restrict` 且默认 `inherit`；runtime metadata / control plane 已同步暴露 `recommended_tools + mounted_tools + blocked_tools`，`selected_tools_json` 退化为 recommended mirror；危险动作仍继续走 ToolBroker / Policy / Approval / Audit 主链）
- [x] Feature 054：Builtin Memory Engine & Shared Retrieval Platform（`local_only` 已升级为内建 Memory Engine，默认优先使用本地 `Qwen3-Embedding-0.6B`，不可用时回退到双语 hash embedding；`memory_reasoning / memory_expand / memory_embedding / memory_rerank` 已接入 Settings / CLI / runtime；`EmbeddingProfile / IndexGeneration / IndexBuildJob / CorpusKind` 已形成共享 retrieval platform contract，Memory 与未来 knowledge base 共用 generation lifecycle；embedding 迁移已支持后台 build、进度展示、cutover / cancel / rollback，且迁移期间旧 generation 持续服务；facts / Vault 候选仍走 proposal / commit / grant / audit 治理链）
- [x] Feature 053：Session-Scoped Project Activation（对齐 Agent Zero 的 `each chat/context has its own active project` 语义；**Project ↔ Session 一一对应**——每个 Project 同时只有一个活跃 Session，每个 Session 锁定一个 Project；`session.new` 现在会冻结当前 `project_id/workspace_id` 并形成待消费的新会话 snapshot；chat 首条消息会透传 token + project/workspace 并写入 `workspace:<workspace_id>:chat:<channel>:<thread_id>` durable scope；`session.focus / session.reset` 会恢复目标会话自己的 project/workspace 到 control-plane selector；Web `useChatStream / ChatWorkbench` 也已支持 pending snapshot 的刷新恢复，不再把新会话 project 绑定退回 surface-selected selector）
- [x] Feature 058：MCP Install Lifecycle & Session Pool（MCP server 完整安装生命周期管理：npm/pip 一键安装向导、安装注册表持久化 `mcp-installs.json`、McpSessionPool 持久连接池（auto-reconnect + health check）、McpInstallerService 异步安装任务与子进程 env 隔离、control plane 新增 `mcp_provider.install / install_status / uninstall` 三个 action、前端 McpInstallWizard 五步安装向导；MCP 工具继续走 ToolBroker / Policy / Audit 主链，McpServerConfig 与 McpRegistryService 保持不变仅扩展）
- [ ] Feature 050：Agent Management Simplification（把 `Agents` 收口为"当前项目主 Agent + 已创建 Agent 列表 + 模板创建流"，并将结构化编辑控件替代技术字段编辑主路径）
- [ ] Feature 063：Behavior File Lifecycle & Smart Loading（Bootstrap 双触发完成检测、BehaviorLoadProfile 差异化加载（FULL/WORKER/MINIMAL）、head/tail 截断策略、session 级缓存、Behavior Compactor LLM 智能合并）
- [x] Feature 070b：工具系统简化重构（check_permission 单函数替代三套 Hook 体系；PathAccessPolicy 黑名单拦截）
- [x] Feature 071：Session Owner / Execution Target Separation（session owner / turn executor / delegation target 语义拆分完成）
- [x] Feature 072b：Core/Deferred 工具分层接通（LLM 首轮 tools schema 从 56 降到 9；tool_search 提升链路连通）
- [x] Feature 073：Deprecated 残留全面清理（ToolProfile 枚举 + Workspace 概念 + Butler 遗留命名全部清除）

### M4 当前状态（2026-04-06 更新）

**已完成**（约 20 个 Feature）：032-041, 048-049, 051-053, 055, 058, 070b, 071, 072b, 073

**进行中**：
- 056: clean-install-skeleton-bootstrap
- 070: direct-agent-chat-closure
- 071b: align-llm-config-flow

**M4 完成标准**：上述 3 个进行中 Feature 完成后签收。

M4 约束：

- M4 能力必须建立在 M3 的 project、session、automation、runtime console 之上，不得倒逼重做核心产品对象
- 上下文压缩与 runtime lineage 类能力必须优先作用于主 Agent / Worker 的真实运行链，并保留 artifact/event/evidence 审计链
- 主 Agent 默认仍是 supervisor 与最终责任人；但在治理允许且任务有界时，可以直接使用已挂载的受治理工具。只有在并行、专业化、权限隔离或上下文隔离明显更优时，才优先委派给 `research / dev / ops / subagent / graph`
- 若系统已经具备 delegated `web.search / web.fetch / browser.*` 路径，则 Butler/Worker 必须把"实时/外部事实问题"优先解释为可治理 delegation，而不是直接退回"没有实时能力"的静态回答
- 默认行为主路径必须来自显式 `BehaviorWorkspace` 与 `RuntimeHintBundle`，由 Butler 产出结构化决策；天气、推荐、排期等问题不得继续扩张为硬编码分类树
- 049 负责"显式行为文件 + 决策 contract"；051 负责把 session、memory、tooling 真正推进到 agent-native 主链，不得继续把这部分长期停留在 control-plane 预取或弱引用 reconstruction
- 052 负责把默认工具面从"默认不给工具"收口到"默认给足可逆标准工具、危险动作单独 gate"；后续不得再把 MCP / Skills 的默认配置退回到 `minimal + explicit enable everything`
- 053 负责把 project 激活从 `surface-selected` 收口到 `session-scoped snapshot`；`/new`、focus、reset、首条消息 scope 与续聊恢复不得再依赖全局 surface selector 漂移
- deterministic 兼容路径必须显式标记为 compatibility fallback，并在 work / request metadata 中暴露 provenance，避免回退路径再次变成隐藏主路径
- live dispatch 必须真的经过 `ButlerSession -> A2AConversation -> WorkerSession` 的 message-native 主链，并保留 runtime context / work lineage；不能只有 A2A adapter 或测试样例
- 每个 Agent 都必须拥有完整上下文管理：session、Memory namespaces、recall、persona、project markdown、policy/tool/auth context 与 scratchpad
- `WorkerSession` 不得退化为纯运行态结构（如 loop_step/max_steps/tool_profile）；它必须是完整的对话/记忆/召回承载体
- `AgentSession` 不得长期停留在 `recent_turn_refs -> task/event reconstruction` 形态；recent conversation、follow-up、compaction/export 必须逐步收口到 transcript-native session
- memory 主链不得长期停留在 system-prefetch-only 模式；051 后默认应支持 agent-led recall，控制面只保留 namespace、预算、审计与降级策略
- 工作台/图形化配置类能力必须优先复用 015 wizard、026 control-plane canonical API、027 memory console、030 delegation/runtime truth、033 context provenance 与 034 compaction status，不得新造平行 backend
- 初始化配置/权限治理类能力必须优先复用 015 onboarding、025 wizard/session、026 control-plane actions/resources、030 capability/MCP runtime truth 与 035 workbench 设置入口；不得让 Web 与 CLI 各维护一套 setup 语义
- 035/036/040 必须显式处理 `context_continuity` 的实际运行状态；若未来再次 degraded，不能把缺失的上下文连续性静默隐藏
- 本轮从架构纠偏到实现落地的正式执行顺序与升级波次事实源，见 `docs/milestone/m4-feature-split.md`

### M5（文件工作台 / 语音多模态 / Companion / Attention）

- [ ] 文件/工作区工作台（file browser / editor / diff / git-aware workspace inspector）
- [ ] 语音与多模态交互表面（STT / TTS / voice session / richer multimodal chat surfaces）
- [ ] Progressive Web App / companion surfaces / remote tunnel polish
- [ ] 更完整的通知中心与 attention model（提醒、升级提示、后台任务完成通知、多端同步提示）

M5 说明：

- 这些内容原先放在 M4，但在当前升级波次里不是阻塞用户可用、也不是阻塞三层结构成立的核心项
- M5 建立在 035/036/039/040 收口完成之后推进，避免继续把"入口闭环"和"未来表面增强"混在同一阶段

---
