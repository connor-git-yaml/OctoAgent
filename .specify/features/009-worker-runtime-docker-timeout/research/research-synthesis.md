# 产研汇总: Feature 009 Worker Runtime + Docker + Timeout/Profile

**特性分支**: `codex/feat-009-worker-runtime`
**汇总日期**: 2026-03-03
**输入**: `research/tech-research.md` + `research/online-research.md`

## 1. 目标-实现矩阵

| 目标 | 约束来源 | 技术实现 |
|------|----------|----------|
| Worker Free Loop | FR-A2A-2 / M1.5 | `WorkerSession` + `WorkerRuntime.run()` |
| Docker 隔离执行接入 | §8.5.4 / m1.5 split F009-T03 | Docker backend + 可降级策略 |
| privileged 激活且显式授权 | §8.5.4 | runtime gate（`privileged_approved=true`） |
| 分层超时 | §8.5.6 | first_output / between_output / max_exec 配置 |
| 可中断/取消并进终态 | FR-A2A-2 | cancel token + TaskRunner cancel 协同 |

## 2. MVP 范围

### In Scope

- WorkerSession 数据模型
- WorkerRuntime Free Loop 驱动（max_steps + budget）
- Docker backend 选择与可用性探测
- privileged profile 显式授权校验
- 分层超时与失败分类
- cancel 信号透传与终态收敛
- 单元 + 集成测试

### Out of Scope

- 真正的 ToolBroker 交互式多轮工具会话
- Checkpoint 持久化与恢复（Feature 010）
- Watchdog 与 drift 检测（Feature 011）

## 3. 门禁结论（GATE_RESEARCH）

- 在线调研证据: 已补齐（3 个调研点，满足 0-5 点要求）
- 本地源码证据: 已覆盖 gateway/core/policy/tooling 关键路径
- 决策: **PASS（AUTO_CONTINUE）**
- 原因: 方案已收敛，且与 Feature 008 契约兼容，无需扩 scope。
