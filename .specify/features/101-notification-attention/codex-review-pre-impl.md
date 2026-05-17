# F101 Pre-Implementation Adversarial Review

**审查对象**：`spec.md` / `plan.md` / `tasks.md` / `analyze.md` / `research/tech-research.md`  
**审查姿态**：pre-implementation challenger review  
**输出文件**：`.specify/features/101-notification-attention/codex-review-pre-impl.md`  
**结论**：NEEDS_FIX_BEFORE_IMPLEMENTATION

## 1. Summary

本轮审查已完整读取 5 个输入文档，并对照 `analyze.md` 排除重复发现。`analyze.md` 已覆盖的 FR-C3 边界歧义、FR-B7 验证缺口、T-B-03 预设 `broadcast_to_session`、dismiss 内存 set 宪法论证、AC-C4 自验证问题不在本报告重复展开。

新增非重复 findings 共 9 项：5 HIGH，4 MEDIUM。核心问题集中在生产链路验证不足、WAITING_APPROVAL 状态机 owner 不清、跨通道 dismiss 链路不完整、quiet hours 语义与可靠投递冲突，以及审批超时策略过粗。

## 2. Findings

### H1 — Phase B mock verification cannot prove real ApprovalGate SSE production chain

- **ID**：H1
- **Severity**：HIGH
- **Title**：Phase B mock 验收无法证明真实 ApprovalGate SSE 生产链路
- **Location**：`spec.md`:~54-61, ~267-280；`plan.md`:~243-257；`tasks.md`:~486-515；`tech-research.md`:~80-104, ~164-212
- **Description**：US1 要求真实触发 Worker `escalate_permission`，验证 Web SSE 推送审批事件，并在用户批准后恢复 RUNNING。Phase B 的验收任务却主要是 `mock octo_harness bootstrap`、`mock approval_gate`、`mock ApprovalGate.wait_for_decision`。这些测试只能证明局部接口存在，不能证明真实 `octo_harness -> ApprovalGate -> SSEHub -> Web session` 链路可用。尤其是本 Feature 的生产缺陷根因正是 `ApprovalGate.sse_push_fn=None` 和 SSEHub per-session 能力未知，mock 验收容易给出假阳性。
- **Adversarial rationale**：作者把“联合 Phase”理解成 FR-C1/C2/C3/C6 同时提交，而不是用一个生产级 integration test 穿透四者。由于 `analyze.md` 已指出 `broadcast_to_session` 示例预设 R1 结论，文档修正方向停留在代码示例注释，没有进一步审查验收层是否仍然 mock 化。
- **Recommendation**：Phase B 增加 service-layer integration test：真实构造 `SSEHub`、真实 `ApprovalGate`、真实或轻量 task/event store，触发 `escalate_permission_handler` 后断言对应 session 收到 approval SSE event。T-B-11 的联合门必须包含该测试，而不只是 `tests/test_f101_approval_gate.py` 的 mock 测点。若 SSEHub 只能 task_id 广播，测试应覆盖 session_id 到 task_id 的映射路径。

### H2 — WAITING_APPROVAL timeout has ApprovalGate/task_runner dual-owner race condition

- **ID**：H2
- **Severity**：HIGH
- **Title**：WAITING_APPROVAL 超时存在 ApprovalGate/task_runner 双 owner 竞态
- **Location**：`spec.md`:~58-61, ~181-182；`plan.md`:~224-231；`tasks.md`:~452-463, ~491-495；`tech-research.md`:~322-326
- **Description**：文档同时要求 `ApprovalGate.wait_for_decision(timeout=300)` 超时返回 `"rejected"`，又要求 `task_runner.py:779` 的超时监控感知 WAITING_APPROVAL 并推进 FAILED。两条路径都可能对同一个 task 执行终态转移，但文档没有定义谁是状态机 owner。真实运行中，用户点击 approve、`wait_for_decision` timeout、task_runner monitor 扫描可能交错发生。当前验收只覆盖“timeout 返回 rejected 后 FAILED”的线性路径，没有覆盖 approve-vs-timeout、double FAILED、FAILED 后 late approve callback 等边界。
- **Adversarial rationale**：作者把 R2 简化为“修复 continue 跳过 WAITING_APPROVAL”，默认 ApprovalGate 和 task_runner 会自然协调。这个假设隐藏了跨组件状态机一致性问题，也隐藏了并发事件对终态幂等性的要求。
- **Recommendation**：明确 WAITING_APPROVAL 的状态转移 owner。建议由 task_runner 统一落终态，ApprovalGate 只产出 decision/timeout event；终态写入必须使用 compare-and-set 或现有 transition guard。新增竞态测试：timeout 与 approve 并发、timeout 后 late approve、monitor 与 `wait_for_decision` 同时触发，只允许一个终态事件落盘。

### H3 — Telegram dismiss missing callback ingress and Web refresh/read path specification

- **ID**：H3
- **Severity**：HIGH
- **Title**：Telegram dismiss 缺 callback 入口与 Web refresh/read path 规格
- **Location**：`spec.md`:~160-162, ~261-263, ~489；`plan.md`:~20-27, ~310-320；`tasks.md`:~674-685, ~725-738；`tech-research.md`:~21-26
- **Description**：GATE_DESIGN 决议是 Telegram dismiss 后 Web 下次刷新反映，不做实时 SSE push。plan/tasks 只规定 NotificationService 维护共享 dismissed set，没有任务定位 Telegram bot callback handler，也没有任务定位 Web notification refresh/list API。换言之，文档定义了一个状态容器，但没有定义写入入口和读取出口。真实链路应是 Telegram callback ingress -> NotificationService state update -> Web refresh/read model -> Web UI 不再展示该通知。
- **Adversarial rationale**：作者把“Web + Telegram 均通过 NotificationService 处理”当作既定事实，但 tech-research 只证明 channel protocol 有 `notify` / `send_approval_request`，没有证明 dismiss callback 和 Web 读取都经过同一个 service。`analyze.md` 只覆盖了 dismiss 内存 set 的 C1 论证不足，没有覆盖跨通道同步链路本身是否完整。
- **Recommendation**：Phase C 新增任务：定位 Telegram callback 处理器并接入 `notification_service.dismiss(notification_id, source="telegram")`；定位 Web notification refresh/list API 并过滤 dismissed id；补 integration test 覆盖 Telegram callback 后 Web refresh 不返回该 notification。若当前没有 Web notification read API，必须在 spec 中明确“下次刷新”的实际机制，否则 Option A 不可验收。

### H4 — quiet hours filtering conflicts with 'reliable push once' semantics

- **ID**：H4
- **Severity**：HIGH
- **Title**：quiet hours 过滤与“Worker 完成可靠推送一次”语义冲突
- **Location**：`spec.md`:~65-78, ~82-94, ~245-249；`plan.md`:~306-309；`tasks.md`:~623-635, ~725-738
- **Description**：US2 要求 quiet hours 内普通通知不推送，AC-B3 明确“不向任何 channel 发送”。US3 又要求 Worker 完成/失败时“可靠推送一次”，AC-B1 还要求 event_store 有对应通知事件记录。plan/tasks 的实现是 quiet hours 内直接 return，不说明被过滤通知是排队、丢弃、只写事件，还是 active hours 恢复后补发。如果 02:00 Worker 完成后被直接过滤且不补发，“可靠推送一次”对普通完成通知就不成立。
- **Adversarial rationale**：作者把 quiet hours 当成 channel-level send filter，没有把“通知生命周期”和“推送投递”拆开定义。这个假设隐藏了 filtered notification 是否仍是 notification、是否计入 dedup、是否进入 EventStore 的核心语义问题。
- **Recommendation**：在 spec 中显式选择一种语义：A. discard，只记录 event_store，不承诺 active hours 补发；B. delayed queue，active hours 后补发一次；C. digest，合并成摘要。若选 A，应重写 US3/AC-B1 文案，避免“可靠推送一次”覆盖 quiet hours 被过滤场景。若选 B/C，必须新增持久队列、恢复策略和去重测试。

### H5 — Fixed 300s approval timeout too coarse for overnight/offline approval scenarios

- **ID**：H5
- **Severity**：HIGH
- **Title**：固定 300s 审批超时对夜间/离线审批场景过粗
- **Location**：`spec.md`:~58-61, ~65-77；`plan.md`:~216-231；`tasks.md`:~424-427, ~452-461；`tech-research.md`:~322-326
- **Description**：文档将 `wait_for_decision(timeout=300)` 作为默认行为，并要求超时后任务走 FAILED。与此同时，US2 把 `approval_pending` 设为 quiet hours 内仍推送的 critical 通知。若审批在用户睡眠、离线、飞行模式、Telegram 不可达时触发，critical push 仍可能无人处理，5 分钟后任务自动失败。对高风险操作默认拒绝可以成立，但这应是显式 policy，而不是隐藏在固定 timeout 中。
- **Adversarial rationale**：作者只分析了 timeout cleanup 的状态一致性，没有审查 timeout duration 是否属于 Attention Model 的策略边界。由于目标是 Notification + Attention Model，审批等待窗口本身就是用户注意力模型的一部分。
- **Recommendation**：把 approval timeout 变成显式策略：按权限风险、任务来源、quiet hours、用户配置决定；至少提供配置项和文档默认值。新增验收：quiet hours 内 approval_pending 的 timeout 策略被明确记录，timeout event 含 reason，用户能理解任务为何 FAILED。对 overnight/offline 场景应考虑 pending-until-active 或 longer critical approval SLA。

### M1 — force_full_recall LONG_PROMPT_THRESHOLD Chinese vs English character density difference

- **ID**：M1
- **Severity**：MEDIUM
- **Title**：`LONG_PROMPT_THRESHOLD=2000` 以 Unicode 字符数计，会被中英文和代码密度系统性影响
- **Location**：`spec.md`:~203-211；`plan.md`:~31-36, ~135-140, ~565-570；`tasks.md`:~227-236, ~243-252；`tech-research.md`:~250-262
- **Description**：文档明确使用 `len(message)` 和 Unicode 字符数作为长 prompt 判断。中文、英文、代码块、JSON、日志的字符/token 比例差异很大；2000 中文字符、2000 英文字符、2000 字符代码对模型上下文压力不是同一量级。这个阈值可能漏掉短但高信息密度的错误栈或代码，也可能误伤长但低复杂度文本。F100 的 `force_full_recall` 目标是复杂上下文请求，不是单纯字符串长度。
- **Adversarial rationale**：clarify 已决定“Unicode 字符数”，后续 plan 将单位当成已关闭问题，没有继续审查它是否能代表 recall planner 成本/收益。该决策降低实现复杂度，但隐藏了跨语言用户体验差异。
- **Recommendation**：保留字符阈值作为第一版可以，但必须配置化并增加测试矩阵：中文、英文、代码块、日志。更稳妥的实现是轻量 token estimate 或复合信号，例如字符数 + newline/code fence + attachment/context count。至少在 `phase-0-recon.md` 或 Phase A 文档中记录该阈值的局限和后续调参入口。

### M2 — AC-F1 Option C assumption: full recall after ask_back resume may cause unexpected tool re-execution

- **ID**：M2
- **Severity**：MEDIUM
- **Title**：AC-F1 选 C 接受 resume 后 full recall，但未审查工具重执行和 loop 语义
- **Location**：`spec.md`:~373-391, ~328-331；`plan.md`:~449-458；`tasks.md`:~966-991
- **Description**：选 C 的验收只要求 `is_recall_planner_skip=False` 且任务继续，认为 ask_back resume 后 full recall 是合理 baseline。该判断没有覆盖多轮 ask_back 循环、resume 后 planner 重新看到上下文时是否会重复生成同一工具意图、以及 trace 中如何解释“因 runtime_context 丢失而 full recall”。如果 full recall 后重新规划触发相同 tool path，用户可能看到重复提问或重复审批尝试。即使最终不重复执行，也需要 trace 表达这是预期 resume recall，而不是 context 丢失 bug。
- **Adversarial rationale**：GATE_DESIGN 将 AC-F1 定性为“不修代码”，导致验收退化成 smoke test。作者验证了“不报错”，但没有验证“resume 后 loop 语义稳定、不引入重复工具意图”。
- **Recommendation**：Phase F 增加 2-3 轮 ask_back loop 测试，确认不会重复执行已完成的 ask_back/request_input/escalate_permission 意图。增加 trace reason 或 debug event，明确标记 “resume_after_user_input_full_recall_expected”。同时记录 full recall 的耗时指标，避免把性能回退伪装成 baseline 行为。

### M3 — Phase 0 reconnaissance has no explicit fallback branches if findings contradict plan assumptions

- **ID**：M3
- **Severity**：MEDIUM
- **Title**：Phase 0 侦察只有结论枚举，没有 contradiction fallback / go-no-go 机制
- **Location**：`plan.md`:~53-116, ~193-209, ~321-327, ~553-569；`tasks.md`:~64-85, ~350-360, ~657-668
- **Description**：Phase 0 输出 `SSEHub_BROADCAST_CAPABILITY = PER_SESSION | TASK_ONLY | NEEDS_NEW_METHOD` 和 `NOTIFICATION_SERVICE_INJECTED = YES | NO`，但没有规定哪些结果必须触发 plan 修订、风险升级、scope cut 或 go/no-go。T-B-02 只说“新增 broadcast_to_session 或 session_id→task_id 映射”，T-C-07 只说“若未注入则先加构造参数”，没有细化 fallback task、测试和验收。若 R1/R3 结论比预期复杂，现有任务仍会线性进入 Phase B/C。这样会把未知复杂度塞进本来已经不可拆分的 Phase B 或 Phase C。
- **Adversarial rationale**：作者已经意识到 R1/R3 未确认，因此误以为“Phase 0 必须实测”本身就是缓解。实际缓解必须包括分支计划，否则侦察只会把 unknown 改名成 known，但不会改变执行路径。
- **Recommendation**：在 Phase 0 出口增加 decision table：每个枚举结果对应实施路径、额外 tasks、是否需要更新 spec/plan/tasks、是否允许进入 Phase B/C。`NEEDS_NEW_METHOD` 或 `NOTIFICATION_SERVICE_INJECTED=NO` 应强制先修订 tasks 并增加对应 integration test。若发现需要 session_id→task_id 持久映射，应重新评估 Phase B 是否仍可单 commit 完成。

### M4 — Notification deduplication key design not specified

- **ID**：M4
- **Severity**：MEDIUM
- **Title**：通知去重 key 未定义，无法判断“同一通知”或“重复推送”
- **Location**：`spec.md`:~82-94, ~160-168, ~261-263；`plan.md`:~310-320, ~321-327；`tasks.md`:~674-685, ~692-701；`tech-research.md`:~21-24
- **Description**：tech-research 提到 `NotificationService` 当前有内存 set 做路由分发 + 去重，spec/tasks 又要求 Worker 完成通知精确一次、dismiss 重复幂等、跨通道同一通知共享状态。但文档没有定义 notification_id 或 dedup key 的组成：按 task_id、task_id+status、task_id+transition_id、approval_request_id，还是 channel-specific message id。没有 key 设计，无法判断 WAITING_APPROVAL 进入、approval timeout FAILED、worker_failed、worker_completed 哪些属于同一通知，哪些应各自发送。dismiss 如果按 task_id 去重，可能误 dismiss 同一 task 的后续 FAILED 通知；如果按 channel message id 去重，则跨通道同步又无法成立。
- **Adversarial rationale**：作者把“内存 set 去重”和“set.add 幂等”当成实现细节，默认它足以支撑所有 AC。其实 dedup key 是 Notification 模型的核心契约，直接影响 AC-B1 精确一次、AC-B6 幂等、Telegram/Web 同步和 quiet hours filtered event 的生命周期。
- **Recommendation**：在 spec 中定义 `notification_id` 生成规则和去重维度。建议使用稳定业务 key：`task_id + notification_type + state_transition_event_id/request_id`，并明确 channel message id 只是投递结果，不作为跨通道身份。新增测试：同一 task 的 WAITING_APPROVAL 与 FAILED 是不同 notification；同一 transition 重试只发送一次；dismiss 一个 approval notification 不会吞掉后续 completion/failure notification。

## 3. Already Covered In analyze.md And Skipped

以下问题已由 `analyze.md` 覆盖，本报告不重复作为 finding：

- FR-C3 实施范围与 FR-C1 联动边界歧义：`analyze.md`:~32-37 HIGH-01。
- FR-B7 `attention_work_count` 间接通过 AC-B1 验证不成立：`analyze.md`:~39-44 HIGH-02。
- plan/T-B-03 代码示例预设 `broadcast_to_session`：`analyze.md`:~46-51 HIGH-03。
- dismiss 内存 set 方案 vs 宪法 C1 边界论证不充分：`analyze.md`:~60-65 HIGH-05。
- AC-C4 自验证设计盲点：`analyze.md`:~69-72 MED-01。

## 4. Target Coverage Appendix

| Review target | Coverage |
|---|---|
| RuntimeContext trust boundary / SSE push / notification dedup | H1 covers SSE production chain；M4 covers dedup key；RuntimeContext AC-F1 cost covered by M2 |
| Phase B joint commit feasibility | H1 covers mock-only joint verification；M3 covers fallback branch missing |
| AC-F1 Option C hidden costs | M2 |
| dismiss Option A concurrency | H3 covers callback/read chain；M4 covers identity/dedup risk |
| ApprovalGate SSE production path fallback | H1 + M3 |
| Phase 0 recon failure paths | M3 |
| quiet hours delayed push semantics | H4 |
| Telegram dismiss sync chain | H3 |
| WAITING_APPROVAL 300s timeout | H2 + H5 |
| force_full_recall threshold | M1 |
| cross-feature compatibility | M4, plus H1/H3 for F064/F099/F100 contract surfaces |
| state machine integration | H2 |

## 5. Verification Checklist

- [x] Report contains at least 5 findings.
- [x] Report contains at least 2 HIGH severity findings.
- [x] Each finding has ID, severity, title, location, description, adversarial rationale, and recommendation.
- [x] Findings avoid duplicating `analyze.md` existing findings.

---

## 6. Finding 处理决策（主编排器，2026-05-16）

按 CLAUDE.local.md §"Codex Adversarial Review 强制规则" 处理流程：

| Finding ID | severity | 处理决策 | 修订位置 |
|-----------|----------|---------|---------|
| **H1** Phase B mock 验收不够 | HIGH | ✅ 接受 | plan §3.6 加 B-9c service-layer integration test；§3.7 联合验收门 6 项（含 integration test）；spec §10 第 9 条 |
| **H2** WAITING_APPROVAL 双 owner 竞态 | HIGH | ✅ 接受（GATE_DESIGN 已通过 task_runner = owner）| spec FR-C3 加状态机 owner + compare-and-set；spec §10 第 7 条；plan §3.6 加 B-9b 竞态测试；spec §9 风险 R9 |
| **H3** Telegram dismiss callback + Web list API 缺失 | HIGH | ✅ 接受 | spec FR-B5 重写（Telegram callback ingress + Web list/refresh API）；plan §4.7 加 C-7b/C-7c task；plan §1.3b decision table TELEGRAM_CALLBACK_HANDLER 项；spec §10 第 8 条；spec §9 R7 |
| **H4** quiet hours 过滤 vs 可靠推送语义冲突 | HIGH | ✅ 接受（用户决议选 A discard）| spec FR-B3 加 discard 语义；spec FR-B6 重写"精确一次=event_store 一次+channel push 受 quiet hours 控制"；US3 验收场景 1 文案改写；plan §4.7 C-9 |
| **H5** 300s 固定超时过粗 | HIGH | ✅ 接受（用户决议保留 300s + 配置项）| spec 新增 FR-C3b（默认 300s + USER.md approval_timeout_seconds + reason 字段）；plan §3.6 B-9d 配置覆盖测试；plan §1.3b decision table APPROVAL_TIMEOUT_DEFAULT 项 |
| **M1** LONG_PROMPT_THRESHOLD 跨语言密度 | MED | ✅ 接受 | plan §2.3 加 A-5b 跨语言测试矩阵（中/英/代码/JSON/log）；phase-0-recon.md 记录阈值局限 |
| **M2** AC-F1 选 C 多轮 ask_back loop 语义 | MED | ✅ 接受 | plan §7.2 加 F-2b 多轮 ask_back loop 测试 + trace `resume_after_user_input_full_recall_expected` 标记 |
| **M3** Phase 0 无 contradiction fallback | MED | ✅ 接受 | plan §1.3b decision table（8 个枚举结果对应实施路径） |
| **M4** notification_id 去重 key 未定义 | MED | ✅ 接受 | spec 新增 FR-B8（sha256 task_id + type + state_transition_event_id）；plan §4.7 C-8 实现规则 + C-12 三场景测试（M4-1/M4-2/M4-3）；spec §9 R8 |

**处理统计**：5 HIGH / 4 MED / 0 LOW 全部接受改动，0 项拒绝。

**spec 修订**：8 处（FR-B3 + FR-B5 重写 + FR-B6 + FR-B8 新增 + FR-C3 + FR-C3b 新增 + §10 第 7/8/9 条 + 风险表加 R7/R8/R9 + US3 验收场景 1 + AC-B1 文案）。

**plan 修订**：6 处（§1.3 加 decision table + §1.4 退出条件 + §2.3 加 A-5b + §3.6 加 B-9b/B-9c/B-9d + §3.7 联合验收门 6 项 + §4.7 加 C-7b/C-7c/C-8 修订 + §7.2 加 F-2b）。

**tasks 修订**：本次不直接修 tasks.md（tasks 子代理重新读 plan 即可对齐；若 Phase 0 实测发现 decision table 命中某行需新增 task，由 Phase 0 出口 commit 时 patch tasks.md）。

**进入 Phase 0 的前提**：以上 spec/plan 修订已 commit-ready；Phase 0 执行时按 §1.3b decision table 实测 + 填表，根据填表结果决定是否触发 tasks.md 范围调整。

