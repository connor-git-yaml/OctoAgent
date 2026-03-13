# 产研汇总：Frontend Workbench Architecture Renewal

**特性分支**: `codex/047-frontend-workbench-architecture`  
**汇总日期**: 2026-03-13  
**输入**: [product-research.md](product-research.md) + [tech-research.md](tech-research.md) + 本轮公开在线调研  
**执行者**: 主编排器

## 1. 产品×技术交叉分析矩阵

| MVP 功能 | 产品优先级 | 技术可行性 | 实现复杂度 | 综合评分 | 建议 |
|---------|-----------|-----------|-----------|---------|------|
| 统一 daily workbench 与 advanced diagnostics 的职责边界 | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| 引入共享 query/action 层并替换双轨 snapshot orchestration | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| 拆分 `AgentCenter / SettingsCenter / ControlPlane` 巨型页面 | P1 | 高 | 高 | ⭐⭐⭐ | 纳入 MVP |
| 建立 design token + UI primitive + page pattern | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| 后端 canonical contract 到前端 type 的同步链 | P1 | 中 | 中 | ⭐⭐⭐ | 纳入 MVP |
| 前端 E2E 黄金路径体系 | P2 | 中 | 中 | ⭐⭐ | 作为 MVP 尾部纳入 |
| 主题系统/炫技动画/全面视觉重做 | P3 | 高 | 中 | ⭐ | 推迟 |

## 2. 可行性评估

### 技术可行性

整体可行性高。当前前端的最大问题是结构纪律，而不是基础栈错误：

- React/Vite/Router 已满足长期演化需要
- 后端已有稳定的 canonical resource/action 模型，可作为前端 contract 事实源
- 新工作台已经存在 `WorkbenchLayout + useWorkbenchSnapshot` 的正确方向
- 当前复杂度主要集中在少数单体文件，可以通过分层重构逐步消化

### 资源评估

- **预估工作量**: 1 个中大型 Feature，建议拆 2-3 个交付阶段推进
- **关键技能需求**:
  - React 架构与组件拆分
  - TypeScript 契约组织
  - Query/cache 设计
  - UX 信息架构重整
- **外部依赖**:
  - 可选引入 TanStack Query
  - 可选引入 codegen/schema export 脚本
  - 不依赖新后端服务

### 约束与限制

- 不能破坏现有 control-plane canonical resource/action 事实源
- 不能用一次性重写中断现有工作台迭代
- 需要兼容近期已落地的 044/046 设置与 provider 入口
- 当前 `.specify/project-context.yaml` / `.md` 软链失效，项目级在线调研策略不可读

## 3. 风险评估

### 综合风险矩阵

| # | 风险 | 来源 | 概率 | 影响 | 缓解策略 | 状态 |
|---|------|------|------|------|---------|------|
| 1 | 双轨数据层在迁移期继续并存，导致行为漂移 | 技术 | 中 | 高 | 先抽 shared query/action registry，再迁移 `Advanced` | 待监控 |
| 2 | 页面拆分但未建立域边界，复杂度只是搬家 | 技术 | 高 | 高 | 先冻结目录和模块职责，再拆文件 | 待监控 |
| 3 | UX 重做流于视觉改版，未解决 debug 泄漏与 IA 混乱 | 产品 | 中 | 高 | 先定义 surface 层级与内容策略，再做视觉实现 | 待监控 |
| 4 | 手写类型继续增长，后端契约改动后前端回归频繁 | 技术 | 高 | 中 | 建立 contract export/codegen 或集中 schema registry | 待监控 |
| 5 | 一次性大范围重构影响当前 Feature 迭代速度 | 产品/技术 | 中 | 中 | 采用 domain-by-domain 增量迁移 | 待监控 |

### 风险分布

- **产品风险**: 2 项（高:1 中:1 低:0）
- **技术风险**: 3 项（高:2 中:1 低:0）

## 4. 最终推荐方案

### 推荐架构

以“保留现有栈、重整工作台骨架”为原则，执行以下路线：

1. 保留 React + Vite + React Router
2. 建立统一的 frontend query/action 层，所有 surface 共用
3. 将 `Home / Chat / Work / Agents / Settings / Memory / Advanced` 全部收敛为域模块
4. 将 `AdvancedControlPlane` 改为 shared data layer 的高级视图，而不是独立控制台
5. 把样式拆为 token / primitive / shell / domain 层，形成正式设计系统
6. 将后端 canonical contract 同步到前端类型，减少手写镜像

### 推荐技术栈

| 类别 | 选择 | 理由 |
|------|------|------|
| 前端框架 | React + Vite + React Router | 当前基线正确，迁移成本最低 |
| Server State | TanStack Query | 最适合统一 snapshot/resource/action invalidation |
| Local UI State | `useState` / `useReducer` / 少量域内 hook | 保持简单，避免多状态库并存 |
| 契约同步 | 从 Pydantic / canonical models 导出前端 type | 利用现有后端事实源，降低漂移 |
| UI 基础设施 | 自建 tokens + primitives + patterns | 比直接换 UI 框架更贴合当前工作台 |
| 测试 | Vitest + RTL + 少量 E2E 黄金路径 | 兼顾开发效率与回归稳定性 |

### 推荐实施路径

1. **Phase 1 (MVP 架构基线)**:
   - 冻结 IA
   - 引入共享 query/action 层
   - 抽离 design tokens / primitives
   - 将 `Advanced` 并回 shared data layer
2. **Phase 2 (页面域化迁移)**:
   - 重构 `Agents / Settings / Memory`
   - 切分大页面与 shared CSS
   - 建立 contract sync 链
3. **Phase 3 (质量巩固)**:
   - 加入黄金路径 E2E
   - 加入 LOC / complexity guard
   - 做性能与可访问性收口

## 5. MVP 范围界定

### 最终 MVP 范围

**纳入**:

- 统一工作台数据层
- 重新定义日常面与高级诊断面的职责
- 拆分三大巨型页面
- 建立设计 token / primitive / pattern
- 建立前端契约同步机制

**排除（明确不在 MVP）**:

- 彻底换框架
- 全站重新视觉包装
- 多主题和品牌皮肤系统
- 全量数据可视化重做

### MVP 成功标准

- 用户主路径更清晰，不再把 debug/内部术语暴露在第一层
- 前端主要页面和 CSS 文件不再持续单体膨胀
- 所有 surface 共享同一套 snapshot/resource/action 数据层
- 新增 Feature 不需要继续在单页里堆积逻辑

## 6. 结论

综合产品和技术视角，047 应当被定义为一个“前端长期主义整治 Feature”，目标不是追求一次性大改，而是为后续 6-12 个月的工作台演化建立稳定底座。

### 置信度

| 维度 | 置信度 | 说明 |
|------|--------|------|
| 产品方向 | 高 | 主路径/高级路径分层问题已十分明确 |
| 技术方案 | 高 | 保留现有栈、重整数据层与目录边界的路径风险最低 |
| MVP 范围 | 中 | 页面拆分与 contract sync 的工作量仍需实施中校准 |

### 后续行动建议

- 以独立 Feature 进入 spec / plan / tasks
- 在实施前把 `Advanced` 与 `Workbench` 共用数据层作为第一优先级
- 为页面文件与共享 CSS 设置强制体量约束
