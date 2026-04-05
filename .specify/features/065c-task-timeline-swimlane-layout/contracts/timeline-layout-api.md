# API Contract: Timeline Layout 计算

**Date**: 2026-03-19
**Module**: `octoagent/frontend/src/utils/roundSplitter.ts`

---

## 函数签名

### computeTimelineLayout

主入口函数，计算时间轴布局或返回降级标志。

```typescript
export function computeTimelineLayout(
  lanes: AgentLane[],
  startTime: string,
  endTime?: string,
): TimelineLayout;
```

**参数**:
- `lanes`: 由 `groupByAgent()` 输出的泳道列表，每个泳道包含按顺序排列的 FlowNode 列表
- `startTime`: Round 的起始时间（ISO 8601 字符串）
- `endTime`: Round 的结束时间（可选，ISO 8601 字符串）

**返回**: `TimelineLayout` 对象

**行为契约**:
1. 有效时间戳 < 2 -> `degraded: true`，其余字段为空
2. 时间范围 = 0（所有时间戳相同） -> `degraded: true`
3. 正常情况 -> 计算所有节点的 `leftPx`/`widthPx`，生成跨泳道连接和时间刻度

**常量**:
- `PX_PER_SECOND = 12`
- `MAX_TOTAL_PX = 8000`
- `MIN_NODE_WIDTH = 48`
- `MIN_GAP = 8`
- `PADDING = 24`

---

### buildDegradedLayout

降级布局构建器。

```typescript
function buildDegradedLayout(lanes: AgentLane[]): TimelineLayout;
```

**返回**: `{ degraded: true, totalWidthPx: 0, nodeLayouts: new Map(), crossLaneLinks: [], timeTicks: [] }`

---

### buildCrossLaneLinks

跨泳道连接线构建器。

```typescript
function buildCrossLaneLinks(
  lanes: AgentLane[],
  nodeLayouts: Map<string, NodeLayout>,
): CrossLaneLink[];
```

**行为契约**:
1. 遍历 Orchestrator 泳道中 `kind === 'worker'` 的节点
2. 根据节点 label 或关联的 agent 名称找到对应的 Worker 泳道
3. 如果找到匹配泳道，生成 dispatch（Orchestrator -> Worker 首节点）和 return（Worker 末节点 -> Orchestrator）
4. 未找到匹配或单泳道场景 -> 返回空数组

---

### generateTimeTicks

时间刻度标记生成器。

```typescript
function generateTimeTicks(
  tMin: number,
  tMax: number,
  scale: number,
  padding: number,
): TimeTick[];
```

**参数**:
- `tMin`: 最小时间戳（毫秒）
- `tMax`: 最大时间戳（毫秒）
- `scale`: 缩放因子（px/ms）
- `padding`: 左侧 padding（像素）

**刻度间隔选择**:
| 时间跨度 | 间隔 |
|----------|------|
| <= 5s | 1s |
| <= 30s | 5s |
| <= 3min | 30s |
| > 3min | 1min |

**标签格式**: `+0s`, `+5s`, `+30s`, `+1m`, `+1m30s`, `+2m`

---

## 组件调用约定

### RoundFlowCard 中的使用方式

```typescript
// 在 RoundFlowCard 组件内
const lanes = useMemo(() => groupByAgent(round.nodes), [round.nodes]);
const layout = useMemo(
  () => computeTimelineLayout(lanes, round.startTime, round.endTime),
  [lanes, round.startTime, round.endTime],
);

// 渲染分支
if (layout.degraded) {
  // 走原有 flex 布局路径（不做任何修改）
} else {
  // 走时间轴布局路径
  // - 节点: style={{ position: 'absolute', left: layout.nodeLayouts.get(node.id)?.leftPx }}
  // - Worker 展宽: widthPx > 48 时追加 .tv-flow-node--span class
  // - 泳道轨道: width = layout.totalWidthPx
  // - SVG overlay: 遍历 layout.crossLaneLinks 渲染 <line>
  // - 刻度尺: 遍历 layout.timeTicks 渲染 <span>
}
```
