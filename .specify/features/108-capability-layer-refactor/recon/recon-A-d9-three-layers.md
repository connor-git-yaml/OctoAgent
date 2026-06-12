# Recon A：D9 三层职责重叠（broker / octo_harness / capability_pack）

> 来源：Phase 1 并行 recon agent A（very thorough），基线 d6148903。
> 简称：**broker** = `packages/tooling/src/octoagent/tooling/broker.py` (773 行)；**harness** = `apps/gateway/src/octoagent/gateway/harness/octo_harness.py` (1388 行)；**cap_pack** = `apps/gateway/src/octoagent/gateway/services/capability_pack.py` (2174 行)

---

## 1. 每个文件的真实职责清单

### broker.py（773 行）—— 工具执行运行时核心
| 职责组 | 成员 | 行号 |
|---|---|---|
| 模块级常量 | `_FINALIZE_HASH_PREFIX_CAP` | 59 |
| **威胁扫描纯函数**（CPU 块，供 `to_thread` 卸载） | `_scan_collect_findings`（内含 `_bounded_hash` / `_collect`） | 62–109 |
| 构造/依赖注入 | `ToolBroker.__init__`（注册表 + hooks + 5 个外部依赖） | 123–154 |
| **注册与发现** | `register` / `try_register` / `registry_diagnostics`(prop) / `discover` / `get_tool_meta` / `unregister` | 160–276 |
| **Hook 管理** | `add_hook`（按类型分类 + priority 排序） | 282–308 |
| **工具执行编排**（查找→started 事件→权限→before hook→执行→after hook→completed/failed→finalize） | `execute` | 314–535 |
| policy 检测 | `_has_policy_checkpoint` | 541–547 |
| handler 调用（sync→async + 超时） | `_invoke_handler` | 549–575 |
| 事件发射 | `_emit_tool_event` / `_emit_started_event` / `_emit_completed_event` / `_emit_failed_event` | 577–685 |
| **结果 finalize + 威胁扫描挂载** | `_finalize_result` | 687–724 |
| 威胁事件发射 | `_emit_threat_flagged_event` | 726–765 |
| 事件持久化 | `_persist_event` | 767–773 |

### octo_harness.py（1388 行）—— FastAPI lifespan 装配器（纯 wiring）
| 职责组 | 成员 | 行号 |
|---|---|---|
| 模块级 shutdown helper | `_final_drain_background_tasks` | 44–81 |
| 构造/DI 字段 | `OctoHarness.__init__` | 123–157 |
| **三入口** | `bootstrap`（调度 11 段）/ `shutdown` / `commit_to_app`(no-op) | 161–284 |
| 段 1：路径 + update service | `_bootstrap_paths` | 288–313 |
| 段 2：DB / stores / migration | `_bootstrap_stores` | 315–351 |
| 段 3：ToolRegistry scan + SnapshotStore | `_bootstrap_tool_registry_and_snapshot` | 353–408 |
| 段 4：owner profile sync | `_bootstrap_owner_profile` | 410–432 |
| **段 5：runtime services（构造 ToolBroker + ApprovalManager + 渠道 registry）** | `_bootstrap_runtime_services` | 434–652 |
| 段 6：LLM / ProviderRouter / FallbackManager | `_bootstrap_llm` | 654–762 |
| **段 7：构造 CapabilityPackService** | `_bootstrap_capability_pack` | 764–818 |
| **段 8：MCP + ApprovalGate + cap_pack.startup()** | `_bootstrap_mcp` | 820–949 |
| 段 9：executors / TaskRunner / Notification / cap_pack.refresh() | `_bootstrap_executors` | 951–1177 |
| 段 10：watchdog / observation routine | `_bootstrap_optional_routines` | 1179–1278 |
| 段 11：control plane / automation | `_bootstrap_control_plane` | 1280–1388 |

### capability_pack.py（2174 行）—— 工具注册聚合 + 治理面 + builtin tool 业务逻辑宿主
| 职责组 | 成员 | 行号 |
|---|---|---|
| **SSRF request hook** | `_ssrf_request_hook` | 73–77 |
| 内部数据模型 | `_WorkerPlanAssignment` / `_WorkerPlanProposal` / `_ResolvedWorkerBinding` | 80–113 |
| profile 等级判定 | `_PROFILE_LEVELS` / `_profile_allows` | 119–126 |
| browser-support import | `_BrowserLinkRef`/`_BrowserSessionState`/`_BrowserSnapshot`/`_HtmlSnapshotParser`/`_truncate_text` | 129–135 |
| **ApprovalOverride 内存缓存** | `_ApprovalOverrideMemoryCache` | 138–185 |
| 构造 | `CapabilityPackService.__init__` | 191–244 |
| 属性 + bind_* wiring | `tool_broker`/`skill_discovery`/`bind_task_runner`/`bind_delegation_plane`/`bind_mcp_registry`/`bind_mcp_installer`/`bind_approval_gate`/`mcp_registry`/`approval_override_cache` | 246–292 |
| **生命周期** | `startup` / `refresh`（含 `_resolve_entrypoints_for`）/ `pack_revision` / `invalidate_pack` / `get_pack` | 294–415 |
| **工具上下文构建（Core/Deferred 分流）** | `build_tool_context` | 425–469 |
| worker binding 解析 | `resolve_worker_binding` / `resolve_worker_type_for_profile` | 471–556 |
| **工具选择/挂载编排** | `select_tools` / `resolve_profile_first_tools`（mount/defer/blocked 三态） | 558–825 |
| bootstrap context 渲染 | `render_bootstrap_context` / `_resolve_owner_profile` / `capability_snapshot` | 827–907 |
| worker plan review/apply | `review_worker_plan` / `apply_worker_plan` / `build_skill_registry_document` | 909–1052 |
| **builtin tool 注册** | `_register_builtin_tools` | 1054–1086 |
| worker profile / bootstrap 模板 | `_build_worker_profiles` / `_build_bootstrap_templates` | 1088–1152 |
| 静态 helper | `_builtin_worker_type_from_profile_id` / `_coerce_tool_profile` / `_dedupe_preserve_order` / `_profile_first_candidate_tool_names` / `_profile_first_discovery_tool_names` / `_resolve_profile_first_source_kind` | 1154–1226 |
| worker 拆分/子任务启动 | `_split_worker_objectives` / `_effective_tool_profile_for_objective` / `_build_worker_assignment` / `_launch_child_task` | 1228–1394 |
| pack 范围过滤/限制 | `_resolve_fallback_toolset_from_pack` / `_resolve_scope_skill_selection` / `_resolve_profile_skill_selection` / `_skill_item_selected` / `_skill_item_state` | 1395–1514 |
| MCP 元数据/策略 | `_mcp_install_summary` / `_enrich_mcp_metadata` / `_resolve_mcp_mount_policy` / `_mcp_tool_enabled_by_default` | 1516–1564 |
| pack scope 过滤 | `_filter_pack_for_scope` / `_restrict_selection_to_pack` / `_resolve_project_context` | 1566–1722 |
| **工具可用性裁决** | `_resolve_tool_availability` / `_resolve_tool_availability_reason` / `_resolve_tool_install_hint` | 1723–1838 |
| **browser-session 业务逻辑** | `_parse_browser_snapshot` / `_fetch_browser_page` / `_browser_session_scope_key` / `_browser_session_id` / `_get_browser_session` / `_require_browser_session` / `_browser_open_session` / `_close_browser_session` / `_browser_session_payload` | 1845–1964 |
| TTS 业务逻辑 | `_tts_binary` / `_tts_command` | 1966–1984 |
| **web 搜索业务逻辑** | `_search_web` / `_is_ddg_anomaly_page` / `_parse_duckduckgo_results` / `_normalize_search_result_url` / `_strip_html_text` | 1986–2106 |
| 文件检视业务逻辑 | `_inspect_pdf_file` / `_inspect_image_file` | 2108–2174 |

**关键观察**：cap_pack 是超载类——同时是 (a) 工具注册聚合器、(b) 治理面（工具选择/可用性）、(c) builtin tool **业务逻辑实现宿主**（browser/tts/web search/file inspect，1845–2174 共 ~330 行）。这是 D9 债的主要质量源。

---

## 2. 三层之间的具体重叠点

### 重叠点 A：工具 schema 注册/解析
- **broker**：`register`(160–184) / `try_register`(186–223) / `discover`(230–247) / `get_tool_meta`(249–261)——注册表**权威存储**。
- **cap_pack**：`_register_builtin_tools`(1054–1086) 调 `reflect_tool_schema`(1085) + `try_register`(1086)；`refresh`(306–389) 把 `discover()`(311) 结果**二次包装**成 `BundledToolDefinition`(336–354) + `_tool_index.rebuild`(312)。
- 结论：schema 产生（reflect）在 cap_pack，存储（registry）在 broker，二次索引（ToolIndex）又在 cap_pack。同一份工具元数据两层各有一份表示。

### 重叠点 B：policy / 权限检查
- **broker**：`execute` 内 `check_permission` 内联(370–392)；`_has_policy_checkpoint`(541–547) 残留。权限**执行点**。
- **cap_pack**：`_ApprovalOverrideMemoryCache`(138–185) + `approval_override_cache` 属性(290–292)，构造时接收同一缓存实例(217)。
- **harness**：`_bootstrap_runtime_services` 构造 `ApprovalManager` + `ApprovalOverrideCache`(608–625) 注入 broker(628–634)，又把**同一个** cache 注入 cap_pack(781–788)。
- 结论：权限**状态（override cache）**横跨三层共享同一实例；`_ApprovalOverrideMemoryCache` 类可下沉 tooling 层。

### 重叠点 C：审批 gate
- **cap_pack**：`bind_approval_gate`(275–283) + `__init__` `self._approval_gate=None`(220) + `_register_builtin_tools` 注入 ToolDeps(1074)。
- **harness**：`_bootstrap_mcp`(864–928) 构造 `ApprovalGate` + `_approval_sse_push_fn` 闭包(880–923) 并 `bind_approval_gate`(925)。
- 结论：gate **构造在 harness、持有/分发在 cap_pack**——cap_pack 充当 late-binding 中转站。

### 重叠点 D：结果 finalize / 截断
- **broker**：`_finalize_result`(687–724)；截断由注册的 `LargeOutputHandler` after-hook 在 `execute`(496–520) 完成；注释(533–535) 明确 after-hook 可能已截断。
- **cap_pack**：`_browser_session_payload` 用 `_truncate_text`(1958)；`_fetch_browser_page` 硬截 `html[:500_000]`(1871)；`build_tool_context` 截断描述 80 字符(459)。
- **harness**：注册 `LargeOutputHandler`(635–642)，`context_window_tokens=128_000`。
- 结论：**截断逻辑分散**——通用截断走 broker after-hook（harness 装配），browser/web 工具在 cap_pack 内又各自截断，无统一策略。

### 重叠点 E：threat scan / SSRF
- **broker**：`_scan_collect_findings`(62–109) + `_finalize_result`(705–713) + `_emit_threat_flagged_event`(726–765)——**工具结果内容**扫描（注入 `ContentThreatScanProtocol`）。
- **cap_pack**：`_ssrf_request_hook`(73–77) + `_fetch_browser_page` 调 `async_ensure_url_safe`(1864) + redirect hook(1868)；`_search_web` 同款(2017)——**出站 URL** 防护。
- **harness**：注入 `ContentThreatScanService()` 到 broker(633)。
- 结论：两类"威胁扫描"分居两层，概念同属 safety scan 但实现完全分离（语义上各自合理）。

### 重叠点 F：工具执行编排
- **broker**：`execute`(314–535)——单次工具调用完整编排。
- **cap_pack**：`select_tools`(558–585) / `resolve_profile_first_tools`(587–825)——执行前工具集编排；`_launch_child_task`(1260–1394)——子任务启动。
- 结论：理论正交，但 cap_pack 的 `_resolve_tool_availability`(1723–1773) 依赖 broker 注册表外的运行时状态（task_runner/mcp_registry/browser_sessions），跨层耦合。

### 重叠点 G：错误包装
- **broker**：`execute` 统一包装 timeout/exception/hook failure/permission denied 成 `ToolResult(is_error=True)`(345–351, 361–367, 386–392, 471–493, 507–513)。
- **cap_pack**：browser/web/file 工具直接 raise 多种异常（1904, 2037/2055, 1864 UnsafeUrlError, 2111/2114, 1973），**依赖 broker except 兜底包装**。
- 结论：无重复包装但属隐式契约耦合；cap_pack raise 风格不统一。

---

## 3. 调用方向 / 层次倒置

**Import 方向**：broker → 仅 `octoagent.core.*` + 包内（✅ 最低层纯净，无对上 import）；cap_pack → `octoagent.tooling`（✅ 高→低）；harness → `octoagent.tooling` + 运行时构造 cap_pack（✅）。

**运行时**：harness 构造 broker(628) → 构造 cap_pack 注入 broker(783) → `cap_pack.startup()`(930) → `_register_builtin_tools` → `broker.try_register`(1086) + `register_all(broker, deps)`(1077)。

**倒置判定**：
- ❌ 无 broker→上层倒置。
- ⚠️ harness ↔ main 循环依赖规避：大量 `from ..main import` / `_main_module`（184、294、322、453、670 等）——延迟 import + 模块属性引用以保留 monkeypatch。
- ⚠️ **真正的结构问题**：cap_pack 把 builtin-tool 业务逻辑（browser/web/tts/file, 1845–2174）塞进治理层——注册已委派 `builtin_tools.register_all`，但业务方法留在 cap_pack 主类，由 `ToolDeps` 反向引用 `_pack_service=self`(1071) 回调。**这是 D9 债的结构核心**。

---

## 4. browser-session 代码范围（cap_pack 内）

| 成员 | 行号 | 行数 |
|---|---|---|
| browser-support import | 129–134 | 6 |
| `__init__` `self._browser_sessions: dict[str, _BrowserSessionState] = {}` | 214 | 1 |
| `capability_snapshot` 中 `browser_session_count` | 893 | 1 |
| `_register_builtin_tools` 注入 `browser_sessions=self._browser_sessions` | 1066 | 1 |
| `_resolve_tool_availability` browser.* 分支 | 1742–1745 | 4 |
| `_resolve_tool_availability_reason` browser.* 分支 | 1790–1793 | 4 |
| **browser-session 方法块**（9 个方法） | 1845–1964 | 120 |

**总计 ≈ 137 行**。耦合点：
1. **共享状态 `self._browser_sessions`**（dict）——经 `ToolDeps.browser_sessions`(1066) **传引用**给 builtin_tools handler，共享同一可变 dict。测试直接覆写（`test_graph_pipeline_security.py:297/322/340`）。
2. 共享 SSRF helper `_ssrf_request_hook`(73–77) + `async_ensure_url_safe`(45)——`_fetch_browser_page`(1864/1868) 与 `_search_web`(2017) 复用。
3. 共享截断 helper `_truncate_text`(135)。
4. 共享 browser-support 数据类型（定义在 `builtin_tools/_browser_support.py`）。
5. 可用性裁决耦合：`_resolve_tool_availability`(1742)/(1790) 读 `self._browser_sessions` 判 DEGRADED——session 运行时状态泄漏进治理面。

**抽取评估**：browser block 几乎自包含，可整体下沉 `builtin_tools/_browser_support.py` 或新 service，但需同时迁 availability 分支 + ToolDeps 注入，保 `_browser_sessions` 共享语义。

---

## 5. Callers 清单

### broker.py（`octoagent.tooling`）
源码：`octo_harness.py`、`capability_pack.py`、`mcp_registry.py`、`routes/ops.py`、`builtin_tools/{graph_pipeline_tool,delegate_task_tool,mcp_tools}.py`、`packages/skills/src/octoagent/skills/{runner,tools}.py`、tooling 包内 8 文件。
测试：tooling 6 + skills 1 + gateway 11+（含 `harness/test_tool_result_threat_scan.py`、`harness/test_finalize_result_offload.py`、e2e_live 4）。

### octo_harness.py
源码：**仅 `main.py`**（唯一生产 caller）。
测试 15 文件（主要 e2e_live）：`test_f101_phase_b.py`、`test_f105_harness_wiring.py`、`harness/test_final_drain_background_tasks.py`、`e2e_live/` 12 个。

### capability_pack.py
源码：`main.py`、`deps.py`、`harness/octo_harness.py`、`routes/memory_candidates.py`、`services/delegation_plane.py`、`services/memory/builtin_memu_bridge.py`、`services/builtin_tools/_deps.py`、`packages/provider/src/octoagent/provider/dx/setup_governance_adapter.py`。
测试 10+ 文件。

---

## 6. 测试直调私有方法清单（⚠️ mixin 约束信号）

### broker.py
| 测试文件 | 直调成员 |
|---|---|
| `apps/gateway/tests/harness/test_finalize_result_offload.py` | `_scan_collect_findings`(76/90)、`_finalize_result` 语义(12) |
| `apps/gateway/tests/e2e_live/test_e2e_tool_result_threat_scan.py` | `_finalize_result`(9)、`_content_scanner` 私有属性断言(103/243) |

### octo_harness.py（hermetic 测试逐段直调——**11 段签名 + 跨段 `self._*` 字段被钉死**）
| 测试文件 | 直调成员 |
|---|---|
| `e2e_live/test_hermetic_isolation.py` | `_bootstrap_paths`(79/112/155)、`_bootstrap_stores`(113/156)、`_bootstrap_tool_registry_and_snapshot`(157)、`_bootstrap_owner_profile`(158)、`_bootstrap_runtime_services`(161)、`_bootstrap_executors`(232/256)；属性 `_store_group`(116/121/167)、`_credential_store_override`(182/250)、`_llm_adapter_override`(183)、`_mcp_servers_dir`(184)、`_data_dir`(185) |
| `harness/test_final_drain_background_tasks.py` | `_final_drain_background_tasks`（模块级，直接 import） |
| `e2e_live/test_e2e_basic_tool_context.py` | `_bootstrap_llm`(69)、`_bootstrap_tool_registry_and_snapshot`(272) |
| `e2e_live/test_e2e_routine.py` | `_bootstrap_control_plane`(54/76) |

### capability_pack.py
| 测试文件 | 直调成员 |
|---|---|
| `services/test_capability_pack_phase_d.py` | `_launch_child_task`（8 处，含 75 行 `__get__` 重绑） |
| `test_capability_pack_tools.py` | `_mcp_tool_enabled_by_default`（8 处） |
| `tools/test_graph_pipeline_security.py` | `_resolve_tool_availability`(301/326/343)、`_resolve_tool_availability_reason`(306)、`_browser_sessions` 覆写(297/322/340) |
| `e2e_live/test_e2e_delegation_a2a.py` | `_resolve_tool_availability("delegate_task")`(158) |
| `e2e_live/test_e2e_mcp_skill_pipeline.py` | `_resolve_tool_availability("graph_pipeline")`(523) |
| `e2e_live/test_e2e_ssrf_guard.py` | `_fetch_browser_page`(90) |
| `services/test_phase_c_worker_to_worker.py` | `_launch_child_task`(29) |

---

## 重构 impact 要点（行为零变更约束下）

1. **broker 最干净**——无对上 import，私有测试耦合面小。`_finalize_result` 覆盖 execute 全部 ~8 退出分支（注释 701），改动需保 fail-open 语义。
2. **cap_pack 是债的核心**——builtin-tool 业务逻辑应抽离；但 `_launch_child_task`/`_resolve_tool_availability*`/`_mcp_tool_enabled_by_default`/`_fetch_browser_page` 被测试**直调私有方法**→ 抽取必须 mixin 继承保留 `self.` 入口（F113 约束）。`_browser_sessions` 共享 dict 经 ToolDeps 传引用，抽取需保引用语义。
3. **harness 11 段被 hermetic 测试逐段直调**——段拆分/合并会破坏测试；重构需保各 `_bootstrap_*` 签名与跨段 `self._*` 中间值字段。**结论：harness 不动结构，只动文档/注释。**
4. **权限/审批 override cache 横跨三层共享同一实例**——有意 wiring，不可单层独立重构，需三层协同（或只做 `_ApprovalOverrideMemoryCache` 类的位置下沉）。
