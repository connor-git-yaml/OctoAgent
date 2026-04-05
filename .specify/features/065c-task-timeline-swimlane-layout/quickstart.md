# Quickstart: 065-task-timeline-swimlane-layout

**日期**: 2026-03-19

---

## 概述

本功能将 Task 可视化泳道从等宽布局改为时间轴对齐布局。涉及 3 个文件的修改，无新增依赖。

---

## 快速开始

### 1. 开发环境

```bash
cd octoagent/frontend
npm run dev    # Vite dev server
```

### 2. 修改文件清单

| 文件 | 改动类型 | 估计行数 |
|------|----------|----------|
| `src/utils/roundSplitter.ts` | 新增接口 + 函数 | +150 行 |
| `src/components/TaskVisualization/RoundFlowCard.tsx` | 重构渲染逻辑 | +80/-20 行 |
| `src/components/TaskVisualization/task-visualization.css` | 新增样式类 | +80 行 |

### 3. 实现顺序

```
Step 1: roundSplitter.ts -- 新增接口和计算函数
    ↓
Step 2: RoundFlowCard.tsx -- 调用计算 + 双路径渲染
    ↓
Step 3: task-visualization.css -- 新增时间轴样式
    ↓
Step 4: 编译验证 (npx tsc --noEmit)
    ↓
Step 5: 功能验证（新任务 + 旧任务 + 边界场景）
```

### 4. 验证命令

```bash
# TypeScript 编译检查
cd octoagent/frontend && npx tsc --noEmit

# 本地预览
npm run dev
# 访问任意 Task 详情页查看泳道可视化
```

### 5. 关键验证场景

- **新任务（有时间戳）**: 节点按时间轴对齐，Worker 展宽为胶囊条
- **旧任务（无时间戳）**: 降级为等宽布局，与改动前完全一致
- **所有时间戳相同**: 降级为等宽布局（不崩溃）
- **水平滚动**: 所有泳道同步滚动
- **节点点击弹框**: 两种布局模式下均正常

---

## 关键接口速查

```typescript
// 主入口
computeTimelineLayout(lanes, startTime, endTime) -> TimelineLayout

// TimelineLayout
{
  totalWidthPx: number,     // 泳道轨道宽度
  nodeLayouts: Map<id, {leftPx, widthPx, laneIndex}>,
  crossLaneLinks: CrossLaneLink[],
  timeTicks: TimeTick[],
  degraded: boolean,        // true = 走旧 flex 布局
}
```
