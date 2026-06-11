# F119 已合入功能 e2e_live 端到端补全 — Spec

> 模式：spec-driver story（5-phase，跳过 research，分析现有代码上下文）
> 来源：2026-06-08 并行合并集成 review（`m6-postmerge-integration-review`）——
> 多个已交付 Feature（F104/F116/F123/F124）只有单测、无 e2e_live 覆盖；
> F123↔F124 互补链、F099/F100 同样缺 e2e。
> baseline：master HEAD `02e139fd`。

## 1. Story（用户/工程视角）

作为 OctoAgent 的维护者，我需要让 M5→M6 已合入的关键功能（文件版本历史、通知持久化、
出站 SSRF 防护、工具结果威胁扫描）在 **e2e_live 套件**里有端到端覆盖——而不只是
单测。单测各自 mock 了上下游（独立 sqlite conn、stub scanner、monkeypatch DNS），
能验证"单元逻辑对"，但验证不了"装配链路对"：

- bootstrap 后 `store_group.artifact_store` 真带独立 `versionable_conn` 吗？
- bootstrap 后 `tool_broker` 真注入了 `content_scanner` 吗？
- Files API 路由真能从 `app.state.store_group` 取到版本、且主响应真不泄漏技术字段吗？
- NotificationService 真能跨"重启"（新实例 rehydrate）恢复 dismiss/active 吗？
- web.fetch 工具入口真接了 SSRF 校验吗？SSRF 拦截的 error 真流经 F124 扫描器吗？

e2e_live 的价值正是"真跑 OctoHarness 全 11 段 bootstrap + 真 SQLite 文件 + 主路径直调"，
把这些装配缝补上——装配漂移时 e2e 失败、单测仍 PASS。

## 2. 范围

### In scope（必做）

| 标的 | e2e 验证目标 | 测试文件 |
|------|-------------|---------|
| **F104 文件工作台** | versionable 写多版本→版本内容可取回（非仅计数器）；Files API 两级导航；diff current+previous 无技术字段泄漏；并发 versionable 写不串/不丢版本 | `test_e2e_file_workbench.py` |
| **F116 通知持久化** | dismiss/active 落盘→新 NotificationService 实例 rehydrate→已读不重现 + active 恢复；跨通道 dismiss 统一 | `test_e2e_notification_persist.py` |
| **F123 出站 SSRF** | web.fetch/`_fetch_browser_page` 传入云元数据/私网/loopback/CGNAT 被拦（发包前预检，DNS 解析到私网也拦）；302 逐跳 re-validate 机制 | `test_e2e_ssrf_guard.py` |
| **F124 工具结果威胁扫描** | tool 结果含注入 payload→broker `_finalize_result` 扫描→`security_findings` 挂载 + `TOOL_RESULT_THREAT_FLAGGED` 事件；**只标注不 block**；F125 收紧后真实技术文档负样本 0 误报 | `test_e2e_tool_result_threat_scan.py` |
| **F123↔F124 互补链** | SSRF 拦截抛 `UnsafeUrlError`→broker exception 分支→`_finalize_result` error 通道流经 F124 扫描器（防御纵深，含 error 文本带注入时产 finding） | `test_e2e_tool_result_threat_scan.py` |

### Stretch（可选，主体跑通且时间允许才做）

| 标的 | e2e 验证目标 | 测试文件 |
|------|-------------|---------|
| F099 ask_back resume | 三工具（ask_back/request_input/escalate_permission）经 broker 真路径 + CONTROL_METADATA_UPDATED 事件 | `test_e2e_askback_decisionloop.py` |
| F100 decision loop | `RuntimeControlContext.force_full_recall` override → recall planner auto 决议 | 同上 |

### Out of scope

- 真打 LLM 的 agent loop（沿用 F087 GATE_P3_DEVIATION：直调主路径绕 LLM 不确定性）。
- 跨 runtime 真触发 A2A（F087 已界定，推迟 F088+）。
- 改动任何 production 代码（packages/ apps/ src 零行改动）。
- 改 13 域 DOMAIN_REGISTRY / `octo e2e` CLI（新文件用 `e2e_full` marker 被 `pytest -m e2e_full`
  自动收集，无需进 CLI 域清单——CLI 域是固定 13 个能力域，本批是回归补全测试）。

## 3. Acceptance Criteria（AC↔test 显式绑定，SDD 强化）

> 每条 AC 紧邻标注对应 test 函数；verify 阶段机械校验该 test 存在且 PASS。
> 每个 case ≥ 2 独立断言点（沿用 F087 FR-11）。

### F104 文件工作台（`test_e2e_file_workbench.py`）

- **AC-104-1** versionable 写 3 版本后，`get_current_and_previous` 返回当前版 + 上一版的
  **真实内容**（current.content / previous.content 各自等于写入的对应版本内容，证明非仅计数器）。
  `[@test test_file_workbench_versions_retrievable]`
- **AC-104-2** Files API 两级导航：`GET /api/files/tasks` 含该 task；
  `GET /api/files/tasks/{id}/logical-files` 只返回 version_count≥2 的逻辑文件（单版本不出现）。
  `[@test test_file_workbench_two_level_navigation]`
- **AC-104-3** `GET /api/files/tasks/{id}/diff?logical_file_id=...` 响应含 current+previous 内容，
  且响应 JSON **不含** `artifact_id` / `storage_ref` / `hash` 技术字段（SC-004 无泄漏）。
  `[@test test_file_workbench_diff_no_technical_field_leak]`
- **AC-104-4** 并发 N 个 versionable 写同一 logical_file（`asyncio.gather`）→ 版本号是
  1..N 连续唯一（`_write_lock` + UNIQUE 约束保证不串/不丢）。
  `[@test test_file_workbench_concurrent_writes_no_version_clash]`

### F116 通知持久化（`test_e2e_notification_persist.py`）

- **AC-116-1** service A `dismiss(id)` 落盘 → 新建 service B + `rehydrate()` →
  `B.is_dismissed(id)` 为 True（跨"重启"不重现）。
  `[@test test_notification_dismiss_survives_restart]`
- **AC-116-2** service A `_record_active(...)` 落盘 → service B rehydrate →
  `B.list_active(session_id)` 恢复该条（active 跨重启持久化）。
  `[@test test_notification_active_survives_restart]`
- **AC-116-3** active 一条后 dismiss 它 → 新实例 rehydrate → `list_active` 不再返回它
  （H2 _dismissed_set 过滤 + _notified_set 种子防重复派发）。
  `[@test test_notification_dismissed_filtered_after_restart]`
- **AC-116-4** 跨通道：`dismiss(id, source="web")` 后 `is_dismissed(id)` True；
  落盘 source 字段正确（同一 store → 跨通道一致）。
  `[@test test_notification_dismiss_cross_channel]`

### F123 出站 SSRF（`test_e2e_ssrf_guard.py`）

- **AC-123-1** `pack_service._fetch_browser_page(云元数据/私网/loopback/CGNAT url)` 抛
  `UnsafeUrlError`（参数化覆盖 169.254.169.254 / 10.x / 127.0.0.1 / 100.64.x）。
  `[@test test_ssrf_fetch_browser_page_blocks_internal]`
- **AC-123-2** 经 broker 真路径 `tool_broker.execute("web.fetch", {url:私网})` →
  `ToolResult.is_error=True`（web.fetch 工具入口真接了 SSRF 校验，发包前预检）。
  `[@test test_ssrf_web_fetch_via_broker_blocked]`
- **AC-123-3** DNS 解析到私网也拦：hostname 经 monkeypatch `_resolve_host` 返回私网 IP →
  `ensure_url_safe` 抛 `UnsafeUrlError`（域名→私网，非仅字面量 IP）。
  `[@test test_ssrf_hostname_resolving_to_private_blocked]`
- **AC-123-4** 302 逐跳 re-validate 机制：`_ssrf_request_hook(httpx.Request(私网url))` 抛
  `UnsafeUrlError`（每跳前校验，重定向进内网被拦）。
  `[@test test_ssrf_redirect_hook_revalidates_each_hop]`

### F124 工具结果威胁扫描（`test_e2e_tool_result_threat_scan.py`）

- **AC-124-1** 注册一个返回含 CONTEXT pattern payload 的 stub 工具 → `broker.execute` →
  `ToolResult.security_findings` 非空 + `ToolResult.is_error=False`（**只标注不 block**）。
  `[@test test_threat_scan_flags_but_does_not_block]`
- **AC-124-2** 同上调用 → events 表含 `TOOL_RESULT_THREAT_FLAGGED` 事件，
  payload 含 pattern_id + content_hashes（无原文）。
  `[@test test_threat_scan_emits_flagged_event]`
- **AC-124-3** F125 收紧验证：注册返回真实技术文档（k8s/安全文章片段）的 stub 工具 →
  `broker.execute` → `security_findings` 为空（反"狼来了"，CONTEXT pattern 不误报）。
  `[@test test_threat_scan_false_positive_clean_on_real_docs]`
- **AC-124-4（F123↔F124 链）** 注册一个 `raise UnsafeUrlError` 的 stub 工具，用 spy 包裹
  `broker._content_scanner` → `broker.execute` → (1) `is_error=True`（SSRF 语义）+
  (2) scanner 被 error 文本调用过（error 通道流经 F124 扫描器，防御纵深）；
  另证 error 文本含注入时 `security_findings` 非空（error 通道真能产 finding）。
  `[@test test_ssrf_error_flows_through_threat_scan]`

## 4. 约束（硬规则）

- **0 regression vs `02e139fd`**：全量回归不低于 baseline（`pytest -m e2e_full` + 全量 `pytest`）。
- **新 e2e 自身 PASS**；`pytest -m e2e_smoke` 必过（不被本批破坏）。
- **不改 production 代码**：packages/ apps/ src 零行改动。
- **尽量不改 conftest/fixture**：新测试为独立文件，复用现有 `octo_harness_e2e` /
  `bootstrapped_harness` 模式 + 直调主路径。若必须改 conftest/fixture → 触发 Codex review；
  纯测试新增可跳过 review（CLAUDE.local.md）。
- **守 H1**：断言尽量走主 Agent 可见路径（broker.execute / Files API / NotificationService 公共方法）。
- **与 F105 并行**：谁后合入谁 rebase；F105 行为零变更，本 e2e 测的现有行为应仍 PASS。
- **不主动 push**：完成后归总报告 + completion-report，等用户拍板。

## 5. 验证方式

1. `cd octoagent && uv run --no-sync python -m pytest apps/gateway/tests/e2e_live/test_e2e_file_workbench.py
   test_e2e_notification_persist.py test_e2e_ssrf_guard.py test_e2e_tool_result_threat_scan.py -p no:randomly -q`
   → 全 PASS。
2. `uv run --no-sync python -m pytest -m e2e_smoke -q` → 全 PASS（未被破坏）。
3. 全量回归对照 baseline（同口径），0 新增 failure。
4. completion-report 标注每条 AC↔test 绑定的实测结果。
