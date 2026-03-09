# Implementation Plan: Feature 032 — OpenClaw Built-in Tool Suite + Live Graph/Subagent Runtime

**Branch**: `codex/feat-032-openclaw-builtin-tool-suite` | **Date**: 2026-03-09 | **Spec**: `.specify/features/032-openclaw-builtin-tool-suite/spec.md`
**Input**: `.specify/features/032-openclaw-builtin-tool-suite/spec.md` + `docs/m4-feature-split.md` + Feature 025-B / 026 / 030 代码基线 + OpenClaw / Pydantic AI references

---

## Summary

Feature 032 本轮不做“所有 OpenClaw 工具全量照搬”，而是完成一个真实可用的纵向闭环：

1. 把 built-in tools 从当前最小 inspect/list 集扩成正式 catalog，并补 availability / degraded / install hint；
2. 把 `graph_agent` 从 target label 补成真实 `pydantic_graph` backend；
3. 把 `subagent` 从 metadata 补成真实 child task / child session / child work；
4. 把主 Agent 的 `split / merge` 补成 durable child-work lifecycle，并接入现有 control plane。

这样可以同时覆盖用户明确提出的三件事：

- 支持 Pydantic AI Graph
- 支持 Worker Spawn Subagent
- 支持主 Agent 创建、合并、拆分 Worker

并且满足“不能只是假装实现”的门禁。

---

## Technical Context

**Language / Version**:

- Python 3.12+
- TypeScript 5.x

**Primary Dependencies**:

- `pydantic_graph`
- `httpx`
- FastAPI / existing control plane / ToolBroker

**Target Platform**:

- 单实例、单 owner、trusted local / LAN runtime

**Testing Strategy**:

- `apps/gateway/tests/test_capability_pack_tools.py`
- `apps/gateway/tests/test_delegation_plane.py`
- `apps/gateway/tests/test_control_plane_api.py`
- `apps/gateway/tests/test_worker_runtime.py`
- `frontend/src/pages/ControlPlane.test.tsx`

**Constraints**:

- 不重做 026 control plane shell
- 不旁路 ToolBroker / Policy / Event / Audit
- 不引入 channel action packs / remote nodes
- `graph_agent` 必须真实走 `pydantic_graph`
- `subagent` 必须形成真实 child task / session

---

## Constitution Check

| Constitution 原则 | 适用性 | 评估 | 说明 |
|---|---|---|---|
| 原则 1: Durability First | 直接适用 | PASS | child work、child task、graph runtime state 都必须 durable |
| 原则 2: Everything is an Event | 直接适用 | PASS | tool use、work lifecycle、child launch、merge 都继续写事件 |
| 原则 3: Tools are Contracts | 直接适用 | PASS | 032 核心就是把 built-in tool suite 做成正式 contract |
| 原则 5: Least Privilege by Default | 直接适用 | PASS | web/browser/tts 等工具必须有 availability/degraded/install hint，不得静默超权 |
| 原则 6: Degrade Gracefully | 直接适用 | PASS | 缺依赖、缺 runtime、缺浏览器、缺系统 TTS 时都必须显式降级 |
| 原则 8: Observability is a Feature | 直接适用 | PASS | runtime truth 必须进入 control plane / session projection |

**结论**: 可直接进入实现。

---

## Project Structure

### 文档制品

```text
.specify/features/032-openclaw-builtin-tool-suite/
├── spec.md
├── plan.md
├── tasks.md
├── checklists/
│   └── requirements.md
└── research/
    ├── product-research.md
    ├── tech-research.md
    ├── research-synthesis.md
    └── online-research.md
```

### 源码与测试变更布局

```text
octoagent/packages/core/src/octoagent/core/models/
├── capability.py
└── control_plane.py

octoagent/apps/gateway/src/octoagent/gateway/services/
├── capability_pack.py
├── control_plane.py
├── delegation_plane.py
├── task_runner.py
├── task_service.py
├── worker_runtime.py
└── execution_context.py

octoagent/apps/gateway/tests/
├── test_capability_pack_tools.py
├── test_control_plane_api.py
├── test_delegation_plane.py
└── test_worker_runtime.py

octoagent/frontend/src/
├── pages/ControlPlane.tsx
├── pages/ControlPlane.test.tsx
└── types/index.ts
```

---

## Architecture

### 1. Built-in Tool Truth

在既有 `BundledCapabilityPack` 上增加：

- `availability`
- `availability_reason`
- `install_hint`
- `entrypoints`

并扩充至少 15 个真实 built-in tools，优先覆盖：

- `agents/sessions/subagents`
- `web/browser`
- `gateway/cron/nodes`
- `pdf/image/tts/canvas`
- `memory(read-only)`

### 2. Graph Runtime Bridge

新增 `GraphRuntimeBackend`：

- 真实消费 `pydantic_graph`
- 将 graph node progression 映射到 execution console / session summary
- 由 `WorkerRuntime` 根据 `target_kind=graph_agent` 选择

### 3. Child Task / Subagent Runtime

新增 child task launcher：

- 当前 worker 通过 built-in tool 或 control-plane action 发起 child task
- child task 作为独立 task/session 进入 TaskRunner
- 通过 metadata 传递 `parent_work_id / requested_target_kind / requested_worker_type`
- `DelegationPlane` 在 child task 侧恢复 parent/child ownership

### 4. Work Split / Merge

在现有 `Work` 模型上补齐：

- `work.split`
- `work.merge`
- child work summary / count / merge readiness

Web control plane 与 built-in tools 共用同一 child-launch helper。

---

## Phase Plan

### Phase 1: Freeze Models & Contracts

目标：补 capability/control-plane 模型，明确 availability/runtime truth 字段。

### Phase 2: Expand Built-in Tool Suite

目标：补 built-in tool catalog 与多工具族真实 handler。

### Phase 3: Live Runtime Truth

目标：落 `pydantic_graph` backend、child task/subagent spawn、parent/child work 关系。

### Phase 4: Control Plane & Verification

目标：把 availability/runtime truth/split-merge 状态接入 control plane 与前端测试。

---

## Risks & Tradeoffs

### Tradeoff 1: 工具族广度 vs 本轮真实可用性

- 选择：优先交付“少一些但真实可达”的 built-in tools，而不是空壳全覆盖。

### Tradeoff 2: Graph runtime 先接 execution/session truth，再做复杂 graph designer

- 选择：032 只交付真实 graph backend 和状态投影，不做图形编辑器。

### Tradeoff 3: Subagent 先基于 child task/session 落地

- 选择：优先把 subagent 做成真实 child task runtime；remote nodes 留后续 Feature。
