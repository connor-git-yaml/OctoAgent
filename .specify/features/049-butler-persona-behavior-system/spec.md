---
feature_id: "049"
title: "Butler Behavior Workspace & Agentic Decision Runtime"
milestone: "M4"
status: "Implemented"
created: "2026-03-14"
updated: "2026-03-14"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §2 Constitution；docs/blueprint.md M3/M4 carry-forward；Feature 039（message-native A2A 主链）；Feature 041（Butler-owned freshness runtime）；OpenClaw workspace file map；Agent Zero prompt composition"
predecessor: "Feature 039、041、044"
parallel_dependency: "Feature 048 负责普通用户主路径表达；049 负责把默认行为从代码特判迁移到显式上下文文件与 agent 决策。"
---

# Feature Specification: Butler Behavior Workspace & Agentic Decision Runtime

**Feature Branch**: `codex/049-butler-persona-behavior-system`  
**Created**: 2026-03-14  
**Updated**: 2026-03-14  
**Status**: Implemented
**Input**: 将当前写死在代码里的天气/推荐/排期补问策略，重构为“少量显式 markdown 文件 + runtime hints + Agent 自主决策”的默认行为系统。整体方向参考 OpenClaw 的 workspace context files 和 Agent Zero 的分层 prompt 装配，但保留 OctoAgent 现有的 durability、governance 与 A2A 审计链。

## Problem Statement

当前 Butler runtime 已经具备 durable session、A2A delegation、memory/recall、governed tools 等底座，但默认行为层仍然偏“代码驱动”而不是“上下文驱动”：

1. **行为主路径仍写死在代码里**
   当前默认行为不是从真实 workspace/context files 读取，而是由运行时代码拼装默认模板、再叠加一组硬编码判断。结果是产品号称有 behavior pack，实际仍是“代码定义行为，文件只是包装”。

2. **补问逻辑扩张成 brittle case tree**
   天气、推荐、排期、比较等问题的判断靠 token/heuristic 和特判分支实现。这类实现会持续膨胀，而且已经出现误判，例如“你直接去 Websearch 今天天气怎么样”这类表达会被错误地识别为已提供地点。

3. **行为文件过多、边界不够克制**
   当前思路趋向 OpenClaw 七文件全集，但 OctoAgent 当前阶段并不需要一次暴露那么多默认文件。文件太多会提高理解和维护成本，也不利于 UI/CLI 做清晰入口。

4. **用户和 Agent 不能把行为系统当作正式产品面来修改**
   现在即使有 behavior pack 的概念，也缺少一套面向 Web/CLI 的“查看 effective source、编辑、生成 patch、review/apply”的完整路径。

5. **Worker 继承语义仍不够显式**
   子 Worker 该看到哪些行为文件、哪些只该被转成 hints/capsule，目前更多依赖实现细节，而不是一个清晰、用户和系统都能解释的 contract。

因此，049 的目标不是继续完善一个 clarification-first 特判器，而是：

> 把 Butler 的默认行为从“代码里写死的补问和场景判断”升级成“显式 behavior workspace + runtime hints + agent 自主决策”，并把可编辑面、治理边界和 Worker 继承规则做成正式产品能力。

## Product Goal

交付一条新的默认行为主链：

- Butler 的默认行为主要来自真实的 markdown behavior workspace，而不是代码里的特判树
- 运行时把 `行为文件 + 当前环境事实 + 会话确认事实 + 工具能力` 一起提供给 Butler，让它自己判断是直接回答、补问一次、委派 research/ops，还是给 best-effort fallback
- Butler 对有界任务可以直接使用已挂载的受治理工具，不再被硬性限制成“只能路由、不能亲自执行”
- 默认只暴露少量核心文件，降低复杂度并方便 UI/CLI 管理
- Worker 只继承共享行为文件与显式上下文胶囊，不读取 Butler 私有文件全集
- governance、tool entitlement、审批、memory 写入仲裁仍然保持代码级硬约束，不交给 Agent 自行决定

## Design Direction

### 049-A 核心原则

- 行为尽量显式化到文件，不继续堆积 case-by-case 代码判断
- 代码保留硬边界，Agent 决定软行为
- 默认文件数量克制，先做“够用且清晰”的核心集合
- 主 Butler 和子 Worker 的可见上下文明确分层
- UI/CLI 必须把这套行为系统作为正式产品面暴露出来

### 049-B Behavior Workspace

本 Feature 只把以下四个文件作为**核心默认文件**：

1. `AGENTS.md`
   - 作用：共享给 Butler 和 Worker
   - 内容：操作原则、delegation、clarification philosophy、安全边界、输出约定

2. `USER.md`
   - 作用：只给 Butler
   - 内容：用户偏好、默认城市/时区、节奏偏好、已确认的长期默认值

3. `PROJECT.md`
   - 作用：Butler 和 Worker 都可见
   - 内容：当前 project 目标、术语、交付标准、当前优先事项

4. `TOOLS.md`
   - 作用：共享给 Butler 和 Worker
   - 内容：本地工具约定、路径、provider 说明、web/browser/memory 的环境提示

以下文件保留为**可选高级扩展**，但不是默认主路径：

- `SOUL.md`
- `IDENTITY.md`
- `HEARTBEAT.md`

当前阶段不把 `MEMORY.md` 当作行为配置文件；它属于长期事实层，应通过 recall/selection 注入，而不是整份当作 persona 配置。

### 049-C Runtime Hint Bundle

运行时新增一组标准 hints，交给 Butler 做判断，而不是代码直接给结论：

- 当前时间、时区、surface、channel
- 当前 project / workspace / selected profile
- 最近会话中已确认的事实
- `USER.md` 中的默认值
- 当前可用工具与受限原因
- 最近一次 delegation / backend failure 的用户态解释
- 当前消息是否显式要求联网、搜索、打开网页、继续上一轮任务
- session-backed `RecentConversation` 与 rolling summary
- hint-first 的 memory runtime 线索；更深 recall 交由 Agent 主动调用 memory 工具

天气、推荐、排期等问题不再走“先被分类器打标签再决定”；而是让 Butler 基于这些 hints 输出结构化决策。

### 049-D Agentic Decision Runtime

Butler 每轮先产出一个结构化 `ButlerDecision`：

- `mode`: `direct_answer | ask_once | delegate_research | delegate_ops | best_effort_answer`
- `missing_inputs`
- `assumptions`
- `tool_intent`
- `target_worker_type`
- `user_visible_boundary_note`

系统负责：

- 校验该决策是否越过 governance
- 为允许的决策补上 durable lineage / A2A / work metadata
- 阻止危险或越权行为

系统**不再负责**：

- 用一组不断扩张的 if/else 直接判定“天气就一定先问城市”
- 用字符串 heuristic 直接给出场景结论

### 049-E UI / CLI

当前已落地的产品面：

- Web: `Settings -> Behavior Files` 的只读 operator 视图
  - 查看当前 project 的 effective source chain
  - 查看核心文件、可见性、来源与 Worker 继承范围
  - 查看当前 CLI 入口
- CLI:
  - `octo behavior ls`
  - `octo behavior show <file>`
  - `octo behavior init`

当前阶段明确选择：

- Web 先做只读 view，不在页面内另造一套文件真相
- 文件写入仍以本地文件系统与 CLI 为 canonical path
- patch proposal / review / apply 工作流保留为后续增强，不作为本轮 049 收口门槛

### 049-F 仍然保留在代码里的硬约束

以下内容仍由系统强约束，不交给 Agent 自改：

- approval / policy gate
- tool entitlement / allowlist
- memory 写入治理与 SoR 仲裁
- 最大补问轮数、loop guard、超时和重试上限
- durable Task / Work / A2A / Event 审计链

## Scope Alignment

### In Scope

- 核心 behavior workspace 的文件集合、可见性和加载规则
- runtime hint bundle 的 contract
- `ButlerDecision` 结构化决策 contract
- Butler / Worker 各自可见的行为文件与上下文胶囊规则
- Web/CLI 的 behavior file 管理入口设计
- agent proposal -> review/apply 的治理流程
- 用行为文件和 hints 替代现有 case-by-case 行为树的迁移策略

### Out of Scope

- 直接重写整个 Worker persona 系统
- 新建 persona marketplace
- 用 md 文件替代审批、权限、memory 仲裁等硬约束
- 把 `SOUL.md / IDENTITY.md / HEARTBEAT.md` 全部作为当前阶段的默认入口
- 在本 Feature 中顺手重做 Memory 数据模型或 Setup 系统

## User Scenarios & Testing

### User Story 1 - 用户能直接看到并修改 Butler 行为的核心文件 (Priority: P1)

作为用户，我希望 Butler 的默认行为和偏好是几份明确的文件，而不是散落在代码和神秘 prompt 里。

**Independent Test**: 在 Web 或 CLI 中查看当前 project 的 `AGENTS.md / USER.md / PROJECT.md / TOOLS.md`，修改后开启新会话，行为发生对应变化，并可追溯 effective source。

**Acceptance Scenarios**:

1. **Given** 当前 project 绑定了 `USER.md` 默认城市与回答偏好，**When** 用户查看 behavior 面板，**Then** 系统能展示文件内容、来源和最后更新时间。
2. **Given** 用户修改了 `AGENTS.md` 中的 delegation / clarification 表述，**When** 新会话启动，**Then** Butler 的默认行为按新文件表现，而不是继续只受旧代码模板支配。

---

### User Story 2 - Butler 基于上下文和 hints 自己决定，而不是依赖场景硬编码 (Priority: P1)

作为用户，我希望 Butler 面对天气、推荐、排期这类问题时，先综合已有上下文和工具能力判断，而不是每类问题都走一套写死的代码分支。

**Independent Test**: 用同一组问题分别在“有默认城市 / 有已确认城市 / 没有地点 / 明确要求 WebSearch”几种上下文下测试，验证 Butler 决策不同但都可解释。

**Acceptance Scenarios**:

1. **Given** 用户问“今天天气怎么样”，且会话中已确认城市或 `USER.md` 中存在默认城市，**When** web path 可用，**Then** Butler 可以直接委派 research 查询，并在答案中显式说明使用了哪个位置假设。
2. **Given** 用户问“你直接去 WebSearch 今天天气怎么样”，且没有任何可用地点线索，**When** Butler 决策时，**Then** 系统不得因为字符串 heuristic 把“你直接去”这类前缀误当地点；Butler 应在 `ask_once` 和 `best_effort_answer` 之间做显式可解释决策。
3. **Given** 用户问“帮我把今天下午的工作拆成 3 个优先级”，且系统没有真实待办列表，**When** Butler 决策时，**Then** 它会先补最关键的缺失信息或明确提供通用框架，而不是假装已经知道上下文。

---

### User Story 3 - Agent 能帮我修改行为系统，但不能悄悄生效 (Priority: P2)

作为用户，我希望 Agent 能基于真实使用情况提出行为文件修改建议，但默认不能静默重写这些文件。

**Independent Test**: Butler 生成一个 behavior patch proposal，用户在 Web/CLI 中 review 后 apply；未 apply 时，行为不应变化。

**Acceptance Scenarios**:

1. **Given** Butler 发现某类问题经常因为 `TOOLS.md` 缺路径说明导致失败，**When** 它生成 patch proposal，**Then** 提案以 diff 形式展示，并挂接 review/apply。
2. **Given** 用户拒绝 proposal，**When** 后续新会话启动，**Then** runtime 仍使用旧文件，不得隐式生效。

---

### User Story 4 - Worker 只继承共享文件和显式 capsule (Priority: P2)

作为系统设计者，我希望 Worker 只获得完成任务所需的共享规则和上下文，而不是 Butler 的全部私有偏好。

**Independent Test**: 触发 Butler -> Worker delegation，验证 Worker 只看到 `AGENTS.md / PROJECT.md / TOOLS.md` 与任务 capsule；`USER.md` 只以显式筛选后的 hints 出现。

**Acceptance Scenarios**:

1. **Given** Butler 创建一个 research worker，**When** WorkerSession 启动，**Then** Worker 不直接读取完整 `USER.md`，只接收允许转交的 user defaults / confirmed facts。
2. **Given** 用户在 `USER.md` 中记录了隐私性偏好，**When** 发生 delegation，**Then** 这些内容不会默认整份泄露到 Worker 上下文。

## Edge Cases

- 简单常识问题不能因为引入 `ButlerDecision` 而普遍变慢
- `USER.md` 中的默认城市只能作为 assumption，不得伪装成实时确认事实
- 工具不可用时，Butler 必须把限制解释成“当前 runtime/tooling 限制”，而不是“系统没有这类能力”
- behavior files 缺失或损坏时，必须回退到默认模板并记录来源链
- 如果用户明确要求“不要问我，直接给 best-effort”，Butler 可以减少补问，但仍需标明假设

## Functional Requirements

- **FR-001**: 系统 MUST 把 behavior workspace 作为默认行为的主事实源，不得继续把代码内置模板作为长期 canonical source。
- **FR-002**: 当前阶段 MUST 至少支持四个核心默认文件：`AGENTS.md`、`USER.md`、`PROJECT.md`、`TOOLS.md`。
- **FR-003**: `SOUL.md`、`IDENTITY.md`、`HEARTBEAT.md` MAY 作为高级扩展支持，但 MUST NOT 成为当前阶段默认必需文件。
- **FR-004**: 主 Butler runtime MUST 同时消费 behavior files 与 runtime hint bundle，而不是只基于当前用户一句话或场景分类器做决策。
- **FR-004A**: 主 Butler runtime MUST 使用 session-backed `RecentConversation` / rolling summary，而不是只从当前 task events 拼接跟进上下文。
- **FR-005**: 系统 MUST 引入结构化 `ButlerDecision` contract，至少包含 `mode`、`missing_inputs`、`assumptions`、`tool_intent`、`target_worker_type`、`user_visible_boundary_note`。
- **FR-005A**: Butler 对有界任务 MAY 直接使用已挂载的受治理工具；系统不得再以硬编码 prompt/role 限制把 Butler 永久锁成“只能委派”的 supervisor shell。
- **FR-006**: 代码 MUST 保留 deterministic governance，包括审批、tool entitlement、memory 写入仲裁、loop guard 和 durable lineage；这些约束不得下放给 md 文件或模型自由决定。
- **FR-007**: Worker runtime MUST 默认只消费共享文件与显式 capsule；`USER.md` 之类主会话私有文件不得整份透传。
- **FR-008**: 系统 MUST 提供 Web/CLI 的 behavior file 管理入口。当前阶段至少包含 Web 只读 effective view 与 CLI `ls/show/init` 主路径；更完整的 edit/diff/review/apply 可作为后续增强。
- **FR-009**: 系统 MUST 能展示每次 Butler/Worker 运行实际使用的 effective behavior source 与 runtime hints provenance。
- **FR-010**: 现有天气/推荐/排期/比较等 case-by-case 行为判断 MUST 被逐步迁移出主决策路径，不得继续扩张为新的硬编码分类树。
- **FR-011**: 049 的 acceptance matrix MUST 覆盖“已确认默认值”“会话确认事实”“无关键线索”“用户显式要求联网/搜索”几类上下文，而不是只测天气单案。
- **FR-012**: 迁移期间系统 MAY 保留兼容 fallback，但必须把其标记为 temporary compatibility path，而不是新的长期架构；运行时必须暴露 `decision_source / decision_fallback_reason / decision_model_resolution_status` 一类 provenance。
- **FR-013**: Butler 默认应采用 hint-first memory runtime；预加载记忆只作为线索和初始摘要，更完整事实、证据与历史应优先通过 `memory.search / memory.recall / memory.read` 继续获取。

## 049 边界说明（2026-03-15 回写）

049 的完成态明确收口为：

- 初版 `BehaviorWorkspace`
- `RuntimeHintBundle + ButlerDecision`
- 初始 Web/CLI 行为文件入口
- Butler 侧的 bounded direct tooling 与 hint-first 决策主链

049 不再承担以下范围：

- 四层 `BehaviorWorkspaceScope`
- project-centered 行为目录与 `project.secret-bindings.json`
- `project_path_manifest + storage_boundary_hints`
- bootstrap 模板与默认会话 Agent 的用户画像/名字/性格引导
- `Agents` 页的完整 Behavior Center 与 `Settings` 行为入口迁移

这些范围统一由 Feature 055 承接。

## Key Entities

- **BehaviorWorkspace**: 初版 project 或 system 作用域下的显式行为文件集合；多 Agent 四层 scope 与 project-centered 布局由 055 承接。
- **BehaviorFile**: `AGENTS.md / USER.md / PROJECT.md / TOOLS.md` 等单个文件的内容、可见性与版本。
- **RuntimeHintBundle**: Butler 决策时可见的环境事实、确认事实、默认值和工具状态集合。
- **ButlerDecision**: 每轮回答前的结构化行为决策结果。
- **WorkerContextCapsule**: Butler 明确转交给 Worker 的共享文件切片和任务上下文。
- **BehaviorPatchProposal**: Agent 生成、等待 review/apply 的行为文件变更提案。

## Success Criteria

- **SC-001**: Butler 默认行为的主要来源从代码特判迁移到显式 behavior workspace，用户能看到并修改这些文件。
- **SC-002**: 天气、推荐、排期等问题的主决策路径不再依赖不断扩张的场景硬编码，而是基于 files + hints + `ButlerDecision`。
- **SC-003**: Worker 默认只消费共享文件和 capsule，不再隐式继承主会话私有行为全集。
- **SC-004**: Web 与 CLI 都能查看 effective behavior source，并完成 behavior patch 的 review/apply。
- **SC-005**: 049 落地后，系统可以对外清晰描述为“代码保留治理，Agent 决定行为”，而不是“继续在代码里增加一个更复杂的补问分类器”。
