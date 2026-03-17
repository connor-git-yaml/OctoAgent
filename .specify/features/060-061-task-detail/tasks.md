# Tasks: Task 详情页可视化模式

**Input**: `.specify/features/060-061-task-detail/` (spec.md, plan.md)
**Scope**: 纯前端实现 -- 新增 8 个文件，修改 2 个文件，不改后端

---

## Phase 1: Foundational (类型定义 + 归类引擎)

**Purpose**: 定义数据结构、实现阶段归类纯函数 -- 所有可视化组件依赖此阶段

- [x] T001 [P] 在 `octoagent/frontend/src/types/index.ts` 中新增 Phase 相关类型定义：`PhaseId`、`PhaseStatus`、`PhaseConfig`、`PhaseState`、`ClassifiedResult` -- **S**
  - 文件: `octoagent/frontend/src/types/index.ts`（修改）
  - 依赖: 无
  - 说明: 新增类型定义追加到文件末尾，不修改现有类型

- [x] T002 创建 `octoagent/frontend/src/utils/phaseClassifier.ts` -- 阶段归类引擎 -- **M**
  - 文件: `octoagent/frontend/src/utils/phaseClassifier.ts`（新增）
  - 依赖: T001
  - 说明: 包含 `PHASE_CONFIGS` 常量（5 个阶段的 label/color/userVisible 配置）、`PHASE_MAP` 映射表（EventType -> PhaseId，覆盖全部 67 种 + 兜底 system）、`classifyStateTransition()` 特殊处理函数、`classifyEvents()` 主函数（events + taskStatus -> PhaseState[]）、`formatFileSize()` 工具函数

**Checkpoint**: 归类引擎可独立测试 -- 传入 mock events 数组，验证返回的 PhaseState[] 正确

---

## Phase 2: US-001 + US-003 -- 核心可视化框架 (Priority: P1)

**Goal**: 用户打开详情页默认看到可视化模式，进度条正确反映阶段进度，可切换到 Raw Data 模式

**Independent Test**: 打开一个 RUNNING 任务详情页，验证进度条高亮当前阶段，点击 toggle 可切换回 Raw Data 模式

- [x] T003 [P] 创建 `octoagent/frontend/src/components/TaskVisualization/task-visualization.css` -- 全部可视化样式 -- **M**
  - 文件: `octoagent/frontend/src/components/TaskVisualization/task-visualization.css`（新增）
  - 依赖: 无
  - 说明: 所有 class 使用 `tv-` 前缀。包含：SegmentedToggle 样式（`.tv-segmented`）、PipelineBar 样式（`.tv-pipeline-bar`、`.tv-pipeline-node`、`.tv-pipeline-line`、`@keyframes tv-pulse`）、PhaseCard 样式（`.tv-phase-card`、`.tv-phase-card-header`、`.tv-phase-event`、`@keyframes tv-slide-in`）、ArtifactGrid 样式（`.tv-artifact-grid`、`.tv-artifact-card`）。复用 `var(--cp-*)` design tokens

- [x] T004 [P] 创建 `octoagent/frontend/src/components/TaskVisualization/SegmentedToggle.tsx` -- 模式切换控件 -- **S**
  - 文件: `octoagent/frontend/src/components/TaskVisualization/SegmentedToggle.tsx`（新增）
  - 依赖: T003（样式）
  - 说明: Props: `{ value: "visual" | "raw"; onChange: (v) => void }`。两段式 segmented control，选中项背景高亮，CSS transition 切换

- [x] T005 [P] 创建 `octoagent/frontend/src/components/TaskVisualization/PipelineBar.tsx` -- 四节点进度条 -- **M**
  - 文件: `octoagent/frontend/src/components/TaskVisualization/PipelineBar.tsx`（新增）
  - 依赖: T001、T003
  - 说明: Props: `{ phases: PhaseState[] }`。渲染 4 个用户可见阶段（排除 system）的圆形节点 + 连线。节点状态：done（实心+勾号）、active（呼吸动画）、error（实心+叉号）、pending（空心）。连线：已走过实线、未到达虚线。CSS 伪元素实现勾号/叉号

- [x] T006 创建 `octoagent/frontend/src/components/TaskVisualization/PhaseCard.tsx` -- 单个阶段卡片 -- **L**
  - 文件: `octoagent/frontend/src/components/TaskVisualization/PhaseCard.tsx`（新增）
  - 依赖: T001、T003
  - 说明: Props: `{ phase: PhaseState }`。左侧 4px 彩色 border + 阶段图标/名称/时间范围 + 事件摘要列表。按 PhaseId 差异化提取摘要：接收（消息内容+渠道）、思考（模型+token+耗时）、执行（工具名+结果+耗时）、完成（终态 badge+产出）。>5 条事件时折叠，默认显示最近 3 条 + "展开全部"按钮

- [x] T007 创建 `octoagent/frontend/src/components/TaskVisualization/PhaseCardList.tsx` -- 卡片流容器 -- **S**
  - 文件: `octoagent/frontend/src/components/TaskVisualization/PhaseCardList.tsx`（新增）
  - 依赖: T006
  - 说明: Props: `{ phases: PhaseState[] }`。遍历 phases，仅渲染 status 非 pending 的阶段（已到达），为每个渲染 PhaseCard

- [x] T008 创建 `octoagent/frontend/src/components/TaskVisualization/index.ts` -- barrel export -- **S**
  - 文件: `octoagent/frontend/src/components/TaskVisualization/index.ts`（新增）
  - 依赖: T004、T005、T007
  - 说明: 导出 SegmentedToggle、PipelineBar、PhaseCardList、ArtifactGrid（T009）和 CSS import

- [x] T009 修改 `octoagent/frontend/src/pages/TaskDetail.tsx` -- 引入 toggle + 双模式渲染 -- **M**
  - 文件: `octoagent/frontend/src/pages/TaskDetail.tsx`（修改）
  - 依赖: T002、T004、T005、T007
  - 说明: 新增 `viewMode` state（"visual" | "raw"，默认 "visual"）。在任务头部下方插入 SegmentedToggle。viewMode === "visual" 时：调用 `classifyEvents(events, task.status)` 获取 phases，渲染 PipelineBar + PhaseCardList。viewMode === "raw" 时：渲染现有的事件时间线 + Artifacts 区（保持不变）。handleSSEEvent 回调完全不改

**Checkpoint**: US-001 + US-003 可验证 -- 进度条正确显示阶段，toggle 切换无数据丢失，SSE 实时更新自动反映到可视化（US-002 自动获得，因为数据流不变）

---

## Phase 3: US-004 + US-005 -- 完成态回顾 + Artifacts 区 (Priority: P2)

**Goal**: 已完成/失败任务的可视化回顾正确渲染，底部 Artifacts 区以友好格式展示

**Independent Test**: 打开 SUCCEEDED 任务验证四阶段全亮，打开 FAILED 任务验证失败阶段标红，验证 Artifacts 区显示文件图标和可读大小

- [x] T010 创建 `octoagent/frontend/src/components/TaskVisualization/ArtifactGrid.tsx` -- Artifacts 网格 -- **S**
  - 文件: `octoagent/frontend/src/components/TaskVisualization/ArtifactGrid.tsx`（新增）
  - 依赖: T001、T002（formatFileSize）、T003
  - 说明: Props: `{ artifacts: Artifact[] }`。CSS grid 布局（auto-fill, minmax(200px, 1fr)）。每个 artifact card 显示文件类型图标（基于 mime/扩展名，CSS + Unicode）、名称、友好大小。无 artifacts 时不渲染

- [x] T011 在 TaskDetail.tsx 可视化模式中挂载 ArtifactGrid -- **S**
  - 文件: `octoagent/frontend/src/pages/TaskDetail.tsx`（修改）、`octoagent/frontend/src/components/TaskVisualization/index.ts`（修改）
  - 依赖: T009、T010
  - 说明: 在可视化模式的 PhaseCardList 下方添加 ArtifactGrid 渲染（仅 artifacts.length > 0 时）。更新 barrel export 包含 ArtifactGrid

**Checkpoint**: US-004 + US-005 可验证 -- 已完成任务全阶段回顾正确，失败任务标红，Artifacts 区图标+可读大小

---

## Phase 4: Polish & Cross-Cutting

**Purpose**: 边界情况处理、动画完善、代码清理

- [x] T012 [P] 边界情况处理：空事件列表、未识别事件类型、快速连续 SSE -- **S**
  - 文件: `octoagent/frontend/src/utils/phaseClassifier.ts`（修改）、`octoagent/frontend/src/components/TaskVisualization/PipelineBar.tsx`（修改）
  - 依赖: T002、T005
  - 说明: 确认空 events 返回全 pending phases + 进度条全灰；确认未识别 EventType 字符串归入 system；确认 React key 策略正确避免 SSE 快速更新闪烁

- [x] T013 [P] 新事件 slide-in 动画完善 -- **S**
  - 文件: `octoagent/frontend/src/components/TaskVisualization/PhaseCard.tsx`（修改）、`octoagent/frontend/src/components/TaskVisualization/task-visualization.css`（修改）
  - 依赖: T006、T003
  - 说明: 为 PhaseCard 内新增事件的 DOM 节点添加 `.tv-phase-event-enter` class + `@keyframes tv-slide-in` 动画（translateY(12px) + opacity 0 -> normal, 300ms）。利用 React key 保证仅新节点触发

---

## FR 覆盖映射

| FR | 任务 |
|----|------|
| FR-001 (segmented toggle) | T004, T009 |
| FR-002 (默认可视化) | T009 |
| FR-003 (共享数据源) | T009 |
| FR-004 (65 种归类) | T002 |
| FR-005 (未识别兜底) | T002, T012 |
| FR-006 (映射配置化) | T002 |
| FR-007 (进度条 4 阶段) | T005 |
| FR-008 (已完成勾号) | T005, T003 |
| FR-009 (呼吸动画) | T005, T003 |
| FR-010 (未到达灰色) | T005, T003 |
| FR-011 (连线实/虚) | T005, T003 |
| FR-012 (design tokens) | T003 |
| FR-013 (阶段卡片流) | T006, T007 |
| FR-014 (接收卡片) | T006 |
| FR-015 (思考卡片) | T006 |
| FR-016 (执行卡片) | T006 |
| FR-017 (完成卡片) | T006 |
| FR-018 (4px 彩色左边框) | T006, T003 |
| FR-019 (折叠 >5 条) | T006 |
| FR-020 (Artifacts 区) | T010, T011 |
| FR-021 (友好大小) | T002 (formatFileSize), T010 |
| FR-022 (SSE 自动归类) | T002, T009 |
| FR-023 (slide-in 动画) | T013 |
| FR-024 (复用 useSSE) | T009 |
| FR-025 (复用 tokens) | T003 |
| FR-026 (无新依赖) | 全局约束 |

覆盖率: 26/26 = **100%**

---

## Dependencies & Execution Order

### Phase 依赖

```
Phase 1 (T001-T002)  ──→  Phase 2 (T003-T009)  ──→  Phase 3 (T010-T011)  ──→  Phase 4 (T012-T013)
```

### 并行机会

- **Phase 1**: T001 可独立先行，T002 依赖 T001
- **Phase 2**: T003/T004/T005/T006 四个文件互不依赖（仅共享类型定义），可并行创建。T007 依赖 T006，T008 依赖 T004+T005+T007，T009 依赖 T002+T004+T005+T007
- **Phase 3**: T010 可与 Phase 2 后期任务并行，T011 依赖 T009+T010
- **Phase 4**: T012 和 T013 互不依赖，可并行

### 推荐实现顺序（单人串行）

T001 -> T002 -> T003 -> T005 -> T004 -> T006 -> T007 -> T008 -> T009 -> T010 -> T011 -> T012 -> T013

---

## Notes

- 全部 13 个任务，覆盖 5 个 User Stories
- 8 个任务可并行（标 [P] 或不同文件无依赖）
- 预估总量：~650 行新增代码 + ~30 行修改代码
- 不引入任何 npm 依赖，不改后端
