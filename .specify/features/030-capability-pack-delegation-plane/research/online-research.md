---
required: true
mode: full
points_count: 3
tools:
  - context7:/pydantic/pydantic-ai
  - context7:/lancedb/lancedb
  - context7:/agronholm/apscheduler
queries:
  - "pydantic graph persistence checkpoint replay resume interrupt human-in-the-loop durable execution graph examples and APIs"
  - "Python vector search metadata filtering hybrid search reranking where filter examples for embedded local database"
  - "persistent datastore scheduler misfire coalesce max concurrent jobs pause resume events task metadata examples"
skip_reason: ""
---

# Online Research: Feature 030 — Built-in Capability Pack + Delegation Plane + Skill Pipeline

## 调研点 1：pydantic-graph 的 durable execution 语义

**来源**

- Pydantic AI / pydantic-graph 官方文档
- https://github.com/pydantic/pydantic-ai/blob/main/docs/graph.md
- https://github.com/pydantic/pydantic-ai/blob/main/README.md

**关键发现**

- 官方 graph 文档明确把 persistence 定义为“在每个节点执行前后快照 state，以支持中断恢复”。
- 官方推荐执行模型是 `initialize` + `iter` / `iter_from_persistence`，而不是一次性 run 到结束。
- durable execution 的目标明确覆盖 long-running、asynchronous、human-in-the-loop workflow。

**对设计的影响**

- OctoAgent 的 Skill Pipeline 不应只提供 `run()` 一把梭，而应提供逐节点推进、resume、replay。
- pipeline pause 点必须是一等能力，而不是异常分支。
- checkpoint 至少要覆盖 node 前状态、当前 node、执行状态、end result。

## 调研点 2：LanceDB 的向量检索 + metadata filter 能力

**来源**

- LanceDB 官方文档
- https://context7.com/lancedb/lancedb/llms.txt

**关键发现**

- LanceDB 支持向量检索、`where(...)` metadata filter、hybrid search、reranker、标量索引。
- Python 路径可以把本地 embedding 与本地表存储组合起来，不依赖远程服务。
- 这很适合 ToolIndex：工具条目体量不大，但需要 query + filter + local-first。

**对设计的影响**

- ToolIndex 应抽象为 backend + query/filter API，而不是把 LanceDB 细节泄漏到 Gateway。
- 为满足 degrade gracefully，应保留本地内存 fallback；但 canonical query model 需要与 Lance 风格 filter 兼容。
- ToolIndex hit 应保留 score、matched metadata、selected tool ids，方便进入 control plane。

## 调研点 3：APScheduler 的任务 metadata / misfire / max concurrency 语义

**来源**

- APScheduler 官方文档
- https://github.com/agronholm/apscheduler/blob/master/docs/api.md

**关键发现**

- APScheduler 任务配置本身就支持 `metadata`、`misfire_grace_time`、`max_running_jobs`。
- 这意味着 automation/delegation 的定时触发无需新造一套 scheduler contract，只需把 Work / action metadata 写到 job config。
- scheduler 的职责应保持在“触发”，真正执行仍由 control-plane action / delegation runtime 处理。

**对设计的影响**

- 030 不应重做 026 的 automation framework。
- 定时 delegation / pipeline run 可以直接复用现有 automation scheduler，并在 metadata 中挂 `work_id`、`pipeline_id`、`project_id`。
- 对 delayed work / timeout / replay，需要把状态保留在 Work / Pipeline store，而不是依赖 scheduler 本身持久化全部业务状态。

## Findings

1. 官方证据支持把 pipeline 做成“逐节点持久化恢复”的 deterministic 子流程，而不是单次 run 黑盒。
2. 官方证据支持 ToolIndex 走本地向量检索 + metadata filter 的可插拔实现，LanceDB 是合理增强路径。
3. 官方证据支持继续复用 APScheduler 作为触发器，不必在 030 内重做调度层。

## Impacts On Design

1. Skill Pipeline Engine 采用“节点级 checkpoint + replay + pause/resume + node retry”的显式 API。
2. ToolIndex 采用 backend abstraction，默认本地 fallback，可在环境允许时切到 Lance backend。
3. Work / Pipeline 的真实状态必须持久化在 OctoAgent 自己的 store；APScheduler 只承载触发，不承载 canonical business state。
