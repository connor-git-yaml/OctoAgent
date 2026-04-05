# Data Model: 065-task-timeline-swimlane-layout

**Date**: 2026-03-19

---

## 新增类型（在 `roundSplitter.ts` 中定义）

### NodeLayout

单个节点在时间轴上的布局信息。

```typescript
export interface NodeLayout {
  /** 节点水平起始位置（像素） */
  leftPx: number;
  /** 节点宽度（普通节点 = 48，展宽 Worker 节点 > 48） */
  widthPx: number;
  /** 所在泳道索引（0-based，对应 AgentLane[] 下标） */
  laneIndex: number;
}
```

### CrossLaneLink

跨泳道连接线描述。

```typescript
export interface CrossLaneLink {
  /** 起始泳道索引 */
  fromLaneIndex: number;
  /** 起始节点 ID */
  fromNodeId: string;
  /** 目标泳道索引 */
  toLaneIndex: number;
  /** 目标节点 ID */
  toNodeId: string;
  /** 连接类型：dispatch（Orchestrator -> Worker）或 return（Worker -> Orchestrator） */
  type: "dispatch" | "return";
}
```

### TimeTick

时间刻度标记。

```typescript
export interface TimeTick {
  /** 刻度标签文本，如 "+0s", "+5s", "+1m30s" */
  label: string;
  /** 刻度水平位置（像素） */
  leftPx: number;
}
```

### TimelineLayout

时间轴布局计算的完整输出。

```typescript
export interface TimelineLayout {
  /** 时间轴总宽度（像素），所有泳道轨道的 width 都设为此值 */
  totalWidthPx: number;
  /** 节点布局映射：nodeId -> NodeLayout */
  nodeLayouts: Map<string, NodeLayout>;
  /** 跨泳道连接线描述列表 */
  crossLaneLinks: CrossLaneLink[];
  /** 时间刻度标记列表 */
  timeTicks: TimeTick[];
  /** 是否降级为等宽布局（有效时间戳 < 2 或时间范围 = 0） */
  degraded: boolean;
}
```

---

## 已有类型（不修改，仅引用）

### FlowNode（roundSplitter.ts）

```typescript
// 关键字段用于布局计算：
interface FlowNode {
  id: string;           // 唯一标识，作为 nodeLayouts Map 的 key
  kind: FlowNodeKind;   // "worker" 类型的节点需要展宽
  ts: string;           // ISO 时间戳，用于计算 leftPx
  durationMs: number;   // 耗时，Worker 节点用于计算 widthPx
  agent: string;        // 所属 Agent 名称，用于匹配泳道
}
```

### AgentLane（roundSplitter.ts）

```typescript
// 关键字段用于布局计算：
interface AgentLane {
  agent: string;        // Agent 名称，用于泳道标识
  nodes: FlowNode[];    // 泳道内节点列表
}
```

### Round（roundSplitter.ts）

```typescript
// 关键字段用于布局计算：
interface Round {
  startTime: string;    // 轮次起始时间
  endTime?: string;     // 轮次结束时间
  nodes: FlowNode[];    // 所有节点（未分泳道前）
}
```

---

## 关系图

```
Round
  ├── startTime, endTime  ──> computeTimelineLayout() 输入
  └── nodes ──> groupByAgent() ──> AgentLane[]
                                      │
                                      ├──> computeTimelineLayout()
                                      │        ├── nodeLayouts: Map<nodeId, NodeLayout>
                                      │        ├── crossLaneLinks: CrossLaneLink[]
                                      │        ├── timeTicks: TimeTick[]
                                      │        ├── totalWidthPx: number
                                      │        └── degraded: boolean
                                      │
                                      └──> RoundFlowCard 渲染
                                               ├── degraded=true  → flex 等宽布局
                                               └── degraded=false → 时间轴 absolute 布局
```
