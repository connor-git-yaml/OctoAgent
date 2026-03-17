# Feature Specification: Task 详情页可视化模式

**Feature Branch**: `060-061-task-detail`
**Feature ID**: 060-061-task-detail
**Created**: 2026-03-17
**Status**: Draft
**Input**: 为 Task 详情页增加可视化模式，将 65 种 EventType 归纳为用户可理解的阶段，以流水线视图呈现。保留现有 Raw Data 模式，通过 toggle 切换。

---

## 背景与动机

当前 TaskDetail 页面以时间线（timeline）形式逐条展示事件的原始类型和 payload 摘要。这种模式对开发者有用，但对普通用户来说：

1. **信息过载** -- 一个典型任务可能产生数十条事件，类型名如 `MODEL_CALL_COMPLETED`、`TOOL_CALL_STARTED` 等对非技术用户毫无意义
2. **缺乏全局感** -- 用户无法一眼看出任务"走到哪了"，必须逐条阅读事件才能推断当前进度
3. **不符合 UX 规范** -- CLAUDE.md 明确要求"主界面避免直接暴露 debug / 运维术语"

本特性在不改动后端 API 的前提下，纯前端新增一个可视化模式，将事件归纳为用户可理解的阶段（接收 / 思考 / 执行 / 完成），以流水线视图呈现任务全貌。现有的 Raw Data 模式完整保留，作为高级/调试用途。

---

## User Scenarios & Testing

### US-001 -- 查看进行中任务的可视化进度（Priority: P1）

作为用户，我打开一个正在运行的任务详情页，希望一眼看到任务当前走到了哪个阶段（接收 / 思考 / 执行 / 完成），而不是阅读一堆技术事件日志。

**Why this priority**: 这是核心价值。用户最频繁的场景是"我的任务在干什么、走到哪了"。没有阶段可视化，其他增强都失去意义。

**Independent Test**: 发起一个需要工具调用的任务，打开详情页，验证进度条正确高亮当前阶段，阶段卡片展示用户可理解的信息。

**Acceptance Scenarios**:

1. **Given** 用户打开一个状态为 RUNNING 的任务详情页，**When** 页面加载完成，**Then** 默认显示可视化模式：顶部进度条高亮"接收"和"思考"阶段（已完成 + 进行中），中部展示对应阶段卡片
2. **Given** 任务正在执行工具调用，**When** 进度条处于"执行"阶段，**Then** "执行"节点显示呼吸动画，"接收"和"思考"节点显示已完成勾号，"完成"节点为灰色空心
3. **Given** 任务产生了 MODEL_CALL_COMPLETED 事件，**When** 思考卡片展开，**Then** 显示模型名称、token 用量、耗时等用户可理解的摘要信息

---

### US-002 -- SSE 实时更新可视化视图（Priority: P1）

作为用户，当任务正在运行时，我希望可视化视图能实时更新 -- 新事件自动归入对应阶段，进度条跟着推进，而不需要我手动刷新页面。

**Why this priority**: 实时性是可视化模式区别于静态报告的关键体验。没有实时更新，用户仍需反复刷新才能看到进展，价值大打折扣。

**Independent Test**: 在任务运行过程中保持详情页打开，观察 SSE 推送的新事件是否自动归类并更新进度条和卡片内容。

**Acceptance Scenarios**:

1. **Given** 用户正在查看一个进行中任务的可视化模式，**When** SSE 推送一条 TOOL_CALL_STARTED 事件，**Then** 进度条自动推进到"执行"阶段，执行卡片中新增一条工具调用条目（带 slide-in 动画）
2. **Given** SSE 推送 STATE_TRANSITION 到终态 SUCCEEDED，**When** 事件被接收，**Then** 进度条四个阶段全部高亮为已完成，完成卡片展示最终状态 badge 和产出摘要

---

### US-003 -- 切换可视化与 Raw Data 模式（Priority: P1）

作为用户，我希望能在可视化模式和 Raw Data 模式之间自由切换，可视化帮我快速了解全貌，Raw Data 帮我在需要时查看技术细节。

**Why this priority**: 两种模式互补是方案设计的基础约束。只有可视化而丢失 Raw Data 会让高级用户失去调试能力。

**Independent Test**: 在详情页点击模式切换 toggle，验证两种模式都正确渲染相同任务数据。

**Acceptance Scenarios**:

1. **Given** 用户在可视化模式下查看任务，**When** 点击 toggle 切换到"Raw Data"，**Then** 页面切换为原有的事件时间线 + Artifacts 布局，数据完整无丢失
2. **Given** 用户在 Raw Data 模式下，**When** 切换回"可视化"，**Then** 可视化视图立即按当前事件数据重新渲染，阶段归类和进度条状态正确
3. **Given** 用户首次进入任务详情页，**When** 页面加载，**Then** 默认选中"可视化"模式

---

### US-004 -- 查看已完成任务的可视化回顾（Priority: P2）

作为用户，我打开一个已完成（或已失败/已取消）的任务详情页，希望看到任务的完整执行回顾 -- 每个阶段发生了什么、耗时多少、最终结果如何。

**Why this priority**: 虽然不如实时进度紧急，但任务完成后的回顾是常见场景（尤其是检查失败原因）。

**Independent Test**: 打开一个 SUCCEEDED 状态的任务，验证进度条四阶段全部显示已完成，卡片展示完整执行记录。

**Acceptance Scenarios**:

1. **Given** 用户打开一个 SUCCEEDED 任务，**When** 可视化模式加载，**Then** 进度条四个阶段全部亮色填充勾号，完成卡片展示最终状态 badge 和产出摘要
2. **Given** 用户打开一个 FAILED 任务，**When** 可视化模式加载，**Then** 进度条在失败阶段标红，完成卡片用危险色 badge 展示失败状态，展示最后一条 ERROR 事件的用户友好描述

---

### US-005 -- 查看 Artifacts 产出区（Priority: P2）

作为用户，我希望在可视化模式的底部看到任务产出的文件/结果，并以友好格式展示（文件类型图标 + 可读大小），而不是只看到原始字节数。

**Why this priority**: Artifacts 是任务的最终产出，对用户有直接价值。但它是独立的底部区域，不影响核心的阶段可视化。

**Independent Test**: 打开一个产生了 Artifacts 的任务，验证底部 Artifacts 区展示文件图标和人类可读大小。

**Acceptance Scenarios**:

1. **Given** 任务有 3 个 artifacts，**When** 可视化模式加载，**Then** 底部 Artifacts 区展示每个 artifact 的文件类型图标、名称和友好大小（如"1.2 KB"而非"1234 bytes"）
2. **Given** 任务没有任何 artifact，**When** 可视化模式加载，**Then** 底部 Artifacts 区不显示（与当前 Raw Data 模式行为一致）

---

### Edge Cases

- **事件类型未被识别**：后端 EventType 枚举当前有 65 种，未来可能继续增加。未被显式归类的事件应有默认归类策略，不得导致渲染异常或事件丢失
- **空事件列表**：任务刚创建但尚无事件时，可视化模式应展示进度条全灰状态，不报错
- **大量事件**：某些长时间运行的任务可能产生上百条事件，思考/执行卡片内需折叠机制防止页面过长
- **快速连续 SSE 事件**：短时间内收到大量事件时，UI 更新不得导致明显卡顿或闪烁
- **SSE 断连重连**：SSE 重连后应正确渲染积累的事件，不出现重复或遗漏（复用现有 useSSE 的去重逻辑）
- **失败/取消任务的进度条**：非 SUCCEEDED 终态时，进度条应在对应阶段展示特殊状态（失败标红），不是简单的全灰

---

## Requirements

### Functional Requirements

**视图切换**

- **FR-001**: 系统 MUST 在 Task 详情页头部提供 segmented toggle 控件，包含"可视化"和"Raw Data"两个选项
- **FR-002**: 系统 MUST 默认选中"可视化"模式，用户首次进入详情页时直接看到可视化视图
- **FR-003**: 两种模式 MUST 共享相同的 events 和 artifacts 数据源，切换时无需重新请求 API

**事件阶段归类**

- **FR-004**: 系统 MUST 将全部 65 种 EventType 归类到以下 5 个阶段之一：
  - **接收（Received）**: TASK_CREATED, USER_MESSAGE
  - **思考（Thinking）**: MODEL_CALL_STARTED, MODEL_CALL_COMPLETED, MODEL_CALL_FAILED, CONTEXT_COMPACTION_COMPLETED, MEMORY_RECALL_SCHEDULED, MEMORY_RECALL_COMPLETED, MEMORY_RECALL_FAILED, ORCH_DECISION
  - **执行（Executing）**: TOOL_CALL_STARTED, TOOL_CALL_COMPLETED, TOOL_CALL_FAILED, SKILL_STARTED, SKILL_COMPLETED, SKILL_FAILED, WORKER_DISPATCHED, WORKER_RETURNED, WORK_CREATED, WORK_STATUS_CHANGED, EXECUTION_STATUS_CHANGED, EXECUTION_LOG, EXECUTION_STEP, EXECUTION_INPUT_REQUESTED, EXECUTION_INPUT_ATTACHED, EXECUTION_CANCEL_REQUESTED, A2A_MESSAGE_SENT, A2A_MESSAGE_RECEIVED, PIPELINE_RUN_UPDATED, PIPELINE_CHECKPOINT_SAVED, TOOL_INDEX_SELECTED
  - **完成（Completed）**: STATE_TRANSITION（到终态时）, ARTIFACT_CREATED
  - **系统（System）**: 所有不属于以上四类的事件（POLICY_*, APPROVAL_*, CREDENTIAL_*, OAUTH_*, CHECKPOINT_*, RESUME_*, TASK_HEARTBEAT, TASK_MILESTONE, TASK_DRIFT_DETECTED, OPERATOR_ACTION_RECORDED, BACKUP_*, CHAT_IMPORT_*, CONTROL_PLANE_*, POLICY_CONFIG_CHANGED, ERROR, STATE_TRANSITION 到非终态时）
- **FR-005**: 系统 MUST 对未识别的事件类型（不在枚举中的 string）默认归入"系统"阶段 [AUTO-RESOLVED: 归入"系统"阶段最安全，不影响用户核心阅读流程]
- **FR-006**: 阶段归类映射 SHOULD 以前端配置对象形式实现，方便后续调整，不得散落在渲染逻辑中

**进度条（Pipeline Bar）**

- **FR-007**: 系统 MUST 在可视化模式顶部展示水平进度条，包含"接收 / 思考 / 执行 / 完成"四个用户可见阶段（"系统"阶段不在进度条上展示，其事件归入就近的可见阶段卡片下方或折叠展示）
- **FR-008**: 已完成阶段的节点 MUST 使用亮色填充 + 勾号图标
- **FR-009**: 进行中阶段的节点 MUST 使用呼吸动画（CSS @keyframes pulse）
- **FR-010**: 未到达阶段的节点 MUST 使用灰色空心样式
- **FR-011**: 节点间连线 MUST 区分已走过（实线）和未到达（虚线）
- **FR-012**: 进度条 MUST 使用 --cp-success（已完成）、--cp-primary（进行中）、--cp-border（未到达）等已有 design tokens

**阶段卡片流**

- **FR-013**: 系统 MUST 在进度条下方展示阶段卡片，每个已到达的阶段对应一张卡片
- **FR-014**: 接收卡片 MUST 展示：用户消息内容、来源渠道、时间
- **FR-015**: 思考卡片 MUST 展示：模型名称、token 用量、耗时；多轮模型调用 SHOULD 折叠展示，默认显示最新一轮
- **FR-016**: 执行卡片 MUST 展示：工具/Skill 名称 + 执行结果（成功/失败）+ 耗时
- **FR-017**: 完成卡片 MUST 展示：最终状态 badge + 产出物摘要
- **FR-018**: 每张卡片左侧 MUST 有 4px 彩色 border 区分阶段颜色
- **FR-019**: 当单个阶段内事件超过 5 条时，系统 SHOULD 自动折叠较早的事件，保留最近 3 条可见，提供"展开全部"操作

**Artifacts 区**

- **FR-020**: 可视化模式底部 MUST 展示 Artifacts 区（仅当有 artifacts 时）
- **FR-021**: 每个 artifact MUST 展示文件类型图标和友好大小格式化（如 1.2 KB、3.5 MB）

**SSE 实时更新**

- **FR-022**: 收到 SSE 新事件时，系统 MUST 自动将其归类到对应阶段，更新进度条状态
- **FR-023**: 新事件归入卡片时 SHOULD 使用 slide-in 动画效果
- **FR-024**: SSE 实时更新 MUST 复用现有 useSSE hook 和去重逻辑，不引入新的 SSE 连接

**视觉一致性**

- **FR-025**: 所有新增样式 MUST 复用 --cp-* design tokens（颜色、间距、圆角、阴影），不引入硬编码色值
- **FR-026**: 系统 MUST NOT 引入任何新的 npm 依赖

### Key Entities

- **Phase（阶段）**: 用户可理解的任务生命周期阶段。包含 id（received/thinking/executing/completed/system）、显示名称、颜色标识、状态（pending/active/done/error）
- **PhaseMapping（阶段映射）**: EventType 到 Phase 的映射配置。一个 EventType 恰好归入一个 Phase
- **PhaseCard（阶段卡片）**: 可视化模式中每个阶段的展示容器。聚合该阶段的所有事件，提取用户友好的摘要信息

---

### Non-Functional Requirements

- **NFR-001**: 可视化模式渲染 100 条事件时，初次渲染耗时 MUST 不超过 200ms（纯前端，无 API 调用）
- **NFR-002**: SSE 事件到达后，进度条和卡片更新 MUST 在 100ms 内反映到 UI
- **NFR-003**: toggle 切换两种模式 MUST 无感知延迟（小于 50ms），无页面闪烁
- **NFR-004**: 新增的 CSS 和组件代码 MUST 不影响其他页面的渲染性能
- **NFR-005**: 可视化模式 MUST 在主流浏览器的最近两个版本中正常工作（Chrome、Firefox、Safari）

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 用户打开任务详情页后，能在 3 秒内判断任务当前处于哪个阶段（接收/思考/执行/完成），无需阅读任何技术事件名称
- **SC-002**: 可视化模式正确归类全部 65 种 EventType，无遗漏、无渲染错误
- **SC-003**: SSE 实时推送的事件在可视化模式中正确更新，进度条和卡片内容与 Raw Data 模式的事件列表保持数据一致
- **SC-004**: 新旧模式之间切换无数据丢失，Raw Data 模式功能与改动前完全一致
- **SC-005**: 不引入任何新的 npm 依赖，不改动任何后端 API

---

## Out of Scope

- **后端 API 改动**: 不新增、不修改任何后端接口，纯前端实现
- **事件聚合/统计 API**: 不在后端做事件预聚合，全部归类逻辑在前端完成
- **阶段自定义配置 UI**: 用户不能通过 UI 自定义事件到阶段的映射关系（但代码层面映射表可配置）
- **移动端适配**: 本次不考虑移动端响应式布局优化
- **进度条百分比**: 不展示精确百分比，只展示阶段级粒度
- **历史模式偏好持久化**: 不存储用户上次选择的模式偏好（每次默认可视化）
