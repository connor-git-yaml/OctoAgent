# Technical Research: 065-task-timeline-swimlane-layout

**Date**: 2026-03-19

---

## Decision 1: 时间映射策略

**Decision**: 线性时间映射 + 防重叠后处理

**Rationale**: 线性映射（1 秒 = 12px）直觉最清晰，用户可直接通过水平距离判断时间间隔。通过 `maxTotalPx = 8000` 约束极端场景，通过 `minGap = 8px` 防重叠后处理确保密集事件可分辨。

**Alternatives Considered**:
- 对数映射: 能压缩极端时长差异，但破坏线性直觉（"看起来一样宽但时长差 10 倍"），用户认知负担高。拒绝。
- 分段线性映射（超阈值后缩放因子变小）: 增加实现复杂度，且非连续缩放会导致刻度尺不均匀。拒绝。

---

## Decision 2: 跨泳道连接线实现方式

**Decision**: SVG overlay 层

**Rationale**: 典型场景仅 2-6 条跨泳道连接线，SVG 性能无压力。SVG 原生支持斜线、虚线、箭头等样式，CSS 可控。通过 `pointer-events: none` 不阻挡底层交互。

**Alternatives Considered**:
- Canvas: 需要手动处理 DPI 缩放、重绘逻辑，且无法使用 CSS 变量控制样式。过重。拒绝。
- DOM 元素 + CSS transform 旋转: 难以精确控制斜线端点，且旋转后 border 模糊。拒绝。
- CSS `::after` 伪元素: 无法跨多个 DOM 元素画连接线。拒绝。

---

## Decision 3: 降级策略

**Decision**: 双路径渲染 -- 降级时保留完整原有 flex JSX

**Rationale**: 规范要求降级布局"与改动前完全一致"（SC-003）。最安全的实现方式是在 `layout.degraded === true` 时走完全不修改的原有渲染路径，而不是用 absolute 定位模拟 flex 等宽效果。

**Alternatives Considered**:
- 统一用 absolute 定位 + 等间距计算模拟 flex: 存在微妙的像素差异风险（padding、connector 宽度等），难以保证"完全一致"。拒绝。
- 始终使用 flex + 额外 margin 模拟时间对齐: flex 布局难以精确控制像素级定位，且 Worker 展宽与 flex 布局冲突。拒绝。

---

## Decision 4: 布局计算代码位置

**Decision**: 在 `roundSplitter.ts` 中新增，不新建文件

**Rationale**: 布局计算强依赖 `FlowNode`、`AgentLane` 等类型和 `groupByAgent` 的输出结构，放在同一文件减少导入链。新增 ~150 行在可管理范围内。若后续膨胀超过 300 行可考虑拆分。

**Alternatives Considered**:
- 新建 `timelineLayout.ts`: 需要导出/导入大量类型，增加模块间耦合的管理开销。当前规模不需要。保留为后续拆分选项。

---

## Decision 5: 缩放因子与约束参数

**Decision**: `PX_PER_SECOND = 12`, `maxTotalPx = 8000`, `minNodeWidth = 48`, `minGap = 8`, `padding = 24`

**Rationale**:
- `12px/s`: 5 秒任务 = 60px + padding，足够紧凑；30 秒任务 = 360px，适合屏幕宽度
- `8000px`: 约 11 分钟的时间跨度上限，超过此值缩放因子自动压缩
- `48px`: 与当前节点圆 (32px) + 文字区域的最小视觉宽度匹配
- `8px`: 两个 48px 节点之间的最小间隔，确保可分别点击
- `24px`: 左右 padding 防止首尾节点贴边

---

## Decision 6: 混合时间戳处理（部分节点缺失）

**Decision**: 有效时间戳 >= 2 时启用时间轴模式，缺失时间戳的节点在前后有效节点间线性插值

**Rationale**: 实际数据中可能有少量事件缺失时间戳（如手动插入的事件）。完全降级会丧失大部分时间信息，线性插值是最不意外的默认行为。

**Alternatives Considered**:
- 严格模式（任何缺失即降级）: 过于保守，一个缺失就丧失所有时间信息。拒绝。
- 缺失节点固定宽度插入: 会导致后续节点位置偏移，破坏已知时间戳的对齐精度。拒绝。
