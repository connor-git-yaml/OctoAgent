# F103c — Codex pre-impl Adversarial Review（spec + plan + tasks）

> 时间：2026-05-26
> 评审范围：spec.md / plan.md / tasks.md / research/baseline-recon.md
> Codex（GPT-5.4 high）站在挑战者立场
> agentId: a6c99cf97cd6fe157

## Finding 汇总

| ID | Severity | Title | 状态 |
|----|----------|-------|------|
| PH1 | HIGH | `agent_runtime_id=""` 兜底破坏 audit chain | ✅ accept-fixed |
| PM1 | MEDIUM | sha256 去重不能 cover HIGH 通知风暴 | ✅ accept-fixed |
| PM2 | MEDIUM | "8 条" logger 升级口径自相矛盾 | ✅ accept-fixed |
| PM3 | MEDIUM | helper 签名与 SSE 广播路径不匹配 | ✅ accept-fixed |
| — | LOW | 无 | — |

**总体结论**（Codex）：adjust-spec-first（修订 spec/plan 再进 implement）
**处理**：4 项全 accept-fix，spec/plan/tasks 已修订到位。

---

## PH1（HIGH）：`agent_runtime_id=""` 兜底破坏 audit chain

**Codex 原文**：spec 要求 `WORKER_LOG_EMITTED` / `WORKER_ERROR` 含 `agent_runtime_id`，且 AC2-3 明确要求 "含 task_id / agent_runtime_id audit chain"。但 plan/tasks 多处允许空串兜底。现有代码其实有真实来源：
- `worker_runtime.py:570-572` 从 `envelope.metadata["agent_runtime_id"]` 透传
- `dispatch_service.py:269-274` 把 `target_agent_runtime_id` 写入 metadata
- `AgentRuntime.agent_runtime_id` / `AgentSession.agent_runtime_id` 是非空身份字段

**Impact**：EventStore 能接受这些事件，但高级审计无法从 `WORKER_LOG_EMITTED/WORKER_ERROR` 反查 Worker runtime/session/RecallFrame，F096 四层链路会在新增事件处断掉。

**闭环处理（accept-fix）**：
1. spec FR-A2 / FR-A3 增加 `degraded_reason: str | None = None` 字段（用于显式标记 audit chain 降级原因，如 `"agent_runtime_id_unavailable"`）
2. spec Entity 1 / Entity 2 加 degraded_reason 字段，标注"空串仅当 degraded_reason 同时设置"
3. spec AC2-3 改写：派生成功路径 `agent_runtime_id` 必非空；派生失败路径 `agent_runtime_id == ""` 且 `degraded_reason` 必填
4. plan Phase B 新增 `derive_agent_runtime_id(metadata) -> tuple[str, str | None]` 派生工具，按 `agent_runtime_id / target_agent_runtime_id / source_agent_runtime_id` 优先级查找
5. plan Phase C-1 各调用点先派生再调 helper，不允许传字面量空串
6. helper 内部 `assert agent_runtime_id != "" or degraded_reason is not None`
7. tasks B-2 加 3 case 覆盖派生 + 断言

---

## PM1（MEDIUM）：sha256 去重不能 cover HIGH 通知风暴

**Codex 原文**：实际去重键是 `task_id:event_type:state_transition_event_id`，调用处默认 `state_transition_event_id=""`。plan 中 `audit_worker_error(...)` 没有传入新 `WORKER_ERROR` event id。**这不是 storm limiter**：不同 task 的重复 Worker crash 会全部 HIGH 推送；同一 task 下若产生多个不同 `WORKER_ERROR`，又可能因为空 `state_transition_event_id` 被过度去重。

**Impact**：spec §9 风险评估"低，已 cover"不成立。

**闭环处理（accept-fix）**：
1. spec §EC2 新增 EC2b：明确"sha256 只做幂等不做限速"；audit_worker_error 必须传 event_id 给 state_transition_event_id；storm control 推迟 F108
2. spec §9 风险表：HIGH 通知风暴 severity 从"低"提升到"中"，缓解策略改写
3. plan Phase B `audit_worker_error` 内部捕获 emit 返回的 Event 对象，将 `event.event_id` 传给 `notify_task_state_change(state_transition_event_id=event.event_id)`
4. tasks B-1 helper 签名返回 `Event | None`；B-2 case 5 验收 `state_transition_event_id == event.event_id`

---

## PM2（MEDIUM）：升级清单口径自相矛盾

**Codex 原文**：spec §0.3 表里第 6 条是 `WORKER_ERROR`，不是 `WORKER_LOG_EMITTED`；第 8 条又写 `dispatch_service.py` "4 处合并"。但 AC2-3 要求"8 条 WORKER_LOG_EMITTED + 1 条 WORKER_ERROR 全部可见"。tasks C-3 又允许 implement 阶段"选 1-2 条最高价值"。

**Impact**：实现者可以合法地只升 1 条 dispatch warning，同时声称 grep 数量达标；review 无法判断 AC2-3 的"8 条"到底是哪 8 条。

**闭环处理（accept-fix）**：
1. spec §0.3 表格冻结：精确 7 条 `WORKER_LOG_EMITTED` + 1 条 `WORKER_ERROR`，每条 file:line + 精确 key
2. dispatch_service.py 仅升级 `:974` `a2a_target_profile_resolve_worker_binding_failed` 一条；其他 2 条（`:988` `a2a_target_profile_explicit_id_not_found` / `:994` `a2a_target_profile_fallback_to_source`）保留 structlog only
3. spec AC2-3 精确写"7 条 WORKER_LOG_EMITTED + 1 条 WORKER_ERROR"
4. plan Phase C-3 表格冻结至单一 key
5. tasks C-3-1 / C-3-2 重写：单 key 升级 + 单 key 不升级（明文）
6. tasks E-4 验证从"grep ≥ 8 处"改为"按 key 逐条断言"

---

## PM3（MEDIUM）：helper 签名与 SSE 广播路径不匹配

**Codex 原文**：FR-B1/tasks B-1 让 `audit_worker_log(store: StoreGroup, ...)` 内部"用 `TaskService.append_structured_event`"。但真实入口 `TaskService.append_structured_event` 依赖 `TaskService(self._stores, self._sse_hub)`，并在有 `_sse_hub` 时广播（task_service.py:368-395）。仅传 `StoreGroup` 无法完成与现有事件路径一致的 SSE 广播。

**Impact**：AC1-2 写"control_plane API 或 SSE 可见"，但 helper 设计会诱导实现出"EventStore 可查、SSE 不广播"的半闭环。

**实测验证**（主 session）：task_service.py:368-395 确认 `append_structured_event` 调 `self._sse_hub.broadcast(task_id, event)`。Codex 判断成立。

**闭环处理（accept-fix）**：
1. plan Phase B helper 签名第一个参数从 `store: StoreGroup` 改为 `task_service: TaskService`
2. plan Phase C-1 各调用点：使用 worker_runtime 已构造的 `task_service = TaskService(self._stores, self._sse_hub, project_root=...)` 实例
3. plan Phase C-4 dispatch_exception 路径：使用 task_runner 上下文的 `service = TaskService(self._stores, self._sse_hub)` 实例
4. tasks B-1 / B-2 全部更新签名

---

## 修订后保留的设计假设

| 假设 | 来源 | 状态 |
|------|------|------|
| 2 个 EventType（WORKER_LOG_EMITTED + WORKER_ERROR）| spec §0.2 | Codex 未质疑，保留 |
| 1 天预估 ~10h | plan §6 | Codex 未质疑，保留 |
| traceback_artifact_id 本期固定 None | spec FR-A3 | Codex 未质疑（虽 plan §7 排除），保留 |
| baseline 已 cover N-H1 worker restart | spec §0.1 | Codex 未质疑，保留（D Phase 仅补 e2e test）|
| 不做 stderr 路由改造 | spec §0.2 | Codex 未质疑，保留 |

---

## 修订 commit 说明（与 spec/plan/tasks commit 一起）

Phase 0 commit message 草稿：

> docs(F103c-spec): spec + plan + tasks + baseline-recon + Codex pre-impl review 闭环（PH1+PM1+PM2+PM3 全 accept-fix）
