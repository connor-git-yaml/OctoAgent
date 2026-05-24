# F102 Quality Checklist

**生成时间**：2026-05-18
**Spec 版本**：Draft（74c9ab3 upstream）
**检查员**：质量检查表子代理

---

## 维度 1：可测性

**CHK-1.1** [✓] AC-B1 可独立测试性
AC-B1 标注 `[可独立测试]`，通过 mock cron trigger 触发 `_run_daily_summary()` 可在单测中执行；"P50 < 5s（不含 LLM 调用时间）"性能标准可测量。
无问题。

**CHK-1.2** [✗] AC-B4 quiet hours 边界的测试可执行性——**BLOCKER**
AC-B4 描述"通知被 F101 NotificationService 的 quiet hours 过滤器拦截"，但测试策略（§11）中对应 `test_daily_routine_priority.py` 覆盖的是 AC-B4 + AC-B7，而 AC-B4 的 quiet hours 过滤需要 `NotificationService` 内部状态（USER.md `active_hours` 解析结果）。spec 未说明测试是使用真实 `NotificationService` 还是 mock——若使用 mock，AC-B4 就退化为验证"调用了 notify_task_state_change"而非真正验证 quiet hours 过滤行为；若使用真实 NotificationService，需要注入 mock USER.md 内容。测试可行性未明确定义，可能导致 AC-B4 的 PASS/FAIL 判定依赖实现方理解。
**建议**：在 AC-B4 或 §11 中明确说明测试方法（推荐：真实 NotificationService + mock SnapshotStore 返回含 quiet hours 的 USER.md 内容）。

**CHK-1.3** [✓] AC-B6 cron 注册可测试性
AC-B6 验证 `job_id="_daily_routine"` 存在于 scheduler，可通过 `scheduler.get_job("_daily_routine")` 断言，不依赖实时时钟。`test_daily_routine_startup.py` 已列出。可测。

**CHK-1.4** [✗] AC-B1 / AC-E1 审计 task_id 不一致——WARNING
AC-B1 说"ROUTINE_TRIGGERED + ROUTINE_COMPLETED 事件写入 event_store"，AC-E1 说"查询 task_id=`_daily_routine_audit`"，FR-B7 调用样板中 task_id 也是 `"_daily_routine_audit"`。但 FR-B1 注册的 cron job id 是 `"_daily_routine"`（不带 `_audit` 后缀），命名不对称。虽然这是两个不同概念（cron job id vs event audit task id），但 spec 未显式说明两者关系，容易在实现时混淆。
**建议**：在 §7.4 或 §8.1 中加一句明确说明："cron job_id `_daily_routine` 是 APScheduler 调度标识，与 event_store 的 audit task_id `_daily_routine_audit` 是两个不同概念，互不关联。"

**CHK-1.5** [✓] AC-T1 task_store 新 API 可测试性
AC-T1 有明确 SQL 语义（`created_at >= start AND created_at < end`）和性能上界（< 500ms），可通过真实 SQLite + 构造测试数据验证。`test_task_store_time_range.py` 已列出且覆盖边界条件。可测。

---

## 维度 2：架构整洁度

**CHK-2.1** [✗] DailyRoutineService 文件复杂度预估——WARNING
`daily_routine.py` 包含：6 个 DI 依赖注入 + 3 个 USER.md 解析函数 + 1 个 LLM 调用路径 + 1 个 fallback 路径 + APScheduler 注册 + 审计 task 占位 + 事件写入 + 通知调用。按 FR-B2 列出的 9 步执行顺序估算，单文件行数可能达 400-600 行，接近 F091 D6 教训（agent_context.py 4112 行的前兆模式）。§14 复杂度评估标注"MEDIUM"，但未预警文件行数风险。
**建议**：plan 阶段在 Phase 0 侦察时明确设定单文件行数上限（建议 ≤ 350 行），若超出则提前规划拆分（如将 3 个 USER.md 解析函数提取到 `user_profile_parsing.py`，spec FR-D2 已提到此选项但标注"或"，应明确化）。

**CHK-2.2** [✓] 6 个 DI 依赖合理性
`scheduler` / `task_store` / `event_store` / `notification_service` / `snapshot_store` / `provider_router` 六个依赖均为业务必需（无一冗余）：scheduler 注册 cron、task_store 查时间范围、event_store 写审计事件、notification_service 推通知、snapshot_store 读 USER.md、provider_router 调 LLM。facade 抽象在此场景增加而非减少复杂度。6 个 DI 依赖合理。

**CHK-2.3** [✓] LLM + fallback 两条路径的抽象度
FR-B3 定义 fallback 触发条件（任何 LLM 异常）、fallback 模板格式、`ROUTINE_COMPLETED.fallback` 字段标记。两条路径分属 `_generate_summary_llm()` 和 `_generate_summary_fallback()`（§7.4 类结构），边界清晰，不引入过度抽象。合理。

**CHK-2.4** [✗] misfire_grace_time 值不一致——WARNING
FR-B1 样板代码中 `misfire_grace_time=300`（5 分钟），但 tech-research §任务 1 中现有 AutomationSchedulerService 注册样板是 `misfire_grace_time=30`（30 秒）。spec 未说明为何 daily routine 使用更长的 grace time（300s）。若系统重启恰好在 08:30，300s grace time 会在重启后 5 分钟内触发补发，而 30s 版本不会——这个行为差异是有意设计的还是笔误？
**建议**：spec 中补充 `misfire_grace_time=300` 的选择理由，或与现有约定对齐为 30s。

---

## 维度 3：与 F101 边界一致性

**CHK-3.1** [✓] F102 未修改 F101 NotificationService 核心结构
spec §8.2 声称"F101 接口复用"，FR-B7 仅调用 `notify_task_state_change`（已有方法），无新增参数、无修改签名。与 tech-research §任务 2 中实测的签名 `notify_task_state_change(task_id, event_type, payload, priority=LOW, active_hours=None, state_transition_event_id="", session_id=None)` 匹配。边界清晰，未入侵 F101。

**CHK-3.2** [✗] FR-B7 与 F101 实际签名的 channel 参数——BLOCKER
FR-B7 样板调用中**没有 `channel` 参数**，tech-research §任务 2 实测签名也没有 channel 参数。但 AC-D3 要求"只向 Telegram channel 推送，Web SSE channel 不发送（`notification_service.notify_task_state_change` 调用时 channel 过滤生效）"。`notify_task_state_change` 当前签名没有 channel 过滤参数——`summary_channels` 配置如何传递到 NotificationService 内部过滤逻辑，spec **完全没有说明**。这是 AC-D3 的实现路径缺失，属于规范不完整。
两种可能的实现路径均未在 spec 中定义：
- (a) `notify_task_state_change` 新增 `channels: frozenset[str] | None` 参数（需修改 F101 接口）
- (b) `DailyRoutineService` 在调用前手动过滤 channel，但 NotificationService 内部 channel routing 逻辑不对外暴露

**建议**：spec 必须明确 `summary_channels` 过滤的实现路径，若需修改 `notify_task_state_change` 签名则在 §8.2 中说明；若不需要（如 NotificationService 已有 per-channel routing 机制），则引用具体接口。

**CHK-3.3** [✓] AC-F1 sha256 notification_id 格式与 F101 一致
AC-F1 定义 `notification_id = sha256("_daily_routine:{date}:ROUTINE_SUMMARY")[:16]`，与 F101 sha256 去重机制一致（F101 实施记录中 sha256 notification_id 格式）。格式对齐。

---

## 维度 4：Constitution 合规

**CHK-4.1** [✓] C2 Everything is an Event：4 新 EventType 充分性
4 个 EventType（TRIGGERED / COMPLETED / FAILED / SKIPPED）+ `RoutineCompletedPayload` schema 含 8 个字段（routine_type / date / worker_count / failed_count / attention_count / elapsed_ms / llm_elapsed_ms / fallback / summary_length）。覆盖触发时间、执行结果、耗时、LLM vs fallback 路径区分。C2 满足。

**CHK-4.2** [✗] C6 Degrade Gracefully：cron 注册失败兜底未定义——WARNING
spec 定义了 LLM 失败 fallback（FR-B3）、USER.md 解析失败 fallback（SD-1 非法值 fallback），但**未定义 APScheduler cron 注册失败（`scheduler.add_job` 抛出异常）时的兜底行为**。若 startup() 时 scheduler 不可用，daily routine 静默失败且无任何审计事件。Constitution C6 要求"任一插件/依赖不可用时，系统不得整体不可用"——此处至少应在 startup() 中记录 ERROR 日志。
**建议**：在 FR-B1 或 FR-B5 中补充 `startup()` 异常处理：cron 注册失败时写 ERROR 日志（不抛出，避免影响 gateway 启动），并在 `ROUTINE_FAILED` 或独立系统日志中记录原因。

**CHK-4.3** [✓] C6 LLM fallback + USER.md 解析失败兜底
FR-B3 fallback 路径（deterministic 模板）和 SD-1 三个解析函数（非法值 → 默认值 + WARNING log）均已定义。NFR-2 要求 fallback 在 1s 内完成。C6 在这两个场景满足。

**CHK-4.4** [✓] C8 Observability：routine 状态 / 耗时 / 失败原因审计
`ROUTINE_COMPLETED` 含 `elapsed_ms` / `llm_elapsed_ms` / `fallback`；`ROUTINE_FAILED` 含 `error_type` + `error_msg`（FR-E3）；`ROUTINE_SKIPPED` 含 `reason`（AC-B2）；`ROUTINE_TRIGGERED` 含触发时间戳（AC-E1）。全路径可审计。C8 满足。

**CHK-4.5** [✓] C9 Agent Autonomy：LLM prompt 设计留足空间
spec 未硬编码 LLM 摘要输出格式（不像 FR-B3 fallback 模板那样强制字段顺序）。F102 是系统服务，不涉及 LLM 决策 routing。C9 满足（在 F102 场景下）。

---

## 维度 5：测试覆盖完整性

**CHK-5.1** [✗] AC-B6 / AC-E3 / AC-E4 测试覆盖缺口——WARNING
§11 测试策略逐 AC 映射：
- AC-B6（cron 注册）→ `test_daily_routine_startup.py` ✓
- AC-B7（priority 提升）→ `test_daily_routine_priority.py` ✓
- **AC-E3（ROUTINE_FAILED + CancelledError re-raise）→ 无对应测试文件** ✗
- **AC-E4（attention_count in payload）→ 无对应测试文件** ✗（仅在集成测试 test_daily_routine_integration.py 覆盖 AC-E1，AC-E4 未单独列出）
- AC-T1 → `test_task_store_time_range.py` ✓

AC-E3 验证 CancelledError 显式 re-raise 是 Constitution C6 核心保证，遗漏测试覆盖是明显风险。
**建议**：将 AC-E3 / AC-E4 加入 `test_daily_routine_summary.py` 覆盖范围，或新增 `test_daily_routine_error_handling.py`。

**CHK-5.2** [✓] e2e_smoke 不新增能力域的合理性
spec §11 已明确说明原因（cron 时间依赖让 smoke 测试不稳定），通过 mock cron trigger 的集成测试替代。理由充分，与 F101 smoke 策略一致。

**CHK-5.3** [✗] 集成测试缺少 AC-B2（routine_active=false）——WARNING
`test_daily_routine_integration.py` 覆盖了 AC-B1 / AC-B2 / AC-E1 / AC-E2 / AC-F1，但 AC-B2 是"routine_active=false 时跳过"——这个场景既有 USER.md 解析（块 D），又有 event_store 写 ROUTINE_SKIPPED（块 E），是典型集成测试场景。§11 中 AC-B2 被隐含包含在 integration test 中但未显式列出，若实现者只看测试文件名可能遗漏。
**建议**：§11 中 integration test 覆盖列表显式加入 AC-B2，避免歧义。

---

## 维度 6：Scope 控制

**CHK-6.1** [✓] Out of Scope 5 项明确定义
§2.2 Out of Scope 表格列出 7 项（含归属 Feature 和理由），§12 补充了防追问说明。WeeklyRoutine / dismiss 持久化 / Blueprint / D8 / Worker↔Worker 全部有明确归属。边界清晰。

**CHK-6.2** [✓] quiet hours 多日丢失风险 mitigate 可行性
§9 风险表标注"LOW"，缓解措施是"用户调整 daily_summary_time"。这在 F102 范围内可完成（USER.md 可配置），属于合理的 trade-off，不需要 F102 增加重试机制。可接受。

**CHK-6.3** [✗] N+1 查询性能 mitigate 边界不明确——WARNING
§9 风险表提到"若 task 量 > 200 考虑 batch_get_events API（推 F107）"，但 FR-T1 和 NFR-1 中 `event_store.get_events_by_types_since` 对单 task 查询 < 200ms，若昨日有 50 个 task 则总查询可达 10s，远超 NFR-1 中"routine 触发到推送 P50 < 5s（不含 LLM）"的要求。N+1 风险与 NFR-1 性能要求之间存在**潜在冲突**，spec 未定义 task 量的安全上限，也未说明超限时的降级行为。
**建议**：在 NFR-1 或 §9 中明确 `task 量 ≤ N 时 P50 < 5s`（给出 N 的具体值），并定义超过阈值时的行为（如只汇总最近 100 个 task + 注释说明）。

---

## 维度 7：依赖关系

**CHK-7.1** [✓] 前置依赖全部已完成
§10 列出 6 个前置依赖：F101 NotificationService（✅ 74c9ab3）/ F101 NOTIFICATION_DISPATCHED EventType（✅）/ F084 USER.md SoT（✅）/ F086 AutomationSchedulerService（✅）/ M3 SqliteEventStore（✅）/ F081 provider_router cheap alias（✅）。均标注完成状态。

**CHK-7.2** [✗] OQ-2 cheap alias 可用性是**已知风险但未在依赖表中标注**——WARNING
§13 OQ-2 明确说明"plan Phase 0 需确认 cheap alias 在当前 ProviderRouter 配置中已定义"，但 §10 依赖表中 F081 provider_router 标注 ✅ 未区分"provider_router 本身存在"和"cheap alias 已配置"。若 cheap alias 未配置，LLM 路径永远 fallback，AC-B1（"汇总摘要（LLM 路径或 fallback 路径）"）仍满足，但无法独立验收 LLM 路径。此问题已在 OQ-2 中记录，属于已知项，但风险等级应在依赖表中显式标注。
**建议**：在 §10 依赖表中将 provider_router 行备注"需 Phase 0 确认 cheap alias 已配置"。

**CHK-7.3** [✓] 后续 Feature 依赖描述准确
F103 依赖"F102 DailyRoutine 架构描述"（Blueprint 更新内容），F107 依赖"DailyRoutineService DI 接口稳定性"，WeeklyRoutine 依赖"DailyRoutineService 基础框架可扩展"——三个描述均与 spec 设计一致，无矛盾。

---

## 总结

| 维度 | 通过 | BLOCKER | WARNING |
|------|------|---------|---------|
| 维度 1：可测性 | CHK-1.1 / 1.3 / 1.5 | CHK-1.2 | CHK-1.4 |
| 维度 2：架构整洁度 | CHK-2.2 / 2.3 | — | CHK-2.1 / 2.4 |
| 维度 3：F101 边界一致性 | CHK-3.1 / 3.3 | CHK-3.2 | — |
| 维度 4：Constitution 合规 | CHK-4.1 / 4.3 / 4.4 / 4.5 | — | CHK-4.2 |
| 维度 5：测试覆盖 | CHK-5.2 | — | CHK-5.1 / 5.3 |
| 维度 6：Scope 控制 | CHK-6.1 / 6.2 | — | CHK-6.3 |
| 维度 7：依赖关系 | CHK-7.1 / 7.3 | — | CHK-7.2 |

**总计**：21 项检查，**15 项通过，2 项 BLOCKER，7 项 WARNING**

### BLOCKER（必须在 GATE_DESIGN 前修复）

1. **CHK-1.2**：AC-B4 quiet hours 测试可执行性未明确——测试策略不说明使用真实 NotificationService 还是 mock，导致 PASS/FAIL 判定依赖实现方理解，无法保证 AC-B4 真实验收。
2. **CHK-3.2**：AC-D3 `summary_channels` 过滤的实现路径缺失——`notify_task_state_change` 当前签名无 channel 参数，spec 未说明如何将 channel 过滤传递到 NotificationService。此处是规范空白，实现者无法按 spec 独立完成 AC-D3。

### 强烈建议修改的前 3 条

1. **BLOCKER CHK-3.2**（最高优先级）：在 §8.2 或新增 §8.2.1 中明确 `summary_channels` 过滤路径——是新增 `notify_task_state_change` 的 `channels` 参数，还是 DailyRoutineService 在调用前手动路由。若需修改 F101 接口则必须在 spec 中显式声明。
2. **BLOCKER CHK-1.2**：在 AC-B4 正文或 §11 中补充一句："AC-B4 集成测试使用真实 NotificationService + mock SnapshotStore（返回含 quiet hours 的 USER.md 内容）验证 `filtered=True` 的 NOTIFICATION_DISPATCHED 事件写入。"
3. **WARNING CHK-5.1**：AC-E3（ROUTINE_FAILED + CancelledError re-raise）是 Constitution C6 核心保证，必须有明确测试覆盖。建议将其加入 `test_daily_routine_summary.py` 或新建 error handling 测试文件，并在 §11 中显式映射。
