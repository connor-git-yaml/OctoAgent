# Research Synthesis: Feature 041 — Dynamic Root Agent Profiles

## 1. 综合判断

041 适合现在启动，而且应该作为 039 的直接下一层演进推进。

原因很明确：

- 039 已经交付 Butler/supervisor 与 worker review/apply 的治理主链；
- 035/036 已经把面向用户的工作台和设置治理入口立起来；
- blueprint 已经明确要求 `AgentProfile / WorkerProfile` 成为正式产品对象；
- 当前真正的瓶颈不是能力缺失，而是 worker identity 仍被固定枚举锁死。

因此，041 要做的不是“重写多 Agent 系统”，而是：

> 把 `WorkerType` 从“用户世界里的 Agent 身份”降级为“系统内建 archetype”，再把 `WorkerProfile` 产品化，让 Butler 和用户都能围绕正式 profile 创建、审查、运行和复用 Root Agent。

## 2. 已确认事实

### 2.1 blueprint 与产品方向一致

blueprint 已明确要求：

- `AgentProfile / WorkerProfile` 必须是正式产品对象
- `session / automation / work` 必须引用 profile id 与 effective config snapshot
- Agent / Worker / Subagent / Graph Agent 必须进入统一 control plane

所以 041 不是新方向，而是把 blueprint 已写明但尚未落地到 worker 侧的对象模型真正补齐。

### 2.2 当前系统最强的接缝点在 039 和 026

可以直接复用的基线已经存在：

- 026 的 canonical control-plane resources/actions
- 030 的 capability pack / tool catalog / ToolIndex
- 033 的 profile/context continuity 事实源思路
- 039 的 review/apply worker governance

这意味着 041 完全没必要平行造 backend。

### 2.3 当前前端最需要的是对象分层，而不是更多表单

当前 `Agents` 与 `ControlPlane` 页的问题，不是“字段不够多”，而是：

- 模板、正式 profile、运行实例没有拆层
- `worker_type` 仍然是主要分类轴
- 运行时无法追踪 profile lineage

所以前端目标应是 `Profile Library / Profile Studio / Runtime Workers` 三层，而不是继续往现有大表单里塞字段。

## 3. 推荐方案

### 3.1 产品对象

冻结以下一等对象：

- `WorkerArchetype`
- `WorkerProfile`
- `WorkerProfileRevision`
- `EffectiveWorkerSnapshot`
- `WorkerInstanceProjection`

其中：

- `WorkerArchetype` 是系统模板
- `WorkerProfile` 是用户资产
- `WorkerProfileRevision` 用于审查和发布
- `EffectiveWorkerSnapshot` 用于运行时追溯
- `WorkerInstanceProjection` 用于 UI 展示当前实例

### 3.2 控制面主链

Butler 或用户可以：

1. 从 starter template / 现有 profile / 当前实例发起 profile draft
2. 在 `Profile Studio` 中编辑身份、能力边界、工具、运行时和策略
3. 通过 `review/apply` 生成或更新正式 profile revision
4. 从 profile 启动实例或绑定到 project 默认行为
5. 在 runtime lens 中查看某次 work 实际使用的 profile revision 与工具快照

### 3.3 前端 IA

`AgentCenter` 推荐重构为三个一级区块：

- `Butler`
- `Profile Library`
- `Runtime Workers`

并新增一个 `Profile Studio` 作为独立页或右侧大 Drawer：

- 第 1 步：基础身份
- 第 2 步：能力边界
- 第 3 步：Review / Publish

### 3.4 接缝要求

041 必须遵守以下接缝：

- 不重做 039 review/apply，只做对象升级
- 不重做 026 backend contract，只扩 canonical resources/actions
- 不绕过 ToolBroker / policy / audit
- 不把 secret 实值或 prompt 原文散落到前端私有状态里

## 4. 推荐实施阶段

### Phase A: Profile Registry

- 新增 `WorkerProfile` 领域模型、store、projection、control-plane resources/actions
- 把四个内建 worker 降级成 starter templates

### Phase B: Runtime Binding

- `Work`、delegation、child launch、runtime truth 改为 `profile_id + snapshot`
- 保留 legacy `worker_type` 兼容字段

### Phase C: Agent Console UI

- 重构 `AgentCenter`
- 新增 `Profile Library / Profile Studio / Runtime Inspector`
- `ControlPlane` 增加 profile lens

### Phase D: Butler-assisted Creation

- Butler 通过 review/apply 发起 profile 提案
- 支持从运行实例反向提炼为 profile

## 5. 最终建议

- 041 应直接按正式 Feature 推进，而不是先做“自由创建 worker profile 的实验性 tool”。
- 第一轮要优先冻结对象模型、接缝、兼容策略和前端信息架构，再进入实现。
- 设计上要同时满足两件事：
  - 像 Agent Zero 一样可扩展
  - 像当前 OctoAgent 一样可治理、可审计、可恢复
