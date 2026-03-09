# M3 Feature 拆分方案（v1.1）

> **文档类型**: 里程碑拆分方案（Implementation Planning）  
> **依据**: `docs/blueprint.md` §8.7 + §8.9.4 + §14（M3 定义）+ 本轮 OpenClaw / Agent Zero 深度调研  
> **状态**: v1.3 — 024-031 已交付；2026-03-09 新增 Feature 033 作为 Agent context continuity 补位设计
> **日期**: 2026-03-09

---

## 1. 背景与目标

### 1.1 当前基线

截至 2026-03-09，M3 的主功能线已经基本交付：

- 024 已交付 installer / updater / doctor-migrate / verify operator flow
- 025 已交付 project/workspace、default project migration、secret store、统一 wizard、asset manifest
- 026 已交付正式 control plane backend 与 Web 控制台
- 027 已交付 Memory Console + Vault authorized retrieval
- 028 已交付 MemU deep integration 与 degrade path
- 029 已交付 WeChat Import + Multi-source Import Workbench
- 030 已交付 capability pack、ToolIndex、Delegation Plane、Skill Pipeline

031 完成后，原本判断当前剩余工作主要是发布后的持续硬化项；但 2026-03-09 的 live-usage 复核暴露出一个新的结构性缺口：

- Feature 031 已补齐 acceptance 制品、release report 和 remaining risks 清单
- control-plane 的 front-door 部署边界已经写入正式验收门禁，并对 `loopback` 模式补了代理转发 header 的 fail-closed 拒绝
- OpenClaw -> OctoAgent 迁移演练已经完成，后续差距主要转入 live cutover 与长期运维阶段
- 新增发现：主 Agent 运行时仍未真正消费 `AgentProfile`、owner basics、bootstrap guidance、recent session summary 与 long-term memory retrieval；当前 `TaskService -> LLMService` 仍基本以原始 `user_text` 驱动

### 1.2 本轮复核后的结论

M3 的功能建设已经足够完整，但复核结论需修正为：

1. **M3 主功能线 024-031 已交付**：Project、Control Plane、Memory Console、MemU、Import Workbench、Delegation Plane 和 acceptance harness 都已存在。
2. **031 原范围已完成 release 收口**：已具备独立 spec、release gates、验收矩阵、迁移演练和最终报告；但 M3 最终签收仍受 033 的 context continuity gate 阻塞。
3. **033 成为新增 cutover-blocking 补位 Feature**：因为当前主 Agent 的上下文连续性没有真正落到运行链，M3 仍缺一条“日常可用”的主链闭环。
4. **公网边界必须继续按 front-door 约束执行**：当前产品适合单 owner / localhost、bearer 或 trusted-network 部署，不应裸暴露。
5. **OpenClaw 迁移演练已纳入 M3 签收**：但正式 live cutover 前，应优先完成 033，而不是先开启 M4 体验增强。

### 1.3 调研证据（不仅 README）

| 来源 | 关键证据 | 可借鉴点 |
|---|---|---|
| OpenClaw | `docs/start/wizard.md`、`docs/experiments/onboarding-config-protocol.md` | 统一 onboarding wizard，CLI / Web 共用一套 wizard + config protocol |
| OpenClaw | `docs/cli/secrets.md` | `audit / configure / apply / reload` 的 SecretRef 生命周期管理 |
| OpenClaw | `docs/install/installer.md`、`docs/install/updating.md` | 安装、升级、doctor、restart 串成连续 operator flow |
| OpenClaw | `docs/web/control-ui.md`、`docs/web/dashboard.md`、`ui/package.json` | 以 Control UI 为中心的管理面，配置、更新、频道、cron、skills 聚合到一个控制台 |
| OpenClaw | `docs/tools/slash-commands.md` | Telegram / Discord 原生命令、`/approve`、`/model`、`/skill`、`/subagents`、`/acp` 的统一命令面 |
| OpenClaw | `docs/tools/skills.md`、`docs/start/bootstrapping.md` | 内置 skill 平台、bundled/managed/workspace skills，以及 `BOOTSTRAP.md` / `AGENTS.md` 的首次运行能力注入 |
| OpenClaw | `docs/tools/subagents.md`、`docs/tools/acp-agents.md` | `sessions_spawn`、nested subagents、ACP runtime、thread-bound session、orchestrator pattern |
| Agent Zero | `python/helpers/settings.py`、`initialize.py` | “设置中心”先于复杂架构，配置入口统一 |
| Agent Zero | `python/helpers/secrets.py` | secret alias / placeholder 的用户体验，比直接暴露明文 env 更进一步 |
| Agent Zero | `python/api/memory_dashboard.py`、`python/helpers/memory.py`、`python/helpers/memory_consolidation.py` | Memory dashboard、记忆检索/编辑/合并，是用户真正能感知到的 memory 能力 |
| Agent Zero | `python/api/backup_create.py`、`python/api/backup_restore_preview.py` | backup/restore 走 API 和 UI 面，而不是只靠底层脚本 |
| Agent Zero | `docs/guides/projects.md` | projects = instructions + memory + secrets + files + subagent config 的统一隔离单位 |
| Agent Zero | `docs/guides/a2a-setup.md`、`python/tools/call_subordinate.py` | A2A server/client + subordinate agent 委派能力 |

### 1.4 M3 总目标

M3 的一句话目标：

> **把 OctoAgent 从“工程师可运行的 Personal AI OS”推进到“普通用户 Ready 的 Personal AI OS”。**

### 1.5 第二轮专项复核（2026-03-07）

| 专项 | 参考产品事实 | M3 承接位置 |
|---|---|---|
| Project / Workspace 一等公民 | Agent Zero 已把 projects 做成 instructions + memory + secrets + files + A2A context 的统一隔离单位 | Feature 025 |
| Telegram 指令能力 | OpenClaw 已把 `/approve`、`/model`、`/skill`、`/subagents`、`/acp` 收敛到统一命令面 | Feature 026 |
| Session / Chat Lifecycle | OpenClaw 有 `/new`、`/reset`、`/export-session`、`/focus`、`/queue`；Agent Zero 有 save/load chats 与消息排队 | Feature 026 |
| Scheduler / Automation | OpenClaw Control UI 内建 cron jobs；Agent Zero 已把 scheduler 做成带 project support 的正式 UI | Feature 026 |
| Runtime Diagnostics Console | OpenClaw Control UI 有 status / logs / models / update；Agent Zero 有 dashboard / settings / memory dashboard | Feature 026 |
| 内置 Skill/Tools 与 Bootstrap | OpenClaw 有 bundled skills + bootstrapping；Agent Zero 有 built-in tools + projects/skills UI | Feature 030 |
| A2A / Worker / Subagent / Graph Agent | OpenClaw 有 `sessions_spawn` + nested subagents + ACP；Agent Zero 有 subordinate + A2A server/client | Feature 030 |
| 管理 UI（agents/memory/权限/secrets/status） | OpenClaw Control UI + Agent Zero settings/projects/memory dashboard 已覆盖大部分 operator 面 | Feature 026 + Feature 027 |
| 发布前收口 | OpenClaw 把 wizard、Control UI、updating、export session 组织成连续用户路径；Agent Zero 把 projects、backup、memory、tunnel 作为正式 operator flow | Feature 031 |
| 主 Agent 上下文连续性 | OpenClaw 用 bootstrapping + `AGENTS.md` / `USER.md` / `SOUL.md` 建立 session startup contract；Agent Zero 用 projects + memory + settings 让主会话保持长期连续性 | Feature 033 |

---

## 2. 设计约束（M3 必须遵守）

### 2.1 安装与升级约束

- 用户应能通过一键安装入口完成运行时依赖准备、初始配置和首次 dashboard 打开
- 升级必须包含 `preflight -> migrate -> restart -> verify` 流程，失败时有恢复建议
- 不允许把“升级后自己 rerun 一堆脚本”当作默认用户路径

### 2.2 配置与 Secret 约束

- 环境变量继续保留，但只作为 CI / 容器编排 / 高级用户路径
- 普通用户默认走统一 Secret Store，不要求记住要 `export` 哪些变量
- Provider、channel、gateway、workspace、model 选择必须收敛到同一套配置入口
- secret 必须按 `global provider auth`、`project binding`、`runtime injection` 分层管理
- `project` 必须是正式产品对象，而不是 secret binding 的附属概念；instructions、memory、knowledge、files、A2A/channel target 都应优先落在 project 上

### 2.3 Web 管理台约束

- Web 不是“辅助页面”，而是 M3 的主控制面之一
- 配置中心、channel/device 管理、approvals、backup/restore、memory 浏览必须统一在同一控制台
- Agents、permissions、secrets、runtime status 必须可见可管，不能只在日志或底层配置文件里
- session/chat lifecycle 必须进入正式产品面，不能只保留 task/event 视角
- automation/scheduler 必须是用户可创建、可观察、可重放的能力，而不是只在底层留调度器
- runtime diagnostics 必须是控制台正式组成部分，至少覆盖 health、logs、event stream、usage/cost、provider/model、worker/subagent/work 状态
- UI 优先复用成熟开源组件和表单/状态库，而不是自研一整套控件系统

### 2.4 Channel Command / Delegation 约束

- Telegram / Web 必须共用同一控制动作语义：approve、model switch、skill invoke、subagent/work control、status query
- 命令面必须有最小授权模型，避免未授权发送者直接切模型、调用技能或控制子代理
- 主 Agent、Worker、Subagent、ACP-like runtime、Graph Agent 必须有清晰的 session / work / ownership 边界
- Work 的创建、委派、合并、取消、超时与人工介入都必须事件化、可追溯、可回放

### 2.5 Memory 约束

- SoR / Fragments / Vault / WriteProposal 继续是唯一治理内核
- MemU 的高级能力必须通过这套治理模型输出，不能旁路写入权威事实
- 多模态记忆、Category、ToM 等结果必须带证据链，且可在管理台中追溯
- MemU 不可用时系统必须自动降级回核心 Memory 能力

### 2.6 核心对象关系与术语收口（2026-03-08 补充）

| 对象 | 归属 / 作用域 | 主要承载 | 默认继承来源 | 说明 |
|---|---|---|---|---|
| `Project` | Owner 显式选择的一级产品对象 | instructions、memory bindings、secret bindings、asset bindings、channel/A2A routing | system defaults | M3 的根隔离单位 |
| `AgentProfile` | system 或 project 作用域的可复用模板 | persona、instruction overlays、model route、tool profile、capability pack refs、policy refs、budget defaults | project | 供 session / automation / work 选择 |
| `Session` | 严格隶属于一个 project | history、queue、focus、effective config snapshot | project + selected agent profile | 正式会话对象 |
| `Automation` | 严格隶属于一个 project | schedule、target、run history、effective config snapshot | project + selected agent profile | 可创建 session 或直接派生 work |
| `Work` | 隶属于一个 session 或 automation | delegation graph、owner、children、artifacts、budget、state | session 或 automation | 委派/合并/回放的一等单位 |

补充约束：

- 必须明确 `project -> session -> work` 与 `project -> automation -> work` 两条继承链，禁止在运行时临时拼装一套不可追溯的 effective config
- `AgentProfile` / `WorkerProfile` 必须是正式产品对象，不能只作为 `AGENTS.md` 或 bootstrap 文件里的隐式约定
- `project/workspace` 至少要有最小 asset manifest 能力（upload / list / inspect / bind）；完整 file browser / editor / diff 可以延后到 M4
- 术语必须收敛：`tool profile`、`auth profile`、`agent profile`、`readiness level` 分开命名；除记忆分区外不再使用裸 `profile`

### 2.7 发布 / 迁移 / 信任边界约束（2026-03-08 追加）

- Feature 031 不得再引入新业务能力，只做收口验收、最小接缝修补与 release report
- 在当前实现下，control-plane / ops 入口仍按单 owner、localhost 或 trusted-network 心智使用；如果没有新增 front-door auth，则文档、默认部署方式和验收报告必须明确禁止“默认可直接公网暴露”的暗示
- M3 正式签收前必须完成一次 OpenClaw -> OctoAgent 迁移演练，至少覆盖 project 建立、secret 处理、导入、memory 审计、dashboard 操作与 rollback 记录
- 验收 harness 必须考虑共享 `.venv` 并发 `uv run` 的环境竞争；需要串行化相关步骤或显式使用隔离环境，避免把工具链竞争误判成产品不稳定

### 2.8 Agent Context Continuity 约束（2026-03-09 追加）

- `AgentProfile` 不能只作为 blueprint 中的术语或 bootstrap 文件里的隐式约定，必须是正式 durable object
- owner basics / assistant identity / bootstrap guidance 必须进入主 Agent 的真实运行链，而不是只停留在配置或文档层
- 短期上下文连续性必须 durable，不能只依赖进程内 history
- 长期 Memory 检索必须真正进入主 Agent / automation / delegation 路径，但不得绕过 020/027/028 的治理边界
- control plane 必须能够解释“本次回答用了哪些 profile/bootstrap/recent summary/memory hits”，否则 033 视为未完成

---

## 3. 并行拆分方案（M3 = 8 个主 Feature + 1 个补位 Feature）

### 3.1 依赖图

```text
M2 收口
   │
   ├── Track A: 用户上手与运维
   │   ├── Feature 024：Installer + Updater + Doctor/Migrate
   │   └── Feature 025：Unified Config Wizard + Secret Store + Project Workspace
   │
   ├── Track B: 控制面产品化
   │   └── Feature 026：Command Surface + Session Center + Scheduler + Runtime Console
   │
   ├── Track C: Memory 产品化
   │   ├── Feature 027：Memory Console + Vault Authorized Retrieval
   │   └── Feature 028：MemU Deep Integration
   │
   ├── Track D: 增强能力
   │   ├── Feature 029：WeChat Import + Multi-source Import Workbench
   │   └── Feature 030：Built-in Capability Pack + Delegation Plane + Skill Pipeline
   │
   └── 全部汇合
       └── Feature 031：M3 User-Ready E2E Acceptance
```

031 完成后新增：

```text
Feature 025 + 027 + 030 + 031
   └── Feature 033：Agent Profile + Bootstrap + Context Continuity
```

截至 2026-03-09，Feature 024-031 已全部合入 `master`；新增 033 不推翻既有依赖图，而是用于修补“主 Agent 没有真正消费 profile/bootstrap/memory”这一条被 031 复核遗漏的主链缺口。

### 3.2 并行化原则

1. **先打通用户主路径，再做高级能力炫技**：024/025/026 的优先级高于 028/030。
2. **UI 与配置协议共用一个事实源**：Wizard step model、config schema、UI hints 不能在 CLI/Web 各自为政。
3. **Memory 产品化先于 Memory 炫技**：先让用户能看懂、检索、追溯，再上多模态 / ToM / Category。
4. **高级能力不能破坏治理边界**：MemU、ToolIndex、Skill Pipeline 全部只能建立在现有 Event / SoR / Approval 模型之上。

---

## 4. Feature 详细拆解

### Feature 024：Installer + Updater + Doctor/Migrate

**实现状态**：已交付（截至 2026-03-08 已合入 master）

**一句话目标**：把安装、升级、迁移和修复做成一条可重复、可恢复、可验证的 operator flow。

**借鉴来源**：

- OpenClaw `install.sh` / `install-cli.sh` / `openclaw update` / `openclaw doctor`
- Agent Zero `initialize.py` / `migration.py` / `update_check.py`

**任务拆解**：

- F024-T01：设计一键安装入口（CLI install script / App 首启 / 本地 prefix 安装三种模式）
- F024-T02：实现 `octo update`，支持 `--dry-run`、版本通道、依赖更新、doctor 预检
- F024-T03：实现 `octo migrate` / 自动迁移注册表，处理 SQLite schema、config schema、service entrypoint 演进
- F024-T04：升级失败时生成结构化报告，并给出 rollback / restore 建议
- F024-T05：把 upgrade/restart/verify 能力接入 Web 管理台

**验收标准**：

- 新用户可在单条安装入口中完成依赖准备和 OctoAgent 安装
- 已安装实例可通过 `octo update` 或 Web 按钮执行安全升级
- 升级前会执行 doctor/preflight，升级后自动给出健康检查结果
- 迁移失败时不会把实例留在半损坏状态

---

### Feature 025：Unified Config Wizard + Secret Store + Project Workspace

**实现状态**：已交付（截至 2026-03-08 已合入 master）

**一句话目标**：把 Provider、Telegram、Gateway、模型选择、secret 生命周期与 project/workspace 隔离统一到一条配置主路径中，环境变量退居高级路径。

**借鉴来源**：

- OpenClaw `openclaw onboard`、`openclaw configure`、`openclaw secrets`
- OpenClaw onboarding/config protocol（wizard.start / wizard.next / config.schema）
- Agent Zero settings center + secret placeholder 模式

**任务拆解**：

- F025-T01：定义统一 wizard session 协议，CLI / Web 共用同一套 step model
- F025-T02：定义 `Project` 作为正式产品对象，统一承载：
  - instructions / default agent profile binding
  - memory mode / partition bindings
  - secret bindings
  - knowledge / files / workspace asset bindings
  - channel / A2A target bindings
- F025-T03：设计 OctoAgent Secret Store 分层：
  - global provider auth store
  - project-scoped bindings
  - runtime short-lived injection
- F025-T04：实现 `octo project create/select/edit/inspect` 与 project selector 协议（CLI / Web 共用）
- F025-T05：实现 `octo secrets audit/configure/apply/reload/rotate`
- F025-T06：收敛 Provider Key、OAuth Token、Telegram Bot Token、Gateway Token、Webhook Secret 的默认存储位置
- F025-T07：在普通用户路径中移除“必须手工 export env 才能跑起来”的依赖
- F025-T08：支持 SecretRef（env / file / exec / OS keychain fallback）与审计/轮换
- F025-T09：实现 project asset manifest 的最小能力：upload / list / inspect / bind（knowledge / files / artifacts 共用）

**验收标准**：

- 新用户通过一个向导即可完成 provider、channel、gateway、model 的配置
- 用户可以创建 / 选择 / 切换 project，并把 instructions、default agent profile、memory、secrets、files、routing 统一绑定到 project
- 正常用户路径不再需要手工维护多处 `.env`
- 所有高价值 secret 都能在同一入口中完成审计、应用、reload 和轮换
- secret 不进入日志、事件、LLM 上下文，且按 project/scope 隔离
- project 至少提供 asset manifest 的 upload / list / inspect / bind 路径，使 knowledge/files/artifacts 有稳定挂载点

---

### Feature 026：Command Surface + Session Center + Scheduler + Runtime Console

**一句话目标**：把 Telegram / Web 控制动作、会话生命周期、automation/scheduler 与 runtime diagnostics 统一成同一套 operator surface，并把现有最小 Web UI 进化成真正的控制台。

**借鉴来源**：

- OpenClaw slash commands + native commands + Control UI
- Agent Zero 的 settings / backup / memory dashboard API-first 设计

**任务拆解**：

- F026-T01：设计统一 command/action registry，保证 Telegram 与 Web 共享同一动作语义
- F026-T02：提供最小 Telegram 控制命令：`approve`、model 切换、skill 调用、subagent/work 控制、status 查询
- F026-T03：设计 session/chat center，覆盖 history、export、queue、focus/unfocus、reset/new、interrupt/resume
- F026-T04：重构前端信息架构：Dashboard / Projects / Sessions / Tasks / Operator / Config / Channels / Agents / Recovery / Memory / Security
- F026-T05：实现配置中心，基于 `config.schema + uiHints` 渲染表单
- F026-T06：实现 channel/device 管理面（Telegram pairing、device trust、token 状态、channel readiness）
- F026-T07：把 approvals / retry / cancel / alert acknowledge / backup / restore / import / update 收敛到管理台
- F026-T08：补齐 agents / permissions / secrets / runtime status 的管理与查询面
- F026-T09：实现 automation / scheduler 面板（create / run / pause / resume / run history / project binding）
- F026-T10：实现 runtime diagnostics console（health、logs、event stream、provider/model 状态、usage/cost、worker/subagent/work graph 状态）
- F026-T11：接入 Memory 浏览、Vault 授权、证据追溯视图

**UI/UX 技术选型建议**：

- 保持 **React 19 + Vite** 基线，不建议为对齐 OpenClaw 而迁移到 Lit
- 新增 **Tailwind CSS + shadcn/ui + Radix UI** 作为控件与布局底座，减少表单、drawer、dialog、tabs、table 的重复建设
- 使用 **TanStack Query** 管理 gateway 状态与轮询/失效重取
- 使用 **React Hook Form** 处理关键向导表单；配置中心补充 **JSON Schema Form** 路径（建议 `@rjsf/core`）以加速广覆盖字段渲染
- 图表与诊断面板优先选用 **Recharts**；复杂时序与 drill-down 再评估 ECharts
- M3 设计阶段可结合 `ui-ux-pro-max` 生成 design system、响应式和无障碍规则

**验收标准**：

- 用户可以通过 Telegram 或 Web 完成最基本的 operator 动作，而非只能“看不能控”
- 用户可以仅通过 Web 控制台完成大多数日常操作，而非依赖终端
- 用户可以在 session/chat center 中完成 history/export、queue、focus/reset、interrupt/resume 等日常会话操作
- 配置中心具备 schema 驱动表单与基础校验
- 用户可以创建 recurring automation 并查看 run history，且 automation 可以显式绑定到 project / channel / target
- diagnostics console 可以查看 health、logs、event stream、provider/model、usage/cost 与 worker/subagent/work graph 运行态
- approval / recovery / channel / agents / permissions / secrets / memory / update 都有明确入口和状态反馈
- 控制台在桌面与移动端都能稳定工作

**交付状态（2026-03-08）**：

- 已交付统一 control-plane backend：六类 canonical resources、`ActionRegistryDocument`、`ActionRequest/ActionResultEnvelope`、`ControlPlaneEvent`、snapshot/per-resource/actions/events routes。
- 已打通 Telegram / Web 共用 action semantics，现有控制命令与 Web 操作统一落到同一 action registry。
- 已交付正式 Web Control Plane：首页切换为 `Dashboard / Projects / Sessions / Operator / Automation / Diagnostics / Config / Channels`。
- 已交付 Session Center、Automation/Scheduler 面板、Runtime Diagnostics Console、配置中心、channel/device 管理入口，以及 approvals/retry/cancel/backup/restore/import/update 的统一控制台入口。
- Memory/Vault detailed view 已由 Feature 027 收口；Secret Store 实值管理与 Wizard 深交互已由 Feature 025 收口，并通过现有 control-plane 资源消费。

---

### Feature 027：Memory Console + Vault Authorized Retrieval

**实现状态**：已交付（2026-03-08）

**一句话目标**：把 Memory 从“系统内部能力”变成“用户可理解、可检视、可授权”的产品面。

**借鉴来源**：

- Agent Zero `memory_dashboard.py`
- 当前 OctoAgent SoR / Fragments / Vault / WriteProposal 设计

**任务拆解**：

- F027-T01：实现 Memory 浏览器，按 partition / scope / layer 浏览 SoR、Fragments、Vault 引用
- F027-T02：展示 `subject_key` 的 current / superseded 历史与 evidence refs
- F027-T03：实现 Vault 授权检索面板（授权申请、授权记录、检索结果证据链）
- F027-T04：实现 WriteProposal 审计视图（提案来源、验证结果、commit 状态）
- F027-T05：补齐 memory 相关权限模型（谁能看 SoR、谁能申请 Vault、谁能执行 delete/update）
- F027-T06：实现 Memory export / inspect / restore 校验入口

**验收标准**：

- 用户可以在 UI 中看懂某条记忆的来源、当前版本、相关 agent/work 和证据
- Vault 默认不可检索，授权后才可查阅，并留下审计记录
- Memory 浏览不暴露敏感原文，除非对应授权已生效

---

### Feature 028：MemU Deep Integration

**实现状态**：已交付（2026-03-08）

**一句话目标**：把 MemU 从“可选 adapter”提升为 OctoAgent Memory 体系中的高级 engine，但不破坏现有治理模型。

**借鉴来源**：

- `MemUBackend` 预留接口
- Agent Zero 的 memory consolidation / similarity recall 经验
- OctoAgent 当前 SoR / Fragments / Vault / WriteProposal 模型

**任务拆解**：

- F028-T01：完善 `MemUBackend` 协议，实现检索、索引、增量同步和健康诊断
- F028-T02：实现多模态 ingest 管线（文本 / 图片 / 音频 / 文档 → Fragments / artifact refs）
- F028-T03：实现 Category / relation / entity / ToM 等高级派生层
- F028-T04：把高级结果约束为：
  - `Fragments`
  - 派生索引
  - `WriteProposal`
  三种输出之一，不允许直接旁路写 SoR
- F028-T05：实现 Memory consolidation / compaction / flush 的可审计执行链
- F028-T06：建立 MemU 不可用时的自动降级与回切策略

**产品设计原则**：

- SoR 只保存经过仲裁的权威事实
- Fragments 保存原始证据与可检索摘要
- Vault 控制敏感内容访问
- MemU 的多模态、Category、ToM 结果属于“智能层”，不是“真相层”

**验收标准**：

- MemU 可作为主检索 backend 提供更强 recall，但任何落盘事实仍经过 WriteProposal 治理
- 多模态 / Category / ToM 结果都能追溯到 artifact / fragment 证据
- MemU 失效时系统自动降级，主聊天与核心检索不整体失效

---

### Feature 029：WeChat Import + Multi-source Import Workbench

**实现状态**：已交付（2026-03-08）

**一句话目标**：把导入从单条 CLI 命令扩展成用户可操作、可预览、可修正的多源导入工作台。

**借鉴来源**：

- M2 Chat Import Core
- Agent Zero backup/import 的 API 化路径

**任务拆解**：

- F029-T01：实现微信导入插件与 source-specific adapter
- F029-T02：实现导入工作台（dry-run、mapping、dedupe 结果、cursor/resume）
- F029-T03：支持多源附件进入 artifact / fragment / MemU 管线
- F029-T04：把导入提案与 Memory proposal / commit 打通
- F029-T05：在管理台提供导入报告、warnings、errors、resume 入口

**验收标准**：

- 用户可在 UI 中预览导入影响，再决定是否执行
- 导入结果可追溯到 chat scope / artifacts / memory changes
- 多源导入不会污染主聊天和无关 partition

---

### Feature 030：Built-in Capability Pack + Delegation Plane + Skill Pipeline

**一句话目标**：把内置 skill/tool/bootstrap、A2A/work/subagent/graph-agent 委派链，以及 Skill Pipeline 做成可解释、可回放、可降级的增强层。

**实现状态**：已交付（2026-03-08）

- 已落地 bundled capability pack：bundled skills、bundled tools、worker bootstrap files、capability registry
- 已落地 ToolIndex：metadata filter、query top-N、fallback toolset、control-plane 可见 hit/selection
- 已落地 Skill Pipeline Engine：checkpoint、replay、pause/resume、node retry、HITL gate
- 已落地 Delegation Plane：Work create/assign/cancel/escalate、route reason、多 Worker 类型派发、单 Worker 降级
- 已落地 control-plane backend/frontend 增量资源：capability pack、delegation、pipelines

**借鉴来源**：

- OpenClaw skills / bootstrapping / subagents / ACP agents / `sessions_spawn`
- Agent Zero built-in tools / `call_subordinate` / A2A / project-scoped subagent config
- Blueprint 既有 ToolIndex / Skill Pipeline / 多 Worker 路线

**任务拆解**：

- F030-T01：定义 `AgentProfile` / `WorkerProfile` 与 bundled capability pack：
  - role / persona / instruction overlays
  - model route / tool profile / capability pack refs
  - policy refs / memory access hints / budget defaults
  - bundled skills / bundled tools / bundled worker bootstrap files（`AGENTS.md` / `BOOTSTRAP.md` / worker profile）
- F030-T02：实现 ToolIndex（向量检索 + metadata filter + 动态工具注入）
- F030-T03：实现 Skill Pipeline Engine（节点重试、checkpoint、暂停、人工介入）
- F030-T04：定义 `Work` 作为主 Agent 的委派/合并单位，支持 create / assign / merge / cancel / timeout / escalation，并保留来源 session/automation 与 selected agent profile 的 effective config snapshot
- F030-T05：实现主 Agent → Worker / Subagent / ACP-like runtime / Graph Agent 的统一委派协议，委派 envelope 必须携带 `agent_profile_id`、route reason 与 ownership
- F030-T06：实现多 Worker 类型（ops/research/dev）的 capability registry 与派发策略
- F030-T07：在管理台展示 tool hit、pipeline graph、worker routing reason、work ownership、subagent/runtime status
- F030-T08：保证失败时可回退到单 Worker / 静态工具集路径

**验收标准**：

- ToolIndex top-5 命中率 > 80%
- Skill Pipeline 支持 checkpoint / replay / HITL interrupt
- 主 Agent 能创建/管理/合并 Work，并把 Work 派发给 Worker / Subagent / ACP-like runtime / Graph Agent
- 多 Worker 派发具备可解释的 route reason，失败可降级
- session / automation 可以显式绑定 `AgentProfile`，并在委派事件链与管理台中看到继承后的 effective config
- bundled capability pack 能在新 agent / worker 首次启动时注入必要的基础能力与 bootstrap 上下文

---

### Feature 031：M3 User-Ready E2E Acceptance

**一句话目标**：用真实用户路径证明 M3 不是“功能上线”，而是“产品可用”。

**实现状态**：已完成

**任务拆解**：

- F031-T01：定义 fresh machine install → onboard → first chat → dashboard → update → restore 的完整验收矩阵
- F031-T02：定义普通用户路径与高级用户路径两套验收脚本
- F031-T03：定义 secret storage / rotate / reload / audit 的安全验收
- F031-T04：定义 Memory + MemU + Vault + import 的验收样本
- F031-T05：定义 Web 控制台移动端与桌面端验收用例
- F031-T06：定义 project / agent profile / automation 继承链验收：
  - 跨 project 切换不会串用 secrets、memory、agent profile
  - automation 触发的 work 会保留 project / agent profile / budget / target 继承来源
- F031-T07：定义 control-plane / ops 的信任边界验收：
  - 当前默认部署是否为 localhost / trusted network
  - 如果走反向代理 / VPN / Tailscale，文档与运行方式是否明确说明保护前提
  - 不允许把当前未内建 front-door auth 的入口误写成“可直接公网暴露”
- F031-T08：定义 update / backup / restore drill 的发布门禁：
  - backup-before-update
  - preflight / migrate / restart / verify
  - failure report / rollback suggestion / restore dry-run
- F031-T09：定义 OpenClaw → OctoAgent 迁移演练：
  - project / workspace / secret 处理
  - import mapping / memory evidence / vault review
  - dashboard / automation / control plane 验证
  - rollback 与 deferred items 记录
- F031-T10：产出 M3 release report，明确 pass / blocked / deferred / remaining risks

**收口结果**：

- acceptance matrix：已落地到 `.specify/features/031-m3-user-ready-acceptance/contracts/m3-acceptance-matrix.md`
- migration rehearsal：已落地到 `.specify/features/031-m3-user-ready-acceptance/verification/openclaw-migration-rehearsal.md`
- release report：已落地到 `.specify/features/031-m3-user-ready-acceptance/verification/verification-report.md`
- 关键接缝修补：control plane `project.select` 与 delegation selector 对齐；WeFlow `.jsonl` 导入已纳入 OpenClaw rehearsal 主路径

**验收标准**：

- 新用户无需深度理解底层拓扑，也能在合理时间内完成首次可用
- 运维用户可以在管理台完成配置、审批、恢复、升级和 Memory 检查
- 用户可以验证 project、agent profile、automation、work 之间的 effective config 继承关系，且跨 project 不串状态
- 高级能力可感知，但不会破坏系统的安全、可审计与可恢复性
- 部署边界清楚：当前产品若仍是单 owner / trusted-network 模式，报告与文档必须明确写实
- 至少完成一次 OpenClaw 迁移演练，并留下人工步骤、风险与 rollback 证据

---

### Feature 033：Agent Profile + Bootstrap + Context Continuity

**一句话目标**：把 `AgentProfile`、owner basics、bootstrap、recent session summary 和 long-term memory retrieval 真正接进主 Agent 的运行链，让 OctoAgent 从“有 Memory 的系统”变成“有连续上下文的长期助手”。

**实现状态**：规划中（2026-03-09 新增）

**为什么不是 M4**：

- 这是当前主聊天真正可用性的主链缺口，不是体验增强
- 如果 033 不完成，M4 的语音/工作台/companion 只会建立在 stateless 主会话之上

**借鉴来源**：

- OpenClaw `docs/start/bootstrapping.md`、`docs/reference/templates/BOOTSTRAP.md`、`docs/reference/templates/AGENTS.md`
- Agent Zero `docs/guides/projects.md`、`python/api/memory_dashboard.py`、`python/helpers/memory.py`
- 当前 OctoAgent 的 025/027/030/031 既有基线

**任务拆解**：

- F033-T01：定义 `AgentProfile`、`OwnerProfile`、`BootstrapSession`、`SessionContextState`、`ContextFrame`
- F033-T02：实现主 Agent 的统一 context assembly：
  - project/workspace bindings
  - owner/assistant basics
  - bootstrap-derived guidance
  - recent summary / recent artifacts
  - memory retrieval hits / evidence refs
- F033-T03：把 context assembly 真正接入 `TaskService -> LLMService`，禁止继续只传 `user_text`
- F033-T04：把 `agent_profile_id` / `context_frame_id` 接入 session / automation / work / pipeline / worker runtime 继承链
- F033-T05：把 profile / bootstrap / context provenance 接入 control plane
- F033-T06：补齐 failing integration tests、恢复测试与真实 e2e，证明不是假实现

**验收标准**：

- 首聊后能形成最小 owner/assistant bootstrap，并在下一轮聊天中生效
- recent session continuity 在重启后仍成立，不依赖进程内 history
- main agent 的实际运行链能消费 profile/bootstrap/recent summary/memory hits，而不是仅当前一句话
- 跨 project 切换不串用 agent profile、recent summary 或 memory retrieval
- 控制台能解释某次回答用到了哪些 context sources 与 degraded reason

---

## 5. 推荐技术选型（M3）

### 5.1 配置与管理台

- **前端保留 React + Vite**
  - 理由：当前 repo 已有 React 基线；M3 重点是产品化而不是换框架
- **UI primitives：Tailwind CSS + shadcn/ui + Radix UI**
  - 理由：减少控件层重复建设，尤其适合 admin / dashboard / wizard / forms
- **远程状态：TanStack Query**
  - 理由：适合 gateway 状态、轮询、缓存失效与 optimistic update
- **表单：React Hook Form + JSON Schema Form**
  - 理由：关键向导手写，宽覆盖配置表单走 schema 驱动

### 5.2 命令与控制面

- Telegram / Web 共用同一 `command registry` / `action registry`
  - 理由：避免双套语义和权限模型
- Telegram 原生命令优先覆盖：
  - `approve`
  - `model`
  - `skill`
  - `subagent`
  - `status`
  - 理由：这是用户最常用、最能直接感知的控制动作
- 所有命令都必须映射到事件链，而不是单纯的 adapter 内分支逻辑

### 5.3 Secret Store

推荐默认分层：

1. `~/.octoagent/auth-profiles.json`
   - 全局 provider auth profile 元数据
2. `~/.octoagent/secrets/` 或 OS keychain
   - 实际 secret 值，按 provider / channel / gateway 分类
3. `~/.octoagent/projects/<project-id>/bindings.json`
   - project 到 secret ref 的绑定关系
4. runtime injection
   - 启动时短期注入到进程内，不要求用户长期手管 env

原则：

- 用户只需要知道“这个 secret 属于哪个项目/通道/provider”
- 系统负责把 secret 解析并注入到 LiteLLM、Gateway、Channel runtime

### 5.4 Memory + MemU

推荐把 MemU 放在“推荐 backend”位置，而不是“外挂插件角落”：

- 核心治理：SQLite + Event Store + SoR / Fragments / Vault / WriteProposal
- 高级引擎：MemU（检索 / 增量同步 / 多模态 / Category / ToM）
- UI 视图：Memory Console / Evidence View / Vault Authorization

这样既保留架构边界，也能让高级能力真正进入主产品心智。

### 5.5 Delegation Plane

推荐的委派层次：

1. `Main Agent`
   - 面向用户会话与 Work 管理
2. `Worker`
   - 面向角色能力（ops / research / dev）
3. `Subagent`
   - 面向隔离的短中期任务
4. `ACP-like Runtime`
   - 面向外部 coding harness / 长任务会话
5. `Graph Agent`
   - 面向确定性 pipeline / DAG / FSM 子流程

原则：

- 所有委派都显式归属于某个 `Work`
- 所有 `Work` 都有 owner、state、budget、artifacts、children
- 主 Agent 负责合并结果，不允许子代理直接绕过主线修改最终状态

---

## 6. 本轮结论

截至 2026-03-09，M3 的主功能建设与 release harness 已完成 024-031，但 live-usage 复核新增了一个必须优先处理的补位结论：

1. 031 已证明 install / project / control plane / memory / import / delegation / migration drill 可以联合成立。
2. 但 031 没有真正证明“主 Agent 拥有连续上下文”，因为当前运行链尚未正式消费 `AgentProfile`、owner basics、bootstrap 与 memory retrieval。
3. 因此，当前最重要的不是直接转入 M4，而是先完成 Feature 033。

只有当 033 把主 Agent 的 context continuity 主链补齐后，OctoAgent 才能从“能力齐全的系统”真正推进到“日常可长期使用的助手”。

---

## 7. M4 Backlog（低优先级体验增强）

这些能力重要，但不应抢占 M3 的 user-ready 主闭环资源：

- 文件/工作区工作台：file browser / editor / diff / git-aware workspace inspector
- 语音与多模态交互表面：STT / TTS / voice session / richer multimodal chat surfaces
- Progressive Web App / companion surfaces / remote tunnel polish
- 更完整的通知中心与 attention model

M4 原则：

- 只在 M3 的 project / session / automation / runtime console 成型后推进
- 以“增强体验”优先，不以重建核心产品对象为目标
