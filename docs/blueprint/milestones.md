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

- [x] 历史交付：接入 LiteLLM Proxy + 运行时 alias group 配置（cheap/main/fallback）+ 语义 alias 映射 — Feature 002 已交付，后由 ProviderRouter direct 退役
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
- 历史验收：OpenAI/OpenRouter API Key → credential store → LiteLLM Proxy → 真实 LLM 调用成功；当前改由 ProviderRouter direct 验收
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
- [x] Feature 009：Worker Runtime（历史交付范围曾写作 Docker；当前真实实现为 Free Loop + Inline/Graph + timeout/cancel，未实现 Docker 隔离 backend）
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
- [x] Feature 019：历史任务名为 JobRunner docker backend + 交互式执行控制台；当前只保留执行控制台与 runtime 接口，Docker backend 未实现且不再宣称交付
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
- 历史 M2 验收曾要求 JobRunner 在 Docker 内执行；该项未形成真实 backend，当前只验收日志流、取消与可选人工输入，隔离执行必须在后续独立能力具备真实实现后重新验收
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
- `behavior.write_file(file_id, content, confirmed)` 接口：Agent 只传 `file_id` 短名（如 `USER.md`），系统根据当前 session 的 agent/project 上下文通过 `resolve_write_path_by_file_id()` 自动解析磁盘路径。**F136 起 `confirmed=true` 不再是 LLM 自证**——REVIEW_REQUIRED 文件写入经服务端 ApprovalGate（审批卡片含 unified diff，用户在 Web/Telegram 批准后才落盘；每次写独立审批、不入 session allowlist）
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

### M3 Carry-Forward（Feature 033）：Agent Profile + Bootstrap + Context Continuity ✅ 已完成（M5 阶段 1 关闭）

- 目标：把 `AgentProfile`、owner basics、bootstrap、recent session summary 和 long-term memory retrieval 真正接进 Butler 与 Worker 的运行链
- 这不是 M4 体验增强，而是当前多 Agent 系统"是否像长期助手组织而不是 stateless router + tools shell" 的基础门槛
- **2026-05-25 关闭状态**：F093-F096 Worker 完整对等 4 维（Session / Memory / Behavior / Recall Audit）全部交付，Worker 侧的 session continuity / private memory / recall parity 已与主 Agent 对等
- `GATE-M3-CONTEXT-CONTINUITY` ✅ 全 Agent 路径关闭（M5 阶段 1）
- `GATE-M4-AGENT-RUNTIME-CONTINUITY` ✅ 关闭（F093-F096）

### M3 Carry-Forward（Feature 038）：Agent Memory Recall Optimization ✅ 已完成（M5 阶段 1 关闭）

- 目标：把 Agent Memory 从 `chat import / fragment & SoR 写入 / backend resolve / runtime recall / built-in tool` 收敛为同一条 `project shared + agent private + work evidence` 主链
- 已完成项：`MemoryService.recall_memory()`、`MemoryRecallHit/Result`、`ContextFrame.memory_recall provenance`、`memory.recall` built-in tool、`ChatImportService` runtime resolver 接线
- 已完成项：delayed recall durable carrier、`MEMORY_RECALL_*` events/artifacts、Control Plane recall provenance 可视化、内建 `keyword_overlap post-filter + heuristic rerank` hooks
- 2026-04-05 架构整治：多 scope 并行 recall（asyncio.gather）、`memory_recall_completed` 可观测日志、recall hooks 拆为 MemoryRecallService 独立模块
- **2026-05-09 M5 阶段 1 关闭**：F094 引入 `AGENT_PRIVATE` namespace（仅 Worker 路径生效，main direct 保留 PROJECT_SHARED）；F096 引入 `list_recall_frames` audit endpoint + `MEMORY_RECALL_COMPLETED` 同步路径 emit；AC-7b 四层 audit chain（profile_id↔runtime_id↔LOADED.agent_id↔RecallFrame）实测通过
- 038 的完成态：backend resolver 已进入主 Agent 与 Worker 的真实运行链，可按 namespace / agent / session 维度审计 recall 质量与 provenance（F096 endpoint 暴露）

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
- [x] Feature 050：Agent Management Simplification（`Agents` 页已收口为"当前项目主 Agent + 已创建 Agent 列表 + 模板创建流"，结构化编辑控件已替代技术字段编辑主路径；经代码核查确认已实现）
- [x] Feature 063：Behavior File Lifecycle & Smart Loading（Phase 1/2 已完成：Bootstrap 双触发完成检测、BehaviorLoadProfile 差异化加载（FULL/WORKER/MINIMAL）、head/tail 截断策略、session 级缓存；Phase 3 Behavior Compactor LLM 智能合并推迟到 M5，M4 仅保留 FR-7 运行时监控指标）
- [x] Feature 070b：工具系统简化重构（check_permission 单函数替代三套 Hook 体系；PathAccessPolicy 黑名单拦截）
- [x] Feature 071：Session Owner / Execution Target Separation（session owner / turn executor / delegation target 语义拆分完成）
- [x] Feature 072b：Core/Deferred 工具分层接通（LLM 首轮 tools schema 从 56 降到 9；tool_search 提升链路连通）
- [x] Feature 073：Deprecated 残留全面清理（ToolProfile 枚举 + Workspace 概念 + Butler 遗留命名全部清除）
- [x] Feature 074：Dead Route 修复与 Echo Mode 警示（`/work` 路由注册到 `TaskList`；TaskList 标题/加载态/空态全部中文化）
- [x] Feature 075：UI 多语言一致性（ApprovalCard 按钮中文化；导航标签 Agents→智能体 / Skills→技能 / Memory→记忆 / Settings→设置；全部加载/空态文案统一中文；22 个文件 75 处文案修正）
- [x] Feature 076：新会话流程简化（单 Agent 场景直接创建跳过 Modal；多 Agent 默认选中当前 Agent；会话名预填"对话 MM-DD HH:mm"格式）
- [x] Feature 071b：Align LLM Config Flow（Slice A/B/D/E 核心已实现 + Slice C 文案对齐完成；config.sync/add_provider/set_model_alias description 明确事实源关系；CLI help 与 SKILL.md v2.0.0 对齐；Slice D 高层工具暴露推迟到 M5）

### M4 当前状态（2026-04-06 更新）

**已完成**（30 个 Feature）：032-041, 048-056, 058, 063, 070, 070b, 071, 071b, 072b, 073, 074, 075, 076

**M4 签收状态**：全部 Feature 已完成。071b Slice D 高层工具暴露 + 063 Phase 3 Behavior Compactor 推迟到 M5。

M4 约束：

- M4 能力必须建立在 M3 的 project、session、automation、runtime console 之上，不得倒逼重做核心产品对象
- 主 Agent 默认仍是 supervisor 与最终责任人；治理允许且任务有界时可直接使用受治理工具，仅在并行/专业化/隔离明显更优时才委派 Worker/Subagent
- 若系统已具备 delegated `web.search / web.fetch / browser.*` 路径，主 Agent/Worker 必须把"实时/外部事实问题"优先解释为可治理 delegation，而非退回"没有实时能力"
- 默认行为主路径来自 `BehaviorWorkspace` 与 `RuntimeHintBundle`，由主 Agent 产出结构化决策；不得继续扩张硬编码分类树
- 兼容路径必须显式标记为 compatibility fallback，在 work/request metadata 中暴露 provenance
- live dispatch 必须经过 `MainAgentSession -> A2AConversation -> WorkerSession` 的 message-native 主链，保留 runtime context / work lineage
- 每个 Agent 必须拥有完整上下文管理：session、Memory namespaces、recall、persona、project markdown、policy/tool/auth context 与 scratchpad
- UX 收尾（074-076）以用户可感知的体验改进为目标，不引入新的后端架构变更
- 本轮执行顺序与升级波次事实源，见 `docs/milestone/m4-feature-split.md`

### M5（架构债清理 + Worker 完整对等性 + 委托模式分离 + 用户感知 ROI）✅ 已完成（2026-05-25）

> **决策来源**（2026-05-05 拍板）：架构战略评估 + Worker vs 主 Agent 实测 + 架构债 review（对照 Hermes Agent / OpenClaw / Agent Zero）。
> **哲学**：**先治本再扩张**——M5 是大重构，把 H2 完整对等性 + H3 委托模式两路分离 + 架构债一并清；M6 才开始 surface 扩张。
> **实施详情索引**：CLAUDE.local.md §"M5 / M6 战略规划"（13 Feature 完整实施记录 + 架构债 D1-D14 映射）。
> **三条核心设计哲学**：详见 [agent-collaboration-philosophy.md](agent-collaboration-philosophy.md)（H1 管家 mediated / H2 完整对等性 / H3 两种委托模式）。

#### M5 整体完成状态

13 个 Feature 全部交付，分 4 阶段串行/并行推进。F084-F088 是 M5 启动前的基础设施修复（与 M5 同期落地，详见 §"M5 后续修复"段）。

| 阶段 | 主题 | 范围 | 状态 |
|------|------|------|------|
| 阶段 0 | 架构债前置清理（必须严格串行） | F090-F092 | ✅ |
| 阶段 1 | Agent 完整上下文栈对等 | F093-F096 | ✅ |
| 阶段 2 | 委托模式两路分离 | F097-F100 | ✅ |
| 阶段 3 | 用户感知 ROI + 文档 | F101-F103 | ✅ |

#### 阶段 0：架构债前置清理（F090-F092，严格串行）

| Feature | Commit | 一句话目的 |
|---------|--------|-----------|
| **F090 Type System & Naming Cleanup** | 2026-05-06 | metadata flag → `RuntimeControlContext` 显式字段（双轨）；`AgentProfile + kind` 字段（WorkerProfile 类保留，完全合并推迟 F107）；`WorkerSession` → `WorkerDispatchState` 重命名（dispatch 瞬时状态） |
| **F091 State Machine Unification + F090 残留** | 2026-05-06 | 3 个状态枚举（TaskStatus / WorkerRuntimeState / WorkStatus，WorkerExecutionStatus 实测不存在）建跨枚举映射函数；MERGED/ESCALATED/DELETED 显式 raise ValueError；F090 D1 读取端 4 处真实 reader 切换 |
| **F092 DelegationPlane Unification** | 2026-05-08 | 主路径 `plane.spawn_child` 统一 spawn 编排入口；DelegationManager production 构造从 5+ 处 → 1 处；3 条豁免路径显式归档（`apply_worker_plan` / `work.split` / `spawn_from_profile`）；`SpawnChildResult` 三态 + `emit_audit_event` 参数 |

#### 阶段 1：Agent 完整上下文栈对等（F093-F096）

| Feature | Commit | 一句话目的 |
|---------|--------|-----------|
| **F093 Worker Full Session Parity** | 2026-05-09 | Worker turn 写入 + rolling_summary / memory_cursor 字段 + `AGENT_SESSION_TURN_PERSISTED` 事件；agent_context.py 4112→4008 行（D6 拆分） |
| **F094 Worker Memory Parity** | 2026-05-09 | `AGENT_PRIVATE` namespace 仅 Worker 路径生效（main direct 保留 PROJECT_SHARED，完整对等留 F107）；RecallFrame 用 `agent_runtime_id`（不是 `agent_id`）；废弃 WORKER_PRIVATE 路径 |
| **F095 Worker Behavior Workspace Parity** | 2026-05-09 | `_PROFILE_ALLOWLIST[WORKER]` 5 → 8 文件：`{AGENTS, TOOLS, IDENTITY, PROJECT, KNOWLEDGE, USER, SOUL, HEARTBEAT}`（去 BOOTSTRAP 加 USER，**用户 GATE_DESIGN v0.2 翻转**）；修复 envelope 双过滤剥离 IDENTITY 的 baseline 隐性 bug；SOUL.worker.md / HEARTBEAT.worker.md 模板 |
| **F096 Worker Recall Audit & Provenance** | cc64f0c | 阶段 1 收尾整合：list_recall_frames audit endpoint + MEMORY_RECALL_COMPLETED 同步路径 emit + BEHAVIOR_PACK_LOADED EventStore 接入 + BEHAVIOR_PACK_USED 新增；AC-7b 四层 audit chain（profile_id↔runtime_id↔LOADED.agent_id↔RecallFrame） |

#### 阶段 2：委托模式两路分离（F097-F100，依赖阶段 0 + 1）

| Feature | Commit | 一句话目的 |
|---------|--------|-----------|
| **F097 Subagent Mode Cleanup**（H3-A） | 4441a5a | 显式建模 `SubagentDelegation` Pydantic model + ephemeral AgentProfile (kind=subagent) + `SUBAGENT_INTERNAL` session 路径 + cleanup hook + `SUBAGENT_COMPLETED` event；Memory α 共享引用（caller AGENT_PRIVATE） |
| **F098 A2A Mode + Worker↔Worker**（H3-B） | c2e97d5 | A2A source+target 双向独立加载；**删除 `_enforce_child_target_kind_policy`（关闭 D14 Worker↔Worker 硬禁止）**；新增 `CONTROL_METADATA_UPDATED` event 解决 USER_MESSAGE 复用污染；ephemeral runtime 独立路径；BaseDelegation 公共抽象提取；orchestrator.py 3623→2733 行（D7 拆分 A2ADispatchMixin） |
| **F099 Ask-Back Channel + Source Generalization** | 049f5aa | 三工具引入：`worker.ask_back` / `worker.request_input` / `worker.escalate_permission`；`source_runtime_kind` 5 值枚举（MAIN / WORKER / SUBAGENT / AUTOMATION / USER_CHANNEL）；is_caller_worker resume 通过 CONTROL_METADATA_UPDATED 持久化 |
| **F100 Decision Loop Alignment**（H1） | 182e9ed | `RuntimeControlContext.force_full_recall: bool = False`（H1 override 字段）；`RecallPlannerMode="auto"` 实际语义启用（按 delegation_mode 自动决议）；F090 D1 双轨彻底收尾（移除 orchestrator metadata 写入 + helper fallback） |

#### 阶段 3：用户感知 ROI + 文档（F101-F103）

| Feature | Commit | 一句话目的 |
|---------|--------|-----------|
| **F101 Notification + Attention Model** | 74c9ab3 | NotificationService 四级优先级（CRITICAL/HIGH/MEDIUM/LOW）+ quiet hours discard + USER.md SoT + dismiss 跨通道统一 + Telegram callback + Web API + sha256 notification_id；`NOTIFICATION_DISPATCHED` 新 EventType；`WAITING_APPROVAL` 状态机改造（task_runner 单 owner + CAS + 双注册）；ApprovalGate SSE production 接入；force_full_recall producer |
| **F102 Proactive Followup（Hermes Routine）** | 9185862 | `DailyRoutineService` cron 触发 + 9 步执行 + LLM/fallback 双路径 + LLM token budget 截断（max_input ≤ 2000 字符 + max_output ≤ 512 token）；4 新 EventType（ROUTINE_TRIGGERED/COMPLETED/FAILED/SKIPPED）挂在 `_daily_routine_audit` task；USER.md +3 机器可读字段（daily_summary_time / routine_active / summary_channels）；F101 NotificationService.notify_task_state_change 加 channels 可选参数 |
| **F103 Blueprint v0.1 Incremental 修订** | （本 Feature） | 同步 F084-F102 到 Blueprint 5 子文档 + 新增 §"Agent 协作三条设计哲学"独立章节 + 关闭架构债 D13（三层消息模型文档） |

#### M5 后续修复（与 M5 同期落地的 F081-F088 基础设施）

- **Feature 081（LiteLLM 完全退役）** ✅: ProviderRouter 直连替代 LiteLLM Proxy；migrate-080 双对象迁移。详见 [codebase-architecture/provider-direct-routing.md](../codebase-architecture/provider-direct-routing.md)
- **Feature 082（Bootstrap & Profile Integrity）** ⚠️→F084 退役：F082 因发现 bootstrap 状态机本身的设计缺陷被 F084 整体替代（净删 ~2400 行 dead code）
- **Feature 083（测试并发加速）** ✅（务实版本）：修 thread shutdown hang（aiosqlite + asyncio executor）+ 修 fixture `os.environ` 污染 + `attach_input` 测试 race 加严等待。进程退出从 30+ 分钟 hang → ~20s。详见 [codebase-architecture/testing-concurrency.md](../codebase-architecture/testing-concurrency.md)
- **Feature 084（Context + Harness 全栈重构）** ✅（仿 Hermes Agent 模式）：
  - **Harness 层**：中央 ToolRegistry（数据驱动 entrypoints）+ ToolsetResolver + ThreatScanner（17+ pattern + invisible Unicode）+ SnapshotStore（冻结快照 + Live State 二分，保护 prefix cache）+ ApprovalGate（session allowlist + SSE）+ DelegationManager（max_depth=2 / max_concurrent=3）
  - **Context 层**：USER.md 是 SoT，OwnerProfile 退化为派生只读视图；`user_profile.update/read/observe` 三工具 + Memory Candidates API（promote/discard/bulk_discard with atomic claim + skipped_ids）
  - **WriteResult 通用回显契约**：18+ 写工具 return type 强制 WriteResult 子类，注册期 fail-fast；保留 task_id / memory_id / run_id 等关联键不压扁
  - **退役**：BootstrapSession / BootstrapOrchestrator / UserMdRenderer / bootstrap_integrity / bootstrap_commands CLI（净删 ~2400 行 dead code）
  - 详见 [codebase-architecture/harness-and-context.md](../codebase-architecture/harness-and-context.md)
- **Feature 085（capability_pack 拆分）** ✅: 47 个工具 handler 迁移到 `builtin_tools/` 子包，CapabilityPackService 从 5112 → 2138 行（-58%），退化为编排层（详见 architecture-audit.md §14.8 A1）
- **Feature 086（APScheduler 框架增强）** ✅: 为 F102 Routine 提供 cron 注册基础
- **Feature 087（Agent e2e Live Test Suite）** ✅: 替换旧 `test_acceptance_scenarios.py` 5 域循环为 13 能力域 e2e_live 套件；OctoHarness 抽离 4 个 DI 钩子；smoke 5 + full 8；hermetic 隔离（5 类凭证 env + 4 路径 env + 5 module 单例）；pre-commit hook + `octo e2e` CLI 4 模式。详见 [codebase-architecture/e2e-testing.md](../codebase-architecture/e2e-testing.md)
- **Feature 088（Module Singletons）** ✅: 测试 hermetic 隔离单例清单维护（`MODULE_SINGLETONS.md`）

#### M5 → M6 切换 acceptance

M5 acceptance gate 全部关闭：

- F090-F103 acceptance criteria 全部通过（详见各 Feature `.specify/features/<NNN>-*/completion-report.md`）
- F102 baseline (9185862) e2e_smoke 5x 循环 PASS
- 全量回归 ≥ 3571 passed（F102 baseline）+ 0 regression
- 三条设计哲学（H1/H2/H3）已显式建模到代码（H1 force_full_recall / H2 Worker 完整对等性 4 维 / H3 SubagentDelegation + A2A WorkerDelegation 两路）
- 14 条架构债（D1-D14）：12 条已闭环、2 条显式推迟 F107（D2 WorkerProfile 完全合并、D8 control_plane 隐性耦合）

**M6 启动条件已满足。** M6 范围见下文 §"M6（Surface 扩张）"。

#### M5 carry-forward gate 关闭

- `GATE-M3-CONTEXT-CONTINUITY` ✅：Butler 主链 + Worker session continuity（F093）+ Worker memory parity（F094）+ Worker recall audit（F096）全部闭环
- `GATE-M4-AGENT-RUNTIME-CONTINUITY` ✅：F093-F096 Worker 完整对等 4 维全部实现，主 Agent 与 Worker 上下文栈对等

---

### M6（Surface 扩张 F104-F111 + 地基/债务 F112-F122）✅ 完成（2026-06-22）

> **M6 收官（2026-06-22）**：surface F104-F110 全部关闭 + 地基/债务/安全/效率 F112-F126 + D2 合并 F117 全部 ✅；F111 Behavior Compactor 推 M7。**交付**：文件工作台 v0.1+v0.2（git-aware）/ 多平台 4 渠道（Telegram/Web/Slack/Discord）/ Plugin Loader / Capability 大重构（12,328 行 → 18 模块）/ profile 合并 / 安全双向（SSRF + tool 结果扫描）/ 语音（STT+TTS+voice session）。**benchmark 验收 ✅（2026-06-27，本会话）**：控变量 DeepSeek-V3.2 跑出 pass_rate **0.276 = M5 baseline**，回归护栏域（tool_call 100% / connor_real_world 3/4 / snapshot 50%）全守住——**M6 零回归实证**。先决修复 provider 瞬态重试（`52320d7c`，根治 anyio asyncio TLS 读竞态致主 agent 调用偶发退 Echo 假成功；30 task 主调用 0 退 Echo 逐条验证），benchmark 首次可信。**用户拍板：M6 benchmark 闸以零回归收口**——M6 属重构/扩面/安全/语音 milestone，不含解题能力提升，强 model 验证留到 M7 认知深化真有协作/记忆能力提升时再做（避开订阅 OAuth 自动化 benchmark 的 ToS 灰色地带）。下一步：**M7 启动，方向 = 认知深化**（见 §M7）。
> **遗留小项（backlog，非阻塞）**：F106 `test_start_degrades_without_watchdog` pre-existing race flake（隔离过，非回归）；F105 v0.2 H-2 source_channel_id（与 A2A source 泛化一并立项）；F107 git 快照无 EventStore 事件（M3 归档）。

M5 全部关闭后启动。原计划"M6 不做架构债清理"——但 **2026-06-07 调研 + 架构审计 workflow**（31 agent，SDD/开源 agent 调研 + F097-F104 代码审计 + E2E 缺口，全过对抗验证）发现 M5 大重构留有未收口残渣（双轨死代码 + `agent_context.py` 膨胀到 4585 行），用户拍板"**先夯地基再扩张**"：F112-F116 地基 sprint 先于 F105 执行。

> **2026-05-25 端到端 review 重排**（对照 Hermes Agent 抓出真差距）：新增 F105 Multi-Platform Gateway（P0）；原 F105 Companion → F106 改为 User Plugin Loader（社区 skill 扩展基础设施，Companion 推到 M7）；F107 起编号顺延。
>
> **2026-06-07 调研 + 审计后拍板（3 项）**：①先夯地基再扩张（地基 sprint 先于 F105）；②F108 拆分收窄（D2/dismiss/agent_context 拆出为独立 Feature）；③全采纳 4 项 SDD 工作流强化（AC↔test 绑定 / traceability 校验 / living-docs 漂移闸 / 多评审 panel，详见 CLAUDE 工作流规则）。

| Feature | 一句话目的 | 依赖 |
|---------|-----------|------|
| **F104 文件工作台 v0.1**（diff 视图）✅ 完成（49e5108）| artifact_versions 表 + 连接级写隔离 + Files API + DiffView jsdiff；实际动了 backend（artifact 版本历史存储），非纯 UI | F084 |
| **F105 Multi-Platform Gateway v0.1**（新增，P0）**【2026-06-10 范围收窄】** ✅ 完成（2026-06-10，7 commits fast-forward 合入 master edf23f18，用户拍板）| v0.1 = platform_registry + ChannelAdapter 抽象 + 现有 Telegram/Web 改造成 adapter（**行为零变更实证**：3930 passed = baseline 3899 + 31 新增 0 regression，现有 telegram/notification/chat 测试 0 修改，e2e_smoke 8/8）；ConversationBinding 表 + last-route resolver 落地（OC-2/OC-6 地基，v0.1 不接出站）；**H1 构造性保证**：binding 写入面无 agent_profile_id 参数 + direct-worker 会话排除。**抽象诚实边界**（双评审定调）：outbound/通知/生命周期 registry 化，inbound 留 per-platform（v0.2 ingress 契约）。**Slack/Discord 推 v0.2**。Pre-impl 双评审（Codex 4H + Opus 4M）+ Final 双评审全闭环。制品 `.specify/features/105-platform-gateway-v01/`，架构文档 `docs/codebase-architecture/platform-gateway.md` | M5 全闭环 |
| **F105 v0.2 Multi-Platform Gateway**（Slack/Discord + ingress 契约 + 出站接线）✅ 完成（2026-06-12，9 commits fast-forward 合入 master 0b046da4，用户拍板）| ①**ingress 契约**：ChannelAdapter += `inbound_router()`，harness 统一挂载（telegram 挂载迁移行为零变更，EQ-A1~A6 论证）；②**Slack** Events API 全链（v0 HMAC raw-body 验签 + 静态 allowlist deny-all + team_id 边界）；③**Discord** Interactions（Ed25519 验签失败必 401 + slash command，WS Gateway 范围外）；④**出站首次接线**：resolver v2（`last_runtime_active_at` 活跃证据与 kind 解耦）+ 通知 eligibility = DM runtime ∪ CONFIGURED（频道发言不构成通知同意）+ CONFIGURED reconcile（default_notify_channel 配置即可收通知，变更/清空回收旧行）；⑤**D17a 重试恢复**：duplicate + task 仍 CREATED 补 enqueue（平台 retry 变恢复窗口）；⑥**L1 修复**：telegram 通知 chat_id 惰性解析（配对后即刻生效，唯一显式行为变更区）。**4 轮双评审全闭环**（pre-impl Codex 3H+1M / Opus 3M+4L；Final Codex 1H+1M / Opus APPROVE 3L；0 HIGH 残留，1 分歧人裁 Codex 胜）。**4003 passed = baseline 3931 + 72，0 regression**；零变更区 telegram/web 核心 4 文件 0 diff + 9 契约测试 0 断言修改。制品 `.specify/features/105-platform-gateway-v02/`（含 handoff：**H-1 telegram enqueue 同型窗口 ✅ 已修（3208f728 cleanup，连同 2 个非 hermetic ingress 测试 hermetic 化）** / H-2 source_channel_id 单独立项 / v0.3 交互组件）| F105 v0.1 |
| **F106 User Plugin Loader**（原 Companion 改名）✅ 完成（2026-06-22，e7496bbc）| Phase A declarative（发现/分类/注册/REST/降级）+ Phase B 代码执行安全核心（**审批=人工发起 `POST /approve`，无 LLM 同轮自填 flag** + code-hash 验证 + 审批前零 import + `.approved` 跨重启持久化 + hooks lifecycle）+ Phase C watchdog 热重载 + git 安装/更新（**修 H-1 symlink RCE**）；reject-on-collision 防劫持内置 skill 名 + 单 plugin try/except 降级#6；22 passed 双评审。**behavior overlay（FR-3.5）显式推迟**；Companion 推 M7 | M5 全闭环 |
| **F107 文件工作台 v0.2**（git-aware）✅ 完成（2026-06-22，16 commits，已合入 master a6d883b4）| **hybrid 双轨**（CL-2 用户拍板）：**W1** behavior 文件版本历史（独立 SQLite `behavior_versions` 镜像 F104 `artifact_versions`，record-after+baseline）+ 任意两版 diff + Two-Phase 恢复（`behavior.restore_version` 守 #4/#7）+ `BEHAVIOR_VERSION_RECORDED` 事件；落 Agent 中心（DiffView 抽出共享 `DiffBody`）。**W2** workspace **真 git**（仿 Hermes shadow-git：subprocess 外部 bare store + 每 workspace `GIT_DIR/WORK_TREE/INDEX_FILE` 重定向→用户目录无 `.git` + plumbing 快照 CAS + deny-list 排除 secrets/behavior/artifacts#5 + 降级#6 + 注入防御）；浏览历史/改了哪些文件/谁改的(blame)/恢复到此版本（durable `workspace_rollback_requests` 状态机 + CAS + pre-snapshot 恢复点，仅文件态 SD-10）；Files Tab additive 模式切换。**行为文件不纳入 git**（scoping+secrets+REVIEW_REQUIRED 三重硬墙否决）。**2 处 spec 偏离已归档**：W2-B 触发点 broker-hook→file-mutating 工具内写前快照（ExecutionContext 缺 project_root/loop_step）；W2-D 回滚 ApprovalGate SSE→REST Two-Phase（同 W1-C 范式）。**2 wave 双评审（W1-E Opus 1H+1M+3L/Codex 4M；W2-E Codex 2H+2M+1L/Opus 1H+3M+3L）全闭环 0 HIGH**——W2-E 双 provider 互补抓出 commit-scoping/slug-traversal/multi-project 4 真 HIGH。**全量回归 4014 passed 0 failed**（vs f3d8a267）+ 前端 0 新 fail + e2e_smoke 8/8。制品 `.specify/features/107-file-workbench-v02/`（spec/plan/tasks/trace/completion-report/handoff） | F104 |
| **F108 Capability Layer Refactor**（编号顺延，原 F107）**【2026-06-12 拆 F108a/F108b，用户拍板】**| 核心：D9 三层职责（tooling/harness/capability_pack）+ 巨型 domain service 二次拆分（F121）+ D8 解耦（F118）；D11 改名 / D12 DRY 降为顺手项。**已拆出：D2→F117 / dismiss→F116 / agent_context→F113**。**F108a（W1-W5 域内机械拆分）✅ 完成（2026-06-12，15 commits fast-forward 合入 master 4ecc74c2，用户拍板）**：6 巨型文件 12,328 行→18 个职责单一模块（behavior_workspace 1741→8 模块 + D12 写核 two-phase 收口 / coordinator 1889→1213 + D11 WorkerRuntimeAdapter / setup 2576→1520 / worker 2101→1298 / session 1847→1503 / capability_pack 2174→1523 业务逻辑出治理层）；行为零变更（字节级+方法级双工具对账，rebase 后全量门 **4130 passed 0 failed 零 deselect**）；每 wave Codex+第二席双评审 0 HIGH 残留（W5 Opus 席订阅限额中断→主 session 按 F103c 先例接管）；新红线实证：模块级 patch 命名空间耦合（`__globals__` 解析）。制品 `.specify/features/108-capability-layer-refactor/`（program 计划+双评审）+ `108a-domain-internal-split/`（w1-w5 ledger+completion-report+handoff）。**F108b（W6-W8 跨层契约收口）✅ 完成（2026-06-12 同日，8 commits fast-forward 合入 master e860c612，用户拍板）**：W6 审批缓存类下沉 tooling（sha256 字节保真）+ harness-and-context §2.8 三层职责定调；W7 F118 typed DI（ControlPlaneServiceRegistry 构造期 TypeError 前移 + 3 concrete accessor 错误语义字节锁 + bind setter；fail-fast accessor 刻意不做——消费方 None-check 是 Constitution #6 故意降级）；W8 F124/F125 遗留 LOW 闭环 + **AmbientRuntime 挪出冻结前缀（F108 全程唯一显式行为变更，独立 commit 9839927a 可单独 revert）** + no-bypass _SINKS 漂移修复。最终门 **4135 passed 0 failed 零 deselect**；每 wave 双评审 0 HIGH。**F108 program（a+b 8 waves 24 commits）全部兑现：D9/F121/F118/D11/D12 闭环**。spin out 三项 → **F126 已兑现 ✅（a0f288d2）**：schema 校验 fail-closed BeforeHook+结构化 retry / tool_call_id tail eviction（KV-cache 硬门 T120 2/3 transport 实测 PASS）/ artifact read-back（per-turn 预算 warn-only 最小版），4071 passed 双评审 0 HIGH。制品 `.specify/features/108b-cross-layer-contract/` | F101 / F102 |
| **F109 语音 PoC**（STT only）✅ 完成（2026-06-22，双评审 0 HIGH，已合入 master 4562497b）| 单向语音输入 → text：Telegram voice message → 本地 STT（**faster-whisper**，GATE_DESIGN 用户拍板 D1：隐私导向选本地非云 API）→ 转写文本回填 `context.text` → 走**现有 chat 主路径**（哲学 H1：语音=入站预处理，不改 Agent 模型）。可替换薄抽象 `SttBackend`（gateway `voice/` 包，optional 依赖 + lazy import + 优雅降级#6）+ `TelegramBotClient.get_file/download_file_bytes`（流式超限即断）+ `_handle_voice_message`（幂等预检/可用性/守卫/下载/转写/replace 回填）+ 5 类降级回复。**0 regression**（2095 passed vs d6f0ec54 baseline 2073，+22 测试）+ e2e_smoke 8/8。**双评审 panel**（Codex + Opus）0 HIGH：流式下载/polling 测试/早停证明全闭环，并发幂等窗口接受（带原因，F110 硬化）。制品 `.specify/features/109-voice-poc/`。**不做 TTS/voice session → F110** | — |
| **F110 语音 v0.1**（STT+TTS+voice session）✅ 完成（2026-06-22，双评审 0 HIGH，**已合入 master 4a6996c9**）| **M6 收官件（M6 surface 扩张 F104-F110 全部关闭）**。Telegram 完整语音往返：STT 复用 F109 + **TTS 出站**（本地 **Piper / GPL-3.0**，GATE_DESIGN D1 用户拍板接受 GPL 作 optional 依赖、`TtsBackend` 抽象可换；H1 出站后处理挂 `notify_task_result`，**不碰 Agent 决策环/AgentSession**）+ **voice_mode 状态机**（`ConversationBinding.metadata` 三态 unset/True/False + 发语音自动开 + `/voice on\|off`，**显式关不自动重开**）+ AC-D4 多轮连续 + `send_voice`（multipart sendVoice）+ 8 类降级矩阵（TTS 任何失败退文字、永不崩 #6）+ read-modify-write 防 metadata 全量替换。**双评审 panel（Codex+Opus）**：2 HIGH（H1 piper API 错用 `synthesize`→`synthesize_wav` / H2 AC↔test 绑定名）+ F2 首轮竞态/M1 测试缺口/F4 超时并发/F6 Protocol/L2 共 5 项全闭环，**0 HIGH 残留**；主节点 **ephemeral venv 真 piper 冒烟坐实** `synthesize_wav`+libopus 编码端到端（闭合双评审 #1 盲区）。**0 regression**（baseline 4341 / blast-radius gateway+core 2647，唯一失败=pre-existing F106 race）+ e2e_smoke 8/8。制品 `.specify/features/110-voice-v01/`。**实时双工 / Web·Slack·Discord 出站 → v0.2** | F093（Worker Full Session Parity）|
| **F111 Behavior Compactor LLM 智能合并** ⏭ 推 M7（2026-06-22 用户拍板）| F063 Phase 3 推迟项；"token 成本下降后做"的条件项，现在做 ROI 存疑 → M7 与 sleep-time compute 同期评估 | — |

**M6 地基 sprint（先于 F105 执行）**——执行顺序 F114 ✅ →〔F115 / F116 / F112 / F123 ✅ 可并行〕→ F113（待 F112）→ 然后 F105：

| Feature | 类型/规模 | 目的（用户视角） |
|---------|-----------|-----------------|
| **F114** threat_scanner 假 0 修复 ✅（d2936e0）| fix S | 已修双重假 0（断言路径 + scorer 取数遗漏）；L1 确定性 PASS。L2 DeepSeek FAIL 属控变量画像非 bug |
| **F115** daily_routine 时区接入 USER.md ✅ | fix S | 已修：USER.md 新增机器可读 `user_timezone` 字段，时区按 USER.md > env OCTOAGENT_USER_TIMEZONE > UTC 降级（每次读 config 派生，移除 `__init__` env-only stale 缓存）；cron 触发时刻 + "昨日"窗口现真正受 USER.md 控制 |
| **F116** notification dismiss 持久化 ✅（9d5e12d）| fix S | 已修：新增 notification_store.py（SQLite）+ sqlite_init 表 + 启动 rehydrate；dismiss/active 跨重启持久化，跨通道统一 |
| **F112** 双轨死代码清理 ✅（6b60e26）| refactor S | 已清：metadata fallback 残渣 + WORKER_PRIVATE 守卫收敛，行为零变更 |
| **F113** agent_context.py 拆 mixin ✅ | refactor L | 已拆：4600 → 1079 行（-76.5%），5 mixin（EntityEnsure 1049 / PromptAssembly 773 / SessionReplay 644 / MemoryRecall 476 / MemoryService 192）+ helpers 叶子文件（打破循环 import）+ 主文件留编排根（build_task_context 等跨簇组合根，审计 A4 决议不可抽）；re-export 保外部 import 零改动；AST 92 定义逐节点对账零丢失；行为零变更 |

**M6 债务/测试候选（穿插 F117-F122）**：F117 WorkerProfile/AgentProfile 合并（D2）✅ 核心完成（6b7ad8a3，W0-W4：删 WorkerProfile/Revision 类+表 + 塌缩 worker_profile_id + migration_117 真实例副本 dry-run/apply 验证 + 双评审；**W3 改名收尾 ✅**：`requested_worker_profile_*`/`effective_worker_snapshot_id` ~104 wire（含 19 持久化 metadata key）+ FE DTO 收敛为 `requested_agent_profile_*`/`effective_profile_snapshot_id`；DB 列走 sqlite_init 数据保全防御 RENAME，事件 metadata key 读端双轨兼容；`AgentRuntimeItem.worker_profile_id` 冗余字段删除 + `build_agent_runtime_id`/`_ensure_a2a_agent_runtime` 形参收敛）；F118 control_plane D8 解耦（M，并入 F108）；F119 e2e_live 补全 ✅（79caa367，F104/F116/F123/F124 + SSRF↔扫描互补链）；F120 F104 versionable 收窄 + FK 诚实化（L，**M7**）；F121 巨型 domain service 二次拆分（L，并入 F108）；F122 A2A docstring + worker_type 命名（S，并入 F108）。

**调研可吸收点（架构层，作 F108/F105 设计输入，非新 Feature）**：prefix-cache 工具侧不变量（工具集稳定排序 + policy-deny 不删 schema，⚠️ 不照搬 logit_bias）；大工具输出无损卸载为 artifact 引用（落 tool 回写路径，底座 artifact_store）；Google A2A 状态机 gap audit + agent card 式 capability 自描述（F105 借鉴，不照搬 wire protocol）。

**主动剔除（调研确认 skip）**：spec-as-source 代码再生、外部 durable-execution 引擎（LangGraph/Temporal，已 event-sourcing 更成熟）、AIOS。

**M6 竞品源码深读增量（2026-06-08，workflow 深读 vendored Hermes/OpenClaw/Agent Zero/Pydantic AI/Claude Code + 反向验证）**：
- **F123 出站 SSRF 预检 ✅**（fix 安全 S）：新增 `harness/url_safety.py`——`ensure_url_safe`/`async_ensure_url_safe` 解析目标 IP 拦私网(RFC1918)/loopback/link-local/CGNAT/unspecified/multicast/reserved + 云元数据 always-block 地板（169.254.169.254 / ECS / Azure IMDS / 阿里云 / IPv4-mapped + NAT64 + 6to4 内嵌形态，开关也不放开）；`_fetch_browser_page` + `_search_web` 接入 + httpx request event-hook 逐跳重校验 302；`security.allow_private_urls` 开关（yaml mtime 失效缓存 + `OCTOAGENT_ALLOW_PRIVATE_URLS` env 覆盖，默认 false）。删旧 `_validate_remote_url`（仅检 scheme/netloc）。**已挡**：字面量内网/元数据、静态 DNS 解析到内网、302 绕内网、混淆 IP（依赖同 resolver）。**limitation**：DNS rebinding TOCTOU 需连接级 pinning（pre-flight 无法根治，列 M6/M7 egress 域）。Codex 2 轮 + 独立 Claude review，0 HIGH 残留。F105 复用其校验层。
- **F105 设计输入（OpenClaw）**：channel plugin registry / ConversationBinding（**H1：所有平台收敛单一主 Agent，不指向不同 agentId**）/ last-route 出站解析 / per-job delivery+isolated / outbound delivery-queue。
- **F124 工具结果威胁扫描 ✅**（6f69512，5 commit + Codex final 2H+2M）：Hermes F2 落地——ThreatScanner 加 ScanScope（MEMORY/CONTEXT）维度 + broker after-hook 对 tool 结果（web.fetch/MCP/terminal 输出）做 context-scope 扫描，**只标注 `[security-warning]` 不 block/不改 raw**；新增 security_render.py（live/replay/memory-extraction 三路径渲染单一 helper）+ content_threat_scan.py（C10 单一入口）。MEMORY scope 冻结零回归（不破 F114）。原计划是 F108 设计输入，实施时提升为独立 Feature。
- **F108 设计输入**：~~tool 结果 context-scope scan（Hermes F2）~~ **已独立实现为 F124 ✅** / 执行前 schema 校验 + 结构化 retry（Pydantic）/ artifact read-back + per-turn 预算（Hermes）/ tool_call_id 确定性 tail eviction + AmbientRuntime 时间戳挪出缓存前缀（Claude Code，零风险缓存收益）/ 决策环具名扩展缝（Agent Zero）。
- **F106 设计输入**：plugin toggle/热重载/git（Agent Zero az-2，在现有 SkillDiscovery 上扩）。
- **M7**：文件系统 checkpoint/rollback（Hermes，亦 F107 输入）；用户/Agent 自助 proactive cron（OC-5，后端 CRUD 已在缺工具+UI，F102 同域）；skill 自改进闭环（sleep-time 同期）。
- **剔除**：Pydantic typed deps DI（已有 ToolDeps+ExecutionRuntimeContext）/ Claude Code 细分 failure hook（走 event-sourcing）/ Agent Zero 自改写规则（违 #4/#7）。
- ~~**待核查**：`behavior.write_file` 的 `confirmed=true` LLM 自填未接 ApprovalGate，疑似自确认绕过人审，需实测。~~ **已证实并修复（F136 ✅）**：F135 Codex P1 + 经验复现确认首调 confirmed=true 可无 proposal 直写 REVIEW_REQUIRED 文件；F136 把 confirmed 绑定服务端 ApprovalGate（`builtin_tools/write_approval.py`，镜像 escalate_permission 生产范式；显式拒绝恢复 RUNNING 的差异语义见 spec DP-4）。

**不进 M5/M6 的项**：071b Slice D 高层工具暴露（命中"不需要 Codex review 的微改"，空闲间隙顺手做）；Agent Zero Extensions / Instruments 系统（规模 ≥ 1 个月，放 M7 评估）；front-door 公网暴露 / 多用户 / 团队 / 家庭模式（Blueprint §0 已锁单用户深度）；Companion（原 F105，推 M7）。
**M7 追加（2026-06-07 调研）**：sleep-time compute 后台记忆巩固（Letta，底层组件已齐但独立能力域，与 F111 同期，需强 model 验证）；Serena 式 LSP/符号级 Python 代码理解（先外挂 MCP dogfood 评估）。

**M6 并行合并集成 review 结论（2026-06-08，6 Feature 合并后交叉影响 + 回归 + F113 就绪度）**：
- **0 真回归**：组合态 master 实测 3919 passed / 0 failed（远超 ≥3026 baseline）；agent_context.py(F112∩F124) / octo_harness.py(F116∩F124) / daily_routine_config.py(F115∩F102) 三交叉热点实读验证不冲突。
- **F125（fix M）✅ 完成**（f3532ca5，rebase F113 零冲突，3899 passed 0 regression）：broker `_finalize_result` 同步扫描 → `asyncio.to_thread` 卸载（GIL 下 event loop 单次停顿 200-325ms→~54ms，最慢 CTX-C2-004 82→31ms）+ CONTEXT 真实语料误报 89%→0%（≥44 负样本锁死）+ docstring 单遍全文 + 线程安全契约。**4 轮 Codex re-review 收敛**（3H/2M/1L→0H/1M/2L）+ 主节点自查抓 14 边界误报（比 re-review 早抓 9 个）。**越权授予检测维度归档**（developer/elevated/root/superuser/unlimited 与运维 IAM/DB/SSH + SaaS 术语不可区分，CTX-RH-005 收敛 unrestricted/unbounded + you 主语/越狱对象约束，Codex round-4 接受为工程权衡，伴指令版 CTX-RH-004 兜底）。制品 + 归档（Unicode 同形字 / CONTEXT decode / 窗口填充 / RH-003 developer mode / GIL）见 `.specify/features/125-f124-hotpath-falsepos/completion-report.md §7`。
- **F113 就绪确认**：agent_context.py 现 4600 行，4 簇成立，建议实际拆 **5 个 mixin**（多 Memory-service ~203），Entity-ensure ~1075 优先抽，`build_task_context`+`_resolve_context_bundle` 必须留基类。
- **e2e 缺口归 F119**：F123/F124/F116 均有单测无 e2e_live。
- **基础设施待修**：主仓 `octoagent/.venv` editable .pth 指向已删 worktree → pre-commit 裸 pytest ModuleNotFoundError（SKIP_E2E 根因），重跑 `uv sync` 可修。

---

### M7（认知深化：记忆 / 学习）✅ 完成（2026-07-19，F127+F111 交付 / F128 defer 带触发条件 / DeepSeek 零回归 bench 收官）

> **方向（用户拍板 2026-06-27）**：M6 完成 surface 扩张后，M7 转向**让 agent 越用越懂用户**——后台记忆巩固、行为规则智能合并、skill 自改进。区别于 M5（协作对等）/ M6（功能面），M7 是**认知能力域**。
> **验证特殊性**：本域改进 DeepSeek 控变量照不出（记忆深化 / 主动委托看不出），**需强 model 单独验证**——M6 推迟的"强 model OctoBench"在此域兑现。
> **详细规划**：CLAUDE.local.md §"M7 战略规划"。

| Feature | 一句话目的（用户视角） | 规模 | 顺序 |
|---------|----------------------|------|------|
| **F127 Sleep-Time Memory Consolidation**（旗舰）✅ 完成（2026-07-03）| agent 空闲 / 定时在后台"巩固记忆"——回顾近期会话、去重 / 合并 / 组织事实、强化 recall，越用越懂你。仿 Letta sleep-time compute。底层组件（F102 routine / F097 subagent / F094 AGENT_PRIVATE memory / recall）已齐，M7 编排成独立认知能力域。**v0.1 实际范围（用户拍板 5 决策收窄）**：纯 cron 深夜触发 + 事实层去重合并（复用 MERGE 写管道）+ 破坏性变更全人审（`consolidation_candidates` + CONFLICT 终态 + REST 审批）+ 敏感分区排除三层防御 + H1 守界通知；idle-detect / session 摘要 / recall 权重 / 敏感 vault-aware MERGE 推 v0.2。G-lite 真 LLM 验证 6/6 PASS（DeepSeek-V3.2）；强 model 质量评估归 M7 统一 OctoBench 方案。见 core-design §8.7.6 + `.specify/features/127-sleep-time-consolidation/` | XL | 第 1 ✅ |
| **F111 Behavior Compactor**（LLM 智能合并）✅ 完成（2026-07-15，30 commits ff push master `bfe8ec82`）| 已交付 v0.1（拍板范围全兑现）：①单文件去冗余（跨文件 defer v0.2）②cron（03:30，`compact_active` 默认关）+ 手动 `octo behavior compact` 双触发——cron 复用 F127 编排全套（合成 root spawn 审计容器/单飞两层/占位泄漏防御），手动前台直调 ③**审批载体实测定=独立 `behavior_compact_candidates` 候选表 + REST accept/reject**（`gate_behavior_write` 三重硬冲突实测：300s 阻塞超时丢弃/超时不恢复 RUNNING/CRITICAL 豁免 quiet hours——nightly 无人值守不适配；候选表复用 F127 五态+atomic claim+CONFLICT 新鲜度模式、新建文件级表防概念错配） ④三层验证全落地：H1 变小/H2 PROTECTED 占位符构造性保证/H6 USER.md config parity 等确定性护栏进 L4/L3 + `e2e_scripted` 全链（脚本脑→REST→落盘→F107 版本→事件链，进 CI）+ `real_llm` 质量用例真打 PASS（GPT-5.5：342→242 字符三组冗余各并一条语义保留） ⑤零 agent 自主 commit（accept 唯一落盘入口 + 静态断言）。**Codex 21 轮迭代至连续两轮 0 finding**（5 P1+14 P2+3 P3 全闭环或拒绝带证据）+ Opus 自审 4 真问题闭环；全量 ≥5347 passed 0 regression。制品 `.specify/features/111-behavior-compactor/`（含 completion-report）| **L** | 第 2 ✅ |
| **F128 Skill Self-Improvement**（Hermes 式闭环）⏸ **defer 带触发条件**（2026-07-19 用户拍板）| 评估取证结论：底座已便宜（F111 候选表机器全可复用，L→M）但**学习信号太薄**——SKILL_STARTED/COMPLETED/FAILED/USAGE_REPORT 事件机制在、实例装 10 skill，但日常使用 skill 显式执行量低，无足量成败样本可学。**触发条件**：真实使用积累足量 skill 执行 或 出现重复失败模式 → 复用 F111 候选表+审批机器启动 | L→M | ⏸ |

**串行理由**：三者都触碰 memory / behavior 子系统，并行会严重 rebase 冲突。F127 旗舰**设计先行**（spec / plan 设计 → 用户拍板范围 → 再实施）。

**M7 收官（2026-07-19 用户三拍板）**：F128 defer 带触发条件；收官 bench 归因后无生产代码回归；F134 bearer 加固、候选审批前端 UI、P2 清扫与平台级 follow-ups 转入 M10。详细 bench 证据归档于 `benchmarks/baselines/m7-close-20260719`。

**M7 其他 carry-forward 候选**（认知深化 主线后评估）：F120 F104 versionable 收窄 + FK 诚实化；Serena 式 LSP / 符号级代码理解（先外挂 MCP dogfood）；OC-5 用户 / agent 自助 proactive cron；Hermes 文件系统 checkpoint / rollback；Agent Zero 持久交互 shell + Extensions；Companion（独立 agent 伴侣，最开放，可能独立 milestone）。

**bench infra 待办（M7 期间顺手）**：①OctoHarness 轻量 bootstrap（达 SC-001 ≤1h，现每 task 起完整 harness ~2min/task）；②teardown 竞速 memory-extraction 退 Echo（无害噪声，bench-specific 生命周期）；③Tier2 τ-bench 真跑接入。

---

### M8（部署与日常使用：从“能跑”到“稳定日用”）✅ 功能完成（F129、F131-F133；F134 已于 M10 完成）

> **目标（用户拍板 2026-07-03）**：Connor 要开始**真实部署 + 工作/生活日常使用** Octo。功能面 M5-M7 已很全，卡点在进程守护、日志、可靠渠道与日常自助能力。
> **决策来源**：`octo-daily-use-readiness` workflow（38 agent 深读 Hermes/OpenClaw 部署机制 + Octo 三域审计 + 外部远程方案调研，26 findings 过对抗验证 / 7 条幻觉 gap 被推翻）。报告 Artifact 存 CLAUDE.local.md §M8。
> **里程碑边界**：M8 交付常驻服务、Telegram 可靠性、cron 自助和语音解耦；浏览器远程访问统一转入 M11 F150。

| Feature | 优先级 | 一句话 | 规模 |
|---------|--------|--------|------|
| **F129 常驻服务地基**（进程守护 + 日志落盘）✅ 完成（2026-07-04，双评审 10 finding 全闭环 0 HIGH）| **P0** | `octo service {install,uninstall,status}` → launchd/systemd 三态幂等 + stable-working-dir 红线 + 退避熔断；`octo restart` 在 OS_SERVICE 策略下委托 launchctl/systemctl（不再要求进程存活）；RotatingFileHandler 脱敏落盘（10MB×5 + import 快照防关）+ `octo logs`（tail/-f/--level + 启动期崩溃回退 err.log）+ doctor 服务健康/睡眠风险 2 check（只检测绝不改系统设置，可选 `--keep-awake`）。详见 `.specify/features/129-service-foundation/completion-report.md` + `deployment-and-ops.md` §12.5.6 | M |
| **F131 Telegram 可靠性** ✅ 完成（2026-07-06，Opus 自审 1 HIGH + Codex 4 轮 0 HIGH，146 tests 0 regression）| P1 | polling 指数退避（替扁平 sleep 防 busy-loop）/ 409 双开识别（`_is_getupdates_conflict` + 用户可修 hint，区别普通网络错）/ 出站补偿 spool（`telegram_outbound_spool` 表 + 独立后台 drain loop，send 失败入队、进程重启不丢；带按钮审批不 spool）。诊断结论：入站已防丢（offset 重发），真缺口在出站零补偿。详见 `.specify/features/131-telegram-reliability/completion-report.md` + `platform-gateway.md` §6b | S |
| **F132 cron 自助工具**（OC-5）✅ 完成（2026-07-06，双评审 0 HIGH）| P1 | 用户/agent 从手机自助建定时任务。诊断确认后端 CRUD（F026 automation store/service/scheduler）完整但**只能跑管理动作、无法给用户发提醒**——补 ①`cron.create/update/delete` agent 工具（进 CoreToolSet，F135 先例；NL↔cron 由 LLM 自译 Constitution #9，工具校验 + 拒纯数字 DOW 防 APScheduler Monday=0 off-by-one）；②`reminder.notify` action 补齐 cron→交付用户缺口（NotificationService，H1 守界=通知非对话轮次）；③时区接 F115 降级链；④破坏性操作走 `gate_destructive_action` Two-Phase；⑤Web `AutomationCenter` 只读列表+toggle（删除走对话治理）。制品 `.specify/features/132-cron-self-serve/` | M |
| **F133 voice 从 polling 剥离** ✅ 完成（2026-07-06，Codex 1 P2 闭环 + re-review 0 finding，4606 passed 0 regression）| P1 | 语音处理与 Telegram polling 解耦。诊断确认 STT backend 已 to_thread（event loop 不卡），真缺口=polling 协程串行 await 整条 voice pipeline（下载+转写+降级 send 内联 `_ingest_update`）——改为入队全局 FIFO + 单 consumer 后台 worker（并发上界=1 防 CPU 打爆、天然同 chat 保序、lazy spawn 自愈、shutdown 干净 cancel+清队列），转写成功走 `_ingest_text_context`（baseline 后半段原样抽取，H1 只挪"何时跑"）。顺带闭合 F109 转写前并发幂等窗口（串行 worker 处理时点预检）。durability trade-off 显式归档（offset 先行确认、崩溃丢未转写语音，v0.1 接受）。制品 `.specify/features/133-voice-async/` | M |
| **F134 bearer 加固** | P1→M10 | 失败限流 + `uvicorn.access` secret 脱敏 | S |

**P2 nice-to-have（候选，不阻塞）**：容器交付 · toolsets 死配置清理 · cron 后台失败 HIGH 通知。

**波次编排**：①**F129 守护+日志**（强绑先做，是一切前提）→ ②F131/F132/F133 并行（渠道/自助/语音）→ ③P2 顺手清。

**部署形态**：单用户不需容器化。首选**禁睡的常驻 Mac（mini）+ launchd/systemd user unit**，得到崩溃自愈与开机自启；远程访问由 M11 F150 的 Cloudflare 出站 tunnel 提供。

**竞品借鉴（已验证）**：Hermes `service_manager` 五态抽象 + unit/plist 模板 + stable-working-dir + rotating+脱敏日志；OpenClaw `auth-rate-limit` + `telegram-ingress` spool/409 建模。

**已确认无需做（REFUTED，留档防重复立项）**：`--host 0.0.0.0 默认全网卡`（默认已 127.0.0.1）· `任务结果结构性推不到手机`（链路未被 gate）· `通知无优先级/静默`（grep 的是过时 worktree）· `doctor 只 28 行空壳`（真 doctor 557 行 13 检查）· `更新无回滚`（四阶段状态机 + BackupService）· `弱 token 拷示例即失守`（示例只存变量名）· `terminal.exec 锁死 project`（沙箱是 instance root）。**Octo 地基比想象扎实，真缺口集中在"守护 + 触达"。**

**真机部署首日修复（2026-07-04/05，真实使用暴露的 gap 当天回补）**：实例从 F086 跨 M5/M6/M7 升级到 master（评估先行、零数据丢失）后真机日常用，暴露并已修 3 类真 bug（纸面调研照不出、只有真用才出）：
- **记忆巩固管线生产静默失败** ✅（`b46ecc2b`）：`LlmServiceProtocol` 契约误声明 call_with_fallback + harness 注入裸 FallbackManager → 巩固/画像/派生/ToM 四管线每 tick AttributeError；测试全用 AsyncMock 假 LLM 掩盖。三层修：协议改回 call + harness 注 LLMService + 4 消费方构造期 `ensure_llm_call_contract` fail-fast + 契约锁测试防翻烙饼。
- **F135 日常使用就绪修复** ✅（`662df4a7`）：**gap-1** `behavior.write_file` 落进 Deferred 桶（须 tool_search 两跳）致"首次见面填画像"引导闭环走不通 → 提入 `CoreToolSet.default()`（同 graph_pipeline/delegate_task 先例）；**gap-2** F129 launchd 干净环境 PATH 无 node → npx 型 MCP 全挂 → plist 注入 `~/.volta/bin`+homebrew（过 stable-working-dir 守卫）+ schema marker 让已装用户自愈重写；gap-3 时区/USER.md 用户手动解。
- **F136 behavior.write_file 服务端审批绑定** ✅（`4e80b149`）：F135 gap-1 暴露的 pre-existing 安全缺陷——`confirmed=true` 是 LLM 自填参数、可一轮自确认绕过人审直接写 behavior 文件 → 服务端审批绑定关闭自确认；顺手修 escalate_permission 同款 allow-always 404 隐患。
- **待办 follow-up**：F135 P2 衍生——`octo service install --force` 重写已运行服务时 launchd 卸载→重载有窗口，20s 就绪超时太紧致 `repair-required`（真机实测手动 `launchctl bootstrap` 即恢复），install 应加 bootstrap 兜底 / 放宽超时（归 F129 followup 或 P2）。

---

### M9（质量保证体系：四层测试金字塔 + 门禁改造）✅ 完成（2026-07-13，10 Feature F137-F144 全清，CI 全绿）

> **目标（用户拍板 2026-07-09）**：把 E2E 验证拆成四层金字塔（L1 UI E2E / L2 非 UI 含真 LLM / L3 确定性无 LLM / L4 单元）+ UI/逻辑分离 + 质量门禁分层，最大化自动化、**最小化用户手工测试**。
> **决策来源**：`qa-four-layer-audit` workflow（内部 6 路审计 + 竞品 5 路测试架构深读 + 28 候选对抗验证）+ `deep-research`（业界测试分层 / 去 LLM 依赖 / UI 可测性 / 门禁 四子题，9 条硬证据 3-0/2-0）。两 workflow 均撞 session 用量上限（synthesis/critique + 5 路 agent 被杀），**synthesis 由主节点综合完成**（决策集中原则，3 幸存审计交叉覆盖被杀路 + 亲历补齐）。
> **两个总纲判断**：①**金字塔倒挂**——L4 单元 ~87%（厚），但 L2 真 LLM 回归**仅 2 文件**、L1 UI E2E **绝对零**；L3 有 ~50 文件但缺「能产 tool_calls 的脚本化 LLM」→ agent 决策环在 L3 测不了 = **反复要手工测的结构性根因**。②**CI 断链**——唯一 workflow 引用的测试文件已删（4600+ 测试零 CI 覆盖）；前端复杂度护栏已 FAIL 无人知；pyproject marker 描述与实现矛盾；provider 无硬闸防漏网真调用（bench TLS 事故根源=漏网真调用被 FallbackManager 静默退 Echo）。
> **三项用户拍板**：①独立 M9 里程碑（7 Feature 成体系跨 gate/L1-L4）；②首波 F137 止血 + F138 keystone 并行；③L1 走 Playwright 薄输入 + 外部断言（断言走 event_store/文件系统 diff，不脆弱 DOM）。

| Feature | 层 | 优先级 | 一句话 | 规模 |
|---------|-----|--------|--------|------|
| **F137 门禁止血** ✅ 完成（2026-07-11，ff push master fd502885；Codex 5 轮 2P1+6P2 全闭环 0 HIGH；4884 passed = baseline+38 0 净回归 + e2e_smoke 8/8 + e2e_full 真跑 opt-in 实证）| gate | **P0** | 已交付：①硬闸 `model_request_gate`（env 缺省 allow 生产零感知；测试 pytest11 插件+根 conftest 双布线 deny，**显式 env 优先**）——闸点实测修正为 **`call()` 入口第一行 + `embed()` no-op 分支后**（`_dispatch` 太晚：OAuthResolver.resolve 是 preemptive 刷新会先打真 auth 端点）+ 6 处 swallow 站点 re-raise（fallback/llm_service×2/runner/memu_bridge embed 两级），deny 全量 triage 45 失败全为「直测 dispatch 机器」类②按文件声明放行，0 假绿 0 漏 opt-in；②CI 改写 B-lite 双 job（确定性层串行+junit+--reruns 1 过渡桥 / 前端 complexity+vitest 阻断）；③前端门禁进 pre-commit（SKIP_FRONTEND_CHECK=1）+3 阈值放宽 F143 ratchet；④marker 名实相符；⑤顺带删 provider `__init__` 对 gateway 的 vestigial 兼容 re-export（倒置依赖，gateway-less venv 插件加载即炸的根因）。**顺带抓 2 真 bug**：memu_bridge 4×`log`→NameError 潜伏（#6 违反）+ 前端 vitest 存量 11 failed 从未进闸（6 文件 --exclude 记欠账已派 fix chip，含疑似真 UI 回归）| S-M |
| **F138 脚本化 LLM harness**（keystone）✅ 完成（2026-07-11，ff push master 5e316de0，rebase onto F137 代码零冲突；Codex spec 评审 0 HIGH + 双评审 0 HIGH）| L3 | **P0** | 已交付：harness `model_client` + `clock` DI（默认 None 生产行为等价；override 与 `OCTOAGENT_LLM_MODE` 解耦、不要求 provider 凭证）+ `QueueModelClient` 上提为 `octoagent.skills.testing.ScriptedModelClient`（拍板②脚本脑优先，conftest re-export 零改既有消费者）+ keystone e2e `test_e2e_scripted_decision_loop.py`（marker `e2e_scripted`：脚本 LLM 决策 → 真 tool_broker 派发 → 真回写，**决策环前半段 L3 首次覆盖**；零真 LLM / 零宿主 OAuth → CI-runnable，4 case <3s）+ clock DI watchdog demonstrating consumer（6 调用点，F103d offset-naive bug 类 L4 可测）+ e2e-testing/testing-strategy 文档漂移修。**`SchemaTestAdapter`（TestModel 等价）deferred → Phase 2**（spec §2.2 归档范围/理由/启动条件）| L→**M-L**（deferred 收窄）|
| **F139 provider wire 录制回放**（Fable 复审收窄，原「VCR 体系 L2→L3」）✅ 完成（2026-07-13，本地 worktree `F139-wire-replay` 待拍板合入）| L3 | P1→**P2** | 已交付（收窄范围 4 件全闭环）：①secret 过滤 serializer 先行——六道管线（token 端点 drop/请求头 allowlist/响应头 allowlist/log_redaction 复用/fail-closed 已知凭证禁串+模式扫描/事务式原子落盘）+ 身份字段定点洗刷（真录实锤 codex 回显 `safety_identifier=user-xxx` 账户标识+`prompt_cache_key`+`instructions` 回流，模式扫描抓不到、人眼 review 抓到）+ committed cassette 永久扫描；②8 盘 cassette——siliconflow 4 真录（simple/tool_call/U+2028 探针/embeddings，8 次调用）+ codex 2 真录（4 次调用，订阅一次性）+ anthropic 2 **手写 golden 显式归档**（宿主无凭证，`meta.source=handwritten-golden`，含 event:/ping/SSE 注释行等真 wire 元素）；③完整消费护栏（pydantic-ai fail_partially_used 范式，tamper 实证 FAIL）；④**U+2028 评估闭环（F142 输入）**：探针实测模型能 round-trip 输出 U+2028 但 SiliconFlow 对 line-separator **特判转义**（CJK 则 ensure_ascii=False 原样）→ wire 无未转义原始字节，按收紧判据（仅探针命中才动生产）**归档不动生产**，F142 现状钉住测试维持+cassette 永久钉 provider 转义习性。**设计偏离归档**：弃 vcrpy 自研极简 recorder/replay（stdlib JSON cassette，~440 行 test-only）——零新依赖处处默认可跑（主仓 venv/hook/CI 无 uv sync 协调、无 importorskip 假绿窗口）+ 无全局 monkeypatch 与 xdist 零互扰；**pyproject 零改动、生产代码零改动**。真实 wire 习性入册：SiliconFlow 每 chunk 带 usage/CJK 原样字节。spec 阶段 Codex 对抗评审 2H+3M+1L 全闭环。44 新测试全绿（hermetic unset 全凭证验证）。制品 `.specify/features/139-wire-replay/` | S-M |
| **F140 L1 UI E2E** ✅ 完成（2026-07-12）| L1 | P1 | 已交付 hermetic Gateway 启动器、脚本脑聊天写盘、bearer FrontDoorGate + SSE query 鉴权、UI 外部断言、testid 契约和独立 Playwright CI job。场景首跑抓出 Authorization header 被调用方覆盖的 production bug并完成回归；审批场景后由 F145 补齐 | L |
| **F141 三模式 lane 门禁** ✅ 完成（2026-07-13）| gate | P1 | 已交付 `repo-scripts/lane.py` 的 pr/baseline/release 编排；release 强制 live-real-llm、`octo attest service --json` 与 attestation 签署；并完成 real_llm marker、flaky quarantine、change-policy、changed-lines coverage、测试契约和 gate 脚本单测 | M |
| **F142 确定性护栏补齐** ✅ 完成（2026-07-12，worktree `F142-guards` 待拍板合入）| L3/L4 | P1 | 已交付五件：①第三方库语义钉住 `tests/lib_semantics/`（anyio/httpx **真本地 TLS server**（cryptography ephemeral 证书零外网）钉「流中断异常 ∈ `_TRANSIENT_TRANSPORT_ERRORS`」+ RST 面精确复现 bench 事故空 message `ReadError('')` 签名 + ProviderClient 真栈重试恢复端到端；APScheduler 真 CronTrigger 钉 Monday=0（数字 0→周一即 off-by-one 陷阱本体）；piper importorskip 真库签名；aiosqlite 已真库覆盖显式略过）；②prompt token 预算护栏（真 bootstrap + 真 chat 主路径捕获 `compiled_context.messages`——system 面实测 8938 tokens cap 10300 / 工具 schema 面 68 工具 11253 cap 13000（tokenizer cl100k 校准）+ 关键短语在场 ×5 + 退役内容负向扫描（`.env.litellm` 为存活文件名化石显式排除））；③wire 边界用例族 13 用例（malformed JSON `continue` 分支 ×3 transport 首次覆盖 + 真 httpx LineDecoder 字节级粘包/半包/CJK 切断穿透 + **U+2028 切行静默丢 delta 行为钉住**（真发现：provider 未转义 U+2028 会致 delta 静默丢失，修复候选=自管 SSE framing 归档）+ 2MB 行现状钉住；行缓冲无上限评估=威胁模型低+修复非极小→不动生产归档）；④dirty-equals 0.11 引入 + 3 范式样例（full-shape/IsNow/IsPartialDict；importorskip 收窄函数级）——inline-snapshot defer（三件配套待 F141 后评估）；⑤xdist_group 18 文件 11 组 + **CI backend job 翻 `-n auto --dist=loadgroup`**（本地 3 轮全绿 4894 passed，串行 378s→~26s ~14x；顺手治两类真问题：set 参数化收集不确定性 ERROR + attach_input 次生窗口 race）。**F137 4 个 CI-skip 欠账处置**：f131 spool drain / f009 timeout 治根因（条件轮询替固定 sleep）移除 skipif；finalize_offload 130ms / threat_scanner 5000 字符 1ms 两性能断言升级**永久 CI 豁免**（绝对时长阈值按开发机校准共享 runner 本质不可靠，均有确定性伴测 CI 照跑）。制品 `.specify/features/142-deterministic-guards/` | M |
| **F143 UI 变薄扩 L4** ✅ 完成（2026-07-13，7 commits worktree `F143-ui-thin` 待拍板合入；Codex spec 评审 1H+2M 全闭环 + final 0 finding + Opus 席 0 HIGH 0 MED；vitest 204→382 全绿 / tsc 0 / complexity 收回后 PASS / **L1 Playwright 本地 3/3**）| L4 | P2 | 已交付：①useChatStream 659→**487**——事件分支唯一事实源 `chatStreamReducer.ts` 纯函数（(state,event)→state，占位 id 注入）+ 24 序列直测（乱序/漏事件+final 兜底信号/轮边界/审批生命周期/COMPLETED+final 不触发兜底）；顺带显式修复 onerror 清 isStreaming 的 ref 时序竞态（Codex spec 评审 HIGH 闭环，声明式非漂移）②ChatWorkbench 1207→**989**——五块纯派生下沉 domains/chat（work/A2A 上下文、活动视图、审批横幅四源合一、会话头部、slash 命令），JSX/handler 零改动、L1 锚点原位 ③8 纯逻辑文件 L4 全兑现（+146 用例，全输入输出契约断言）④删死代码 ApprovalPanel/useApprovals 净 -450 行（`/api/approve` 后端路由自此前端零调用方，路由本身不动）⑤共享 FakeEventSource（src/test/，收敛 3 处内联副本）+ MarkdownContent 8 条 XSS 消毒断言。**阈值真收回**：ChatWorkbench 1250→默认 1200 / useChatStream 700→默认 500（explicit 行删除）/ index.css 4600→4480 只挡增长（3300 需独立样式拆分 follow-up 归档）| M |
| **F144 验收自动吸收** ✅ 完成（2026-07-12）| L3+live | P1 | 已交付 front-door mode×header 矩阵、`octo attest service` SIGKILL→新 pid→`/ready` 探针及 hermetic 单测、写入审批 L3 scripted 双路径；attestation 清单仅保留物理 reboot 的 ATT-129-BOOT | S-M |

**波②③落地 + 组合态验收（2026-07-12）**：F140 L1、F142 护栏与 F144 验收吸收联合全量 5060 passed / 0 failed，e2e_smoke 8、e2e_scripted 16、attest/frontdoor 聚焦测试全绿。`octo attest service` 真跑 SIGKILL 后由 launchd 拉起新 pid 并恢复 `/ready`，F129 崩溃自愈通过；物理残余只剩重启验开机自启。

**波④组合态验收（2026-07-13，本地叠加未 push 等拍板）**：F141（三模式 lane：`lane.py` pr/baseline/release + release 强制 live + quarantine 过期 FAIL + changed-lines 90% 门 + tests/AGENTS.md 契约）∥ F143（UI 变薄：ChatWorkbench 1207→989 / useChatStream 659→487 **阈值真收回兑现 F137 ratchet** + vitest 204→382 + 删死代码 450 行）∥ F139（wire 回放：8 cassette + 六道 secret 管线**实录抓到 codex safety_identifier 泄漏**并加洗刷 + 弃 vcrpy 自研 440 行零依赖零生产改 + U+2028 结论归档）。串行 rebase 叠加（F141→F143→F139，rebase 用 `GIT_EDITOR=:` 强制非交互避坑，milestones/testing-strategy 不同行自动合并零手解）→ **组合态联合全量 5214 passed / 0 failed**（baseline 5066 + F141 gate 99 + F139 wire 49 精确对齐）+ e2e_smoke 8 + e2e_scripted 16 + wire 回放 49（离线）+ **lane.py pr 四 lane 全 PASS** + 前端 vitest 382 + complexity 收回后 PASS + tsc 0。教训复现 ×2：组合态验收前必主仓 `uv sync`（venv 指向）**+ frontend `npm ci`**（node_modules 陈旧缺 marked/diff 致 8 假失败）+ 移 dist。**待办**：①用户 push（SSH，F141 含 CI workflow）②主 session 跑 `lane.py release` 的 live 半边（9 用例真打 GPT-5.5 + attest 探针 + attestation 签署）——真机 opt-in。**此波合入 = M9 十个 Feature 全清。**

**波④ CI 闭环 + M9 收官（2026-07-13）**：quarantine、complexity、backend、vitest、live-real-llm 与 attest-service 全绿；唯一未签署项为 ATT-129-BOOT。M9 全部 Feature 收口，四层金字塔从“倒挂 + CI 断链”变为 L1-L4 齐备、三模式 lane 和真机 service 验收自动化。

**波次编排（Fable 5 复审 v2）**：①**F137 止血 ∥ F138 keystone**（首发并行，文件不冲突：F137=CI/pyproject/githooks/provider，F138=harness/adapter/skills）→ ②**F140 L1（依赖 F138，提前——直接服务「减手工测」主诉求）∥ F142 护栏** → ③F141 lane（依赖 F137 CI）∥ F139（收窄后 P2）→ ④F143 顺手。**首波两个均设计先行已完成**（worktree `F137-gate-triage`/`F138-scripted-llm`，六岔路已拍板，待收窄 spec + 双评审后实施）。**波② F140 已完成**（2026-07-12，见其行；F142 并行中）。

**Fable 5 复审调整（2026-07-09，切模型整体复审后 v2，7 处）**：①F139 收窄+降 P2（理由见其行——agent-loop VCR 论证被推翻）；②changed-lines 覆盖门 F142→F141 归位；③xdist_group 显式入 F142，**F137 CI 首版接受串行 ~20-40min**（GitHub 2-core，单用户 push 事后安全网可接受；F142 落地后减半，实施 agent 勿直接 `-n auto`——F083 race 会炸）；④F140 依赖 F138 显式化并提前到波②；⑤F141 补 attestation lane；⑥F137 spec 收窄注两条——**硬闸点须在 `call()` 入口早于任何 proactive auth-refresh 副作用**（否则 deny 模式带过期凭证仍会打真 auth 端点，实施时核实 refresh 触发时机）；**deny 布线优先 provider 包 pytest11 entry-point 插件**（全 venv 构造性生效、无 9 个 tests 目录 conftest 多点同步债；`octoagent/` 根 conftest 次选——per-package rootdir 直跑会绕过）+ benchmarks 显式 allow（OctoBench 控变量真调用是设计内勿误杀）；⑦F137 CI 首跑预算一轮环境敏感失败 triage（~72 处 sleep 断言慢 runner 可能抖）+ e2e_smoke 无凭证 SKIP 属预期（**F138 keystone 测试将成为第一条 CI 可跑全栈冒烟**；后续可给 smoke 配 stub credential fixture 变体让其真跑进 CI）。复审维持不变：硬闸异常类型区分漏网/合法降级、脚本脑优先、skills `testing` 子模块、Echo 并存+override 与 echo-mode 解耦、前端 3 FAIL 放宽阈值不抢 F143、首波并行文件不冲突判断。

**验证吸收原则（2026-07-12 用户拍板，M9 全局约束）**：任何“请用户手工验证”的输出视为体系缺陷——必须先尝试分层吸收（L4 单元 → L3 确定性 e2e/scripted → L1 UI 自动化 → 本机 live 探针），只有物理不可自动化的残余才允许落 attestation 清单（F141 release lane 消费）。首批吸收为 F129 崩溃自愈与 F135 gap-1。

**首波落地 + CI 首跑闭环（2026-07-11）**：F137（fd502885）→ F138 rebase 零代码冲突（5e316de0，组合态 4975 passed / smoke 8/8 / keystone 9/9）→ CI 首跑 triage 一轮如预算（925bc29b：**1 真 bug**=F127 红线测试硬编码本机绝对路径改模块 `__file__` 派生 + 4 环境敏感时序/性能断言 `CI=true` skipif 记欠账归 F142）→ lint 归零（a8d99366）→ **CI 全绿**（frontend 29s + backend-deterministic 6m25s，远好于 20-40min 预估；concurrency 组自动取消旧 run 生效）。运维备忘：push 触碰 `.github/workflows/` 需 SSH（HTTPS OAuth 凭证均缺 workflow scope）；双合入后主仓 `uv sync` 已收敛 venv（pytest11 entry point 注册 + editable 指回主仓）。

**竞品借鉴（已对抗验证「我们是否已有」，28 候选 = 5 真缺 / 21 部分有 / 2 已有）**：pydantic-ai（**TestModel/FunctionModel** 确定性测试模型 + **ALLOW_MODEL_REQUESTS** 硬闸 + **VCR cassette** + inline-snapshot/dirty-equals + xdist_group 治 flaky 非重试，Python 测试天花板 100% 分支覆盖）；cc-haha（**三模式 lane + release 必跑 live** + **UI 仅输入通道外部断言** + change-policy 路由 + changed-lines 覆盖门 + flaky quarantine）；agent-zero（**第三方库语义钉住** + prompt 预算护栏 + tests/AGENTS.md）。**DeepResearch 外部共识**：TestModel（pydantic-ai）/GenericFakeChatModel（LangChain）跨框架确认脚本化 fake model 是行业惯例非单一做法；ALLOW_MODEL_REQUESTS 机械禁真调用；unit(fake) vs integration(real) 边界=是否打真 LLM；LLM-as-judge 非确定性需校准、不能做免校准门禁，code grader 才 fast/cheap/reproducible（Anthropic）；capability eval（低通过率）vs regression eval（~100%）分套件分阈值（Anthropic）；tau-bench SOTA agent <50% → 真 agent E2E 不能做二元 CI 门（arxiv 2406.12045）。

**风险留档**：①M9 **不消灭真 LLM 测试**——决策质量仍需真 model，本质是「把不需判断力的用例降层、需判断力的收敛到 weekly 而非 per-commit」；②F141 收紧会挡现在习惯的 `SKIP_E2E=1` 随手 bypass（**故意**，堵 AC-1 反复推迟）；③F140 L1 harness 首建（起后端复用 OctoHarness DI + 前端 dev server）是实打实 L 规模。

**已确认已有别重造（对抗验证 already-have）**：UI 测试收敛到协议层（48 文件 HTTP 层 + SSE 序列断言 + 前端 FakeEventSource stub，结构上已具备，仅缺 golden snapshot 便利件）；provider double /v1 wire 回归（a6b51fc4 已治本 + 23 用例三 transport 出站 body shape 断言成体系，比 pydantic-ai SDK 层更贴 wire）。

---

### M10（部署完成度收尾：从「功能就绪」到「日用无欠账」）✅ 功能完成（4 Feature 全 ✅，CI 全绿）

> **目标（用户拍板 2026-07-19）**：把散在各 Feature 归档里的最后一公里欠账收齐——审批不再只能 REST/Telegram、安全尾巴收掉、平台一致性小债清零。浏览器远程访问统一归入 M11 F150，不作为 M10 收官条件。
> **波次**：首波 F145∥F134∥F146 三路并行，F147 第二波。

| Feature | 规模 | 一句话 | 波次 |
|---------|------|--------|------|
| **F145 统一候选审批中心** ✅ 完成（2026-07-19，7 commits；三席评审 Codex 0H/2P2 已修 + Opus 0H/1MED 已修/4LOW 归档 + Fable 自审交叉印证，0 HIGH/MED 残留；5367 passed = baseline 5356+11 0 regression / 前端 vitest 424 / L1 4 passed）| M | 已交付：新「审批」nav 项（三源合计红点 badge，`GET /api/approval-center/summary` 只读汇总端点）+ `/approvals` **单页三分组**（非 tab：新记忆复用 CandidateCard 零改动 / F127 记忆合并 ProposalCard+来源预览 / F111 规则精简 unified diff 折叠渲染复用 DiffLineList），旧 `/memory/candidates` 页吸收删除 + redirect。**零改三源审批语义**；仅两处薄读扩展（summary 计数 + consolidation list `source_previews`——破坏性 MERGE 的知情决策材料，敏感候选/含敏感源恒空列表与审批端第三层防御同判定源）。CONFLICT 终态呈现按 body.status 分流（conflict=移除+已失效 toast 不诱导重试 / pending=保留+可重试）。**L1 场景③未 defer 真兑现**：launcher 注入 source_hash 对账候选 → UI 点接受 → 盘上覆写+pending 归零+bomb 三通道外部断言（F140 deferred 的审批场景闭环）。制品 `.specify/features/145-approval-center/` | ① |
| **F134 bearer 加固** ✅ 完成（2026-07-19）| S | 已交付 `FrontDoorGuard` 内 verify-first 失败限流：60 秒 10 次错误 credential 触发 300 秒 lockout，正确 credential 恒放行并清计数；同时给 `uvicorn.access` 安装幂等脱敏 filter，阻止 SSE query credential 落盘。未创建第二认证入口 | ① |
| **F146 平台一致性三小件** ✅ 完成（2026-07-19，5 commits 未 push 待拍板；Codex spec+final 两轮 0 finding + Opus 自审 0 HIGH；5361 passed = 基线 5347 + 14 新测试 0 regression）| S | 已交付：①F102/F127 `_read_user_md` 推广 F111 盘优先修法（共享 helper `user_md_cron.py`，F111 自身同步收敛去三份重复；构造器必填 project_root）——盘外编辑对 cron 即时可见 ②三写点（F136 LLM 工具/F107 restore/**取证发现的 Web 编辑器同名同缺口**）落盘 USER.md 后补 live-state 同步（F111 accept 范式；ControlPlaneContext +snapshot_store None 降级）——quiet hours/`user_profile.read` 无需重启读到新值；F111 accept 与 user_profile.update 核查确认无需改 ③三姊妹 cron 时间热重载统一语义=**下一次已排定 tick 读盘生效无需重启**（tick 内 reconcile key 比对；闭环 F111 Codex round5 P2 归档 follow-up）。14 测试锚全行为变更锚定；2 既有缺口归档不扩面（写核非原子窗口/Web 写缺 cache invalidate，见 completion-report §3）| ① |
| **F147 清扫篮** ✅ 完成（2026-07-19）| S-M | 已交付 toolsets 死配置清理、cron 后台失败 HIGH 通知、bench infra 两债、容器交付不做结论与 console_output 窄终端适配。单用户常驻由 OS service 托管，Cloudflare 出站 tunnel 不要求容器网络 | ② |

**首波组合态验收（2026-07-19，本地叠加未 push 等拍板）**：F134（21c）→F146（5c）→F145（8c）串行 rebase 叠加，代码零冲突（仅 milestones 三方 ✅ 行文档冲突，含 F145 多 docs commit 同型冲突循环解）。联合验收全绿：**后端确定性全量 5409 passed / 0 failed**（`-m 'not real_llm'` F141 baseline lane 同款，xdist 并行 3min）+ e2e_smoke/scripted 26 + **frontdoor 矩阵 42 passed**（F144 17 格 + F134 限流扩格）+ 前端 vitest **428/46** + complexity/tsc 全过 + **L1 Playwright 4/4**（含 F145 新增审批点击场景，10.6s）。venv/npm/dist 三收敛惯例照做。

**验收项（非 Feature）**：某次重启 Mac 后签 ATT-129-BOOT（attestation 清单唯一残余，不阻止 M10 功能收官）。

**4 Feature 落地闭环（2026-07-19）**：F145 审批中心、F134 bearer 加固、F146 平台一致性与 F147 清扫篮全合入 master，CI 全绿。F147 同时修复 429 退避吃决策步数与 cron 失败无 HIGH 通知两项真实债务。

**M10 收官（2026-07-19，功能层全 ✅）**：F145、F134、F146、F147 全部完成，5419 passed / 0 regression。剩余 ATT-129-BOOT 是独立物理验收；远程访问统一由 M11 F150 实施。

**2026-07-20 二次架构复审后的 P0**：先把当前旧 VPN 方案删除工作形成可验证 baseline；F150 仅提前做真实 named-tunnel/SSE/Access spike，用于退休外部可行性风险。生产实现先完成 F151 运行与打包边界，再按 F150 → F149 推进。ATT-129-BOOT 可独立完成，不作为 F151/F150 前置。

---

### M11（运行边界收口 + Cloudflare 远程访问 + Web 工作台 v2）⏳ 进行中（2026-07-20 二次重评）

> **来源**：用户在 Claude Design 产出 Web + Mobile 两份 v2 设计稿（Spotify 深色风 · project 851e3fb2），F148 已完成主工作台。2026-07-20 又对 Octo、OpenClaw、Hermes Agent、Agent Zero 做固定版本源码审计，结论见 [architecture-audit.md](architecture-audit.md) §14.14：三个项目都值得借局部运行机制，但它们的兼容层、全局状态、Plugin/Memory 膨胀和 host-first 安全模型不应成为 Octo 的目标架构。
> **架构定位**：Octo 保持单用户、单 Gateway application host 的模块化单体；`apps/kernel`、`workers/*` 是历史物理拆分设想，不新建这些目录、`packages/management` 或第二套 runtime。先修真实依赖环、打包与失真配置，再开放公网；God service 只按后续触达的垂直切片收敛，不做 big-bang 重写。
> **远程产品定位（2026-07-20 二次重评）**：Cloudflare named tunnel + Access 是唯一远程访问路径。手机使用标准浏览器，不安装网络客户端，不改变已有网络连接；Gateway 保持 loopback 回源，Access 边缘认证后由 origin 验证 JWT 与 owner identity。浏览器直接复用 Access application session，不再增加配对码、Octo Cookie、remote session/browser device 表；真正设备身份统一归 F153。
> **设计与文案硬约束**：① Butler→主 Agent，LiteLLM→ProviderRouter，普通页面禁暴露 JWT/AUD/JWKS 等内部词；②继续消费 F148 `--cp-*` 深色 token，不造第二套主题、不向 `index.css` 堆叠；③ Cloudflare 在边缘终止 TLS，禁写“端到端加密”；④ F150 删除二次配对 UI，只补“未配置 / Access 登录 / 可用 / 会话过期或登出 / 故障恢复”的手机与桌面状态；⑤ `#1ed760` 个人自用可，未来公开分发前换成自有品牌色。

| Feature | 规模 | 一句话 | 波次 |
|---------|------|--------|------|
| **F148 设计系统 + Web 主工作台 v2** ✅ 完成（2026-07-20，9 commits ff push master；Codex spec+final 2 finding 全闭环 + Opus 自审 0 HIGH；438 vitest + L1 4/4 + tsc 0 + complexity 过）| L | 已交付：**Phase 0** `tokens.css` `--cp-*` 原地翻转 Spotify 深色（committed dark 删冗余 dark-media 块，不并造第二套）+ Figtree/remixicon 自托管 + `theme-v2.css` 三动画（octoPulse/octoBar/octoJelly）+ 旧 accent 覆盖；**Phase 1** 三栏——左栏会话按 `project_id` 分组+折叠+octoBar 运行指示+就绪卡 / 中栏对话加壳（内核 JSX 保留）+octoJelly 空舞台 / 右栏新 `SessionRunPanel`（本会话运行状态只读镜像：状态/进度/事件流/工件/打开任务）；**Phase 2** `GlobalTaskOverlay`（读 `delegation.works` 同源同状态词表）；**Phase 3** 加载页 octoPulse。**纯前端零后端**（勘察先验：跨项目会话+当前运行任务现成；多并发任务列表 defer）。**复用数据逻辑只换视觉/结构**（零新 hook/fetch/协议）。文案映射 Butler→主 Agent、无 LiteLLM 泄漏。`index.css` 4477 零增长。**限制**：原稿逐像素未自证（DesignSync 不可达，按 §M11 书面规格+Spotify 语言实现）/ 右栏停止控制+多任务列表 defer / octo-mark 已换设计稿绿泡泡（主 session 从原稿 assets 取，subagent DesignSync 不可达） / F149 页 accent 残留渐进边界。详见 `.specify/features/148-web-workbench-v2/completion-report.md` | ① |
| **F151 Runtime Boundary & Architecture Truth** 📋 编号预留、待立项 | XL | M11 生产改动前的硬门：把 `provider/dx` 迁入既有 `apps/gateway/cli`，禁止 Provider 反向 import Gateway；删除 LiteLLM Proxy、`.env.litellm`、SDK 平行调用与 no-op compatibility；删除/诚实重命名“required 但仍 inline”的 Docker 配置；补 Gateway 直接依赖、clean-wheel 安装/启动、security/backend fail-closed 契约；用显式 runtime service bundle 收敛 class-level 注入，并给 `task_service`/`orchestrator` 等热点设“不再恶化”ratchet。同步 constitution/Blueprint/实现级文档。禁为此新增 management/kernel/worker 包或大爆炸重写 | ① 硬前置 |
| **F150 Cloudflare 零信任远程** ⏳ 二次重评 spec 待评审（2026-07-20）| M | 唯一远程入口：named tunnel + 官方 service、Gateway loopback 回源、Access 全站保护、origin JWT + owner allowlist、Host/Origin/CSRF、真实 SSE gate。复用 Access application session，F150 零新增配对/session/device 表；禁 quick tunnel、浏览器 bearer/service token 与 provider/profile 抽象。Phase 0 live spike 可先于 F151，生产实现不可越过 F151 gate。制品 `.specify/features/150-cloudflare-tunnel/` | ② |
| **F149 Web 其余页面 v2** 📋 待立项 | M-L | 两波完成全站：A 波=审批/任务/自动化/设置高频页；B 波=智能体/记忆/文件/技能/MCP 高信息密度页。网络 DTO 从 OpenAPI 生成，手写类型只保留 UI view model；所有页面消费 F148 token，普通界面不暴露内部字段，并补手机宽度、空态、失败态、权限态 L1。设计 recon 可并行，生产实现等 F150 契约稳定 | ③ |

**新波次**：0️⃣ 关闭当前旧 VPN 方案删除工作并建立全绿 baseline；并行只做 F150 真实链路 spike → ① F151 完整 Spec Driver 闭环 → ② F150 实施与 live 验收（F149 同期只做设计 recon）→ ③ F149 两波全站 v2。

**推进协议**：Spec Driver 是外层唯一研发流程（constitution → spec/research → plan → tasks → implement → verify）；每次只有一个生产 Feature 处于 implement。Superpowers 的小任务、先写失败测试、频繁 review 用作 Feature 内执行纪律，不另建第二套制品或状态机。每个 task 应是可独立验证的垂直切片，竞品代码只作为 evidence，不作为兼容目标。

**M11 完成定义**：F148/F149/F150/F151 全部 ✅；Cloudflare owner/JWKS/Host/Origin/CSRF fail-closed、Access 登出/过期与真实 SSE 延迟通过；手机无需额外网络客户端或二次配对；Web v2 全站切换；干净环境安装 Gateway wheel 可启动；Provider 无 Gateway 反向 import；Blueprint、constitution 与实现级文档不再描述已退役的 Proxy/Docker backend。

### M12（原生 iOS + 健康/日程感知）📋 设计门禁先行（2026-07-20 重排）

> **为何独立**：HealthKit/EventKit 是新的高敏感数据入口与 Agent 能力域，不是 Web UI 的原生外壳。M12 必须先回答“设备如何可信连接、数据如何最小化、什么能进 LLM、如何撤销/删除/审计”，再做 SwiftUI 页面。
> **安全翻转**：旧 F150 handoff 提议在 iOS App 使用 `CF-Access-Client-Id/Secret`，现已否决。静态 service secret 一旦进入 App 包就不是可信 secret。F152/F153 必须从交互式 Access 或设备注册换短期凭证中完成设计，并支持撤销、轮换和单设备审计。
> **浏览器与原生分界**：F150 的 Access browser session 不能直接给 `URLSession` 当原生设备凭证。F153 必须先用真实 iPhone spike 在“交互式 Access user session / 独立 mobile API + Octo device proof / 可用套餐下的 mTLS”中选出无需内置 service secret 的可行路径；未通过前不得读取 HealthKit。
> **数据安全不变量**：Memory/召回内容是不可信证据，不是指令；raw sample、normalized fact、retrieval snapshot、transcript/LLM context 四层分开。每次进入 LLM 或 Memory 的事实都需 provenance、consent、delete/revoke audit；高敏感写入不能采用 shutdown 时允许丢写的 best-effort 语义。
> **Apple 权限事实**：[HealthKit 读权限按数据类型授权](https://developer.apple.com/documentation/HealthKit/authorizing-access-to-health-data)，App 不能把“用户拒绝读取”与“没有数据”简单区分；[EventKit 读取日历在当前 iOS API 下需要 full access](https://developer.apple.com/documentation/eventkit/accessing-the-event-store)，没有 OS 级 read-only 权限。F155 的“只读”只能是 Octo 代码与产品承诺，不能误写为系统最小权限。
> **启动依赖**：M11 的 F150 浏览器远程和 F151 架构/打包边界均完成；M12 privacy threat model 通过 review。设计 token 可复用视觉语言，但移动端信息架构由 Claude Design 重新校正，不照搬 Web 三栏。

| Feature | 规模 | 目标 | 顺序 |
|---------|------|------|------|
| **F152 Privacy, Identity & Ingestion Contract** 📋 编号预留、待立项 | L | threat model + 数据分类：raw sample / normalized fact / retrieval snapshot / transcript/LLM context / Memory；定义 provenance、consent、撤销、删除、审计、TTL 与 device capability。明确 Memory 是不可信证据、哪些数据永不进模型、哪些每次需批准 | ① 严格前置 |
| **F153 iOS Device Trust & Secure Transport** 📋 编号预留、待立项 | L-XL | 先做真机 transport spike，再冻结方案；设备生成密钥，browser/owner 辅助 challenge 注册，短期 capability-scoped token，Keychain 保存、proof-of-possession、单设备撤销/轮换。复用同一 tunnel 但不内置 Cloudflare service secret；只打通 `/ready` + 最小 API，不接 HealthKit | ② |
| **F154 HealthKit Read-Only Vertical Slice** 📋 编号预留、待立项 | L-XL | 按最小数据类型授权 → 只读样本 → 本地归一化 → 用户预览/批准 → 单次 Agent 分析 → 审计。UI 必须把“无可读数据/权限受限”作为诚实状态；v0.1 不后台全量同步、不自动写 Memory | ③ |
| **F155 EventKit App-Read-Only Vertical Slice（OS Full-Access Gate）** 📋 编号预留、待立项 | L | 启动前由用户明确接受“系统要求 full access、Octo 实现层不写日历”。写路径在代码和 capability 中物理缺席；只读限定时间范围、预览批准和单次分析，正文默认不进长期记忆。若不能接受该权限，F152 review 时将本 Feature 移出 M12，而不是伪装成系统 read-only | ④ 决策门 |
| **F156 Native Companion Experience** 📋 编号预留、待立项 | XL | SwiftUI 对话、任务、审批、记忆候选、连接状态与通知；只消费 F152-F155 已证明的认证/数据能力，不复制 Web 三栏或另造状态词表。APNs、后台刷新和上架准备在能力链通过后进入 | ⑤ |

**M12 波次**：F152 → F153 → F154 → F155 → F156，默认严格串行。只有视觉探索可与 F152/F153 并行，生产代码不得绕过隐私/身份 gate。

**M12 完成定义**：无静态服务端 secret 进入 App；设备密钥、短期 capability token 与单设备撤销可验证；HealthKit 权限状态和数据删除真实可测；若实施 EventKit，UI 明示 OS full access 且产品写路径物理不存在；raw/normalized/retrieval/transcript/Memory 边界可审计；每个敏感垂直切片先经用户预览批准再分析；SwiftUI/L1/真机权限、断网恢复和撤销后拒绝全部通过。
