---
feature_id: "049"
title: "Butler Behavior Workspace & Agentic Decision Runtime"
created: "2026-03-14"
updated: "2026-03-14"
status: "Implemented"
---

# Plan - Feature 049

## 1. 目标

把 Butler 的默认行为从“代码里的场景特判”迁移到：

- 少量显式 behavior files
- role/environment/communication/solving 风格的分层装配
- `RuntimeHintBundle + ButlerDecision` 决策链
- 可见、可编辑、可审计的 UI/CLI 管理入口

## 2. 非目标

- 不把治理边界下放给 md 文件
- 不把 `SOUL.md / IDENTITY.md / HEARTBEAT.md` 做成当前阶段默认主路径
- 不顺带重做 Memory、Setup 或 Worker persona 全家桶
- 不继续为天气、推荐、排期等问题追加新的硬编码分类树

## 3. 设计原则

### 3.1 文件显式优先

行为入口优先来自真实文件，而不是代码模板或隐藏 prompt。

### 3.2 Agent 决策优先

软行为由 Butler 基于 hints 做判断；代码只保留 deterministic guardrails。

### 3.3 默认文件克制

当前阶段只收口四个核心文件：

- `AGENTS.md`
- `USER.md`
- `PROJECT.md`
- `TOOLS.md`

### 3.4 主会话与子会话分层

Worker 默认只看到共享文件和显式 capsule，不读主会话私有行为全集。

### 3.5 UI / CLI 同源

behavior file 的查看、编辑、diff、apply 不能只存在于本地文件系统。

## 4. 参考证据

- OpenClaw 文件分层与主/子会话可见性：
  - `_references/openclaw-snapshot/data/skills/agent-config/references/file-map.md`
- OpenClaw 文件修改工作流：
  - `_references/openclaw-snapshot/data/skills/agent-config/SKILL.md`
- Agent Zero 分层 system prompt：
  - `_references/opensource/agent-zero/prompts/agent.system.main.md`
  - `_references/opensource/agent-zero/prompts/agent.system.main.role.md`
  - `_references/opensource/agent-zero/prompts/agent.system.main.solving.md`
- 当前 OctoAgent runtime 基线：
  - `docs/blueprint.md`
  - `.specify/features/041-butler-worker-runtime-readiness/spec.md`
  - `.specify/features/044-settings-center-refresh/spec.md`

## 5. 实施切片

### Slice A - Behavior Workspace 模型

- 定义 `BehaviorWorkspace / BehaviorFile / BehaviorVisibility`
- 确定四个核心文件和三个可选扩展文件
- 定义 project / system fallback 与 effective source chain

### Slice B - Runtime Hint Bundle 与 ButlerDecision

- 定义 `RuntimeHintBundle`
- 定义 `ButlerDecision`
- 规定 direct / ask_once / delegate / best_effort 的 contract
- 明确哪些判断继续留在 deterministic runtime guard

### Slice C - Worker Context Capsule

- 定义 Butler 到 Worker 的共享文件集合
- 定义 `USER.md` 如何被筛选为 hints 而不是整份透传
- 将 A2A payload / work metadata 与 behavior source 对齐

### Slice D - Web / CLI 产品面

- 设计 behavior files 管理入口
- 设计 effective source / diff / review / apply 界面与命令
- 对齐 Settings / Agents / Chat 的入口层级

### Slice E - 兼容与迁移

- 设计从当前代码模板 / `behavior_pack` metadata 到真实文件的迁移
- 设计兼容 fallback
- 明确旧天气/location 特判的下线顺序

### Slice F - 验收矩阵

- 覆盖天气、推荐、排期、比较、显式 WebSearch 指令
- 覆盖“有默认值 / 有会话确认事实 / 完全缺失 / 工具不可用”四类上下文

## 6. 风险

- 如果 `ButlerDecision` 设计得太重，会拖慢简单问题
- 如果文件边界不清，会导致规则散落重复
- 如果 UI/CLI 只做文件编辑不做 effective source，可维护性仍然差
- 如果迁移太激进，现有 041 freshness 路径可能短期回退

## 7. 验证方式

- behavior workspace 装配单测
- `RuntimeHintBundle / ButlerDecision` contract 测试
- Worker 可见文件与 capsule 测试
- Web 只读 behavior view 与 CLI 管理入口验收
- 真实对话回放：天气、推荐、排期、比较、显式 web search 请求

## 8. 当前实现收口（2026-03-14）

- `BehaviorWorkspace` 已从真实文件系统加载，并带 source chain / visibility / shared slice
- `RuntimeHintBundle` 已接入 Butler prompt
- `ButlerDecision` 已支持模型预路由、generic `delegate_research / delegate_ops` 和 recent conversation context
- deterministic 场景树已降级为 compatibility fallback，并带 provenance
- CLI 已提供 `octo behavior ls/show/init`
- Web `Settings` 已提供 `Behavior Files` 只读 operator 视图
