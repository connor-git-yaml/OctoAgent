# 产品调研报告: M2 Integration Acceptance

**特性分支**: `codex/feat-023-m2-integration-acceptance`  
**调研日期**: 2026-03-07  
**调研模式**: full（本地 references + 当前代码基线）

## 1. 需求概述

**需求描述**: 基于 `docs/m2-feature-split.md` 的 Feature 023，汇合 015-022，完成 “M2 已可日常使用” 的用户视角验收，不引入新业务能力。

**核心目标**:

- 把 `octo config`、`octo doctor --live`、`octo onboard`、Telegram pairing、首条消息入站串成一次真实的首次使用闭环
- 把 Web / Telegram operator control 从“各自可用”提升为“同一事件链上的等价操作”
- 把 A2A-Lite、JobRunner、interactive execution 从“分段 contract 正确”提升为“同一执行链可验收”
- 把 Memory / Chat Import / backup / export / restore dry-run 串成 “导入后数据仍可迁移、可恢复、可证明” 的完整链路

**目标用户**:

- 第一次配置 OctoAgent 的 owner
- 每天用 Telegram / Web 操作任务与审批的个人维护者
- 需要相信 “导入的数据没有黑洞、系统真的能恢复” 的重度用户

## 2. 用户问题与价值判断

### 当前用户痛点

1. 015-022 已有不少能力，但更多是“单 Feature 分段通过”，不是用户实际感知到的一条连续工作流。
2. 新用户从 `octo config init` 进入后，还会在 `octo doctor --live`、Telegram 配置、pairing、首条消息验证之间来回切换入口。
3. operator inbox 在 Web 和 Telegram 都存在，但用户还无法确信“同一件待办”在两个渠道上的动作语义、审计和状态回放完全一致。
4. A2A-Lite、JobRunner、interactive execution、checkpoint resume 已分别完成，但缺少“协议消息真正驱动执行链”的验收证据。
5. Chat Import、Memory、backup/export/restore 各自可用，但还没有验证 “导入后的关键数据确实被 backup 覆盖、能被 export 看见、能通过 restore dry-run 证明可恢复”。

### 对用户真正有价值的结果

1. 新用户能按照单条路径完成最小配置、doctor、自检、pairing 和首条消息验证，而不是猜下一步去哪。
2. 同一条待审批或告警，无论在 Web 还是 Telegram 处理，结果都写入同一审计链，不出现“双套状态”。
3. 当任务等待输入、审批、取消或恢复时，用户能确认 A2A 和 JobRunner 不是只在模型层/协议层“理论可用”，而是真正参与执行面。
4. 用户可以相信导入的聊天历史已经进入系统持久化边界，并且在 backup / export / restore dry-run 中可见。
5. M2 验收报告不只是“全部通过”，还会明确剩余风险和非目标边界。

## 3. 参考产品复核

### OpenClaw：首次使用与控制面是连续的产品体验

本地参考显示 OpenClaw 把 `configure`、`onboard`、`doctor`、`dashboard` 串成一个连续的 owner 体验，而不是把它们当作互不关联的脚本：

- `_references/opensource/openclaw/src/commands/onboard.ts`
- `_references/opensource/openclaw/src/commands/doctor.ts`
- `_references/opensource/openclaw/docs/channels/pairing.md`
- `_references/opensource/openclaw/docs/web/dashboard.md`

对 023 的启发：

- 首次使用路径必须是一个连续流程，不能要求用户在 CLI / YAML / Web 之间自己拼装
- pairing 与 operator control 不能只做“某端可用”，必须能说明哪条路径是主路径、降级路径是什么

### Agent Zero：首次 working chat 与持久化能力必须能被普通用户直接感知

Agent Zero 安装文档明确把目标定义成 “从零到第一条可工作的 chat”，并把持久化、备份、聊天保存/加载作为用户可感知能力：

- `_references/opensource/agent-zero/knowledge/main/about/installation.md`
- `_references/opensource/agent-zero/knowledge/main/about/github_readme.md`
- `_references/opensource/agent-zero/python/api/backup_create.py`

对 023 的启发：

- “first working chat” 是产品里程碑，不是开发者内部状态
- 持久化与恢复能力必须在验收链里被验证，而不是等 M3 再说

### Pydantic AI：多 Agent / hand-off / durable execution 需要真实控制流证据

Pydantic AI 文档强调，多 agent、handoff、graph/durable execution 的复杂性并不在于“模型定义”，而在于真实控制流是否被约束和验证：

- `_references/opensource/pydantic-ai/docs/multi-agent-applications.md`
- `_references/opensource/pydantic-ai/docs/durable_execution/overview.md`
- `_references/opensource/pydantic-ai/pydantic_ai_slim/pydantic_ai/_a2a.py`

对 023 的启发：

- 018/019 的协议与执行能力不能只停留在 round-trip / unit 级别
- 023 必须补上协议进入执行面后的联合验收，而不是只看 schema

## 4. MVP 范围建议

### Must-have（023 MVP）

- 首次使用闭环验收线：
  - `octo config init`
  - `octo doctor --live`
  - Telegram pairing
  - `octo onboard --channel telegram`
  - 首条 Telegram 入站消息创建 task
- operator parity 验收线：
  - pairing
  - approval
  - retry / cancel
  - alert ack
- A2A + JobRunner 联合验收线：
  - `A2A TASK`
  - `DispatchEnvelope`
  - `WorkerRuntime / JobRunner`
  - `RESULT/ERROR`
- import / memory / recovery 验收线：
  - `octo import chats`
  - Memory commit
  - `octo export chats`
  - `octo backup create`
  - `octo restore dry-run`
- M2 验收报告与剩余风险清单

### Nice-to-have（二期）

- 前端可视化验收面板
- 验收报告自动生成 HTML/Markdown 双格式
- 更丰富的 cross-channel 手动 smoke checklist

### Out of Scope

- 新增 Telegram 新能力、A2A 新消息类型、Memory 新分区
- destructive restore apply
- 新的 operator dashboard
- 新的 source adapter

## 5. 对上游文档的影响判断

023 的 spec 需要把一个边界写死：

- 023 允许修补阻塞验收的最小 DX 断点
- 023 不允许把这些断点修补扩成新 Feature

此外，`docs/m2-feature-split.md` 与后续验收报告需要同步回写：

- 首次使用主路径定义
- operator parity 的验收范围
- A2A + JobRunner + Memory + Recovery 的联合验收证据

## 6. 结论与建议

Feature 023 的产品价值不是“再做一个能力”，而是把 M2 从“开发者知道已经做了很多”收敛成“用户真的可以每天使用，并知道哪里仍有边界”。如果没有 023，M2 仍然会停留在一组看起来完整、实际却缺少联合证据的分段 Feature。
