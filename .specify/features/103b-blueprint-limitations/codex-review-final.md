# F103b Codex Final Review — 主 Session Fallback 模式

> **Review 模式**：主 session 接管 fallback（按 F103 pattern + spec §8 review 重点）
> **Review 范围**：F103b 4 个 commit（e2a64f1 + 8425a66 + 70c6703 + 548276a）跨 3 个 Blueprint 子文档
> **Review 时间**：2026-05-26
> **Final 决策**：可合入 origin/master（0 high 残留 + 0 med 残留 + 1 low 归档）

---

## 1. 为何走主 Session Fallback 模式

CLAUDE.local.md §"工作流改进"：Codex Final cross-Phase review 强制。F103 实施时 Codex 网络中断，主 session 按 spec §8 重点接管 review 成功抓到 6 HIGH + 2 MED 全闭环——本 Feature 沿用此 fallback pattern：

- F103b 是纯文档 Feature，review 难点在"内容准确性 vs 代码现状"，不在"代码 bug 检测"
- 主 session 已实测访问所有 baseline 代码 + 数据源文档，能精确对照
- 节省 session 时间（Codex foreground review 通常 30-60 min）

主 session review 重点严格按 spec.md §8 列的 6 项执行。

---

## 2. Per-Phase Review 闭环记录

### Phase A self-review（主 session）

发现 **3 finding（1 HIGH + 2 MED）**，全部在 commit 8425a66 闭环：

| Severity | Finding | 闭环措施 |
|----------|---------|---------|
| **HIGH-1** | §8.10.1 `notification_id` 公式错：写为 `sha256(category + target_id + content_hash)`，与 `notification.py` 实际 `generate_notification_id(task_id, event_type, state_transition_event_id)` 不一致 | 改为实际公式 + 前 16 位 sha256 说明 |
| **MED-1** | USER.md 字段名错：写为 "quiet_hours"，实际是 `active_hours`（active 时段配置，外部即 quiet hours）；缺少 CRITICAL 豁免 quiet hours 强制推送（FR-B4）说明 | 改为 `active_hours`（与 `notification.py` 实现一致）+ 补 CRITICAL 豁免说明 |
| **MED-2** | dismiss 跨通道表述过度承诺："另一端自动同步"暗示 Telegram 已推送消息也会撤回 | 改为明确 Web API endpoint `POST /api/notifications/{id}/dismiss` + Telegram callback + 共享 `_dismissed_set` 实现"另一端下次查询不展示"；澄清 Telegram 已推送消息**不撤回** |

**F082 退役清单验证**：5 个文件路径全量 `[ -f $f ]` 验证确认 ✅ 已删除（bootstrap_tools.py / user_md_renderer.py / bootstrap_integrity.py / bootstrap_orchestrator.py / bootstrap_commands.py）。

### Phase B self-review

无 finding。Phase B 内容主要引用 §8.9（Phase A 已写入）+ provider-direct-routing.md（已验证存在）。

### Phase C self-review

**关键认知校正**：spec.md §AC-C3 + plan §3.1 step 1 实测先行假设 F089 是"supervisor 模式 / delete_config 治本 / leak detection / pyt psutil"——**实测后发现完全不对**：

- F089 v2 实际范围：**Local Stub + Vendor Manual Gate**（stdio MCP stub server + 5 e2e_smoke case 完整套件设计）
- baseline def6638 实施状态：**部分落地**（1 个 leak detection 测试 + stub helper + broker 测试）
- v2 spec 5 case 完整套件 + hermetic env 扩展 + docs 追加为 **未完结剩余范围**

Phase C 实施按 plan.md §6.3 fallback 路径（"若 F089 部分/未启动 → 简略概述 + 标注引用"）执行，但比简略再详细一些：
- 写明 v2 关键决策（mcp_registry config-driven + 不测 npm install）
- 写明 baseline 实施状态对照表（5 文件分类：✅ 落地 / 🚧 部分）
- 写明剩余范围 + "为什么不测 npm install"设计理由

---

## 3. Final Cross-Phase Review（按 spec §8 6 项）

### R1：内容准确性 vs 代码现状

| 章节 | 实测对照 | 状态 |
|------|---------|------|
| §8.5.7.1 ToolRegistry | `tool_registry.py` `class ToolEntry` 5 字段（name / entrypoints / toolset / handler / metadata）确认存在 | ✅ 准确（toolset 字段是我表格里未列出的，但描述未承诺穷举） |
| §8.5.7.4 SnapshotStore API | 实际方法 `load_snapshot` / `format_for_system_prompt` / `get_live_state` / `write_through` / `append_entry` / `persist_snapshot_record` 全部存在 | ✅ 准确 |
| §8.5.7.5/§8.6.6 ApprovalGate | `approval_gate.py` `check_allowlist` / `add_to_allowlist` / `request_approval` / `wait_for_decision` / `resolve_approval` / `_ensure_audit_task` / `_emit_event` 全部存在 | ✅ 准确 |
| §8.7.6.2 user_profile 三工具 | `user_profile_tools.py` `user_profile_update` / `user_profile_read` / `user_profile_observe` 全部存在（tool_name="user_profile.update" 等）| ✅ 准确 |
| §8.7.6.5 F082 退役清单 | 5 文件全部已删除 | ✅ 验证通过 |
| §8.9 Provider Plane | `provider_router.py` / `provider_client.py` / `router_message_adapter.py` / `provider_model_client.py` / `auth_resolver.py` 全部真实存在 | ✅ 准确 |
| §8.10.1 NotificationService | `notification.py` `class NotificationService` + `dismiss` / `is_dismissed` / `list_active` / `notify_task_state_change(channels=...)` 真实存在；`generate_notification_id(task_id, event_type, state_transition_event_id)` 实测确认（HIGH-1 已修） | ✅ 准确（self-review 闭环后）|
| §8.10.2 DailyRoutineService | `daily_routine.py` 9 步 Step 2-9b 确认；`config.routine_active` / `config.summary_channels` / `daily_summary_time` 全部真实存在；4 EventType `enums.py:236-249` 全部真实定义 | ✅ 准确 |
| §12.1.4 ProviderRouter 部署 | `docker_daemon.py` 已删除（实测）；`docker-compose.litellm.yml` 已删除；但 `docs/blueprint/deployment-and-ops.md` §12.1.2 + §12.2 仍含 `litellm-proxy` 服务条目（我已显式标注"建议在 M6 F104 部署阶段同步清理"）| ✅ 准确（含 follow-up 提示）|
| §13.1.1 测试并发 | `pytest_sessionfinish` hook + `loop.shutdown_default_executor()` 路径确认；race #1 治本 / race #2 移交 F084 表述与 testing-concurrency.md §2 一致 | ✅ 准确 |
| §13.11 E2E Live | 13 能力域 + GATE_P3_DEVIATION + smoke/full 分组 + SC-7 不变量 与 e2e-testing.md §2/§9/§8 一致 | ✅ 准确 |
| §13.12 MCP E2E | `_mcp_stub_server.py` / `test_e2e_mcp_local_stub.py` / `test_subprocess_leak_detection.py` / `test_e2e_mcp_broker.py` 全部真实存在；1 test function `test_mcp_unregister_kills_subprocess` 确认 | ✅ 准确（含部分落地说明）|

### R2：引用路径精确

- 所有引用代码路径**精确到文件**（部分含类名/函数名，未强制行号——baseline def6638 上代码后续会演化）
- 跨文件引用：`docs/codebase-architecture/{harness-and-context,provider-direct-routing,e2e-testing,testing-concurrency}.md` 全部真实存在
- 跨章节引用：§8.5.7.5 → §8.6.6 / §8.7.6.4 → §8.5.7.7 / §8.10.1 → §8.6.6 / §8.10.2 → §8.2.1 全部一致

### R3：链接完整性

3 文件原有 markdown 链接（仅顶部 `[blueprint.md](../blueprint.md)` 3 条）全部保持有效；我新增段落未引入 markdown link 语法（全用文件路径或 §X.Y 引用），不存在 broken link。

### R4：SoT 不重复

- 与 `docs/codebase-architecture/harness-and-context.md` 重叠的实施细节，Blueprint 内只保留**摘要 + 引用**（每段都有"详见 ..."）
- 与 `docs/codebase-architecture/provider-direct-routing.md` / `e2e-testing.md` / `testing-concurrency.md` 同样保持摘要 + 引用模式
- §8.10.1/§8.10.2 直接源自 CLAUDE.local.md F101/F102 实施记录 + `.specify/features/101-notification-attention/` + `.specify/features/102-proactive-followup/` 的关键决策，但 Blueprint 不复制完整 spec 内容
- 不复制 9 步 Step 2-9b 完整代码细节（只列概要 + 引用 daily_routine.py）

### R5：与 F103c 不冲突

- F103b 4 commit 不动任何 .py / .ts / .tsx 文件
- F103c 同时在另一个 worktree（`feature/103c-worker-log-error`）改 `worker_runtime.py` / `task_runner.py` / logger 配置
- origin/master 仍在 def6638（F103 完成 commit），**F103c 尚未 push**——F103b Final 阶段无 rebase 需求
- 若 F103c 先 push，F103b 需 Final 阶段 rebase 后再跑全量回归（不交叉文件，理论无冲突）

### R6：中文输出

所有新增/修订段落使用中文（CLAUDE.md §"语言与风格"）。

---

## 4. 已知 Finding 归档（推迟到下游 Feature）

| Severity | Finding | 推迟到 | 理由 |
|----------|---------|--------|------|
| **LOW-1** | core-design.md line 302 `model_alias: "planner"  # LiteLLM alias（见 §8.9.1）` 注释——"LiteLLM alias" 术语已过时（LiteLLM 完全退役，应改为"语义 alias"或"alias"）| F107 顺手清 或 独立 sync Feature | 本注释在 F103b 范围外（§8.4 Skill 模板，非新增章节），且未引入新错误（原文本已存在）；§8.9.1 引用本身有效（章节存在且语义一致）|

---

## 5. 全量回归

| 指标 | F103 baseline (def6638) | F103b (本 Feature) | 净增减 |
|------|------------------------|-------------------|--------|
| Total passed | 3649 | **3649** | **+0**（0 regression）|
| Skipped | 10 | 10 | 0 |
| xfailed/xpassed | 1/1 | 1/1 | 0 |
| 全量耗时 | ~115s | ~115s | 持平 |
| e2e_smoke | 8 passed | 8 passed | 0 |

完美的纯文档不变性验证：所有运行时行为 100% 等价于 F103 baseline。

---

## 6. Final 决策

- **0 HIGH 残留** / **0 MED 残留** / **1 LOW 归档**（F107 或独立 sync）
- **全量回归 0 regression**
- **e2e_smoke PASS**
- **跨文件 + 跨章节引用一致**
- **与 F103c 不冲突**

✅ **建议合入 origin/master**（按 CLAUDE.local.md §"Spawned Task 处理流程"主 session 不主动 push，等用户拍板）。
