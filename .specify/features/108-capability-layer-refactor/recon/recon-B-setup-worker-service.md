# Recon B：setup_service.py + worker_service.py（F121 拆分输入）

> 来源：Phase 1 并行 recon agent B（very thorough），基线 d6148903。
> 两文件均为 `DomainServiceBase` 子类，共享基类（`_base.py`）提供 `_resolve_selection / _param_str / _param_bool / _completed_result / _resource_ref / _action_error / _get_service / _normalize_dict / _normalize_text_list / _tool_profile_allowed / _resolve_effective_policy_profile / _policy_profile_by_id / _stores / _ctx` 等。

---

## 文件 1：setup_service.py（2576 行，类 `SetupDomainService`）

### 1. 职责簇划分（7 簇）

| 簇 | 方法清单 | 行号范围 | 估算行数 | 说明 |
|---|---|---|---|---|
| **A. 路由表** | `__init__`, `action_routes`, `document_routes` | 71–109 | ~39 | 注册 6 action + 6 document |
| **B. Resource Producers（文档构建）** | `get_config_schema`, `get_project_selector`, `get_skill_governance_document`, `get_setup_governance_document`, `get_capability_pack_document`, `get_diagnostics_summary` | 115–753 | ~638 | 6 个大文档；`get_setup_governance_document` 是聚合根 |
| **C. Action Handlers** | `_handle_project_select`, `_handle_setup_review`, `_handle_setup_apply`, `_handle_setup_oauth_and_apply`, `_handle_setup_quick_connect`, `_handle_skills_selection_save`, `_handle_config_apply` | 759–1303 | ~544 | 6 个 setup/config 动作 |
| **D. Skill Selection 域** | `_normalize_skill_selection_payload`, `_normalize_skill_selection_for_scope`, `_resolve_project_skill_selection`, `_skill_item_selected`, `_apply_skill_selection_to_items` | 1311–1434 | ~124 | skill 选择归一化/投影；纯逻辑 |
| **E. Config / Secret / Env IO** | `_save_runtime_secret_values`, `_build_config_ui_hints`, `_credential_store`, `_env_file_values`, `_write_env_values`, `_safe_secret_audit`, `_format_config_validation_errors`, `_collect_provider_runtime_details`, `_collect_bridge_refs` | 1438–1697, 2154–2360 | ~470 | config UI hint 表 + .env 读写 + 凭证 store + secret audit |
| **F. Setup Review / Risk 引擎** | `_build_setup_review_summary`, `_collect_memory_alias_risks`, `_resolve_active_agent_profile_payload`, `_merge_agent_profile_payload` | 1701–2150, 2223–2292 | ~520 | 巨型风险评估器（`_build_setup_review_summary` 单方法 ~412 行 1701–2112）|
| **G. Runtime 激活 / Wizard / Diagnostics / 工具函数** | `_activate_runtime_after_config_change`, `_restart_runtime_after_delay`, `_map_update_source`, `_get_wizard_session`, `_load_runtime_snapshot`, `_load_update_summary`, `_build_channel_summary`, `_deep_merge_dicts`, `_dedupe_resource_refs` | 2362–2576 | ~215 | 运行时激活 + wizard session + 诊断快照 + 通用 merge/dedupe |

**簇间调用关系：**
- B 依赖几乎所有：`get_setup_governance_document`(B) → B(`get_project_selector`/`get_config_schema`/`get_diagnostics_summary`/`get_skill_governance_document`/`get_capability_pack_document`) + F(`_build_setup_review_summary`/`_resolve_active_agent_profile_payload`) + E(`_safe_secret_audit`/`_collect_provider_runtime_details`) + 跨服务 `_get_service("agent")`。
- C → B + D + E + F + G + 跨服务。`_handle_setup_apply` 调 D/E/F/G + agent/mcp service。
- F(`_build_setup_review_summary`) → E(`_credential_store`)、F(`_collect_memory_alias_risks`)、基类(`_tool_profile_allowed`/`_policy_profile_by_id`)。
- D → B(`get_skill_governance_document`) 回环（`_normalize_skill_selection_for_scope` 调 B）。

### 2. 编排根识别（跨 ≥2 簇组合，不可抽出，须留主类）

| 编排根 | 行号 | 组合的簇 |
|---|---|---|
| `get_setup_governance_document` | 355–567 | B+E+F+跨服务（最大聚合根，类比 F113 `build_task_context`）|
| `_handle_setup_review` | 798–881 | B+D+F+E+跨服务(agent) |
| `_handle_setup_apply` | 883–1047 | B+D+E+F+G(`_proxy_manager`)+跨服务(agent×2) |
| `_handle_setup_oauth_and_apply` | 1049–1170 | C(`_handle_setup_apply`)+跨服务(mcp `_handle_provider_oauth_openai_codex`) |
| `_handle_setup_quick_connect` | 1172–1221 | C(`_handle_setup_apply`/`_handle_setup_review`)+G(`_activate_runtime_after_config_change`) |
| `get_diagnostics_summary` | 601–753 | B(`get_project_selector`)+G(`_load_*`/`_build_channel_summary`/`_get_wizard_session`)+`BackupService`+跨服务(memory) |
| `_build_setup_review_summary` | 1701–2112 | F+E(`_credential_store` 间接)+基类 policy helpers（自身横跨 5 类 risk 收集，是 F 簇内编排根）|

### 3. behavior 文件 IO
setup_service.py **不涉及任何 behavior 文件读写**。文件 IO 仅限 `.env`/`.env.litellm`（`_env_file_values` 2157、`_write_env_values` 2171）和 `auth-profiles.json`（`_credential_store` 2154）——属 provider/secret 域，非 behavior 域。

### 4. 模块级自由函数 vs 类方法
- **自由函数：0 个**（除 `log` 模块级变量，行 63）。
- **静态方法（无类状态，可无痛外提）：** `_format_config_validation_errors`(2211)、`_map_update_source`(2446)、`_dedupe_resource_refs`(2564)。
- **`_deep_merge_dicts`(2551)** 实例方法但纯递归、不引用 `self.*`，可改静态外提。
- 「轻 self 依赖」（外提需带 `_action_error` 等基类调用，非完全无痛）：`_skill_item_selected`(2393，不引用 self)、`_normalize_skill_selection_payload`(1311，仅用 `self._action_error`)、`_build_config_ui_hints`(1523，不引用 self 但体量大)。

### 5. import 结构
**顶部 import（行 10–61）：** stdlib；`octoagent.core.models`（25 符号，20–49）；`octoagent.policy.DEFAULT_PROFILE`；provider.auth 的 `ApiKeyCredential/ProviderProfile/CredentialStore`；`BackupService`；config 的 `OctoAgentConfig/load_config/save_config`；**`import octoagent.gateway.services.control_plane as _cp_pkg`（行 57，关键：通过 package 引用以支持 monkeypatch，搬运时不可改为直接 import `RuntimeActivationService`）**；`SecretService`；`pydantic.{SecretStr,ValidationError}`；`._base`。

**Lazy import（函数内，搬运不可上提重排）：**
- 行 2346：`_collect_bridge_refs` → `from octoagent.core.models import ProjectBindingType`
- 行 2457–2460：`_get_wizard_session` → `WizardSessionDocument, WizardStepDocument`
- 行 2461：`_get_wizard_session` → `OnboardingService`
- `_cp_pkg.RuntimeActivationService`（行 2383）经顶部 package 别名延迟解析，**monkeypatch 依赖此间接引用**。

### 6. 谁 import 此文件
**唯一生产调用方：** `_coordinator.py`（行 78 import、158 实例化、216 设 `_proxy_manager`、891/894/897/904/932/938/944/953 委派调用）。其余 grep 命中均为注释。

### 7. 测试直接调用私有方法
**无。** 测试经 coordinator 走 action/document 路由间接触达。`test_control_plane_api.py` 仅访问 `control_plane_service._mcp_service / ._proxy_manager`（其他服务属性）。

---

## 文件 2：worker_service.py（2100 行，类 `WorkerProfileDomainService`）

### 1. 职责簇划分（6 簇）

| 簇 | 方法清单 | 行号范围 | 估算行数 | 说明 |
|---|---|---|---|---|
| **A. 路由表** | `__init__`, `action_routes`, `document_routes` | 72–99 | ~28 | 12 action + 2 document |
| **B. Resource Producers** | `get_worker_profiles_document`, `get_worker_profile_revisions_document` | 105–501 | ~397 | 两大文档；`get_worker_profiles_document` 287 行，内嵌 behavior materialize |
| **C. Action Handlers — Worker Profile 生命周期** | `_handle_worker_profile_review`, `_create`, `_update`, `_clone`, `_archive`, `_apply`, `_publish`, `_bind_default`, `_handle_worker_spawn_from_profile`, `_handle_worker_extract_profile_from_runtime` | 611–1271 | ~661 | 10 个动作 |
| **C2. Action Handlers — Behavior 文件 IO（域泄漏）** | `_handle_behavior_read_file`, `_handle_behavior_write_file` | 507–609 | ~103 | **见 §3** |
| **D. Capability Pack 本地副本** | `_get_capability_pack_document` | 1277–1300 | ~24 | 与 setup_service 独立的副本 |
| **E. Worker Profile Helpers — 核心域逻辑** | `_list_available_model_aliases`, `_validate_model_alias`, `_worker_profile_label`, `_worker_profile_summary`, `_worker_snapshot_id`, `_tool_selection_from_work`, `_build_agent_profile_from_worker_profile`, `_sync_worker_profile_agent_profile`, `_bind_worker_profile_as_default`, `_build_worker_dynamic_context`, `_worker_profile_control_capabilities`, `_normalize_string_list`, `_slugify_worker_profile_token`, `_generate_worker_profile_id`, `_resolve_builtin_worker_source`, `_get_worker_profile_in_scope`, `_get_work_in_scope`, `_review_worker_profile_draft`, `_worker_profile_snapshot_payload`, `_save_worker_profile_draft`, `_publish_worker_profile_revision` | 1306–2100 | ~795 | 含 `_review_worker_profile_draft`（243 行 1723–1966，校验编排根）|

**簇间调用关系：**
- B → E（6 个 helper）+ D + **behavior 模块**(`resolve_behavior_agent_slug`/`materialize_agent_behavior_files`) + `build_behavior_system_summary`(来自 `..agent_decision`)。
- C → E（几乎全部）+ D + B（间接）+ behavior 模块。`_handle_worker_profile_apply` 调 E(review/save/publish/sync/bind 五连)。
- C2 → 仅 behavior 模块（**不调 E，与 C/E 零调用耦合**）。
- E(`_review_worker_profile_draft`) → D + E 多 helper + 基类 policy。

### 2. 编排根识别（跨 ≥2 簇，不可抽出）

| 编排根 | 行号 | 组合的簇 |
|---|---|---|
| `get_worker_profiles_document` | 105–391 | B+E+D+behavior 模块+`build_behavior_system_summary`（最大聚合根）|
| `_handle_worker_profile_apply` | 898–995 | C+E(review/save/publish/sync/bind 五连)+D |
| `_handle_worker_profile_publish` | 997–1077 | C(可委派 `_handle_worker_profile_apply`)+E |
| `_handle_worker_profile_create` | 671–732 | C+E(review/save)+behavior(`materialize_agent_behavior_files`) |
| `_handle_worker_spawn_from_profile` | 1124–1202 | C+E+`TaskService`/`task_runner` |
| `_handle_worker_extract_profile_from_runtime` | 1204–1271 | C+E |
| `_review_worker_profile_draft` | 1723–1966 | E 簇内编排根（调 D+多 E helper+基类 policy），243 行校验聚合 |
| `_sync_worker_profile_agent_profile` | 1393–1414 | E+behavior |

### 3. ⭐ behavior 文件 IO 概念泄漏（核心发现）

**直接做 behavior 文件读写的方法：**

| 方法 | 行号 | 调用的 behavior API | 直接磁盘 IO |
|---|---|---|---|
| `_handle_behavior_read_file` | 507–548 | `validate_behavior_file_path`(516)、`read_behavior_file_content`(523) | `resolved.exists()`(520)、`resolved.read_text()`(537) — **直接读磁盘** |
| `_handle_behavior_write_file` | 550–609 | `resolve_write_path_by_file_id`(564)、`check_behavior_file_budget`(574) | `resolved.parent.mkdir()`(584)、`resolved.write_text()`(585) — **直接写磁盘** |

两者直接持有 `Path` 句柄做 read_text/write_text/mkdir，绕过 behavior 模块封装边界。

**额外 behavior 概念使用（materialize，分布 B/C/E 簇）：**
- `get_worker_profiles_document`：`resolve_behavior_agent_slug`(161,248)、`materialize_agent_behavior_files`(162,249)
- `_handle_worker_profile_create`：`materialize_agent_behavior_files`(701)
- `_sync_worker_profile_agent_profile`：`resolve_behavior_agent_slug`(1407)、`materialize_agent_behavior_files`(1408)
- `_build_agent_profile_from_worker_profile`：lazy import `normalize_behavior_agent_slug`(1366)

**迁移影响面：** 若把 read_text/write_text/mkdir 收口进 behavior_workspace（如新增 `write_behavior_file_content(...)`），worker_service 两 handler 改为薄包装（仅 `ControlPlaneActionError` 转译 + `_completed_result`）；对外 action 契约（`behavior.read_file`/`behavior.write_file`）不变；behavior_workspace 现有 17 个调用方不受影响。
**建议：** 这两个 handler 作为 behavior 域的薄 control_plane adapter 单独成 `behavior_service.py`（或迁入既有 behavior 域 service）——与 C/E 簇零调用关系（C2 独立），天然可切割边界。

### 4. 模块级自由函数 vs 类方法
- **自由函数：0 个**。
- **静态方法（可无痛外提）：** `_worker_profile_label`(1325)、`_worker_profile_summary`(1335)、`_worker_snapshot_id`(1341)、`_tool_selection_from_work`(1346)、`_normalize_string_list`(1608)、`_slugify_worker_profile_token`(1619) —— 6 个 `@staticmethod` 纯函数。
- **轻 self 依赖（可静态化外提）：** `_worker_profile_snapshot_payload`(1968)、`_build_worker_dynamic_context`(1438，仅调静态)、`_worker_profile_control_capabilities`(1503)、`_build_agent_profile_from_worker_profile`(1357，含 lazy import 须保留)。
- **强 self/状态依赖（留 mixin/主类）：** 所有 async 方法（调 `self._stores`/`self._ctx`/`_resolve_selection`），含 `_review_worker_profile_draft`/`_save_worker_profile_draft`/`_publish_worker_profile_revision`/`_generate_worker_profile_id`/`_get_worker_profile_in_scope`。

### 5. import 结构
**顶部（17–64）：** stdlib；**`from octoagent.core.behavior_workspace import (check_behavior_file_budget, materialize_agent_behavior_files, read_behavior_file_content, resolve_behavior_agent_slug, resolve_write_path_by_file_id, validate_behavior_file_path)`（25–32，behavior 域 6 符号——behavior IO 切割信号）**；`octoagent.core.models`（26 符号）；`load_config`；`ulid.ULID`；`from ..agent_decision import build_behavior_system_summary`(62)；`from ..task_service import TaskService`(63)；`._base`(64)。

**Lazy import：** 行 1366（唯一）：`_build_agent_profile_from_worker_profile` → `normalize_behavior_agent_slug`，须保留位置。

### 6. 谁 import 此文件
**唯一生产调用方：** `_coordinator.py`（行 80 import、163 实例化、175/188 注册、914/917 委派）。其余 grep 命中均为注释。

### 7. 测试直接调用私有方法
**无直接调用。** 测试经 coordinator 走 action/document 路由。

---

## 拆分约束总结（对 F121 决策的关键输入）

1. **唯一耦合点是 `_coordinator.py`** —— 两文件均只此一处 import + 实例化。拆出的 mixin/helper 只需保 coordinator 可拿到等价公开接口。
2. **零测试直调私有方法** —— 不存在「测试锁定 `_xxx` 签名」约束，mixin 继承 vs 自由函数两条路都可行。可优先把无 self 的 `@staticmethod` 群外提为 `*_helpers.py`。
3. **编排根必须留主类**：setup 的 `get_setup_governance_document`/`_build_setup_review_summary`/`_handle_setup_apply`；worker 的 `get_worker_profiles_document`/`_review_worker_profile_draft`/`_handle_worker_profile_apply`。
4. **behavior IO 是 worker_service 最干净切割面**：`_handle_behavior_read_file`/`_write_file`（507–609）与 worker profile 簇零调用耦合。建议优先剥离为独立 `behavior_service.py` 并把直接磁盘 IO 收口进 behavior 模块。
5. **lazy import 不可重排**（F113 教训）：setup 3 处（2346/2457/2461）+ `_cp_pkg` 间接引用（57/2383，monkeypatch 依赖）；worker 1 处（1366）。
