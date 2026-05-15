## F100 Pre-Implementation Adversarial Review

**日期**：2026-05-14
**Reviewer**：Codex GPT-5.4（挑战者立场）

## Findings（按 severity 排序）

### HIGH-1: H1 主目标缺少生产侧触发点，`AUTO/force_full_recall` 目前是死能力
- **位置**：`.specify/features/100-decision-loop-alignment/spec.md:36-40,84-99,137-146`；`.specify/features/100-decision-loop-alignment/plan.md:66-129`；`octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:780-785,849-854`；`octoagent/packages/core/src/octoagent/core/models/orchestrator.py:92-95`；`octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py:906-935`
- **问题描述**：spec 把 H1 定义为“主 Agent 自跑复杂查询时可强制 full recall”，但 plan Phase D 只新增字段、helper 和单测，没有任何 production writer 负责把 `force_full_recall=True` 或 `recall_planner_mode="auto"` 真正写进主路径。现状 main inline 仍在 orchestrator 两处硬编码 `recall_planner_mode="skip"`；delegation path 则依模型默认值保持 `full`。这意味着实现完 plan 后，`AUTO` 只会活在 helper/单测里，H1 在真实流量上不可达。
- **影响**：Feature 名义上完成，但主目标不会落地；`AC-1/US-1` 只能在构造对象的单测里通过，真实 `main_inline` 复杂查询仍然永远 skip recall planner。
- **建议**：把“谁来写 `force_full_recall` / `auto`”提升为显式实现任务，至少补一个 production producer（例如路由/上下文感知分发层），并增加端到端验收：真实 `main_inline` 请求在满足条件时能进入 full recall，而不是只改 helper。

### HIGH-2: AC-9/Phase C 与现有 pre-decision `RuntimeControlContext` 设计冲突，`unspecified` 不是单纯漏传
- **位置**：`.specify/features/100-decision-loop-alignment/spec.md:187-190`；`.specify/features/100-decision-loop-alignment/plan.md:21-48,154-168`；`octoagent/apps/gateway/src/octoagent/gateway/routes/chat.py:433-445,482-493`；`octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:770-785,1046-1051`
- **问题描述**：plan 认为“所有 production 构造点都应显式传 `delegation_mode`”，但代码里 chat 入口会先构造一个不带 `delegation_mode` 的 seed `RuntimeControlContext`，随后 orchestrator 才用 `is_single_loop_main_active()` 判断是否走 single-loop 或 routing。这里的 `unspecified` 不是遗留脏值，而是“尚未决策”的状态。如果 Phase E 把 `unspecified` 在 helper 中一律改为 raise，而 Phase C 又要求所有构造点都填显式值，当前 pre-decision 模式没有正确值可填。
- **影响**：正常 chat 请求可能在决策前就因 helper raise 失败；或者被迫提前写入错误 `delegation_mode`，造成 single-loop/routing 漂移。
- **建议**：不要把 AC-9 定义成“所有构造点显式化”。更安全的做法是：只对“已完成决策并将被消费的 runtime_context” fail-fast，或新增独立的 `pre_decision` 状态/位，避免拿 `unspecified` 同时承载“未决策”和“漏传”两种语义。

### HIGH-3: F099 ask_back 兼容性建立在不存在的 `runtime_context` 透传上
- **位置**：`.specify/features/100-decision-loop-alignment/spec.md:112-114,166-169`；`.specify/features/100-decision-loop-alignment/plan.md:227-255`；`octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py:613-639,691-700`；`octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py:2704-2707`；`octoagent/apps/gateway/src/octoagent/gateway/services/connection_metadata.py:10-58`
- **问题描述**：spec/plan 把 F099 兼容前提写成“attach_input 恢复后继续透传原 `runtime_context`，因此仍按 worker_inline skip”。但代码并不支持这件事：`attach_input()` 只把 `is_caller_worker_signal` 塞进 `resume_state_snapshot`；`_run_job()` 恢复时重新取的是 `get_latest_user_metadata()`，而该函数只合并 USER_MESSAGE / CONTROL_METADATA_UPDATED 中的 allowlisted control metadata，根本不包含 `runtime_context_json`。也就是说，resume 路径的真实前提不是“runtime_context 已透传”，这与 spec 当前叙述冲突。
- **影响**：Phase D/E 改 helper 后，ask_back turn N+1 的 recall/routing 行为很可能漂移；而 plan 把验证放到 Phase F，已经晚于 destructive 的 fallback removal。
- **建议**：在 implement 前先补一个真实恢复链路验证，明确 resume 时 `runtime_context` 到底从哪来；若当前确实会丢失，就必须先持久化/重建 `delegation_mode + recall_planner_mode`，再讨论 Phase E 移除 fallback。

### MEDIUM-1: 当前 Phase 顺序不够安全，F 应前置到 E 之前
- **位置**：`.specify/features/100-decision-loop-alignment/plan.md:154-255`；`octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py:613-639,691-700`
- **问题描述**：plan 现在是 `D -> E -> F`。但从代码看，ask_back resume 是最依赖旧 fallback / 旧恢复语义的路径之一；一旦 E 先删除 fallback，再到 F 才验证，失败时已经进入跨 Phase 回滚场景，定位成本高。
- **影响**：一旦 F 暴露问题，往往需要回撤 E 或在 E 后追加兼容补丁，增加 review/bisect 成本，也弱化了“逐 Phase 风险收敛”的目的。
- **建议**：把顺序改成 `C -> F(先做恢复链路验证/补测) -> D -> E`，或者至少把 E 拆成“只删写入”和“删 reader fallback”两个子 Phase，后一半必须等待 F 通过后再做。

### MEDIUM-2: AC-PERF-1 的 5% hard gate 测量设计不足以支撑 hard gate
- **位置**：`.specify/features/100-decision-loop-alignment/spec.md:191-201,395-402`；`.specify/features/100-decision-loop-alignment/plan.md:263-277`
- **问题描述**：spec/plan 用 `e2e_smoke` 5 次循环比较 P50/P95，并据此设置 5% hard gate。这个方法统计上太薄：5 个样本基本没有可解释的 P95，且 e2e_smoke 混入 provider、网络、缓存、数据库抖动，噪声量级很可能大于 5%。
- **影响**：可能因为偶然噪声错误拦截，也可能把真实轻微回归洗掉，导致 hard gate 既不稳也不准。
- **建议**：把 hard gate 改成更可控的基准方案，例如固定环境下每路径至少 20 次、冷热分开、记录原始样本；对 override 场景再单独做 mock/planner 基准，避免用高噪声 e2e 直接做唯一门槛。

### LOW-1: `force_full_recall: bool` 与 spec 自己声明的 F107 演进方向不一致
- **位置**：`.specify/features/100-decision-loop-alignment/spec.md:245-259,341-346,450-453`；`.specify/features/100-decision-loop-alignment/plan.md:14,323-324`
- **问题描述**：F100 现在锁定 `force_full_recall: bool`，但 spec 末尾又明确预告 F107 可能需要 `Literal["off","full","partial"]`。这说明当前命名和类型都只覆盖了一半演进方向，未来要么重命名，要么叠加第二个 override 字段。
- **影响**：不是当前 blocking bug，但会给 F107 带来额外迁移/兼容负担。
- **建议**：二选一即可：要么在 F100 就承认这是一次性字段，后续允许破坏式升级；要么现在直接改成可扩展的 `recall_override_mode` 一类枚举，减少后续重构成本。

## 总评
- Finding 数量：HIGH 3 / MEDIUM 2 / LOW 1
- 是否建议进入 implement 阶段：NO。当前 spec/plan 对 H1 触发点、`unspecified` 的语义边界、以及 F099 resume 兼容前提都还没有锁实；直接实现很容易做成“单测通过、主链偏移”。
- 关键修复优先级排序（如有 HIGH）：
1. 先澄清并修正 `runtime_context` 在 ask_back resume 链路中的真实恢复机制，再决定能否删 fallback。
2. 重写 AC-9 / Phase C 的边界，区分 pre-decision seed context 与 post-decision consumed context。
3. 给 H1 增加真实 production producer 与 e2e 验收，否则 `AUTO/force_full_recall` 只是死配置。
