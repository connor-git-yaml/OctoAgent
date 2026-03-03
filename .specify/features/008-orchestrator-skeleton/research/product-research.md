# 产品调研报告: Feature 008 Orchestrator Skeleton（单 Worker）

**特性分支**: `codex/feat-008-orchestrator-skeleton`
**调研日期**: 2026-03-02
**调研模式**: 离线（代码库 + 参考实现）

## 1. 需求概述

**需求描述**: 完成 Feature 008：实现最小 Orchestrator 控制平面，支持 `Task -> Orchestrator -> Worker` 单 Worker 派发、可审计事件链、失败分类和高风险门禁。

**核心功能点**:
- 冻结控制平面契约：`OrchestratorRequest`、`DispatchEnvelope`、`WorkerResult`
- 单 Worker 路由与派发主循环
- 事件可观测：`ORCH_DECISION`、`WORKER_DISPATCHED`、`WORKER_RETURNED`
- 高风险 gate（仅控制平面入口，不重写全量策略引擎）

**目标用户**:
- 直接用户：Owner（Connor）作为单人运维和开发者
- 间接用户：后续 Feature 009/010/013 的开发者与测试者

## 2. 产品现状与机会

### 现状
- 当前 M1 主链路是 `TaskRunner -> TaskService` 直接处理 LLM。
- 缺少独立的 Orchestrator 控制层，难以承载 M1.5 的 Worker Runtime、Checkpoint/Resume、Watchdog。
- 审批与策略能力已在 Feature 006 成型，但没有嵌入到“任务派发”这一层。

### 机会
- 通过 Feature 008 建立“控制平面最小骨架”，把后续 Feature 009/010 的变更集中在 Worker/恢复层，降低返工。
- 通过统一派发信封和事件，提前建立可追溯性，符合 Constitution C2/C8。
- 通过跳数与版本字段，提前规避多 Worker 时代的协议债务。

## 3. 参考产品对比（面向架构能力）

| 维度 | Agent Zero | AgentStudio | Pydantic AI | 本 Feature 008 目标 |
|------|-----------|------------|-------------|---------------------|
| 控制平面入口 | 有委派工具入口 | 有统一执行器工厂 | 提供 delegation/handoff 模式 | 提供 Orchestrator 服务入口 |
| 派发协议 | 偏运行时约定 | 执行器配置驱动 | 模式指导为主 | 强类型 `DispatchEnvelope` |
| 可观测事件 | 较完整日志 | 任务执行器日志较完整 | 需应用侧补齐 | 强制 3 类控制平面事件 |
| 安全门禁 | 有较多运行时控制 | 有 guard 体系 | 框架层中立 | 高风险 gate 最小接入 |
| 扩展到多 Worker | 可扩展 | 可扩展 | 可扩展 | 通过字段预留能力 |

## 4. 用户场景验证

### 核心场景
1. **标准低风险消息**：用户发消息，系统应经 Orchestrator 派发到默认 Worker 并回传结果。
2. **高风险请求**：任务风险级别为 HIGH 时，系统应先做 gate 决策，再决定是否派发。
3. **异常路径**：Worker 返回失败时，应明确 `retryable` 分类并写入事件链。

### 核心假设
| 假设 | 验证结果 | 证据 |
|------|---------|------|
| 单 Worker 也需要正式 Dispatch 协议 | ✅ 已验证 | `docs/m1.5-feature-split.md` 明确要求 `contract_version/hop_count/max_hops` |
| 当前主链路可在不重写大部分代码的情况下接入 Orchestrator | ✅ 已验证 | `apps/gateway/services/task_runner.py` 可作为调度入口替换点 |
| 事件体系可承载控制平面新增事件 | ✅ 已验证 | 现有 `EventStore` + `EventType` 已可扩展 |

## 5. MVP 范围建议

### Must-have（MVP）
- 契约模型与字段冻结（含版本、路由理由、能力、跳数）
- 单 Worker 路由与派发循环
- 三类控制平面事件落盘
- 失败分类（可重试/不可重试）
- 高风险 gate 最小接入
- 单元 + 集成测试闭环

### Nice-to-have（后续）
- 多 Worker 能力匹配与负载均衡
- 策略驱动路由（非规则优先）
- 控制平面重试策略和退避算法

### Future（Feature 009+）
- Worker Free Loop Runtime
- Docker 执行与 profile
- Checkpoint/Resume 原子恢复

## 6. 结论与建议

### 总结
Feature 008 的产品价值不是“新增用户可见功能”，而是“冻结控制平面边界并降低后续架构返工风险”。MVP 应以控制平面契约、事件可观测和高风险 gate 为核心，不扩展到多 Worker 调度算法。

### 对技术调研的建议
- 明确 `DispatchEnvelope` 字段和默认值，确保对 Feature 009/010 前向兼容。
- 采用最小侵入方式接入当前 `TaskRunner`，避免大面积迁移。
- 事件 payload 需足够结构化，便于后续审计与回放。

### 风险与不确定性
- 风险 1：范围膨胀到 Feature 009（Worker runtime 细节）
  - 缓解：本 Feature 只交付单 Worker skeleton 和契约，不实现 runtime 预算控制。
- 风险 2：高风险 gate 与现有审批体系语义不一致
  - 缓解：明确“仅控制平面入口 gate”，不重写 Feature 006 的工具级 gate。
