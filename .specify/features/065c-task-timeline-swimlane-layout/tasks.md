# Tasks: Task 可视化时间轴对齐泳道布局

**Feature ID**: `065-task-timeline-swimlane-layout`
**Date**: 2026-03-19
**Input**: spec.md, plan.md, data-model.md, contracts/timeline-layout-api.md
**Source Files**: `roundSplitter.ts`, `RoundFlowCard.tsx`, `task-visualization.css`

---

## User Stories Summary

| Story | Title | Priority | Phase |
|-------|-------|----------|-------|
| US1 | 时间轴对齐的泳道布局 | P1 | Phase 2 |
| US2 | Worker 节点展宽为胶囊条 | P1 | Phase 2 |
| US4 | 时间数据不足时优雅降级 | P1 | Phase 2 |
| US3 | 跨泳道斜线连接 | P2 | Phase 4 |
| US5 | 时间刻度尺 | P2 | Phase 3 |

> 注: US1/US2/US4 均为 P1 核心功能，共同构成 MVP。US3/US5 为 P2 增强。

---

## Phase 1: Foundational -- TypeScript 接口定义

**Purpose**: 定义所有新增类型接口和布局计算常量，为后续数据层和组件层提供类型基础。

- [x] T001 [P] 在 `roundSplitter.ts` 顶部定义 `NodeLayout` 接口
  - **File**: `octoagent/frontend/src/utils/roundSplitter.ts`
  - **Position**: 在 `AgentLane` 接口之后新增
  - **Output**: `export interface NodeLayout { leftPx: number; widthPx: number; laneIndex: number; }`
  - **Verify**: `npx tsc --noEmit` 通过

- [x] T002 [P] 在 `roundSplitter.ts` 定义 `CrossLaneLink` 接口
  - **File**: `octoagent/frontend/src/utils/roundSplitter.ts`
  - **Output**: `export interface CrossLaneLink { fromLaneIndex: number; fromNodeId: string; toLaneIndex: number; toNodeId: string; type: "dispatch" | "return"; }`
  - **Verify**: `npx tsc --noEmit` 通过

- [x] T003 [P] 在 `roundSplitter.ts` 定义 `TimeTick` 接口
  - **File**: `octoagent/frontend/src/utils/roundSplitter.ts`
  - **Output**: `export interface TimeTick { label: string; leftPx: number; }`
  - **Verify**: `npx tsc --noEmit` 通过

- [x] T004 [P] 在 `roundSplitter.ts` 定义 `TimelineLayout` 接口
  - **File**: `octoagent/frontend/src/utils/roundSplitter.ts`
  - **Depends on**: T001, T002, T003（类型引用）
  - **Output**: `export interface TimelineLayout { totalWidthPx: number; nodeLayouts: Map<string, NodeLayout>; crossLaneLinks: CrossLaneLink[]; timeTicks: TimeTick[]; degraded: boolean; }`
  - **Verify**: `npx tsc --noEmit` 通过

- [x] T005 [P] 在 `roundSplitter.ts` 定义布局计算常量
  - **File**: `octoagent/frontend/src/utils/roundSplitter.ts`
  - **Output**: `PX_PER_SECOND = 12`, `MAX_TOTAL_PX = 8000`, `MIN_NODE_WIDTH = 48`, `MIN_GAP = 8`, `PADDING = 24`
  - **Verify**: `npx tsc --noEmit` 通过

**Checkpoint**: 所有类型定义就绪，后续函数实现可引用这些类型。

---

## Phase 2: P1 核心 -- 时间轴布局计算 + 降级 + 基础渲染 (US1 + US2 + US4)

**Goal**: 实现时间轴布局的完整数据层和组件层渲染，覆盖正常时间轴模式和降级模式，支持 Worker 节点展宽。

**Independent Test**:
- 新任务（有时间戳）: 节点按时间横轴定位，Worker 展宽为胶囊条
- 旧任务（无时间戳）: 渲染结果与改动前完全一致
- 混合数据: 有效时间戳 >= 2 时正常布局，缺失节点均匀分布

### 数据层 -- roundSplitter.ts

- [x] T006 [US4] 实现 `buildDegradedLayout()` 函数
  - **File**: `octoagent/frontend/src/utils/roundSplitter.ts`
  - **Depends on**: T004
  - **Input**: `lanes: AgentLane[]`
  - **Output**: `TimelineLayout` 对象，`degraded: true`，`nodeLayouts` 为空 Map，`crossLaneLinks/timeTicks` 为空数组，`totalWidthPx: 0`
  - **Logic**: 纯构造函数，无复杂逻辑
  - **Covers**: FR-005, FR-006（降级基础设施）
  - **Verify**: 函数返回值符合 `TimelineLayout` 类型，`degraded === true`

- [x] T007 [US1] 实现 `computeTimelineLayout()` 主函数 -- 时间戳解析与降级判断
  - **File**: `octoagent/frontend/src/utils/roundSplitter.ts`
  - **Depends on**: T005, T006
  - **Input**: `lanes: AgentLane[]`, `startTime: string`, `endTime?: string`
  - **Output**: `TimelineLayout`
  - **Logic**:
    1. 遍历所有 lanes 的所有 nodes，解析 `node.ts` 为毫秒时间戳（`Date.parse()`）
    2. 过滤有效时间戳（非 NaN、非 0）
    3. 有效时间戳 < 2 -> 调用 `buildDegradedLayout(lanes)` 返回
    4. 计算 `tMin = min(validTimestamps)`, `tMax = max(validTimestamps)`
    5. `tMax === tMin`（时间范围 = 0）-> 调用 `buildDegradedLayout(lanes)` 返回
  - **Covers**: FR-005, FR-006
  - **Verify**: 无有效时间戳时返回 `degraded: true`；所有时间戳相同时返回 `degraded: true`

- [x] T008 [US1] 实现 `computeTimelineLayout()` -- 缩放因子与节点定位
  - **File**: `octoagent/frontend/src/utils/roundSplitter.ts`
  - **Depends on**: T007
  - **Logic**（接 T007 正常路径）:
    1. 计算原始缩放: `rawScale = PX_PER_SECOND / 1000`（px/ms）
    2. 计算原始总宽度: `rawWidth = PADDING * 2 + (tMax - tMin) * rawScale`
    3. 如果 `rawWidth > MAX_TOTAL_PX`，重新计算: `scale = (MAX_TOTAL_PX - PADDING * 2) / (tMax - tMin)`
    4. 否则 `scale = rawScale`
    5. 遍历所有节点，计算 `leftPx = PADDING + (nodeTs - tMin) * scale`
    6. Worker 节点（`kind === 'worker'`）: `widthPx = max(node.durationMs * scale, MIN_NODE_WIDTH)`
    7. 普通节点: `widthPx = MIN_NODE_WIDTH`
    8. 填充 `nodeLayouts` Map: `node.id -> { leftPx, widthPx, laneIndex }`
  - **Covers**: FR-001, FR-002, FR-004
  - **Verify**: 节点 `leftPx` 单调递增（同泳道内）；Worker `widthPx >= MIN_NODE_WIDTH`；总宽度 <= MAX_TOTAL_PX

- [x] T009 [US1] 实现 `computeTimelineLayout()` -- 防重叠修正
  - **File**: `octoagent/frontend/src/utils/roundSplitter.ts`
  - **Depends on**: T008
  - **Logic**:
    1. 遍历每个泳道的节点列表（按时间序）
    2. 对于每对相邻节点 `(prev, next)`，检查: `nextLeft < prevLeft + prevWidth + MIN_GAP`
    3. 如果重叠，修正: `nextLeft = prevLeft + prevWidth + MIN_GAP`
    4. 修正后更新 `nodeLayouts` Map 中对应条目
    5. 重新计算 `totalWidthPx = max(所有节点 leftPx + widthPx) + PADDING`
  - **Covers**: FR-003
  - **Verify**: 同一泳道内任意相邻节点间距 >= `MIN_GAP`

- [x] T010 [US1] 实现混合时间戳插值逻辑
  - **File**: `octoagent/frontend/src/utils/roundSplitter.ts`
  - **Depends on**: T008
  - **Logic**:
    1. 在 T007 中统计有效时间戳 >= 2 但部分节点缺失时间戳的情况
    2. 缺失时间戳的节点：找到其前后最近的有效时间戳节点
    3. 在两个有效时间戳之间进行线性插值，均匀分配缺失节点的 `leftPx`
    4. 如果缺失节点在头部（前无有效节点），使用 `tMin` 作为参考
    5. 如果缺失节点在尾部（后无有效节点），使用 `tMax` 作为参考
  - **Covers**: FR-001（混合数据场景），spec US4 Acceptance 2
  - **Verify**: 缺失时间戳的节点在有效节点之间均匀分布，不重叠

### 组件层 -- RoundFlowCard.tsx

- [x] T011 [US4] 在 `RoundFlowCard` 中调用布局计算并实现条件分支
  - **File**: `octoagent/frontend/src/components/TaskVisualization/RoundFlowCard.tsx`
  - **Depends on**: T007
  - **Import**: `computeTimelineLayout` from `roundSplitter.ts`
  - **Logic**:
    1. 在已有 `lanes` useMemo 之后新增: `const layout = useMemo(() => computeTimelineLayout(lanes, round.startTime, round.endTime), [lanes, round.startTime, round.endTime]);`
    2. 在泳道渲染区域 (`<div className="tv-lanes">`) 前添加条件分支:
       - `layout.degraded === true`: 保留当前 `tv-lanes` > `tv-lane` > `tv-lane-flow-scroll` > `tv-lane-flow`(flex) 的完整 JSX，**不做任何改动**
       - `layout.degraded === false`: 渲染时间轴路径（T012/T013/T014 实现）
  - **Covers**: FR-005, FR-013, FR-014
  - **Verify**: 旧数据（无时间戳）渲染结果与改动前完全一致；节点点击弹框正常；泳道折叠/展开正常

- [x] T012 [US1] 实现时间轴路径 -- 统一滚动容器和泳道轨道
  - **File**: `octoagent/frontend/src/components/TaskVisualization/RoundFlowCard.tsx`
  - **Depends on**: T011
  - **Logic**:
    1. 外层: `div.tv-timeline-container` 包裹所有时间轴内容
    2. 滚动容器: `div.tv-lanes-scroll`（`overflow-x: auto`），包含刻度尺和所有泳道
    3. 泳道标签: 保持原有 `div.tv-lane-label` 结构不变（包含 agent 名称、耗时、状态）
    4. 泳道轨道: `div.tv-lane-track`（`position: relative; width: ${layout.totalWidthPx}px`）
    5. 节点: 复用现有 `button.tv-flow-node`，改为 `style={{ position: 'absolute', left: nl.leftPx, width: nl.widthPx }}`
    6. 时间轴模式下不渲染 `tv-flow-connector`（间距由 absolute 定位保证）
    7. 泳道折叠逻辑（`shouldCollapse`）在时间轴模式下保持正常工作
  - **Covers**: FR-007, FR-014
  - **Verify**: 所有泳道在同一滚动容器内同步滚动；无双滚动条；泳道折叠功能正常

- [x] T013 [US2] 实现 Worker 展宽节点渲染（胶囊条）
  - **File**: `octoagent/frontend/src/components/TaskVisualization/RoundFlowCard.tsx`
  - **Depends on**: T012
  - **Logic**:
    1. 从 `layout.nodeLayouts.get(node.id)` 获取 `widthPx`
    2. 当 `widthPx > MIN_NODE_WIDTH(48)` 时:
       - 节点追加 class `tv-flow-node--span`
       - 内部用 `div.tv-flow-node-bar`（pill-shaped container）包裹图标 + 标签
       - `tv-flow-node-bar` 宽度为 `widthPx`
    3. 当 `widthPx <= 48` 时:
       - 保持普通圆形节点渲染（现有 `tv-flow-node-circle` 结构）
    4. 两种渲染均保留: 耗时角标(`tv-flow-node-dur`)、Artifact 角标(`tv-flow-node-artifact`)、`onNodeClick` 回调
  - **Covers**: FR-002, FR-009, FR-013
  - **Verify**: 长时长 Worker 显示为胶囊条，短时长 Worker 显示为圆形；点击功能正常

- [x] T014 [US1] 保持节点交互功能在时间轴模式下正常
  - **File**: `octoagent/frontend/src/components/TaskVisualization/RoundFlowCard.tsx`
  - **Depends on**: T012, T013
  - **Logic**:
    1. 确认 `onNodeClick` 回调在时间轴路径的每个 `button.tv-flow-node` 上正确绑定
    2. 确认 `title={node.label}` 属性保留
    3. 确认 `shouldCollapse` 逻辑在时间轴渲染路径中也正确处理泳道折叠
    4. 确认 `expanded`/`setExpanded` 交互在时间轴模式下正常
  - **Covers**: FR-013, FR-014
  - **Verify**: 点击节点弹出详情弹框；折叠/展开按钮正常

### 样式层 -- task-visualization.css

- [x] T015 [P] [US1] 新增统一滚动容器样式
  - **File**: `octoagent/frontend/src/components/TaskVisualization/task-visualization.css`
  - **Depends on**: T012（确认 class 名）
  - **Output**: `.tv-timeline-container { position: relative; }` + `.tv-lanes-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }`
  - **Covers**: FR-007
  - **Verify**: 水平滚动正常，触摸设备滚动流畅

- [x] T016 [P] [US1] 新增泳道轨道和 absolute 定位节点样式
  - **File**: `octoagent/frontend/src/components/TaskVisualization/task-visualization.css`
  - **Depends on**: T012
  - **Output**: `.tv-lane-track { position: relative; min-height: 52px; }` + `.tv-lane-track .tv-flow-node { position: absolute; top: 4px; }`
  - **Covers**: FR-001
  - **Verify**: 节点在轨道内通过 absolute 定位正确排列

- [x] T017 [P] [US2] 新增 Worker 胶囊条样式
  - **File**: `octoagent/frontend/src/components/TaskVisualization/task-visualization.css`
  - **Depends on**: T013
  - **Output**:
    - `.tv-flow-node--span { flex-direction: row; gap: 6px; }`
    - `.tv-flow-node-bar { display: flex; align-items: center; gap: 6px; height: 32px; border-radius: 16px; padding: 0 12px; background: var(--cp-success-soft); width: 100%; }`
    - `.tv-flow-node--span .tv-flow-node-text { max-width: none; }`
  - **Covers**: FR-009
  - **Verify**: 展宽 Worker 节点呈现胶囊条形状（pill-shaped），文字不截断

**Checkpoint**: P1 MVP 完成。新任务按时间轴定位，Worker 展宽为胶囊条，旧任务降级为等宽布局。此时应进行手动验证确认核心功能。

---

## Phase 3: P2 增强 -- 时间刻度尺 (US5)

**Goal**: 在泳道上方显示自适应时间刻度尺，为时间轴布局提供明确时间参照。

**Independent Test**: 查看新任务的时间轴视图，确认泳道上方显示了带有时间标注的刻度尺；降级模式不显示刻度尺。

### 数据层

- [x] T018 [US5] 实现 `generateTimeTicks()` 函数
  - **File**: `octoagent/frontend/src/utils/roundSplitter.ts`
  - **Depends on**: T005
  - **Input**: `tMin: number`, `tMax: number`, `scale: number`, `padding: number`
  - **Output**: `TimeTick[]`
  - **Logic**:
    1. 计算时间跨度 `spanMs = tMax - tMin`
    2. 选择刻度间隔:
       - `spanMs <= 5000` -> `intervalMs = 1000`（1s）
       - `spanMs <= 30000` -> `intervalMs = 5000`（5s）
       - `spanMs <= 180000` -> `intervalMs = 30000`（30s）
       - `spanMs > 180000` -> `intervalMs = 60000`（1min）
    3. 从 `tMin` 向上取整到最近的 `intervalMs` 整数倍开始
    4. 循环生成刻度: `t = start; t <= tMax; t += intervalMs`
    5. 标签格式化: 相对于 `tMin` 的偏移量
       - `< 60s`: `+Ns`（如 `+0s`, `+5s`, `+30s`）
       - `>= 60s`: `+NmMs`（如 `+1m`, `+1m30s`, `+2m`）
    6. 位置: `leftPx = padding + (t - tMin) * scale`
  - **Covers**: FR-008
  - **Verify**: 不同时间跨度选择合适的间隔；标签格式正确

- [x] T019 [US5] 在 `computeTimelineLayout()` 中集成 `generateTimeTicks` 调用
  - **File**: `octoagent/frontend/src/utils/roundSplitter.ts`
  - **Depends on**: T008, T018
  - **Logic**: 在 `computeTimelineLayout` 正常路径（非降级）末尾调用 `generateTimeTicks(tMin, tMax, scale, PADDING)` 并赋值到 `TimelineLayout.timeTicks`
  - **Covers**: FR-008
  - **Verify**: 正常模式下 `layout.timeTicks` 非空；降级模式下为空数组

### 组件层

- [x] T020 [US5] 实现时间刻度尺渲染
  - **File**: `octoagent/frontend/src/components/TaskVisualization/RoundFlowCard.tsx`
  - **Depends on**: T012, T019
  - **Logic**:
    1. 在 `div.tv-lanes-scroll` 内部、泳道列表之前渲染 `div.tv-time-axis`
    2. 宽度与泳道轨道一致: `width: ${layout.totalWidthPx}px`
    3. 遍历 `layout.timeTicks`，渲染 `span.tv-time-tick`（`position: absolute; left: ${tick.leftPx}px`）
    4. 当 `layout.degraded === true` 时不渲染刻度尺
  - **Covers**: FR-008，spec US5 Acceptance 1-3
  - **Verify**: 刻度尺与泳道轨道宽度一致，同步滚动；降级模式无刻度尺

### 样式层

- [x] T021 [P] [US5] 新增时间刻度尺样式
  - **File**: `octoagent/frontend/src/components/TaskVisualization/task-visualization.css`
  - **Depends on**: T020
  - **Output**:
    - `.tv-time-axis { position: relative; height: 24px; border-bottom: 1px solid var(--cp-border); margin-left: calc(110px + var(--cp-space-3)); }`
    - `.tv-time-tick { position: absolute; top: 0; font-size: 10px; color: var(--cp-muted); transform: translateX(-50%); white-space: nowrap; }`
  - **Covers**: FR-008
  - **Verify**: 刻度标签在刻度线位置居中对齐；不超出容器

**Checkpoint**: 时间刻度尺就绪，时间轴布局有明确的时间参照。

---

## Phase 4: P2 增强 -- 跨泳道斜线连接 (US3)

**Goal**: 用 SVG 斜线可视化 Orchestrator 与 Worker 之间的 dispatch/return 调用关系。

**Independent Test**: 查看包含 Worker 的任务，确认有斜线连接；单泳道任务无斜线；斜线不阻挡节点点击。

### 数据层

- [x] T022 [US3] 实现 `buildCrossLaneLinks()` 函数
  - **File**: `octoagent/frontend/src/utils/roundSplitter.ts`
  - **Depends on**: T004, T008
  - **Input**: `lanes: AgentLane[]`, `nodeLayouts: Map<string, NodeLayout>`
  - **Output**: `CrossLaneLink[]`
  - **Logic**:
    1. 找到 Orchestrator 泳道（`lane.agent === "Orchestrator"`），记录其索引 `orchIdx`
    2. 遍历 Orchestrator 泳道的节点，找 `kind === 'worker'` 的节点
    3. 对每个 Worker 节点:
       a. 根据节点 `label` 或关联信息匹配对应的 Worker 泳道（通过 `lane.agent` 匹配）
       b. 如果找到匹配泳道，获取该泳道的首节点和末节点
       c. 生成 dispatch 连接: `{ fromLaneIndex: orchIdx, fromNodeId: workerNode.id, toLaneIndex: workerLaneIdx, toNodeId: firstNode.id, type: 'dispatch' }`
       d. 生成 return 连接: `{ fromLaneIndex: workerLaneIdx, fromNodeId: lastNode.id, toLaneIndex: orchIdx, toNodeId: workerNode.id, type: 'return' }`
    4. 未找到匹配泳道或单泳道场景 -> 返回空数组
  - **Covers**: FR-010
  - **Verify**: 多泳道任务生成 dispatch + return 对；单泳道返回空数组

- [x] T023 [US3] 在 `computeTimelineLayout()` 中集成 `buildCrossLaneLinks` 调用
  - **File**: `octoagent/frontend/src/utils/roundSplitter.ts`
  - **Depends on**: T008, T022
  - **Logic**: 在正常路径（非降级）末尾调用 `buildCrossLaneLinks(lanes, nodeLayouts)` 并赋值到 `TimelineLayout.crossLaneLinks`
  - **Covers**: FR-010
  - **Verify**: 正常模式下 `layout.crossLaneLinks` 可能非空；降级模式下为空数组

### 组件层

- [x] T024 [US3] 实现 SVG overlay 渲染跨泳道连接线
  - **File**: `octoagent/frontend/src/components/TaskVisualization/RoundFlowCard.tsx`
  - **Depends on**: T012, T023
  - **Logic**:
    1. 在 `div.tv-timeline-container` 内泳道列表之后渲染 `svg.tv-cross-lane-svg`
    2. SVG 尺寸: `width: ${layout.totalWidthPx}px`, `height` 根据泳道数量计算（`lanes.length * (52 + 4)`）
    3. `pointer-events: none` 确保不阻挡节点交互
    4. 遍历 `layout.crossLaneLinks`，渲染 `<line>` 元素:
       - X 坐标: 基于 `nodeLayouts` 中的 `leftPx` 和 `widthPx`
         - dispatch: x1 = fromNode.leftPx, x2 = toNode.leftPx + toNode.widthPx / 2
         - return: x1 = fromNode.leftPx + fromNode.widthPx / 2, x2 = toNode.leftPx + toNode.widthPx
       - Y 坐标: 基于泳道索引 * (laneHeight[52] + gap[4]) + laneHeight/2
    5. dispatch 线 class: `tv-cross-line--dispatch`
    6. return 线 class: `tv-cross-line--return`
    7. 当 `layout.crossLaneLinks` 为空时不渲染 SVG 元素
  - **Covers**: FR-010, FR-011, FR-012
  - **Verify**: 斜线正确连接 Orchestrator 和 Worker；dispatch/return 颜色不同；节点仍可点击

### 样式层

- [x] T025 [P] [US3] 新增 SVG 跨泳道斜线样式
  - **File**: `octoagent/frontend/src/components/TaskVisualization/task-visualization.css`
  - **Depends on**: T024
  - **Output**:
    - `.tv-cross-lane-svg { position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 10; }`
    - `.tv-cross-line--dispatch { stroke: var(--cp-primary); stroke-width: 1.5; stroke-dasharray: 6 3; opacity: 0.7; }`
    - `.tv-cross-line--return { stroke: var(--cp-success); stroke-width: 1.5; stroke-dasharray: 6 3; opacity: 0.7; }`
  - **Covers**: FR-011, FR-012
  - **Verify**: SVG 层不响应鼠标事件（`pointer-events: none`）；dispatch 线和 return 线视觉可区分

**Checkpoint**: 跨泳道连接线就绪，用户可以看到 Orchestrator 与 Worker 的调用/返回关系。

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: 边缘场景处理、代码清理、验证

- [ ] T026 Edge Case: 处理泳道折叠与 SVG 斜线的交互
  - **File**: `octoagent/frontend/src/components/TaskVisualization/RoundFlowCard.tsx`
  - **Depends on**: T024
  - **Logic**: 当泳道被折叠时，跳过已折叠泳道的斜线渲染（不渲染指向折叠泳道的连接线）
  - **Verify**: 折叠泳道后不出现指向空位置的悬空斜线

- [ ] T027 Edge Case: SVG Y 坐标与实际 DOM 高度对齐
  - **File**: `octoagent/frontend/src/components/TaskVisualization/RoundFlowCard.tsx`
  - **Depends on**: T024
  - **Logic**: 如果泳道实际 DOM 高度与预设常量（52px + 4px gap）不一致，考虑使用 `useRef` + `useLayoutEffect` 读取实际泳道高度来计算 SVG Y 坐标，或者确保 CSS 强制泳道高度一致
  - **Verify**: 斜线端点精确指向节点中心，不出现错位

- [ ] T028 [P] TypeScript 编译检查
  - **Files**: 全部修改文件
  - **Command**: `cd octoagent/frontend && npx tsc --noEmit`
  - **Covers**: SC-005
  - **Verify**: 编译零错误

- [ ] T029 [P] 手动验证 -- 时间轴模式
  - **Prerequisites**: 启动 dev server，创建或查看一个包含 Orchestrator + Worker 的新任务
  - **Verify**:
    - 节点按时间横轴对齐（SC-001）
    - Worker 展宽为胶囊条，宽度与执行时长成比例（SC-002）
    - 水平滚动流畅，泳道同步对齐（SC-004）
    - 时间刻度尺显示正确间隔（如有 Phase 3）
    - 跨泳道斜线正确连接（如有 Phase 4）

- [ ] T030 [P] 手动验证 -- 降级模式
  - **Prerequisites**: 查看一个旧任务（无有效时间戳）
  - **Verify**:
    - 渲染结果与改动前完全一致（SC-003）
    - 节点点击弹框正常（FR-013）
    - 泳道折叠/展开正常（FR-014）
    - 无刻度尺显示

- [ ] T031 [P] 手动验证 -- 边缘场景
  - **Verify**:
    - 所有节点时间戳完全相同 -> 降级为等宽布局（不崩溃）
    - 极长任务（> 10 分钟）-> 总宽度不超过 8000px
    - 单泳道任务 -> 无斜线，时间轴正常
    - 节点时间间隔极短 -> 防重叠修正生效，节点可分别点击
    - 浏览器窗口窄于内容 -> 滚动条正确，无泳道错位

---

## FR Coverage Matrix

| FR | Description | Task(s) |
|----|-------------|---------|
| FR-001 | 基于时间戳计算节点水平位置 | T007, T008, T010 |
| FR-002 | Worker 节点展宽宽度计算 | T008, T013 |
| FR-003 | 防重叠修正 | T009 |
| FR-004 | 总宽度上限约束 | T008 |
| FR-005 | 有效时间戳 < 2 降级 | T006, T007, T011 |
| FR-006 | 时间范围 = 0 降级 | T006, T007, T011 |
| FR-007 | 统一水平滚动容器 | T012, T015 |
| FR-008 | 时间刻度尺（P2） | T018, T019, T020, T021 |
| FR-009 | Worker 胶囊条渲染 | T013, T017 |
| FR-010 | 跨泳道连接线描述（P2） | T022, T023, T024 |
| FR-011 | SVG overlay + pointer-events:none（P2） | T024, T025 |
| FR-012 | dispatch/return 视觉区分（P2） | T024, T025 |
| FR-013 | 节点点击弹框兼容 | T011, T013, T014 |
| FR-014 | 泳道折叠/展开兼容 | T011, T014 |

**Coverage**: 14/14 FR (100%)

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (接口定义) -- 无前置，立即开始
    |
    v
Phase 2 (P1 核心) -- 依赖 Phase 1 完成
    |
    +--> Phase 3 (P2 刻度尺) -- 依赖 Phase 2 完成
    |
    +--> Phase 4 (P2 斜线) -- 依赖 Phase 2 完成
    |
    v
Phase 5 (Polish) -- 依赖 Phase 2 完成，Phase 3/4 可选
```

### User Story Dependencies

- **US1 (时间轴布局)**: Phase 1 + Phase 2 数据层 + 组件层 -- 核心基础
- **US2 (Worker 展宽)**: 依赖 US1 的布局计算 -- 在 Phase 2 中一并实现
- **US4 (降级)**: 依赖 US1 的 `computeTimelineLayout` -- 在 Phase 2 中一并实现
- **US5 (刻度尺)**: 依赖 US1 的缩放因子和时间范围 -- Phase 3 独立
- **US3 (斜线)**: 依赖 US1 的 `nodeLayouts` -- Phase 4 独立

### Parallel Opportunities

- **Phase 1 内部**: T001-T005 全部可并行（不同接口定义，无交叉）
- **Phase 2 内部**: T015/T016/T017 样式任务可与组件层并行
- **Phase 3 与 Phase 4**: 可并行实施（各自独立的数据层+组件层+样式层）
- **Phase 5 内部**: T028-T031 验证任务可并行

### Recommended Strategy: MVP First

1. **Phase 1**: T001-T005（接口定义，~15 min）
2. **Phase 2**: T006-T017（P1 核心，~3 hr）
3. **STOP & VALIDATE**: 手动验证 T029/T030 确认 P1 功能
4. **Phase 3**: T018-T021（刻度尺，~1 hr）-- 可选
5. **Phase 4**: T022-T025（斜线，~1.5 hr）-- 可选
6. **Phase 5**: T026-T031（边缘场景 + 验证，~1 hr）
