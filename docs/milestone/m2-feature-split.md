# M2 Feature 拆分方案（v4）

> **文档类型**: 里程碑拆分方案（Implementation Planning）  
> **依据**: `docs/blueprint.md` §5.1 + §12.4 + §12.9 + §14（M2 定义）+ `docs/m1.5-feature-split.md` 收口结论  
> **状态**: v4 — Feature 015/016/017/018/019/020/021/022 已交付，Feature 023 待启动
> **日期**: 2026-03-07
> **变更记录**: v1(2026-03-06，M2 初版拆分) → v2(2026-03-07，按当前 `master` 交付事实回写已完成与待启动状态) → v3(2026-03-07，补回写 Feature 017 已交付，收敛剩余项为 021/023) → v4(2026-03-07，回写 Feature 021 已交付，补齐 CLI / dry-run / ImportReport)

---

## 1. 背景与目标

### 1.1 当前基线

基于当前 `master`（2026-03-07）：

- M0 / M1 / M1.5 核心能力已交付，系统已经具备最小 Agent 闭环。
- M1.5 已解决“能不能稳定跑起来”的问题；M2 要解决“你是否愿意每天真的用它”的问题。
- M2 第一波 contract / transport / recovery / DX / operator control 能力已完成：015、016、017、018、019、020、022。
- 当前剩余待启动 Feature 只剩 023（M2 集成验收）。

### 1.1.1 M2 实际交付状态

| Feature | 状态 | 日期 | 关键产出 |
|---|---|---:|---|
| 015 | 已交付 | 2026-03-07 | `octo onboard` + doctor remediation + channel verifier |
| 016 | 已交付 | 2026-03-07 | Telegram transport + pairing + session routing |
| 017 | 已交付 | 2026-03-07 | Unified operator inbox + mobile controls |
| 018 | 已交付 | 2026-03-07 | A2A-Lite envelope + state mapper |
| 019 | 已交付 | 2026-03-07 | Interactive execution console + durable input resume |
| 020 | 已交付 | 2026-03-07 | Memory core + proposal/commit contract |
| 021 | 已交付 | 2026-03-07 | `octo import chats` + dry-run + ImportReport + governed chat import |
| 022 | 已交付 | 2026-03-07 | Backup/restore/export + recovery drill |
| 023 | 待启动 | - | M2 E2E integration acceptance |

### 1.2 竞品复核后的三类缺口

本轮从用户视角复核 OpenClaw 与 Agent Zero，得出的核心结论如下：

| 竞品事实 | 对用户的价值 | OctoAgent 当前缺口 | M2 调整 |
|---|---|---|---|
| OpenClaw 把 `onboard` / wizard / doctor / dashboard 串成一条连续上手路径 | 新用户不用猜“下一步该去哪” | 我们有 `octo config` 与 `octo doctor`，但还没有跨 provider/channel/runtime 的可恢复 onboarding | 新增 Feature 015 |
| OpenClaw 有多渠道 inbox、pairing、安全默认值、pending 队列与渠道内审批 | 操作者可以直接在常用渠道处理待办 | 我们只有能力层设计，缺少 Web/Telegram 等价的 operator inbox | 新增 Feature 016 + 017 |
| Agent Zero 提供交互式终端、远程访问、聊天 load/save、backup/restore | 长任务出问题时用户能立即干预，并能迁移/恢复实例 | 我们底层有 backup 策略，但用户侧没有 backup/export/restore dry-run 与交互式执行入口 | 新增 Feature 019 + 022 |

### 1.3 M2 目标

M2 的目标不只是“把 Telegram/A2A/Memory 实现出来”，而是把 OctoAgent 推到一个**可日常使用**的状态：

- 新用户可以从零完成安装、配置、doctor、自检、pairing 和首条消息验证；
- 操作者可以在 Web 或 Telegram 中统一处理 approvals、watchdog alerts、retry、cancel；
- JobRunner、Memory、Chat Import、Backup/Restore 都有用户可触达的稳定入口；
- 集成阶段只验收，不再新增业务能力。

---

## 2. M2 需求提取（来自 Blueprint + 本轮回写）

| 需求来源 | 级别 | 要点 |
|---|---|---|
| FR-CH-2 | 必须 [M2] | TelegramChannel（pairing / allowlist / thread 映射） |
| FR-CH-3 | 应该 [M2] | Chat Import Core（dedupe / window / summarize） |
| FR-CH-5 | 应该 [M2] | 统一操作收件箱与移动端等价控制 |
| FR-A2A-3 | 应该 [M2] | A2A-Lite + A2AStateMapper |
| FR-EXEC-1 | 必须 | JobRunner 抽象（docker / ssh / remote） |
| FR-EXEC-4 | 应该 [M2] | 长任务交互式控制 |
| FR-MEM-1 / FR-MEM-2 / FR-MEM-3 | 必须 / 应该 | SoR / Fragments / WriteProposal / Vault skeleton |
| FR-OPS-3 | 应该 [M2] | onboarding + doctor guided remediation |
| FR-OPS-4 | 应该 [M2] | backup / restore / export 自助化 |
| §12.4 备份与恢复 | 必须映射 | 现有底层备份策略要产品化为用户入口 |
| §12.9 DX | 必须映射 | `octo config` / `octo doctor` / `octo onboard` 形成闭环 |

---

## 3. 并行拆分方案（M2 = 9 个 Feature）

### 3.1 依赖图

```text
M1.5 基线（已完成）
   │
   ├── Track A: 上手体验与渠道
   │   ├── Feature 015 已交付: Octo Onboard + Doctor Guided Remediation
   │   ├── Feature 016 已交付: Telegram Channel + Pairing + Session Routing
   │   └── Feature 017 已交付: Unified Operator Inbox + Mobile Task Controls
   │
   ├── Track B: 控制平面与执行面
   │   ├── Feature 018 已交付: A2A-Lite Envelope + StateMapper
   │   └── Feature 019 已交付: JobRunner Docker Backend + Interactive Console
   │
   ├── Track C: Memory 与导入
   │   ├── Feature 020 已交付: Memory Core + WriteProposal + Vault Skeleton
   │   └── Feature 021 已交付: Chat Import Core
   │
   ├── Track D: 可迁移与恢复
   │   └── Feature 022 已交付: Backup/Restore + Export + Recovery Drill
   │
   └── 全部汇合
       └── Feature 023 待启动: M2 E2E 集成验收
```

### 3.2 并行化原则

1. **先冻结 contract，再并行编码**：016、017、018、020 的 contract 已冻结并落地；021 已完成接线，023 只消费这些 contract，不再重定义 schema。
2. **体验层只消费 contract，不重定义 schema**：015/017/022 不得单独发明 task / approval / message / backup 的新主数据模型。
3. **导入与 Memory 解耦**：021 已按 `WriteProposal -> validate -> commit` 接入真实 Memory 仲裁，后续 adapter 只消费 frozen import contract。
4. **集成 Feature 不引入新能力**：023 仅联调 015-022 的真实依赖与验收，不接“顺手加一个功能”。

### 3.3 调研复核后的四条必改约束（Must）

1. **首次使用路径必须闭环**：`octo config` → `octo doctor --live` → channel pairing → 首条消息验证必须是单一连续流程，并支持中断恢复。
2. **操作者控制必须渠道等价**：approve / retry / cancel / pending queue / alert acknowledge 在 Web 与 Telegram 必须落同一事件链，不允许双套语义。
3. **长任务交互必须可审计**：日志流、人工输入、取消、重试、产物查看都要事件化，不能只存在于临时终端窗口。
4. **恢复与迁移必须自助化**：backup / export / restore dry-run 必须有 CLI 或 Web 入口，不允许只有底层脚本和 runbook。

---

## 4. Feature 详细拆解

### Feature 015：Octo Onboard + Doctor Guided Remediation

**一句话目标**：把 `octo config`、`octo doctor --live`、渠道接入和首条消息验证串成可恢复的一次性 onboarding 流程。

**覆盖需求**：
- FR-OPS-3
- §12.9 DX
- M2 执行约束 1

**参考实现证据**：
- `/_references/opensource/openclaw/README.md`（`openclaw onboard` + wizard + doctor）
- `/_references/opensource/agent-zero/knowledge/main/about/installation.md`（Settings 驱动的首次配置与远程入口）

**任务拆解**：
- F015-T01：定义 `OnboardingSession` / `OnboardingCheckpoint` 数据模型（当前步骤、阻塞项、修复建议、更新时间）。
- F015-T02：实现 `octo onboard` CLI，串联 provider 配置、doctor、channel 选择、首条消息验证。
- F015-T03：实现中断恢复能力（resume 上次步骤，而不是重头开始）。
- F015-T04：把 `octo doctor` 输出升级为 action-oriented remediation（明确下一条命令或 UI 动作）。
- F015-T05：增加 onboarding E2E 测试（中断 / 修复 / 恢复 / 成功完成）。

**验收标准**：
- 新用户可在一次向导内完成 provider 配置、doctor、自检、可选 Telegram pairing 和首条消息验证；
- onboarding 中断后可从最近完成步骤继续；
- 任一步失败时能给出下一条可执行修复动作，而不是仅输出原始异常。

---

### Feature 016：Telegram Channel + Pairing + Session Routing

**一句话目标**：打通 Telegram 作为首个真实外部渠道，并稳定落实 pairing、allowlist、thread/session 路由。

**覆盖需求**：
- FR-CH-2
- FR-CH-5（渠道等价操作的一部分）

**参考实现证据**：
- `/_references/opensource/openclaw/README.md`（DM pairing 默认安全策略）
- `/_references/opensource/openclaw/docs/channels/telegram.md`（多渠道 routing 思路）

**任务拆解**：
- F016-T01：定义 Telegram ingress/egress adapter 与 `NormalizedMessage` 映射规则。
- F016-T02：实现 pairing / allowlist / webhook / polling fallback。
- F016-T03：实现 DM / 群 / reply thread 的 `scope_id` / `thread_id` 映射。
- F016-T04：实现 Telegram 出站回复、审批卡片、错误提示与重试语义。
- F016-T05：增加集成测试（pairing、重复消息去重、群聊映射、回传链路）。

**验收标准**：
- Telegram 消息可稳定进入 `NormalizedMessage -> Task` 链路；
- pairing / allowlist 默认启用，未授权消息不会静默执行；
- DM、群聊、reply thread 的路由规则稳定且可回放。

---

### Feature 017：Unified Operator Inbox + Mobile Task Controls

**一句话目标**：把 approvals、watchdog alerts、retry/cancel、pending 队列整合成统一的 operator inbox，并提供 Web/Telegram 等价操作入口。

**覆盖需求**：
- FR-CH-5
- FR-EXEC-4（控制入口）
- §13 审批与用户控制原则

**参考实现证据**：
- `/_references/opensource/openclaw/README.md`（multi-channel inbox / control UI）
- `/_references/opensource/agent-zero/knowledge/main/about/github_readme.md`（实时交互、用户可随时 intervene）

**任务拆解**：
- F017-T01：定义 `OperatorInboxItem` 数据模型（approval / alert / retryable failure / pairing request）。
- F017-T02：实现 Web Inbox 视图（pending 数量、过期时间、最近动作结果、快速操作）。
- F017-T03：实现 Telegram inline keyboard 等价操作（approve / deny / retry / cancel / ack）。
- F017-T04：接入 Task Journal / Watchdog / Approval 事件聚合，避免用户切多个页面找状态。
- F017-T05：补齐 operator action 的审计事件与回放测试。

**验收标准**：
- 操作者可在 Web 或 Telegram 中完成 approve / retry / cancel / alert acknowledge；
- pending 数量、过期时间与最近动作结果可见；
- 所有动作写入同一任务/审批事件链，可回放、可追溯。

---

### Feature 018：A2A-Lite Envelope + A2AStateMapper

**一句话目标**：冻结 OctoAgent 内部 Agent 通信协议，为后续多 Worker / 外部 SubAgent 扩展清出稳定 contract。

**覆盖需求**：
- FR-A2A-3
- §10.2 A2A-Lite Envelope

**参考实现证据**：
- `/_references/opensource/pydantic-ai/docs/multi-agent-applications.md`
- `docs/blueprint.md` §10.2 / §10.2.1 / §10.2.2

**任务拆解**：
- F018-T01：定义 `TASK/UPDATE/CANCEL/RESULT/ERROR/HEARTBEAT` envelope、版本字段、扩展位。
- F018-T02：实现 `A2AStateMapper`（内部状态 <-> A2A TaskState 双向映射）。
- F018-T03：实现 artifact 映射层（OctoAgent Artifact -> A2A Artifact）。
- F018-T04：实现幂等键、重放保护、跳数保护与版本兼容测试。
- F018-T05：形成对 019/023 可直接消费的 contract fixture。

**验收标准**：
- A2A-Lite 消息在 Orchestrator ↔ Worker 间可可靠投递；
- 状态与 artifact 映射幂等；
- 扩展字段不会破坏标准 A2A 兼容行为。

---

### Feature 019：JobRunner Docker Backend + Interactive Console

**一句话目标**：把 JobRunner 从“后台执行器”做成“可观察、可交互、可中断”的执行面。

**覆盖需求**：
- FR-EXEC-1
- FR-EXEC-2
- FR-EXEC-4

**参考实现证据**：
- `/_references/opensource/agent-zero/knowledge/main/about/github_readme.md`（实时交互终端）
- `/_references/opensource/agent-zero/knowledge/main/about/installation.md`（Docker-first 用户路径）

**任务拆解**：
- F019-T01：实现 JobRunner docker backend（start / status / stream_logs / cancel / collect_artifacts / attach_input）。
- F019-T02：定义 `ExecutionConsoleSession` / `ExecutionStreamEvent` 模型。
- F019-T03：实现 stdout/stderr 流、最后产物、当前步骤、交互输入的统一事件协议。
- F019-T04：接入 Policy Gate，对需要人工输入的长任务维持最小权限和审计记录。
- F019-T05：增加长任务/交互输入/取消/产物回收集成测试。

**验收标准**：
- 长任务可在 Docker 中执行并流式返回日志与产物；
- 用户可发送明确的人类输入或取消信号；
- 交互与取消全部可审计、可回放。

---

### Feature 020：Memory Core + WriteProposal + Vault Skeleton

**一句话目标**：落地 M2 最小记忆治理内核，保证写入经过仲裁、SoR 唯一 current、敏感分区默认不可检索。

**覆盖需求**：
- FR-MEM-1
- FR-MEM-2
- FR-MEM-3

**参考实现证据**：
- `docs/blueprint.md` §8.7 Memory
- `/_references/opensource/agent-zero/knowledge/main/about/github_readme.md`（项目隔离与记忆价值）

**任务拆解**：
- F020-T01：定义 Fragments / SoR / WriteProposal / Vault skeleton 数据模型。
- F020-T02：实现写入仲裁器（合法性校验、冲突检测、证据引用、commit）。
- F020-T03：实现 SoR 版本化与唯一 current 约束。
- F020-T04：实现基础检索接口与敏感分区默认拒绝策略。
- F020-T05：增加 unit/integration tests（同一 `subject_key` 唯一 current、冲突写入、Vault 默认拒绝）。

**冻结接口（2026-03-07）**：
- `propose_write()`
- `validate_proposal()`
- `commit_memory()`
- `search_memory()`
- `get_memory()`
- `before_compaction_flush()`
- `MemoryBackend`
- `MemUBackend`（adapter 位）

**M2 插件化落点（2026-03-07）**：
- `packages/memory` 负责 governance plane：proposal / arbitration / SoR current / Vault policy
- `MemUBackend` 负责 memory engine plane：检索、索引、增量同步、后续 chat import / knowledge update 扩展
- backend 失效时自动降级回本地 SQLite metadata search，不阻塞任务系统

**明确非目标（避免与 021 / Context Manager / M3 混淆）**：
- 不实现 Chat Import Core
- 不实现工作上下文 GC / auto-compaction 引擎
- 不实现 Vault 授权检索与浏览 UI

**验收标准**：
- 写入必须经 `WriteProposal -> validate -> commit`；
- SoR 同 `subject_key` 永远只有一条 `current`；
- Vault 分区默认不可检索。

---

### Feature 021：Chat Import Core

**一句话目标**：把外部聊天导入做成可直接使用的通用内核，提供 `octo import chats`、`--dry-run`、`ImportReport`，并按 chat scope 受治理写入记忆。

**覆盖需求**：
- FR-CH-3
- FR-MEM-1 / FR-MEM-2（与 020 对接）

**参考实现证据**：
- `docs/blueprint.md` §8.7.5 Chat Import Core
- OpenClaw 多渠道会话模型（会话与 channel 解耦）

**任务拆解**：
- F021-T00：提供 `octo import chats` CLI 入口与 `normalized-jsonl` contract。
- F021-T01：定义 ImportBatch / ImportCursor / ImportWindow / ImportSummary 模型。
- F021-T02：实现增量去重（message id / hash / source cursor）。
- F021-T03：实现窗口化摘要与 artifact 引用。
- F021-T04：把导入内容映射到 chat scope，并通过 020 的仲裁器写入 SoR / Fragments。
- F021-T05：增加 dry-run / 重复执行 / resume / 摘要窗口边界测试。
- F021-T06：持久化 `ImportReport`，向用户展示 counts / cursor / warnings / errors。

**验收标准**：
- 外部聊天可增量导入且不会重复写入；
- 用户可先用 `--dry-run` 预览，不产生副作用；
- 窗口化摘要正确，不把长原文直接塞进主上下文；
- 导入写入不污染不相关 chat scope；
- 每次真实导入都生成可回看的 `ImportReport`。

---

### Feature 022：Backup/Restore + Export + Recovery Drill

**一句话目标**：把现有底层备份策略提升为用户可触达的 backup / export / restore dry-run 能力。

**覆盖需求**：
- FR-OPS-4
- §12.4 备份与恢复

**参考实现证据**：
- `/_references/opensource/agent-zero/knowledge/main/about/installation.md`（Backup & Restore / Tunnel）
- `/_references/opensource/agent-zero/knowledge/main/about/github_readme.md`（load/save chats）

**任务拆解**：
- F022-T01：定义 BackupBundle / RestorePlan / ExportManifest 数据模型。
- F022-T02：实现 CLI 入口：`octo backup create` / `octo restore dry-run` / `octo export chats`。
- F022-T03：实现 Web 侧最小入口（查看最近备份、恢复验证时间、导出入口）。
- F022-T04：实现 restore dry-run 冲突检查（路径、schema version、缺失文件、覆盖提示）。
- F022-T05：固化恢复演练记录（最近成功验证时间、失败原因、修复建议）。

**验收标准**：
- 用户可以创建 backup bundle 并查看最近一次恢复验证结果；
- restore 至少支持 dry-run，不会盲目覆盖现有实例；
- 会话导出与恢复入口可被普通操作者使用，而不依赖手工 shell 操作。

---

### Feature 023：M2 集成验收（串行）

**一句话目标**：汇合 015-022，完成“可日常使用”的 M2 验收，不扩展功能范围。

**覆盖需求**：
- M2 全部验收条目
- FR-CH-2 / FR-CH-3 / FR-CH-5 / FR-A2A-3 / FR-EXEC-1 / FR-MEM-1/2/3 / FR-OPS-3/4 联合验收

**任务拆解**：
- F023-T01：替换并行阶段的 mock，接入真实依赖。
- F023-T02：新增 E2E 场景：
  - onboarding -> doctor -> Telegram pairing -> 首条消息；
  - Web/Telegram 双端 approvals / alerts / retry / cancel；
  - A2A 消息投递 + JobRunner 交互执行；
  - Memory 写入仲裁 + Chat Import；
  - backup/export/restore dry-run。
- F023-T03：执行 M0/M1/M1.5 回归。
- F023-T04：产出 M2 verification report 与遗留风险清单。

**验收标准**：
- M2 验收条目全部通过；
- 不回归 M0/M1/M1.5 已交付能力；
- 验收报告可作为 M3 准入基线。

---

## 5. Design Gates

- **GATE-M2-ONBOARD**
  - 若 015 无法完成“config -> doctor -> channel -> first message”闭环，016/017/023 不得进入签字阶段。

- **GATE-M2-CHANNEL-PARITY**
  - 若 017 在 Web 与 Telegram 上的操作语义不一致，023 不得通过；必须统一到同一事件 contract。

- **GATE-M2-A2A-CONTRACT**
  - 若 018 缺失版本化、重放保护、状态映射幂等，019/023 不得进入联调。

- **GATE-M2-MEMORY-GOVERNANCE**
  - 若 020 未保证 `WriteProposal -> validate -> commit`，021/023 不得接真实记忆写入。

- **GATE-M2-RESTORE**
  - 若 022 没有 `restore dry-run` 与最近恢复验证记录，023 不得宣称“具备可恢复能力”。

---

## 6. 推荐启动顺序

最大化并发的启动建议：

1. **已完成批次**：015 / 016 / 017 / 018 / 019 / 020 / 021 / 022
2. **当前主线**：023（M2 集成验收）
3. **最后串行**：023 集成验收

当前建议至少分成 2 条收口轨：

- A 轨：023（M2 E2E Integration Acceptance）
- B 轨：023 前置验收脚本 / fixture / gate 清单准备
