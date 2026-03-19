# Feature Specification: Task 可视化时间轴对齐泳道布局

**Feature ID**: 065-task-timeline-swimlane-layout
**Created**: 2026-03-19
**Status**: Draft
**Input**: 将 Task 可视化的泳道从等宽线性排列改为按时间戳定位的时间轴布局，支持 Worker 节点展宽、跨泳道连接线、统一水平滚动，并在时间数据不足时优雅降级。

---

## User Scenarios & Testing

### User Story 1 - 时间轴对齐的泳道布局 (Priority: P1)

用户在 Task 详情页查看一个涉及 Orchestrator 和 Worker 的任务时，希望各泳道的节点按照真实发生时间在横轴上对齐排列，能直观看出"哪些操作在时间上是并行的、哪些是串行的"，而不是全部等宽排布看不出时序关系。

**Why this priority**: 这是整个功能的核心价值所在。当前等宽布局完全丧失了时间维度信息，无法判断步骤间的真实并行关系和耗时分布。时间轴对齐是后续展宽和斜线连接的基础。

**Independent Test**: 创建一个含多个 Agent 泳道的任务，查看 Task 详情页可视化区域。节点应按时间戳水平定位，同一时刻发生的事件在不同泳道间垂直对齐。

**Acceptance Scenarios**:

1. **Given** 一个任务包含 Orchestrator 和 Worker 两个泳道且事件均有有效时间戳, **When** 用户打开该任务的可视化视图, **Then** 顶部出现时间刻度尺，节点按时间戳水平定位，相同时刻的节点在不同泳道间垂直对齐。
2. **Given** 任务时长跨度超过屏幕宽度, **When** 用户水平滚动, **Then** 所有泳道同步滚动，时间刻度尺始终与节点位置对应。
3. **Given** 两个节点时间戳非常接近（间隔 < 1 秒）, **When** 计算布局时, **Then** 节点不互相重叠，系统自动进行防重叠修正使两个节点仍可分别识别和点击。

---

### User Story 2 - Worker 节点展宽为胶囊条 (Priority: P1)

用户在 Orchestrator 泳道看到一个 Worker 节点时，希望该节点的宽度反映 Worker 的实际执行时长，而不是固定的小圆点，这样一眼就能看出哪个 Worker 执行了很久、哪个很快就完成了。

**Why this priority**: Worker 执行时长是用户最关注的性能指标之一，与 Story 1 共同构成 MVP 最小可交付单元。

**Independent Test**: 创建一个包含两个 Worker 的任务（一个执行 2 秒、一个执行 30 秒），查看 Orchestrator 泳道中两个 Worker 节点的视觉宽度差异。

**Acceptance Scenarios**:

1. **Given** Orchestrator 泳道中有一个 Worker 节点且该 Worker 执行了 10 秒, **When** 渲染时间轴布局, **Then** 该 Worker 节点显示为胶囊条形状（pill-shaped bar），宽度按 10 秒对应的像素值展宽。
2. **Given** Worker 执行时长极短（< 1 秒）, **When** 渲染布局, **Then** Worker 节点仍保持最小宽度（不小于标准节点尺寸 48px），不会因时间过短而变成不可见的窄条。
3. **Given** Orchestrator 泳道中一个 Worker 节点展宽占据了较大的时间区间, **When** 对比 Worker 自身泳道, **Then** Worker 泳道的首尾时间范围与 Orchestrator 泳道中该 Worker 胶囊条的起止位置在时间轴上对齐。

---

### User Story 3 - 跨泳道斜线连接 (Priority: P2)

用户希望看到 Orchestrator 派发 Worker 和 Worker 返回结果的连接关系，用斜线从 Orchestrator 的 Worker 节点连接到 Worker 泳道的首个节点（dispatch），以及从 Worker 泳道末尾连回 Orchestrator（return），形成可视化的调用-返回流。

**Why this priority**: 跨泳道连接是理解多 Agent 协作关系的关键视觉线索，但非 MVP 最小必要——即使没有斜线，用户通过时间对齐和展宽已经能大致理解并行关系。作为 P2 可在 P1 稳定后追加。

**Independent Test**: 查看一个 Orchestrator 派发了 Worker 的任务，确认有斜线从 Orchestrator 的 Worker 节点连向对应 Worker 泳道的起始位置。

**Acceptance Scenarios**:

1. **Given** Orchestrator 泳道有一个 Worker 节点且存在对应的 Worker 泳道, **When** 渲染时间轴视图, **Then** 一条 dispatch 斜线从 Worker 节点左侧连向 Worker 泳道首个节点，一条 return 斜线从 Worker 泳道末尾节点连回 Orchestrator。
2. **Given** 斜线覆盖在节点上方, **When** 用户点击节点, **Then** 斜线不阻挡节点的点击交互（斜线层不响应鼠标事件）。
3. **Given** dispatch 连接线和 return 连接线, **When** 视觉呈现, **Then** dispatch 线和 return 线使用不同颜色区分（如 dispatch 为橙色、return 为绿色），并采用虚线样式。

---

### User Story 4 - 时间数据不足时优雅降级 (Priority: P1)

用户查看旧任务（系统升级前创建的任务，事件缺少时间戳）时，可视化不能崩溃或空白，必须回退到当前的等宽 flex 布局，保证一切功能如常运转。

**Why this priority**: 数据兼容性是必须保障的底线。如果新布局在旧数据上崩溃，用户体验将严重倒退。

**Independent Test**: 查看一个旧任务（大部分事件没有有效时间戳），确认页面正常渲染为当前的等宽布局。

**Acceptance Scenarios**:

1. **Given** 一个任务的事件中有效时间戳少于 2 个, **When** 系统计算布局, **Then** 返回降级标志 (`degraded = true`)，采用当前 flex 等宽布局渲染，视觉效果与改动前完全一致。
2. **Given** 混合数据（部分节点有时间戳、部分没有）且有效时间戳 >= 2 个, **When** 系统计算布局, **Then** 有时间戳的节点按时间定位，无时间戳的节点在其前后有时间戳的节点之间均匀分布。
3. **Given** 降级布局被激活, **When** 用户点击节点查看详情弹框, **Then** 弹框功能正常工作，与当前行为无差异。

---

### User Story 5 - 时间刻度尺 (Priority: P2)

用户希望在泳道上方看到一个时间刻度尺，标注绝对时间或相对时间，让时间轴布局有明确的时间参照。

**Why this priority**: 刻度尺增强了时间轴的可读性，但核心价值（节点对齐、展宽）不依赖于刻度尺。

**Independent Test**: 查看一个新任务的时间轴视图，确认泳道上方显示了带有时间标注的刻度尺。

**Acceptance Scenarios**:

1. **Given** 任务时长为 5 秒, **When** 渲染时间刻度尺, **Then** 刻度间隔自动选择为 1 秒。
2. **Given** 任务时长为 3 分钟, **When** 渲染时间刻度尺, **Then** 刻度间隔自动选择为 30 秒或 1 分钟（不过密也不过疏）。
3. **Given** 降级布局（degraded = true）, **When** 渲染视图, **Then** 不显示时间刻度尺。

---

### Edge Cases

- **所有节点时间戳完全相同**: 当一个轮次内所有事件的时间戳完全相同（如批量导入数据），时间范围为 0 时，布局应降级为等宽布局而不是除以零崩溃。
- **极长任务时间跨度（> 10 分钟）**: 缩放因子需受最大总宽度（8000px）约束，超长任务不应生成过宽的布局，同时节点不应因缩放过小而不可见。
- **单泳道任务（仅 Orchestrator）**: 没有 Worker 泳道时，跨泳道连接线逻辑应安全跳过，不生成任何斜线，时间轴布局仍然正常工作。
- **Worker 泳道在 Orchestrator 节点之前开始**: 如果事件排序导致 Worker 首个事件早于 Orchestrator 的 dispatch 事件，斜线连接应正常处理或安全忽略。
- **节点点击穿透**: SVG overlay 层必须设置 `pointer-events: none`，确保不阻挡底层节点的点击和 hover 交互。
- **浏览器窗口窄于泳道内容**: 统一滚动容器必须正确工作，不出现双滚动条或泳道错位。

---

## Requirements

### Functional Requirements

#### 布局计算

- **FR-001**: 系统 MUST 基于事件时间戳计算每个节点的水平位置（`leftPx`），使节点按真实时间在横轴上定位。
- **FR-002**: 系统 MUST 对 Worker 类型节点计算展宽宽度（`widthPx`），宽度 = max(执行时长 * 缩放因子, 最小节点宽度 48px)。
- **FR-003**: 系统 MUST 执行防重叠修正：当两个相邻节点计算位置过近时，后一个节点应向右偏移以保证最小间距。
- **FR-004**: 系统 MUST 对总宽度施加上限约束（maxTotalPx），防止极长任务产生过宽布局。

#### 降级机制

- **FR-005**: 系统 MUST 在有效时间戳少于 2 个时降级为等宽 flex 布局，渲染结果与改动前完全一致。
- **FR-006**: 系统 MUST 在所有节点时间戳相同（时间范围为 0）时降级为等宽布局。

#### 渲染

- **FR-007**: 系统 MUST 提供统一水平滚动容器，所有泳道和时间刻度尺在同一滚动区域内同步滚动。
- **FR-008**: 系统 SHOULD 在泳道上方渲染时间刻度尺，刻度间隔根据任务时长自动选择（1s / 5s / 30s / 1min）。
- **FR-009**: 系统 MUST 将 Worker 展宽节点渲染为胶囊条形状（pill-shaped bar），区别于普通圆形节点。

#### 跨泳道连接

- **FR-010**: 系统 SHOULD 生成跨泳道连接线描述（dispatch 和 return），用于标示 Orchestrator 与 Worker 之间的调用关系。
- **FR-011**: 系统 SHOULD 使用 SVG overlay 层渲染跨泳道斜线，该层 MUST 设置 `pointer-events: none` 以不阻挡节点交互。
- **FR-012**: dispatch 连接线和 return 连接线 SHOULD 使用不同视觉样式（颜色、线型）以便区分。

#### 兼容性

- **FR-013**: 系统 MUST 保持节点点击弹框功能在时间轴布局和降级布局下均正常工作。
- **FR-014**: 系统 MUST 保持泳道折叠/展开功能在时间轴布局下正常工作。

### Key Entities

- **TimelineLayout**: 时间轴布局计算结果，包含总宽度、每个节点的位置和尺寸映射、跨泳道连接线描述、时间刻度标记、以及是否降级标志。
- **NodeLayout**: 单个节点的布局信息，包含水平起始位置（leftPx）、宽度（widthPx，展宽节点 > 48px）、所属泳道索引。
- **CrossLaneLink**: 跨泳道连接线描述，包含起始泳道/节点、目标泳道/节点、连接类型（dispatch / return）。
- **TimeTick**: 时间刻度标记，包含时间值和水平位置。

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 含有效时间戳的新任务在可视化视图中，同一时刻的事件在不同泳道间垂直对齐，偏差不超过 1 个刻度间隔。
- **SC-002**: Worker 节点在 Orchestrator 泳道中的视觉宽度与其实际执行时长成正比，用户可通过宽度直觉判断哪个 Worker 耗时最长。
- **SC-003**: 旧任务（无有效时间戳）的可视化渲染结果与改动前完全一致，无视觉差异、无功能缺失。
- **SC-004**: 时间轴模式下的水平滚动流畅，所有泳道保持同步对齐，不出现泳道错位或双滚动条。
- **SC-005**: TypeScript 编译（`npx tsc --noEmit`）无错误通过。
