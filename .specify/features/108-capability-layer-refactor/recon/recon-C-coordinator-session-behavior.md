# Recon C：_coordinator.py + session_service.py + behavior_workspace.py（F121 拆分输入）

> 来源：Phase 1 并行 recon agent C（very thorough），基线 d6148903。

---

## 文件 1：`_coordinator.py` (1889 行)
`apps/gateway/src/octoagent/gateway/services/control_plane/_coordinator.py`
类 `ControlPlaneService`（唯一类，整文件就是这一个 thin facade）。

### 1.1 职责簇划分

| 簇名 | 成员 | 行号范围 | 估算行数 | 簇间调用 |
|---|---|---|---|---|
| **A. 构造 / 装配** | `__init__` | 93–200 | ~108 | 实例化 9 个 DomainService，注册 `service_registry`、`_action_dispatch`、`_document_dispatch`，调用簇 H `_build_registry` |
| **B. 延迟绑定 / 属性** | `automation_store`(property), `bind_automation_scheduler`, `bind_proxy_manager`, `bind_mcp_installer` | 206–221 | ~16 | 注入到 setup/mcp/automation service |
| **C. Action Registry 查询** | `get_action_registry`, `get_action_definition` | 227–234 | ~8 | 读簇 H 产物 `_registry` |
| **D. Telegram 适配** | `build_telegram_action_request`, `_has_telegram_alias` | 248–358 | ~111 | `_has_telegram_alias`→簇 C `get_action_definition` |
| **E. execute_action 编排 + 路由** | `execute_action`, `_dispatch_action`, `_dispatch_inline_action` | 364–596 | ~233 | **编排根**：调簇 I (`_publish_*`)、簇 F、`_action_dispatch`（→各 DomainService）、簇 J facades |
| **F. Operator action helpers** | `_handle_operator_approval`, `_handle_operator_action`, `_handle_operator_request`, `_map_operator_source`, `_map_update_source` | 602–699 | ~98 | 委托 `operator_action_service`；被簇 E 调用 |
| **G. 结果构建工具** | `_completed_result`, `_deferred_result`, `_resource_ref` | 705–752 | ~48 | 叶子工具，被 E/F/J 复用 |
| **H. get_snapshot 聚合** | `get_snapshot`, `_degraded_snapshot_resource`(staticmethod, 1309) | 758–884 + 1309–1329 | ~150 | **编排根**：`asyncio.gather` 16 个簇 J facade getter + 簇 C + 簇 K bootstrap |
| **J. Document getter facades** | `get_wizard_session`, `get_config_schema`, `get_project_selector`, `get_session_projection`, `_resolve_selection`, `_resolve_active_agent_profile_payload`, `get_agent_profiles_document`, `get_worker_profiles_document`, `get_worker_profile_revisions_document`, `get_owner_profile_document`, `get_bootstrap_session_document`, `get_context_continuity_document`, `get_policy_profiles_document`, `get_skill_governance_document`, `get_mcp_provider_catalog_document`, `get_setup_governance_document`, `get_automation_document`, `get_capability_pack_document`, `get_delegation_document`, `get_skill_pipeline_document`, `get_diagnostics_summary`, `get_memory_console`, `get_retrieval_platform_document`, `list_recall_frames` | 890–963 | ~74 | 纯委托到对应 DomainService，零业务逻辑 |
| **K. 事件查询 / Audit list** | `list_events` | 969–1017 | ~49 | 调簇 I `_ensure_audit_task` + event_store |
| **L. Automation run 记录** | `record_automation_run_status`, `create_automation_run` | 1023–1055 | ~33 | 委托 automation_service，回传 `_publish_resource_event` 回调 |
| **M. 默认 bootstrap** | `_ensure_default_main_agent_bootstrap` | 1061–1177 | ~117 | 直接读写 stores（**唯一在 coordinator 里残留的实质业务逻辑**），被簇 H 调用 |
| **I. 事件基础设施** | `_publish_action_event`, `_publish_action_result_event`, `_publish_resource_event`, `_append_control_event`, `_ensure_audit_task`, `_map_control_event_type` | 1183–1307 | ~125 | 叶子簇，被 E/K/L 调用；`_append_control_event`→`_ensure_audit_task` |
| **H2. Registry 构建** | `_build_registry`（含内嵌 `definition` 闭包，~140 个 action 定义） | 1335–1889 | ~555 | 构造期被 `__init__` 调用一次。**最大单簇，纯声明式数据** |

### 1.2 编排根识别（必须留基类，参照 F113 `build_task_context`）
- **`execute_action` (364)** — 跨簇 E→I→F→各 DomainService 组合调用，对外唯一 action 入口。**留基类**。
- **`get_snapshot` (758)** — 跨簇 J（16 getter）+ C + M 组合并行编排。**留基类**。
- **`_dispatch_action` / `_dispatch_inline_action` (419/430)** — 路由分发，inline 与 domain 两层组合。**留基类**（inline 分支体可拆为 ops/update/operator 子模块）。
- **`build_telegram_action_request` (248)** — 跨 D→C 组合，纯解析，**可下沉为独立 `telegram_command_parser` 模块**（不是必须留基类）。

### 1.4 与其它 control_plane service 的关系
- **编排了谁**：`__init__` (147–163) 实例化并持有全部 9 个 DomainService（`session/work/agent/automation/import/mcp/memory/setup/worker`），通过 `service_registry` (166–176) 注册供跨 service 调用；`_action_dispatch`/`_document_dispatch` (190–197) 聚合各 service 的 `action_routes()`/`document_routes()`。
- **暴露什么给谁**：对 `main.py`/`deps.py`/`octo_harness.py` 暴露 `execute_action` / `get_snapshot` / `build_telegram_action_request` / `list_events` / `bind_*` / `ensure_system_automation_jobs` / `record_automation_run_status` / `create_automation_run`；实例由 `octo_harness.py:1329` 创建并挂到 `app.state.control_plane_service`。

### 1.5 import 结构
- **顶部 import** (15–84)：stdlib (`asyncio/time/datetime/pathlib/typing`)、`structlog`、`octoagent.core.models`（大批 model）、`StoreGroup`、`AutomationStore`、`ControlPlaneStateStore`、`ImportWorkbenchService`、`MemoryConsoleService`、`RetrievalPlatformService`、`ulid.ULID`；`._base`（`ControlPlaneActionError`, `ControlPlaneContext`）+ 9 个 `.xxx_service` DomainService。
- **函数内 lazy import**（11 处）：437/447 `OnboardingService`；487/501 `BackupService`；586/605 `OperatorActionKind`；645 `OperatorActionRequest, OperatorActionSource`；683 `OperatorActionSource`；693 `UpdateTriggerSource`；1048 `ControlPlaneActor`；1063 `WorkerProfile/WorkerProfileOriginKind/WorkerProfileStatus`。

### 1.7 测试直接调用私有方法
- 未发现任何 test 直接调用 `ControlPlaneService` 的 `_` 私有方法。`test_control_plane_api.py:3399` 仅在注释中提及 `_build_session_projection_items`（实为 session_service 的）。`consolidation_service.py:199` 注释提及 `ControlPlaneService._handle_memory_consolidate`（该方法在 memory_service，不在本文件）。

---

## 文件 2：`session_service.py` (1847 行)
`apps/gateway/src/octoagent/gateway/services/control_plane/session_service.py`
类 `SessionDomainService(DomainServiceBase)`（唯一类）。result/param/selection 辅助（`_completed_result`/`_rejected_result`/`_param_str`/`_resource_ref`/`_resolve_selection`/`_sync_web_project_selector_state`）均继承自 `_base.DomainServiceBase`，不在本文件内。

### 2.1 职责簇划分

| 簇名 | 成员 | 行号范围 | 估算行数 | 簇间调用 |
|---|---|---|---|---|
| **A. 路由表** | `action_routes`, `document_routes` | 97–116 | ~20 | 声明 10 个 handler + 3 个 getter |
| **B. Document Getters** | `get_session_projection`, `get_bootstrap_session_document`, `get_context_continuity_document` | 122–422 | ~301 | `get_session_projection`→簇 C+D；`get_context_continuity` 纯组装 store→DTO（最大单方法 ~218 行投影映射） |
| **C. Session Projection 构建** | `_build_session_projection_items`（两遍扫描） | 428–664 | ~237 | **编排根**：调簇 D 全套 + `_resolve_session_projection_semantics` + `_extract_latest_user_*` + store |
| **D. Projection 辅助** | `_normalize_turn_executor_kind`, `_default_turn_executor_kind_for_runtime`, `_resolve_profile_display_name`, `_is_worker_profile_id`, `_resolve_session_projection_semantics`, `_resolve_projected_session_id_for_task`, `_session_lane_for_status`, `_build_session_projection_summary`, `_resolve_projected_focus`, `_list_tasks_for_projected_session`, `_build_session_capabilities`, `_extract_latest_user_message`, `_extract_latest_user_metadata` | 670–993 | ~324 | 叶子/半叶子，被 B/C/E 调用 |
| **E. Session 解析辅助** | `_resolve_session_projection_target`, `_list_related_agent_sessions_for_projection`, `_resolve_projected_session_alias`, `_resolve_direct_session_worker_profile`, `_ensure_existing_project_session` | 999–1160 | ~162 | `_resolve_session_projection_target`→簇 C+D；被簇 F 所有 handler 调用 |
| **F. Action Handlers** | `_handle_session_focus`, `_handle_session_unfocus`, `_handle_session_new`, `_handle_session_create_with_project`, `_handle_session_reset`, `_handle_session_delete`, `_handle_session_export`, `_handle_session_set_alias`, `_handle_session_interrupt`, `_handle_session_resume` | 1166–1847 | ~682 | **编排根群**：每个 handler 跨 E + store + 基类 + 外部 service 组合 |

### 2.2 编排根识别
- **`get_session_projection` (122)** — 跨 B/C/D + operator_inbox_service。**留基类**（document_routes 入口）。
- **`_build_session_projection_items` (428)** — 跨簇 D 全套 + E + store 两遍扫描，projection 核心编排。**留基类**。
- **`_handle_session_create_with_project` (1289, ~226 行)** — 跨 E + behavior_workspace（`ensure_filesystem_skeleton`/`materialize_agent_behavior_files`/`resolve_behavior_agent_slug`）+ store + state_store + 基类 `_sync_web_project_selector_state`。最重编排根。**留基类**。
- **`_handle_session_reset` / `_handle_session_delete` / `_handle_session_set_alias`** — 均跨 E + store cascade + state。**留基类**。
- 纯叶子可拆模块：簇 D 的静态/纯函数（`_session_lane_for_status`、`_default_turn_executor_kind_for_runtime`、`_normalize_turn_executor_kind`、`_build_session_projection_summary`、`_resolve_projected_session_id_for_task`）可抽到 `session_projection_helpers.py`。

### 2.4 与其它 service 关系
被编排方（由 `_coordinator.__init__` 实例化）。对外暴露 `action_routes()`（10 个 `session.*` handler）+ `document_routes()`（`sessions`/`bootstrap_session`/`context_continuity`）。跨包依赖：`agent_context`（`build_projected_session_id`/`build_scope_aware_session_id`）、`connection_metadata`、`startup_bootstrap`、`task_service.TaskService`、`behavior_workspace`（3 个函数）。

### 2.5 import 结构
- **顶部** (10–87)：`re`、`collections.defaultdict`、`Mapping`、`datetime`、`structlog`；**`octoagent.core.behavior_workspace`** 3 函数 (19–23)；大批 `octoagent.core.models`；`DEFAULT_PERMISSION_PRESET`/`resolve_permission_preset`；`BackupService`；`ulid`；`._base`；`..agent_context`、`..connection_metadata`、`..startup_bootstrap`、`..task_service.TaskService`。
- **函数内 lazy import**（2 处）：980 `EventType`（`_extract_latest_user_message`）；1628 `delete_session_cascade`（`_handle_session_delete`）。

### 2.7 测试直接调用私有方法
- 无 test 直接调用本文件私有方法。仅注释引用：`test_task_service_context_integration.py:3027`（提及 `_handle_session_create_with_project`）、`test_control_plane_api.py:3399`（提及 `_build_session_projection_items`）。⇒ 私有逻辑由黑盒 API 测试间接覆盖，拆分无需迁移 test 私有引用。

---

## 文件 3：`behavior_workspace.py` (1741 行)
`packages/core/src/octoagent/core/behavior_workspace.py`

### 3.1 "纯自由函数大杂烩"验证
- **函数总数**：53 个 top-level `def`（35 public + 18 private `_`）。**结论：基本属实，是自由函数集合**——但并非纯函数；含文件 IO 副作用。
- **是否有类**：**有 6 个**，但都是数据/枚举载体，无方法逻辑大类：`BehaviorLoadProfile(StrEnum)` (116)、`OnboardingState`(@dataclass, 153，唯一带方法 `is_completed`)、`_BehaviorFileTemplate`/`_ResolvedBehaviorSource`/`_BehaviorBudgetResult`（@dataclass frozen slots，326/339/346）、`BehaviorBudgetResult(TypedDict)` (1707)。无 service 类。
- **模块级可变状态**：**无可变全局**。所有模块级符号是不可变常量（tuple/dict 常量/编译 regex `_SLUG_RE`）；唯一进程级状态是 `@cache` 装饰的 `_load_behavior_template_text` (1495) 模板文本 memoization。**无 `global` 语句**。⇒ 线程/并发安全，拆模块零状态迁移成本。

### 3.2 函数调用图聚类（可拆独立模块）

| 拟拆模块 | 成员函数 | 行号 | 说明 |
|---|---|---|---|
| **`onboarding_state.py`** | `OnboardingState`, `_onboarding_state_path`, `load_onboarding_state`, `save_onboarding_state`, `mark_onboarding_completed` | 152–237 | 状态文件 JSON IO + 原子写。自洽 |
| **`behavior_paths.py`**（路径解析） | `_slugify`, `_normalize_project_slug`, `normalize_behavior_agent_slug`, `resolve_behavior_agent_slug`, `behavior_system_dir`, `behavior_shared_dir`, `behavior_agent_dir`, `project_root_dir`, `behavior_project_dir`, `behavior_legacy_project_dir`, `behavior_project_agent_dir`, `project_workspace_dir`, `project_data_dir`, `project_notes_dir`, `project_artifacts_dir`, `project_secret_bindings_path`, `project_instructions_dir`, `_default_behavior_file_path`, `_default_path_for_file`, `_relative_path_hint`, `resolve_write_path_by_file_id` | 356–447, 684–706, 815–826, 1585–1625 | 纯路径计算，零 IO。最易抽离的叶子簇 |
| **`behavior_skeleton.py`**（文件物化 / IO） | `ensure_filesystem_skeleton`, `materialize_agent_behavior_files`, `materialize_project_behavior_files`, `read_behavior_file_content`, `_read_behavior_file`, `measure_behavior_total_size` | 284–323, 454–681, 777–778, 1672–1704 | 文件写入/读取，依赖 paths + templates |
| **`behavior_budget.py`**（预算/截断） | `truncate_behavior_content`, `_budget_for_file`, `_apply_behavior_budget`, `check_behavior_file_budget`, `BehaviorBudgetResult`, `_BehaviorBudgetResult` | 245–276, 773–804, 1707–1741 | 纯函数 + TypedDict |
| **`behavior_template.py`**（render / template） | `_load_behavior_template_text`(@cache), `_template_name_for_file`, `_render_behavior_template`, `_default_content_for_file`, `_build_file_templates`, `get_behavior_file_review_modes`, `_BehaviorFileTemplate` | 1374–1560 | template 加载+占位符替换。含唯一 `@cache` 状态 |
| **`behavior_validate.py`**（校验） | `validate_behavior_file_path`, `_local_override_file_id` | 768–770, 1628–1669 | path traversal 安全校验 |
| **`behavior_resolver.py`**（overlay 解析/装配，编排根） | `_resolve_behavior_source`, `build_default_behavior_pack_files`, `build_default_behavior_workspace_files`, `resolve_behavior_workspace`, `_template_scope_for_file`, `_is_worker_behavior_profile`, `build_behavior_bootstrap_template_ids`, `build_project_instruction_readme`, `build_project_secret_bindings_stub`, `get_profile_allowlist` | 142–144, 597–681*, 708–765, 807–812, 829–1012, 1015–1371, 1563–1577 | `resolve_behavior_workspace` (1015, ~357 行) 是顶层编排根：调 paths + budget + template + resolver + onboarding，组装 `BehaviorWorkspace` DTO |

### 3.5 import 结构
- **顶部**：stdlib（`hashlib/json/os/re/tempfile/contextlib.suppress/dataclasses/datetime/enum.StrEnum/functools.cache/importlib.resources/pathlib/typing.TypedDict`）、`structlog`、`.models.agent_context.AgentProfile`、`.models.behavior`（10+ behavior model）。
- **函数内 lazy import**：**无**（grep 零命中）。

### 3.6 谁 import behavior_workspace（18 个 caller 文件）
源码 13 个：`harness/octo_harness.py`、`services/agent_context.py`、`agent_context_entity_ensure.py`、`agent_context_prompt_assembly.py`、`agent_decision.py`、`builtin_tools/{_deps,misc_tools,user_profile_tools}.py`、`control_plane/{agent_service,session_service,worker_service}.py`、`startup_bootstrap.py`、`provider/dx/{behavior_commands,project_selector}.py`。
测试 5 个：`packages/core/tests/test_behavior_workspace.py`、`apps/gateway/tests/services/{test_agent_context_phase_c,test_agent_decision_envelope,test_behavior_pack_loaded_phase_g}.py`。
**拆分风险点**：`provider/dx/behavior_commands.py:18` 导入了**私有** `_local_override_file_id`；`worker_service.py:1366` lazy import `normalize_behavior_agent_slug`。拆模块时需保留这些符号的公共可达性（建议在 `behavior_workspace/__init__.py` re-export）。

### 3.7 测试直接调用私有函数
`packages/core/tests/test_behavior_workspace.py` (9–30) 直接 import 的私有符号：`_BEHAVIOR_TEMPLATE_VARIANTS`、`_PROFILE_ALLOWLIST`、`_default_content_for_file`、`_is_worker_behavior_profile`、`_onboarding_state_path`、`_template_name_for_file`。
⇒ 拆分时这 6 个私有符号必须保持从 `octoagent.core.behavior_workspace` 顶层可导入（package `__init__` re-export），否则该 test 直接 break。

---

## 跨文件总结
- **必须留基类的编排根**：coordinator `execute_action` / `get_snapshot` / `_dispatch_action`；session `get_session_projection` / `_build_session_projection_items` / 各 `_handle_session_*`；behavior `resolve_behavior_workspace`。
- **最易先拆的低风险叶子**：coordinator 簇 H2 `_build_registry`（555 行纯声明，可拆 `action_registry.py`）、簇 D telegram 解析；session 簇 D 静态 helper；behavior 的 `behavior_paths.py` + `behavior_budget.py`（纯函数零状态）。
- **拆分时唯一需迁移的 test 私有引用**：仅 `behavior_workspace`（6 个私有符号被 `test_behavior_workspace.py` 直接 import）+ `behavior_commands.py` 对 `_local_override_file_id` 的源码私有依赖。两个 service 文件无 test 私有引用，可黑盒安全拆分。
