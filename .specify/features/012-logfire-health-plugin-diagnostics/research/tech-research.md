# 技术调研报告: Feature 012 Logfire + Health/Plugin Diagnostics

**特性分支**: `codex/feat-012-logfire-health-plugin-diagnostics`
**调研日期**: 2026-03-03
**调研模式**: 在线+代码库
**产品调研基础**: [product-research.md](product-research.md)

## 1. 调研目标

**核心问题**:
- 如何在不重构主链路的前提下增强 Logfire 可观测能力？
- 如何在 ToolBroker 中实现“注册失败可诊断、主流程不中断”？
- 如何扩展 `/ready` 使其覆盖 M1.5 子系统状态？

**产品 MVP 范围（来自产品调研）**:
- Logfire 环境开关 + 可降级
- ToolBroker diagnostics
- 健康检查子系统扩展

## 2. 架构方案对比

### 方案对比表

| 维度 | 方案 A: 最小增量（推荐） | 方案 B: 新 observability 包统一重构 | 方案 C: 仅补文档/不改代码 |
|------|-------------------------|--------------------------------------|--------------------------|
| 概述 | 在 gateway/tooling 局部增强 | 抽象全新观测层再迁移现有代码 | 延后实现 |
| 性能 | 低额外开销 | 中等，初期重构开销高 | 无 |
| 可维护性 | 中高，改动集中 | 高（长期），但短期复杂 | 低（技术债累积） |
| 学习曲线 | 低 | 高 | 低 |
| 社区支持 | 直接复用 Logfire/OTel 实践 | 需自行沉淀 | 无 |
| 与现有项目兼容性 | 高 | 中 | 高 |

### 推荐方案

**推荐**: 方案 A（最小增量）

**理由**:
1. 对齐 M1.5 “先可用、再演进”节奏。
2. 不引入跨包大重构，便于快速验证。
3. 可直接映射到 Feature 012 任务与验收标准。

## 3. 依赖库评估

### 评估矩阵

| 库名 | 用途 | 版本 | 许可证 | 最近更新 | 评级 |
|------|------|------|--------|---------|------|
| logfire | tracing/observability | 4.x（已在锁文件） | MIT | 活跃 | ⭐⭐⭐ |
| structlog | 结构化日志 | 25.x（已使用） | MIT | 活跃 | ⭐⭐⭐ |
| FastAPI | Web 框架与路由 | 0.115+ | MIT | 活跃 | ⭐⭐⭐ |

### 推荐依赖集

**核心依赖**:
- `logfire`: 负责 trace/span 上报与 FastAPI instrumentation。
- `structlog`: 负责结构化日志与上下文字段绑定。

**可选依赖**:
- 不新增新三方依赖，优先复用现有栈。

### 与现有项目的兼容性

| 现有依赖 | 兼容性 | 说明 |
|---------|--------|------|
| gateway `logfire>=3.0` | ✅ 兼容 | 已存在，主要补使用方式 |
| tooling package | ✅ 兼容 | 可局部新增 diagnostics 数据结构 |
| provider health_check | ✅ 兼容 | `/ready` 可直接复用 |

## 4. 设计模式推荐

1. **Fail-open with diagnostics**: 注册流程允许失败但记录诊断，避免系统级中断。  
2. **Health Aggregator**: 聚合子系统状态而非硬耦合调用，降低依赖脆弱性。  
3. **Progressive instrumentation**: 先关键链路埋点，再按场景扩展。

## 5. 技术风险清单

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---------|------|------|---------|
| 1 | middleware 绑定顺序导致 trace 字段丢失 | 中 | 中 | 增加 trace 字段断言测试 |
| 2 | 健康检查对子系统属性访问抛异常 | 中 | 中 | 统一 `getattr` + 容错状态 |
| 3 | diagnostics 结构不稳定导致调用方耦合 | 低 | 中 | 使用明确数据模型 + 单测锁定 |

## 6. 产品-技术对齐度

### 覆盖评估

| MVP 功能 | 技术方案覆盖 | 说明 |
|---------|-------------|------|
| Logfire 开关与降级 | ✅ 完全覆盖 | 在 gateway logging 初始化层实现 |
| ToolBroker 诊断 | ✅ 完全覆盖 | 新增 `try_register` 与 diagnostics 列表 |
| 健康检查子系统扩展 | ✅ 完全覆盖 | `/ready` 新增 subsystem checks |

### 扩展性评估
方案 A 可以无缝扩展到 M1.5 后续的 orchestrator/worker/checkpoint/watchdog 真组件检查。

### Constitution 约束检查

| 约束 | 兼容性 | 说明 |
|------|--------|------|
| C6 Degrade Gracefully | ✅ 兼容 | 注册失败不拖垮主流程 |
| C8 Observability is Feature | ✅ 兼容 | 增加结构化检查与 trace 对齐 |
| C2 Everything is Event | ⚠️ 部分覆盖 | 本次先补诊断与健康，事件化可在后续增强 |

## 7. 结论与建议

### 总结
需求 012 可在当前代码基线以低风险落地，重点在“观测增强 + 诊断可见 + 健康聚合”。

### 对产研汇总的建议
- 明确 Must 与 Should 边界：MVP 先交付 diagnostics 与 health aggregation。
- 将 trace 一致性校验纳入验证闭环，避免“有埋点但不可关联”。
