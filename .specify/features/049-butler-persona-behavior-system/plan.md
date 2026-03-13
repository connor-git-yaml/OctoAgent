---
feature_id: "049"
title: "Butler Persona & Clarification Behavior System"
created: "2026-03-14"
updated: "2026-03-14"
status: "Draft"
---

# Plan - Feature 049

## 1. 目标

把 Butler 的默认行为系统从“零散 prompt/特判/文案”提升为：

- clarification-first
- source-aware
- project-scoped
- 可用 markdown 文件演化
- 可治理、可审计

## 2. 非目标

- 不为天气/推荐/购物等各个场景分别造专用缓存或专用策略树
- 不重做 Worker 全量 persona 系统
- 不引入新的 memory backend
- 不绕过现有 review/apply 治理直接允许任意自我改写

## 3. 设计原则

### 3.1 通用优先于 case patch

under-specified 请求必须由通用 clarification 决策框架处理，而不是新增单案判断。

### 3.2 行为与人格要有正式载体

用户能改、Agent 能提案、系统能装配，才算正式产品能力。

### 3.3 分层优先于大 prompt

借鉴 Agent Zero 的 role / communication / solving 分层，不再把所有行为塞进一段大 prompt。

### 3.4 治理优先

允许 Agent 帮助演化行为文件，但核心行为文件默认走 review/apply，不做静默自改。

## 4. 参考证据

- OpenClaw workspace 行为文件：
  - `_references/opensource/openclaw/docs/reference/AGENTS.default.md`
  - `_references/opensource/openclaw/docs/reference/templates/AGENTS.md`
  - `_references/opensource/openclaw/docs/reference/templates/BOOTSTRAP.md`
- Agent Zero prompt 分层：
  - `_references/opensource/agent-zero/prompts/agent.system.main.role.md`
  - `_references/opensource/agent-zero/prompts/agent.system.main.communication.md`
  - `_references/opensource/agent-zero/prompts/agent.system.main.solving.md`
- 当前 OctoAgent runtime 基线：
  - `docs/blueprint.md`
  - `.specify/features/041-butler-worker-runtime-readiness/spec.md`
  - `.specify/features/042-profile-first-agent-console/spec.md`

## 5. 实施切片

### Slice A - 行为 pack 设计

- 定义 7 个核心 markdown 文件
- 规定 project / agent profile 绑定方式
- 定义默认模板与缺失回退规则

### Slice B - Clarification 决策框架

- 定义 under-specified 请求分类
- 定义补问、fallback、继续委派三种分支
- 约束追问轮数和 best-effort 触发条件

### Slice C - Runtime 装配

- 把 behavior pack 装配到 Butler runtime
- 定义 Worker 继承的 behavior slice
- 为 runtime truth 增加 effective behavior source 说明

### Slice D - 提案与治理

- 定义 BehaviorPatchProposal
- 接入 review/apply 或等价治理动作
- 明确哪些文件可自动提案、哪些默认需审批

### Slice E - 验收矩阵

- 覆盖排期、推荐、比较、实时查询缺位置等场景
- 验证“先补问再答”而不是“先答再补边界”

## 6. 风险

- 如果行为 pack 设计过重，会让首次使用门槛升高
- 如果 clarification 策略过强，会让简单问题也变慢
- 如果直接允许 silent self-edit，会破坏用户对人格边界的控制感

## 7. 验证方式

- 行为装配单测
- under-specified 请求矩阵测试
- behavior patch proposal 流程测试
- 手动对话回放：排期、推荐、实时查询、比较任务
