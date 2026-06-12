# Recon D：F118 control_plane DI + D11 + D12 + 设计输入定位

> 来源：Phase 1 并行 recon agent D（very thorough），基线 d6148903。

---

## A. F118 control_plane D8 解耦

### A.1 全部 `bind_*` + setattr 动态绑定点

**control_plane 内部 bind_* 定义（3 处，均在 coordinator）：**

| file:line | 方法 | 绑定属性 | 调用者 |
|---|---|---|---|
| `_coordinator.py:210` | `bind_automation_scheduler` | `self._automation_scheduler` + `self._automation_service._automation_scheduler`(L212) | `octo_harness.py:1352` (startup) |
| `_coordinator.py:214` | `bind_proxy_manager` | `self._proxy_manager`(L215) + `self._setup_service._proxy_manager`(L216) + `self._mcp_service._proxy_manager`(L217) | `octo_harness.py:1355` (startup) |
| `_coordinator.py:219` | `bind_mcp_installer` | `self._mcp_installer`(L220) + `self._mcp_service._mcp_installer`(L221) | `octo_harness.py:1359` (startup) |
| `automation_service.py:66` | `bind_automation_scheduler` | domain service 内部版本 | 间接经 coordinator L212 直接赋值 |

**实际"setattr 动态绑定"= 跨 service 直接属性赋值**（非 Python `setattr()` 内建），共 **7 处**集中在 `_coordinator.py:211-221`。真正的 `setattr()` 内建仅在无关位置（`octo_harness.py:281` 注释、`memory_console_view.py:406`）。

**审计"14 处"口径**：7 处跨 service 属性赋值 + `_coordinator.py:166-176` `self._ctx.service_registry = {...}`（9 key 延迟注册字典）合计构成"延迟注入"债务面。另注意：仓库其他 service 也有大量 bind_*（capability_pack 5 个、telegram 4 个、notification 3 个等），harness startup 段（octo_harness.py:851-1361）共 ~20+ 处 bind_* 调用——**F118 范围限 control_plane 关联链**。`__init__.py:7` 注释明确 re-export 是为了让测试 `monkeypatch.setattr(control_plane_module, ...)` 生效。

### A.2 `_get_service` 字符串查找
- **定义**：`_base.py:98` `DomainServiceBase._get_service(name)`，从 `self._ctx.service_registry.get(name)` 取。
- **失败行为**：`_base.py:101-102` 抛**裸 `RuntimeError`**（非 `ControlPlaneActionError`）→ 不被 execute_action 的 REJECTED 分支捕获，落 `_coordinator.py:391` 通用 Exception 分支 → `ACTION_EXECUTION_FAILED`。
- **9 个调用点**：`mcp_service.py:495`→`"setup"`；`setup_service.py:360,361,363,837,945,995,1007`→`"agent"`；`setup_service.py:1106`→`"mcp"`。被查字符串集合：`{"setup","agent","mcp"}`（registry 定义 9 key）。

### A.3 构造期流程 + 循环依赖（改构造期显式传参的关键障碍）

`ControlPlaneService.__init__`(`_coordinator.py:93-200`)：
1. L112-137 构建 `ControlPlaneContext`（共享依赖）。
2. L147-163 实例化 9 个 domain service，全部只接收 `self._ctx`。
3. L166-176 构造完成后**回填** `self._ctx.service_registry = {...}`——延迟注册根因：service 实例化时彼此尚未就绪。
4. L179-197 汇总 action/document 路由。L200 构建 registry。

**循环依赖**（`octo_harness.py:1329-1354`）：`ControlPlaneService` 先构造(1329) → `AutomationSchedulerService(control_plane_service=...)` 构造需要 control_plane 实例(1348-1351) → `bind_automation_scheduler(scheduler)` 反向绑回(1352)。**ControlPlane ↔ AutomationScheduler 双向依赖**是改构造期显式传参的硬障碍。`proxy_manager`/`mcp_installer`(1355/1359) 属"startup 期才就绪"的延迟资源，非严格循环但同走 bind_*。

### A.4 测试如何构造 control_plane
- 直接 `ControlPlaneService(...)` 的测试：`tests/test_telegram_service.py:353,395,445`（不走 bind_*/setattr 路径）。
- **无 conftest fixture 依赖 bind_*/setattr**：grep conftest 零命中。测试构造路径与生产 harness bind_* 链解耦。

---

## B. D11 LLMWorkerAdapter 改名
- **定义**：`orchestrator.py:341` `class LLMWorkerAdapter`。
- **误导原因**：仅是 `WorkerRuntime` 薄包装（`__init__` 344-371 构造 WorkerRuntime，`handle()` 381-382 直接 `return await self._runtime.run(...)`）。WorkerRuntime 不限于 LLM——涵盖 Docker 后端/工具执行/取消注册（构造接收 `docker_available_checker`/`cancellation_registry`/`execution_console`）。名字暗示"只跑 LLM 生成"，实际是通用 worker 调度适配器。
- **引用点**：定义 `orchestrator.py:341` + 唯一实例化 `orchestrator.py:431`。**零测试引用、零跨包引用**。
- **改名波及：1 个文件 2 处**。低风险。

---

## C. D12 BehaviorFileRegistry DRY
- **没有名为 `BehaviorFileRegistry` 的类**（grep 零命中）。D12 实指 `behavior_workspace.py` 模块的写入逻辑重复。
- **重复点**：behavior 文件**写入序列**两处近乎重复：
  - `control_plane/worker_service.py:574-603`（`check_behavior_file_budget` → 写入 → 发 `"behavior_file_written"` 事件 → `code="BEHAVIOR_FILE_WRITTEN"`）
  - `builtin_tools/misc_tools.py:235-284`（`resolve_write_path_by_file_id`(218) → `check_behavior_file_budget`(235) → 写入 → 发同名事件(284)）
  - 两条入口（control-plane action vs builtin tool）重复"budget 校验 + 路径解析 + 写文件 + 发同名事件"，未抽公共 helper。
- **物化/校验函数消费方**：`agent_service.py:24,683`、`worker_service.py:25-31,162,249,516,523,574,701,1366,1408`、`session_service.py:21,1400`、`startup_bootstrap.py:16,65`、`agent_decision.py:12,311`、`misc_tools.py:21-24,62,198,218,235,284`；测试 `test_behavior_workspace.py`、`test_agent_decision_envelope.py:17,528,551,691`。

---

## D. 设计输入定位（现状）

### D.1 AmbientRuntime 秒级时间戳 vs prefix-cache 前缀
- **生成**：`agent_context_helpers.py:179` `build_ambient_runtime_facts`——L219 `current_datetime_local: strftime("%Y-%m-%d %H:%M:%S")`（含秒）、L221 `current_time_local: strftime("%H:%M:%S")`（含秒）。
- **注入位置**：`agent_context_prompt_assembly.py:157-169`，AmbientRuntime 块在 **`core_sections`**（L140 起，"Block 1: Core（永远注入）"）。
- **在冻结前缀内：是**。`core_sections` 在 L361 拼成**第一条 system message**；按需内容走第二条 system message（L363-365）。秒级时间戳位于第一条 system message 中段（AgentProfile/OwnerProfile 之后、BehaviorSystem 之前）——**每秒变化破坏整个 system 前缀缓存命中**。
- 另一处复用：`capability_pack.py:848-850, 1140` 同名占位符注入 capability prompt。

### D.2 工具执行前 schema 校验现状
- **现状：没有 JSON-schema 参数校验**。`ToolBroker.execute`(broker.py:314) 流程：查找(343)→STARTED(356)→`check_permission`(370)→before-hook(396)→**直接 `_invoke_handler`(445)**→after-hook(496)。`meta.parameters_json_schema` 仅用于生成工具描述（`tool_index.py:310,354`、`schema.py:122`），execute 路径从不 validate。
- **失败反馈形态**：handler 抛异常 → broker.py:478-493 包成 `ToolResult(is_error=True, error=str(e))`（通用文本，非结构化）。`skills/runner.py:675-705 _build_tool_feedback` 包成 `ToolFeedbackMessage` 回灌下一轮。
- **结构化 retry feedback**：存在**通用** retry-feedback 机制（runner.py:109 feedback list，300-306 loop-guard 注入，155-160 消费清空），但无"schema 校验失败 → 回吐缺失/类型错误字段清单"的结构化反馈。

### D.3 tool_call_id tail eviction
- **文件**：`services/context_compaction.py`。tail keep：529-540（`recent_keep = recent_turns*2`，按 token 预算尾部收缩）；cheap truncation 1175-1216、head/tail 截断 1158-1173。
- **现状：不存在 tool_call_id 配对**。压缩操作 `ConversationTurn`(119-127，字段仅 role/content/source_event_id/artifact_ref，**无 tool_call_id**)。`context_compaction.py` 与 `agent_context_session_replay.py` grep `tool_call_id` 零命中 → tail eviction 按 role/turn 粒度，不保证 assistant tool_call 与 tool_result 配对完整性。

### D.4 大工具输出 artifact 卸载
- **截断 + 卸载已存在**：`packages/tooling/src/octoagent/tooling/hooks_legacy.py:141 class LargeOutputHandler`（after-hook）——阈值(207)、不超不卸(210)、`head_tail_truncate`(214/79)、`_store_as_artifact`(219/241)→`put_artifact`(260)、store 不可用降级仅截断(227)、返回 `artifact_ref + truncated=True`(235-237)。
- **第二层截断（送 LLM 前）**：`skills/runner.py:684-689`，有 artifact 时拼 `[artifact:{ref}] {prefix}...`。
- **artifact_store 接口**：Protocol `packages/core/src/octoagent/core/store/protocols.py:80`；实现 `store/artifact_store.py:64 SqliteArtifactStore`；tooling 侧 Protocol `tooling/protocols.py:55`；注入 `octo_harness.py:630`，broker 持有 `broker.py:149`。

---

## 关键障碍/风险速览
- **A.3 循环依赖**（ControlPlane↔AutomationScheduler）是 D8 改构造期显式传参的核心阻塞点。
- **A.2** `_get_service` 失败抛裸 RuntimeError（错误分类 ACTION_EXECUTION_FAILED 而非 REJECTED）——typed 化时必须保留此可观测语义（行为零变更）。
- **D.1** 秒级时间戳在第一条 system message（冻结前缀）中段——挪出是 LLM 可见 prompt 布局变更，**非零变更**，需显式行为变更 commit。
- **D.2** 执行前完全无 schema 校验——补校验 = 新行为，feature 性质。
- **D.3** turn 模型不含 tool_call_id——配对保护 = 新行为 + 模型字段变更，feature 性质。
- **D.4** artifact 卸载已存在（hooks_legacy LargeOutputHandler）——"无损卸载提前到 truncate 前"是策略调整，行为变更性质。
