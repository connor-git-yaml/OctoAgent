# Research Synthesis: Feature 032 — OpenClaw Built-in Tool Suite + Live Graph/Subagent Runtime

## 1. 综合判断

032 可以在现有 master 上以 additive 方式推进，但前提是把目标定义准确：

- 不是重做 030 的 capability framework
- 不是单纯追求 built-in tools 数量
- 而是把 built-in tools、graph、subagent、child work 变成 **用户真实可达、operator 可见、状态可恢复** 的产品面

## 2. 已确认的事实

### 2.1 030 的框架已经有了

现有仓库已经具备：

- capability pack / ToolIndex
- delegation plane / Work
- deterministic pipeline
- control plane 基础壳层

这意味着 032 不需要再造一套 capability framework。

### 2.2 当前缺的是 live runtime truth

调研证据一致指向一个问题：

- built-in tools 面太薄
- `graph_agent` / `subagent` 更像 runtime label
- child work split 语义缺失
- 用户可达入口不足

## 3. 冻结后的 032 定义

032 的正确定位应是：

> 在复用 025-B / 026 / 030 基线前提下，对齐 OpenClaw 的高价值 built-in tool suite，并把 Graph / subagent / child work split-merge 从标签层推进到真实 runtime truth。

## 4. 四个必须同时成立的支柱

### A. Tool Suite Parity

不是只保留 5 个 inspect/list，而是至少补齐：

- `sessions/subagents`
- `web/browser`
- `gateway/cron/nodes`
- `pdf/image/tts/canvas`
- `memory(read-only)`

### B. Graph Must Be Real

“支持 Pydantic AI Graph”在 032 中的定义必须是：

- 有真实 graph backend / bridge
- 有真实 graph run
- 有 durable state / checkpoint / replay
- 有 control plane 可见状态

如果只有 `graph_agent` target kind，不算完成。

### C. Subagent Must Be Real

“支持 Worker Spawn Subagent”在 032 中的定义必须是：

- 有真实 child runtime / session
- 有 durable session / work lifecycle
- 有 operator 可见状态

如果只有 route reason / metadata，不算完成。

### D. Child Work Split-Merge Must Be Real

“主 Agent 创建、合并、拆分 Worker”在 032 中的定义必须是：

- 有正式 `split_work`
- 有 durable child work objects
- 有 parent/child ownership
- 有 merge summary 与 operator 可见状态

如果只有 create/merge，没有 split，不算完成。

## 5. 非伪实现门禁

032 必须显式启用以下硬门禁：

1. 没有真实入口的工具，不得标记 shipped。
2. 没有真实 backend 的 graph/subagent，不得标记 available。
3. 没有 durable child work split/merge lifecycle，不得宣称 multi-worker split/merge 能力已交付。
4. 测试必须至少覆盖一条“入口 -> 执行 -> 状态可见”贯通链。

## 6. 推荐落地顺序

1. 先做 `BuiltinToolCatalog + availability/degraded truth`
2. 再做 `GraphRuntimeAdapter + SubagentRuntimeAdapter`
3. 再做 `split_work + child work lifecycle`
4. 最后扩 control plane 与 e2e

## 7. 最终建议

032 可以成为 M4 的第一项 Feature，但必须从一开始就把“真实可用”写进规范：

- shipped != 有 handler
- graph != target kind
- subagent != metadata
- split/merge != 只有 merge

只有这样，后面的实现阶段才不会再次滑回“看起来实现了，实际上用户用不上”的状态。
